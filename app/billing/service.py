from datetime import UTC, datetime
import hashlib
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Response
from app.models import StoreTransaction, WebhookReceipt
from app.security import sha256
from app.errors import api_error
from app.billing.apple import AppleVerificationFailed, AppleVerificationUnavailable
from app.billing.entitlements import entitlement_response

async def lock_purchase(db: AsyncSession, key: str):
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        value = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": value})

def validate(value, settings):
    if (value.environment != settings.apple_environment
        or value.product_id not in {settings.lifetime_product_id, settings.trial_product_id}
        or value.ownership_type not in {"PURCHASED", "FAMILY_SHARED"}
        or (value.product_id == settings.trial_product_id and value.ownership_type != "PURCHASED")):
        raise api_error(400, "invalid_transaction", "The purchase does not match this app or offer.")

async def save_transaction(db, value, settings):
    item = await db.scalar(select(StoreTransaction).where(
        StoreTransaction.environment == value.environment, StoreTransaction.transaction_id == value.transaction_id))
    if item is None:
        item = StoreTransaction(transaction_id=value.transaction_id, environment=value.environment,
            original_transaction_id=value.original_transaction_id,
            billing_subject=sha256(f"{settings.rate_limit_salt}:{value.environment}:{value.transaction_id}"),
            product_id=value.product_id, purchased_at=value.purchased_at, status=value.status,
            ownership_type=value.ownership_type, app_transaction_id=value.app_transaction_id)
        db.add(item)
    elif item.apple_signed_at and (value.signed_at is None or value.signed_at.replace(tzinfo=UTC) < item.apple_signed_at.replace(tzinfo=UTC)):
        return item
    elif (item.original_transaction_id != value.original_transaction_id or item.product_id != value.product_id
          or (item.app_transaction_id and item.app_transaction_id != value.app_transaction_id)):
        raise api_error(400, "invalid_transaction", "Transaction identity changed.")
    item.ownership_type = value.ownership_type
    item.app_transaction_id = value.app_transaction_id
    item.revoked_at = item.revoked_at or value.revoked_at
    item.status = "revoked" if item.revoked_at else value.status
    item.expires_at = value.expires_at
    item.apple_signed_at = value.signed_at or item.apple_signed_at
    await db.flush()
    return item

async def verify_store_transaction(body, verifier, db, settings, signer):
    try:
        initial = await verifier.verify_transaction(body.signed_transaction)
        validate(initial, settings)
        value = await verifier.reconcile_transaction(initial)
        validate(value, settings)
        if (value.transaction_id, value.original_transaction_id, value.environment, value.product_id, value.ownership_type, value.app_transaction_id) != (initial.transaction_id, initial.original_transaction_id, initial.environment, initial.product_id, initial.ownership_type, initial.app_transaction_id):
            raise AppleVerificationFailed("Identity mismatch")
    except AppleVerificationUnavailable as error:
        raise api_error(503, "verification_unavailable", "Purchase verification is temporarily unavailable.") from error
    except AppleVerificationFailed as error:
        raise api_error(400, "invalid_transaction", "The purchase could not be verified.") from error
    await lock_purchase(db, f"{value.environment}:{value.transaction_id}")
    item = await save_transaction(db, value, settings)
    await db.commit()
    return entitlement_response(item, settings, signer)

async def app_store_webhook(body, verifier, db, settings):
    try: notification = await verifier.verify_notification(body.signedPayload)
    except AppleVerificationUnavailable as error:
        raise api_error(503, "verification_unavailable", "Notification verification unavailable.") from error
    except AppleVerificationFailed as error:
        raise api_error(400, "invalid_notification", "Invalid notification.") from error
    await lock_purchase(db, "notification:" + notification.notification_id)
    if await db.get(WebhookReceipt, notification.notification_id): return Response(status_code=204)
    if value := notification.transaction:
        validate(value, settings)
        await lock_purchase(db, f"{value.environment}:{value.transaction_id}")
        from dataclasses import replace
        value = replace(value, signed_at=notification.signed_at or value.signed_at)
        item = await save_transaction(db, value, settings)
        stale = item.apple_signed_at and (value.signed_at is None or value.signed_at.replace(tzinfo=UTC) < item.apple_signed_at.replace(tzinfo=UTC))
        if not stale and notification.notification_type in {"REFUND", "REVOKE"}:
            item.status = "revoked"
            item.revoked_at = item.revoked_at or datetime.now(UTC)
    db.add(WebhookReceipt(id=notification.notification_id, provider="app_store"))
    await db.commit()
    return Response(status_code=204)

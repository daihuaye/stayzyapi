from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Response
from sqlalchemy import select, text
import hashlib
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import api_error
from app.observability import emit
from app.models import StoreTransaction, User, WebhookReceipt
from app.schemas import AppStoreNotificationRequest, EntitlementResponse, StoreTransactionRequest
from app.security import TokenSigner, sha256
from app.billing.apple import AppleVerificationFailed, AppleVerificationUnavailable, VerifiedStoreTransaction
from app.billing.entitlements import entitlement_response, recalculate_entitlement


async def lock_purchase(db: AsyncSession, key: str) -> None:
    # PostgreSQL transaction locks also serialize first inserts (no row yet).
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        value = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": value})



async def _save_transaction(
    db: AsyncSession,
    verified: VerifiedStoreTransaction,
    settings: Settings,
    user_id: str | None,
) -> StoreTransaction:
    item = await db.scalar(
        select(StoreTransaction).where(StoreTransaction.transaction_id == verified.transaction_id)
    )
    if item is None:
        item = StoreTransaction(
            transaction_id=verified.transaction_id,
            original_transaction_id=verified.original_transaction_id,
            user_id=user_id,
            billing_subject=sha256(f"{settings.rate_limit_salt}:{verified.original_transaction_id}"),
            product_id=verified.product_id,
            environment=verified.environment,
            status=verified.status,
            purchased_at=verified.purchased_at,
            expires_at=verified.expires_at,
            revoked_at=verified.revoked_at,
            apple_signed_at=verified.signed_at,
            billing_grace_expires_at=verified.billing_grace_expires_at,
        )
        db.add(item)
    else:
        if verified.signed_at is not None and item.apple_signed_at is not None:
            if verified.signed_at.replace(tzinfo=UTC) < item.apple_signed_at.replace(tzinfo=UTC):
                return item
        if user_id is not None:
            item.user_id = user_id
        item.product_id = verified.product_id
        item.environment = verified.environment
        item.status = "revoked" if item.revoked_at is not None else verified.status
        item.purchased_at = verified.purchased_at
        item.expires_at = verified.expires_at
        item.revoked_at = item.revoked_at or verified.revoked_at
        item.apple_signed_at = verified.signed_at or item.apple_signed_at
        item.billing_grace_expires_at = verified.billing_grace_expires_at
    return item


async def verify_store_transaction(
    body: StoreTransactionRequest,
    verifier: Any,
    user: User,
    db: AsyncSession,
    settings: Settings,
    signer: TokenSigner,
) -> EntitlementResponse:
    emit("billing.purchase_verification_started")
    try:
        verified = await verifier.verify_transaction(body.signed_transaction)
    except AppleVerificationUnavailable as error:
        raise api_error(503, "verification_unavailable", "Purchase verification is unavailable.") from error
    except AppleVerificationFailed as error:
        raise api_error(400, "invalid_transaction", "The purchase could not be verified.") from error

    if verified.product_id not in {settings.monthly_product_id, settings.lifetime_product_id}:
        raise api_error(400, "invalid_transaction", "The product is not recognized.")
    if verified.environment != settings.apple_environment:
        raise api_error(400, "wrong_environment", "The purchase belongs to another environment.")
    await lock_purchase(db, f"{verified.environment}:{verified.original_transaction_id}")
    lineage = verified.original_transaction_id
    try:
        verified = await verifier.reconcile_transaction(verified)
    except AppleVerificationUnavailable as error:
        raise api_error(503, "verification_unavailable", "Purchase verification is unavailable.") from error
    except AppleVerificationFailed as error:
        raise api_error(400, "invalid_transaction", "The purchase could not be verified.") from error
    if (verified.original_transaction_id != lineage
            or verified.environment != settings.apple_environment
            or verified.product_id not in {settings.monthly_product_id, settings.lifetime_product_id}):
        raise api_error(400, "invalid_transaction", "The purchase does not match this app.")
    existing = await db.scalar(
        select(StoreTransaction).where(
            StoreTransaction.original_transaction_id == verified.original_transaction_id
        ).order_by(StoreTransaction.purchased_at.desc(), StoreTransaction.apple_signed_at.desc()).limit(1)
    )
    reclaiming_deleted_purchase = existing is not None and existing.user_id is None
    if existing is not None and existing.user_id not in {None, user.id}:
        raise api_error(409, "purchase_already_linked", "The purchase is linked to another account.")
    already_owned = existing is not None and existing.user_id == user.id
    if verified.app_account_token != user.id and not (reclaiming_deleted_purchase or already_owned):
        raise api_error(400, "invalid_transaction", "The purchase belongs to another account.")

    await _save_transaction(db, verified, settings, user.id)
    await db.flush()
    await recalculate_entitlement(db, user.id, settings)
    await db.commit()
    emit("billing.entitlement_committed")
    return await entitlement_response(db, user.id, settings, signer)


async def app_store_webhook(
    body: AppStoreNotificationRequest,
    verifier: Any,
    db: AsyncSession,
    settings: Settings,
) -> Response:
    try:
        notification = await verifier.verify_notification(body.signedPayload)
    except AppleVerificationUnavailable as error:
        raise api_error(503, "verification_unavailable", "Notification verification is unavailable.") from error
    except AppleVerificationFailed as error:
        raise api_error(400, "invalid_notification", "The notification could not be verified.") from error
    await lock_purchase(db, "notification:" + notification.notification_id)
    if await db.get(WebhookReceipt, notification.notification_id) is not None:
        emit("billing.notification_duplicate")
        return Response(status_code=204)

    affected_user_id: str | None = None
    if notification.transaction:
        if notification.transaction.environment != settings.apple_environment or notification.transaction.product_id not in {settings.monthly_product_id, settings.lifetime_product_id}:
            raise api_error(400, "invalid_notification", "The notification does not match this app environment.")
        await lock_purchase(db, f"{notification.transaction.environment}:{notification.transaction.original_transaction_id}")
        existing = await db.scalar(
            select(StoreTransaction).where(
                StoreTransaction.original_transaction_id
                == notification.transaction.original_transaction_id
            ).order_by(StoreTransaction.purchased_at.desc(), StoreTransaction.apple_signed_at.desc()).limit(1)
        )
        affected_user_id = existing.user_id if existing else None
        stale = (existing is not None and existing.apple_signed_at is not None
                 and notification.signed_at is not None
                 and notification.signed_at.replace(tzinfo=UTC) < existing.apple_signed_at.replace(tzinfo=UTC))
        if stale:
            db.add(WebhookReceipt(id=notification.notification_id, provider="app_store"))
            await db.commit()
            return Response(status_code=204)
        item = await _save_transaction(db, notification.transaction, settings, affected_user_id)
        item.apple_signed_at = notification.signed_at or item.apple_signed_at
        if notification.notification_type in {"REFUND", "REVOKE"}:
            item.status = "revoked"
            item.revoked_at = item.revoked_at or datetime.now(UTC)
        elif item.revoked_at is not None:
            item.status = "revoked"
        elif notification.notification_type in {"EXPIRED", "GRACE_PERIOD_EXPIRED"}:
            item.status = "expired"
        elif notification.notification_type == "DID_FAIL_TO_RENEW":
            grace_end = notification.grace_period_expires_at
            if notification.subtype == "GRACE_PERIOD" and grace_end is not None:
                item.status = "grace"
                item.billing_grace_expires_at = grace_end
            else:
                # Billing retry without Apple's grace period cannot authorize a
                # new download. The local seven-day playback grace is derived
                # separately from the last verified transaction expiration.
                item.status = "expired"

    db.add(WebhookReceipt(id=notification.notification_id, provider="app_store"))
    await db.flush()
    if affected_user_id:
        await recalculate_entitlement(db, affected_user_id, settings)
    await db.commit()
    emit("billing.notification_committed")
    return Response(status_code=204)


async def reconcile_account(db: AsyncSession, user: User, settings: Settings, signer: TokenSigner, verifier: Any):
    items = list(await db.scalars(select(StoreTransaction).where(
        StoreTransaction.user_id == user.id, StoreTransaction.environment == settings.apple_environment)))
    latest = {}
    for item in items:
        if item.original_transaction_id not in latest or item.purchased_at > latest[item.original_transaction_id].purchased_at:
            latest[item.original_transaction_id] = item
    for item in sorted(latest.values(), key=lambda value: value.original_transaction_id):
        if item.revoked_at is not None:
            continue  # A recorded revocation never depends on Apple availability.
        await lock_purchase(db, f"{item.environment}:{item.original_transaction_id}")
        snapshot = VerifiedStoreTransaction(item.transaction_id, item.original_transaction_id, item.product_id,
            user.id, item.environment, item.purchased_at.replace(tzinfo=UTC),
            item.expires_at.replace(tzinfo=UTC) if item.expires_at else None,
            item.revoked_at.replace(tzinfo=UTC) if item.revoked_at else None,
            signed_at=item.apple_signed_at, billing_grace_expires_at=item.billing_grace_expires_at)
        try:
            fresh = await verifier.reconcile_transaction(snapshot)
        except (AppleVerificationUnavailable, AppleVerificationFailed) as error:
            raise api_error(503, "verification_unavailable", "Premium status could not be refreshed.") from error
        if (fresh.original_transaction_id != item.original_transaction_id
                or fresh.environment != settings.apple_environment
                or fresh.product_id not in {settings.monthly_product_id, settings.lifetime_product_id}):
            raise api_error(503, "verification_unavailable", "Premium status could not be refreshed.")
        # The locked ledger owns this lineage, including explicit restoration
        # after account deletion. Apple retains the original account token.
        await _save_transaction(db, fresh, settings, user.id)
    await db.flush()
    await recalculate_entitlement(db, user.id, settings)
    await db.commit()
    emit("billing.entitlement_committed")
    return await entitlement_response(db, user.id, settings, signer)

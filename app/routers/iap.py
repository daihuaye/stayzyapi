from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import get_signer, require_user
from app.errors import api_error
from app.models import StoreTransaction, User, WebhookReceipt
from app.schemas import AppStoreNotificationRequest, EntitlementResponse, StoreTransactionRequest
from app.security import TokenSigner, sha256
from app.services.apple_store import AppleVerificationFailed, AppleVerificationUnavailable, VerifiedStoreTransaction
from app.services.entitlements import entitlement_response, recalculate_entitlement


router = APIRouter(prefix="/v1", tags=["purchases"])


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
        )
        db.add(item)
    else:
        if user_id is not None:
            item.user_id = user_id
        item.product_id = verified.product_id
        item.environment = verified.environment
        item.status = verified.status
        item.purchased_at = verified.purchased_at
        item.expires_at = verified.expires_at
        item.revoked_at = verified.revoked_at
    return item


@router.post("/iap/apple/transactions", response_model=EntitlementResponse)
async def verify_store_transaction(
    body: StoreTransactionRequest,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    signer: TokenSigner = Depends(get_signer),
) -> EntitlementResponse:
    verifier = request.app.state.apple_store_verifier
    try:
        verified = await verifier.verify_transaction(body.signed_transaction)
    except AppleVerificationUnavailable as error:
        raise api_error(503, "verification_unavailable", "Purchase verification is unavailable.") from error
    except AppleVerificationFailed as error:
        raise api_error(400, "invalid_transaction", "The purchase could not be verified.") from error

    if verified.product_id not in {settings.monthly_product_id, settings.lifetime_product_id}:
        raise api_error(400, "invalid_transaction", "The product is not recognized.")
    existing = await db.scalar(
        select(StoreTransaction).where(
            StoreTransaction.original_transaction_id == verified.original_transaction_id
        )
    )
    reclaiming_deleted_purchase = existing is not None and existing.user_id is None
    if verified.app_account_token != user.id and not reclaiming_deleted_purchase:
        raise api_error(400, "invalid_transaction", "The purchase belongs to another account.")
    if existing is not None and existing.user_id not in {None, user.id}:
        raise api_error(409, "purchase_already_linked", "The purchase is linked to another account.")

    await _save_transaction(db, verified, settings, user.id)
    await db.flush()
    await recalculate_entitlement(db, user.id, settings)
    await db.commit()
    return await entitlement_response(db, user.id, settings, signer)


@router.post("/webhooks/app-store", status_code=204)
async def app_store_webhook(
    body: AppStoreNotificationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    verifier = request.app.state.apple_store_verifier
    try:
        notification = await verifier.verify_notification(body.signedPayload)
    except AppleVerificationUnavailable as error:
        raise api_error(503, "verification_unavailable", "Notification verification is unavailable.") from error
    except AppleVerificationFailed as error:
        raise api_error(400, "invalid_notification", "The notification could not be verified.") from error
    if await db.get(WebhookReceipt, notification.notification_id) is not None:
        return Response(status_code=204)

    affected_user_id: str | None = None
    if notification.transaction:
        existing = await db.scalar(
            select(StoreTransaction).where(
                StoreTransaction.original_transaction_id
                == notification.transaction.original_transaction_id
            )
        )
        affected_user_id = existing.user_id if existing else None
        item = await _save_transaction(db, notification.transaction, settings, affected_user_id)
        if notification.notification_type in {"REFUND", "REVOKE"}:
            item.status = "revoked"
            item.revoked_at = item.revoked_at or datetime.now(UTC)
        elif notification.notification_type in {"EXPIRED", "GRACE_PERIOD_EXPIRED"}:
            item.status = "expired"
        elif notification.notification_type == "DID_FAIL_TO_RENEW":
            grace_end = notification.grace_period_expires_at
            if notification.subtype == "GRACE_PERIOD" and grace_end is not None:
                item.status = "grace"
                item.expires_at = grace_end
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
    return Response(status_code=204)

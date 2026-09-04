from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.security import TokenSigner
from app.services.apple_store import VerifiedNotification, VerifiedStoreTransaction
from conftest import sign_in


async def test_monthly_purchase_and_refund_notification(api_client, settings) -> None:
    client, _, email, _, apple = api_client
    signed_in = await sign_in(client, email)
    claims = TokenSigner(settings).decode_access(str(signed_in["access_token"]))
    now = datetime.now(UTC)
    transaction = VerifiedStoreTransaction(
        transaction_id="tx-monthly-1",
        original_transaction_id="original-monthly-1",
        product_id=settings.monthly_product_id,
        app_account_token=claims.user_id,
        environment="Sandbox",
        purchased_at=now,
        expires_at=now + timedelta(days=30),
        revoked_at=None,
    )
    apple.transaction = transaction
    headers = {"Authorization": f"Bearer {signed_in['access_token']}"}

    purchase = await client.post(
        "/v1/iap/apple/transactions",
        headers=headers,
        json={"signed_transaction": "signed-transaction-payload-that-is-long-enough"},
    )
    assert purchase.status_code == 200
    assert purchase.json()["status"] == "active"
    assert purchase.json()["plan"] == "monthly"

    apple.notification = VerifiedNotification(
        notification_id="notification-refund-1",
        notification_type="REFUND",
        subtype=None,
        transaction=VerifiedStoreTransaction(
            **{**transaction.__dict__, "revoked_at": now + timedelta(minutes=1)}
        ),
    )
    payload = {"signedPayload": "signed-notification-payload-that-is-long-enough"}
    first = await client.post("/v1/webhooks/app-store", json=payload)
    duplicate = await client.post("/v1/webhooks/app-store", json=payload)
    assert first.status_code == 204
    assert duplicate.status_code == 204

    entitlement = await client.get("/v1/entitlements", headers=headers)
    assert entitlement.json()["status"] == "inactive"


async def test_purchase_rejects_mismatched_account_token(api_client, settings) -> None:
    client, _, email, _, apple = api_client
    signed_in = await sign_in(client, email)
    now = datetime.now(UTC)
    apple.transaction = VerifiedStoreTransaction(
        transaction_id="tx-other",
        original_transaction_id="original-other",
        product_id=settings.lifetime_product_id,
        app_account_token="a-different-user-id",
        environment="Sandbox",
        purchased_at=now,
        expires_at=None,
        revoked_at=None,
    )
    response = await client.post(
        "/v1/iap/apple/transactions",
        headers={"Authorization": f"Bearer {signed_in['access_token']}"},
        json={"signed_transaction": "signed-transaction-payload-that-is-long-enough"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_transaction"


async def test_billing_grace_extends_service_but_not_after_grace_expires(
    api_client,
    settings,
) -> None:
    client, _, email, _, apple = api_client
    signed_in = await sign_in(client, email)
    claims = TokenSigner(settings).decode_access(str(signed_in["access_token"]))
    headers = {"Authorization": f"Bearer {signed_in['access_token']}"}
    now = datetime.now(UTC)
    expired = VerifiedStoreTransaction(
        transaction_id="tx-grace-1",
        original_transaction_id="original-grace-1",
        product_id=settings.monthly_product_id,
        app_account_token=claims.user_id,
        environment="Sandbox",
        purchased_at=now - timedelta(days=30),
        expires_at=now - timedelta(minutes=1),
        revoked_at=None,
    )
    apple.transaction = expired
    purchase = await client.post(
        "/v1/iap/apple/transactions",
        headers=headers,
        json={"signed_transaction": "signed-transaction-payload-that-is-long-enough"},
    )
    assert purchase.status_code == 200
    assert purchase.json()["status"] == "grace"

    grace_end = now + timedelta(days=5)
    apple.notification = VerifiedNotification(
        notification_id="notification-grace-1",
        notification_type="DID_FAIL_TO_RENEW",
        subtype="GRACE_PERIOD",
        transaction=expired,
        grace_period_expires_at=grace_end,
        is_in_billing_retry_period=True,
    )
    payload = {"signedPayload": "signed-notification-payload-that-is-long-enough"}
    assert (await client.post("/v1/webhooks/app-store", json=payload)).status_code == 204
    active = await client.get("/v1/entitlements", headers=headers)
    assert active.json()["status"] == "active"

    apple.notification = VerifiedNotification(
        notification_id="notification-grace-expired-1",
        notification_type="GRACE_PERIOD_EXPIRED",
        subtype=None,
        transaction=expired,
        is_in_billing_retry_period=True,
    )
    assert (await client.post("/v1/webhooks/app-store", json=payload)).status_code == 204
    local_playback = await client.get("/v1/entitlements", headers=headers)
    assert local_playback.json()["status"] == "grace"

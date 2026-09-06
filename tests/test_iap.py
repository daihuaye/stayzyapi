from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.security import TokenSigner
from app.services.apple_store import VerifiedNotification, VerifiedStoreTransaction
from conftest import sign_in


async def test_lifetime_purchase_and_refund_notification(api_client, settings) -> None:
    client, _, email, _, apple = api_client
    signed_in = await sign_in(client, email)
    claims = TokenSigner(settings).decode_access(str(signed_in["access_token"]))
    now = datetime.now(UTC)
    transaction = VerifiedStoreTransaction(
        transaction_id="tx-lifetime-1",
        original_transaction_id="original-lifetime-1",
        product_id=settings.lifetime_product_id,
        app_account_token=claims.user_id,
        environment="Sandbox",
        purchased_at=now,
        expires_at=None,
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
    assert purchase.json()["plan"] == "lifetime"

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


async def test_removed_monthly_product_is_rejected(api_client, settings):
    client, _, email, _, apple = api_client
    signed_in = await sign_in(client, email)
    claims = TokenSigner(settings).decode_access(str(signed_in["access_token"]))
    apple.transaction = VerifiedStoreTransaction(
        "unsupported", "unsupported", "com.vistasolutions.stayzy.premium.monthly",
        claims.user_id, "Sandbox", datetime.now(UTC), None, None)
    response = await client.post("/v1/iap/apple/transactions",
        headers={"Authorization": f"Bearer {signed_in['access_token']}"},
        json={"signed_transaction": "signed-transaction-payload-that-is-long-enough"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_transaction"

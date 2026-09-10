from dataclasses import replace
from datetime import UTC, datetime, timedelta
import jwt
import pytest
from sqlalchemy import select
from app.billing.apple import VerifiedStoreTransaction, VerifiedNotification, AppleVerificationFailed, AppleVerificationUnavailable, AppleStoreVerifier
from app.models import StoreTransaction
from app.security import TokenSigner

PROOF = "apple-signed-purchase-proof-" * 3

def transaction(settings, **changes):
    value = VerifiedStoreTransaction("member-1", "original-1", settings.lifetime_product_id,
        None, settings.apple_environment, datetime.now(UTC), None, None,
        signed_at=datetime.now(UTC), ownership_type="FAMILY_SHARED", app_transaction_id="recipient-1")
    return replace(value, **changes)

async def exchange(api_client, settings, **changes):
    client, _, _, _, verifier = api_client
    verifier.transaction = transaction(settings, **changes)
    result = await client.post("/v1/iap/apple/transactions", json={"signed_transaction": PROOF})
    assert result.status_code == 200, result.text
    return result.json()

@pytest.mark.parametrize("ownership", ["PURCHASED", "FAMILY_SHARED"])
async def test_account_free_purchase(api_client, settings, session_factory, ownership):
    data = await exchange(api_client, settings, ownership_type=ownership)
    assert data["status"] == "active" and data["ownership_type"] == ownership
    signer = TokenSigner(settings)
    grant_id = signer.decode_purchase(data["access_token"])
    claims = jwt.decode(data["access_token"], signer.verification_key, algorithms=[signer.algorithm], audience="stayzy-purchases")
    assert claims["exp"] - claims["iat"] == 900
    async with session_factory() as db:
        item = await db.get(StoreTransaction, grant_id)
        assert item.ownership_type == ownership
    again = await exchange(api_client, settings, ownership_type=ownership)
    assert signer.decode_purchase(again["access_token"]) == grant_id

async def test_family_revoke_does_not_revoke_own_grant(api_client, settings):
    client, _, _, _, verifier = api_client
    shared = await exchange(api_client, settings)
    own = await exchange(api_client, settings, transaction_id="own", original_transaction_id="own-original", ownership_type="PURCHASED")
    verifier.notification = VerifiedNotification("revoke-1", "REVOKE", None, transaction(settings), datetime.now(UTC))
    for _ in range(2):
        assert (await client.post("/v1/webhooks/app-store", json={"signedPayload": PROOF})).status_code == 204
    for data, status in [(shared,"inactive"),(own,"active")]:
        r = await client.get("/v1/entitlements", headers={"Authorization": "Bearer " + data["access_token"]})
        assert r.json()["status"] == status
    denied = await client.post("/v1/voice-packs/voice/download", json={"locale":"en-US"},
        headers={"Authorization": "Bearer " + shared["access_token"]})
    assert denied.status_code == 403 and "archive_url" not in denied.text
    # An older proof cannot resurrect a recorded revocation.
    assert (await exchange(api_client, settings))["status"] == "inactive"

async def test_stale_notification_does_not_revoke_newer_grant(api_client, settings):
    client, _, _, _, verifier = api_client
    data = await exchange(api_client, settings)
    verifier.notification = VerifiedNotification("old", "REVOKE", None, transaction(settings), datetime.now(UTC)-timedelta(days=1))
    await client.post("/v1/webhooks/app-store", json={"signedPayload":PROOF})
    r = await client.get("/v1/entitlements", headers={"Authorization":"Bearer "+data["access_token"]})
    assert r.json()["status"] == "active"

@pytest.mark.parametrize("changes", [{"environment":"Production"},{"product_id":"unknown"},
    {"ownership_type":"UNKNOWN"},{"ownership_type":"FAMILY_SHARED","product_id":"com.vistasolutions.stayzy.trial.seven_days"}])
async def test_invalid_purchase_rejected(api_client, settings, changes):
    client, _, _, _, verifier = api_client
    verifier.transaction = transaction(settings, **changes)
    assert (await client.post("/v1/iap/apple/transactions", json={"signed_transaction":PROOF})).status_code == 400

@pytest.mark.parametrize("failure,code", [(AppleVerificationFailed,400),(AppleVerificationUnavailable,503)])
async def test_verification_failure(api_client, failure, code):
    client, _, _, _, verifier = api_client
    async def fail(_): raise failure("private")
    verifier.verify_transaction = fail
    r = await client.post("/v1/iap/apple/transactions", json={"signed_transaction":PROOF})
    assert r.status_code == code and "private" not in r.text

async def test_reconciled_recipient_must_match(api_client, settings):
    client, _, _, _, verifier = api_client
    verifier.transaction = transaction(settings)
    async def changed(value): return replace(value, app_transaction_id="different-member")
    verifier.reconcile_transaction = changed
    assert (await client.post("/v1/iap/apple/transactions", json={"signed_transaction":PROOF})).status_code == 400

@pytest.mark.parametrize("days,active", [(0,True),(6,True),(7,False),(8,False)])
async def test_individual_trial_deadline(api_client, settings, days, active):
    data = await exchange(api_client, settings, product_id=settings.trial_product_id, ownership_type="PURCHASED",
        purchased_at=datetime.now(UTC)-timedelta(days=days))
    assert (data["status"] == "active") == active
    assert bool(data["access_token"]) == active

async def test_token_isolation_expiry_and_retired_routes(api_client, settings):
    client = api_client[0]; data = await exchange(api_client, settings)
    assert (await client.get("/v1/admin/auth/me", headers={"Authorization":"Bearer "+data["access_token"]})).status_code == 401
    signer=TokenSigner(settings)
    for audience, expiry in [("stayzy-ios",datetime.now(UTC)+timedelta(minutes=1)),("stayzy-purchases",datetime.now(UTC)-timedelta(seconds=1))]:
        token=jwt.encode({"type":"purchase","sub":"grant","aud":audience,"iss":"stayzy-api","iat":datetime.now(UTC)-timedelta(hours=1),"exp":expiry}, signer.signing_key, algorithm=signer.algorithm)
        assert (await client.get("/v1/entitlements",headers={"Authorization":"Bearer "+token})).status_code == 401
    for path in ["/v1/auth/apple","/v1/auth/magic-links","/v1/auth/refresh","/v1/auth/logout"]:
        assert (await client.post(path,json={})).status_code == 404
    assert (await client.get("/v1/me")).status_code == 404
    assert (await client.delete("/v1/account")).status_code == 404

async def test_unsigned_request_cannot_download(api_client):
    result=await api_client[0].post("/v1/voice-packs/voice/download",json={"locale":"en-US"})
    assert result.status_code == 401 and "archive_url" not in result.text

@pytest.mark.parametrize("ownership",["PURCHASED","FAMILY_SHARED"])
def test_apple_decoder_preserves_ownership(settings, ownership):
    from types import SimpleNamespace
    decoded=SimpleNamespace(transactionId="1",originalTransactionId="2",productId=settings.lifetime_product_id,
        purchaseDate=1800000000000,inAppOwnershipType=ownership,appTransactionId="recipient",environment="Sandbox")
    value=AppleStoreVerifier(settings)._transaction(decoded)
    assert value.ownership_type==ownership and value.app_account_token is None and value.app_transaction_id=="recipient"

async def test_entitlement_read_cannot_extend_purchase_token(api_client, settings):
    data=await exchange(api_client,settings)
    result=await api_client[0].get("/v1/entitlements",headers={"Authorization":"Bearer "+data["access_token"]})
    assert result.status_code==200 and result.json()["access_token"] is None
    assert result.headers["cache-control"]=="no-store"

async def test_same_family_member_can_receive_new_grant_after_revocation(api_client, settings):
    await exchange(api_client, settings, revoked_at=datetime.now(UTC))
    replacement=await exchange(api_client, settings, transaction_id="new-member-grant")
    assert replacement["status"]=="active"

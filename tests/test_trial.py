from datetime import UTC, datetime, timedelta
from dataclasses import replace
from types import SimpleNamespace

from app.billing.apple import AppleStoreVerifier, VerifiedStoreTransaction
from app.security import TokenSigner
from conftest import sign_in


async def test_guest_trial_links_with_original_deadline_and_no_grace(api_client, settings):
    client, _, email, _, apple = api_client
    login = await sign_in(client, email)
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    start = datetime.now(UTC) - timedelta(days=3)
    apple.transaction = VerifiedStoreTransaction("trial-1", "trial-1", settings.trial_product_id,
        None, "Sandbox", start, None, None)
    async def submit():
        return await client.post("/v1/iap/apple/transactions", headers=headers,
            json={"signed_transaction": "signed-trial-transaction-long-enough"})
    first = await submit()
    assert first.status_code == 200
    assert first.json()["plan"] == "trial"
    assert first.json()["status"] == "active"
    assert datetime.fromisoformat(first.json()["valid_until"]) == start + timedelta(days=7)
    assert first.json()["offline_until"] == first.json()["valid_until"]
    repeated = await submit()
    assert repeated.json()["valid_until"] == first.json()["valid_until"]
    other = await sign_in(client, email, "other-trial@example.com")
    stolen = await client.post("/v1/iap/apple/transactions",
        headers={"Authorization": f"Bearer {other['access_token']}"},
        json={"signed_transaction": "signed-trial-transaction-long-enough"})
    assert stolen.status_code == 409


async def test_expired_trial_has_no_playback_grace(api_client, settings):
    client, _, email, _, apple = api_client
    login = await sign_in(client, email)
    apple.transaction = VerifiedStoreTransaction("trial-expired", "trial-expired", settings.trial_product_id,
        None, "Sandbox", datetime.now(UTC) - timedelta(days=7, seconds=1), None, None)
    result = await client.post("/v1/iap/apple/transactions",
        headers={"Authorization": f"Bearer {login['access_token']}"},
        json={"signed_transaction": "signed-trial-transaction-long-enough"})
    assert result.status_code == 200
    assert result.json()["status"] == "inactive"
    assert result.json()["plan"] == "trial"
    assert result.json()["valid_until"] == result.json()["offline_until"]


async def test_lifetime_wins_over_trial_and_trial_refund_revokes(api_client, settings):
    client, _, email, _, apple = api_client
    login = await sign_in(client, email)
    user_id = TokenSigner(settings).decode_access(login["access_token"]).user_id
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    async def submit():
        return await client.post("/v1/iap/apple/transactions", headers=headers,
            json={"signed_transaction": "signed-trial-transaction-long-enough"})
    trial = VerifiedStoreTransaction("trial-refund", "trial-refund", settings.trial_product_id,
        None, "Sandbox", datetime.now(UTC), None, None)
    apple.transaction = trial
    assert (await submit()).json()["status"] == "active"
    apple.transaction = replace(trial, revoked_at=datetime.now(UTC))
    assert (await submit()).json()["status"] == "inactive"
    apple.transaction = VerifiedStoreTransaction("lifetime", "lifetime", settings.lifetime_product_id,
        user_id, "Sandbox", datetime.now(UTC), None, None)
    assert (await submit()).json()["plan"] == "lifetime"
    apple.transaction = trial
    assert (await submit()).json()["plan"] == "lifetime"


def test_trial_adapter_uses_original_purchase_date(settings):
    start = datetime.now(UTC) - timedelta(days=3)
    decoded = SimpleNamespace(transactionId="1", originalTransactionId="1",
        productId=settings.trial_product_id, purchaseDate=int(datetime.now(UTC).timestamp()*1000),
        originalPurchaseDate=int(start.timestamp()*1000), environment="Sandbox")
    value = AppleStoreVerifier(settings)._transaction(decoded)
    assert abs((value.purchased_at-start).total_seconds()) < .001


async def test_trial_download_authorization_ends_at_seven_days(api_client, session_factory, settings):
    from test_catalog import seed_catalog
    client, _, email, storage, apple = api_client
    await seed_catalog(session_factory)
    login = await sign_in(client, email)
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    apple.transaction = VerifiedStoreTransaction("trial-download", "trial-download", settings.trial_product_id,
        None, "Sandbox", datetime.now(UTC)-timedelta(days=1), None, None)
    async def submit():
        return await client.post("/v1/iap/apple/transactions", headers=headers,
            json={"signed_transaction": "signed-trial-transaction-long-enough"})
    assert (await submit()).json()["status"] == "active"
    storage.manifests["packs/willow.json"] = {"schemaVersion": 1, "voiceID": "voice_willow", "files": {}}
    path = "/v1/voice-packs/voice_willow/download"
    assert (await client.post(path, headers=headers, json={"locale": "en-US"})).status_code == 200
    apple.transaction = replace(apple.transaction, purchased_at=datetime.now(UTC)-timedelta(days=8))
    assert (await submit()).json()["status"] == "inactive"
    assert (await client.post(path, headers=headers, json={"locale": "en-US"})).status_code == 403

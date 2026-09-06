from datetime import datetime, UTC, timedelta
from dataclasses import replace
from sqlalchemy import select
from app.models import StoreTransaction
from app.billing.apple import VerifiedStoreTransaction
from app.billing.service import _save_transaction
from app.billing.entitlements import entitlement_state
from conftest import sign_in

async def test_recorded_revocation_cannot_be_erased(session_factory, settings):
    now = datetime.now(UTC)
    tx = VerifiedStoreTransaction('replay', 'lineage', settings.lifetime_product_id, None, 'Sandbox', now, None, now, signed_at=now)
    async with session_factory() as db:
        await _save_transaction(db, tx, settings, None)
        await db.flush()
        item = await _save_transaction(db, replace(tx, revoked_at=None, signed_at=now+timedelta(minutes=1)), settings, None)
        assert item.status == 'revoked'
        assert item.revoked_at is not None

async def test_old_signed_update_cannot_revoke_newer_transaction(session_factory, settings):
    now = datetime.now(UTC)
    tx = VerifiedStoreTransaction('ordered', 'ordered-lineage', settings.lifetime_product_id, None, 'Sandbox', now, None, None, signed_at=now)
    async with session_factory() as db:
        await _save_transaction(db, tx, settings, None)
        await db.flush()
        item = await _save_transaction(db, replace(tx, revoked_at=now, signed_at=now-timedelta(minutes=1)), settings, None)
        assert item.status == 'active'
        assert item.revoked_at is None

async def test_wrong_environment_rejected(api_client, settings):
    client, _, mail, _, apple = api_client
    tokens = await sign_in(client, mail)
    apple.transaction = VerifiedStoreTransaction('wrong-env', 'wrong-env', settings.lifetime_product_id, None, 'Production', datetime.now(UTC), None, None)
    result = await client.post('/v1/iap/apple/transactions', headers={'Authorization': 'Bearer '+tokens['access_token']}, json={'signed_transaction': 'a-valid-length-but-fake-signed-transaction'})
    assert result.status_code == 400
    assert result.json()['detail']['code'] == 'wrong_environment'

async def test_latest_refund_supersedes_older_period(api_client, session_factory, settings):
    client, _, mail, _, _ = api_client
    tokens = await sign_in(client, mail)
    account = (await client.get('/v1/me',headers={'Authorization': 'Bearer '+tokens['access_token']})).json()['id']
    now = datetime.now(UTC)
    async with session_factory() as db:
        old = VerifiedStoreTransaction('old-paid', 'renewed-lineage', settings.lifetime_product_id, account, 'Sandbox', now-timedelta(days=1), None, None)
        new = replace(old, transaction_id='refunded-new', purchased_at=now, revoked_at=now)
        await _save_transaction(db, old, settings, account)
        await _save_transaction(db, new, settings, account)
        await db.flush()
        assert (await entitlement_state(db, account, settings)).status == 'inactive'

async def test_deleted_account_restoration_stays_owned(api_client, settings):
    client, _, mail, _, apple = api_client
    first = await sign_in(client, mail)
    auth = {'Authorization': 'Bearer '+first['access_token']}
    owner = (await client.get('/v1/me', headers=auth)).json()['id']
    apple.transaction = VerifiedStoreTransaction('restore-tx', 'restore-lineage', settings.lifetime_product_id, owner, 'Sandbox', datetime.now(UTC), None, None)
    body = {'signed_transaction': 'a-valid-length-but-fake-signed-transaction'}
    assert (await client.post('/v1/iap/apple/transactions', headers=auth, json=body)).status_code == 200
    second = await sign_in(client, mail, 'second@example.com')
    other = {'Authorization': 'Bearer '+second['access_token']}
    rejected = await client.post('/v1/iap/apple/transactions', headers=other, json=body)
    assert rejected.status_code == 409
    assert (await client.delete('/v1/account', headers=auth)).status_code == 204
    for _ in range(2):
        restored = await client.post('/v1/iap/apple/transactions', headers=other, json=body)
        assert restored.status_code == 200
        assert restored.json()['plan'] == 'lifetime'
    assert (await client.get('/v1/entitlements', headers=other)).json()['status'] == 'active'

async def test_apple_reconciliation_refreshes_non_consumable(monkeypatch, settings):
    from types import SimpleNamespace as NS
    from app.billing.apple import AppleStoreVerifier
    import appstoreserverlibrary.api_client as apple_api
    now = datetime.now(UTC)
    old = VerifiedStoreTransaction('original', 'lineage-api', settings.lifetime_product_id, None, 'Sandbox', now, None, None)
    refunded = replace(old, revoked_at=now, signed_at=now)
    closed = []
    class Client:
        def __init__(self, *args): pass
        async def get_transaction_info(self, identifier):
            assert identifier == old.transaction_id
            return NS(signedTransactionInfo='fresh')
        async def async_close(self): closed.append(True)
    monkeypatch.setattr(apple_api, 'AsyncAppStoreServerAPIClient', Client)
    verifier = AppleStoreVerifier(settings.model_copy(update={'apple_api_key_id': 'test', 'apple_api_issuer_id': 'test', 'apple_api_private_key': 'test'}))
    async def verify(payload):
        assert payload == 'fresh'
        return refunded
    monkeypatch.setattr(verifier, 'verify_transaction', verify)
    assert await verifier.reconcile_transaction(old) == refunded
    assert closed == [True]

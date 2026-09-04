from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import AuthSession, CompanionDefinition, StoreTransaction, VoiceDefinition, VoicePackVersion
from app.security import TokenSigner
from conftest import sign_in


async def seed_catalog(session_factory) -> None:
    async with session_factory() as db:
        db.add_all(
            [
                VoiceDefinition(
                    id="voice_willow",
                    display_name="Willow",
                    description="Warm and calm",
                    tier="premium",
                    supported_locales=["en-US"],
                    provider="private-provider",
                    provider_voice_id="provider-secret",
                    model="private-model",
                    instructions="private instructions",
                    instruction_version="v1",
                    preview_object_key="previews/willow.aac",
                    status="active",
                    sort_order=1,
                ),
                CompanionDefinition(
                    id="stayzy.default",
                    display_name="Stayzy",
                    description="Original companion",
                    tier="free",
                    status="active",
                    sort_order=0,
                ),
                CompanionDefinition(
                    id="stayzy.premium",
                    display_name="Sprout",
                    description="Premium companion",
                    tier="premium",
                    status="active",
                    sort_order=1,
                ),
                VoicePackVersion(
                    voice_id="voice_willow",
                    locale="en-US",
                    catalog_version="catalog-1",
                    version="2026.09.1",
                    archive_object_key="packs/willow.zip",
                    manifest_object_key="packs/willow.json",
                    sha256="a" * 64,
                    size_bytes=1234,
                    status="active",
                ),
            ]
        )
        await db.commit()


async def test_catalog_is_provider_neutral_and_locked_for_guests(api_client, session_factory) -> None:
    client, _, _, _, _ = api_client
    await seed_catalog(session_factory)

    response = await client.get("/v1/catalog/voices?locale=en-US")
    assert response.status_code == 200
    item = response.json()["voices"][0]
    assert item["id"] == "voice_willow"
    assert item["is_locked"] is True
    serialized = response.text.lower()
    for private_value in ["provider", "private-model", "provider-secret", "instructions"]:
        assert private_value not in serialized

    companions = await client.get("/v1/catalog/companions")
    assert [item["is_locked"] for item in companions.json()["companions"]] == [False, True]


async def test_active_premium_can_download_pack(api_client, session_factory, settings) -> None:
    client, _, email, storage, _ = api_client
    await seed_catalog(session_factory)
    signed_in = await sign_in(client, email)
    claims = TokenSigner(settings).decode_access(str(signed_in["access_token"]))
    now = datetime.now(UTC)
    async with session_factory() as db:
        db.add(
            StoreTransaction(
                transaction_id="transaction-1",
                original_transaction_id="original-1",
                user_id=claims.user_id,
                billing_subject="subject",
                product_id=settings.monthly_product_id,
                environment="Sandbox",
                status="active",
                purchased_at=now,
                expires_at=now + timedelta(days=30),
            )
        )
        await db.commit()
    storage.manifests["packs/willow.json"] = {
        "schemaVersion": 1,
        "voiceID": "voice_willow",
        "files": {"ready.001": {"path": "ready.001.aac", "sha256": "b" * 64}},
    }
    headers = {"Authorization": f"Bearer {signed_in['access_token']}"}

    catalog = await client.get("/v1/catalog/voices?locale=en-US", headers=headers)
    assert catalog.json()["voices"][0]["is_locked"] is False
    download = await client.post(
        "/v1/voice-packs/voice_willow/download",
        headers=headers,
        json={"locale": "en-US"},
    )
    assert download.status_code == 200
    assert download.json()["sha256"] == "a" * 64
    assert "signature=private" in download.json()["archive_url"]


async def test_expired_subscription_grace_allows_playback_but_not_download(
    api_client,
    session_factory,
    settings,
) -> None:
    client, _, email, _, _ = api_client
    await seed_catalog(session_factory)
    signed_in = await sign_in(client, email)
    claims = TokenSigner(settings).decode_access(str(signed_in["access_token"]))
    now = datetime.now(UTC)
    async with session_factory() as db:
        db.add(
            StoreTransaction(
                transaction_id="transaction-grace",
                original_transaction_id="original-grace",
                user_id=claims.user_id,
                billing_subject="subject",
                product_id=settings.monthly_product_id,
                environment="Sandbox",
                status="expired",
                purchased_at=now - timedelta(days=31),
                expires_at=now - timedelta(days=1),
            )
        )
        await db.commit()
    headers = {"Authorization": f"Bearer {signed_in['access_token']}"}

    entitlement = await client.get("/v1/entitlements", headers=headers)
    assert entitlement.json()["status"] == "grace"
    download = await client.post(
        "/v1/voice-packs/voice_willow/download",
        headers=headers,
        json={"locale": "en-US"},
    )
    assert download.status_code == 403
    assert download.json()["detail"]["code"] == "premium_required"

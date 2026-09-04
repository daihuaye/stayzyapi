from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import func, select

from app.models import AuthSession, MagicLink, User
from app.security import sha256
from conftest import sign_in


async def test_magic_link_is_hashed_single_use_and_returns_generic_response(
    api_client,
    session_factory,
) -> None:
    client, _, email, _, _ = api_client
    response = await client.post("/v1/auth/magic-links", json={"email": " Person@Example.COM "})
    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    token = email.latest_token

    async with session_factory() as db:
        challenge = await db.scalar(select(MagicLink))
        assert challenge is not None
        assert challenge.email == "person@example.com"
        assert challenge.token_hash == sha256(token)
        assert token not in challenge.token_hash

    first = await client.post("/v1/auth/magic-links/verify", json={"token": token})
    second = await client.post("/v1/auth/magic-links/verify", json={"token": token})
    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["detail"]["code"] == "invalid_magic_link"


async def test_magic_link_rate_limits_without_revealing_state(
    api_client,
    session_factory,
) -> None:
    client, _, email, _, _ = api_client
    for _ in range(4):
        response = await client.post("/v1/auth/magic-links", json={"email": "person@example.com"})
        assert response.status_code == 202
        assert response.json() == {"status": "accepted"}
    assert len(email.deliveries) == 3
    async with session_factory() as db:
        challenges = list(await db.scalars(select(MagicLink).order_by(MagicLink.created_at)))
        assert len(challenges) == 4
        assert challenges[-1].send_state == "throttled"


async def test_browser_get_does_not_consume_magic_link(api_client, session_factory) -> None:
    client, _, email, _, _ = api_client
    await client.post("/v1/auth/magic-links", json={"email": "person@example.com"})
    token = email.latest_token
    landing = await client.get(f"/auth/verify?token={token}")
    assert landing.status_code == 200
    assert "Open Stayzy" in landing.text

    async with session_factory() as db:
        challenge = await db.scalar(select(MagicLink))
        assert challenge is not None
        assert challenge.used_at is None

    verified = await client.post("/v1/auth/magic-links/verify", json={"token": token})
    assert verified.status_code == 200


async def test_refresh_rotation_detects_reuse_and_revokes_family(
    api_client,
    session_factory,
) -> None:
    client, _, email, _, _ = api_client
    signed_in = await sign_in(client, email)
    original_refresh = str(signed_in["refresh_token"])

    rotated = await client.post("/v1/auth/refresh", json={"refresh_token": original_refresh})
    assert rotated.status_code == 200
    reused = await client.post("/v1/auth/refresh", json={"refresh_token": original_refresh})
    assert reused.status_code == 401
    assert reused.json()["detail"]["code"] == "session_compromised"

    async with session_factory() as db:
        active = await db.scalar(
            select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None))
        )
        assert active == 0


async def test_refresh_preserves_original_authentication_time(
    api_client,
    session_factory,
) -> None:
    client, _, email, _, _ = api_client
    signed_in = await sign_in(client, email)
    original_authentication = datetime.now(UTC) - timedelta(hours=1)
    async with session_factory() as db:
        session = await db.scalar(select(AuthSession))
        assert session is not None
        session.authenticated_at = original_authentication
        await db.commit()

    rotated = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": signed_in["refresh_token"]},
    )
    assert rotated.status_code == 200
    claims = jwt.decode(
        rotated.json()["access_token"],
        options={"verify_signature": False},
    )
    assert claims["auth_time"] == int(original_authentication.timestamp())


async def test_me_logout_and_account_deletion(api_client, session_factory) -> None:
    client, _, email, _, _ = api_client
    signed_in = await sign_in(client, email)
    headers = {"Authorization": f"Bearer {signed_in['access_token']}"}

    me = await client.get("/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["masked_email"].endswith("@example.com")
    assert me.json()["entitlement"]["status"] == "inactive"

    deleted = await client.delete("/v1/account", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get("/v1/me", headers=headers)).status_code == 401

    async with session_factory() as db:
        user = await db.scalar(select(User))
        assert user is not None
        assert user.status == "deleted"
        assert user.email.endswith("@invalid")

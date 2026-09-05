from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.db import Base, get_db
from app.routers import auth, catalog, entitlements, health, iap, links, webhooks
from app.services.apple_store import VerifiedNotification, VerifiedStoreTransaction
from app.services.email import EmailSendResult


class FakeEmailSender:
    def __init__(self) -> None:
        self.deliveries: list[tuple[str, str, str]] = []

    async def send_magic_link(
        self,
        email: str,
        magic_link: str,
        challenge_id: str,
    ) -> EmailSendResult:
        self.deliveries.append((email, magic_link, challenge_id))
        return EmailSendResult(accepted=True, message_id=f"message-{challenge_id}")

    @property
    def latest_token(self) -> str:
        query = parse_qs(urlparse(self.deliveries[-1][1]).query)
        return query["token"][0]


class FakeStorage:
    def __init__(self) -> None:
        self.available = True
        self.manifests: dict[str, dict[str, object]] = {}

    async def presign_get(self, key: str) -> tuple[str, datetime]:
        if not self.available:
            raise RuntimeError("storage unavailable")
        return f"https://objects.example.test/{key}?signature=private", datetime.now(UTC) + timedelta(minutes=5)

    async def get_json(self, key: str) -> dict[str, object]:
        if not self.available:
            raise RuntimeError("storage unavailable")
        return self.manifests[key]

    async def ready(self) -> bool:
        return self.available


@dataclass
class FakeAppleVerifier:
    transaction: VerifiedStoreTransaction | None = None
    notification: VerifiedNotification | None = None

    async def verify_transaction(self, _: str) -> VerifiedStoreTransaction:
        assert self.transaction is not None
        return self.transaction

    async def reconcile_transaction(self, transaction: VerifiedStoreTransaction) -> VerifiedStoreTransaction:
        return transaction

    async def verify_notification(self, _: str) -> VerifiedNotification:
        assert self.notification is not None
        return self.notification


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        public_app_url="https://links.example.test",
        development_jwt_secret="test-secret-that-is-at-least-32-bytes",
        rate_limit_salt="test-rate-limit-salt",
        allowed_hosts=["testserver", "localhost"],
        sendgrid_webhook_public_key=None,
    )


@pytest_asyncio.fixture
async def session_factory(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def api_client(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI, FakeEmailSender, FakeStorage, FakeAppleVerifier]]:
    app = FastAPI()
    from app.observability import install_diagnostics
    install_diagnostics(app)
    app.include_router(health.router)
    app.include_router(links.router)
    app.include_router(auth.router)
    app.include_router(auth.account_router)
    app.include_router(catalog.router)
    app.include_router(entitlements.router)
    app.include_router(iap.router)
    app.include_router(webhooks.router)

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    email = FakeEmailSender()
    storage = FakeStorage()
    apple = FakeAppleVerifier()
    app.state.email_sender = email
    app.state.storage = storage
    app.state.apple_store_verifier = apple
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, app, email, storage, apple


async def sign_in(
    client: httpx.AsyncClient,
    email: FakeEmailSender,
    address: str = "person@example.com",
) -> dict[str, str | int]:
    response = await client.post("/v1/auth/magic-links", json={"email": address})
    assert response.status_code == 202
    verified = await client.post(
        "/v1/auth/magic-links/verify",
        json={"token": email.latest_token},
    )
    assert verified.status_code == 200
    return verified.json()

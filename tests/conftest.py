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
from app.admin_models import Administrator, AdministratorLock
from app.admin_security import hash_password
from app.db import Base, get_db
from app.routers import auth, catalog, entitlements, health, iap, links, webhooks
from app.services.apple_store import VerifiedNotification, VerifiedStoreTransaction
from app.services.email import EmailSendResult


class FakeEmailSender:
    def __init__(self) -> None:
        self.deliveries: list[tuple[str, str, str]] = []
        self.admin_deliveries: list[tuple[str, str, str]] = []

    async def send_magic_link(
        self,
        email: str,
        magic_link: str,
        challenge_id: str,
    ) -> EmailSendResult:
        self.deliveries.append((email, magic_link, challenge_id))
        return EmailSendResult(accepted=True, message_id=f"message-{challenge_id}")

    async def send_admin_reset(self, email, link, challenge_id):
        self.admin_deliveries.append((email, link, challenge_id))
        return EmailSendResult(accepted=True)

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
def settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
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
    async with factory() as db:
        db.add(AdministratorLock(id=1, revision=0))
        await db.commit()
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
    from app.routers import experiments, admin
    app.include_router(admin.router)
    app.include_router(experiments.router)
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


@pytest_asyncio.fixture
async def admin_headers(api_client, session_factory):
    async with session_factory() as db:
        db.add(Administrator(email="test-owner@example.com", role="owner", active=True,
                             password_hash=await hash_password("test owner password 123"), must_change_password=False))
        await db.commit()
    response = await api_client[0].post("/v1/admin/auth/login", json={"email": "test-owner@example.com", "password": "test owner password 123"})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["session_token"]}

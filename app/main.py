from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.observability import emit, install_diagnostics
from app.config import get_settings
from app.routers import auth, catalog, entitlements, health, iap, links, webhooks
from app.services.apple_store import AppleStoreVerifier
from app.services.email import SendGridEmailSender
from app.services.storage import ObjectStorage


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    emit("service.started", environment=settings.environment,
         apple_environment=settings.apple_environment, email_key_configured=bool(settings.sendgrid_api_key),
         email_template_configured=bool(settings.sendgrid_magic_link_template_id),
         email_sender_configured=bool(settings.sendgrid_from_email),
         email_webhook_configured=bool(settings.sendgrid_webhook_public_key),
         database_driver=settings.database_url.split(":", 1)[0])
    app.state.email_sender = SendGridEmailSender(settings)
    app.state.storage = ObjectStorage(settings)
    app.state.apple_store_verifier = AppleStoreVerifier(settings)
    yield
    await app.state.email_sender.close()


settings = get_settings()
app = FastAPI(
    title="Stayzy API",
    version="0.1.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
install_diagnostics(app)
app.include_router(health.router)
app.include_router(links.router)
app.include_router(auth.router)
app.include_router(auth.account_router)
app.include_router(catalog.router)
app.include_router(entitlements.router)
app.include_router(iap.router)
app.include_router(webhooks.router)

from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from contextlib import suppress
from app.jobs.purge_telemetry import main as purge_telemetry

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.observability import emit, install_diagnostics
from app.routers import experiments, admin, telemetry, admin_telemetry
from app.config import get_settings
from app.routers import catalog, entitlements, health, iap
from app.services.apple_store import AppleStoreVerifier
from app.services.email import SendGridEmailSender
from app.services.storage import ObjectStorage


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    emit("service.started", environment=settings.environment,
         apple_environment=settings.apple_environment, email_key_configured=bool(settings.sendgrid_api_key),
         email_sender_configured=bool(settings.sendgrid_from_email),
         database_driver=settings.database_url.split(":", 1)[0])
    app.state.email_sender = SendGridEmailSender(settings)
    app.state.storage = ObjectStorage(settings)
    app.state.apple_store_verifier = AppleStoreVerifier(settings)
    async def retention_loop():
        while True:
            try:
                await purge_telemetry()
            except Exception:
                emit("telemetry.purge_failed")
            await asyncio.sleep(86400)
    retention_task = asyncio.create_task(retention_loop())
    try:
        yield
    finally:
        retention_task.cancel()
        with suppress(asyncio.CancelledError):
            await retention_task
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
app.include_router(telemetry.router)
app.include_router(admin_telemetry.router)
app.include_router(experiments.router)
app.include_router(admin.router)
app.include_router(health.router)
app.include_router(catalog.router)
app.include_router(entitlements.router)
app.include_router(iap.router)

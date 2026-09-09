from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Header
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.admin_models import Administrator, AdministratorAttempt, AdministratorLock, AdministratorReset, AdministratorSession
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import api_error
from app.security import normalize_email, sha256

password_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = password_hasher.hash("unused dummy administrator password")
SESSION_HOURS = 8


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def hash_password(password: str) -> str:
    return await run_in_threadpool(password_hasher.hash, password)


async def verify_password(encoded: str, password: str) -> bool:
    try:
        return await run_in_threadpool(password_hasher.verify, encoded, password)
    except (VerificationError, InvalidHashError):
        return False


async def security_lock(db: AsyncSession) -> None:
    # The migration seeds this row. No process-local lock can protect multiple replicas.
    result = await db.execute(update(AdministratorLock).where(AdministratorLock.id == 1)
                              .values(revision=AdministratorLock.revision + 1))
    if result.rowcount != 1:
        raise api_error(503, "admin_unavailable", "Administrator database migration is required.")


async def revoke_all(db: AsyncSession, administrator_id: str) -> None:
    now = datetime.now(UTC)
    await db.execute(update(AdministratorSession).where(
        AdministratorSession.administrator_id == administrator_id,
        AdministratorSession.revoked_at.is_(None)).values(revoked_at=now))
    await db.execute(update(AdministratorReset).where(
        AdministratorReset.administrator_id == administrator_id,
        AdministratorReset.used_at.is_(None)).values(used_at=now))


@dataclass
class AdminPrincipal:
    account: Administrator
    session: AdministratorSession


async def require_admin_session(db: AsyncSession = Depends(get_db),
                                authorization: str | None = Header(default=None)) -> AdminPrincipal:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 256:
        raise api_error(401, "admin_unauthorized", "Sign in is required.")
    await security_lock(db)
    session = await db.scalar(select(AdministratorSession).where(
        AdministratorSession.token_hash == sha256(token),
        AdministratorSession.revoked_at.is_(None),
        AdministratorSession.expires_at > datetime.now(UTC)))
    account = await db.get(Administrator, session.administrator_id) if session else None
    if not session or not account or not account.active:
        raise api_error(401, "admin_unauthorized", "The session is invalid or expired.")
    return AdminPrincipal(account, session)


async def require_administrator(principal: AdminPrincipal = Depends(require_admin_session)) -> AdminPrincipal:
    if principal.account.must_change_password:
        raise api_error(403, "password_change_required", "Change your temporary password first.")
    return principal


async def require_owner(principal: AdminPrincipal = Depends(require_administrator)) -> AdminPrincipal:
    if principal.account.role != "owner":
        raise api_error(403, "owner_required", "Only owners can manage administrator accounts.")
    return principal


async def record_attempt(db: AsyncSession, email: str, kind: str, settings: Settings) -> AdministratorAttempt | None:
    """Call while holding security_lock; record before password hashing or email sending."""
    now = datetime.now(UTC)
    await db.execute(delete(AdministratorAttempt).where(AdministratorAttempt.created_at < now - timedelta(days=1)))
    digest = sha256(f"{settings.rate_limit_salt}:{normalize_email(email)}")
    personal = await db.scalar(select(func.count()).select_from(AdministratorAttempt).where(
        AdministratorAttempt.kind == kind, AdministratorAttempt.email_hash == digest,
        AdministratorAttempt.failed.is_(True), AdministratorAttempt.created_at > now - timedelta(minutes=15)))
    global_count = await db.scalar(select(func.count()).select_from(AdministratorAttempt).where(
        AdministratorAttempt.kind == kind, AdministratorAttempt.created_at > now - timedelta(minutes=1)))
    personal_limit = 5 if kind == "login" else 3
    global_limit = settings.admin_login_budget if kind == "login" else settings.admin_reset_budget
    if (personal or 0) >= personal_limit or (global_count or 0) >= global_limit:
        return None
    attempt = AdministratorAttempt(kind=kind, email_hash=digest, failed=True)
    db.add(attempt)
    await db.flush()
    return attempt

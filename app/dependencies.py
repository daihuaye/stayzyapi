from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.errors import api_error
from app.models import AuthSession, User
from app.security import AccessClaims, TokenError, TokenSigner


bearer = HTTPBearer(auto_error=False)


def get_signer(settings: Settings = Depends(get_settings)) -> TokenSigner:
    return TokenSigner(settings)


async def optional_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    signer: TokenSigner = Depends(get_signer),
) -> AccessClaims | None:
    if credentials is None:
        return None
    try:
        return signer.decode_access(credentials.credentials)
    except TokenError:
        return None


async def require_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    signer: TokenSigner = Depends(get_signer),
) -> AccessClaims:
    if credentials is None:
        raise api_error(401, "authentication_required", "Sign in is required.")
    try:
        return signer.decode_access(credentials.credentials)
    except TokenError as error:
        raise api_error(401, "authentication_required", "The session is invalid or expired.") from error


async def require_user(
    claims: AccessClaims = Depends(require_claims),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await db.scalar(select(User).where(User.id == claims.user_id, User.status == "active"))
    if user is None:
        raise api_error(401, "authentication_required", "The account is unavailable.")
    session = await db.scalar(
        select(AuthSession).where(
            AuthSession.id == claims.session_id,
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    if session is None:
        raise api_error(401, "authentication_required", "The session is no longer active.")
    return user


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", maxsplit=1)[0].strip()
    return request.client.host if request.client else "unknown"


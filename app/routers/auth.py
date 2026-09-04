from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging

from app.observability import emit, failure_fields
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import client_ip, get_signer, require_claims, require_user
from app.errors import api_error
from app.models import AuthSession, Entitlement, MagicLink, StoreTransaction, User
from app.schemas import AccountResponse, MagicLinkRequest, MagicLinkVerifyRequest, RefreshRequest, TokenResponse
from app.security import AccessClaims, TokenSigner, generate_secret, hash_ip, mask_email, normalize_email, sha256
from app.services.email import SendGridEmailSender
from app.services.entitlements import entitlement_response


router = APIRouter(prefix="/v1/auth", tags=["authentication"])
account_router = APIRouter(prefix="/v1", tags=["account"])


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@router.post("/magic-links", status_code=202)
async def request_magic_link(
    body: MagicLinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    emit("auth.magic_link_requested")
    now = datetime.now(UTC)
    email = normalize_email(str(body.email))
    address_hash = hash_ip(client_ip(request), settings.rate_limit_salt)
    email_count = await db.scalar(
        select(func.count()).select_from(MagicLink).where(
            MagicLink.email == email,
            MagicLink.created_at >= now - timedelta(minutes=15),
        )
    )
    ip_count = await db.scalar(
        select(func.count()).select_from(MagicLink).where(
            MagicLink.requested_ip_hash == address_hash,
            MagicLink.created_at >= now - timedelta(hours=1),
        )
    )
    if (email_count or 0) >= 3 or (ip_count or 0) >= 10:
        emit("auth.magic_link_throttled", reason="email_limit" if (email_count or 0) >= 3 else "ip_limit")
        # Keep a minimal audit record while returning the same response shape.
        # The synthetic token is never returned or sent and cannot authenticate.
        db.add(
            MagicLink(
                email=email,
                token_hash=sha256(generate_secret()),
                requested_ip_hash=address_hash,
                expires_at=now + timedelta(minutes=15),
                send_state="throttled",
            )
        )
        await db.commit()
        return {"status": "accepted"}

    token = generate_secret()
    challenge = MagicLink(
        email=email,
        token_hash=sha256(token),
        requested_ip_hash=address_hash,
        expires_at=now + timedelta(minutes=15),
    )
    db.add(challenge)
    await db.commit()
    await db.refresh(challenge)

    query = urlencode({"token": token})
    magic_url = f"{settings.public_app_url.rstrip('/')}/auth/verify?{query}"
    sender: SendGridEmailSender = request.app.state.email_sender
    try:
        result = await sender.send_magic_link(email, magic_url, challenge.id)
        challenge.send_state = "accepted" if result.accepted else "failed"
        challenge.sendgrid_message_id = result.message_id
    except Exception as error:
        emit("auth.magic_link_send_exception", level=logging.ERROR, **failure_fields(error))
        challenge.send_state = "failed"
    await db.commit()
    emit("auth.magic_link_processed", send_state=challenge.send_state)
    return {"status": "accepted"}


@router.post("/magic-links/verify", response_model=TokenResponse)
async def verify_magic_link(
    body: MagicLinkVerifyRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    signer: TokenSigner = Depends(get_signer),
) -> TokenResponse:
    now = datetime.now(UTC)
    challenge = await db.scalar(
        select(MagicLink).where(MagicLink.token_hash == sha256(body.token)).with_for_update()
    )
    if challenge is None or challenge.used_at is not None or _aware(challenge.expires_at) <= now:
        raise api_error(400, "invalid_magic_link", "This sign-in link is invalid or expired.")

    challenge.attempt_count += 1
    challenge.used_at = now
    user = await db.scalar(select(User).where(User.email == challenge.email, User.status == "active"))
    if user is None:
        user = User(email=challenge.email)
        db.add(user)
        await db.flush()

    refresh_token = generate_secret()
    session = AuthSession(
        user_id=user.id,
        token_hash=sha256(refresh_token),
        authenticated_at=now,
        expires_at=now + timedelta(days=settings.refresh_token_days),
    )
    db.add(session)
    await db.commit()
    return TokenResponse(
        access_token=signer.access_token(user.id, session.id, now),
        refresh_token=refresh_token,
        expires_in=settings.access_token_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    signer: TokenSigner = Depends(get_signer),
) -> TokenResponse:
    now = datetime.now(UTC)
    current = await db.scalar(
        select(AuthSession).where(AuthSession.token_hash == sha256(body.refresh_token)).with_for_update()
    )
    if current is None or _aware(current.expires_at) <= now:
        raise api_error(401, "authentication_required", "The refresh token is invalid or expired.")
    if current.revoked_at is not None:
        if current.replaced_by_id is not None:
            await db.execute(
                update(AuthSession)
                .where(AuthSession.family_id == current.family_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await db.commit()
            raise api_error(401, "session_compromised", "This session was revoked for safety.")
        raise api_error(401, "authentication_required", "The session is no longer active.")

    token = generate_secret()
    replacement = AuthSession(
        family_id=current.family_id,
        user_id=current.user_id,
        token_hash=sha256(token),
        authenticated_at=_aware(current.authenticated_at),
        expires_at=now + timedelta(days=settings.refresh_token_days),
    )
    db.add(replacement)
    await db.flush()
    current.revoked_at = now
    current.replaced_by_id = replacement.id
    await db.commit()
    return TokenResponse(
        access_token=signer.access_token(
            current.user_id,
            replacement.id,
            _aware(current.authenticated_at),
        ),
        refresh_token=token,
        expires_in=settings.access_token_minutes * 60,
    )


@router.post("/logout", status_code=204)
async def logout(
    claims: AccessClaims = Depends(require_claims),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == claims.session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
    return Response(status_code=204)


@account_router.get("/me", response_model=AccountResponse)
async def me(
    user: User = Depends(require_user),
    claims: AccessClaims = Depends(require_claims),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    signer: TokenSigner = Depends(get_signer),
) -> AccountResponse:
    recent_cutoff = datetime.now(UTC) - timedelta(minutes=settings.recent_authentication_minutes)
    return AccountResponse(
        id=user.id,
        masked_email=mask_email(user.email),
        status=user.status,
        entitlement=await entitlement_response(db, user.id, settings, signer),
        requires_recent_authentication=claims.authenticated_at < recent_cutoff,
    )


@account_router.delete("/account", status_code=204)
async def delete_account(
    user: User = Depends(require_user),
    claims: AccessClaims = Depends(require_claims),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.recent_authentication_minutes)
    if claims.authenticated_at < cutoff:
        raise api_error(403, "recent_authentication_required", "Request a new sign-in link first.")
    now = datetime.now(UTC)
    original_email = user.email
    user.email = f"deleted+{user.id}@invalid"
    user.status = "deleted"
    user.deleted_at = now
    await db.execute(delete(MagicLink).where(MagicLink.email == original_email))
    await db.execute(update(AuthSession).where(AuthSession.user_id == user.id).values(revoked_at=now))
    await db.execute(delete(Entitlement).where(Entitlement.user_id == user.id))
    await db.execute(
        update(StoreTransaction)
        .where(StoreTransaction.user_id == user.id)
        .values(user_id=None)
    )
    await db.commit()
    return Response(status_code=204)

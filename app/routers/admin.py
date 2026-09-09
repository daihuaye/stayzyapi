from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_models import Administrator, AdministratorReset, AdministratorSession
from app.admin_security import (AdminPrincipal, DUMMY_HASH, SESSION_HOURS, hash_password,
                               record_attempt, require_admin_session, require_owner, revoke_all,
                               security_lock, verify_password, password_hasher)
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import api_error
from app.observability import emit, failure_fields
from app.security import generate_secret, normalize_email, sha256


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/v1/admin", tags=["administrators"], dependencies=[Depends(no_store)])
Password = Annotated[str, Field(strict=True, min_length=15, max_length=128)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Body):
    email: EmailStr
    password: Annotated[str, Field(strict=True, min_length=1, max_length=128)]


class ChangePassword(Body):
    current_password: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    new_password: Password


class EmailRequest(Body):
    email: EmailStr


class ResetPassword(Body):
    token: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    new_password: Password


class AccountCreate(Body):
    email: EmailStr
    role: Literal["owner", "admin"]
    temporary_password: Password


class AccountUpdate(Body):
    role: Literal["owner", "admin"] | None = None
    active: StrictBool | None = None


class AccountResponse(BaseModel):
    id: str
    email: str
    role: Literal["owner", "admin"]
    active: bool
    must_change_password: bool
    created_at: datetime


class LoginResponse(BaseModel):
    session_token: str
    expires_at: datetime
    account: AccountResponse


def account_response(account: Administrator) -> AccountResponse:
    return AccountResponse(id=account.id, email=account.email, role=account.role,
                           active=account.active, must_change_password=account.must_change_password,
                           created_at=account.created_at)


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: Login, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)):
    await security_lock(db)
    email = normalize_email(str(body.email))
    attempt = await record_attempt(db, email, "login", settings)
    if attempt is None:
        await db.commit()
        emit("admin.login_throttled")
        raise api_error(429, "admin_login_throttled", "Too many sign-in attempts. Try again in 15 minutes.")
    account = await db.scalar(select(Administrator).where(Administrator.email == email))
    valid = await verify_password(account.password_hash if account else DUMMY_HASH, body.password)
    if not valid or not account or not account.active:
        await db.commit()
        emit("admin.login_failed")
        raise api_error(401, "invalid_credentials", "Email or password was not accepted.")
    attempt.failed = False
    if password_hasher.check_needs_rehash(account.password_hash):
        account.password_hash = await hash_password(body.password)
    token = generate_secret()
    expiry = datetime.now(UTC) + timedelta(hours=SESSION_HOURS)
    db.add(AdministratorSession(administrator_id=account.id, token_hash=sha256(token), expires_at=expiry))
    await db.commit()
    emit("admin.login_succeeded", administrator_id=account.id)
    return LoginResponse(session_token=token, expires_at=expiry, account=account_response(account))


@router.get("/auth/me", response_model=AccountResponse)
async def me(principal: AdminPrincipal = Depends(require_admin_session)):
    return account_response(principal.account)


@router.post("/auth/logout", status_code=204)
async def logout(principal: AdminPrincipal = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
    principal.session.revoked_at = datetime.now(UTC)
    await db.commit()
    emit("admin.logout", administrator_id=principal.account.id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/auth/change-password", status_code=204)
async def change_password(body: ChangePassword, principal: AdminPrincipal = Depends(require_admin_session),
                          db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)):
    attempt = await record_attempt(db, principal.account.email, "login", settings)
    if attempt is None:
        await db.commit()
        raise api_error(429, "admin_login_throttled", "Too many attempts. Try again in 15 minutes.")
    if not await verify_password(principal.account.password_hash, body.current_password):
        await db.commit()
        raise api_error(400, "incorrect_password", "Your current password was not accepted.")
    attempt.failed = False
    if body.current_password == body.new_password:
        await db.commit()
        raise api_error(400, "password_unchanged", "Choose a different password.")
    principal.account.password_hash = await hash_password(body.new_password)
    principal.account.must_change_password = False
    await revoke_all(db, principal.account.id)
    await db.commit()
    emit("admin.password_changed", administrator_id=principal.account.id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/auth/forgot-password", status_code=202)
async def forgot_password(body: EmailRequest, request: Request, db: AsyncSession = Depends(get_db),
                          settings: Settings = Depends(get_settings)):
    await security_lock(db)
    email = normalize_email(str(body.email))
    attempt = await record_attempt(db, email, "reset", settings)
    account = await db.scalar(select(Administrator).where(Administrator.email == email, Administrator.active.is_(True)))
    acknowledgment = {"status": "accepted"}
    if attempt is None or account is None:
        await db.commit()
        return acknowledgment
    origin = urlsplit(settings.admin_web_url or "")
    if (origin.scheme not in {"http", "https"} or not origin.netloc or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in {"", "/"}
            or (settings.environment == "production" and origin.scheme != "https")):
        await db.commit()
        emit("admin.reset_delivery_failed", reason="invalid_web_origin")
        return acknowledgment
    now = datetime.now(UTC)
    from sqlalchemy import update
    await db.execute(update(AdministratorReset).where(AdministratorReset.administrator_id == account.id,
                     AdministratorReset.used_at.is_(None)).values(used_at=now))
    token = generate_secret()
    reset = AdministratorReset(administrator_id=account.id, token_hash=sha256(token), expires_at=now + timedelta(minutes=30))
    db.add(reset)
    await db.commit()
    link = f"{settings.admin_web_url.rstrip('/')}/admin/reset-password#token={quote(token, safe='')}"
    try:
        result = await request.app.state.email_sender.send_admin_reset(account.email, link, reset.id)
        emit("admin.reset_requested", administrator_id=account.id, accepted=result.accepted)
    except Exception as error:
        emit("admin.reset_delivery_failed", administrator_id=account.id, **failure_fields(error))
    return acknowledgment


@router.post("/auth/reset-password", status_code=204)
async def reset_password(body: ResetPassword, db: AsyncSession = Depends(get_db)):
    await security_lock(db)
    reset = await db.scalar(select(AdministratorReset).where(AdministratorReset.token_hash == sha256(body.token),
                       AdministratorReset.used_at.is_(None), AdministratorReset.expires_at > datetime.now(UTC)))
    account = await db.get(Administrator, reset.administrator_id) if reset else None
    if not reset or not account or not account.active:
        raise api_error(400, "invalid_reset", "This reset link is invalid or expired. Request a new one.")
    if await verify_password(account.password_hash, body.new_password):
        raise api_error(400, "password_unchanged", "Choose a different password.")
    account.password_hash = await hash_password(body.new_password)
    account.must_change_password = False
    await revoke_all(db, account.id)
    await db.commit()
    emit("admin.password_reset", administrator_id=account.id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/accounts", response_model=list[AccountResponse])
async def accounts(_: AdminPrincipal = Depends(require_owner), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Administrator).order_by(Administrator.created_at, Administrator.id))).all()
    return [account_response(account) for account in rows]


@router.post("/accounts", response_model=AccountResponse, status_code=201)
async def create_account(body: AccountCreate, principal: AdminPrincipal = Depends(require_owner), db: AsyncSession = Depends(get_db)):
    account = Administrator(email=normalize_email(str(body.email)), role=body.role,
                            password_hash=await hash_password(body.temporary_password), must_change_password=True)
    db.add(account)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise api_error(409, "admin_exists", "An administrator with that email already exists.")
    emit("admin.account_created", administrator_id=principal.account.id, target_administrator_id=account.id, role=account.role)
    return account_response(account)


@router.patch("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(account_id: str, body: AccountUpdate, principal: AdminPrincipal = Depends(require_owner),
                         db: AsyncSession = Depends(get_db)):
    if not body.model_fields_set or any(getattr(body, key) is None for key in body.model_fields_set):
        raise api_error(422, "invalid_account_update", "Provide a role or active status.")
    account = await db.get(Administrator, account_id)
    if account is None:
        raise api_error(404, "admin_not_found", "Administrator not found.")
    role = body.role if body.role is not None else account.role
    active = body.active if body.active is not None else account.active
    if account.id == principal.account.id and (not active or role != "owner"):
        raise api_error(409, "self_change_forbidden", "You cannot deactivate or demote your own account.")
    if account.role == "owner" and account.active and (role != "owner" or not active):
        owners = await db.scalar(select(func.count()).select_from(Administrator).where(Administrator.role == "owner", Administrator.active.is_(True)))
        if (owners or 0) <= 1:
            raise api_error(409, "last_owner", "At least one active owner must remain.")
    account.role, account.active = role, active
    if not active:
        await revoke_all(db, account.id)
    await db.commit()
    emit("admin.account_updated", administrator_id=principal.account.id, target_administrator_id=account.id, role=role, active=active)
    return account_response(account)

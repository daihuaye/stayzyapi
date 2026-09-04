from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings


class TokenError(ValueError):
    pass


@dataclass(frozen=True)
class AccessClaims:
    user_id: str
    session_id: str
    authenticated_at: datetime


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def normalize_email(email: str) -> str:
    local, separator, domain = email.strip().partition("@")
    if not separator:
        return email.strip().lower()
    return f"{local.lower()}@{domain.lower()}"


def hash_ip(address: str, salt: str) -> str:
    return sha256(f"{salt}:{address}")


def mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return "***"
    visible = local[:1]
    return f"{visible}{'*' * max(3, len(local) - 1)}@{domain}"


class TokenSigner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.algorithm = "ES256" if settings.jwt_private_key and settings.jwt_public_key else "HS256"
        self.signing_key = settings.jwt_private_key or settings.development_jwt_secret
        self.verification_key = settings.jwt_public_key or settings.development_jwt_secret

    def access_token(self, user_id: str, session_id: str, authenticated_at: datetime) -> str:
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=self.settings.access_token_minutes)
        return jwt.encode(
            {
                "type": "access",
                "sub": user_id,
                "sid": session_id,
                "auth_time": int(authenticated_at.timestamp()),
                "iat": int(now.timestamp()),
                "exp": int(expires.timestamp()),
                "iss": "stayzy-api",
                "aud": "stayzy-ios",
            },
            self.signing_key,
            algorithm=self.algorithm,
        )

    def entitlement_token(
        self,
        user_id: str,
        status: str,
        plan: str | None,
        valid_until: datetime | None,
        offline_until: datetime | None,
    ) -> str:
        now = datetime.now(UTC)
        token_expiry = offline_until or (now + timedelta(minutes=5))
        if token_expiry <= now:
            token_expiry = now + timedelta(minutes=5)
        return jwt.encode(
            {
                "type": "entitlement",
                "sub": user_id,
                "feature": "premium_all",
                "status": status,
                "plan": plan,
                "valid_until": int(valid_until.timestamp()) if valid_until else None,
                "offline_until": int(offline_until.timestamp()) if offline_until else None,
                "iat": int(now.timestamp()),
                "exp": int(token_expiry.timestamp()),
                "iss": "stayzy-api",
                "aud": "stayzy-ios",
            },
            self.signing_key,
            algorithm=self.algorithm,
        )

    def decode_access(self, token: str) -> AccessClaims:
        try:
            payload = jwt.decode(
                token,
                self.verification_key,
                algorithms=[self.algorithm],
                audience="stayzy-ios",
                issuer="stayzy-api",
            )
            if payload.get("type") != "access":
                raise TokenError("Wrong token type")
            return AccessClaims(
                user_id=str(payload["sub"]),
                session_id=str(payload["sid"]),
                authenticated_at=datetime.fromtimestamp(int(payload["auth_time"]), UTC),
            )
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
            raise TokenError("Invalid access token") from error

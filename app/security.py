from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings


class TokenError(ValueError):
    pass


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

    def purchase_token(self, grant_id: str) -> str:
        now = datetime.now(UTC)
        return jwt.encode({"type": "purchase", "sub": grant_id, "iss": "stayzy-api",
            "aud": "stayzy-purchases", "iat": now, "exp": now + timedelta(minutes=15)},
            self.signing_key, algorithm=self.algorithm)

    def decode_purchase(self, token: str) -> str:
        try:
            payload = jwt.decode(token, self.verification_key, algorithms=[self.algorithm],
                audience="stayzy-purchases", issuer="stayzy-api",
                options={"require": ["sub", "iat", "exp", "type"]})
            if payload["type"] != "purchase" or not isinstance(payload["sub"], str):
                raise TokenError("Wrong token type")
            return payload["sub"]
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
            raise TokenError("Invalid purchase token") from error

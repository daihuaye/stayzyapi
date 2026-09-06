from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class APIError(BaseModel):
    code: str
    message: str


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerifyRequest(BaseModel):
    token: str = Field(min_length=32, max_length=512)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class EntitlementResponse(BaseModel):
    feature: Literal["premium_all"] = "premium_all"
    status: Literal["active", "inactive", "grace"]
    plan: Literal["trial", "monthly", "lifetime"] | None
    valid_until: datetime | None
    offline_until: datetime | None
    signed_entitlement: str


class AccountResponse(BaseModel):
    id: str
    masked_email: str
    status: str
    entitlement: EntitlementResponse
    requires_recent_authentication: bool


class VoiceCatalogItem(BaseModel):
    id: str
    name: str
    description: str
    locale: str
    tier: Literal["free", "premium"]
    is_locked: bool
    preview_url: str | None
    pack_version: str | None
    download_bytes: int | None


class VoiceCatalogResponse(BaseModel):
    voices: list[VoiceCatalogItem]


class CompanionCatalogItem(BaseModel):
    id: str
    name: str
    description: str
    tier: Literal["free", "premium"]
    is_locked: bool


class CompanionCatalogResponse(BaseModel):
    companions: list[CompanionCatalogItem]


class VoicePackDownloadRequest(BaseModel):
    locale: str = Field(default="en-US", pattern=r"^[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?$")


class VoicePackDownloadResponse(BaseModel):
    voice_id: str
    locale: str
    pack_version: str
    archive_url: str
    expires_at: datetime
    sha256: str
    size_bytes: int
    manifest: dict[str, object]


class StoreTransactionRequest(BaseModel):
    signed_transaction: str = Field(min_length=32)


class AppStoreNotificationRequest(BaseModel):
    signedPayload: str = Field(min_length=32)


class HealthResponse(BaseModel):
    status: Literal["alive", "ready", "not_ready"]


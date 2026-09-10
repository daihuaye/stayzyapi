from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class APIError(BaseModel):
    code: str
    message: str


class EntitlementResponse(BaseModel):
    feature: Literal["premium_all"] = "premium_all"
    status: Literal["active", "inactive"]
    plan: Literal["trial", "lifetime"] | None
    valid_until: datetime | None = None
    ownership_type: Literal["PURCHASED", "FAMILY_SHARED"]
    access_token: str | None = None
    expires_in: int = 900


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
    signed_transaction: str = Field(min_length=32, max_length=32768)


class AppStoreNotificationRequest(BaseModel):
    signedPayload: str = Field(min_length=32)


class HealthResponse(BaseModel):
    status: Literal["alive", "ready", "not_ready"]


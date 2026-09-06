from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import optional_claims, require_user
from app.errors import api_error
from app.observability import emit
from app.models import AuthSession, CompanionDefinition, User, VoiceDefinition, VoicePackVersion
from app.schemas import (
    CompanionCatalogItem,
    CompanionCatalogResponse,
    VoiceCatalogItem,
    VoiceCatalogResponse,
    VoicePackDownloadRequest,
    VoicePackDownloadResponse,
)
from app.security import AccessClaims
from app.services.entitlements import entitlement_state
from app.services.storage import ObjectStorage, StorageUnavailable


router = APIRouter(prefix="/v1", tags=["catalog"])


async def _premium_for_optional_user(
    db: AsyncSession,
    claims: AccessClaims | None,
    settings: Settings,
) -> bool:
    if claims is None:
        return False
    active_session = await db.scalar(
        select(AuthSession.id).where(
            AuthSession.id == claims.session_id,
            AuthSession.user_id == claims.user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    if active_session is None:
        return False
    return (await entitlement_state(db, claims.user_id, settings)).permits_download


@router.get("/catalog/voices", response_model=VoiceCatalogResponse)
async def voices(
    request: Request,
    locale: str = "en-US",
    claims: AccessClaims | None = Depends(optional_claims),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VoiceCatalogResponse:
    premium = await _premium_for_optional_user(db, claims, settings)
    definitions = list(
        await db.scalars(
            select(VoiceDefinition)
            .where(VoiceDefinition.status == "active")
            .order_by(VoiceDefinition.sort_order, VoiceDefinition.display_name)
        )
    )
    storage: ObjectStorage = request.app.state.storage
    items: list[VoiceCatalogItem] = []
    for definition in definitions:
        if locale not in definition.supported_locales:
            continue
        pack = await db.scalar(
            select(VoicePackVersion)
            .where(
                VoicePackVersion.voice_id == definition.id,
                VoicePackVersion.locale == locale,
                VoicePackVersion.status == "active",
            )
            .order_by(VoicePackVersion.created_at.desc())
        )
        preview_url = None
        if definition.preview_object_key:
            try:
                preview_url, _ = await storage.presign_get(definition.preview_object_key)
            except StorageUnavailable:
                preview_url = None
        items.append(
            VoiceCatalogItem(
                id=definition.id,
                name=definition.display_name,
                description=definition.description,
                locale=locale,
                tier=definition.tier,
                is_locked=definition.tier == "premium" and not premium,
                preview_url=preview_url,
                pack_version=pack.version if pack else None,
                download_bytes=pack.size_bytes if pack else None,
            )
        )
    return VoiceCatalogResponse(voices=items)


@router.get("/catalog/companions", response_model=CompanionCatalogResponse)
async def companions(
    claims: AccessClaims | None = Depends(optional_claims),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CompanionCatalogResponse:
    premium = await _premium_for_optional_user(db, claims, settings)
    definitions = list(
        await db.scalars(
            select(CompanionDefinition)
            .where(CompanionDefinition.status == "active")
            .order_by(CompanionDefinition.sort_order, CompanionDefinition.display_name)
        )
    )
    return CompanionCatalogResponse(
        companions=[
            CompanionCatalogItem(
                id=item.id,
                name=item.display_name,
                description=item.description,
                tier=item.tier,
                is_locked=item.tier == "premium" and not premium,
            )
            for item in definitions
        ]
    )


@router.post("/voice-packs/{voice_id}/download", response_model=VoicePackDownloadResponse)
async def authorize_voice_pack_download(
    voice_id: str,
    body: VoicePackDownloadRequest,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VoicePackDownloadResponse:
    state = await entitlement_state(db, user.id, settings)
    emit("voice_download.entitlement_checked", allowed=state.permits_download,
         status=state.status, plan=state.plan)
    if not state.permits_download:
        raise api_error(403, "premium_required", "An active Premium purchase is required.")
    definition = await db.scalar(
        select(VoiceDefinition).where(VoiceDefinition.id == voice_id, VoiceDefinition.status == "active")
    )
    if definition is None or body.locale not in definition.supported_locales:
        emit("voice_download.pack_unavailable", reason="definition_or_locale")
        raise api_error(404, "voice_pack_unavailable", "This voice pack is unavailable.")
    pack = await db.scalar(
        select(VoicePackVersion)
        .where(
            VoicePackVersion.voice_id == voice_id,
            VoicePackVersion.locale == body.locale,
            VoicePackVersion.status == "active",
        )
        .order_by(VoicePackVersion.created_at.desc())
    )
    if pack is None:
        emit("voice_download.pack_unavailable", reason="no_active_pack")
        raise api_error(404, "voice_pack_unavailable", "This voice pack is unavailable.")
    emit("voice_download.pack_available")
    storage: ObjectStorage = request.app.state.storage
    try:
        archive_url, expires_at = await storage.presign_get(pack.archive_object_key)
        manifest = await storage.get_json(pack.manifest_object_key)
    except Exception as error:
        emit("voice_download.storage_authorization", outcome="failed")
        raise api_error(503, "storage_unavailable", "The voice pack is temporarily unavailable.") from error
    emit("voice_download.storage_authorization", outcome="authorized")
    return VoicePackDownloadResponse(
        voice_id=voice_id,
        locale=body.locale,
        pack_version=pack.version,
        archive_url=archive_url,
        expires_at=expires_at,
        sha256=pack.sha256,
        size_bytes=pack.size_bytes,
        manifest=manifest,
    )

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import get_signer, require_user
from app.models import User
from app.schemas import EntitlementResponse
from app.security import TokenSigner
from app.services.entitlements import entitlement_response


router = APIRouter(prefix="/v1", tags=["entitlements"])


@router.get("/entitlements", response_model=EntitlementResponse)
async def entitlements(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    signer: TokenSigner = Depends(get_signer),
) -> EntitlementResponse:
    return await entitlement_response(db, user.id, settings, signer)


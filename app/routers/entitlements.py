from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import get_signer, require_user
from app.models import User
from app.schemas import EntitlementResponse
from app.security import TokenSigner
from app.billing.service import reconcile_account


router = APIRouter(prefix="/v1", tags=["entitlements"])


@router.get("/entitlements", response_model=EntitlementResponse)
async def entitlements(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    signer: TokenSigner = Depends(get_signer),
) -> EntitlementResponse:
    return await reconcile_account(db, user, settings, signer, request.app.state.apple_store_verifier)


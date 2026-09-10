from app.dependencies import no_store
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.billing import service
from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import get_signer
from app.schemas import AppStoreNotificationRequest, EntitlementResponse, StoreTransactionRequest
from app.security import TokenSigner

router = APIRouter(prefix="/v1", tags=["purchases"], dependencies=[Depends(no_store)])

@router.post("/iap/apple/transactions", response_model=EntitlementResponse)
async def verify_store_transaction(body: StoreTransactionRequest, request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings), signer: TokenSigner = Depends(get_signer)):
    return await service.verify_store_transaction(body, request.app.state.apple_store_verifier, db, settings, signer)

@router.post("/webhooks/app-store", status_code=204)
async def app_store_webhook(body: AppStoreNotificationRequest, request: Request,
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)):
    return await service.app_store_webhook(body, request.app.state.apple_store_verifier, db, settings)

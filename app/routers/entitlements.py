from app.dependencies import no_store
from fastapi import APIRouter, Depends
from app.config import Settings, get_settings
from app.dependencies import optional_grant, get_signer
from app.models import StoreTransaction
from app.schemas import EntitlementResponse
from app.security import TokenSigner
from app.billing.entitlements import entitlement_response
from app.errors import api_error
router = APIRouter(prefix="/v1", tags=["entitlements"], dependencies=[Depends(no_store)])
@router.get("/entitlements", response_model=EntitlementResponse)
async def entitlements(grant: StoreTransaction | None = Depends(optional_grant),
    settings: Settings = Depends(get_settings), signer: TokenSigner = Depends(get_signer)):
    if grant is None: raise api_error(401, "purchase_required", "Restore Purchases to check your access.")
    return entitlement_response(grant, settings, signer, issue_token=False)

from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import Settings, get_settings
from app.db import get_db
from app.models import StoreTransaction
from app.security import TokenError, TokenSigner
from app.errors import api_error
from app.billing.entitlements import entitlement_state

bearer = HTTPBearer(auto_error=False)
def get_signer(settings: Settings = Depends(get_settings)) -> TokenSigner:
    return TokenSigner(settings)

async def optional_grant(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    signer: TokenSigner = Depends(get_signer), db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings)) -> StoreTransaction | None:
    if credentials is None: return None
    try: grant_id = signer.decode_purchase(credentials.credentials)
    except TokenError as error:
        raise api_error(401, "purchase_token_expired", "Refresh your purchase access and try again.") from error
    grant = await db.get(StoreTransaction, grant_id)
    if grant is None or grant.environment != settings.apple_environment:
        raise api_error(401, "purchase_required", "Restore Purchases to check your access.")
    return grant

async def require_grant(grant: StoreTransaction | None = Depends(optional_grant),
    settings: Settings = Depends(get_settings)) -> StoreTransaction:
    if grant is None:
        raise api_error(401, "purchase_required", "Restore Purchases to check your access.")
    if not entitlement_state(grant, settings).permits_download:
        raise api_error(403, "premium_required", "This purchase no longer provides access.")
    return grant

def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded: return forwarded.split(",", maxsplit=1)[0].strip()
    return request.client.host if request.client else "unknown"

def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"

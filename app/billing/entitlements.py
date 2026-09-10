from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from app.config import Settings
from app.models import StoreTransaction
from app.schemas import EntitlementResponse
from app.security import TokenSigner

@dataclass(frozen=True)
class EntitlementState:
    status: str
    plan: str | None
    valid_until: datetime | None
    @property
    def permits_download(self): return self.status == "active"

def entitlement_state(item: StoreTransaction, settings: Settings) -> EntitlementState:
    trial = item.product_id == settings.trial_product_id
    deadline = item.purchased_at.replace(tzinfo=UTC) + timedelta(days=7) if trial else None
    recognized = item.product_id in {settings.trial_product_id, settings.lifetime_product_id}
    active = recognized and item.status == "active" and item.revoked_at is None
    if trial: active = active and item.ownership_type == "PURCHASED" and datetime.now(UTC) < deadline
    return EntitlementState("active" if active else "inactive", "trial" if trial else "lifetime", deadline)

def entitlement_response(item: StoreTransaction, settings: Settings, signer: TokenSigner, *, issue_token: bool = True) -> EntitlementResponse:
    state = entitlement_state(item, settings)
    return EntitlementResponse(status=state.status, plan=state.plan, valid_until=state.valid_until,
        ownership_type=item.ownership_type,
        access_token=signer.purchase_token(item.id) if issue_token and state.permits_download else None)

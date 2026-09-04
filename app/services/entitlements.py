from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Entitlement, StoreTransaction
from app.schemas import EntitlementResponse
from app.security import TokenSigner


@dataclass(frozen=True)
class EntitlementState:
    status: str
    plan: str | None
    valid_until: datetime | None
    offline_until: datetime | None

    @property
    def permits_download(self) -> bool:
        return self.status == "active"


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


async def entitlement_state(db: AsyncSession, user_id: str, settings: Settings) -> EntitlementState:
    now = datetime.now(UTC)
    transactions = list(
        await db.scalars(
            select(StoreTransaction).where(
                StoreTransaction.user_id == user_id,
                StoreTransaction.revoked_at.is_(None),
                # Expired monthly transactions still define the seven-day local
                # playback grace. Refunds and revocations are excluded above.
                StoreTransaction.status.in_(["active", "grace", "expired"]),
            )
        )
    )

    lifetime = next(
        (item for item in transactions if item.product_id == settings.lifetime_product_id),
        None,
    )
    if lifetime is not None:
        return EntitlementState(
            status="active",
            plan="lifetime",
            valid_until=None,
            offline_until=now + timedelta(days=3650),
        )

    monthly_expirations = [
        expiry
        for item in transactions
        if item.product_id == settings.monthly_product_id
        if (expiry := _aware(item.expires_at)) is not None
    ]
    if monthly_expirations:
        valid_until = max(monthly_expirations)
        offline_until = valid_until + timedelta(days=settings.offline_grace_days)
        status = "active" if now <= valid_until else "grace" if now <= offline_until else "inactive"
        return EntitlementState(status, "monthly", valid_until, offline_until)

    return EntitlementState("inactive", None, None, None)


async def entitlement_response(
    db: AsyncSession,
    user_id: str,
    settings: Settings,
    signer: TokenSigner,
) -> EntitlementResponse:
    state = await entitlement_state(db, user_id, settings)
    signed = signer.entitlement_token(
        user_id,
        state.status,
        state.plan,
        state.valid_until,
        state.offline_until,
    )
    return EntitlementResponse(
        status=state.status,
        plan=state.plan,
        valid_until=state.valid_until,
        offline_until=state.offline_until,
        signed_entitlement=signed,
    )


async def recalculate_entitlement(db: AsyncSession, user_id: str, settings: Settings) -> EntitlementState:
    state = await entitlement_state(db, user_id, settings)
    entitlement = await db.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user_id,
            Entitlement.feature_key == "premium_all",
        )
    )
    if entitlement is None:
        entitlement = Entitlement(
            user_id=user_id,
            feature_key="premium_all",
            source="app_store",
            status=state.status,
            valid_until=state.valid_until,
        )
        db.add(entitlement)
    else:
        entitlement.status = state.status
        entitlement.source = "app_store"
        entitlement.valid_until = state.valid_until
    return state

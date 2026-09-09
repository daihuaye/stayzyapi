from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_security import AdminPrincipal, require_administrator
from app.db import get_db
from app.errors import api_error
from app.models import ExperimentRule
from app.observability import emit


router = APIRouter(prefix="/v1", tags=["experiments"])


class RuleResponse(BaseModel):
    enabled: bool
    rolloutPercentage: int
    allocationSalt: str


class ExperimentsResponse(BaseModel):
    schemaVersion: int = 1
    rules: dict[str, RuleResponse]


class RuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    rolloutPercentage: Annotated[int, Field(strict=True, ge=0, le=100)]


class RuleCreate(RuleUpdate):
    key: Annotated[str, Field(strict=True, min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")]


class RuleCreated(RuleResponse):
    key: str


def serialize(rule: ExperimentRule) -> RuleResponse:
    return RuleResponse(enabled=rule.enabled, rolloutPercentage=rule.rollout_percentage,
                        allocationSalt=rule.allocation_salt)


@router.get("/experiments", response_model=ExperimentsResponse)
async def experiments(response: Response, db: AsyncSession = Depends(get_db)) -> ExperimentsResponse:
    response.headers["Cache-Control"] = "no-store"
    rows = (await db.scalars(select(ExperimentRule).order_by(ExperimentRule.key))).all()
    return ExperimentsResponse(rules={row.key: serialize(row) for row in rows})


@router.get("/admin/experiments", response_model=ExperimentsResponse,
            dependencies=[Depends(require_administrator)])
async def list_experiments(response: Response, db: AsyncSession = Depends(get_db)) -> ExperimentsResponse:
    return await experiments(response, db)


@router.put("/admin/experiments/{key}", response_model=RuleResponse,
            dependencies=[Depends(require_administrator)])
async def update_experiment(key: str, body: RuleUpdate, response: Response,
                            principal: AdminPrincipal = Depends(require_administrator),
                            db: AsyncSession = Depends(get_db)) -> RuleResponse:
    response.headers["Cache-Control"] = "no-store"
    rule = await db.scalar(select(ExperimentRule).where(ExperimentRule.key == key).with_for_update())
    if rule is None:
        raise api_error(404, "experiment_not_found", "Experiment is not registered.")
    previous_enabled, previous_percentage = rule.enabled, rule.rollout_percentage
    rule.enabled = body.enabled
    rule.rollout_percentage = body.rolloutPercentage
    rule.updated_at = datetime.now(UTC)
    await db.commit()
    emit("experiment.updated", administrator_id=principal.account.id, experiment_key=key, previous_enabled=previous_enabled,
         previous_percentage=previous_percentage, enabled=rule.enabled,
         rollout_percentage=rule.rollout_percentage)
    return serialize(rule)


@router.post("/admin/experiments", response_model=RuleCreated, status_code=201,
             dependencies=[Depends(require_administrator)])
async def create_experiment(body: RuleCreate, response: Response,
                            principal: AdminPrincipal = Depends(require_administrator),
                            db: AsyncSession = Depends(get_db)) -> RuleCreated:
    rule = ExperimentRule(key=body.key, enabled=body.enabled,
                          rollout_percentage=body.rolloutPercentage,
                          allocation_salt=secrets.token_hex(16))
    db.add(rule)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if await db.get(ExperimentRule, body.key) is not None:
            raise api_error(409, "experiment_exists", "An experiment with this key already exists.")
        raise
    response.headers["Cache-Control"] = "no-store"
    emit("experiment.created", administrator_id=principal.account.id, experiment_key=rule.key, enabled=rule.enabled,
         rollout_percentage=rule.rollout_percentage)
    return RuleCreated(key=rule.key, **serialize(rule).model_dump())

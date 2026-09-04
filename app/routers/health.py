from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
import logging

from app.observability import emit, failure_fields
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas import HealthResponse


router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="alive")


@router.get("/health/ready", response_model=HealthResponse)
async def ready(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> HealthResponse:
    dependency = "database"
    try:
        await db.execute(text("SELECT 1"))
        dependency = "bucket"
        storage_ready = await request.app.state.storage.ready()
        if not storage_ready:
            raise RuntimeError("bucket unavailable")
        return HealthResponse(status="ready")
    except Exception as error:
        emit("readiness.failed", level=logging.ERROR, dependency=dependency, **failure_fields(error))
        response.status_code = 503
        return HealthResponse(status="not_ready")


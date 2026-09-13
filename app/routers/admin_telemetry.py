"""Restricted telemetry reads; no anonymous reporting or collection controls."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.admin_security import require_administrator
from app.db import get_db
from app import telemetry_reporting as reports
from app.telemetry_report_schemas import OverviewReport, HealthReport, SessionPage, SessionDetail, EventPage


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/v1/admin/telemetry", tags=["admin telemetry"],
                   dependencies=[Depends(require_administrator), Depends(no_store)])


@router.get("/overview", response_model=OverviewReport)
async def overview(w=Depends(reports.window), db: AsyncSession = Depends(get_db)):
    return await reports.overview(db, w)


@router.get("/health", response_model=HealthReport)
async def health(w=Depends(reports.window), db: AsyncSession = Depends(get_db)):
    return await reports.health(db, w)


@router.get("/sessions", response_model=SessionPage)
async def sessions(w=Depends(reports.window), db: AsyncSession = Depends(get_db),
                   limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None, max_length=2048),
                   session_id: UUID | None = None,
                   status: Literal["inProgress", "endedEarly", "completed", "cancelled", "failed"] | None = None,
                   state: str | None = Query(None, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$"),
                   buddy: bool | None = None, errors: bool | None = None, possible_drop_off: bool | None = None,
                   stage: str | None = Query(None, max_length=40),
                   reason: str | None = Query(None, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$"),
                   wait_state: str | None = Query(None, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$"), activity: bool = False):
    return await reports.sessions(db, w, limit, cursor, str(session_id) if session_id else None,
                                  status, state, buddy, errors, possible_drop_off, stage, reason, wait_state, activity)


@router.get("/sessions/{installation_id}/{session_id}", response_model=SessionDetail)
async def session_detail(installation_id: UUID, session_id: UUID, w=Depends(reports.window), db: AsyncSession = Depends(get_db)):
    return await reports.session_detail(db, w, str(installation_id), str(session_id))


@router.get("/sessions/{installation_id}/{session_id}/events", response_model=EventPage)
async def session_events(installation_id: UUID, session_id: UUID, w=Depends(reports.window), db: AsyncSession = Depends(get_db),
                         limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None, max_length=2048)):
    return await reports.session_events(db, w, str(installation_id), str(session_id), limit, cursor)

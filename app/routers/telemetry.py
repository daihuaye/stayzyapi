import json
import time
from collections import OrderedDict
from datetime import UTC, datetime
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.config import get_settings
from app.db import get_db
from app.observability import emit
from app.telemetry import Event, TelemetryEvent, TelemetrySession
from pydantic import ValidationError

router = APIRouter(prefix="/v1/telemetry", tags=["telemetry"])
# Bounded, per-worker abuse protection. Gateway limits must cover all workers.
_buckets = OrderedDict()
def allowed(key, limit):
    minute = int(time.monotonic() // 60)
    previous, count = _buckets.pop(key, (minute, 0))
    count = count + 1 if previous == minute else 1
    _buckets[key] = (minute, count)
    while len(_buckets) > 10000: _buckets.popitem(last=False)
    return count <= limit

@router.get("/config")
async def configuration(settings=Depends(get_settings)):
    return JSONResponse({"collection_enabled": settings.telemetry_enabled}, headers={"Cache-Control": "no-store"})

@router.post("/events")
async def ingest(request: Request, db: AsyncSession = Depends(get_db), settings=Depends(get_settings)):
    if not settings.telemetry_enabled:
        return JSONResponse({"accepted": [], "duplicates": [], "rejected": [], "collection_enabled": False})
    # Do not trust caller-provided forwarded IPs.
    ip = request.client.host if request.client else "unknown"
    if not allowed(ip, settings.telemetry_requests_per_minute):
        return JSONResponse({"detail": {"code": "rate_limited", "message": "Retry later"}}, 429, headers={"Retry-After": "60"})
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 256 * 1024:
            return JSONResponse({"detail": {"code": "payload_too_large", "message": "Split batch"}}, 413)
    try:
        batch = json.loads(body)
        if not isinstance(batch, dict) or set(batch) != {"events"} or not isinstance(batch["events"], list) or not 1 <= len(batch["events"]) <= 100:
            raise ValueError()
    except (ValueError, TypeError):
        return JSONResponse({"detail": {"code": "invalid_batch", "message": "Invalid event batch"}}, 422)
    result = {"accepted": [], "duplicates": [], "rejected": [], "collection_enabled": True}
    now = datetime.now(UTC)
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    for raw in batch["events"]:
        try:
            event = Event.model_validate(raw)
        except (ValidationError, ValueError):
            try: event_id = str(UUID(str(raw.get("event_id"))))
            except (ValueError, AttributeError): event_id = None
            result["rejected"].append({"event_id": event_id, "reason": "invalid_event"})
            continue
        event_id = str(event.event_id)
        payload = event.model_dump(mode="json", exclude_none=True)
        statement = insert(TelemetryEvent).values(event_id=event_id, installation_id=str(event.installation_id),
            session_id=str(event.session_id) if event.session_id else None, sequence=event.sequence,
            name=event.name, environment=event.environment, occurred_at=event.occurred_at, received_at=now,
            payload=payload).on_conflict_do_nothing(index_elements=["event_id"]).returning(TelemetryEvent.event_id)
        inserted = (await db.execute(statement)).scalar_one_or_none()
        result["accepted" if inserted else "duplicates"].append(event_id)
        if inserted and event.name in {"session.progress", "session.outcome"}:
            statement = insert(TelemetrySession).values(installation_id=str(event.installation_id),
                session_id=str(event.session_id), sequence=event.sequence, updated_at=now, summary=payload)
            statement = statement.on_conflict_do_update(index_elements=["installation_id", "session_id"],
                set_={"sequence": statement.excluded.sequence, "updated_at": now, "summary": payload},
                where=TelemetrySession.sequence < statement.excluded.sequence)
            await db.execute(statement)
    await db.commit()
    emit("telemetry.ingested", accepted_count=len(result["accepted"]), duplicate_count=len(result["duplicates"]), rejected_count=len(result["rejected"]))
    return JSONResponse(result, headers={"Cache-Control": "no-store"})

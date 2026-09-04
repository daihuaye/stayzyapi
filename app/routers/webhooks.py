from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.errors import api_error
from app.models import EmailDeliveryEvent, MagicLink
from app.services.sendgrid_webhook import verify_sendgrid_signature


router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
SUPPORTED_EVENTS = {"delivered", "deferred", "bounce", "blocked", "dropped"}


@router.post("/sendgrid/events", status_code=204)
async def sendgrid_events(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    payload = await request.body()
    signature = request.headers.get("x-twilio-email-event-webhook-signature")
    timestamp = request.headers.get("x-twilio-email-event-webhook-timestamp")
    if not verify_sendgrid_signature(
        payload,
        timestamp,
        signature,
        settings.sendgrid_webhook_public_key,
    ):
        raise api_error(401, "invalid_signature", "The webhook signature is invalid.")
    try:
        events = json.loads(payload)
    except json.JSONDecodeError as error:
        raise api_error(400, "invalid_webhook", "The webhook payload is invalid.") from error
    if not isinstance(events, list):
        raise api_error(400, "invalid_webhook", "The webhook payload must be an array.")

    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("event") not in SUPPORTED_EVENTS:
            continue
        event_id = str(event.get("sg_event_id") or hashlib.sha256(payload + str(index).encode()).hexdigest())
        if await db.get(EmailDeliveryEvent, event_id) is not None:
            continue
        message_id = event.get("sg_message_id")
        occurred_at = datetime.fromtimestamp(int(event.get("timestamp", 0)), UTC)
        db.add(
            EmailDeliveryEvent(
                id=event_id,
                sendgrid_message_id=str(message_id) if message_id else None,
                event=str(event["event"]),
                occurred_at=occurred_at,
            )
        )
        if message_id:
            challenge = await db.scalar(
                select(MagicLink).where(MagicLink.sendgrid_message_id == str(message_id))
            )
            if challenge:
                challenge.send_state = str(event["event"])
    await db.commit()
    return Response(status_code=204)


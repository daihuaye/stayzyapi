from __future__ import annotations

from dataclasses import dataclass
import logging
import time

import httpx

from app.config import Settings
from app.observability import emit, failure_fields


@dataclass(frozen=True)
class EmailSendResult:
    accepted: bool
    message_id: str | None = None


class SendGridEmailSender:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=8)

    async def send_magic_link(self, email: str, magic_link: str, challenge_id: str) -> EmailSendResult:
        missing = [name for name, value in {
            "STAYZY_SENDGRID_API_KEY": self.settings.sendgrid_api_key,
            "STAYZY_SENDGRID_MAGIC_LINK_TEMPLATE_ID": self.settings.sendgrid_magic_link_template_id,
            "STAYZY_SENDGRID_FROM_EMAIL": self.settings.sendgrid_from_email,
        }.items() if not value]
        if missing:
            emit("email.skipped", level=logging.ERROR, reason="missing_configuration", missing=missing)
            return EmailSendResult(accepted=False)

        emit("email.send_started")
        started = time.monotonic()
        try:
            response = await self.client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {self.settings.sendgrid_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": {"email": self._from_address(), "name": "Stayzy"},
                    "personalizations": [
                        {
                            "to": [{"email": email}],
                            "dynamic_template_data": {
                                "subject": "Your sign-in link for Stayzy",
                                "magic_link": magic_link,
                                "expires_minutes": 15,
                            },
                        }
                    ],
                    "template_id": self.settings.sendgrid_magic_link_template_id,
                    "tracking_settings": {
                        "click_tracking": {"enable": False, "enable_text": False},
                        "open_tracking": {"enable": False},
                    },
                },
            )
        except Exception as error:
            emit("email.send_failed", level=logging.ERROR,
                 duration_ms=round((time.monotonic() - started) * 1000, 1), **failure_fields(error))
            raise
        hints = {400: "check_template_and_payload", 401: "check_api_key",
                 403: "check_sender_verification_and_mail_send_permission",
                 429: "provider_rate_limited"}
        emit("email.accepted" if response.status_code == 202 else "email.rejected",
             level=logging.INFO if response.status_code == 202 else logging.ERROR,
             status=response.status_code,
             reason="accepted_for_delivery" if response.status_code == 202 else hints.get(
                 response.status_code, "provider_unavailable" if response.status_code >= 500 else "provider_rejected"),
             duration_ms=round((time.monotonic() - started) * 1000, 1))
        return EmailSendResult(
            accepted=response.status_code == 202,
            message_id=response.headers.get("x-message-id"),
        )

    def _from_address(self) -> str:
        value = self.settings.sendgrid_from_email
        if "<" in value and ">" in value:
            return value.split("<", maxsplit=1)[1].split(">", maxsplit=1)[0].strip()
        return value

    async def close(self) -> None:
        await self.client.aclose()

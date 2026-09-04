from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import Settings


@dataclass(frozen=True)
class EmailSendResult:
    accepted: bool
    message_id: str | None = None


class SendGridEmailSender:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=8)

    async def send_magic_link(self, email: str, magic_link: str, challenge_id: str) -> EmailSendResult:
        if not self.settings.sendgrid_api_key or not self.settings.sendgrid_magic_link_template_id:
            return EmailSendResult(accepted=False)

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
                        "dynamic_template_data": {"magic_link": magic_link, "expires_minutes": 15},
                    }
                ],
                "template_id": self.settings.sendgrid_magic_link_template_id,
                "tracking_settings": {
                    "click_tracking": {"enable": False, "enable_text": False},
                    "open_tracking": {"enable": False},
                },
            },
        )
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

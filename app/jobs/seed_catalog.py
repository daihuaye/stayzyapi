from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db import SessionFactory
from app.models import CompanionDefinition, VoiceDefinition


VOICE_SEEDS = [
    {
        "id": "voice_willow",
        "display_name": "Willow",
        "description": "Warm and quietly encouraging",
        "tier": "premium",
        "supported_locales": ["en-US"],
        "provider": "openai",
        "provider_voice_id": "coral",
        "model": "gpt-4o-mini-tts-2025-12-15",
        "instructions": (
            "Speak warmly and calmly, with gentle confidence. Use a natural conversational "
            "pace, soft intonation, and short pauses. Avoid urgency or exaggerated enthusiasm."
        ),
        "instruction_version": "warm-calm-v2-coral",
        "preview_object_key": "voice-previews/voice_willow/en-US/preview.aac",
        "status": "active",
        "sort_order": 10,
    },
    {
        "id": "voice_harbor",
        "display_name": "Harbor",
        "description": "Steady, grounded, and reassuring",
        "tier": "premium",
        "supported_locales": ["en-US"],
        "provider": "openai",
        "provider_voice_id": "echo",
        "model": "gpt-4o-mini-tts-2025-12-15",
        "instructions": (
            "Speak with a grounded, reassuring tone. Keep a relaxed conversational pace, "
            "gentle emphasis, and comfortable pauses. Never sound urgent or theatrical."
        ),
        "instruction_version": "grounded-v2-echo",
        "preview_object_key": "voice-previews/voice_harbor/en-US/preview.aac",
        "status": "active",
        "sort_order": 20,
    },
]

COMPANION_SEEDS = [
    ("stayzy.default", "Stayzy", "Your original focus companion", "free", 0),
    ("stayzy.alternate", "Sprout", "A playful alternate companion", "premium", 10),
    ("community.my-avatar", "Glow", "A calm animated companion", "premium", 20),
]


async def seed() -> None:
    async with SessionFactory() as db:
        for values in VOICE_SEEDS:
            item = await db.get(VoiceDefinition, values["id"])
            if item is None:
                db.add(VoiceDefinition(**values))
            else:
                for key, value in values.items():
                    setattr(item, key, value)
        for identifier, name, description, tier, order in COMPANION_SEEDS:
            item = await db.get(CompanionDefinition, identifier)
            if item is None:
                db.add(
                    CompanionDefinition(
                        id=identifier,
                        display_name=name,
                        description=description,
                        tier=tier,
                        status="active",
                        sort_order=order,
                    )
                )
            else:
                item.display_name = name
                item.description = description
                item.tier = tier
                item.status = "active"
                item.sort_order = order
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())

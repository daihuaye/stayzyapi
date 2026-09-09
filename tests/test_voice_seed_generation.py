from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.jobs import seed_catalog
from app.jobs.build_voice_pack import generate_audio
from app.models import VoiceDefinition


async def test_reseed_updates_voices_without_replacing_published_previews(session_factory, monkeypatch):
    monkeypatch.setattr(seed_catalog, "SessionFactory", session_factory)
    await seed_catalog.seed()
    async with session_factory() as db:
        for identifier, old_voice in [("voice_willow", "coral"), ("voice_harbor", "echo")]:
            item = await db.get(VoiceDefinition, identifier)
            item.provider_voice_id = old_voice
            item.preview_object_key = f"published/{identifier}/old-preview.aac"
        await db.commit()
    await seed_catalog.seed()
    await seed_catalog.seed()
    async with session_factory() as db:
        for identifier, provider, delivery in [
            ("voice_willow", "cedar", "warm-calm-v3-cedar"),
            ("voice_harbor", "nova", "grounded-v3-nova"),
        ]:
            item = await db.get(VoiceDefinition, identifier)
            assert item.provider_voice_id == provider
            assert item.instruction_version == delivery
            assert item.preview_object_key == f"published/{identifier}/old-preview.aac"


@pytest.mark.parametrize("values", seed_catalog.VOICE_SEEDS)
async def test_generate_audio_uses_resolved_voice_and_instructions(values):
    calls = []
    response = AsyncMock()
    response.read.return_value = b"audio"
    context = AsyncMock()
    context.__aenter__.return_value = response

    def create(**kwargs):
        calls.append(kwargs)
        return context

    client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(
        with_streaming_response=SimpleNamespace(create=create))))
    assert await generate_audio(client, VoiceDefinition(**values), "Ready.") == b"audio"
    assert calls == [dict(model=values["model"], voice=values["provider_voice_id"],
                          input="Ready.", instructions=values["instructions"], response_format="aac")]


@pytest.mark.parametrize("values", seed_catalog.VOICE_SEEDS)
async def test_preflight_reports_resolved_delivery(values, session_factory, monkeypatch, capsys):
    from app.config import Settings
    from app.jobs import build_voice_pack
    monkeypatch.setattr(seed_catalog, "SessionFactory", session_factory)
    monkeypatch.setattr(build_voice_pack, "SessionFactory", session_factory)
    await seed_catalog.seed()
    settings = Settings(_env_file=None, environment="test", openai_api_key=None,
                        bucket="test", bucket_access_key_id="test", bucket_secret_access_key="test")
    monkeypatch.setattr(build_voice_pack, "get_settings", lambda: settings)
    storage = SimpleNamespace(missing_configuration=(), invalid_configuration=(), ready=AsyncMock(return_value=True))
    monkeypatch.setattr(build_voice_pack, "ObjectStorage", lambda _: storage)
    generate = AsyncMock(side_effect=AssertionError("Preflight must not generate speech"))
    monkeypatch.setattr(build_voice_pack, "generate_audio", generate)
    await build_voice_pack.build(values["id"], "en-US", build_voice_pack.DEFAULT_PHRASE_PATH,
                                 "test", preflight_only=True)
    output = capsys.readouterr().out
    for expected in (values["provider_voice_id"], values["model"], values["instruction_version"], "500 phrases"):
        assert expected in output
    generate.assert_not_called()

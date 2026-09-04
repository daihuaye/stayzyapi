from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI
from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionFactory
from app.models import VoiceDefinition, VoicePackVersion
from app.services.storage import ObjectStorage


PREVIEW_TEXT = "One thing at a time. I'm right here with you."
DEFAULT_PHRASE_PATH = Path(__file__).resolve().parents[1] / "assets" / "CompanionPhrases.json"


class VoicePackConfigurationError(RuntimeError):
    """A safe, actionable configuration failure for the release operator."""


def load_phrases(path: Path) -> list[dict[str, str]]:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise VoicePackConfigurationError(
            f"Phrase catalog was not found at {path}. Pass --phrases with an existing path."
        ) from error
    if not isinstance(raw, dict):
        raise ValueError("Phrase catalog must be an object")
    phrases: list[dict[str, str]] = []
    for context, collection in raw.items():
        if not isinstance(collection, list):
            raise ValueError(f"Phrase collection {context} must be an array")
        for index, entry in enumerate(collection, start=1):
            if isinstance(entry, str):
                phrase = {"id": f"{context}.{index:03d}", "text": entry}
            elif isinstance(entry, dict):
                phrase = {"id": str(entry["id"]), "text": str(entry["text"])}
            else:
                raise ValueError(f"Invalid phrase in {context}")
            if not phrase["text"].strip():
                raise ValueError(f"Phrase {phrase['id']} is empty")
            phrases.append(phrase)
    identifiers = [phrase["id"] for phrase in phrases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Phrase IDs must be unique")
    return phrases


def catalog_version(phrases: list[dict[str, str]]) -> str:
    encoded = json.dumps(phrases, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def validate_aac(content: bytes, phrase_id: str) -> None:
    if len(content) < 128:
        raise ValueError(f"Generated audio for {phrase_id} is unexpectedly short")
    sample_rates = [
        96_000, 88_200, 64_000, 48_000, 44_100, 32_000, 24_000,
        22_050, 16_000, 12_000, 11_025, 8_000, 7_350,
    ]
    offset = 0
    frame_count = 0
    sample_rate: int | None = None
    while offset + 7 <= len(content):
        if content[offset] != 0xFF or content[offset + 1] & 0xF6 != 0xF0:
            raise ValueError(f"Generated audio for {phrase_id} is not valid ADTS AAC")
        rate_index = (content[offset + 2] & 0x3C) >> 2
        if rate_index >= len(sample_rates):
            raise ValueError(f"Generated audio for {phrase_id} has an invalid sample rate")
        current_rate = sample_rates[rate_index]
        sample_rate = sample_rate or current_rate
        if current_rate != sample_rate:
            raise ValueError(f"Generated audio for {phrase_id} changes sample rate")
        frame_length = (
            ((content[offset + 3] & 0x03) << 11)
            | (content[offset + 4] << 3)
            | ((content[offset + 5] & 0xE0) >> 5)
        )
        if frame_length < 7 or offset + frame_length > len(content):
            raise ValueError(f"Generated audio for {phrase_id} has a truncated AAC frame")
        offset += frame_length
        frame_count += 1
    if offset != len(content) or frame_count == 0 or sample_rate is None:
        raise ValueError(f"Generated audio for {phrase_id} has trailing or missing AAC data")
    duration = frame_count * 1_024 / sample_rate
    if not 0.15 <= duration <= 30:
        raise ValueError(f"Generated audio for {phrase_id} has an invalid duration")


async def generate_audio(client: AsyncOpenAI, voice: VoiceDefinition, text: str) -> bytes:
    async with client.audio.speech.with_streaming_response.create(
        model=voice.model,
        voice=voice.provider_voice_id,
        input=text,
        instructions=voice.instructions,
        response_format="aac",
    ) as response:
        return await response.read()


async def build(
    voice_id: str,
    locale: str,
    phrase_path: Path,
    version: str,
    *,
    preflight_only: bool = False,
) -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        raise VoicePackConfigurationError(
            "STAYZY_OPENAI_API_KEY is required for the pack-builder job"
        )
    storage = ObjectStorage(settings)
    if missing := storage.missing_configuration:
        raise VoicePackConfigurationError(
            "Railway Bucket configuration is missing or empty: "
            f"{', '.join(missing)}. Copy the matching values from the Railway "
            "Bucket Credentials tab into .env, or add Railway variable references "
            "to the pack-builder service."
        )
    if invalid := storage.invalid_configuration:
        raise VoicePackConfigurationError("; ".join(invalid))
    phrases = load_phrases(phrase_path)
    if len(phrases) < 500:
        raise ValueError(f"Expected at least 500 phrases, found {len(phrases)}")

    async with SessionFactory() as db:
        voice = await db.scalar(select(VoiceDefinition).where(VoiceDefinition.id == voice_id))
        if voice is None or voice.status != "active":
            raise ValueError("Voice definition is unavailable")
        if locale not in voice.supported_locales:
            raise ValueError(f"Voice does not support {locale}")
        if not await storage.ready():
            raise VoicePackConfigurationError(
                "Railway Bucket preflight failed. Check the bucket name, endpoint, region, "
                "access key, secret key, and network access. No speech was generated."
            )
        if preflight_only:
            print(
                f"Voice-pack preflight passed for {voice_id} ({locale}) with "
                f"{len(phrases)} phrases."
            )
            return

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        try:
            with tempfile.TemporaryDirectory(prefix="stayzy-voice-") as directory:
                root = Path(directory)
                files: dict[str, dict[str, object]] = {}
                semaphore = asyncio.Semaphore(3)

                async def create_one(phrase: dict[str, str]) -> None:
                    async with semaphore:
                        content = await generate_audio(client, voice, phrase["text"])
                    validate_aac(content, phrase["id"])
                    filename = f"{phrase['id']}.aac"
                    (root / filename).write_bytes(content)
                    files[phrase["id"]] = {
                        "path": filename,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "bytes": len(content),
                    }

                await asyncio.gather(*(create_one(phrase) for phrase in phrases))
                preview = await generate_audio(client, voice, PREVIEW_TEXT)
                validate_aac(preview, "preview")

                manifest = {
                    "schemaVersion": 1,
                    "archiveFormat": "zip-store-v1",
                    "voiceID": voice.id,
                    "locale": locale,
                    "packVersion": version,
                    "catalogVersion": catalog_version(phrases),
                    "deliveryVersion": voice.instruction_version,
                    "files": dict(sorted(files.items())),
                }
                manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                archive_buffer = io.BytesIO()
                # AAC is already compressed. Storing entries without another compression
                # pass keeps packs nearly the same size and lets the iOS client validate
                # and activate them without shipping a third-party ZIP dependency.
                with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_STORED) as archive:
                    archive.writestr("manifest.json", manifest_bytes)
                    for path in sorted(root.glob("*.aac")):
                        archive.write(path, path.name)
                archive_bytes = archive_buffer.getvalue()
                archive_sha = hashlib.sha256(archive_bytes).hexdigest()
                prefix = f"voice-packs/{voice.id}/{locale}/{manifest['catalogVersion']}/{version}"
                archive_key = f"{prefix}/pack.zip"
                manifest_key = f"{prefix}/manifest.json"
                preview_key = f"voice-previews/{voice.id}/{locale}/{version}/preview.aac"
                await storage.put_bytes(archive_key, archive_bytes, "application/zip")
                await storage.put_bytes(manifest_key, manifest_bytes, "application/json")
                await storage.put_bytes(preview_key, preview, "audio/aac")

                pack = VoicePackVersion(
                    voice_id=voice.id,
                    locale=locale,
                    catalog_version=str(manifest["catalogVersion"]),
                    version=version,
                    archive_object_key=archive_key,
                    manifest_object_key=manifest_key,
                    sha256=archive_sha,
                    size_bytes=len(archive_bytes),
                    status="draft",
                )
                db.add(pack)
                await db.flush()
                await db.execute(
                    update(VoicePackVersion)
                    .where(
                        VoicePackVersion.voice_id == voice.id,
                        VoicePackVersion.locale == locale,
                        VoicePackVersion.status == "active",
                    )
                    .values(status="retired")
                )
                pack.status = "active"
                voice.preview_object_key = preview_key
                await db.commit()
        finally:
            await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and publish a Stayzy voice pack")
    parser.add_argument("--voice-id", required=True)
    parser.add_argument("--locale", default="en-US")
    parser.add_argument(
        "--phrases",
        type=Path,
        default=DEFAULT_PHRASE_PATH,
    )
    parser.add_argument("--version", default=datetime.now(UTC).strftime("%Y.%m.%d.%H%M"))
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate configuration, storage, catalog, and voice without generating audio",
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            build(
                args.voice_id,
                args.locale,
                args.phrases,
                args.version,
                preflight_only=args.preflight_only,
            )
        )
    except VoicePackConfigurationError as error:
        parser.exit(1, f"Voice-pack builder stopped: {error}\n")


if __name__ == "__main__":
    main()

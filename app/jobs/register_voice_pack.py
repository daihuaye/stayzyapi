"""Register an already uploaded pack in the API database without generating audio."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from sqlalchemy import select, update
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db import SessionFactory
from app.jobs.build_voice_pack import DEFAULT_PHRASE_PATH, load_phrases
from app.models import VoiceDefinition, VoicePackVersion
from app.services.storage import ObjectStorage


MAX_ARCHIVE_BYTES = 100 * 1024 * 1024


def validate_archive(path: Path, manifest: dict, voice_id: str, locale: str) -> dict:
    """Validate the same stored ZIP and phrase checksums consumed by iOS."""
    if (manifest.get('schemaVersion') != 1 or manifest.get('archiveFormat') != 'zip-store-v1'
            or manifest.get('voiceID') != voice_id or manifest.get('locale') != locale):
        raise ValueError('Manifest format, voice, or locale does not match')
    for key in ('packVersion', 'catalogVersion'):
        if not isinstance(manifest.get(key), str) or not manifest[key] or '/' in manifest[key]:
            raise ValueError(f'Invalid {key}')
    files = manifest.get('files')
    expected_ids = {phrase['id'] for phrase in load_phrases(DEFAULT_PHRASE_PATH)}
    if not isinstance(files, dict) or set(files) != expected_ids:
        raise ValueError('Pack does not contain the current phrase catalog')
    paths = []
    for item in files.values():
        name = item.get('path', '')
        if (not name or name.startswith('/') or '\\' in name
                or any(part in {'', '.', '..'} for part in name.split('/'))):
            raise ValueError('Unsafe audio path')
        paths.append(name)
    if len(set(paths)) != len(paths):
        raise ValueError('Duplicate audio path')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if (len(entries) != len(files) + 1
                or {item.filename for item in entries} != set(paths) | {'manifest.json'}
                or any(item.compress_type != zipfile.ZIP_STORED or item.flag_bits & 1 for item in entries)
                or sum(item.file_size for item in entries) > MAX_ARCHIVE_BYTES):
            raise ValueError('Unsupported archive entries or size')
        if json.loads(archive.read('manifest.json')) != manifest:
            raise ValueError('Archive and uploaded manifest differ')
        for item in files.values():
            content = archive.read(item['path'])
            if (len(content) != item.get('bytes')
                    or hashlib.sha256(content).hexdigest() != item.get('sha256')):
                raise ValueError('Audio checksum or length mismatch')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'version': manifest['packVersion'], 'catalog_version': manifest['catalogVersion'],
            'size_bytes': path.stat().st_size, 'sha256': digest}


async def activate_pack(db, voice_id: str, locale: str, manifest_key: str, metadata: dict) -> None:
    # Serialize activation against other registrations for this voice.
    voice = await db.scalar(select(VoiceDefinition).where(VoiceDefinition.id == voice_id).with_for_update())
    if voice is None or voice.status != 'active' or locale not in voice.supported_locales:
        raise ValueError('Active voice definition or locale is missing from target database')
    pack = await db.scalar(select(VoicePackVersion).where(
        VoicePackVersion.voice_id == voice_id, VoicePackVersion.locale == locale,
        VoicePackVersion.version == metadata['version']))
    if pack is not None and (pack.sha256 != metadata['sha256'] or pack.size_bytes != metadata['size_bytes']):
        raise ValueError('Existing version has different content; refusing to overwrite it')
    await db.execute(update(VoicePackVersion).where(
        VoicePackVersion.voice_id == voice_id, VoicePackVersion.locale == locale,
        VoicePackVersion.status == 'active').values(status='retired'))
    if pack is None:
        pack = VoicePackVersion(voice_id=voice_id, locale=locale, **metadata)
        db.add(pack)
    pack.archive_object_key = manifest_key.removesuffix('manifest.json') + 'pack.zip'
    pack.manifest_object_key = manifest_key
    pack.status = 'active'
    await db.flush()


async def register(voice_id: str, locale: str, manifest_key: str, check_only: bool) -> None:
    settings = get_settings()
    url = make_url(settings.database_url)
    print(f'Target database: {url.drivername}; host={url.host or "local"}; database={url.database}')
    if not check_only and url.get_backend_name() != 'postgresql':
        raise ValueError('Registration requires the Railway PostgreSQL database. Set STAYZY_DATABASE_URL; local SQLite is refused.')
    prefix = f'voice-packs/{voice_id}/{locale}/'
    if not manifest_key.startswith(prefix) or not manifest_key.endswith('/manifest.json'):
        raise ValueError('Manifest key must identify this voice and locale')
    storage = ObjectStorage(settings)
    client = storage._s3()
    manifest = await storage.get_json(manifest_key)
    expected_key = f"{prefix}{manifest.get('catalogVersion')}/{manifest.get('packVersion')}/manifest.json"
    if expected_key != manifest_key:
        raise ValueError('Object key and manifest version do not match')
    archive_key = manifest_key.removesuffix('manifest.json') + 'pack.zip'
    head = await asyncio.to_thread(client.head_object, Bucket=settings.bucket, Key=archive_key)
    if not 0 < head['ContentLength'] <= MAX_ARCHIVE_BYTES:
        raise ValueError('Archive size is outside the supported range')
    with tempfile.TemporaryDirectory(prefix='stayzy-register-') as directory:
        path = Path(directory) / 'pack.zip'
        await asyncio.to_thread(client.download_file, settings.bucket, archive_key, str(path))
        metadata = await asyncio.to_thread(validate_archive, path, manifest, voice_id, locale)
    print(f"Verified {voice_id} ({locale}): version={metadata['version']}, bytes={metadata['size_bytes']}")
    if check_only:
        print('Check only: no database changes made.')
        return
    async with SessionFactory() as db:
        async with db.begin():
            await activate_pack(db, voice_id, locale, manifest_key, metadata)
    print(f"Published {voice_id} ({locale}): pack_version={metadata['version']}, download_bytes={metadata['size_bytes']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--voice-id', required=True)
    parser.add_argument('--locale', default='en-US')
    parser.add_argument('--manifest-key', required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(register(args.voice_id, args.locale, args.manifest_key, args.check_only))
    except ValueError as error:
        parser.exit(1, f'Pack registration stopped: {error}\n')


if __name__ == '__main__':
    main()

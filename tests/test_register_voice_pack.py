import hashlib
import json
import zipfile

import pytest
from sqlalchemy import select

from app.jobs.register_voice_pack import activate_pack, validate_archive
from app.jobs.build_voice_pack import DEFAULT_PHRASE_PATH, load_phrases
from app.models import VoicePackVersion
from test_catalog import seed_catalog


def make_pack(tmp_path, corrupt=False):
    content = b'audio'
    files = {p['id']: {'path': p['id']+'.aac', 'bytes': len(content),
                      'sha256': hashlib.sha256(content).hexdigest()}
             for p in load_phrases(DEFAULT_PHRASE_PATH)}
    manifest = {'schemaVersion': 1, 'archiveFormat': 'zip-store-v1', 'voiceID': 'voice_willow',
                'locale': 'en-US', 'packVersion': 'recovered', 'catalogVersion': 'catalog', 'files': files}
    path = tmp_path/'pack.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        for item in files.values():
            archive.writestr(item['path'], b'wrong' if corrupt else content)
    return path, manifest


def test_verify_archive_and_reject_wrong_voice_or_corruption(tmp_path):
    path, manifest = make_pack(tmp_path)
    result = validate_archive(path, manifest, 'voice_willow', 'en-US')
    assert result['size_bytes'] == path.stat().st_size
    assert result['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='does not match'):
        validate_archive(path, manifest, 'voice_harbor', 'en-US')
    path, manifest = make_pack(tmp_path, corrupt=True)
    with pytest.raises(ValueError, match='checksum'):
        validate_archive(path, manifest, 'voice_willow', 'en-US')


async def test_registration_activates_catalog_idempotently(api_client, session_factory, tmp_path):
    client, _, _, _, _ = api_client
    await seed_catalog(session_factory)
    path, manifest = make_pack(tmp_path)
    metadata = validate_archive(path, manifest, 'voice_willow', 'en-US')
    key = 'voice-packs/voice_willow/en-US/catalog/recovered/manifest.json'
    for _ in range(2):
        async with session_factory() as db:
            async with db.begin():
                await activate_pack(db, 'voice_willow', 'en-US', key, metadata)
    async with session_factory() as db:
        packs = list(await db.scalars(select(VoicePackVersion)))
        assert len(packs) == 2
        assert len([p for p in packs if p.status == 'active']) == 1
    response = await client.get('/v1/catalog/voices?locale=en-US')
    assert response.json()['voices'][0]['pack_version'] == 'recovered'
    assert response.json()['voices'][0]['download_bytes'] == path.stat().st_size
    async with session_factory() as db:
        with pytest.raises(ValueError, match='different content'):
            async with db.begin():
                await activate_pack(db, 'voice_willow', 'en-US', key, {**metadata, 'sha256': 'b'*64})
    response = await client.get('/v1/catalog/voices?locale=en-US')
    assert response.json()['voices'][0]['pack_version'] == 'recovered'

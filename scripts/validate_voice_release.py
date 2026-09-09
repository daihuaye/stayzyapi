"""Download and decode a published release before bundling or hosted activation.

Run with python -m scripts.validate_voice_release; requires ffmpeg on PATH.
Outputs contain release metadata only, never credentials or signed URLs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from app.config import get_settings
from app.jobs.build_voice_pack import DEFAULT_PHRASE_PATH, catalog_version, load_phrases, validate_aac
from app.jobs.register_voice_pack import validate_archive
from app.services.storage import ObjectStorage


def verify(voice_id: str, version: str, delivery: str, output: Path) -> None:
    settings = get_settings()
    client = ObjectStorage(settings)._s3()
    catalog = catalog_version(load_phrases(DEFAULT_PHRASE_PATH))
    prefix = f'voice-packs/{voice_id}/en-US/{catalog}/{version}'
    output.mkdir(parents=True, exist_ok=False)
    for name in ('manifest.json', 'pack.zip'):
        client.download_file(settings.bucket, f'{prefix}/{name}', str(output / name))
    manifest = json.loads((output / 'manifest.json').read_bytes())
    assert manifest['deliveryVersion'] == delivery
    assert manifest['packVersion'] == version and manifest['catalogVersion'] == catalog
    metadata = validate_archive(output / 'pack.zip', manifest, voice_id, 'en-US')
    bundle = output / 'bundle'
    bundle.mkdir()
    with zipfile.ZipFile(output / 'pack.zip') as archive:
        archive.extractall(bundle)  # validate_archive above checks every entry and path.
    preview_key = f'voice-previews/{voice_id}/en-US/{version}/preview.aac'
    client.download_file(settings.bucket, preview_key, str(bundle / 'preview.aac'))

    def decode(path: Path) -> None:
        validate_aac(path.read_bytes(), path.name)
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
                       check=True, capture_output=True)

    paths = list(bundle.glob('*.aac'))
    assert len(paths) == 501
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(decode, paths))
    preview = (bundle / 'preview.aac').read_bytes()
    report = dict(voiceID=voice_id, locale='en-US', deliveryVersion=delivery,
                  manifestKey=f'{prefix}/manifest.json', previewKey=preview_key,
                  phraseCount=len(manifest['files']), decodedAACFiles=len(paths),
                  previewBytes=len(preview), previewSHA256=hashlib.sha256(preview).hexdigest(), **metadata)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--voice-id', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--delivery', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    verify(args.voice_id, args.version, args.delivery, args.output)

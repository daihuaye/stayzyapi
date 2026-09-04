from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import pytest

from app.config import Settings
from app.jobs.build_voice_pack import catalog_version, load_phrases, validate_aac
from app.services.sendgrid_webhook import verify_sendgrid_signature
from app.services.storage import ObjectStorage


def test_railway_postgres_url_uses_async_driver() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql://user:password@postgres.railway.internal:5432/railway",
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_list_settings_accept_csv_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAYZY_ALLOWED_HOSTS", "localhost,127.0.0.1")
    monkeypatch.setenv(
        "STAYZY_APPLE_ROOT_CERTIFICATE_PATHS",
        "/certificates/apple-root.pem,/certificates/apple-root-2.pem",
    )

    settings = Settings(_env_file=None)

    assert settings.allowed_hosts == ["localhost", "127.0.0.1"]
    assert settings.apple_root_certificate_paths == [
        "/certificates/apple-root.pem",
        "/certificates/apple-root-2.pem",
    ]


def test_list_settings_continue_to_accept_json_arrays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAYZY_ALLOWED_HOSTS", '["api.stayzy.app", "localhost"]')

    settings = Settings(_env_file=None)

    assert settings.allowed_hosts == ["api.stayzy.app", "localhost"]


def test_empty_optional_environment_value_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAYZY_APPLE_APP_ID", "")

    settings = Settings(_env_file=None)

    assert settings.apple_app_id is None


def test_storage_reports_exact_missing_builder_configuration() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        bucket="",
        bucket_access_key_id=None,
        bucket_secret_access_key="",
    )

    storage = ObjectStorage(settings)

    assert storage.missing_configuration == (
        "STAYZY_BUCKET",
        "STAYZY_BUCKET_ACCESS_KEY_ID",
        "STAYZY_BUCKET_SECRET_ACCESS_KEY",
    )
    assert not storage.configured


def test_storage_is_configured_when_required_values_are_present() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        bucket="stayzy-voice-packs-example",
        bucket_access_key_id="access-key",
        bucket_secret_access_key="secret-key",
    )

    storage = ObjectStorage(settings)

    assert storage.missing_configuration == ()
    assert storage.configured


def test_sendgrid_signature_uses_timestamp_and_raw_payload() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key = base64.b64encode(public_der).decode()
    timestamp = "1788480000"
    payload = b'[{"event":"delivered"}]'
    signature = private_key.sign(timestamp.encode() + payload, ec.ECDSA(hashes.SHA256()))
    encoded_signature = base64.b64encode(signature).decode()

    assert verify_sendgrid_signature(payload, timestamp, encoded_signature, public_key)
    assert not verify_sendgrid_signature(payload + b" ", timestamp, encoded_signature, public_key)


def test_phrase_catalog_has_stable_unique_ids(tmp_path) -> None:
    path = tmp_path / "phrases.json"
    path.write_text(json.dumps({"ready": ["Hello", {"id": "ready.custom", "text": "Welcome"}]}))
    phrases = load_phrases(path)
    assert phrases == [
        {"id": "ready.001", "text": "Hello"},
        {"id": "ready.custom", "text": "Welcome"},
    ]
    assert catalog_version(phrases) == catalog_version(phrases)


def test_aac_validation_checks_frames_and_duration() -> None:
    # 44.1 kHz ADTS frames. Sixty frames is about 1.39 seconds.
    frame_payload = b"\x00" * 121
    frame_length = 128
    header = bytes(
        [
            0xFF,
            0xF1,
            0x50,
            0x80 | ((frame_length >> 11) & 0x03),
            (frame_length >> 3) & 0xFF,
            ((frame_length & 0x07) << 5) | 0x1F,
            0xFC,
        ]
    )
    validate_aac((header + frame_payload) * 60, "ready.001")
    with pytest.raises(ValueError):
        validate_aac(b"not an AAC file" * 20, "ready.002")

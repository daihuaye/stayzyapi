from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.config import Config

from app.config import Settings


class StorageUnavailable(RuntimeError):
    pass


class ObjectStorage:
    _required_configuration = (
        ("bucket", "STAYZY_BUCKET"),
        ("bucket_access_key_id", "STAYZY_BUCKET_ACCESS_KEY_ID"),
        ("bucket_secret_access_key", "STAYZY_BUCKET_SECRET_ACCESS_KEY"),
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any | None = None

    @property
    def missing_configuration(self) -> tuple[str, ...]:
        return tuple(
            environment_name
            for attribute, environment_name in self._required_configuration
            if not str(getattr(self.settings, attribute) or "").strip()
        )

    @property
    def configured(self) -> bool:
        return not self.missing_configuration

    def _s3(self) -> Any:
        if not self.configured:
            raise StorageUnavailable("Object storage is not configured")
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.bucket_endpoint,
                region_name=self.settings.bucket_region,
                aws_access_key_id=self.settings.bucket_access_key_id,
                aws_secret_access_key=self.settings.bucket_secret_access_key,
                config=Config(signature_version="s3v4"),
            )
        return self._client

    async def presign_get(self, key: str) -> tuple[str, datetime]:
        expires = self.settings.presigned_url_seconds
        url = await asyncio.to_thread(
            self._s3().generate_presigned_url,
            "get_object",
            Params={"Bucket": self.settings.bucket, "Key": key},
            ExpiresIn=expires,
        )
        return url, datetime.now(UTC) + timedelta(seconds=expires)

    async def get_json(self, key: str) -> dict[str, object]:
        response = await asyncio.to_thread(
            self._s3().get_object,
            Bucket=self.settings.bucket,
            Key=key,
        )
        body = await asyncio.to_thread(response["Body"].read)
        value = json.loads(body)
        if not isinstance(value, dict):
            raise StorageUnavailable("Manifest is not a JSON object")
        return value

    async def put_bytes(self, key: str, content: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._s3().put_object,
            Bucket=self.settings.bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
        )

    async def ready(self) -> bool:
        if not self.configured:
            return False
        try:
            await asyncio.to_thread(self._s3().head_bucket, Bucket=self.settings.bucket)
            return True
        except Exception:
            return False

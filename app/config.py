from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STAYZY_",
        case_sensitive=False,
        env_ignore_empty=True,
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./stayzy-dev.db"
    public_app_url: str = "https://links.stayzy.app"
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )

    jwt_private_key: str | None = None
    jwt_public_key: str | None = None
    development_jwt_secret: str = "development-only-change-me"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    recent_authentication_minutes: int = 10
    offline_grace_days: int = 7
    rate_limit_salt: str = "development-rate-limit-salt"

    sendgrid_api_key: str | None = None
    sendgrid_from_email: str = "Stayzy <signin@mail.stayzy.app>"
    sendgrid_magic_link_template_id: str | None = None
    sendgrid_webhook_public_key: str | None = None

    bucket: str | None = None
    bucket_endpoint: str = "https://storage.railway.app"
    bucket_region: str = "auto"
    bucket_access_key_id: str | None = None
    bucket_secret_access_key: str | None = None
    presigned_url_seconds: int = 300

    openai_api_key: str | None = None
    apple_bundle_id: str = "com.vistasolutions.stayzy"
    apple_api_key_id: str | None = None
    apple_api_issuer_id: str | None = None
    apple_api_private_key: str | None = None
    apple_team_id: str | None = None
    apple_app_id: int | None = None
    apple_environment: Literal["Sandbox", "Production"] = "Sandbox"
    apple_root_certificate_paths: Annotated[list[str], NoDecode] = Field(default_factory=list)

    monthly_product_id: str = "com.vistasolutions.stayzy.premium.monthly"
    trial_product_id: str = "com.vistasolutions.stayzy.trial.seven_days"
    lifetime_product_id: str = "com.vistasolutions.stayzy.premium.lifetime"

    @field_validator("database_url", mode="before")
    @classmethod
    def use_async_postgres_driver(cls, value: object) -> object:
        # Railway exposes a standard postgresql:// URL. SQLAlchemy's async
        # engine needs the asyncpg driver name, so accept Railway's value as-is.
        if isinstance(value, str):
            if value.startswith("postgres://"):
                return value.replace("postgres://", "postgresql+asyncpg://", 1)
            if value.startswith("postgresql://"):
                return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("allowed_hosts", "apple_root_certificate_paths", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    pass
                else:
                    if isinstance(decoded, list):
                        return decoded
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.environment in {"production", "staging"}:
            expected = "Production" if self.environment == "production" else "Sandbox"
            if self.apple_environment != expected:
                raise ValueError(f"{self.environment} requires Apple {expected} verification")
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise ValueError("Production requires a PostgreSQL database URL")
            required = {
                "jwt_private_key": self.jwt_private_key,
                "jwt_public_key": self.jwt_public_key,
                "sendgrid_api_key": self.sendgrid_api_key,
                "sendgrid_magic_link_template_id": self.sendgrid_magic_link_template_id,
                "sendgrid_webhook_public_key": self.sendgrid_webhook_public_key,
                "bucket": self.bucket,
                "bucket_access_key_id": self.bucket_access_key_id,
                "bucket_secret_access_key": self.bucket_secret_access_key,
                "apple_api_key_id": self.apple_api_key_id,
                "apple_api_issuer_id": self.apple_api_issuer_id,
                "apple_api_private_key": self.apple_api_private_key,
                "apple_team_id": self.apple_team_id,
                "apple_app_id": self.apple_app_id,
                "apple_root_certificate_paths": self.apple_root_certificate_paths,
            }
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise ValueError(f"Missing production settings: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

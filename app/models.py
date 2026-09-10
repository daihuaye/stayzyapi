from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class StoreTransaction(Base):
    __tablename__ = "store_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    transaction_id: Mapped[str] = mapped_column(String(128), index=True)
    original_transaction_id: Mapped[str] = mapped_column(String(128), index=True)
    ownership_type: Mapped[str] = mapped_column(String(24), default="PURCHASED")
    app_transaction_id: Mapped[str | None] = mapped_column(String(128), index=True)
    __table_args__ = (UniqueConstraint("environment", "transaction_id", name="uq_store_environment_transaction"),)
    billing_subject: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(160), index=True)
    environment: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    apple_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    billing_grace_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class VoiceDefinition(Base):
    __tablename__ = "voice_definitions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(300))
    tier: Mapped[str] = mapped_column(String(24), default="premium", index=True)
    supported_locales: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(40))
    provider_voice_id: Mapped[str] = mapped_column(String(160))
    model: Mapped[str] = mapped_column(String(120))
    instructions: Mapped[str] = mapped_column(Text)
    instruction_version: Mapped[str] = mapped_column(String(80))
    preview_object_key: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class VoicePackVersion(Base):
    __tablename__ = "voice_pack_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    voice_id: Mapped[str] = mapped_column(ForeignKey("voice_definitions.id", ondelete="CASCADE"), index=True)
    locale: Mapped[str] = mapped_column(String(35), index=True)
    catalog_version: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(80))
    archive_object_key: Mapped[str] = mapped_column(String(500))
    manifest_object_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("voice_id", "locale", "version", name="uq_voice_pack_version"),
    )


class CompanionDefinition(Base):
    __tablename__ = "companion_definitions"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(300), default="")
    tier: Mapped[str] = mapped_column(String(24), default="free", index=True)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipts"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExperimentRule(Base):
    __tablename__ = "experiment_rules"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rollout_percentage: Mapped[int] = mapped_column(Integer, nullable=False)
    allocation_salt: Mapped[str] = mapped_column(String(80), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100", name="experiment_percentage_range"),
    )

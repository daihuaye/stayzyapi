"""Versioned anonymous telemetry. No arbitrary strings or nested user content."""
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator
from sqlalchemy import DateTime, Index, Integer, JSON, String, UniqueConstraint, delete
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

Identifier = Annotated[str, Field(max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")]
Number = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
class Interval(Strict):
    id: UUID
    kind: Identifier
    started_at: Number
    ended_at: Number | None = None
    duration: Number
    estimated_seconds: Number | None = None
    attribution_measured_seconds: Number | None = None
    is_break: StrictBool = False
    participant_id: UUID | None = None
    recognition_epoch_id: UUID | None = None
class Properties(Strict):
    # Registry of privacy-reviewed fields. New fields require a schema change.
    state: Identifier | None = None
    previous_state: Identifier | None = None
    reason: Identifier | None = None
    action: Identifier | None = None
    screen: Literal["focus", "history", "settings", "report", "onboarding"] | None = None
    status: Literal["inProgress", "endedEarly", "failed", "cancelled", "completed"] | None = None
    origin: Identifier | None = None
    task: Literal["Homework", "Reading", "Piano_Practice", "Exercise", "Meditation", "custom"] | None = None
    configuration: dict[Identifier, Number | StrictBool | Identifier] | None = None
    configuration_available: StrictBool | None = None
    duration: Number | None = None
    foreground_seconds: Number | None = None
    last_interaction_at: Number | None = None
    target_seconds: Number | None = None
    present_seconds: Number | None = None
    away_seconds: Number | None = None
    break_seconds: Number | None = None
    manual_seconds: Number | None = None
    technical_seconds: Number | None = None
    uncertain_seconds: Number | None = None
    elapsed_seconds: Number | None = None
    progress: Annotated[float, Field(ge=0, le=1)] | None = None
    resume_count: Number | None = None
    interruption_count: Number | None = None
    attempt: Number | None = None
    count: Number | None = None
    first_at: Number | None = None
    last_at: Number | None = None
    samples: Number | None = None
    processing_ms: Number | None = None
    max_processing_ms: Number | None = None
    body_samples: Number | None = None
    face_samples: Number | None = None
    missing_samples: Number | None = None
    unknown_samples: Number | None = None
    estimated_samples: Number | None = None
    ambiguous_samples: Number | None = None
    stale_tracks: Number | None = None
    max_people: Number | None = None
    estimated_seconds: Number | None = None
    attribution_measured_seconds: Number | None = None
    known_seconds: Number | None = None
    unknown_seconds: Number | None = None
    degraded_seconds: Number | None = None
    recognition_epoch_id: UUID | None = None
    snapshot_id: UUID | None = None
    revision: Annotated[int, Field(ge=0)] | None = None
    part_index: Annotated[int, Field(ge=0, le=100000)] | None = None
    part_count: Annotated[int, Field(ge=1, le=100001)] | None = None
    intervals: Annotated[list[Interval], Field(max_length=50)] | None = None

    @model_validator(mode="after")
    def configuration_keys(self):
        allowed = {"target_seconds", "tracks_participants", "speech_enabled", "haptics_enabled",
                   "notifications_enabled", "voice_id", "appearance_id", "palette_id", "motion_level",
                   "minimum_confidence", "minimum_face_confidence", "stable_entry_duration",
                   "confirmed_exit_duration", "initial_warm_up_duration", "camera_startup_timeout",
                   "camera_maximum_start_attempts", "camera_retry_base_delay", "reminder_cooldown",
                   "automatic_break_threshold", "maximum_frame_silence", "sampling_interval",
                   "maximum_simultaneous_requests", "recognition_model", "camera_position",
                   "recognition_match", "recognition_margin", "recognition_new", "recognition_confirm_seconds",
                   "recognition_sampling_seconds", "track_expiry_seconds", "companion_visible", "camera_preset", "analysis_mirrored"}
        if self.configuration and any(k not in allowed and k not in {"experiment_companion", "experiment_rive_character"} for k in self.configuration):
            raise ValueError("configuration field not registered")
        if self.part_index is not None and (self.part_count is None or self.part_index >= self.part_count):
            raise ValueError("invalid part")
        return self

EVENTS = {
 "app.launch", "app.inactive", "app.foreground", "app.background", "app.usage", "ui.screen", "ui.action",
 "setup.viewed", "setup.start_tapped", "access.shown", "access.result", "permission.requested", "permission.result",
 "session.started", "session.adopted", "session.resumed", "session.configuration_changed",
 "session.state", "session.progress", "session.outcome", "session.timeline",
 "camera.configuration", "camera.start_attempt", "camera.started", "camera.error", "camera.recovered", "camera.window",
 "recognition.reset", "recognition.error", "telemetry.dropped", "voice.result"
}
class Event(Strict):
    event_id: UUID
    name: str
    version: Literal[1]
    installation_id: UUID
    app_run_id: UUID
    session_id: UUID | None = None
    start_attempt_id: UUID | None = None
    sequence: Annotated[int, Field(ge=1)] | None = None
    run_index: Annotated[int, Field(ge=0)] = 0
    occurred_at: datetime
    environment: Literal["production", "debug", "test"]
    app_version: Identifier
    app_build: Identifier
    os_version: Identifier
    device_class: Identifier
    properties: Properties

    @model_validator(mode="after")
    def registered(self):
        if self.name not in EVENTS: raise ValueError("unknown event")
        if self.session_id is not None and self.sequence is None: raise ValueError("session sequence required")
        if self.name.startswith("session.") and self.session_id is None: raise ValueError("session required")
        if self.name == "session.timeline" and any(getattr(self.properties, k) is None for k in ("snapshot_id", "revision", "part_index", "part_count", "intervals")):
            raise ValueError("timeline metadata required")
        if self.name in {"session.progress", "session.outcome"} and self.properties.status is None:
            raise ValueError("status required")
        if self.occurred_at.tzinfo is None: raise ValueError("timezone required")
        if self.occurred_at > datetime.now(UTC) + timedelta(days=1): raise ValueError("future timestamp")
        return self

class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(36))
    session_id: Mapped[str | None] = mapped_column(String(36))
    sequence: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    environment: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_telemetry_session", "installation_id", "session_id", "sequence"),
                     Index("ix_telemetry_usage", "environment", "name", "occurred_at"),
                     Index("ix_telemetry_retention", "received_at"))
class TelemetrySession(Base):
    __tablename__ = "telemetry_sessions"
    installation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[dict] = mapped_column(JSON)

async def purge(db, now=None):
    cutoff = (now or datetime.now(UTC)) - timedelta(days=90)
    await db.execute(delete(TelemetryEvent).where(TelemetryEvent.received_at < cutoff))
    await db.execute(delete(TelemetrySession).where(TelemetrySession.updated_at < cutoff))
    await db.commit()

"""Version-one admin reporting response contracts (no public telemetry reads)."""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel

Numbers = dict[str, int | float | None]


class ReportMeta(BaseModel):
    as_of: datetime
    start: datetime
    end: datetime
    environment: Literal["production", "debug", "test"]


class Datum(BaseModel):
    label: str | None
    value: int | float | None
    mean_progress: float | None = None


class OverviewReport(ReportMeta):
    summary: Numbers
    daily: list[dict[str, Any]]
    screens: list[Datum]
    outcomes: list[Datum]
    waits: list[Datum]
    attempts: Numbers
    funnel: list[Datum]
    permission_denials: int
    setup_views: int
    quality: Numbers
    app_versions: list[str]


class HealthReport(ReportMeta):
    summary: Numbers
    daily: list[dict[str, Any]]
    versions: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]


class SnapshotReport(BaseModel):
    snapshot_id: str
    revision: int
    expected_parts: int
    received_parts: int
    complete: bool
    outcome_sequence: int | None
    intervals: list[dict[str, Any]] | None = None


class SessionReport(BaseModel):
    installation_id: str
    session_id: str
    created_at: datetime
    last_activity: datetime
    last_received: datetime
    app_version: str
    configuration: dict[str, Any] | None
    start_metadata: dict[str, Any]
    configuration_available: bool
    buddy_tracking: bool | None
    status: Literal["inProgress", "endedEarly", "completed", "cancelled", "failed"]
    state: str | None
    error_count: int | float
    run_count: int
    possible_drop_off: bool
    totals: dict[str, Any]
    timeline: SnapshotReport | None = None


class SessionPage(ReportMeta):
    items: list[SessionReport]
    next_cursor: str | None


class EventPage(ReportMeta):
    items: list[dict[str, Any]]
    next_cursor: str | None


class SessionDetail(ReportMeta):
    session: SessionReport
    configuration_changes: list[dict[str, Any]]
    outcomes: list[dict[str, Any]]
    epochs: list[dict[str, Any]]
    state_intervals: list[dict[str, Any]]
    checkpoints: list[dict[str, Any]]
    checkpoint_count: int
    markers: list[dict[str, Any]]
    marker_count: int
    snapshots: list[SnapshotReport]
    selected_snapshot: SnapshotReport | None

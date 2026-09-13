from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
from app.telemetry import TelemetryEvent

NOW = datetime.now(UTC) - timedelta(seconds=2)
I, S = str(uuid4()), str(uuid4())


def ev(name, seq, p=None, *, session=S, installation=I, age=2, received_age=None, version="1.0", environment="production", attempt=None):
    occurred = NOW - timedelta(days=age)
    payload = dict(event_id=str(uuid4()), name=name, version=1, installation_id=installation,
                   app_run_id=str(uuid4()), session_id=session, sequence=seq, run_index=0,
                   occurred_at=occurred.isoformat(), environment=environment, app_version=version,
                   app_build="1", os_version="18", device_class="iphone", properties=p or {}, start_attempt_id=attempt)
    return TelemetryEvent(event_id=payload["event_id"], name=name, installation_id=installation, session_id=session,
                          sequence=seq, environment=environment, occurred_at=occurred,
                          received_at=NOW - timedelta(days=received_age if received_age is not None else age), payload=payload)


async def seed(factory, *events):
    async with factory() as db:
        db.add_all(events)
        await db.commit()


def params(**extra):
    return {"as_of": NOW.isoformat(), "start": (NOW-timedelta(days=7)).isoformat(), "end": NOW.isoformat(), **extra}


async def get(client, headers, path="overview", **extra):
    result = await client.get("/v1/admin/telemetry/"+path, headers=headers, params=params(**extra))
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    return result.json()


async def test_auth_filters_and_empty(api_client, admin_headers):
    client = api_client[0]
    for path in ["overview", "health", "sessions", f"sessions/{I}/{S}", f"sessions/{I}/{S}/events"]:
        assert (await client.get("/v1/admin/telemetry/"+path)).status_code == 401
    for extra in [{"environment": "all"}, {"start": "2026-01-01T00:00:00Z", "end": "2026-05-01T00:00:00Z"}, {"start": "2026-01-01T00:00:00"}, {"limit": 101}, {"cursor": "garbage"}]:
        assert (await client.get("/v1/admin/telemetry/sessions", headers=admin_headers, params=extra)).status_code == 422
    assert (await get(client, admin_headers))["summary"]["sessions_started"] == 0
    assert (await get(client, admin_headers, "health"))["summary"]["mean_processing_ms"] is None


async def test_reconstruction_continuation_snapshots_and_totals(api_client, admin_headers, session_factory):
    snapshot = str(uuid4())
    await seed(session_factory,
        ev("session.started", 1, {"configuration": {"target_seconds": 100, "tracks_participants": True}}),
        ev("session.progress", 2, {"status": "inProgress", "present_seconds": 10, "progress": .1}),
        ev("session.outcome", 3, {"status": "endedEarly", "present_seconds": 20, "progress": .2, "snapshot_id": snapshot, "revision": 1, "part_count": 2}),
        ev("session.timeline", 4, {"snapshot_id": snapshot, "revision": 1, "part_count": 2, "part_index": 0, "intervals": []}),
        ev("session.resumed", 5, {}, age=1.5),
        ev("session.configuration_changed", 6, {"configuration": {"target_seconds": 500}}),
        ev("session.state", 7, {"state": "focused", "previous_state": "acquiring", "duration": 5}),
        ev("session.progress", 8, {"status": "inProgress", "present_seconds": 30, "progress": .3}),
        ev("app.usage", None, {"duration": 10, "foreground_seconds": 10, "screen": "focus"}, session=None),
        ev("app.usage", None, {"duration": 15, "foreground_seconds": 25, "screen": "focus"}, session=None))
    data = await get(api_client[0], admin_headers)
    assert data["summary"]["foreground_seconds"] == 25
    row = (await get(api_client[0], admin_headers, "sessions"))["items"][0]
    assert row["status"] == "inProgress" and row["totals"]["present_seconds"] == 30
    assert row["configuration"]["target_seconds"] == 100
    assert row["timeline"]["received_parts"] == 1 and not row["timeline"]["complete"]
    detail = await get(api_client[0], admin_headers, f"sessions/{I}/{S}")
    assert detail["state_intervals"][0]["duration"] == 5
    assert detail["configuration_changes"][0]["properties"]["configuration"]["target_seconds"] == 500
    assert len(detail["outcomes"]) == 1


async def test_drop_off_delayed_delivery_breaks_and_terminal(api_client, admin_headers, session_factory):
    sessions = [str(uuid4()) for _ in range(6)]
    for index, sid in enumerate(sessions):
        await seed(session_factory, ev("session.started", 1, session=sid))
        if index == 0:
            await seed(session_factory, ev("session.state", 2, {"state": "acquiring"}, session=sid))
        elif index == 1:
            await seed(session_factory, ev("session.state", 2, {"state": "acquiring"}, session=sid, received_age=.1))
        elif index == 2:
            await seed(session_factory, ev("session.state", 2, {"state": "manual_break"}, session=sid))
        elif index == 3:
            await seed(session_factory, ev("session.outcome", 2, {"status": "endedEarly"}, session=sid))
        elif index == 4:
            await seed(session_factory, ev("session.state", 2, {"state": "acquiring"}, session=sid, age=.9))
        else:
            await seed(session_factory, ev("session.outcome", 2, {"status": "completed"}, session=sid))
    data = await get(api_client[0], admin_headers, "sessions", possible_drop_off="true")
    assert [r["session_id"] for r in data["items"]] == sessions[:1]


async def test_funnel_cohorts_versions_and_health(api_client, admin_headers, session_factory):
    attempt = str(uuid4())
    await seed(session_factory,
        ev("setup.start_tapped", None, session=None, attempt=attempt),
        ev("access.shown", None, session=None, attempt=attempt),
        ev("session.started", 1, attempt=attempt),
        ev("permission.result", 2, {"reason": "authorized"}),
        ev("camera.started", 3, {"duration": 2}),
        ev("session.state", 4, {"state": "focused"}),
        ev("session.outcome", 5, {"status": "completed"}, age=.1),
        ev("camera.window", 6, {"samples": 2, "processing_ms": 20, "max_processing_ms": 15, "known_seconds": 10}),
        ev("camera.window", 7, {"samples": 8, "processing_ms": 160, "max_processing_ms": 40, "degraded_seconds": 5}),
        ev("session.started", 1, session=str(uuid4()), age=20),
        ev("session.started", 1, session=str(uuid4()), environment="debug"))
    data = await get(api_client[0], admin_headers, end=(NOW-timedelta(days=1)).isoformat())
    assert data["attempts"] == {"tapped": 1, "created": 1, "gated": 1}
    assert [r["value"] for r in data["funnel"]] == [1, 1, 1, 1, 1]
    health = await get(api_client[0], admin_headers, "health")
    assert health["summary"]["mean_processing_ms"] == 18
    assert health["summary"]["max_processing_ms"] == 40
    assert health["summary"]["startup_mean_seconds"] == 2


async def test_identity_adoption_pagination_and_snapshot_cutoff(api_client, admin_headers, session_factory):
    await seed(session_factory, ev("session.adopted", 1, {"configuration_available": False}),
               ev("session.started", 1, installation=str(uuid4())),
               ev("session.outcome", 2, {"status": "completed"}, received_age=-1))
    first = await get(api_client[0], admin_headers, "sessions", limit=1)
    second = await get(api_client[0], admin_headers, "sessions", limit=1, cursor=first["next_cursor"])
    assert second["next_cursor"] is None
    assert first["items"][0]["installation_id"] != second["items"][0]["installation_id"]
    detail = await get(api_client[0], admin_headers, f"sessions/{I}/{S}")
    assert detail["session"]["configuration_available"] is False
    assert detail["session"]["status"] == "inProgress"
    events = await get(api_client[0], admin_headers, f"sessions/{I}/{S}/events", limit=1)
    assert len(events["items"]) == 1 and events["next_cursor"] is None


async def test_epoch_attribution_and_missing_snapshot(api_client, admin_headers, session_factory):
    snap, epoch1, epoch2, state_id = map(str, [uuid4(), uuid4(), uuid4(), uuid4()])
    intervals = [{"id": state_id, "kind": "acquiring", "started_at": NOW.timestamp()-50, "ended_at": NOW.timestamp()-40, "duration": 10},
                 {"id": str(uuid4()), "kind": "buddy_visit", "started_at": NOW.timestamp()-40, "duration": 10, "recognition_epoch_id": epoch1, "estimated_seconds": 3, "attribution_measured_seconds": 10},
                 {"id": str(uuid4()), "kind": "unknown_visit", "started_at": NOW.timestamp()-30, "duration": 5, "recognition_epoch_id": epoch2}]
    await seed(session_factory, ev("session.started", 1),
               ev("session.outcome", 2, {"status": "endedEarly", "known_seconds": 10, "unknown_seconds": 5, "estimated_seconds": 3, "attribution_measured_seconds": 10, "snapshot_id": snap, "revision": 2, "part_count": 2}),
               ev("session.timeline", 3, {"snapshot_id": snap, "revision": 2, "part_index": 0, "part_count": 2, "intervals": intervals}),
               ev("session.timeline", 4, {"snapshot_id": snap, "revision": 2, "part_index": 0, "part_count": 2, "intervals": intervals}))
    health = await get(api_client[0], admin_headers, "health")
    assert health["summary"]["known_seconds"] == 10
    assert health["summary"]["estimated_seconds"] == 3
    overview = await get(api_client[0], admin_headers)
    assert overview["quality"]["incomplete_snapshots"] == 1
    detail = await get(api_client[0], admin_headers, f"sessions/{I}/{S}")
    assert detail["selected_snapshot"]["received_parts"] == 1
    assert len(detail["state_intervals"]) == 1
    epochs = {e["recognition_epoch_id"]: e for e in detail["epochs"]}
    assert epochs[epoch1]["known_seconds"] == 10 and epochs[epoch2]["unknown_seconds"] == 5
    other = str(uuid4())
    await seed(session_factory, ev("session.started", 1, session=other), ev("session.outcome", 2, {"status": "completed", "snapshot_id": str(uuid4()), "revision": 2, "part_count": 1}, session=other))
    assert (await get(api_client[0], admin_headers))["quality"]["incomplete_snapshots"] == 2


async def test_diagnostic_drilldown_old_session_and_event_pages(api_client, admin_headers, session_factory):
    await seed(session_factory, ev("session.started", 1, age=20), ev("session.state", 2, {"previous_state": "acquiring", "state": "focused", "duration": 7}),
               ev("camera.error", 3, {"reason": "frame_silence", "count": 4}))
    assert (await get(api_client[0], admin_headers, "sessions"))["items"] == []
    found = await get(api_client[0], admin_headers, "sessions", activity="true", wait_state="acquiring")
    assert found["items"][0]["session_id"] == S
    found = await get(api_client[0], admin_headers, "sessions", activity="true", reason="frame_silence")
    assert found["items"][0]["error_count"] == 4
    first = await get(api_client[0], admin_headers, f"sessions/{I}/{S}/events", limit=2)
    second = await get(api_client[0], admin_headers, f"sessions/{I}/{S}/events", limit=2, cursor=first["next_cursor"])
    assert [e["sequence"] for e in first["items"] + second["items"]] == [1, 2, 3]
    assert second["next_cursor"] is None


def test_reporting_migration_round_trip():
    import importlib
    from sqlalchemy import create_engine, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    initial = importlib.import_module("migrations.versions.0008_telemetry")
    reporting = importlib.import_module("migrations.versions.0009_telemetry_reporting")
    with create_engine("sqlite:///:memory:").begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            initial.upgrade()
            reporting.upgrade()
            assert "ix_telemetry_reporting_identity" in {i["name"] for i in inspect(conn).get_indexes("telemetry_events")}
            reporting.downgrade()
            assert "ix_telemetry_reporting_identity" not in {i["name"] for i in inspect(conn).get_indexes("telemetry_events")}


async def test_sqlite_timestamps_are_explicit_utc(api_client, admin_headers, session_factory):
    event = ev("session.started", 1)
    await seed(session_factory, event, ev("camera.error", 2, {"reason": "frame_silence"}))
    row = (await get(api_client[0], admin_headers, "sessions"))["items"][0]
    parsed = datetime.fromisoformat(row["last_activity"])
    assert parsed.tzinfo is not None and parsed == event.occurred_at
    health = await get(api_client[0], admin_headers, "health")
    parsed = datetime.fromisoformat(health["diagnostics"][0]["occurred_at"])
    assert parsed.tzinfo is not None and parsed == event.occurred_at


async def test_activity_version_filter_matches_diagnostic_version(api_client, admin_headers, session_factory):
    await seed(session_factory, ev("session.started", 1, age=20, version="1"),
               ev("camera.error", 2, {"reason": "frame_silence"}, version="2"))
    result = await get(api_client[0], admin_headers, "sessions", activity="true", app_version="2", reason="frame_silence")
    assert result["items"][0]["session_id"] == S
    assert result["items"][0]["app_version"] == "1"

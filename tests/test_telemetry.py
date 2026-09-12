from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from app.telemetry import TelemetryEvent, TelemetrySession, purge
from app.routers.telemetry import _buckets

@pytest.fixture(autouse=True)
def reset_limits():
    _buckets.clear()

def event(**changes):
    value = dict(event_id=str(uuid4()), name="session.progress", version=1,
        installation_id=str(uuid4()), app_run_id=str(uuid4()), session_id=str(uuid4()), sequence=1,
        occurred_at=datetime.now(UTC).isoformat(), environment="production", app_version="1.0",
        app_build="1", os_version="18.0", device_class="iphone", properties={"status": "inProgress", "present_seconds": 15})
    value.update(changes)
    return value

async def test_retries_and_reordering(api_client, session_factory):
    client = api_client[0]
    newer = event(sequence=20, name="session.outcome", properties={"status": "completed", "present_seconds": 60})
    older = {**newer, "event_id": str(uuid4()), "sequence": 2, "name": "session.progress", "properties": {"status": "inProgress"}}
    response = await client.post("/v1/telemetry/events", json={"events": [newer, older, newer]})
    assert response.status_code == 200
    assert len(response.json()["accepted"]) == 2
    assert response.json()["duplicates"] == [newer["event_id"]]
    async with session_factory() as db:
        summary = await db.scalar(select(TelemetrySession))
        assert summary.sequence == 20
        assert summary.summary["properties"]["status"] == "completed"
        assert await db.scalar(select(func.count()).select_from(TelemetryEvent)) == 2

async def test_partial_validation_and_privacy(api_client):
    good = event()
    invalid = [event(properties={"task_name": "secret"}), event(properties={"configuration": {"name": "private"}}),
        event(name="unregistered"), event(version=2), event(properties={"duration": -1}),
        event(name="session.timeline", properties={"part_count": 0}), event(session_id=None)]
    result = (await api_client[0].post("/v1/telemetry/events", json={"events": [good, *invalid]})).json()
    assert result["accepted"] == [good["event_id"]]
    assert len(result["rejected"]) == len(invalid)
    assert "secret" not in str(result)

async def test_limits_and_kill_switch(api_client, settings):
    client = api_client[0]
    assert (await client.post("/v1/telemetry/events", content=b"x" * (256 * 1024 + 1))).status_code == 413
    assert (await client.post("/v1/telemetry/events", json={"events": [event()] * 101})).status_code == 422
    settings.telemetry_requests_per_minute = 1
    _buckets.clear()
    assert (await client.post("/v1/telemetry/events", json={"events": [event()]})).status_code == 200
    response = await client.post("/v1/telemetry/events", json={"events": [event()]})
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    settings.telemetry_enabled = False
    result = (await client.post("/v1/telemetry/events", json={"events": [event()]})).json()
    assert result["collection_enabled"] is False and result["accepted"] == []

async def test_timeline_parts_and_retention(api_client, session_factory):
    first = event(name="session.timeline", properties={"snapshot_id": str(uuid4()), "revision": 8, "part_index": 1, "part_count": 2, "intervals": []})
    second = {**first, "event_id": str(uuid4()), "sequence": 2, "properties": {**first["properties"], "part_index": 0}}
    assert len((await api_client[0].post("/v1/telemetry/events", json={"events": [first, second]})).json()["accepted"]) == 2
    async with session_factory() as db:
        await purge(db, datetime.now(UTC) + timedelta(days=91))
        assert await db.scalar(select(func.count()).select_from(TelemetryEvent)) == 0

async def test_numeric_swift_wire_timestamp_and_no_payload_logging(api_client, caplog):
    value = event(occurred_at=datetime.now(UTC).timestamp(), properties={"status": "inProgress", "configuration": {"tracks_participants": True, "minimum_confidence": 0.6}})
    with caplog.at_level("INFO"):
        response = await api_client[0].post("/v1/telemetry/events", json={"events": [value]})
    assert response.json()["accepted"] == [value["event_id"]]
    assert value["session_id"] not in caplog.text

async def test_configuration_switch(api_client, settings):
    assert (await api_client[0].get("/v1/telemetry/config")).json() == {"collection_enabled": True}
    settings.telemetry_enabled = False
    assert (await api_client[0].get("/v1/telemetry/config")).json() == {"collection_enabled": False}

def test_migration_round_trip():
    import importlib
    from sqlalchemy import create_engine, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    migration = importlib.import_module("migrations.versions.0008_telemetry")
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert {"telemetry_events", "telemetry_sessions"}.issubset(inspect(connection).get_table_names())
            migration.downgrade()
            assert not inspect(connection).get_table_names()

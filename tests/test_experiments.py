from __future__ import annotations

import pytest
from sqlalchemy import select
from app.models import ExperimentRule


async def seed(session_factory):
    async with session_factory() as db:
        db.add(ExperimentRule(key="companion", enabled=True, rollout_percentage=100,
                              allocation_salt="companion-v1"))
        await db.commit()


@pytest.mark.asyncio
async def test_public_and_live_admin_update(api_client, settings, session_factory, caplog, admin_headers):
    client, *_ = api_client
    await seed(session_factory)
    public = await client.get("/v1/experiments")
    assert public.status_code == 200
    assert public.headers["cache-control"] == "no-store"
    assert public.json() == {"schemaVersion": 1, "rules": {"companion": {
        "enabled": True, "rolloutPercentage": 100, "allocationSalt": "companion-v1"}}}
    headers = {**admin_headers, "XCorrelationId": "ba3c1f1a-63cf-4f23-a40b-13fc2f8e2e83"}
    with caplog.at_level("INFO"):
        changed = await client.put("/v1/admin/experiments/companion", headers=headers,
                                   json={"enabled": False, "rolloutPercentage": 25})
    assert changed.status_code == 200
    assert changed.json()["allocationSalt"] == "companion-v1"
    assert changed.headers["XCorrelationId"] == headers["XCorrelationId"]
    assert "experiment.updated" in caplog.text
    assert "operator-test-secret" not in caplog.text
    latest = (await client.get("/v1/experiments")).json()["rules"]["companion"]
    assert latest == changed.json()
    assert (await client.get("/v1/admin/experiments", headers=headers)).status_code == 200
    async with session_factory() as db:
        rule = await db.scalar(select(ExperimentRule))
        assert rule.updated_at is not None


@pytest.mark.asyncio
async def test_admin_fails_closed(api_client, settings):
    client, *_ = api_client
    assert (await client.get("/v1/admin/experiments")).status_code == 401
    for authorization in [None, "Bearer wrong", "Basic secret", "Bearer sécret"]:
        headers = {"Authorization": authorization} if authorization and authorization.isascii() else {}
        assert (await client.get("/v1/admin/experiments", headers=headers)).status_code == 401
        assert (await client.put("/v1/admin/experiments/companion", headers=headers,
                                 json={"enabled": True, "rolloutPercentage": 50})).status_code == 401


@pytest.mark.asyncio
async def test_validation_unknown_keys_and_boundaries(api_client, settings, session_factory, admin_headers):
    client, *_ = api_client
    await seed(session_factory)
    headers = admin_headers
    for percentage in [-1, 101, 0.5, "50", True]:
        response = await client.put("/v1/admin/experiments/companion", headers=headers,
                                    json={"enabled": True, "rolloutPercentage": percentage})
        assert response.status_code == 422
    for percentage in [0, 100]:
        response = await client.put("/v1/admin/experiments/companion", headers=headers,
                                    json={"enabled": True, "rolloutPercentage": percentage})
        assert response.status_code == 200
        assert response.json()["allocationSalt"] == "companion-v1"
    assert (await client.put("/v1/admin/experiments/unknown", headers=headers,
                             json={"enabled": True, "rolloutPercentage": 50})).status_code == 404
    assert (await client.put("/v1/admin/experiments/companion", headers=headers,
                             json={"enabled": True, "rolloutPercentage": 50,
                                   "allocationSalt": "reshuffle"})).status_code == 422


def test_migration_seeds_and_downgrades(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    database = tmp_path / "experiments.db"
    environment = dict(os.environ, STAYZY_ENVIRONMENT="test",
                       STAYZY_DATABASE_URL=f"sqlite+aiosqlite:///{database}")
    root = Path(__file__).resolve().parents[1]
    def migrate(target, direction="upgrade"):
        subprocess.run([sys.executable, "-m", "alembic", direction, target],
                       cwd=root, env=environment, check=True, capture_output=True)
    migrate("0005_administrators")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT key, enabled, rollout_percentage, allocation_salt FROM experiment_rules ORDER BY key").fetchall() == [
            ("companion", 1, 100, "companion-v1"),
            ("rive_character", 0, 0, "rive-character-v1")]
    migrate("0002_billing_freshness", "downgrade")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='experiment_rules'").fetchall() == []
    migrate("0005_administrators")


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_row", [False, True])
async def test_retired_flight_is_hidden_and_cannot_be_created_or_updated(
    api_client, session_factory, admin_headers, stale_row
):
    client, *_ = api_client
    await seed(session_factory)
    if stale_row:
        async with session_factory() as db:
            db.add(ExperimentRule(key="rive_character", enabled=True, rollout_percentage=100,
                                  allocation_salt="rive-character-v1"))
            await db.commit()
    for path in ["/v1/experiments", "/v1/admin/experiments"]:
        response = await client.get(path, headers=admin_headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert set(response.json()["rules"]) == {"companion"}
        assert response.json()["rules"]["companion"]["enabled"] is True
    update = await client.put("/v1/admin/experiments/rive_character", headers=admin_headers,
                              json={"enabled": True, "rolloutPercentage": 100})
    assert update.status_code == 404
    assert "experiment_not_found" in update.text
    creation = await client.post("/v1/admin/experiments", headers=admin_headers,
                                  json={"key": "rive_character", "enabled": True, "rolloutPercentage": 100})
    assert creation.status_code == 422
    assert "experiment_retired" in creation.text


def test_retirement_migration_preserves_other_rules(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    database = tmp_path / "retirement.db"
    environment = dict(os.environ, STAYZY_ENVIRONMENT="test",
                       STAYZY_DATABASE_URL=f"sqlite+aiosqlite:///{database}")
    root = Path(__file__).resolve().parents[1]

    def migrate(target, direction="upgrade"):
        subprocess.run([sys.executable, "-m", "alembic", direction, target],
                       cwd=root, env=environment, check=True, capture_output=True)

    def rows():
        with sqlite3.connect(database) as db:
            return db.execute("SELECT key, enabled, rollout_percentage, allocation_salt FROM experiment_rules ORDER BY key").fetchall()

    migrate("0009_telemetry_reporting")
    with sqlite3.connect(database) as db:
        db.execute("UPDATE experiment_rules SET enabled=1, rollout_percentage=75 WHERE key='rive_character'")
        db.execute("INSERT INTO experiment_rules (key, enabled, rollout_percentage, allocation_salt, updated_at) VALUES ('custom', 1, 42, 'custom-salt', CURRENT_TIMESTAMP)")
    expected = [row for row in rows() if row[0] != "rive_character"]
    migrate("head")
    assert rows() == expected
    migrate("0009_telemetry_reporting", "downgrade")
    assert rows() == expected + [("rive_character", 0, 0, "rive-character-v1")]
    migrate("head")
    assert rows() == expected
    # Fresh installation is independent of unrelated historical downgrade support.
    database = tmp_path / "fresh.db"
    environment["STAYZY_DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    migrate("head")
    assert rows() == [("companion", 1, 100, "companion-v1")]

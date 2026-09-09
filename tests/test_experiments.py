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
async def test_public_and_live_admin_update(api_client, settings, session_factory, caplog):
    client, *_ = api_client
    await seed(session_factory)
    public = await client.get("/v1/experiments")
    assert public.status_code == 200
    assert public.headers["cache-control"] == "no-store"
    assert public.json() == {"schemaVersion": 1, "rules": {"companion": {
        "enabled": True, "rolloutPercentage": 100, "allocationSalt": "companion-v1"}}}
    settings.experiment_admin_token = "operator-test-secret"
    headers = {"Authorization": "Bearer operator-test-secret", "XCorrelationId": "ba3c1f1a-63cf-4f23-a40b-13fc2f8e2e83"}
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
    settings.experiment_admin_token = None
    assert (await client.get("/v1/admin/experiments")).status_code == 503
    settings.experiment_admin_token = "secret"
    for authorization in [None, "Bearer wrong", "Basic secret", "Bearer sécret"]:
        headers = {"Authorization": authorization} if authorization and authorization.isascii() else {}
        assert (await client.get("/v1/admin/experiments", headers=headers)).status_code == 401
        assert (await client.put("/v1/admin/experiments/companion", headers=headers,
                                 json={"enabled": True, "rolloutPercentage": 50})).status_code == 401


@pytest.mark.asyncio
async def test_validation_unknown_keys_and_boundaries(api_client, settings, session_factory):
    client, *_ = api_client
    await seed(session_factory)
    settings.experiment_admin_token = "secret"
    headers = {"Authorization": "Bearer secret"}
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
    migrate("head")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT key, enabled, rollout_percentage, allocation_salt FROM experiment_rules").fetchall() == [
            ("companion", 1, 100, "companion-v1")]
    migrate("0002_billing_freshness", "downgrade")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='experiment_rules'").fetchall() == []
    migrate("head")

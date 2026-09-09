from __future__ import annotations

import asyncio
import pytest


@pytest.mark.asyncio
async def test_create_deliver_update_and_duplicate(api_client, settings, caplog, admin_headers):
    client, *_ = api_client
    headers = admin_headers
    body = {"key": "focus_coach", "enabled": False, "rolloutPercentage": 0}
    with caplog.at_level("INFO"):
        created = await client.post("/v1/admin/experiments", headers=headers, json=body)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    rule = created.json()
    assert rule.keys() == {"key", "enabled", "rolloutPercentage", "allocationSalt"}
    assert len(rule["allocationSalt"]) == 32
    assert "experiment.created" in caplog.text
    assert "creation-test-secret" not in caplog.text
    for endpoint in ["/v1/experiments", "/v1/admin/experiments"]:
        configuration = (await client.get(endpoint, headers=headers)).json()
        assert configuration["schemaVersion"] == 1
        assert configuration["rules"]["focus_coach"]["allocationSalt"] == rule["allocationSalt"]
    changed = await client.put("/v1/admin/experiments/focus_coach", headers=headers,
                               json={"enabled": True, "rolloutPercentage": 100})
    assert changed.status_code == 200
    assert changed.json()["allocationSalt"] == rule["allocationSalt"]
    duplicate = await client.post("/v1/admin/experiments", headers=headers, json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "experiment_exists"
    public = (await client.get("/v1/experiments")).json()["rules"]["focus_coach"]
    assert public["enabled"] is True and public["rolloutPercentage"] == 100


@pytest.mark.asyncio
async def test_creation_validation_and_auth(api_client, settings, admin_headers):
    client, *_ = api_client
    headers = admin_headers
    body = {"key": "valid", "enabled": False, "rolloutPercentage": 0}
    assert (await client.post("/v1/admin/experiments", json=body)).status_code == 401
    for field, values in {
        "key": ["", "Upper", "1start", "with-dash", "white space", "é", "a" * 81, 123, "key\n"],
        "enabled": [1, "true", None],
        "rolloutPercentage": [-1, 101, 0.5, "50", True],
    }.items():
        for value in values:
            response = await client.post("/v1/admin/experiments", headers=headers, json={**body, field: value})
            assert response.status_code == 422, (field, value, response.text)
    assert (await client.post("/v1/admin/experiments", headers=headers,
                              json={**body, "allocationSalt": "injected"})).status_code == 422
    assert (await client.post("/v1/admin/experiments", headers=headers,
                              json={**body, "key": "a" * 80, "rolloutPercentage": 100})).status_code == 201


@pytest.mark.asyncio
async def test_concurrent_duplicate_creation(api_client, settings, admin_headers):
    client, *_ = api_client
    responses = await asyncio.gather(*[
        client.post("/v1/admin/experiments", headers=admin_headers,
                    json={"key": "concurrent", "enabled": False, "rolloutPercentage": 0})
        for _ in range(2)
    ])
    assert sorted(response.status_code for response in responses) == [201, 409]

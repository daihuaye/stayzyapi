import pytest
from app.services.developer_access import DEVELOPER_EMAILS, has_developer_access
from conftest import sign_in


@pytest.mark.parametrize("address", sorted(DEVELOPER_EMAILS))
async def test_allowlisted_authenticated_accounts(api_client, address):
    client, _, email, *_ = api_client
    tokens = await sign_in(client, email, address)
    response = await client.get("/v1/me", headers={"Authorization": "Bearer " + tokens["access_token"]})
    assert response.status_code == 200
    assert response.json()["developer_access"] is True
    assert "email" not in response.json()
    assert has_developer_access("  " + address.upper() + "  ")


async def test_denies_guests_and_other_accounts(api_client):
    client, _, email, *_ = api_client
    assert (await client.get("/v1/me")).status_code == 401
    tokens = await sign_in(client, email, "outsider@example.com")
    response = await client.get("/v1/me?email=daihua.ye@gmail.com",
                                headers={"Authorization": "Bearer " + tokens["access_token"]})
    assert response.status_code == 200
    assert response.json()["developer_access"] is False
    assert not has_developer_access("daihua.ye+other@gmail.com")
    assert not has_developer_access("daihua.ye@gmail.com.example.com")

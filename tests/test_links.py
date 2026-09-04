from __future__ import annotations


async def test_aasa_uses_configured_team_and_bundle(api_client, settings) -> None:
    client, _, _, _, _ = api_client
    settings.apple_team_id = "ABCD123456"

    response = await client.get("/.well-known/apple-app-site-association")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["applinks"]["details"] == [
        {
            "appID": "ABCD123456.com.daihuaye.stayzy",
            "components": [
                {"/": "/auth/verify", "comment": "Stayzy passwordless sign-in"}
            ],
        }
    ]


async def test_browser_magic_link_get_does_not_consume_token(api_client) -> None:
    client, _, email, _, _ = api_client
    requested = await client.post(
        "/v1/auth/magic-links",
        json={"email": "person@example.com"},
    )
    assert requested.status_code == 202
    token = email.latest_token

    browser = await client.get("/auth/verify", params={"token": token})
    assert browser.status_code == 200
    assert browser.headers["cache-control"] == "no-store"

    consumed = await client.post(
        "/v1/auth/magic-links/verify",
        json={"token": token},
    )
    assert consumed.status_code == 200

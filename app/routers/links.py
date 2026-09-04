from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import Settings, get_settings


router = APIRouter(tags=["app-links"])


@router.get("/.well-known/apple-app-site-association", include_in_schema=False)
async def apple_app_site_association(
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    details: list[dict[str, object]] = []
    if settings.apple_team_id:
        details.append(
            {
                "appID": f"{settings.apple_team_id}.{settings.apple_bundle_id}",
                "components": [{"/": "/auth/verify", "comment": "Stayzy passwordless sign-in"}],
            }
        )
    return JSONResponse(
        content={"applinks": {"apps": [], "details": details}},
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/auth/verify", response_class=HTMLResponse, include_in_schema=False)
async def magic_link_landing(
    token: str = Query(min_length=32, max_length=512),
) -> HTMLResponse:
    # This GET deliberately never reads or mutates the magic-link database.
    # Mail scanners can safely inspect it; only the app's POST endpoint consumes
    # the one-time token.
    return HTMLResponse(
        """<!doctype html>
<html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Open Stayzy</title></head><body style="font-family:-apple-system,sans-serif;max-width:36rem;margin:4rem auto;padding:1rem;text-align:center">
<h1>Open Stayzy to continue</h1><p>This sign-in link is used only after Stayzy receives it.</p>
<p>If the app did not open, return to the email and press the link on your iPhone or iPad.</p>
</body></html>""",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )

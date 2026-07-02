"""Public chat playground — a ChatGPT-style client that consumes this gateway's API.

Served same-origin so the browser can call /v1/chat/stream with the user's API key
(no CORS hop). The page is static HTML/JS; all auth happens client-side via X-API-KEY.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app.core.config import settings

router = APIRouter(tags=["playground"], include_in_schema=False)
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Brand mark: rounded blue→violet tile with a white spark/star. Served as the
# site favicon (modern browsers render SVG favicons).
FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
    "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
    "<stop offset='0' stop-color='#2563eb'/><stop offset='1' stop-color='#7c3aed'/>"
    "</linearGradient></defs>"
    "<rect width='64' height='64' rx='15' fill='url(#g)'/>"
    "<path d='M32 13l4.6 9.4 10.4 1.5-7.5 7.3 1.8 10.3L32 46.8l-9.3 4.9 1.8-10.3"
    "-7.5-7.3 10.4-1.5z' fill='#fff'/></svg>"
)


@router.get("/favicon.ico", include_in_schema=False)
@router.get("/favicon.svg", include_in_schema=False)
async def favicon() -> Response:
    return Response(
        content=FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/chat", response_class=HTMLResponse)
async def chat_playground(request: Request) -> Response:
    return _templates.TemplateResponse(
        request, "chat.html", {"default_model": settings.default_model}
    )


@router.get("/integrate", response_class=HTMLResponse)
async def integrate_guide(request: Request) -> Response:
    """Client integration guide: copy-paste samples for Python/JS/.NET/PHP/Oracle."""
    base_url = str(request.base_url).rstrip("/")
    return _templates.TemplateResponse(
        request,
        "integrate.html",
        {"default_model": settings.default_model, "base_url": base_url},
    )

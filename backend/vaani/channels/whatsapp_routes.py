from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..errors import BadRequest, NotFound, Unauthorized
from ..security import secret_matches
from .whatsapp import verify_signature

router = APIRouter(include_in_schema=False)


@router.get("/whatsapp/webhook")
async def whatsapp_verify(request: Request):
    """Meta's one-time subscription handshake."""
    ctx = request.app.state.ctx
    if ctx.whatsapp is None:
        raise NotFound()
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and secret_matches(
        params.get("hub.verify_token"), ctx.settings.whatsapp_verify_token
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise Unauthorized()


@router.post("/whatsapp/webhook")
async def whatsapp_events(request: Request):
    ctx = request.app.state.ctx
    if ctx.whatsapp is None:
        raise NotFound()
    raw = await request.body()
    if not verify_signature(ctx.settings.whatsapp_app_secret, raw, request.headers.get("x-hub-signature-256")):
        raise Unauthorized()
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise BadRequest() from exc
    # Acknowledge immediately; Meta retries deliveries that aren't answered quickly.
    ctx.whatsapp.dispatch(payload)
    return {"ok": True}

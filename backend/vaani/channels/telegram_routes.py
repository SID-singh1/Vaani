from __future__ import annotations

from fastapi import APIRouter, Request

from ..errors import NotFound, Unauthorized
from ..security import secret_matches

router = APIRouter(include_in_schema=False)


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    channel = request.app.state.ctx.telegram
    if channel is None or channel.mode != "webhook":
        raise NotFound()
    if not secret_matches(request.headers.get("x-telegram-bot-api-secret-token"), channel.webhook_secret):
        raise Unauthorized()
    await channel.feed_webhook(await request.json())
    return {"ok": True}

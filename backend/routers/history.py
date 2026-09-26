import json
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from db.database import get_db
from db.models import Interaction
from schemas.api_schemas import HistoryResponse, InteractionHistoryItem
from core.config import config

router = APIRouter()

@router.get("/history/{user_id}", response_model=HistoryResponse)
def get_history(user_id: str, request: Request, db: Session = Depends(get_db)):
    # Protect Telegram users' privacy: Only authorized requests can query Telegram user histories
    if user_id.startswith("tg_"):
        auth_header = request.headers.get("X-Internal-Secret", "")
        auth_query = request.query_params.get("secret", "")
        if auth_header != config.INTERNAL_API_SECRET and auth_query != config.INTERNAL_API_SECRET:
            raise HTTPException(
                status_code=403, 
                detail="Access to private Telegram history is restricted. Use the /history command inside the Telegram bot."
            )

    interactions = db.query(Interaction).filter(Interaction.user_id == user_id).order_by(Interaction.timestamp.desc()).limit(5).all()
    
    history_items = []
    for interaction in interactions:
        action_items = json.loads(interaction.action_items) if interaction.action_items else []
        history_items.append(
            InteractionHistoryItem(
                id=interaction.id,
                timestamp=interaction.timestamp,
                transcript=interaction.transcript,
                summary=interaction.summary,
                action_items=action_items,
                sentiment=interaction.sentiment
            )
        )
    return HistoryResponse(history=history_items)

import json
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from db.database import get_db
from db.models import Interaction
from schemas.api_schemas import HistoryResponse, InteractionHistoryItem
from core.config import config

from typing import Optional, List
from pydantic import BaseModel

router = APIRouter()

class DeleteHistoryRequest(BaseModel):
    interaction_ids: Optional[List[str]] = None
    delete_all: bool = False

@router.get("/history/{user_id}", response_model=HistoryResponse)
def get_history(user_id: str, request: Request, limit: int = 5, db: Session = Depends(get_db)):
    # Protect Telegram users' privacy: Only authorized requests can query Telegram user histories
    if user_id.startswith("tg_"):
        auth_header = request.headers.get("X-Internal-Secret", "")
        auth_query = request.query_params.get("secret", "")
        if auth_header != config.INTERNAL_API_SECRET and auth_query != config.INTERNAL_API_SECRET:
            raise HTTPException(
                status_code=403, 
                detail="Access to private Telegram history is restricted. Use the /history command inside the Telegram bot."
            )

    safe_limit = min(max(1, limit), 50)
    interactions = db.query(Interaction).filter(Interaction.user_id == user_id).order_by(Interaction.timestamp.desc()).limit(safe_limit).all()
    
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

@router.post("/history/{user_id}/delete")
def delete_history(user_id: str, request: Request, payload: Optional[DeleteHistoryRequest] = None, db: Session = Depends(get_db)):
    if user_id.startswith("tg_"):
        auth_header = request.headers.get("X-Internal-Secret", "")
        if auth_header != config.INTERNAL_API_SECRET:
            raise HTTPException(status_code=403, detail="Unauthorized")

    query = db.query(Interaction).filter(Interaction.user_id == user_id)
    if payload and payload.interaction_ids and not payload.delete_all:
        query = query.filter(Interaction.id.in_(payload.interaction_ids))
    
    deleted_count = query.delete(synchronize_session=False)
    db.commit()
    return {"status": "success", "deleted_count": deleted_count}

@router.get("/interaction/{interaction_id}")
def get_single_interaction(interaction_id: str, request: Request, db: Session = Depends(get_db)):
    interaction = db.query(Interaction).filter(Interaction.id == interaction_id).first()
    if not interaction:
        raise HTTPException(status_code=404, detail="Interaction not found")
    
    # If it belongs to a Telegram user, check secret if accessed externally
    if interaction.user_id and interaction.user_id.startswith("tg_"):
        auth_header = request.headers.get("X-Internal-Secret", "")
        auth_query = request.query_params.get("secret", "")
        if auth_header != config.INTERNAL_API_SECRET and auth_query != config.INTERNAL_API_SECRET:
            raise HTTPException(status_code=403, detail="Unauthorized")
            
    action_items = json.loads(interaction.action_items) if interaction.action_items else []
    return {
        "id": interaction.id,
        "transcript": interaction.transcript,
        "summary": interaction.summary,
        "action_items": action_items,
        "sentiment": interaction.sentiment,
        "timestamp": interaction.timestamp.isoformat() if interaction.timestamp else None
    }


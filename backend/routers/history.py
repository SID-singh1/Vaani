import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from db.database import get_db
from db.models import Interaction
from schemas.api_schemas import HistoryResponse, InteractionHistoryItem

router = APIRouter()

@router.get("/history/{user_id}", response_model=HistoryResponse)
def get_history(user_id: str, db: Session = Depends(get_db)):
    interactions = db.query(Interaction).filter(Interaction.user_id == user_id).order_by(Interaction.timestamp.desc()).all()
    
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

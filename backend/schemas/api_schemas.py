from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class UsageStatus(BaseModel):
    tier: str
    requests_remaining_today: Optional[int] = None

class ProcessAudioResponse(BaseModel):
    interaction_id: str
    transcript: str
    summary: str
    action_items: List[str]
    sentiment: str
    usage: UsageStatus

class InteractionHistoryItem(BaseModel):
    id: str
    timestamp: datetime
    transcript: Optional[str]
    summary: Optional[str]
    action_items: List[str]
    sentiment: Optional[str]

    class Config:
        from_attributes = True

class HistoryResponse(BaseModel):
    history: List[InteractionHistoryItem]

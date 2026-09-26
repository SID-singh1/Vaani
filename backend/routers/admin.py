from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from db.database import get_db
from db.models import User, Interaction
from core.config import config

router = APIRouter()

@router.get("/analytics")
def get_analytics(request: Request, db: Session = Depends(get_db)):
    if config.ADMIN_SECRET_KEY:
        auth_header = request.headers.get("x-admin-key", "")
        auth_query = request.query_params.get("key", "")
        if auth_header != config.ADMIN_SECRET_KEY and auth_query != config.ADMIN_SECRET_KEY:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid Admin Key")
    try:
        # Total users
        total_users = db.query(User).count()

        # Total voice notes processed
        total_interactions = db.query(Interaction).count()

        # Sentiment Breakdown
        sentiment_counts = db.query(Interaction.sentiment, func.count(Interaction.id)).group_by(Interaction.sentiment).all()
        sentiment_data = {
            "Positive": 0,
            "Neutral": 0,
            "Negative": 0,
            "Unknown": 0
        }
        for sentiment, count in sentiment_counts:
            if sentiment:
                sentiment_data[sentiment] = count

        # Interactions over the last 7 days
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_interactions = db.query(
            func.date(Interaction.timestamp).label("date"), 
            func.count(Interaction.id).label("count")
        ).filter(Interaction.timestamp >= seven_days_ago) \
         .group_by(func.date(Interaction.timestamp)) \
         .order_by(func.date(Interaction.timestamp)).all()

        timeline = {str(item.date): item.count for item in recent_interactions}

        return {
            "total_users": total_users,
            "total_interactions": total_interactions,
            "sentiment_breakdown": sentiment_data,
            "timeline": timeline
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

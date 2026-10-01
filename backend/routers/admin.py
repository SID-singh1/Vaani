from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from db.database import get_db
from db.models import User, Interaction, Feedback
from core.config import config

router = APIRouter()

@router.get("/analytics")
def get_analytics(request: Request, days: int = 7, db: Session = Depends(get_db)):
    if config.ADMIN_SECRET_KEY:
        auth_header = request.headers.get("x-admin-key", "")
        auth_query = request.query_params.get("key", "")
        if auth_header != config.ADMIN_SECRET_KEY and auth_query != config.ADMIN_SECRET_KEY:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid Admin Key")
    try:
        # Total users and interactions
        total_users = db.query(User).count()
        total_interactions = db.query(Interaction).count()

        # Today's interactions (UTC)
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_interactions = db.query(Interaction).filter(Interaction.timestamp >= today_start).count()

        # Feedback & Accuracy metrics
        thumbs_up = db.query(Interaction).filter(Interaction.accuracy_rating == "thumbs_up").count()
        thumbs_down = db.query(Interaction).filter(Interaction.accuracy_rating == "thumbs_down").count()
        total_feedback = thumbs_up + thumbs_down
        accuracy_percentage = round((thumbs_up / total_feedback) * 100, 1) if total_feedback > 0 else None

        # Sentiment Breakdown
        sentiment_counts = db.query(Interaction.sentiment, func.count(Interaction.id)).group_by(Interaction.sentiment).all()
        sentiment_data = {
            "Positive": 0,
            "Neutral": 0,
            "Negative": 0,
            "Unknown": 0
        }
        for sentiment, count in sentiment_counts:
            if sentiment in sentiment_data:
                sentiment_data[sentiment] = count
            elif sentiment:
                sentiment_data["Unknown"] += count

        # Interactions timeline over the selected date range
        cutoff_date = datetime.utcnow() - timedelta(days=days) if days > 0 else datetime.min
        recent_interactions_query = db.query(
            func.date(Interaction.timestamp).label("date"), 
            func.count(Interaction.id).label("count")
        )
        if days > 0:
            recent_interactions_query = recent_interactions_query.filter(Interaction.timestamp >= cutoff_date)
            
        recent_interactions = recent_interactions_query.group_by(func.date(Interaction.timestamp)) \
                                                      .order_by(func.date(Interaction.timestamp)).all()

        timeline = {str(item.date): item.count for item in recent_interactions}

        # 10 Most recent interactions for live activity feed
        recent_records = db.query(Interaction).order_by(Interaction.timestamp.desc()).limit(10).all()
        recent_list = []
        for r in recent_records:
            user_display = r.user_id
            if user_display and len(user_display) > 10:
                user_display = user_display[:6] + "..." + user_display[-4:]
            recent_list.append({
                "id": r.id,
                "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M") if r.timestamp else "",
                "user_id": user_display,
                "summary": r.summary or "No summary generated",
                "sentiment": r.sentiment or "Neutral",
                "accuracy_rating": r.accuracy_rating or "unrated"
            })

        # 10 Most recent direct user feedback messages
        feedback_records = db.query(Feedback).order_by(Feedback.timestamp.desc()).limit(10).all()
        feedback_list = [
            {
                "id": f.id,
                "timestamp": f.timestamp.strftime("%Y-%m-%d %H:%M") if f.timestamp else "",
                "user_id": f.user_id[:6] + "..." if f.user_id and len(f.user_id) > 6 else f.user_id,
                "message": f.message
            }
            for f in feedback_records
        ]

        return {
            "total_users": total_users,
            "total_interactions": total_interactions,
            "today_interactions": today_interactions,
            "feedback": {
                "total": total_feedback,
                "thumbs_up": thumbs_up,
                "thumbs_down": thumbs_down,
                "accuracy_percentage": accuracy_percentage
            },
            "sentiment_breakdown": sentiment_data,
            "timeline": timeline,
            "recent_interactions": recent_list,
            "user_feedbacks": feedback_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

import os
import uuid
import json
from fastapi import APIRouter, UploadFile, File, Form, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from db.database import get_db
from db.models import User, Interaction
from schemas.api_schemas import ProcessAudioResponse, UsageStatus
from services.asr_service import transcribe_audio
from services.llm_service import summarize_transcript
from core.rate_limiter import limiter
from core.config import config

router = APIRouter()

TEMP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ml", "data", "temp"))
os.makedirs(TEMP_DIR, exist_ok=True)

@router.post("/process-audio", response_model=ProcessAudioResponse)
@limiter.limit(config.RATE_LIMIT_DEFAULT)
async def process_audio(
    request: Request,
    user_id: str = Form(...),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # Ensure user exists
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id)
        db.add(user)
        db.commit()

    # Save audio temporarily
    temp_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}_{audio.filename}")
    try:
        with open(temp_path, "wb") as f:
            content = await audio.read()
            f.write(content)
            
        # Run ML Pipeline
        transcript = await transcribe_audio(temp_path)
        llm_result = await summarize_transcript(transcript)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
    finally:
        # Cleanup temporary audio file
        if os.path.exists(temp_path):
            os.remove(temp_path)
    
    # Save Interaction
    interaction = Interaction(
        user_id=user_id,
        transcript=transcript,
        summary=llm_result["summary"],
        action_items=json.dumps(llm_result["action_items"]),
        sentiment=llm_result["sentiment"]
    )
    db.add(interaction)
    db.commit()

    # Return response
    return ProcessAudioResponse(
        transcript=transcript,
        summary=llm_result["summary"],
        action_items=llm_result["action_items"],
        sentiment=llm_result["sentiment"],
        usage=UsageStatus(tier=user.tier, requests_remaining_today=None) # We can calculate remaining later
    )


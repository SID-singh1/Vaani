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

MAX_AUDIO_SIZE = 15 * 1024 * 1024  # 15 MB limit

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

    # Sanitize extension and generate safe UUID filename (prevents directory traversal)
    original_ext = os.path.splitext(audio.filename or "")[1].lower()
    allowed_exts = [".ogg", ".oga", ".wav", ".mp3", ".webm", ".m4a", ".aac"]
    safe_ext = original_ext if original_ext in allowed_exts else ".oga"
    safe_filename = f"{uuid.uuid4()}{safe_ext}"
    temp_path = os.path.join(TEMP_DIR, safe_filename)

    try:
        content = await audio.read()
        if len(content) > MAX_AUDIO_SIZE:
            raise HTTPException(status_code=413, detail="Audio file too large. Maximum allowed size is 15MB.")

        with open(temp_path, "wb") as f:
            f.write(content)
            
        # Run ML Pipeline
        transcript = await transcribe_audio(temp_path)
        llm_result = await summarize_transcript(transcript)
        
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
        db.refresh(interaction)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Log failure to database
        failed_interaction = Interaction(
            user_id=user_id,
            error_message=str(e),
            sentiment="Error"
        )
        db.add(failed_interaction)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
    finally:
        # Cleanup temporary audio file
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # Return response
    return ProcessAudioResponse(
        interaction_id=interaction.id,
        transcript=transcript,
        summary=llm_result["summary"],
        action_items=llm_result["action_items"],
        sentiment=llm_result["sentiment"],
        usage=UsageStatus(tier=user.tier, requests_remaining_today=None) # We can calculate remaining later
    )

@router.post("/feedback")
def submit_feedback(
    interaction_id: str = Form(...),
    rating: str = Form(...),
    db: Session = Depends(get_db)
):
    interaction = db.query(Interaction).filter(Interaction.id == interaction_id).first()
    if not interaction:
        raise HTTPException(status_code=404, detail="Interaction not found")
    
    interaction.accuracy_rating = rating
    db.commit()
    return {"status": "success"}


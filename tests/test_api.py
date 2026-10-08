import sys
import os
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

# Add backend to path so we can import main
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

from main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert len(response.text) > 0

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "Vaani"}

@patch("routers.audio.transcribe_audio", new_callable=AsyncMock)
@patch("routers.audio.summarize_transcript", new_callable=AsyncMock)
def test_process_audio(mock_summarize, mock_transcribe):
    mock_transcribe.return_value = "Kal team meeting karni hai"
    mock_summarize.return_value = {
        "transcript": "Kal team meeting karni hai",
        "summary": "Team meeting scheduled for tomorrow.",
        "action_items": ["Hold team meeting tomorrow"],
        "sentiment": "Neutral"
    }

    dummy_wav = "tests/test_sample.wav"
    # Minimal valid 44-byte WAV header
    with open(dummy_wav, "wb") as f:
        f.write(b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

    try:
        with open(dummy_wav, "rb") as f:
            response = client.post(
                "/process-audio",
                data={"user_id": "test_user_123"},
                files={"audio": ("dummy.wav", f, "audio/wav")}
            )

        assert response.status_code == 200
        data = response.json()
        assert "transcript" in data
        assert "summary" in data
        assert "action_items" in data
        assert "sentiment" in data
        assert data["usage"]["tier"] == "free"
    finally:
        if os.path.exists(dummy_wav):
            os.remove(dummy_wav)

def test_history():
    response = client.get("/history/test_user_123")
    assert response.status_code == 200
    data = response.json()
    assert "history" in data
    assert len(data["history"]) >= 1
    assert data["history"][0]["transcript"] is not None

def test_get_single_interaction():
    # First get an existing interaction id from history
    hist_res = client.get("/history/test_user_123")
    assert hist_res.status_code == 200
    interaction_id = hist_res.json()["history"][0]["id"]

    # Test valid fetch
    res = client.get(f"/interaction/{interaction_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == interaction_id
    assert "transcript" in data
    assert "summary" in data
    assert "action_items" in data

    # Test not found
    not_found = client.get("/interaction/non_existent_id_999")
    assert not_found.status_code == 404

def test_hallucination_and_repetition_cleaner():
    from services.asr_service import _clean_whisper_hallucinations

    # 1. Strips YouTube/Whisper outro hallucinations
    assert _clean_whisper_hallucinations("Main kal aunga. Thank you for watching!") == "Main kal aunga."

    # 2. Collapses multi-word autoregressive loops
    loop_text = "apne apne avaram aur apne apne avaram aur tip ko"
    cleaned = _clean_whisper_hallucinations(loop_text)
    assert cleaned == "apne apne avaram aur tip ko"

    # 3. Preserves natural Hindi 2-word reduplication
    reduplication = "kabhi kabhi hum dheere dheere chalte hain"
    assert _clean_whisper_hallucinations(reduplication) == "kabhi kabhi hum dheere dheere chalte hain"

    # 4. Collapses 3+ repeated single words
    triple_repeat = "audio note audio note audio note"
    assert _clean_whisper_hallucinations(triple_repeat) == "audio note"


def test_local_mode_transcription_returns_text():
    # Regression: the local branch used to be unreachable, so local mode returned None.
    import asyncio
    import services.asr_service as asr

    with patch.object(asr, "USE_LOCAL_MODELS", True), \
         patch.object(asr, "_transcribe_locally", return_value="kal meeting hai") as local:
        result = asyncio.run(asr.transcribe_audio("note.wav"))

    assert result == "kal meeting hai"
    local.assert_called_once_with("note.wav")

def test_telegram_history_requires_secret_header():
    from core.config import config

    # No secret configured on the server: always denied, even with a guessed value.
    with patch.object(config, "INTERNAL_API_SECRET", ""):
        res = client.get("/history/tg_1", headers={"X-Internal-Secret": "vaani_internal_secret_key_2026"})
        assert res.status_code == 403

    with patch.object(config, "INTERNAL_API_SECRET", "s3cret"):
        assert client.get("/history/tg_1").status_code == 403
        # Query-string secrets are no longer accepted (they end up in logs).
        assert client.get("/history/tg_1?secret=s3cret").status_code == 403
        assert client.get("/history/tg_1", headers={"X-Internal-Secret": "s3cret"}).status_code == 200


def test_admin_requires_header_key():
    from core.config import config

    with patch.object(config, "ADMIN_SECRET_KEY", "adm"):
        assert client.get("/admin/analytics?key=adm").status_code == 401
        assert client.get("/admin/analytics", headers={"x-admin-key": "adm"}).status_code == 200

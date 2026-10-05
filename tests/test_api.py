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

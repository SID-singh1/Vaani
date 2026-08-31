import sys
import os
import pytest
from fastapi.testclient import TestClient

# Add backend to path so we can import main
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

from main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()

def test_process_audio():
    # Create a dummy audio file
    with open("dummy.wav", "wb") as f:
        f.write(b"dummy audio data")
        
    with open("dummy.wav", "rb") as f:
        response = client.post(
            "/process-audio",
            data={"user_id": "test_user_123"},
            files={"audio": ("dummy.wav", f, "audio/wav")}
        )
    
    os.remove("dummy.wav")
    
    assert response.status_code == 200
    data = response.json()
    assert "transcript" in data
    assert "summary" in data
    assert "action_items" in data
    assert "sentiment" in data
    assert data["usage"]["tier"] == "free"

def test_history():
    response = client.get("/history/test_user_123")
    assert response.status_code == 200
    data = response.json()
    assert "history" in data
    assert len(data["history"]) >= 1
    assert data["history"][0]["transcript"] is not None

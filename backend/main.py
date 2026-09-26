from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from routers import audio, history
from core.rate_limiter import limiter
from core.config import config
from db.database import engine, Base

import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Create DB tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title=config.PROJECT_NAME)

# Set up Rate Limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For dev. In prod, lock this down.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

# Include routers
from routers import audio, history, admin
app.include_router(audio.router, tags=["Audio Processing"])
app.include_router(history.router, tags=["History"])
app.include_router(admin.router, prefix="/admin", tags=["Admin Analytics"])

# Mount static web files
WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))
os.makedirs(WEB_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Vaani"}

@app.get("/")
def read_root():
    # Serve index.html by default
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": f"Welcome to {config.PROJECT_NAME} API"}

@app.get("/admin")
def read_admin():
    # Serve admin.html
    admin_path = os.path.join(WEB_DIR, "admin.html")
    if os.path.exists(admin_path):
        return FileResponse(admin_path)
    return {"message": "Admin dashboard not found"}

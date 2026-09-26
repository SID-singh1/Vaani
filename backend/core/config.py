import os

class Config:
    PROJECT_NAME = "Vaani Backend"
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vaani.db")
    # Rate limit: 30 requests per minute per IP to prevent spam and protect AI quotas
    RATE_LIMIT_DEFAULT = os.getenv("RATE_LIMIT_DEFAULT", "30/minute")
    
    # Internal secret shared between Telegram bot and Backend to protect private user history
    INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "vaani_internal_secret_key_2026")
    
    # Admin secret key to protect admin metrics (leave empty to allow open access in dev)
    ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "")

config = Config()

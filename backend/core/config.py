import hmac
import os

class Config:
    PROJECT_NAME = "Vaani Backend"
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vaani.db")
    # Rate limit: 30 requests per minute per IP to prevent spam and protect AI quotas
    RATE_LIMIT_DEFAULT = os.getenv("RATE_LIMIT_DEFAULT", "30/minute")
    
    # Internal secret shared between Telegram bot and Backend to protect private user history.
    # No default: if unset, access to Telegram histories is denied (fail closed).
    INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")
    
    # Admin secret key to protect admin metrics (leave empty to allow open access in dev)
    ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "")

config = Config()


def secret_matches(provided: str, expected: str) -> bool:
    """Constant-time comparison that never accepts an unset secret."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())

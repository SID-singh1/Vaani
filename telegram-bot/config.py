import os
from dotenv import load_dotenv

# Load environment variables from the parent directory's .env file
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(dotenv_path)

class BotConfig:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    FASTAPI_BACKEND_URL = os.getenv("FASTAPI_BACKEND_URL", "http://127.0.0.1:8000")
    INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "vaani_internal_secret_key_2026")
    
    @classmethod
    def validate(cls):
        if not cls.TELEGRAM_BOT_TOKEN or cls.TELEGRAM_BOT_TOKEN == "your_token_here":
            raise ValueError("TELEGRAM_BOT_TOKEN is missing or invalid in the .env file. Please add it.")

config = BotConfig()

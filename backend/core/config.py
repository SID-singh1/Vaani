import os

class Config:
    PROJECT_NAME = "Vaani Backend"
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vaani.db")
    RATE_LIMIT_DEFAULT = "5/day"

config = Config()

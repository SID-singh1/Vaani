from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from core.config import config

if config.DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        config.DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

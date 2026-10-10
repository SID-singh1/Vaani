from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow() -> datetime:
    # Stored naive-UTC to match rows written by earlier versions.
    return datetime.now(UTC).replace(tzinfo=None)


class NoteStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    ACTIVE = (QUEUED, PROCESSING)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)  # web_* / user_* (legacy web) / tg_* / wa_*
    tier = Column(String, default="free")
    created_at = Column(DateTime, default=utcnow)
    engine_preference = Column(String, nullable=True)
    claimed_at = Column(DateTime, nullable=True)  # when a legacy web id was bound to a signed session

    interactions = relationship("Interaction", back_populates="user")
    feedbacks = relationship("Feedback", back_populates="user")


class Interaction(Base):
    """One voice (or text) note and its processing job. The table name predates v2."""

    __tablename__ = "interactions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id = Column(String, ForeignKey("users.id"))
    timestamp = Column(DateTime, default=utcnow)  # created at
    audio_duration_sec = Column(Float, nullable=True)
    transcript = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    action_items = Column(Text, nullable=True)  # JSON list of {task, owner, due} (or plain strings, pre-v2)
    sentiment = Column(String, nullable=True)
    accuracy_rating = Column(String, nullable=True)  # thumbs_up / thumbs_down
    error_message = Column(Text, nullable=True)

    # v2
    status = Column(String, nullable=True, index=True)
    stage = Column(String, nullable=True)
    title = Column(String, nullable=True)
    channel = Column(String, nullable=True)  # web / telegram / whatsapp
    engine = Column(String, nullable=True)  # cloud / private
    source = Column(String, nullable=True)  # audio / text
    models = Column(String, nullable=True)  # which models actually served the note
    external_id = Column(String, nullable=True)  # channel message id, for idempotent webhooks
    asr_ms = Column(Integer, nullable=True)
    llm_ms = Column(Integer, nullable=True)
    total_ms = Column(Integer, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="interactions")

    __table_args__ = (
        Index("ix_interactions_user_ts", "user_id", "timestamp"),
        Index("ux_interactions_external_id", "external_id", unique=True),
    )

    @property
    def effective_status(self) -> str:
        if self.status:
            return self.status
        # Rows from before v2 were written synchronously: either finished or errored.
        return NoteStatus.FAILED if self.error_message else NoteStatus.DONE

    def action_items_list(self) -> list[dict]:
        if not self.action_items:
            return []
        try:
            raw = json.loads(self.action_items)
        except (TypeError, ValueError):
            return []
        items = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, str):
                items.append({"task": item, "owner": None, "due": None})
            elif isinstance(item, dict) and item.get("task"):
                items.append({"task": item["task"], "owner": item.get("owner"), "due": item.get("due")})
        return items


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id = Column(String, ForeignKey("users.id"))
    timestamp = Column(DateTime, default=utcnow)
    message = Column(Text, nullable=False)

    user = relationship("User", back_populates="feedbacks")

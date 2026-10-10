"""Usage limits that keep the service inside free API tiers and stop one user from
starving everyone else. Counts come from the notes table, so they survive restarts."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from .config import Settings
from .db import repo
from .db.models import utcnow
from .errors import QuotaExceeded


def _human_wait(td: timedelta) -> str:
    minutes = max(1, int(td.total_seconds() // 60) + 1)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60)
    return f"{hours} hour{'s' if hours != 1 else ''}"


class QuotaService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def unlimited(self, user_id: str) -> bool:
        return user_id in self.settings.unlimited_user_ids

    def check(self, session: Session, user_id: str, engine: str, *, private_available: bool = False) -> None:
        if self.unlimited(user_id):
            return
        s = self.settings
        now = utcnow()

        window = timedelta(minutes=s.user_burst_window_minutes)
        if s.user_burst_limit and repo.count_user_notes_since(session, user_id, now - window) >= s.user_burst_limit:
            raise QuotaExceeded(
                f"You're sending notes quickly. You can send {s.user_burst_limit} every "
                f"{s.user_burst_window_minutes} minutes, so please wait a few minutes."
            )

        day = now - timedelta(days=1)
        if s.user_daily_note_limit and repo.count_user_notes_since(session, user_id, day) >= s.user_daily_note_limit:
            oldest = repo.oldest_user_note_since(session, user_id, day) or now
            wait = _human_wait(oldest + timedelta(days=1) - now)
            raise QuotaExceeded(
                f"You've used all {s.user_daily_note_limit} free notes for today. You can send more in about {wait}."
            )

        cloud_cap = s.global_daily_cloud_limit
        if engine == "cloud" and cloud_cap and repo.count_engine_notes_since(session, "cloud", day) >= cloud_cap:
            hint = " You can switch to Private mode in the meantime." if private_available else ""
            raise QuotaExceeded(
                "Vaani has reached its free daily capacity for Fast mode. Please try again tomorrow." + hint
            )

    def usage(self, session: Session, user_id: str) -> dict:
        used = repo.count_user_notes_since(session, user_id, utcnow() - timedelta(days=1))
        limit = None if self.unlimited(user_id) else (self.settings.user_daily_note_limit or None)
        return {"used_today": used, "daily_limit": limit}

"""Usage limits that keep the service inside free API tiers and stop one user from
starving everyone else. Counts come from the notes table, so they survive restarts.

Free users have per-user limits (notes and audio minutes per rolling 24h, plus a burst limit).
Pro users (users.tier = "pro") skip those. Everyone shares the global cloud caps, which are
sized to the free Groq/Gemini quotas; UNLIMITED_USER_IDS (the developer) skip everything."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from .config import Settings
from .db import repo
from .db.models import utcnow
from .errors import QuotaExceeded

PRO_TIER = "pro"


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

    @staticmethod
    def tier(session: Session, user_id: str) -> str:
        user = repo.get_user(session, user_id)
        return (user.tier if user else None) or "free"

    def check(self, session: Session, user_id: str, engine: str, *, private_available: bool = False) -> None:
        if self.unlimited(user_id):
            return
        s = self.settings
        now = utcnow()
        day = now - timedelta(days=1)

        if self.tier(session, user_id) != PRO_TIER:
            window = timedelta(minutes=s.user_burst_window_minutes)
            if s.user_burst_limit and repo.count_user_notes_since(session, user_id, now - window) >= s.user_burst_limit:
                raise QuotaExceeded(
                    f"You're sending notes quickly. You can send {s.user_burst_limit} every "
                    f"{s.user_burst_window_minutes} minutes, so please wait a few minutes.",
                    limit="burst",
                )

            if (
                s.user_daily_note_limit
                and repo.count_user_notes_since(session, user_id, day) >= s.user_daily_note_limit
            ):
                wait = self._wait(session, user_id, day, now)
                raise QuotaExceeded(
                    f"You've used all {s.user_daily_note_limit} free notes for today. "
                    f"You can send more in about {wait}.",
                    limit="daily_notes",
                )

            if s.user_daily_audio_minutes:
                used = repo.sum_user_audio_since(session, user_id, day) / 60
                if used >= s.user_daily_audio_minutes:
                    wait = self._wait(session, user_id, day, now)
                    raise QuotaExceeded(
                        f"You've used your {s.user_daily_audio_minutes} free minutes of audio for today. "
                        f"You can send more in about {wait}.",
                        limit="daily_audio",
                    )

        if engine == "cloud":
            notes_pct, audio_pct = self.cloud_usage(session)
            if notes_pct >= 100 or audio_pct >= 100:
                hint = " You can switch to Private mode in the meantime." if private_available else ""
                raise QuotaExceeded(
                    "Vaani is at full capacity for today because of high demand. Please try again tomorrow." + hint,
                    limit="global_notes" if notes_pct >= 100 else "global_audio",
                )

    @staticmethod
    def _wait(session: Session, user_id: str, day, now) -> str:
        oldest = repo.oldest_user_note_since(session, user_id, day) or now
        return _human_wait(oldest + timedelta(days=1) - now)

    def cloud_usage(self, session: Session) -> tuple[float, float]:
        """How full the global cloud caps are over the last 24h, as percentages (0 when uncapped)."""
        s = self.settings
        day = utcnow() - timedelta(days=1)
        notes = audio = 0.0
        if s.global_daily_cloud_limit:
            notes = 100 * repo.count_engine_notes_since(session, "cloud", day) / s.global_daily_cloud_limit
        if s.global_daily_audio_minutes:
            audio = 100 * repo.sum_engine_audio_since(session, "cloud", day) / 60 / s.global_daily_audio_minutes
        return notes, audio

    def usage(self, session: Session, user_id: str) -> dict:
        day = utcnow() - timedelta(days=1)
        used = repo.count_user_notes_since(session, user_id, day)
        audio = repo.sum_user_audio_since(session, user_id, day) / 60
        tier = self.tier(session, user_id)
        capped = not self.unlimited(user_id) and tier != PRO_TIER
        return {
            "tier": tier,
            "used_today": used,
            "daily_limit": (self.settings.user_daily_note_limit or None) if capped else None,
            "audio_minutes_today": round(audio, 1),
            "daily_audio_minutes": (self.settings.user_daily_audio_minutes or None) if capped else None,
        }

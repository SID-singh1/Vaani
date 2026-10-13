"""Usage analytics for the admin dashboard. Every number is computed from real rows;
accounts listed in ANALYTICS_EXCLUDED_USER_IDS (your own testing) are left out."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..context import AppContext
from ..db.models import Event, Feedback, Interaction, NoteStatus, WaitlistEntry, utcnow
from ..quotas import QuotaService
from .deps import get_ctx, require_admin
from .ratelimit import client_ip

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(require_admin)])


def mask_user(user_id: str | None) -> str:
    if not user_id:
        return "unknown"
    prefix, _, rest = user_id.partition("_")
    return f"{prefix}_…{rest[-4:]}" if rest else user_id[:4] + "…"


def percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


ENGAGED_EVENTS = ("copy", "download", "share", "transcript_open", "history_open")


def retention(user_days: dict[str, set[date]], first_seen: dict[str, datetime], now: datetime, days: int) -> dict:
    """Of users who started at least `days` ago, the share who came back within `days` of their first day."""
    cohort = [u for u, first in first_seen.items() if first <= now - timedelta(days=days)]
    returned = 0
    for user in cohort:
        start = first_seen[user].date()
        if any(start < d <= start + timedelta(days=days) for d in user_days[user]):
            returned += 1
    return {
        "cohort": len(cohort),
        "returned": returned,
        "rate": round(100 * returned / len(cohort), 1) if cohort else None,
    }


def engagement_stats(session: Session, since: datetime, excluded: list[str], done_in_window: int) -> dict:
    query = select(Event.type, Event.channel, Event.detail, Event.note_id, Event.user_id).where(
        Event.timestamp >= since
    )
    if excluded:
        query = query.where(Event.user_id.not_in(excluded))
    events = session.execute(query).all()
    engaged_notes = {e.note_id for e in events if e.type in ENGAGED_EVENTS and e.note_id}
    quota = [e for e in events if e.type == "quota_hit"]
    return {
        "events": dict(Counter(e.type for e in events)),
        "by_channel": {
            channel: dict(Counter(e.type for e in events if (e.channel or "web") == channel))
            for channel in sorted({e.channel or "web" for e in events})
        },
        # Share of finished notes someone copied, downloaded, shared or opened again.
        "engaged_note_rate": round(100 * len(engaged_notes) / done_in_window, 1) if done_in_window else None,
        "quota_hits": dict(Counter(e.detail or "unknown" for e in quota)),
        "quota_blocked_users": len({e.user_id for e in quota}),
    }


def waitlist_stats(session: Session, since: datetime, excluded: list[str], total_users: int, wau: int) -> dict:
    query = select(WaitlistEntry).order_by(WaitlistEntry.timestamp.desc())
    if excluded:
        query = query.where(WaitlistEntry.user_id.not_in(excluded))
    entries = session.scalars(query).all()
    return {
        "total": len(entries),
        "in_window": sum(1 for e in entries if e.timestamp and e.timestamp >= since),
        "pct_of_users": round(100 * len(entries) / total_users, 1) if total_users else None,
        "pct_of_wau": round(100 * len(entries) / wau, 1) if wau else None,
        "by_source": dict(Counter(e.source or "unknown" for e in entries)),
        "recent": [
            {
                "created_at": e.timestamp.isoformat() + "Z" if e.timestamp else None,
                "user": mask_user(e.user_id),
                "channel": e.channel,
                "source": e.source,
                "email": e.email,
            }
            for e in entries[:20]
        ],
    }


def compute_analytics(session: Session, days: int, excluded: list[str], quotas: QuotaService | None = None) -> dict:
    now = utcnow()
    since = now - timedelta(days=days)
    status = func.coalesce(Interaction.status, NoteStatus.DONE)

    def scoped(query):
        return query.where(Interaction.user_id.not_in(excluded)) if excluded else query

    # All-time activity per user: first note and the distinct days they were active.
    user_days: dict[str, set[date]] = defaultdict(set)
    first_seen: dict[str, datetime] = {}
    for user_id, ts in session.execute(
        scoped(select(Interaction.user_id, Interaction.timestamp).where(status == NoteStatus.DONE))
    ):
        if user_id is None or ts is None:
            continue
        user_days[user_id].add(ts.date())
        if user_id not in first_seen or ts < first_seen[user_id]:
            first_seen[user_id] = ts

    def active_since(delta: timedelta) -> int:
        cutoff = (now - delta).date()
        return sum(1 for days_ in user_days.values() if any(d >= cutoff for d in days_))

    rows = session.execute(
        scoped(
            select(
                Interaction.user_id,
                Interaction.timestamp,
                status.label("status"),
                Interaction.channel,
                Interaction.engine,
                Interaction.total_ms,
                Interaction.audio_duration_sec,
                Interaction.sentiment,
            ).where(Interaction.timestamp >= since)
        )
    ).all()
    done = [r for r in rows if r.status == NoteStatus.DONE]
    failed = [r for r in rows if r.status == NoteStatus.FAILED]
    latencies = [r.total_ms for r in done if r.total_ms]
    speedups = [round(r.audio_duration_sec * 1000 / r.total_ms) for r in done if r.total_ms and r.audio_duration_sec]

    timeline_notes: Counter[str] = Counter()
    timeline_users: dict[str, set[str]] = defaultdict(set)
    for r in done:
        day = r.timestamp.date().isoformat()
        timeline_notes[day] += 1
        timeline_users[day].add(r.user_id)
    timeline = []
    for offset in range(days, -1, -1):
        day = (now - timedelta(days=offset)).date().isoformat()
        timeline.append(
            {"date": day, "notes": timeline_notes.get(day, 0), "active_users": len(timeline_users.get(day, ()))}
        )

    ratings = dict(
        session.execute(
            scoped(
                select(Interaction.accuracy_rating, func.count(Interaction.id))
                .where(Interaction.accuracy_rating.is_not(None))
                .group_by(Interaction.accuracy_rating)
            )
        ).all()
    )
    up, down = ratings.get("thumbs_up", 0), ratings.get("thumbs_down", 0)

    recent = session.scalars(scoped(select(Interaction).order_by(Interaction.timestamp.desc()).limit(12))).all()
    feedback_query = select(Feedback).order_by(Feedback.timestamp.desc()).limit(10)
    if excluded:
        feedback_query = feedback_query.where(Feedback.user_id.not_in(excluded))
    feedback = session.scalars(feedback_query).all()

    returning = sum(1 for days_ in user_days.values() if len(days_) >= 2)
    wau = active_since(timedelta(days=7))
    notes_pct, audio_pct = quotas.cloud_usage(session) if quotas else (None, None)
    return {
        "window_days": days,
        "generated_at": now.isoformat() + "Z",
        "users": {
            "total": len(user_days),
            "dau": active_since(timedelta(days=1)),
            "wau": wau,
            "mau": active_since(timedelta(days=30)),
            "new_in_window": sum(1 for ts in first_seen.values() if ts >= since),
            "returning": returning,
            "returning_rate": round(100 * returning / len(user_days), 1) if user_days else None,
        },
        "notes": {
            "total_done": session.scalar(scoped(select(func.count(Interaction.id)).where(status == NoteStatus.DONE))),
            "in_window": len(done),
            "failed_in_window": len(failed),
            "failure_rate": round(100 * len(failed) / (len(done) + len(failed)), 1) if (done or failed) else None,
            "audio_minutes_in_window": round(sum(r.audio_duration_sec or 0 for r in done) / 60, 1),
        },
        "time_saved": {
            # Listening time users didn't have to spend, and how much faster than real time notes were ready.
            "listening_minutes": round(sum(r.audio_duration_sec or 0 for r in done) / 60, 1),
            "waiting_minutes": round(sum(r.total_ms or 0 for r in done if r.audio_duration_sec) / 60000, 1),
            "median_speedup": percentile(speedups, 50),
        },
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "samples": len(latencies),
        },
        "channels": dict(Counter(r.channel or "web" for r in done)),
        "engines": dict(Counter(r.engine or "unknown" for r in done)),
        "sentiment": dict(Counter(r.sentiment or "Unknown" for r in done)),
        "ratings": {
            "thumbs_up": up,
            "thumbs_down": down,
            "accuracy_pct": round(100 * up / (up + down), 1) if (up + down) else None,
        },
        "retention": {
            "d1": retention(user_days, first_seen, now, 1),
            "d7": retention(user_days, first_seen, now, 7),
        },
        "engagement": engagement_stats(session, since, excluded, len(done)),
        "waitlist": waitlist_stats(session, since, excluded, len(user_days), wau),
        "capacity_today": {
            "notes_pct": round(notes_pct, 1) if notes_pct is not None else None,
            "audio_pct": round(audio_pct, 1) if audio_pct is not None else None,
        },
        "timeline": timeline,
        "recent": [
            {
                "created_at": n.timestamp.isoformat() + "Z" if n.timestamp else None,
                "user": mask_user(n.user_id),
                "channel": n.channel,
                "engine": n.engine,
                "status": n.effective_status,
                "title": n.title or (n.summary or "")[:80] or None,
                "total_ms": n.total_ms,
                "rating": n.accuracy_rating,
            }
            for n in recent
        ],
        "feedback": [
            {
                "created_at": f.timestamp.isoformat() + "Z" if f.timestamp else None,
                "user": mask_user(f.user_id),
                "message": f.message,
            }
            for f in feedback
        ],
    }


@router.get("/request-info")
async def request_info(request: Request, ctx: AppContext = Depends(get_ctx)):
    """Shows how this request reached the app, to set TRUSTED_PROXY_HOPS correctly after deploying:
    send a request with a fake `X-Forwarded-For: 1.2.3.4` and check derived_client_ip is your real IP."""
    hops = ctx.settings.trusted_proxy_hops
    return {
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
        "socket_peer": request.client.host if request.client else None,
        "trusted_proxy_hops": hops,
        "derived_client_ip": client_ip(request, hops),
    }


@router.get("/analytics")
async def analytics(days: int = Query(30, ge=1, le=365), ctx: AppContext = Depends(get_ctx)):
    return await ctx.db.run(compute_analytics, days, ctx.settings.analytics_excluded_user_ids, ctx.quotas)

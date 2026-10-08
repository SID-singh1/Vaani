"""Usage analytics for the admin dashboard. Every number is computed from real rows;
accounts listed in ANALYTICS_EXCLUDED_USER_IDS (your own testing) are left out."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..context import AppContext
from ..db.models import Feedback, Interaction, NoteStatus, utcnow
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


def compute_analytics(session: Session, days: int, excluded: list[str]) -> dict:
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
    return {
        "window_days": days,
        "generated_at": now.isoformat() + "Z",
        "users": {
            "total": len(user_days),
            "dau": active_since(timedelta(days=1)),
            "wau": active_since(timedelta(days=7)),
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
    return await ctx.db.run(compute_analytics, days, ctx.settings.analytics_excluded_user_ids)

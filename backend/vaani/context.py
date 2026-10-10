from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from .config import Settings
from .db.session import Database
from .engines.registry import EngineRegistry
from .jobs import JobManager
from .quotas import QuotaService
from .service import NoteService

if TYPE_CHECKING:
    from .channels.telegram import TelegramChannel
    from .channels.whatsapp import WhatsAppChannel


@dataclass
class AppContext:
    """Everything a request handler or channel needs, created once at startup."""

    settings: Settings
    db: Database
    http: httpx.AsyncClient
    registry: EngineRegistry
    jobs: JobManager
    quotas: QuotaService
    notes: NoteService
    telegram: TelegramChannel | None = None
    whatsapp: WhatsAppChannel | None = None
    started_at: Any = None

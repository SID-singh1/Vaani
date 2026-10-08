"""Application settings, read once from the environment (and a local .env in development)."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"

log = logging.getLogger(__name__)


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    env: str = "development"
    database_url: str = ""
    secret_key: str = ""
    admin_secret_key: str = ""
    public_base_url: str = ""
    allowed_origins: list[str] = field(default_factory=list)
    log_level: str = "INFO"
    log_transcripts: bool = False
    temp_dir: Path = REPO_ROOT / "ml" / "data" / "temp"
    models_dir: Path = REPO_ROOT / "models"

    # Cloud engine
    groq_api_key: str = ""
    gemini_api_key: str = ""
    groq_asr_model: str = "whisper-large-v3"
    groq_asr_language: str = ""
    gemini_models: list[str] = field(default_factory=list)
    groq_llm_models: list[str] = field(default_factory=list)
    cloud_concurrency: int = 3
    max_audio_seconds_cloud: int = 30 * 60

    # Private engine (self-hosted, runs fully on the server's CPU)
    private_engine_enabled: bool = False
    private_asr_backend: str = "faster-whisper"
    private_asr_model: str = "small"
    private_asr_language: str = "hi"
    private_llm_model_path: str = ""
    llama_server_url: str = ""
    llama_server_bin: str = ""
    private_llm_ctx: int = 8192
    private_concurrency: int = 1
    max_audio_seconds_private: int = 10 * 60

    default_engine: str = "cloud"

    # Limits that protect the free API tiers
    max_upload_mb: int = 20
    user_daily_note_limit: int = 20
    user_burst_limit: int = 5
    user_burst_window_minutes: int = 10
    global_daily_cloud_limit: int = 300
    unlimited_user_ids: list[str] = field(default_factory=list)
    ip_rate_limit: str = "30/minute"
    session_rate_limit: str = "10/minute"
    trusted_proxy_hops: int = 0  # 1 behind Render/most PaaS load balancers

    # Telegram
    telegram_bot_token: str = ""
    telegram_mode: str = "disabled"  # disabled | polling | webhook
    telegram_webhook_secret: str = ""

    # WhatsApp Cloud API
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_graph_version: str = "v25.0"
    whatsapp_daily_message_cap: int = 30

    analytics_excluded_user_ids: list[str] = field(default_factory=list)

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cloud_configured(self) -> bool:
        return bool(self.groq_api_key) and bool(self.gemini_api_key or self.groq_llm_models)

    @property
    def whatsapp_enabled(self) -> bool:
        return all(
            (
                self.whatsapp_access_token,
                self.whatsapp_phone_number_id,
                self.whatsapp_app_secret,
                self.whatsapp_verify_token,
            )
        )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def load_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env", override=False)

    env = _str("APP_ENV", "development").lower()
    secret_key = _str("SECRET_KEY")
    if not secret_key:
        if env == "production":
            raise RuntimeError("SECRET_KEY must be set in production (it signs web sessions).")
        # Development only: sessions stop validating after a restart, which is fine locally.
        secret_key = secrets.token_urlsafe(32)
        log.warning("SECRET_KEY is not set; using a random per-process key (development only).")

    telegram_token = _str("TELEGRAM_BOT_TOKEN")
    if telegram_token == "your_token_here":  # noqa: S105 - placeholder from old .env files
        telegram_token = ""
    telegram_mode = _str("TELEGRAM_MODE").lower()
    if not telegram_mode:
        telegram_mode = ("webhook" if env == "production" else "polling") if telegram_token else "disabled"

    # Render sets RENDER_EXTERNAL_URL automatically, so webhooks work without extra config there.
    public_base_url = (_str("PUBLIC_BASE_URL") or _str("RENDER_EXTERNAL_URL")).rstrip("/")

    database_url = _str("DATABASE_URL") or f"sqlite:///{(REPO_ROOT / 'vaani.db').as_posix()}"
    if database_url.startswith("postgres://"):  # Heroku/Supabase-style scheme SQLAlchemy rejects
        database_url = "postgresql://" + database_url[len("postgres://") :]

    return Settings(
        env=env,
        database_url=database_url,
        secret_key=secret_key,
        admin_secret_key=_str("ADMIN_SECRET_KEY"),
        public_base_url=public_base_url,
        allowed_origins=_list("ALLOWED_ORIGINS"),
        log_level=_str("LOG_LEVEL", "INFO").upper(),
        log_transcripts=_bool("LOG_TRANSCRIPTS", False),
        temp_dir=Path(_str("TEMP_DIR") or REPO_ROOT / "ml" / "data" / "temp"),
        models_dir=Path(_str("MODELS_DIR") or REPO_ROOT / "models"),
        groq_api_key=_str("GROQ_API_KEY"),
        gemini_api_key=_str("GEMINI_API_KEY"),
        groq_asr_model=_str("GROQ_ASR_MODEL", "whisper-large-v3"),
        groq_asr_language=_str("GROQ_ASR_LANGUAGE"),
        gemini_models=_list("GEMINI_MODELS", "gemini-2.5-flash,gemini-3.5-flash-lite"),
        groq_llm_models=_list("GROQ_LLM_MODELS", "openai/gpt-oss-120b"),
        cloud_concurrency=_int("CLOUD_CONCURRENCY", 3),
        max_audio_seconds_cloud=_int("MAX_AUDIO_SECONDS_CLOUD", 30 * 60),
        private_engine_enabled=_bool("PRIVATE_ENGINE_ENABLED", False),
        private_asr_backend=_str("PRIVATE_ASR_BACKEND", "faster-whisper").lower(),
        private_asr_model=_str("PRIVATE_ASR_MODEL", "small"),
        private_asr_language=_str("PRIVATE_ASR_LANGUAGE", "hi"),
        private_llm_model_path=_str("PRIVATE_LLM_MODEL_PATH"),
        llama_server_url=_str("LLAMA_SERVER_URL").rstrip("/"),
        llama_server_bin=_str("LLAMA_SERVER_BIN"),
        private_llm_ctx=_int("PRIVATE_LLM_CTX", 8192),
        private_concurrency=_int("PRIVATE_CONCURRENCY", 1),
        max_audio_seconds_private=_int("MAX_AUDIO_SECONDS_PRIVATE", 10 * 60),
        default_engine=_str("DEFAULT_ENGINE", "cloud").lower(),
        max_upload_mb=_int("MAX_UPLOAD_MB", 20),
        user_daily_note_limit=_int("USER_DAILY_NOTE_LIMIT", 20),
        user_burst_limit=_int("USER_BURST_LIMIT", 5),
        user_burst_window_minutes=_int("USER_BURST_WINDOW_MINUTES", 10),
        global_daily_cloud_limit=_int("GLOBAL_DAILY_CLOUD_LIMIT", 300),
        unlimited_user_ids=_list("UNLIMITED_USER_IDS"),
        ip_rate_limit=_str("IP_RATE_LIMIT", "30/minute"),
        session_rate_limit=_str("SESSION_RATE_LIMIT", "10/minute"),
        trusted_proxy_hops=_int("TRUSTED_PROXY_HOPS", 1 if env == "production" else 0),
        telegram_bot_token=telegram_token,
        telegram_mode=telegram_mode,
        telegram_webhook_secret=_str("TELEGRAM_WEBHOOK_SECRET"),
        whatsapp_access_token=_str("WHATSAPP_ACCESS_TOKEN"),
        whatsapp_phone_number_id=_str("WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_app_secret=_str("WHATSAPP_APP_SECRET"),
        whatsapp_verify_token=_str("WHATSAPP_VERIFY_TOKEN"),
        whatsapp_graph_version=_str("WHATSAPP_GRAPH_VERSION", "v25.0"),
        whatsapp_daily_message_cap=_int("WHATSAPP_DAILY_MESSAGE_CAP", 30),
        analytics_excluded_user_ids=_list("ANALYTICS_EXCLUDED_USER_IDS"),
    )

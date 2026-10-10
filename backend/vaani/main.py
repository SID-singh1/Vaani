"""FastAPI application factory and process lifecycle.

Run with:  uvicorn vaani.main:create_app --factory --app-dir backend
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .api import admin as admin_api
from .api import routes as api_routes
from .api.ratelimit import RateLimiter, client_ip, parse_limit
from .config import WEB_DIR, Settings, load_settings
from .context import AppContext
from .db.migrate import run_migrations
from .db.session import Database
from .engines.registry import EngineRegistry, build_registry
from .errors import VaaniError
from .jobs import JobManager
from .pipeline import NotePipeline
from .quotas import QuotaService
from .service import NoteService

log = logging.getLogger("vaani")

RegistryFactory = Callable[[Settings, httpx.AsyncClient], EngineRegistry]

CSP = (
    "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; img-src 'self' data:; "
    "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def setup_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    # httpx logs full request URLs at INFO, and Telegram bot URLs contain the bot token.
    for noisy in ("httpx", "httpcore", "telegram", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class BodySizeLimit:
    """Reject request bodies above a limit before they are read to disk."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._too_large(send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            response_started = response_started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await self._too_large(send)

    async def _too_large(self, send: Send) -> None:
        body = b'{"error":{"code":"file_too_large","message":"That upload is too large."}}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    pass


def create_app(settings: Settings | None = None, registry_factory: RegistryFactory | None = None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.temp_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.database_url)
        await asyncio.to_thread(run_migrations, db.engine)
        http = httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=50, max_keepalive_connections=20))
        registry = (registry_factory or build_registry)(settings, http)
        jobs = JobManager(db, NotePipeline(registry, log_transcripts=settings.log_transcripts))
        quotas = QuotaService(settings)
        ctx = AppContext(
            settings=settings,
            db=db,
            http=http,
            registry=registry,
            jobs=jobs,
            quotas=quotas,
            notes=NoteService(db, registry, jobs, quotas),
        )
        app.state.ctx = ctx
        await jobs.start()

        if settings.telegram_mode != "disabled" and settings.telegram_bot_token:
            from .channels.telegram import TelegramChannel

            ctx.telegram = TelegramChannel(ctx)
            await ctx.telegram.start()
        if settings.whatsapp_enabled:
            from .channels.whatsapp import WhatsAppChannel

            ctx.whatsapp = WhatsAppChannel(ctx)

        engines = ", ".join(e.name for e in registry.available()) or "none"
        log.info(
            "Vaani %s started (%s) | engines: %s | telegram: %s | whatsapp: %s",
            __version__,
            settings.env,
            engines,
            settings.telegram_mode if ctx.telegram else "off",
            "on" if ctx.whatsapp else "off",
        )
        try:
            yield
        finally:
            if ctx.telegram:
                await ctx.telegram.stop()
            if ctx.whatsapp:
                await ctx.whatsapp.close()
            await jobs.stop()
            await registry.close()
            await http.aclose()
            db.dispose()

    app = FastAPI(title="Vaani", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    app.state.upload_limiter = RateLimiter(*parse_limit(settings.ip_rate_limit))
    app.state.session_limiter = RateLimiter(*parse_limit(settings.session_rate_limit))

    @app.exception_handler(VaaniError)
    async def vaani_error(_: Request, exc: VaaniError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.user_message}}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            {"error": {"code": "bad_request", "message": "That request wasn't valid."}}, status_code=400
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": {"code": "internal_error", "message": VaaniError.user_message}}, status_code=500)

    @app.middleware("http")
    async def edge(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/v1/notes":
            try:
                request.app.state.upload_limiter.hit(client_ip(request, settings.trusted_proxy_hops))
            except VaaniError as exc:
                return JSONResponse({"error": {"code": exc.code, "message": exc.user_message}}, status_code=429)
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        return response

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_methods=["*"],
            allow_headers=["authorization", "content-type"],
        )
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_upload_bytes + 1024 * 1024)

    app.include_router(api_routes.router)
    app.include_router(admin_api.router)
    from .channels import telegram_routes, whatsapp_routes

    app.include_router(telegram_routes.router)
    app.include_router(whatsapp_routes.router)

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health(request: Request, deep: bool = False):
        """Liveness. With ?deep=1 it also runs a trivial database query: point an uptime monitor at
        that so free-tier Postgres (Supabase pauses idle projects) sees regular activity."""
        ctx: AppContext = request.app.state.ctx
        body = {
            "status": "ok",
            "version": __version__,
            "engines": [e.name for e in ctx.registry.available()],
            "telegram": settings.telegram_mode if ctx.telegram else "off",
            "whatsapp": bool(ctx.whatsapp),
        }
        if deep:
            try:
                await ctx.db.run(lambda session: session.execute(text("SELECT 1")))
                body["database"] = "ok"
            except Exception:
                log.exception("Database health check failed")
                return JSONResponse({**body, "status": "degraded", "database": "unreachable"}, status_code=503)
        return body

    WEB_DIR.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    for route, page in (("/", "index.html"), ("/admin", "admin.html"), ("/privacy", "privacy.html")):
        app.add_api_route(route, _page(page), methods=["GET", "HEAD"], include_in_schema=False)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml")

    return app


def _page(filename: str):
    async def serve():
        return FileResponse(WEB_DIR / filename)

    serve.__name__ = f"page_{filename.split('.')[0]}"
    return serve

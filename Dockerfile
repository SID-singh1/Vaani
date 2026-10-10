# Vaani API + web client + Telegram/WhatsApp channels.
#
#   docker build -t vaani .                              # cloud engine (fits a 512 MB free instance)
#   docker build -t vaani-private --build-arg PRIVATE=1 .  # adds on-device speech-to-text
#
# The private engine's LLM runs in a separate llama.cpp container; see docker-compose.yml.

FROM python:3.12-slim

ARG PRIVATE=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    PORT=8000 \
    TEMP_DIR=/tmp/vaani \
    MODELS_DIR=/models \
    DATABASE_URL=sqlite:////data/vaani.db

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements-private.txt backend/
RUN pip install -r backend/requirements.txt \
    && if [ "$PRIVATE" = "1" ]; then pip install -r backend/requirements-private.txt; fi

COPY backend/ backend/
COPY web/ web/

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 vaani \
    && mkdir -p /tmp/vaani /models /data \
    && chown -R vaani /tmp/vaani /models /data
USER vaani

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"

# One worker on purpose: the job queue and the Telegram bot live in this process.
CMD ["sh", "-c", "exec uvicorn vaani.main:create_app --factory --app-dir backend --host 0.0.0.0 --port ${PORT} --no-server-header --timeout-graceful-shutdown 20"]

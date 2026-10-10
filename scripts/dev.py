"""Run Vaani locally, safely.

Always uses a local SQLite database (never the DATABASE_URL in .env, which may be
production) and keeps Telegram off unless --telegram-polling is given (polling with the
production bot token would steal the live bot's messages).

    python scripts/dev.py                      # http://127.0.0.1:8000
    python scripts/dev.py --private            # also enable the private engine (local models)
    python scripts/dev.py --telegram-polling   # use a separate *test* bot token for this!
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(ROOT / "dev.db"), help="SQLite file for local data")
    parser.add_argument("--admin-key", help="admin dashboard key for this run")
    parser.add_argument("--private", action="store_true", help="enable the private (on-device) engine")
    parser.add_argument("--telegram-polling", action="store_true")
    args = parser.parse_args()

    os.environ["APP_ENV"] = "development"
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(args.db).as_posix()}"
    os.environ["TELEGRAM_MODE"] = "polling" if args.telegram_polling else "disabled"
    os.environ.pop("PUBLIC_BASE_URL", None)
    if args.admin_key:
        os.environ["ADMIN_SECRET_KEY"] = args.admin_key
    if args.private:
        os.environ["PRIVATE_ENGINE_ENABLED"] = "true"
        models = ROOT / "models"
        exe = "llama-server.exe" if os.name == "nt" else "llama-server"
        os.environ.setdefault("LLAMA_SERVER_BIN", str(ROOT / "ml" / exe))
        os.environ.setdefault("PRIVATE_LLM_MODEL_PATH", str(models / "phi-3-mini-q4_k_m.gguf"))

    sys.path.insert(0, str(ROOT / "backend"))
    import uvicorn

    uvicorn.run("vaani.main:create_app", factory=True, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

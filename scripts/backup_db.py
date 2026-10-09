"""Back up the database to a JSON file (read-only: nothing is modified).

Uses DATABASE_URL from the environment or .env, i.e. your production database if that's
what .env points at. Run it before deploying a version with new migrations:

    python scripts/backup_db.py                 # -> backups/vaani-<timestamp>.json

The file contains users' transcripts: keep it private (backups/ is git-ignored) and delete
it once you no longer need it.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, select

ROOT = Path(__file__).resolve().parents[1]


def _jsonable(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def main() -> None:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit("DATABASE_URL is not set (in the environment or .env).")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    engine = create_engine(url)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    dump = {"created_at": datetime.now(UTC).isoformat(), "tables": {}}
    with engine.connect() as conn:
        for name, table in metadata.tables.items():
            rows = [{k: _jsonable(v) for k, v in row._mapping.items()} for row in conn.execute(select(table))]
            dump["tables"][name] = rows
            print(f"  {name}: {len(rows)} rows")

    out_dir = ROOT / "backups"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"vaani-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Backup written to {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

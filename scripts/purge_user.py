"""Permanently delete everything stored for specific user ids (e.g. synthetic test traffic).

Dry run by default; add --yes to actually delete. Uses DATABASE_URL from the environment
or .env. Back up first (scripts/backup_db.py).

    python scripts/purge_user.py --user-id tg_resume_pumper_999            # shows what would go
    python scripts/purge_user.py --user-id tg_resume_pumper_999 --yes      # deletes it
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
TABLES = ("interactions", "feedbacks", "users")  # children first (foreign keys)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-id", action="append", required=True)
    parser.add_argument("--yes", action="store_true", help="actually delete")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit("DATABASE_URL is not set (in the environment or .env).")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    engine = create_engine(url)
    existing = set(inspect(engine).get_table_names())

    with engine.begin() as conn:
        for table in TABLES:
            if table not in existing:
                continue
            column = "id" if table == "users" else "user_id"
            for user_id in args.user_id:
                count = conn.execute(text(f"SELECT count(*) FROM {table} WHERE {column} = :u"), {"u": user_id}).scalar()  # noqa: S608
                print(f"{table:13s} {user_id}: {count} row(s)")
                if args.yes and count:
                    conn.execute(text(f"DELETE FROM {table} WHERE {column} = :u"), {"u": user_id})  # noqa: S608
    print("Deleted." if args.yes else "Dry run only. Re-run with --yes to delete.")


if __name__ == "__main__":
    main()

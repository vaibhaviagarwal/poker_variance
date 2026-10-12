"""Load the WSOP export into Postgres and build every derived table.

    python src/load.py

Connection comes from DATABASE_URL, defaulting to a local database named
"poker" over the Unix socket — which is what `createdb poker` gives you
after `brew install postgresql@16`.

The raw CSVs are streamed in with COPY rather than INSERTed row by row:
COPY is Postgres's bulk path and loads 700k rows in seconds instead of
minutes. Everything after that is SQL, run in file-name order, so the
whole database can be rebuilt from scratch with one command.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
RAW = ROOT / "data" / "raw" / "unpacked" / "data" / "wsop.com"

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///poker")

RAW_FILES = {
    "raw.wsop_results": RAW / "results.csv",
    "raw.wsop_payouts": RAW / "payouts.csv",
    "raw.wsop_metadata": RAW / "metadata.csv",
}


def run_sql(conn: psycopg.Connection, path: Path) -> None:
    t0 = time.time()
    conn.execute(path.read_text())
    print(f"  {path.name:<22} {time.time() - t0:5.1f}s")


def copy_csv(conn: psycopg.Connection, table: str, path: Path) -> int:
    if not path.exists():
        sys.exit(f"missing {path} — unzip the dataset into data/raw/unpacked first")
    t0 = time.time()
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
            with open(path, "rb") as fh:
                while chunk := fh.read(1 << 20):
                    cp.write(chunk)
        n = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    print(f"  {table:<22} {n:>9,} rows  {time.time() - t0:5.1f}s")
    return n


def main() -> None:
    print(f"connecting to {DATABASE_URL}")
    with psycopg.connect(DATABASE_URL) as conn:
        print("schema")
        run_sql(conn, SQL_DIR / "01_schema.sql")

        print("raw load")
        raw_rows = copy_csv(conn, "raw.wsop_results", RAW_FILES["raw.wsop_results"])
        copy_csv(conn, "raw.wsop_payouts", RAW_FILES["raw.wsop_payouts"])
        copy_csv(conn, "raw.wsop_metadata", RAW_FILES["raw.wsop_metadata"])

        print("transform + analysis views")
        run_sql(conn, SQL_DIR / "02_transform.sql")
        run_sql(conn, SQL_DIR / "03_analysis.sql")
        for extra in sorted(SQL_DIR.glob("0[4-9]_*.sql")):
            run_sql(conn, extra)

        q = lambda s: conn.execute(s).fetchone()[0]
        kept = q("SELECT count(*) FROM core.results")
        print()
        print(f"series   {q('SELECT count(*) FROM core.series'):>9,}")
        print(f"events   {q('SELECT count(*) FROM core.events'):>9,}")
        print(f"players  {q('SELECT count(*) FROM core.players'):>9,}")
        print(f"results  {kept:>9,}   ({raw_rows - kept:,} raw rows dropped: "
              "unparseable payout, blank name, or exact duplicate)")
        print()
        print("event filter:")
        for outcome, events, cashes in conn.execute(
            "SELECT * FROM analysis.exclusion_reasons"
        ):
            print(f"  {outcome:<26} {events:>6,} events  {cashes:>9,} cashes")


if __name__ == "__main__":
    main()

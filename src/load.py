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
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
DEFAULT_RAW = ROOT / "data" / "raw" / "unpacked" / "data" / "wsop.com"

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///poker")
RAW_DIR = Path(os.environ.get("POKER_RAW_DIR", DEFAULT_RAW))

# table -> file name inside the raw directory
RAW_FILES = {
    "raw.wsop_results": "results.csv",
    "raw.wsop_payouts": "payouts.csv",
    "raw.wsop_metadata": "metadata.csv",
}


def run_sql(conn: psycopg.Connection, path: Path) -> None:
    t0 = time.time()
    conn.execute(path.read_text())
    print(f"  {path.name:<22} {time.time() - t0:5.1f}s")


def copy_csv(conn: psycopg.Connection, table: str, path: Path) -> int:
    if not path.exists():
        raise FileNotFoundError(
            f"missing {path} — unzip the dataset into data/raw/unpacked first"
        )
    t0 = time.time()
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
            with open(path, "rb") as fh:
                while chunk := fh.read(1 << 20):
                    cp.write(chunk)
        n = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    print(f"  {table:<22} {n:>9,} rows  {time.time() - t0:5.1f}s")
    return n


def build(conn: psycopg.Connection, raw_dir: Path) -> int:
    """Rebuild every schema from the CSVs in raw_dir. Returns raw row count.

    Separate from main() so the tests can build a small fixture database
    with exactly the same code path the real load uses.
    """
    print("schema")
    run_sql(conn, SQL_DIR / "01_schema.sql")

    print("raw load")
    counts = {t: copy_csv(conn, t, raw_dir / f) for t, f in RAW_FILES.items()}

    print("transform + analysis views")
    run_sql(conn, SQL_DIR / "02_transform.sql")
    run_sql(conn, SQL_DIR / "03_analysis.sql")
    for extra in sorted(SQL_DIR.glob("0[4-9]_*.sql")):
        run_sql(conn, extra)
    return counts["raw.wsop_results"]


def main() -> None:
    print(f"connecting to {DATABASE_URL}")
    with psycopg.connect(DATABASE_URL) as conn:
        raw_rows = build(conn, RAW_DIR)

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

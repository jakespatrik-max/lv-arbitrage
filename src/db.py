"""
SQLite databázová vrstva.

Použití:
    python -m src.db init           # vytvoří/migruje schema
    python -m src.db stats          # vypíše počty záznamů
"""

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

# DB cesta:
# - lokálně: ./data/listings.db
# - Railway: /data/listings.db (persistent volume mount)
# Override: env var DB_PATH
_default_path = Path(__file__).parent.parent / "data" / "listings.db"
DB_PATH = Path(os.environ.get("DB_PATH", str(_default_path)))

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    description TEXT,
    price_czk INTEGER,
    price_original INTEGER,
    currency TEXT,
    model_normalized TEXT,
    model_raw TEXT,
    condition_score REAL,
    condition_raw TEXT,
    photo_urls TEXT,
    location TEXT,
    posted_at TEXT,
    scraped_at TEXT NOT NULL,
    fair_value_czk INTEGER,
    arbitrage_pct REAL,
    counterfeit_risk REAL,
    is_active INTEGER DEFAULT 1,
    is_favorite INTEGER DEFAULT 0,
    is_dismissed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    price_czk INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY(listing_id) REFERENCES listings(id)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    listings_found INTEGER DEFAULT 0,
    listings_new INTEGER DEFAULT 0,
    listings_updated INTEGER DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_model_active
    ON listings(model_normalized, is_active);
CREATE INDEX IF NOT EXISTS idx_listings_arbitrage
    ON listings(arbitrage_pct DESC) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_listings_source
    ON listings(source, source_id);
CREATE INDEX IF NOT EXISTS idx_price_history_listing
    ON price_history(listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_favorite
    ON listings(is_favorite) WHERE is_favorite = 1;
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Context manager pro DB connection. Auto commit při úspěchu, rollback při chybě."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Vytvoří/migruje schema. Bezpečné spustit opakovaně."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    print(f"DB inicializována: {DB_PATH}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_listing(conn: sqlite3.Connection, listing: dict[str, Any]) -> str:
    """
    Vloží nebo aktualizuje inzerát. Klíč = url.

    Vrací: 'new' | 'updated' | 'unchanged'

    Pokud cena změněna, zaznamenává do price_history.
    """
    listing = {**listing}
    if isinstance(listing.get("photo_urls"), list):
        listing["photo_urls"] = json.dumps(listing["photo_urls"])
    listing["scraped_at"] = listing.get("scraped_at") or now_iso()

    existing = conn.execute(
        "SELECT id, price_czk FROM listings WHERE url = ?", (listing["url"],)
    ).fetchone()

    if existing is None:
        cols = ", ".join(listing.keys())
        placeholders = ", ".join(["?"] * len(listing))
        conn.execute(
            f"INSERT INTO listings ({cols}) VALUES ({placeholders})",
            list(listing.values()),
        )
        return "new"

    listing_id = existing["id"]
    old_price = existing["price_czk"]
    new_price = listing.get("price_czk")

    set_clause = ", ".join([f"{k} = ?" for k in listing.keys()])
    conn.execute(
        f"UPDATE listings SET {set_clause} WHERE id = ?",
        [*listing.values(), listing_id],
    )

    if new_price is not None and new_price != old_price:
        conn.execute(
            "INSERT INTO price_history (listing_id, price_czk, observed_at) VALUES (?, ?, ?)",
            (listing_id, new_price, now_iso()),
        )
        return "updated"

    return "unchanged"


def mark_inactive_if_missing(
    conn: sqlite3.Connection, source: str, seen_urls: set[str]
) -> int:
    """Označí jako neaktivní inzeráty z daného zdroje, které nebyly v aktuálním scrape runu."""
    placeholders = ",".join(["?"] * len(seen_urls)) if seen_urls else "''"
    cursor = conn.execute(
        f"""
        UPDATE listings
        SET is_active = 0
        WHERE source = ?
          AND is_active = 1
          AND url NOT IN ({placeholders})
        """,
        [source, *seen_urls],
    )
    return cursor.rowcount


def stats() -> None:
    """Vypíše rychlé statistiky DB."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM listings").fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM listings WHERE is_active = 1"
        ).fetchone()["c"]
        by_source = conn.execute(
            """
            SELECT source, COUNT(*) AS c
            FROM listings WHERE is_active = 1
            GROUP BY source ORDER BY c DESC
            """
        ).fetchall()
        by_model = conn.execute(
            """
            SELECT model_normalized, COUNT(*) AS c, AVG(price_czk) AS avg_price
            FROM listings WHERE is_active = 1 AND model_normalized IS NOT NULL
            GROUP BY model_normalized ORDER BY c DESC
            """
        ).fetchall()

        print(f"\n=== DB STATS ===")
        print(f"Total listings: {total}")
        print(f"Active listings: {active}")
        print(f"\nBy source:")
        for row in by_source:
            print(f"  {row['source']:20s} {row['c']}")
        print(f"\nBy model (active):")
        for row in by_model:
            avg = row["avg_price"] or 0
            print(f"  {row['model_normalized']:20s} count={row['c']:4d}  avg={avg:>8.0f} CZK")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "init"
    if cmd == "init":
        init_db()
    elif cmd == "stats":
        stats()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python -m src.db [init|stats]")
        sys.exit(1)

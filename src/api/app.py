"""
FastAPI backend.

- Servíruje JSON API na /api/*
- Servíruje statický frontend na / (z ./static)
- Health check na /health (pro Railway)

Lokálně:
    uvicorn src.api.app:app --reload --port 8000

Railway: viz Procfile / railway.toml
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import MAX_COUNTERFEIT_RISK_FOR_ALERT, MODELS, SOURCES
from src.db import get_conn, init_db

logger = logging.getLogger("api")

# Cesta ke statickému frontendu (relativně od kořene projektu)
STATIC_DIR = Path(__file__).parent.parent.parent / "static"

# Den-in-app schedule: protože Railway shared-volume mezi services není
# k dispozici, refresh běží v procesu webu místo samostatné cron service.
# Vypnout: SCHEDULE_REFRESH=0 v env.
_SCHEDULE_REFRESH = os.environ.get("SCHEDULE_REFRESH", "1") != "0"
_REFRESH_HOUR_UTC = int(os.environ.get("REFRESH_HOUR_UTC", "6"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB schema; spustit denní refresh scheduler."""
    init_db()
    logger.info("DB schema initialized")

    scheduler = None
    if _SCHEDULE_REFRESH:
        try:
            scheduler = _start_refresh_scheduler()
            logger.info(
                "Scheduled daily refresh at %02d:00 UTC", _REFRESH_HOUR_UTC
            )
        except Exception as e:
            logger.warning("Refresh scheduler failed to start: %s", e)
    yield
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass


def _start_refresh_scheduler():
    """Lazy-import APScheduler ať není require na test/dev když není v env."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    def _run_refresh():
        from src.cli import cmd_refresh
        try:
            logger.info("Scheduled refresh: starting")
            cmd_refresh()
            logger.info("Scheduled refresh: done")
        except Exception as exc:
            logger.exception("Scheduled refresh failed: %s", exc)

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_refresh,
        CronTrigger(hour=_REFRESH_HOUR_UTC, minute=0),
        id="daily_refresh",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.start()
    return scheduler


app = FastAPI(title="LV Arbitrage", version="0.1.0", lifespan=lifespan)

# CORS pro případné samostatné frontendy / Telegram bot serverless funkce
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ============================================================================
# Health & metadata
# ============================================================================
@app.get("/health")
def health() -> dict:
    """Health check pro Railway."""
    try:
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM listings").fetchone()["c"]
        return {"status": "ok", "listings": count}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB not ready: {e}")


@app.get("/api/meta")
def meta() -> dict:
    """Metadata pro frontend — modely, zdroje, stupnice stavu."""
    return {
        "models": [
            {
                "id": m.normalized_id,
                "display_name": m.display_name,
                "retail_eur": m.retail_eur,
                "retail_czk": m.retail_czk,
                "canvas_variants": m.canvas_variants,
            }
            for m in MODELS
        ],
        "sources": [
            {"id": sid, "type": cfg["type"], "base_url": cfg["base_url"]}
            for sid, cfg in SOURCES.items()
        ],
        "default_max_counterfeit_risk": MAX_COUNTERFEIT_RISK_FOR_ALERT,
    }


# ============================================================================
# Listings API
# ============================================================================
def _row_to_listing(row) -> dict:
    """Převede DB row na JSON-friendly dict."""
    d = dict(row)
    if d.get("photo_urls"):
        try:
            d["photo_urls"] = json.loads(d["photo_urls"])
        except (json.JSONDecodeError, TypeError):
            d["photo_urls"] = []
    else:
        d["photo_urls"] = []
    return d


@app.get("/api/listings")
def listings(
    model: Optional[str] = None,
    source: Optional[str] = None,
    min_arbitrage_pct: float = 0.0,
    max_counterfeit_risk: float = 1.0,
    sort: str = "arbitrage_desc",
    limit: int = 200,
    offset: int = 0,
    active_only: bool = True,
    favorites_only: bool = False,
    exclude_dismissed: bool = True,
) -> dict:
    """
    Seznam inzerátů s filtry.

    Sort options: arbitrage_desc | price_asc | price_desc | newest | oldest
    """
    where = []
    params: list = []

    if active_only:
        where.append("is_active = 1")
    if model:
        where.append("model_normalized = ?")
        params.append(model)
    if source:
        where.append("source = ?")
        params.append(source)
    if min_arbitrage_pct > 0:
        where.append("arbitrage_pct >= ?")
        params.append(min_arbitrage_pct)
    if max_counterfeit_risk < 1.0:
        where.append("(counterfeit_risk IS NULL OR counterfeit_risk <= ?)")
        params.append(max_counterfeit_risk)
    if favorites_only:
        where.append("is_favorite = 1")
    if exclude_dismissed:
        where.append("is_dismissed = 0")

    where_clause = " AND ".join(where) if where else "1=1"

    sort_sql = {
        "arbitrage_desc": "arbitrage_pct DESC NULLS LAST",
        "price_asc": "price_czk ASC",
        "price_desc": "price_czk DESC",
        "newest": "scraped_at DESC",
        "oldest": "scraped_at ASC",
    }.get(sort, "arbitrage_pct DESC NULLS LAST")

    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM listings WHERE {where_clause}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT * FROM listings
            WHERE {where_clause}
            ORDER BY {sort_sql}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_row_to_listing(r) for r in rows],
    }


@app.get("/api/listings/{listing_id}")
def listing_detail(listing_id: int) -> dict:
    """Detail jednoho inzerátu + historie cen."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Listing not found")

        history = conn.execute(
            """
            SELECT price_czk, observed_at FROM price_history
            WHERE listing_id = ? ORDER BY observed_at
            """,
            (listing_id,),
        ).fetchall()

    return {
        "listing": _row_to_listing(row),
        "price_history": [dict(h) for h in history],
    }


@app.post("/api/listings/{listing_id}/favorite")
def toggle_favorite(listing_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_favorite FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404)
        new_val = 0 if row["is_favorite"] else 1
        conn.execute(
            "UPDATE listings SET is_favorite = ? WHERE id = ?", (new_val, listing_id)
        )
    return {"is_favorite": bool(new_val)}


@app.post("/api/listings/{listing_id}/dismiss")
def toggle_dismiss(listing_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_dismissed FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404)
        new_val = 0 if row["is_dismissed"] else 1
        conn.execute(
            "UPDATE listings SET is_dismissed = ? WHERE id = ?", (new_val, listing_id)
        )
    return {"is_dismissed": bool(new_val)}


# ============================================================================
# Stats / market overview API
# ============================================================================
@app.get("/api/stats")
def stats() -> dict:
    """Souhrn pro frontend — počty per model, fair values, last scrape."""
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM listings WHERE is_active = 1"
        ).fetchone()["c"]

        opportunities = conn.execute(
            """
            SELECT COUNT(*) AS c FROM listings
            WHERE is_active = 1
              AND is_dismissed = 0
              AND arbitrage_pct >= 0.20
              AND (counterfeit_risk IS NULL OR counterfeit_risk <= 0.55)
            """
        ).fetchone()["c"]

        per_model = conn.execute(
            """
            SELECT
                model_normalized,
                COUNT(*) AS count,
                AVG(price_czk) AS avg_price,
                MIN(price_czk) AS min_price,
                MAX(price_czk) AS max_price
            FROM listings
            WHERE is_active = 1 AND model_normalized IS NOT NULL
            GROUP BY model_normalized
            """
        ).fetchall()

        per_source = conn.execute(
            """
            SELECT source, COUNT(*) AS count
            FROM listings WHERE is_active = 1
            GROUP BY source ORDER BY count DESC
            """
        ).fetchall()

        last_run = conn.execute(
            """
            SELECT source, finished_at, listings_found, listings_new
            FROM scrape_runs WHERE finished_at IS NOT NULL
            ORDER BY finished_at DESC LIMIT 10
            """
        ).fetchall()

    return {
        "total_active": total,
        "opportunities_count": opportunities,
        "per_model": [dict(r) for r in per_model],
        "per_source": [dict(r) for r in per_source],
        "recent_runs": [dict(r) for r in last_run],
    }


# ============================================================================
# Static frontend (musí být POSLEDNÍ kvůli catch-all route na /)
# ============================================================================
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

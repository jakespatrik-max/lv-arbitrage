"""Testy pro fair value + arbitrage skóre. Používá izolovanou in-memory DB."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(monkeypatch):
    """Nastav DB_PATH na čerstvou temp DB pro každý test."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)

    # Re-import db modul s upraveným DB_PATH
    monkeypatch.setenv("DB_PATH", str(db_path))
    import importlib

    import src.db as db_module
    importlib.reload(db_module)
    db_module.init_db()

    # Pricing & dependencies používají src.db.get_conn — taky reload
    import src.analysis.pricing as pricing_module
    importlib.reload(pricing_module)

    yield db_module, pricing_module

    db_path.unlink(missing_ok=True)
    # Reset env
    monkeypatch.delenv("DB_PATH", raising=False)
    importlib.reload(db_module)


def _insert_listing(conn, **kwargs):
    defaults = {
        "source": "luxurybags",
        "source_id": "x",
        "url": "https://example.com/" + kwargs.get("source_id", "x"),
        "title": "X",
        "description": "",
        "price_czk": 10000,
        "price_original": 10000,
        "currency": "CZK",
        "model_normalized": "alma_bb",
        "model_raw": "Alma BB",
        "condition_score": 0.85,
        "condition_raw": "excellent",
        "photo_urls": "[]",
        "location": None,
        "posted_at": None,
        "scraped_at": "2026-05-09T00:00:00",
        "is_active": 1,
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    conn.execute(
        f"INSERT INTO listings ({cols}) VALUES ({placeholders})",
        list(defaults.values()),
    )


def test_compute_fair_value_needs_min_3_samples(isolated_db):
    db, pricing = isolated_db
    with db.get_conn() as conn:
        _insert_listing(conn, source_id="a", source="luxurybags", price_czk=20000)
        _insert_listing(conn, source_id="b", source="armadio", price_czk=22000)
    assert pricing.compute_fair_value("alma_bb") is None  # < 3 samples


def test_compute_fair_value_median_across_pro_shops(isolated_db):
    db, pricing = isolated_db
    with db.get_conn() as conn:
        _insert_listing(conn, source_id="a", source="luxurybags", price_czk=20000)
        _insert_listing(conn, source_id="b", source="armadio", price_czk=22000)
        _insert_listing(conn, source_id="c", source="luxurybags", price_czk=24000)
    assert pricing.compute_fair_value("alma_bb") == 22000  # median


def test_c2c_listings_excluded_from_fair_value(isolated_db):
    db, pricing = isolated_db
    with db.get_conn() as conn:
        _insert_listing(conn, source_id="cz1", source="bazos_cz", price_czk=5000)
        _insert_listing(conn, source_id="cz2", source="bazos_cz", price_czk=6000)
        _insert_listing(conn, source_id="cz3", source="bazos_cz", price_czk=7000)
    # Žádné profi data → None
    assert pricing.compute_fair_value("alma_bb") is None


def test_inactive_listings_excluded(isolated_db):
    db, pricing = isolated_db
    with db.get_conn() as conn:
        _insert_listing(conn, source_id="a", source="luxurybags", price_czk=20000)
        _insert_listing(conn, source_id="b", source="luxurybags", price_czk=22000)
        _insert_listing(conn, source_id="c", source="armadio", price_czk=24000, is_active=0)
    assert pricing.compute_fair_value("alma_bb") is None  # jen 2 aktivní


def test_arbitrage_pct_for_underpriced_listing(isolated_db):
    db, pricing = isolated_db
    with db.get_conn() as conn:
        # Tři profi listingy → fair = 22000
        _insert_listing(conn, source_id="p1", source="luxurybags", price_czk=20000)
        _insert_listing(conn, source_id="p2", source="armadio", price_czk=22000)
        _insert_listing(conn, source_id="p3", source="luxurybags", price_czk=24000)
        # C2C listing s podhodnocenou cenou v dobrém stavu (excellent → mult 0.92)
        _insert_listing(
            conn, source_id="c2c1", source="bazos_cz", price_czk=10000,
            condition_score=0.85,
        )
    n = pricing.update_all_arbitrage_scores()
    assert n >= 1

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT fair_value_czk, arbitrage_pct FROM listings WHERE source_id = 'c2c1'"
        ).fetchone()
    assert row["fair_value_czk"] == 22000
    # adjusted = 22000 * 0.92 = 20240; arb = (20240 - 10000) / 20240 ≈ 0.506
    assert row["arbitrage_pct"] == pytest.approx((20240 - 10000) / 20240, rel=1e-3)


def test_label_from_score_boundaries():
    from src.analysis.pricing import _label_from_score
    assert _label_from_score(1.00) == "new"
    assert _label_from_score(0.95) == "like_new"
    assert _label_from_score(0.85) == "excellent"
    assert _label_from_score(0.75) == "very_good"
    assert _label_from_score(0.60) == "good"
    assert _label_from_score(0.40) == "fair"
    assert _label_from_score(0.20) == "poor"
    assert _label_from_score(None) == "good"

"""
Fair value + arbitrage skóre.

`compute_fair_value(model_id)`:
  - Median ceny aktivních inzerátů z profi obchodů (`luxurybags`, `armadio`)
    pro daný model.
  - Pokud je vzorek < 3, vrátí None (nedostatek dat).

`update_all_arbitrage_scores()`:
  - Pro každý aktivní inzerát s `model_normalized != NULL`:
      adjusted_fair = fair_value × CONDITION_MULTIPLIERS[condition_label]
      arbitrage_pct = (adjusted_fair − price_czk) / adjusted_fair
"""

from __future__ import annotations

import logging
from statistics import median
from typing import Optional

from src.config import CONDITION_MULTIPLIERS, MODEL_BY_ID
from src.db import get_conn

logger = logging.getLogger(__name__)

REFERENCE_SOURCES = ("luxurybags", "armadio")
MIN_SAMPLE_SIZE = 3
DEFAULT_CONDITION_MULTIPLIER = CONDITION_MULTIPLIERS["good"]


def _label_from_score(score: Optional[float]) -> str:
    """Inverse map: condition_score (0..1) -> nejbližší label v CONDITION_MULTIPLIERS."""
    if score is None:
        return "good"
    # CONDITION_MULTIPLIERS jsou vůči "excellent" baseline; pro mapování
    # zpět na label volíme score-thresholdy korespondující se score_condition.
    if score >= 1.0:
        return "new"
    if score >= 0.95:
        return "like_new"
    if score >= 0.85:
        return "excellent"
    if score >= 0.75:
        return "very_good"
    if score >= 0.55:
        return "good"
    if score >= 0.35:
        return "fair"
    return "poor"


def compute_fair_value(model_id: str) -> Optional[int]:
    """Median z aktivních listings z profi obchodů. None pokud vzorek < 3."""
    if model_id not in MODEL_BY_ID:
        return None
    placeholders = ",".join("?" for _ in REFERENCE_SOURCES)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT price_czk
            FROM listings
            WHERE model_normalized = ?
              AND is_active = 1
              AND price_czk IS NOT NULL
              AND price_czk > 0
              AND source IN ({placeholders})
            """,
            (model_id, *REFERENCE_SOURCES),
        ).fetchall()
    prices = [r["price_czk"] for r in rows]
    if len(prices) < MIN_SAMPLE_SIZE:
        return None
    return int(round(median(prices)))


def update_all_arbitrage_scores() -> int:
    """
    Pro každý aktivní listing s model_normalized:
      - fair_value_czk = compute_fair_value(model)
      - adjusted_fair = fair * CONDITION_MULTIPLIERS[label]
      - arbitrage_pct = (adjusted - price_czk) / adjusted
    Vrací počet aktualizovaných řádků.
    """
    fair_value_cache: dict[str, Optional[int]] = {}
    for model_id in MODEL_BY_ID:
        fair_value_cache[model_id] = compute_fair_value(model_id)

    updated = 0
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, model_normalized, condition_score, price_czk
            FROM listings
            WHERE is_active = 1
              AND model_normalized IS NOT NULL
              AND price_czk IS NOT NULL
              AND price_czk > 0
            """
        ).fetchall()

        for row in rows:
            fair = fair_value_cache.get(row["model_normalized"])
            if fair is None:
                # Nedostatek dat — vyčistit existující skóre, ať dashboard nelže
                conn.execute(
                    """
                    UPDATE listings
                    SET fair_value_czk = NULL, arbitrage_pct = NULL
                    WHERE id = ?
                    """,
                    (row["id"],),
                )
                continue

            label = _label_from_score(row["condition_score"])
            multiplier = CONDITION_MULTIPLIERS.get(label, DEFAULT_CONDITION_MULTIPLIER)
            adjusted_fair = fair * multiplier
            if adjusted_fair <= 0:
                continue

            arbitrage_pct = (adjusted_fair - row["price_czk"]) / adjusted_fair

            conn.execute(
                """
                UPDATE listings
                SET fair_value_czk = ?, arbitrage_pct = ?
                WHERE id = ?
                """,
                (fair, arbitrage_pct, row["id"]),
            )
            updated += 1

    return updated

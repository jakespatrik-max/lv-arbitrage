"""
Normalizace modelu — mapuje volný text z titulku/popisu na `model_normalized`
hodnotu z `config.MODELS` přes `search_terms`.

Algoritmus:
1. Lowercase obě strany; odstraň diakritiku, ať `Métis` matchne `metis`.
2. Word-boundary regex (\\b) ať `balmain` nepotopí `alma`.
3. Specifičtější varianty se kontrolují DŘÍVE než obecné — tj. modely
   s delším názvem search termu se zkouší první (`alma bb` před `alma`).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

from src.config import MODELS, Model
from src.db import get_conn

logger = logging.getLogger(__name__)


def _strip_diacritics(text: str) -> str:
    """Odstraň českou/slovenskou diakritiku pro robustní porovnání."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    return _strip_diacritics((text or "").lower())


def _build_term_index() -> list[tuple[str, str, re.Pattern[str]]]:
    """
    Připrav seřazený seznam (model_id, term_normalized, regex) — nejdelší termy
    první, aby specifické varianty matchly před obecnými.
    """
    entries: list[tuple[str, str, re.Pattern[str]]] = []
    for model in MODELS:
        for term in model.search_terms:
            term_norm = _normalize(term)
            # Word-boundary regex; spojovník/mezera se ignoruje
            pattern_src = re.escape(term_norm).replace(r"\ ", r"[\s\-]+")
            pattern = re.compile(rf"(?<![a-z0-9]){pattern_src}(?![a-z0-9])")
            entries.append((model.normalized_id, term_norm, pattern))
    # Seřaď: nejdelší term první (ať `alma bb` přebije `alma`)
    entries.sort(key=lambda e: -len(e[1]))
    return entries


_TERM_INDEX: list[tuple[str, str, re.Pattern[str]]] = _build_term_index()


def classify_model(title: Optional[str], description: Optional[str]) -> Optional[str]:
    """
    Vrátí `model_normalized` (např. "alma_bb") nebo None pokud žádný
    sledovaný model nematchne.

    Title se kontroluje DŘÍVE než description — pokud někdo prodává
    "Speedy 25" a v popisu zmíní "podobná Almě BB", chceme `speedy_25`.
    """
    haystack_title = _normalize(title or "")
    haystack_desc = _normalize(description or "")

    if haystack_title:
        for model_id, _term, pattern in _TERM_INDEX:
            if pattern.search(haystack_title):
                return model_id

    if haystack_desc:
        for model_id, _term, pattern in _TERM_INDEX:
            if pattern.search(haystack_desc):
                return model_id

    return None


def classify_all_listings() -> int:
    """
    Projde všechny listings WHERE model_normalized IS NULL a klasifikuje je.
    Vrací počet aktualizovaných řádků.
    """
    updated = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, description FROM listings WHERE model_normalized IS NULL"
        ).fetchall()
        for row in rows:
            model_id = classify_model(row["title"], row["description"])
            if model_id is None:
                continue
            conn.execute(
                "UPDATE listings SET model_normalized = ? WHERE id = ?",
                (model_id, row["id"]),
            )
            updated += 1
    return updated

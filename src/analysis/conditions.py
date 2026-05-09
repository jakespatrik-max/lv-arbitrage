"""
Skórování stavu (condition) inzerátu.

Vstup: volný text (CS/SK), případně pre-normalizovaný `condition_raw`
přímo ze scraperu.

Výstup: (label, score) — label je jeden z `CONDITION_MULTIPLIERS` v configu,
score je číslo 0–1 použité pro relative ranking (multiplikátor cen je v configu).

Klíčová slova jsou seřazena tak, aby SPECIFICKÁ pravidla byla zkoušena
před obecnými ("jako nová" před "nová", "perfektní stav" před "stav").
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterable, Optional

from src.db import get_conn

logger = logging.getLogger(__name__)


# Pořadí MUSÍ jít od nejspecifičtějšího po nejobecnější.
# Každé pravidlo je seznam keywordů (CS/SK varianty); pokud kterýkoli matchne,
# label se vrátí. Některé klíče (např. "nová") matchnou i fráze jako "není nová",
# proto držíme delší fráze jako první gate.
_RULES: list[tuple[str, float, list[str]]] = [
    # Nejhorší stav má precedenci: "praskliny", "rozbitá" — přebije případné
    # "krásná" v té samé větě.
    ("poor", 0.20, ["poskozena", "poskodena", "rozbita", "rozbity", "rozbite",
                    "vada", "trha", "lepena"]),
    ("fair", 0.40, ["odrene", "odreny", "odrena", "skrabance", "skraban",
                    "praskliny", "patina"]),
    # Pozitivní stavy — specifické varianty PRVNÍ
    ("new", 1.00, ["s visackou", "s tagem", "nepouzita", "nepouzity", "nepouzite",
                   "zbrusu nova", "zbrusu novy", "zcela nova"]),
    ("like_new", 0.95, ["jako nova", "jako novy", "jako nove", "ako nova",
                        "minimalne nosena", "minimalne pouzita", "temer nova"]),
    ("excellent", 0.85, ["vyborny stav", "skvely stav", "skvely", "perfektni stav",
                         "perfektne stav", "vynikajuci stav", "vynikajici stav"]),
    ("very_good", 0.75, ["velmi dobry", "velmi dobra", "velmi dobre", "velmi pekna",
                         "velmi pekny"]),
    ("good", 0.60, ["dobry stav", "dobra stav", "znamky pouzivani",
                    "znamky pouzitia", "par krat", "par-krat", "obcas pouzita"]),
    # Generické "nová" / "nove" — TADY na konci, aby "jako nová" bylo zachycené dřív
    ("new", 1.00, ["nova", "novy", "nove", "nove kabelky"]),
]


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    return _strip_diacritics((text or "").lower())


def _contains_word(haystack: str, needle: str) -> bool:
    """Word-boundary match — `nova` v `znova` nesmí matchnout."""
    pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def score_condition(text: Optional[str]) -> tuple[str, float]:
    """
    Vrátí (label, score). Default při neúspěchu: ("good", 0.60).
    """
    if not text:
        return ("good", 0.60)

    haystack = _normalize(text)

    for label, score, keywords in _RULES:
        for kw in keywords:
            if _contains_word(haystack, kw):
                return (label, score)

    return ("good", 0.60)


def score_all_listings() -> int:
    """
    Doplní condition_score do všech listings, kde chybí (NULL).
    Použije condition_raw + description + title jako vstup.
    Vrátí počet aktualizovaných řádků.
    """
    updated = 0
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, condition_raw, description, title
            FROM listings
            WHERE condition_score IS NULL
            """
        ).fetchall()
        for row in rows:
            text_parts = [
                row["condition_raw"] or "",
                row["title"] or "",
                row["description"] or "",
            ]
            combined = " ".join(p for p in text_parts if p)
            label, score = score_condition(combined)
            conn.execute(
                """
                UPDATE listings
                SET condition_score = ?,
                    condition_raw = COALESCE(condition_raw, ?)
                WHERE id = ?
                """,
                (score, label, row["id"]),
            )
            updated += 1
    return updated

"""
Heuristika rizika padělku (counterfeit_risk 0..1).

Sčítáme váhy z `config.COUNTERFEIT_SIGNALS`. Klíčové signály:
- `description_has_replica_words` (váha 0.50, NEJSILNĚJŠÍ)
- `seller_has_multi_brands` (0.20)  -- pokud popis zmiňuje více brandů
- price ratio vs. fair_value (silný signál ceny)
- chybějící materiály v popisu (data code, dust bag, doklady)
- málo/stock fotky

Výstup je clamp <0.0, 1.0>.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

from src.config import COUNTERFEIT_SIGNALS, REPLICA_KEYWORDS
from src.db import get_conn

logger = logging.getLogger(__name__)


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    return _strip_diacritics((text or "").lower())


# Brand mentions naznačující, že prodejce drží víc značek (= bazaroví prodejci, vyšší riziko)
_OTHER_BRANDS = ("chanel", "dior", "gucci", "prada", "hermes", "fendi",
                 "celine", "saint laurent", "ysl", "valentino", "balenciaga",
                 "bottega", "burberry", "loewe")


def _has_replica_words(text: str) -> bool:
    for kw in REPLICA_KEYWORDS:
        kw_norm = _normalize(kw)
        # Word-boundary, ale "1:1" obsahuje speciální znaky → najdeme přímo substring
        if re.search(rf"(?<![a-z0-9]){re.escape(kw_norm)}(?![a-z0-9])", text):
            return True
        if kw_norm in text and not re.search(r"[a-z]", kw_norm):
            return True
    return False


def _multi_brand_mentions(text: str) -> int:
    return sum(1 for b in _OTHER_BRANDS if b in text)


def _photo_count(photo_urls_field: Any) -> int:
    if photo_urls_field is None:
        return 0
    if isinstance(photo_urls_field, list):
        return len(photo_urls_field)
    if isinstance(photo_urls_field, str):
        try:
            data = json.loads(photo_urls_field)
            return len(data) if isinstance(data, list) else 0
        except json.JSONDecodeError:
            return 0
    return 0


def compute_counterfeit_risk(listing: dict[str, Any]) -> float:
    """
    Vstup: dict s klíči title, description, price_czk, fair_value_czk, photo_urls.
    Vrátí counterfeit risk 0..1 (clamped).
    """
    risk = 0.0

    title = _normalize(listing.get("title") or "")
    description = _normalize(listing.get("description") or "")
    haystack = f"{title}\n{description}"

    # NEJSILNĚJŠÍ signál: replica keywords v popisu nebo titulu
    if _has_replica_words(haystack):
        risk += COUNTERFEIT_SIGNALS["description_has_replica_words"]

    # Multi-brand mentions
    if _multi_brand_mentions(haystack) >= 2:
        risk += COUNTERFEIT_SIGNALS["seller_has_multi_brands"]

    # Cena vs. fair value
    price = listing.get("price_czk")
    fair = listing.get("fair_value_czk")
    if price and fair and fair > 0:
        ratio = price / fair
        if ratio < 0.15:
            risk += COUNTERFEIT_SIGNALS["price_below_15pct_fair"]
            risk += COUNTERFEIT_SIGNALS["price_below_30pct_fair"]
        elif ratio < 0.30:
            risk += COUNTERFEIT_SIGNALS["price_below_30pct_fair"]

    # Chybějící doklady
    if "data code" not in haystack and "datakod" not in haystack:
        risk += COUNTERFEIT_SIGNALS["no_data_code_mentioned"]
    if "dust bag" not in haystack and "prachovka" not in haystack and "obal" not in haystack:
        risk += COUNTERFEIT_SIGNALS["no_dust_bag_mentioned"]
    if not any(k in haystack for k in ("certifik", "uctenka", "uctenku", "doklad", "faktur")):
        risk += COUNTERFEIT_SIGNALS["no_proof_of_purchase"]

    # Málo fotek
    n_photos = _photo_count(listing.get("photo_urls"))
    if n_photos < 3:
        risk += COUNTERFEIT_SIGNALS["few_photos"]

    return max(0.0, min(1.0, risk))


def update_all_counterfeit_risks() -> int:
    """Updatuje counterfeit_risk u všech aktivních listingů. Vrací počet."""
    updated = 0
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, title, description, price_czk, fair_value_czk, photo_urls
            FROM listings
            WHERE is_active = 1
            """
        ).fetchall()
        for row in rows:
            risk = compute_counterfeit_risk(dict(row))
            conn.execute(
                "UPDATE listings SET counterfeit_risk = ? WHERE id = ?",
                (risk, row["id"]),
            )
            updated += 1
    return updated

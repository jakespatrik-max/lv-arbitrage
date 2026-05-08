"""
Scraper pro oblecenie.bazos.sk — slovenská varianta Bazoše.

Strukturně shodný s bazos_cz, jen ceny v EUR (konverze 25.0 → CZK).
Přepisuje pouze _extract_price kvůli detekci €/EUR.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from src.scrapers.bazos_cz import BazosCzScraper

logger = logging.getLogger(__name__)


class BazosSkScraper(BazosCzScraper):
    source_id = "bazos_sk"

    def parse_listing(self, url: str, html: str) -> Optional[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")

        m = self.LISTING_HREF_RE.match(url) or re.search(r"/inzerat/(\d+)/", url)
        if not m:
            return None
        source_id = m.group(1)

        nadpis = soup.find("h1", class_="nadpisdetail")
        title = nadpis.get_text(" ", strip=True) if nadpis else None

        description_el = soup.find("div", class_="popisdetail")
        description = description_el.get_text("\n", strip=True) if description_el else None

        price_value, currency = self._extract_price_and_currency(soup)
        if price_value is None:
            return None
        if currency == "EUR":
            price_original = price_value
            price_czk = int(round(price_value * self.EUR_TO_CZK))
        elif currency == "CZK":
            price_original = price_value
            price_czk = price_value
        else:
            return None

        if price_czk < 1000:
            logger.warning(
                f"bazos_sk: nízká cena {price_czk} CZK na {url} (možná peněženka/pouzdro)"
            )

        location = self._extract_location(soup)
        posted_at = self._extract_posted_at(soup)
        photo_urls = self._extract_photos(source_id, soup)

        return {
            "source_id": source_id,
            "url": url,
            "title": title,
            "description": description,
            "price_czk": price_czk,
            "price_original": price_original,
            "currency": currency,
            "model_raw": title,
            "condition_raw": None,
            "photo_urls": photo_urls,
            "location": location,
            "posted_at": posted_at,
        }

    @staticmethod
    def _extract_price_and_currency(soup: BeautifulSoup) -> tuple[Optional[int], str]:
        for td in soup.find_all("td"):
            if not td.get_text(strip=True).startswith("Cena:"):
                continue
            sib = td.find_next_sibling("td")
            while sib is not None and not sib.get_text(strip=True):
                sib = sib.find_next_sibling("td")
            if sib is None:
                continue
            raw = sib.get_text(" ", strip=True)
            lowered = raw.lower()
            if "dohod" in lowered or "v texte" in lowered or "v textu" in lowered:
                return None, ""
            digits = re.sub(r"[^\d]", "", raw)
            if not digits:
                return None, ""
            value = int(digits)
            if "€" in raw or "eur" in lowered:
                return value, "EUR"
            if "kč" in lowered or "czk" in lowered:
                return value, "CZK"
            # bazos.sk default = EUR
            return value, "EUR"
        return None, ""

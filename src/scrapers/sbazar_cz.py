"""
Scraper pro sbazar.cz.

POZOR: robots.txt sbazar.cz NEPOVOLUJE žádného obecného bota
(`User-agent: *` -> `Disallow: /`). Náš scraper to respektuje skrz
`BaseScraper.can_fetch()` — `iter_listing_urls()` ani `fetch()` v praxi
neproběhnou (vrátí None / yield 0). Parser je implementovaný a otestovaný
proti uloženému fixture, aby byl pokryt celý kontrakt; jakmile by Seznam
v robots.txt povolil náš UA, scraper začne fungovat bez dalších změn.

Detail page má JSON-LD `Product` (name, description, offers.price/Currency,
offers.itemCondition, offers.seller.name — to NEUKLÁDÁME), <time datetime="...">
a `<span class="speakable location">` s lokalitou.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class SbazarCzScraper(BaseScraper):
    source_id = "sbazar_cz"

    LISTING_HREF_RE = re.compile(r"^/inzerat/(\d+)-[a-z0-9-]+/?$")
    DISCOVERY_PATHS = ("/hledej/louis-vuitton",)
    EUR_TO_CZK = 25.0

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def iter_listing_urls(self, query: str) -> Iterator[str]:
        seen: set[str] = set()
        for path in self.DISCOVERY_PATHS:
            url = urljoin(self.config["base_url"], path)
            html = self.fetch(url)  # robots.txt block happens here
            if html is None:
                continue
            for href in self._extract_listing_hrefs(html):
                full = urljoin(self.config["base_url"], href)
                if full in seen:
                    continue
                seen.add(full)
                yield full

    @classmethod
    def _extract_listing_hrefs(cls, html: str) -> Iterator[str]:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=cls.LISTING_HREF_RE):
            yield a["href"]

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse_listing(self, url: str, html: str) -> Optional[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")

        product = self._extract_jsonld_product(soup)
        if product is None:
            return None

        offer = self._first_offer(product)
        price_raw = offer.get("price") if offer else None
        if price_raw in (None, "", 0, "0"):
            return None
        try:
            price_value = float(price_raw)
        except (TypeError, ValueError):
            return None

        currency = (offer.get("priceCurrency") or "CZK").upper()
        if currency == "CZK":
            price_czk = int(round(price_value))
            price_original = price_czk
        elif currency == "EUR":
            price_original = int(round(price_value))
            price_czk = int(round(price_value * self.EUR_TO_CZK))
        else:
            logger.warning(f"sbazar_cz: neznámá měna {currency!r} na {url}")
            return None

        if price_czk < 1000:
            logger.warning(
                f"sbazar_cz: nízká cena {price_czk} CZK na {url} (možná peněženka/pouzdro)"
            )

        title = (product.get("name") or "").strip() or None
        description = (product.get("description") or "").strip() or None

        m = re.search(r"/inzerat/(\d+)-", url)
        source_id = m.group(1) if m else url

        return {
            "source_id": source_id,
            "url": url,
            "title": title,
            "description": description,
            "price_czk": price_czk,
            "price_original": price_original,
            "currency": currency,
            "model_raw": title,
            "condition_raw": self._extract_condition_raw(offer),
            "photo_urls": self._extract_photos(soup, product),
            "location": self._extract_location(soup),
            "posted_at": self._extract_posted_at(soup),
        }

    @staticmethod
    def _extract_jsonld_product(soup: BeautifulSoup) -> Optional[dict[str, Any]]:
        for script in soup.find_all("script", type="application/ld+json"):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            candidates = data if isinstance(data, list) else [data]
            for item in candidates:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    return item
        return None

    @staticmethod
    def _first_offer(product: dict[str, Any]) -> dict[str, Any]:
        offers = product.get("offers")
        if isinstance(offers, list) and offers:
            return offers[0] if isinstance(offers[0], dict) else {}
        if isinstance(offers, dict):
            return offers
        return {}

    @staticmethod
    def _extract_condition_raw(offer: dict[str, Any]) -> Optional[str]:
        ic = offer.get("itemCondition") or ""
        if "NewCondition" in ic:
            return "nové"
        if "UsedCondition" in ic:
            return "použité"
        if "RefurbishedCondition" in ic:
            return "renovované"
        return None

    @staticmethod
    def _extract_location(soup: BeautifulSoup) -> Optional[str]:
        loc_span = soup.find("span", class_="location")
        if loc_span:
            txt = loc_span.get_text(" ", strip=True)
            # "v Orlová" -> "Orlová"
            txt = re.sub(r"^v\s+", "", txt, flags=re.IGNORECASE)
            if txt:
                return txt
        return None

    @staticmethod
    def _extract_posted_at(soup: BeautifulSoup) -> Optional[str]:
        time_el = soup.find("time", attrs={"datetime": True})
        if time_el:
            dt = time_el["datetime"]
            # "2026-05-08T12:36:30" -> "2026-05-08"
            return dt[:10] if len(dt) >= 10 else None
        return None

    @staticmethod
    def _extract_photos(soup: BeautifulSoup, product: dict[str, Any]) -> list[str]:
        photos: list[str] = []
        seen: set[str] = set()

        def _add(url: str) -> None:
            base = url.split("?", 1)[0]
            if base and base not in seen:
                seen.add(base)
                photos.append(url)

        # JSON-LD image
        img = product.get("image")
        if isinstance(img, str):
            _add(img)
        elif isinstance(img, list):
            for entry in img:
                if isinstance(entry, str):
                    _add(entry)
                elif isinstance(entry, dict):
                    url = entry.get("contentUrl") or entry.get("url") or ""
                    if url:
                        _add(url)

        # og:image fallbacks
        for m in soup.find_all("meta", property="og:image"):
            v = m.get("content") or ""
            if v:
                _add(v)
        return photos

"""
Scraper pro luxurybags.cz — profi second-hand obchod, slouží jako referenční
zdroj pro výpočet fair value.

Detail page má strukturu:
- JSON-LD `Product` (name, identifier, image, offers.price, offers.priceCurrency)
- div#panel-01 s popisem (Nedostatky, Sériové číslo, Rozměry, Materiál, ...)
- SVG <text> uvnitř span[data-toggle="score"] s grade letter (A⁺ / A / B / C)

Search/pagination není podporován přes čistý HTML (parametrické URL jsou
v robots.txt zakázané, paginace běží přes JS). Iter URLs proto čte
kategorii /kabelky-damske a filtruje slugy podle query.
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


class LuxuryBagsScraper(BaseScraper):
    source_id = "luxurybags"

    LISTING_HREF_RE = re.compile(r"^/kabelky/(\d+)-")
    CATEGORY_PATHS = ("/kabelky-damske",)
    EUR_TO_CZK = 25.0

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def iter_listing_urls(self, query: str) -> Iterator[str]:
        slug_terms = self._slug_terms(query)
        seen: set[str] = set()
        for path in self.CATEGORY_PATHS:
            url = urljoin(self.config["base_url"], path)
            html = self.fetch(url)
            if html is None:
                continue
            for href in self._extract_listing_hrefs(html, slug_terms):
                full = urljoin(self.config["base_url"], href)
                if full in seen:
                    continue
                seen.add(full)
                yield full

    @staticmethod
    def _slug_terms(query: str) -> list[str]:
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
        return [slug] if slug else []

    @classmethod
    def _extract_listing_hrefs(cls, html: str, slug_terms: list[str]) -> Iterator[str]:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=cls.LISTING_HREF_RE):
            href = a["href"]
            href_l = href.lower()
            if slug_terms and not any(t in href_l for t in slug_terms):
                continue
            yield href

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse_listing(self, url: str, html: str) -> Optional[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")

        product = self._extract_jsonld_product(soup)
        if product is None:
            logger.warning(f"luxurybags: chybí JSON-LD Product na {url}")
            return None

        offers = product.get("offers") or {}
        price_raw = offers.get("price")
        if price_raw in (None, "", 0, "0"):
            return None
        try:
            price_value = float(price_raw)
        except (TypeError, ValueError):
            return None

        currency = (offers.get("priceCurrency") or "CZK").upper()
        if currency == "CZK":
            price_czk = int(round(price_value))
            price_original = price_czk
        elif currency == "EUR":
            price_original = int(round(price_value))
            price_czk = int(round(price_value * self.EUR_TO_CZK))
        else:
            logger.warning(f"luxurybags: neznámá měna {currency!r} na {url}")
            return None

        if price_czk < 1000:
            logger.warning(
                f"luxurybags: nízká cena {price_czk} CZK na {url} "
                f"(možná peněženka/pouzdro, ne kabelka)"
            )

        full_name = (product.get("name") or "").strip()
        title = full_name or None
        # "Louis Vuitton - Alma BB Monogram Canvas Bag" -> "Alma BB Monogram Canvas Bag"
        model_raw: Optional[str] = None
        if full_name:
            model_raw = full_name.split(" - ", 1)[1] if " - " in full_name else full_name

        source_id = str(product.get("identifier") or "").strip()
        if not source_id:
            m = re.search(r"/kabelky/(\d+)-", url)
            source_id = m.group(1) if m else url

        images = product.get("image") or []
        if isinstance(images, str):
            images = [images]
        photo_urls = [img for img in images if isinstance(img, str)]

        condition_raw = self._extract_condition_grade(soup)
        description = self._extract_description(soup)

        return {
            "source_id": source_id,
            "url": url,
            "title": title,
            "description": description,
            "price_czk": price_czk,
            "price_original": price_original,
            "currency": currency,
            "model_raw": model_raw,
            "condition_raw": condition_raw,
            "photo_urls": photo_urls,
            "location": None,
            "posted_at": None,
        }

    @staticmethod
    def _extract_jsonld_product(soup: BeautifulSoup) -> Optional[dict[str, Any]]:
        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or script.get_text() or ""
            raw = raw.strip()
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
    def _extract_condition_grade(soup: BeautifulSoup) -> Optional[str]:
        score = soup.find("span", attrs={"data-toggle": "score"})
        if score is None:
            return None
        text_el = score.find("text")
        if text_el and text_el.get_text(strip=True):
            return text_el.get_text(strip=True)
        return None

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> Optional[str]:
        panel = soup.find("div", id="panel-01")
        if panel is None:
            return None
        # Drop the score-grade widget (legend HTML in data-content blob)
        for widget in panel.find_all(attrs={"data-toggle": "score"}):
            widget.decompose()
        text = panel.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text or None

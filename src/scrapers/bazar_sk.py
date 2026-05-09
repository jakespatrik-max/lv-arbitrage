"""
Scraper pro oblecenie.bazar.sk (United Classifieds).

Detail page má JSON-LD `Product` (name, image, description, offers.price/Currency,
offers.itemCondition, offers.seller.name — to NEPOUŽÍVÁME).
Datum + ID v `div.block-title__info` ("z dňa 26. 4. 2026 (č. 39482067)").
Lokalita ve `span.item-miniature__location`.
Galerie obrázků: img.unitedclassifieds.sk/foto/{base64_resize}/{hash}?st=...
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


class BazarSkScraper(BaseScraper):
    source_id = "bazar_sk"

    LISTING_HREF_RE = re.compile(r"^https?://oblecenie\.bazar\.sk/(\d+)-[a-z0-9-]+/?$")
    DISCOVERY_PATHS = (
        "/kabelky/louis-vuitton/predaj/",
        "/kabelky/",
    )
    EUR_TO_CZK = 25.0

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def iter_listing_urls(self, query: str) -> Iterator[str]:
        seen: set[str] = set()
        for path in self.DISCOVERY_PATHS:
            url = urljoin(self.config["base_url"], path)
            html = self.fetch(url)
            if html is None:
                continue
            for href in self._extract_listing_hrefs(html):
                if href in seen:
                    continue
                seen.add(href)
                yield href

    @classmethod
    def _extract_listing_hrefs(cls, html: str) -> Iterator[str]:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            if cls.LISTING_HREF_RE.match(a["href"]):
                yield a["href"]

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse_listing(self, url: str, html: str) -> Optional[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")

        product = self._extract_jsonld_product(soup)
        if product is None:
            return None

        offers = product.get("offers") or {}
        price_raw = offers.get("price")
        if price_raw in (None, "", 0, "0"):
            return None
        try:
            price_value = float(price_raw)
        except (TypeError, ValueError):
            return None

        currency = (offers.get("priceCurrency") or "EUR").upper()
        if currency == "EUR":
            price_original = int(round(price_value))
            price_czk = int(round(price_value * self.EUR_TO_CZK))
        elif currency == "CZK":
            price_original = int(round(price_value))
            price_czk = price_original
        else:
            logger.warning(f"bazar_sk: neznámá měna {currency!r} na {url}")
            return None

        if price_czk < 1000:
            logger.warning(
                f"bazar_sk: nízká cena {price_czk} CZK na {url} (možná peněženka/pouzdro)"
            )

        title = (product.get("name") or "").strip() or None
        description = (product.get("description") or "").strip() or None

        info_text = self._info_block_text(soup)
        source_id = self._extract_source_id(info_text, url)
        posted_at = self._extract_posted_at(info_text)
        condition_raw = self._extract_condition_raw(info_text, offers)
        location = self._extract_location(soup)
        photo_urls = self._extract_photos(soup, product)

        return {
            "source_id": source_id,
            "url": url,
            "title": title,
            "description": description,
            "price_czk": price_czk,
            "price_original": price_original,
            "currency": currency,
            "model_raw": title,
            "condition_raw": condition_raw,
            "photo_urls": photo_urls,
            "location": location,
            "posted_at": posted_at,
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
    def _info_block_text(soup: BeautifulSoup) -> str:
        info = soup.find("div", class_="block-title__info")
        return info.get_text(" ", strip=True) if info else ""

    @staticmethod
    def _extract_source_id(info_text: str, url: str) -> str:
        m = re.search(r"\(\s*č\.\s*(\d+)\s*\)", info_text)
        if m:
            return m.group(1)
        m = re.search(r"oblecenie\.bazar\.sk/(\d+)-", url)
        return m.group(1) if m else url

    @staticmethod
    def _extract_posted_at(info_text: str) -> Optional[str]:
        m = re.search(r"z\s+d[ňn]a\s+(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", info_text)
        if not m:
            return None
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    @staticmethod
    def _extract_condition_raw(info_text: str, offers: dict[str, Any]) -> Optional[str]:
        # info_text: "Stav: nové, Typ: predaj, ..."
        m = re.search(r"Stav:\s*([^,]+)", info_text)
        if m:
            return m.group(1).strip()
        # Fallback to schema.org itemCondition
        ic = offers.get("itemCondition") or ""
        if "NewCondition" in ic:
            return "nové"
        if "UsedCondition" in ic:
            return "použité"
        return None

    @staticmethod
    def _extract_location(soup: BeautifulSoup) -> Optional[str]:
        span = soup.find("span", class_="item-miniature__location")
        if span:
            txt = span.get_text(" ", strip=True)
            if txt:
                return txt
        return None

    @classmethod
    def _extract_photos(
        cls, soup: BeautifulSoup, product: dict[str, Any]
    ) -> list[str]:
        # Primary image from JSON-LD; gallery from <img> with unitedclassifieds host.
        # Dedupe by image hash (segment after the resize-spec base64).
        photos: list[str] = []
        seen_hashes: set[str] = set()

        primary = product.get("image")
        if isinstance(primary, str) and primary:
            cls._add_photo(primary, photos, seen_hashes)
        elif isinstance(primary, list):
            for img in primary:
                if isinstance(img, str):
                    cls._add_photo(img, photos, seen_hashes)

        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if "unitedclassifieds.sk/foto/" in src:
                cls._add_photo(src, photos, seen_hashes)
        return photos

    @staticmethod
    def _add_photo(url: str, photos: list[str], seen_hashes: set[str]) -> None:
        m = re.search(r"/foto/[^/]+/([^?#]+)", url)
        if not m:
            return
        h = m.group(1)
        if h in seen_hashes:
            return
        seen_hashes.add(h)
        photos.append(url)

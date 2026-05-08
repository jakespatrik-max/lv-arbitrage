"""
Scraper pro armadio.cz — profi second-hand obchod (Shoptet platforma).

Detail page má strukturu:
- itemprop=name (h1)
- itemprop=price (meta) + itemprop=priceCurrency
- itemprop=productID (číselné ID)
- itemprop=image (primární fotka)
- #description s parametry "Stav celkového opotřebení : ...", "Stav produktu : ...",
  "Materiál", "Rozměry", "Barva"
- Galerie obrázků: /usr/www.armadio.cz/user/shop/{big,related}/{productID}-N_*

Search je v robots.txt zakázaný; iter_listing_urls čte brand page
/znacka/louis-vuitton/ a filtruje slugy.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ArmadioScraper(BaseScraper):
    source_id = "armadio"

    BRAND_PATHS = ("/znacka/louis-vuitton/",)
    LISTING_HREF_RE = re.compile(r"^/(?!znacka/|kategorie/|stranka/|index/)[a-z0-9-]+/?$")
    EUR_TO_CZK = 25.0

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def iter_listing_urls(self, query: str) -> Iterator[str]:
        slug_terms = self._slug_terms(query)
        seen: set[str] = set()
        for path in self.BRAND_PATHS:
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
        for a in soup.select("a.image-cropper, a.main-link, a[href]"):
            href = a.get("href") or ""
            if not cls.LISTING_HREF_RE.match(href):
                continue
            href_l = href.lower()
            if slug_terms and not any(t in href_l for t in slug_terms):
                continue
            yield href

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse_listing(self, url: str, html: str) -> Optional[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")

        price_el = soup.find(attrs={"itemprop": "price"})
        if price_el is None:
            return None
        price_raw = price_el.get("content") or price_el.get_text(strip=True)
        if not price_raw:
            return None
        try:
            price_value = float(price_raw)
        except (TypeError, ValueError):
            digits = re.sub(r"[^\d.]", "", price_raw.replace(",", "."))
            try:
                price_value = float(digits)
            except ValueError:
                return None
        if price_value <= 0:
            return None

        currency_el = soup.find(attrs={"itemprop": "priceCurrency"})
        currency = (currency_el.get("content") if currency_el else "CZK") or "CZK"
        currency = currency.upper()

        if currency == "CZK":
            price_czk = int(round(price_value))
            price_original = price_czk
        elif currency == "EUR":
            price_original = int(round(price_value))
            price_czk = int(round(price_value * self.EUR_TO_CZK))
        else:
            logger.warning(f"armadio: neznámá měna {currency!r} na {url}")
            return None

        if price_czk < 1000:
            logger.warning(
                f"armadio: nízká cena {price_czk} CZK na {url} (možná peněženka/pouzdro)"
            )

        # Title — prefer itemprop=name on a leaf product (avoid offers/brand/etc.)
        title = self._extract_title(soup)
        model_raw = self._strip_brand(title) if title else None

        source_id = self._extract_source_id(soup, url)
        photo_urls = self._extract_photos(soup, source_id)
        description = self._extract_description(soup)
        condition_raw = self._extract_condition_raw(description)

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
    def _extract_title(soup: BeautifulSoup) -> Optional[str]:
        h1 = soup.find("h1")
        if h1:
            txt = h1.get_text(" ", strip=True)
            if txt:
                return txt
        # fallback: first itemprop=name that isn't a Brand/Organization/breadcrumb
        for el in soup.find_all(attrs={"itemprop": "name"}):
            txt = (el.get("content") or el.get_text(" ", strip=True)).strip()
            if txt and txt.lower() not in {"louis vuitton", "armadio", "domů", "kabelky"}:
                return txt
        return None

    @staticmethod
    def _strip_brand(title: str) -> str:
        # "LOUIS VUITTON Alma PM ..." -> "Alma PM ..."
        m = re.match(r"^(?:LOUIS VUITTON|Louis Vuitton|GUCCI|Gucci|CHANEL|Chanel|"
                     r"PRADA|Prada|HERMES|Hermes|DIOR|Dior)\s+(.+)$", title)
        return m.group(1) if m else title

    @staticmethod
    def _extract_source_id(soup: BeautifulSoup, url: str) -> str:
        prod_id = soup.find(attrs={"itemprop": "productID"})
        if prod_id is not None:
            val = (prod_id.get("content") or prod_id.get_text(strip=True)).strip()
            if val:
                return val
        # Fallback: derive from gallery image filename pattern (e.g. 31307-5_...)
        for img in soup.find_all("img"):
            v = img.get("data-src") or img.get("src") or ""
            m = re.search(r"/shop/(?:big|related|small)/(\d+)-\d+_", v)
            if m:
                return m.group(1)
        # Last resort: trailing slug as ID surrogate
        m = re.search(r"/([a-z0-9-]+)/?$", url)
        return m.group(1) if m else url

    @staticmethod
    def _extract_photos(soup: BeautifulSoup, source_id: str) -> list[str]:
        photos: list[str] = []
        seen: set[str] = set()
        # Match image filenames that start with "{source_id}-N_..."
        prefix_re = re.compile(rf"/shop/(?:big|related)/{re.escape(source_id)}-\d+_")
        for img in soup.find_all("img"):
            for attr in ("data-src", "src"):
                v = img.get(attr)
                if v and prefix_re.search(v):
                    if v not in seen:
                        seen.add(v)
                        photos.append(v)
                    break
        # If nothing matched, fall back to og:image
        if not photos:
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                photos.append(og["content"])
        return photos

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> Optional[str]:
        desc = soup.find(id="description")
        if desc is None:
            desc = soup.find(attrs={"itemprop": "description"})
        if desc is None:
            return None
        text = desc.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text or None

    @staticmethod
    def _extract_condition_raw(description: Optional[str]) -> Optional[str]:
        if not description:
            return None
        m = re.search(
            r"Stav\s+celkov[ée]ho\s+opot[řr]eben[ií]\s*:\s*([^\n]+)",
            description,
            re.IGNORECASE,
        )
        return m.group(1).strip() if m else None

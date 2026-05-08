"""
Scraper pro obleceni.bazos.cz — C2C inzeráty (oblečení/kabelky).

Detail page má strukturu:
- h1.nadpisdetail: titulek
- span.velikost10 v .inzeratydetnadpis: datum vložení "- [D.M. YYYY]"
- div.popisdetail: text popisu
- <tr> s <td>Cena:</td><td>...<span translate="no">123 Kč</span>
- <tr> s <td>Lokalita:</td><td colspan="2">PSČ Město</td>
- img.bazos.cz/img/{N}/{seg}/{ID}.jpg (full-size; varianty s 't' = thumbnaily)

GDPR: stránka obsahuje "Jméno:" a "Telefon:" — parser je IGNORUJE.

Search vyhledávání (?hledat=) je v robots.txt zakázané; iter_listing_urls
prochází brand kategorii /inzeraty/{query_slug}/ pokud existuje.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class BazosCzScraper(BaseScraper):
    source_id = "bazos_cz"

    LISTING_HREF_RE = re.compile(r"^(?:https?://[a-z]+\.bazos\.cz)?/inzerat/(\d+)/")
    EUR_TO_CZK = 25.0

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def iter_listing_urls(self, query: str) -> Iterator[str]:
        slug = self._slugify(query)
        candidate_paths = [f"/inzeraty/{slug}/"] if slug else []
        candidate_paths.append("/inzeraty/louis-vuitton/")
        seen: set[str] = set()
        for path in candidate_paths:
            url = urljoin(self.config["base_url"], path)
            html = self.fetch(url)
            if html is None:
                continue
            for href in self._extract_listing_hrefs(html):
                full = self._absolutize(href)
                if full in seen:
                    continue
                seen.add(full)
                yield full

    @staticmethod
    def _slugify(query: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")

    def _absolutize(self, href: str) -> str:
        if href.startswith("http"):
            return href
        return urljoin(self.config["base_url"], href)

    @classmethod
    def _extract_listing_hrefs(cls, html: str) -> Iterator[str]:
        soup = BeautifulSoup(html, "lxml")
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if cls.LISTING_HREF_RE.match(href):
                if href not in seen:
                    seen.add(href)
                    yield href

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse_listing(self, url: str, html: str) -> Optional[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")

        m = self.LISTING_HREF_RE.match(url) or re.search(r"/inzerat/(\d+)/", url)
        if not m:
            return None
        source_id = m.group(1)

        nadpis = soup.find("h1", class_="nadpisdetail")
        title = nadpis.get_text(" ", strip=True) if nadpis else None

        description_el = soup.find("div", class_="popisdetail")
        description = (
            description_el.get_text("\n", strip=True) if description_el else None
        )

        price_czk = self._extract_price(soup)
        if price_czk is None:
            return None
        if price_czk < 1000:
            logger.warning(
                f"bazos_cz: nízká cena {price_czk} CZK na {url} (možná peněženka/pouzdro)"
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
            "price_original": price_czk,
            "currency": "CZK",
            "model_raw": title,  # bazos nemá strukturovaný model — analyze fáze klasifikuje
            "condition_raw": None,
            "photo_urls": photo_urls,
            "location": location,
            "posted_at": posted_at,
        }

    @staticmethod
    def _extract_price(soup: BeautifulSoup) -> Optional[int]:
        # Find <td>Cena:</td><td>...<b><span>123 Kč</span></b>...
        for td in soup.find_all("td"):
            text = td.get_text(strip=True)
            if text == "Cena:" or text.startswith("Cena:"):
                value_td = td.find_next_sibling("td")
                if value_td is None:
                    continue
                raw = value_td.get_text(" ", strip=True)
                lowered = raw.lower()
                if "dohod" in lowered or "v textu" in lowered:
                    return None
                digits = re.sub(r"[^\d]", "", raw)
                if not digits:
                    return None
                return int(digits)
        return None

    @staticmethod
    def _extract_location(soup: BeautifulSoup) -> Optional[str]:
        for td in soup.find_all("td"):
            if not td.get_text(strip=True).startswith("Lokalita"):
                continue
            sib = td.find_next_sibling("td")
            while sib is not None and not sib.get_text(strip=True):
                sib = sib.find_next_sibling("td")
            if sib is None:
                continue
            raw = sib.get_text(" ", strip=True)
            # "180 00 Praha 8" -> drop leading PSČ
            stripped = re.sub(r"^\s*\d{3}\s?\d{2}\s+", "", raw)
            return stripped or None
        return None

    @staticmethod
    def _extract_posted_at(soup: BeautifulSoup) -> Optional[str]:
        nadpis = soup.find("div", class_="inzeratydetnadpis")
        if nadpis is None:
            return None
        m = re.search(r"\[(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\]", nadpis.get_text())
        if not m:
            return None
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    @staticmethod
    def _extract_photos(source_id: str, soup: BeautifulSoup) -> list[str]:
        photos: list[str] = []
        seen: set[str] = set()
        # Bazos pattern: /img/{N|Nt}/{seg}/{ID}.jpg — full-size has no 't' suffix
        pat = re.compile(rf"//[^/]*\.bazos\.cz/img/(\d+)(t?)/[^/]+/{re.escape(source_id)}\.jpg")
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            m = pat.search(src)
            if not m:
                continue
            # Prefer full-size (no 't'); rebuild full-size URL from any thumbnail too
            full = re.sub(r"(/img/\d+)t(/)", r"\1\2", src)
            if full not in seen:
                seen.add(full)
                photos.append(full)
        return photos

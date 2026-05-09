"""
Base scraper. Každý platformní scraper dědí od BaseScraper a implementuje:
- iter_listing_urls(query) → vrací URL inzerátů na search stránkách
- parse_listing(url, html) → vrací dict s daty inzerátu

Společné věci (rate limiting, retry, robots.txt, persistence) řeší base.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from src.config import MAX_PAGES_PER_RUN, SOURCES, USER_AGENT
from src.db import get_conn, mark_inactive_if_missing, now_iso, upsert_listing

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Abstraktní třída pro všechny scrapery."""

    source_id: str  # nastav v podtřídě, např. "bazos_cz"

    def __init__(self) -> None:
        if not getattr(self, "source_id", None):
            raise ValueError("Podtřída musí nastavit source_id")
        self.config = SOURCES[self.source_id]
        # Per-source timeout (default 8s); reduces wasted waiting when
        # remote site throttles datacenter egress (typical for bazar.sk).
        timeout_s = self.config.get("timeout_seconds", 8.0)
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_s,
            follow_redirects=True,
        )
        self._last_request_at: float = 0.0
        self._robots: Optional[robotparser.RobotFileParser] = None

    # ------------------------------------------------------------------
    # Robots.txt
    # ------------------------------------------------------------------
    def _load_robots(self) -> robotparser.RobotFileParser:
        if self._robots is not None:
            return self._robots
        rp = robotparser.RobotFileParser()
        parsed = urlparse(self.config["base_url"])
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            rp.set_url(robots_url)
            rp.read()
        except Exception as e:
            logger.warning(f"Nelze načíst {robots_url}: {e}")
        self._robots = rp
        return rp

    def can_fetch(self, url: str) -> bool:
        if not self.config.get("robots_check", True):
            return True
        rp = self._load_robots()
        try:
            return rp.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    # ------------------------------------------------------------------
    # HTTP s rate limitingem a retry
    # ------------------------------------------------------------------
    def _wait_for_rate_limit(self) -> None:
        delay = self.config["rate_limit_seconds"]
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_at = time.monotonic()

    def fetch(self, url: str, max_retries: int = 2) -> Optional[str]:
        if not self.can_fetch(url):
            logger.warning(f"robots.txt zakazuje: {url}")
            return None

        for attempt in range(max_retries):
            self._wait_for_rate_limit()
            try:
                response = self.client.get(url)
                if response.status_code == 200:
                    return response.text
                if response.status_code in (429, 503):
                    backoff = (2**attempt) * 5
                    logger.warning(
                        f"{response.status_code} pro {url}, čekám {backoff}s"
                    )
                    time.sleep(backoff)
                    continue
                logger.warning(f"HTTP {response.status_code} pro {url}")
                return None
            except httpx.HTTPError as e:
                logger.warning(f"HTTP error pro {url}: {e}")
                if attempt == max_retries - 1:
                    return None
                time.sleep(2**attempt)
        return None

    # ------------------------------------------------------------------
    # Abstract API
    # ------------------------------------------------------------------
    @abstractmethod
    def iter_listing_urls(self, query: str) -> Iterator[str]:
        """Vrací URL detailů inzerátů pro daný query (např. 'alma bb')."""
        ...

    @abstractmethod
    def parse_listing(self, url: str, html: str) -> Optional[dict[str, Any]]:
        """Parsuje detail inzerátu. Vrací dict pro upsert_listing nebo None."""
        ...

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def scrape_query(self, query: str, mark_missing_inactive: bool = False) -> dict[str, int]:
        """
        Stáhne všechny inzeráty pro daný query.
        Vrací: {'found': X, 'new': Y, 'updated': Z}
        """
        run_start = now_iso()
        seen_urls: set[str] = set()
        counts = {"found": 0, "new": 0, "updated": 0, "unchanged": 0}

        with get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scrape_runs (source, started_at)
                VALUES (?, ?)
                """,
                (self.source_id, run_start),
            )
            run_id = cursor.lastrowid

            try:
                for url in self.iter_listing_urls(query):
                    counts["found"] += 1
                    seen_urls.add(url)

                    html = self.fetch(url)
                    if html is None:
                        continue

                    listing = self.parse_listing(url, html)
                    if listing is None:
                        continue

                    listing.setdefault("source", self.source_id)
                    listing.setdefault("scraped_at", now_iso())

                    result = upsert_listing(conn, listing)
                    counts[result] = counts.get(result, 0) + 1

                if mark_missing_inactive:
                    mark_inactive_if_missing(conn, self.source_id, seen_urls)

                conn.execute(
                    """
                    UPDATE scrape_runs
                    SET finished_at = ?, listings_found = ?, listings_new = ?, listings_updated = ?
                    WHERE id = ?
                    """,
                    (now_iso(), counts["found"], counts["new"], counts["updated"], run_id),
                )
            except Exception as e:
                conn.execute(
                    "UPDATE scrape_runs SET finished_at = ?, error = ? WHERE id = ?",
                    (now_iso(), str(e), run_id),
                )
                raise

        return counts

    def close(self) -> None:
        self.client.close()

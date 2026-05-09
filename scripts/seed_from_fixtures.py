"""
Seed local SQLite DB from saved HTML fixtures (no network, no rate limit).

Useful for smoke-testing the analyze pipeline + frontend without doing a
full live scrape. Each fixture is parsed via its scraper's parse_listing()
and upserted via src.db.upsert_listing.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make project importable when run as a plain script
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import get_conn, upsert_listing  # noqa: E402
from src.scrapers.armadio import ArmadioScraper  # noqa: E402
from src.scrapers.bazar_sk import BazarSkScraper  # noqa: E402
from src.scrapers.bazos_cz import BazosCzScraper  # noqa: E402
from src.scrapers.bazos_sk import BazosSkScraper  # noqa: E402
from src.scrapers.luxurybags import LuxuryBagsScraper  # noqa: E402
from src.scrapers.sbazar_cz import SbazarCzScraper  # noqa: E402

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures"

ENTRIES = [
    (
        LuxuryBagsScraper,
        FIXTURE_DIR / "luxurybags_alma_bb.html",
        "https://www.luxurybags.cz/kabelky/82488-louis-vuitton-alma-bb-monogram-canvas-bag",
    ),
    (
        ArmadioScraper,
        FIXTURE_DIR / "armadio_alma_pm.html",
        "https://www.armadio.cz/louis-vuitton-alma-pm-amarante-monogram-vernis-bag/",
    ),
    (
        BazosCzScraper,
        FIXTURE_DIR / "bazos_cz_lv.html",
        "https://obleceni.bazos.cz/inzerat/218515298/louis-vuitton-tasky.php",
    ),
    (
        BazosSkScraper,
        FIXTURE_DIR / "bazos_sk_lv.html",
        "https://oblecenie.bazos.sk/inzerat/191318943/louis-vuitton.php",
    ),
    (
        BazarSkScraper,
        FIXTURE_DIR / "bazar_sk_lv.html",
        "https://oblecenie.bazar.sk/39482067-damska-kabelka-lv/",
    ),
    (
        SbazarCzScraper,
        FIXTURE_DIR / "sbazar_cz_lv.html",
        "https://www.sbazar.cz/inzerat/230408402-louis-vuitton-kabelka",
    ),
]


def main() -> int:
    inserted = 0
    with get_conn() as conn:
        for scraper_cls, fixture_path, url in ENTRIES:
            html = fixture_path.read_text(encoding="utf-8")
            s = scraper_cls()
            try:
                listing = s.parse_listing(url, html)
            finally:
                s.close()
            if listing is None:
                print(f"  SKIP {scraper_cls.__name__}: parse_listing returned None")
                continue
            listing.setdefault("source", scraper_cls.source_id)
            result = upsert_listing(conn, listing)
            print(f"  {result.upper():9s} {scraper_cls.__name__:20s} {url[:90]}")
            inserted += 1

    print(f"\nSeeded {inserted} listings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

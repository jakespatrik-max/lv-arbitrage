"""
CLI orchestrace.

Příkazy:
    python -m src.cli init            # init DB
    python -m src.cli scrape-all      # spustí všechny scrapery
    python -m src.cli analyze         # přepočítá fair value + skóre
    python -m src.cli refresh         # scrape-all + analyze (cron job)
    python -m src.cli stats           # přehled DB

Pro Railway cron: nastavit `python -m src.cli refresh` denně ráno.
"""

from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timezone

from src.config import MODELS

logger = logging.getLogger("cli")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def cmd_init() -> int:
    from src.db import init_db
    init_db()
    return 0


def cmd_scrape_all() -> int:
    """
    Importuje všechny scrapery a postupně spustí scrape pro každý sledovaný model.
    Scrapery se importují dynamicky, takže když některý ještě není
    implementovaný, ostatní stále poběží.
    """
    SCRAPER_MODULES = [
        ("src.scrapers.luxurybags", "LuxuryBagsScraper"),  # benchmark first
        ("src.scrapers.armadio", "ArmadioScraper"),
        ("src.scrapers.bazos_cz", "BazosCzScraper"),
        ("src.scrapers.bazos_sk", "BazosSkScraper"),
        ("src.scrapers.bazar_sk", "BazarSkScraper"),
        ("src.scrapers.sbazar_cz", "SbazarCzScraper"),
    ]

    total_failures = 0

    for module_name, class_name in SCRAPER_MODULES:
        try:
            module = __import__(module_name, fromlist=[class_name])
            ScraperClass = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            logger.warning(f"Scraper {module_name}.{class_name} není dostupný: {e}")
            continue

        scraper = ScraperClass()
        try:
            for model in MODELS:
                # Použij první search term jako primární query
                query = model.search_terms[0]
                logger.info(f"[{ScraperClass.__name__}] scrape: {query}")
                try:
                    counts = scraper.scrape_query(
                        query, mark_missing_inactive=False
                    )
                    logger.info(
                        f"[{ScraperClass.__name__}] {query}: "
                        f"found={counts['found']} new={counts['new']} updated={counts['updated']}"
                    )
                except Exception as e:
                    total_failures += 1
                    logger.error(
                        f"[{ScraperClass.__name__}] {query} selhalo: {e}\n"
                        f"{traceback.format_exc()}"
                    )
        finally:
            scraper.close()

    logger.info(f"scrape-all hotovo, selhání: {total_failures}")
    return 0 if total_failures == 0 else 1


def cmd_analyze() -> int:
    """Přepočítá normalize → condition → fair value → arbitrage → counterfeit risk."""
    failures = 0
    try:
        from src.analysis.normalize import classify_all_listings
        n = classify_all_listings()
        logger.info(f"normalize: zpracováno {n} inzerátů")
    except (ImportError, AttributeError) as e:
        logger.warning(f"normalize neimplementován: {e}")
        failures += 1

    try:
        from src.analysis.conditions import score_all_listings
        n = score_all_listings()
        logger.info(f"condition: zpracováno {n} inzerátů")
    except (ImportError, AttributeError) as e:
        logger.warning(f"conditions neimplementován: {e}")
        failures += 1

    try:
        from src.analysis.pricing import update_all_arbitrage_scores
        n = update_all_arbitrage_scores()
        logger.info(f"pricing: aktualizováno {n} inzerátů")
    except (ImportError, AttributeError) as e:
        logger.warning(f"pricing neimplementován: {e}")
        failures += 1

    try:
        from src.analysis.authenticity import update_all_counterfeit_risks
        n = update_all_counterfeit_risks()
        logger.info(f"authenticity: zpracováno {n} inzerátů")
    except (ImportError, AttributeError) as e:
        logger.warning(f"authenticity neimplementován: {e}")
        failures += 1

    return 0 if failures == 0 else 1


def cmd_refresh() -> int:
    """Hlavní cron příkaz: scrape všeho + analyze."""
    started = datetime.now(timezone.utc)
    logger.info(f"=== REFRESH START {started.isoformat()} ===")

    rc_scrape = cmd_scrape_all()
    rc_analyze = cmd_analyze()

    finished = datetime.now(timezone.utc)
    duration = (finished - started).total_seconds()
    logger.info(f"=== REFRESH HOTOVO za {duration:.1f}s, rc={rc_scrape | rc_analyze} ===")
    return rc_scrape | rc_analyze


def cmd_stats() -> int:
    from src.db import stats
    stats()
    return 0


COMMANDS = {
    "init": cmd_init,
    "scrape-all": cmd_scrape_all,
    "analyze": cmd_analyze,
    "refresh": cmd_refresh,
    "stats": cmd_stats,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python -m src.cli <command>")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        return 1
    # Init DB před každým příkazem (idempotentní, řeší cold start na Railway)
    from src.db import init_db
    init_db()
    return COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())

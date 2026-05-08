"""Integration test pro LuxuryBagsScraper.parse_listing nad uloženým fixture."""

from pathlib import Path

import pytest

from src.scrapers.luxurybags import LuxuryBagsScraper

FIXTURE_DIR = Path(__file__).parent / "fixtures"
ALMA_BB_FIXTURE = FIXTURE_DIR / "luxurybags_alma_bb.html"
ALMA_BB_URL = (
    "https://www.luxurybags.cz/kabelky/82488-louis-vuitton-alma-bb-monogram-canvas-bag"
)


@pytest.fixture(scope="module")
def parsed_alma_bb() -> dict:
    scraper = LuxuryBagsScraper()
    try:
        html = ALMA_BB_FIXTURE.read_text(encoding="utf-8")
        result = scraper.parse_listing(ALMA_BB_URL, html)
    finally:
        scraper.close()
    assert result is not None, "parse_listing vrátil None pro validní inzerát"
    return result


def test_source_id_from_url(parsed_alma_bb):
    assert parsed_alma_bb["source_id"] == "82488"


def test_url_passthrough(parsed_alma_bb):
    assert parsed_alma_bb["url"] == ALMA_BB_URL


def test_title_and_model_raw(parsed_alma_bb):
    assert parsed_alma_bb["title"] == "Louis Vuitton - Alma BB Monogram Canvas Bag"
    # Brand prefix se odstřihne — model_raw je samotný produkt
    assert parsed_alma_bb["model_raw"] == "Alma BB Monogram Canvas Bag"


def test_price_czk(parsed_alma_bb):
    # JSON-LD: "price": 23990, "priceCurrency": "CZK"
    assert parsed_alma_bb["price_czk"] == 23990
    assert parsed_alma_bb["price_original"] == 23990
    assert parsed_alma_bb["currency"] == "CZK"


def test_photo_urls(parsed_alma_bb):
    photos = parsed_alma_bb["photo_urls"]
    assert isinstance(photos, list)
    assert len(photos) >= 5
    assert all(p.startswith("https://www.luxurybags.cz/foto/") for p in photos)


def test_condition_raw_is_grade_letter(parsed_alma_bb):
    # Tato kabelka má grade "B" v SVG — luxurybags grading: A⁺/A/B/C
    assert parsed_alma_bb["condition_raw"] == "B"


def test_description_contains_specs(parsed_alma_bb):
    desc = parsed_alma_bb["description"]
    assert desc, "description je prázdný"
    # Klíčová specifická pole se musí dostat do popisu
    assert "Patina" in desc          # Nedostatky
    assert "FL1150" in desc          # Sériové číslo
    assert "25 x 19 x 12" in desc    # Rozměry
    assert "monogram canvas" in desc.lower()


def test_no_personal_data_in_output(parsed_alma_bb):
    # GDPR sanity check: parser nesmí vracet pole se jménem/telefonem/emailem
    forbidden_keys = {"seller_name", "seller_phone", "seller_email", "phone", "email"}
    assert not (forbidden_keys & set(parsed_alma_bb.keys()))


def test_unrelated_currency_returns_none():
    """Listing s jinou měnou než CZK/EUR → None (nevíme, jak konvertovat)."""
    html = """
    <html><body>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"X","identifier":"1",
     "image":[],"offers":{"@type":"Offer","price":100,"priceCurrency":"USD"}}
    </script>
    </body></html>
    """
    scraper = LuxuryBagsScraper()
    try:
        assert scraper.parse_listing("https://www.luxurybags.cz/kabelky/1-x", html) is None
    finally:
        scraper.close()


def test_missing_price_returns_none():
    """Inzerát bez ceny ('dohodou' ekvivalent) → None."""
    html = """
    <html><body>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"X","identifier":"1",
     "image":[],"offers":{"@type":"Offer","priceCurrency":"CZK"}}
    </script>
    </body></html>
    """
    scraper = LuxuryBagsScraper()
    try:
        assert scraper.parse_listing("https://www.luxurybags.cz/kabelky/1-x", html) is None
    finally:
        scraper.close()


def test_eur_price_converts_to_czk():
    html = """
    <html><body>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product",
     "name":"Brand - Bag","identifier":"42","image":[],
     "offers":{"@type":"Offer","price":1000,"priceCurrency":"EUR"}}
    </script>
    </body></html>
    """
    scraper = LuxuryBagsScraper()
    try:
        result = scraper.parse_listing("https://www.luxurybags.cz/kabelky/42-bag", html)
    finally:
        scraper.close()
    assert result is not None
    assert result["currency"] == "EUR"
    assert result["price_original"] == 1000
    assert result["price_czk"] == 25000  # konverze 25.0

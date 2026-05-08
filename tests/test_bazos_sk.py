"""Integration test pro BazosSkScraper.parse_listing."""

from pathlib import Path

import pytest

from src.scrapers.bazos_sk import BazosSkScraper

FIXTURE = Path(__file__).parent / "fixtures" / "bazos_sk_lv.html"
URL = "https://oblecenie.bazos.sk/inzerat/191318943/louis-vuitton.php"


@pytest.fixture(scope="module")
def parsed() -> dict:
    s = BazosSkScraper()
    try:
        r = s.parse_listing(URL, FIXTURE.read_text(encoding="utf-8"))
    finally:
        s.close()
    assert r is not None
    return r


def test_source_id(parsed):
    assert parsed["source_id"] == "191318943"


def test_title(parsed):
    assert parsed["title"] == "Louis Vuitton"


def test_eur_price_converted(parsed):
    # Detail uvádí "125 €" -> CZK přepočet kurzem 25.0
    assert parsed["currency"] == "EUR"
    assert parsed["price_original"] == 125
    assert parsed["price_czk"] == 3125


def test_location(parsed):
    # "811 01 Bratislava" -> "Bratislava"
    assert parsed["location"] == "Bratislava"


def test_posted_at(parsed):
    # "[9.5. 2026]"
    assert parsed["posted_at"] == "2026-05-09"


def test_no_personal_data(parsed):
    forbidden = {"seller_name", "seller_phone", "seller_email", "phone", "email", "name"}
    assert not (forbidden & set(parsed.keys()))


def test_dohodou_returns_none():
    html = """
    <html><body>
    <h1 class="nadpisdetail">Test</h1>
    <table><tr><td>Cena:</td><td>Dohodou</td></tr></table>
    </body></html>
    """
    s = BazosSkScraper()
    try:
        r = s.parse_listing("https://oblecenie.bazos.sk/inzerat/1/x.php", html)
    finally:
        s.close()
    assert r is None


def test_inherits_iter_listing_urls():
    """SK scraper dědí extraktor URL z CZ verze."""
    html = '<a href="/inzerat/999/foo.php">x</a>'
    hrefs = list(BazosSkScraper._extract_listing_hrefs(html))
    assert "/inzerat/999/foo.php" in hrefs

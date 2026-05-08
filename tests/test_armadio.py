"""Integration test pro ArmadioScraper.parse_listing."""

from pathlib import Path

import pytest

from src.scrapers.armadio import ArmadioScraper

FIXTURE = Path(__file__).parent / "fixtures" / "armadio_alma_pm.html"
URL = "https://www.armadio.cz/louis-vuitton-alma-pm-amarante-monogram-vernis-bag/"


@pytest.fixture(scope="module")
def parsed() -> dict:
    scraper = ArmadioScraper()
    try:
        result = scraper.parse_listing(URL, FIXTURE.read_text(encoding="utf-8"))
    finally:
        scraper.close()
    assert result is not None
    return result


def test_source_id(parsed):
    assert parsed["source_id"] == "31307"


def test_url(parsed):
    assert parsed["url"] == URL


def test_title_and_model_raw(parsed):
    assert parsed["title"] == "LOUIS VUITTON Alma PM Amarante Monogram Vernis Bag"
    assert parsed["model_raw"] == "Alma PM Amarante Monogram Vernis Bag"


def test_price(parsed):
    # itemprop=price is "29890.00", currency CZK
    assert parsed["price_czk"] == 29890
    assert parsed["price_original"] == 29890
    assert parsed["currency"] == "CZK"


def test_photos_filtered_to_this_product(parsed):
    photos = parsed["photo_urls"]
    assert len(photos) >= 5
    # All photos must reference productID 31307 (otherwise they're related products)
    assert all("31307-" in p for p in photos)


def test_condition_raw(parsed):
    # Description contains "Stav celkového opotřebení : velmi dobrý"
    assert parsed["condition_raw"] == "velmi dobrý"


def test_description_has_specs(parsed):
    desc = parsed["description"]
    assert desc
    assert "kůže vernis" in desc
    assert "32 x 25 x 16" in desc


def test_no_personal_data(parsed):
    forbidden = {"seller_name", "seller_phone", "seller_email", "phone", "email"}
    assert not (forbidden & set(parsed.keys()))


def test_eur_conversion():
    html = """
    <html><body>
    <h1>Test - Bag</h1>
    <meta itemprop="price" content="500.00">
    <meta itemprop="priceCurrency" content="EUR">
    <meta itemprop="productID" content="42">
    </body></html>
    """
    s = ArmadioScraper()
    try:
        r = s.parse_listing("https://www.armadio.cz/test-bag/", html)
    finally:
        s.close()
    assert r["currency"] == "EUR"
    assert r["price_original"] == 500
    assert r["price_czk"] == 12500


def test_no_price_returns_none():
    html = "<html><body><h1>X</h1></body></html>"
    s = ArmadioScraper()
    try:
        assert s.parse_listing("https://www.armadio.cz/x/", html) is None
    finally:
        s.close()

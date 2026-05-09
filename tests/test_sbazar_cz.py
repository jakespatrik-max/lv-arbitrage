"""Integration test pro SbazarCzScraper."""

from pathlib import Path

import pytest

from src.scrapers.sbazar_cz import SbazarCzScraper

FIXTURE = Path(__file__).parent / "fixtures" / "sbazar_cz_lv.html"
URL = "https://www.sbazar.cz/inzerat/230408402-louis-vuitton-kabelka"


@pytest.fixture(scope="module")
def parsed() -> dict:
    s = SbazarCzScraper()
    try:
        r = s.parse_listing(URL, FIXTURE.read_text(encoding="utf-8"))
    finally:
        s.close()
    assert r is not None
    return r


def test_source_id(parsed):
    assert parsed["source_id"] == "230408402"


def test_title(parsed):
    assert parsed["title"] == "Louis Vuitton kabelka"


def test_price_czk(parsed):
    assert parsed["price_czk"] == 1800
    assert parsed["price_original"] == 1800
    assert parsed["currency"] == "CZK"


def test_description(parsed):
    desc = parsed["description"].lower()
    assert "kabelk" in desc
    assert "25x13x4" in desc


def test_location_strips_v_prefix(parsed):
    # 'v Orlová' -> 'Orlová'
    assert parsed["location"] == "Orlová"


def test_posted_at_iso(parsed):
    assert parsed["posted_at"] == "2026-05-08"


def test_condition_raw_used(parsed):
    # itemCondition: schema.org/UsedCondition -> "použité"
    assert parsed["condition_raw"] == "použité"


def test_photos_present(parsed):
    photos = parsed["photo_urls"]
    assert len(photos) >= 1
    assert all("sdn.cz" in p for p in photos)


def test_no_personal_data(parsed):
    """JSON-LD obsahuje seller.name="Fashion :)" — parser to NESMÍ propustit."""
    forbidden = {"seller_name", "seller_phone", "seller_email", "phone", "email", "name"}
    assert not (forbidden & set(parsed.keys()))
    for v in parsed.values():
        if isinstance(v, str):
            assert "Fashion :)" not in v


def test_listing_url_regex():
    html = '<a href="/inzerat/123-foo">x</a><a href="/jine">no</a>'
    hrefs = list(SbazarCzScraper._extract_listing_hrefs(html))
    assert hrefs == ["/inzerat/123-foo"]

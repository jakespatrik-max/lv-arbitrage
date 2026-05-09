"""Integration test pro BazarSkScraper."""

from pathlib import Path

import pytest

from src.scrapers.bazar_sk import BazarSkScraper

FIXTURE = Path(__file__).parent / "fixtures" / "bazar_sk_lv.html"
URL = "https://oblecenie.bazar.sk/39482067-damska-kabelka-lv/"


@pytest.fixture(scope="module")
def parsed() -> dict:
    s = BazarSkScraper()
    try:
        r = s.parse_listing(URL, FIXTURE.read_text(encoding="utf-8"))
    finally:
        s.close()
    assert r is not None
    return r


def test_source_id_from_info_block(parsed):
    # info block: "(č. 39482067)"
    assert parsed["source_id"] == "39482067"


def test_title(parsed):
    assert parsed["title"] == "Dámska kabelka LV"


def test_eur_to_czk(parsed):
    # JSON-LD: price 45, EUR -> 1125 CZK
    assert parsed["currency"] == "EUR"
    assert parsed["price_original"] == 45
    assert parsed["price_czk"] == 1125


def test_description(parsed):
    desc = parsed["description"].lower()
    # "Predám novu dámsku kabelku dlzka kabelky..."
    assert "kabelk" in desc


def test_location(parsed):
    assert parsed["location"] == "Topoľčany"


def test_posted_at(parsed):
    # "z dňa 26. 4. 2026"
    assert parsed["posted_at"] == "2026-04-26"


def test_condition_raw(parsed):
    # "Stav: nové"
    assert parsed["condition_raw"] == "nové"


def test_photos_present_and_deduped(parsed):
    photos = parsed["photo_urls"]
    assert len(photos) >= 1
    # All URLs from unitedclassifieds; no duplicates by image hash
    import re
    hashes = []
    for p in photos:
        m = re.search(r"/foto/[^/]+/([^?#]+)", p)
        assert m, f"unexpected photo URL: {p}"
        hashes.append(m.group(1))
    assert len(hashes) == len(set(hashes)), "photo deduplication failed"


def test_no_personal_data(parsed):
    # JSON-LD obsahuje seller.name="Zara0603" — parser to NESMÍ propustit
    forbidden = {"seller_name", "seller_phone", "seller_email", "phone", "email", "name"}
    assert not (forbidden & set(parsed.keys()))
    # Ani v žádné string hodnotě (kontrola s seller name)
    for v in parsed.values():
        if isinstance(v, str):
            assert "Zara0603" not in v


def test_no_price_returns_none():
    html = """<html><body>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"X",
     "offers":{"@type":"Offer","priceCurrency":"EUR"}}
    </script></body></html>"""
    s = BazarSkScraper()
    try:
        assert s.parse_listing("https://oblecenie.bazar.sk/1-x/", html) is None
    finally:
        s.close()

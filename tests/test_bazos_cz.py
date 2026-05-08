"""Integration test pro BazosCzScraper.parse_listing."""

from pathlib import Path

import pytest

from src.scrapers.bazos_cz import BazosCzScraper

FIXTURE = Path(__file__).parent / "fixtures" / "bazos_cz_lv.html"
URL = "https://obleceni.bazos.cz/inzerat/218515298/louis-vuitton-tasky.php"


@pytest.fixture(scope="module")
def parsed() -> dict:
    s = BazosCzScraper()
    try:
        r = s.parse_listing(URL, FIXTURE.read_text(encoding="utf-8"))
    finally:
        s.close()
    assert r is not None
    return r


def test_source_id(parsed):
    assert parsed["source_id"] == "218515298"


def test_title(parsed):
    assert parsed["title"] == "Louis vuitton tasky"


def test_price_400(parsed):
    # "400 Kč" — pod prahem 1000, ale parser pokračuje (jen warning)
    assert parsed["price_czk"] == 400
    assert parsed["price_original"] == 400
    assert parsed["currency"] == "CZK"


def test_description(parsed):
    assert "super stav" in parsed["description"].lower()


def test_location_psc_stripped(parsed):
    # "180 00 Praha 8" -> "Praha 8" (PSČ se odstřihne)
    assert parsed["location"] == "Praha 8"


def test_posted_at(parsed):
    # nadpis: "- [7.5. 2026]" -> 2026-05-07
    assert parsed["posted_at"] == "2026-05-07"


def test_photos_full_size(parsed):
    photos = parsed["photo_urls"]
    assert len(photos) >= 1
    # Žádný thumbnail (nesmí být 't' v segmentu)
    for p in photos:
        assert "/img/" in p
        assert "218515298" in p
        # vzor: /img/{N}/{seg}/{ID}.jpg, NE /img/{N}t/{seg}/...
        import re
        assert re.search(r"/img/\d+t/", p) is None, f"thumbnail leaked: {p}"


def test_no_personal_data(parsed):
    """GDPR: parser NESMÍ vrátit jméno prodávajícího ani telefon."""
    forbidden_keys = {"seller_name", "seller_phone", "seller_email", "phone", "email", "name"}
    leaked = forbidden_keys & set(parsed.keys())
    assert not leaked, f"Personal data leaked into output: {leaked}"
    # A taky ať se v description neodkáže Boris/724 (heuristika — title má "vuitton"
    # což je OK; ale Boris ne)
    desc = (parsed["description"] or "").lower()
    assert "boris" not in desc
    assert "724" not in desc


def test_dohodou_returns_none():
    html = """
    <html><body>
    <div class="inzeratydetnadpis"><h1 class="nadpisdetail">Test</h1></div>
    <table><tr><td>Cena:</td><td><b>Dohodou</b></td></tr></table>
    </body></html>
    """
    s = BazosCzScraper()
    try:
        r = s.parse_listing("https://obleceni.bazos.cz/inzerat/123/test.php", html)
    finally:
        s.close()
    assert r is None


def test_no_price_returns_none():
    html = """
    <html><body>
    <div class="inzeratydetnadpis"><h1 class="nadpisdetail">Test</h1></div>
    </body></html>
    """
    s = BazosCzScraper()
    try:
        r = s.parse_listing("https://obleceni.bazos.cz/inzerat/123/test.php", html)
    finally:
        s.close()
    assert r is None


def test_iter_listing_urls_extracts_inzeraty():
    html = """
    <html><body>
    <a href="/inzerat/111/foo.php">A</a>
    <a href="/inzerat/222/bar.php">B</a>
    <a href="/jinak/333">skip</a>
    <a href="https://obleceni.bazos.cz/inzerat/444/baz.php">C</a>
    </body></html>
    """
    hrefs = list(BazosCzScraper._extract_listing_hrefs(html))
    assert "/inzerat/111/foo.php" in hrefs
    assert "/inzerat/222/bar.php" in hrefs
    assert "https://obleceni.bazos.cz/inzerat/444/baz.php" in hrefs
    assert all("/jinak/" not in h for h in hrefs)

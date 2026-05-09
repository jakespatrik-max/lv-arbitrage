"""Testy heuristiky padělků."""

import pytest

from src.analysis.authenticity import compute_counterfeit_risk
from src.config import COUNTERFEIT_SIGNALS


def _base(**kwargs):
    base = {
        "title": "Louis Vuitton Alma BB Monogram",
        "description": "Pěkná kabelka, certifikát, prachovka, dust bag, data code FL1150.",
        "price_czk": 22000,
        "fair_value_czk": 22000,
        "photo_urls": ["a.jpg", "b.jpg", "c.jpg", "d.jpg"],
    }
    base.update(kwargs)
    return base


def test_clean_listing_has_low_risk():
    risk = compute_counterfeit_risk(_base())
    assert 0.0 <= risk < 0.30, f"clean listing risk too high: {risk}"


def test_replica_keyword_dominates():
    risk = compute_counterfeit_risk(_base(description="Krásná 1:1 replika"))
    assert risk >= COUNTERFEIT_SIGNALS["description_has_replica_words"]
    assert risk > 0.50


@pytest.mark.parametrize("word", ["replika", "1:1", "kopie", "fake", "imitace", "napodobenina"])
def test_each_replica_keyword_triggers(word):
    risk = compute_counterfeit_risk(_base(description=f"Mám tu {word} kabelky"))
    assert risk >= COUNTERFEIT_SIGNALS["description_has_replica_words"]


def test_multi_brand_mentions_increase_risk():
    desc = "Prodávám Louis Vuitton, Chanel, Dior, Gucci a Prada za super ceny"
    risk = compute_counterfeit_risk(_base(description=desc))
    assert risk >= COUNTERFEIT_SIGNALS["seller_has_multi_brands"]


def test_price_below_30pct_increases_risk():
    risk = compute_counterfeit_risk(_base(price_czk=5000, fair_value_czk=22000))
    assert risk >= COUNTERFEIT_SIGNALS["price_below_30pct_fair"]


def test_price_below_15pct_doubles_signal():
    risk_15 = compute_counterfeit_risk(_base(price_czk=2000, fair_value_czk=22000))
    risk_30 = compute_counterfeit_risk(_base(price_czk=5000, fair_value_czk=22000))
    assert risk_15 > risk_30


def test_few_photos():
    risk = compute_counterfeit_risk(_base(photo_urls=["only-one.jpg"]))
    assert risk >= COUNTERFEIT_SIGNALS["few_photos"]


def test_photo_urls_as_json_string():
    """DB vrací photo_urls jako JSON string — počítadlo to musí umět."""
    risk_str = compute_counterfeit_risk(_base(photo_urls='["a.jpg","b.jpg","c.jpg","d.jpg"]'))
    risk_list = compute_counterfeit_risk(_base(photo_urls=["a.jpg", "b.jpg", "c.jpg", "d.jpg"]))
    assert risk_str == pytest.approx(risk_list, abs=1e-9)


def test_clamped_to_unit_interval():
    risk = compute_counterfeit_risk(
        _base(
            description="1:1 replika kopie fake imitace; mám i Chanel, Dior, Gucci, Prada, Hermes",
            price_czk=100,
            fair_value_czk=22000,
            photo_urls=[],
        )
    )
    assert 0.0 <= risk <= 1.0


def test_strongest_signal_is_replica_words():
    """Per brief: replica keywords musí mít nejsilnější váhu."""
    weights = COUNTERFEIT_SIGNALS
    assert weights["description_has_replica_words"] >= weights["seller_has_multi_brands"]
    assert weights["description_has_replica_words"] >= weights["price_below_30pct_fair"]

"""Testy pro score_condition."""

import pytest

from src.analysis.conditions import score_condition


@pytest.mark.parametrize(
    "text,expected_label",
    [
        ("Zcela nová kabelka s visačkou", "new"),
        ("Jako nová, nošená 2x", "like_new"),
        ("Ako nová", "like_new"),
        ("Výborný stav, žádné vady", "excellent"),
        ("Skvelý stav", "excellent"),
        ("Velmi dobrý stav, lehké známky používání", "very_good"),
        ("Dobrý stav, znamky pouzivani na rohách", "good"),
        ("Patina na rožkách, jinak v pořádku", "fair"),
        ("Odřené rohy a škrábance", "fair"),
        ("Poškozená — utržené ucho", "poor"),
        ("Vada na zipu", "poor"),
    ],
)
def test_classification(text, expected_label):
    label, _score = score_condition(text)
    assert label == expected_label


def test_default_when_no_match():
    label, score = score_condition("kabelka s krásným potiskem")
    assert label == "good"
    assert score == 0.60


def test_empty_returns_default():
    assert score_condition(None) == ("good", 0.60)
    assert score_condition("") == ("good", 0.60)


def test_specific_beats_generic():
    """'jako nová' nesmí být klasifikovaná jen jako 'new' (= 1.00)."""
    label, score = score_condition("Krásná, jako nová kabelka")
    assert label == "like_new"
    assert score == 0.95


def test_negative_keyword_dominates_positive():
    """Pokud je v textu i 'krásná' i 'praskliny', praskliny vyhrávají."""
    label, _ = score_condition("Krásná, ale jsou tam praskliny")
    assert label == "fair"


def test_score_in_valid_range():
    for text in ["nová", "jako nová", "dobrá", "patina", "rozbitá", "blabla"]:
        label, score = score_condition(text)
        assert 0.0 <= score <= 1.0
        assert label in {"new", "like_new", "excellent", "very_good", "good", "fair", "poor"}

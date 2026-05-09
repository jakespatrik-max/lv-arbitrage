"""Testy pro classify_model."""

import pytest

from src.analysis.normalize import classify_model


@pytest.mark.parametrize(
    "title,desc,expected",
    [
        ("Louis Vuitton Alma BB Monogram", "", "alma_bb"),
        ("ALMA BB v perfektním stavu", "", "alma_bb"),
        ("Krásná Alma-BB", None, "alma_bb"),
        ("LV Speedy 25", "", "speedy_25"),
        ("Speedy25 Damier Ebene", "", "speedy_25"),
        ("LV Pochette Métis Monogram", "", "pochette_metis"),
        ("Pochette Metis (bez háčků)", None, "pochette_metis"),
        ("Capucines BB Taurillon", "", "capucines_bb"),
        ("Neverfull MM Damier", "", "neverfull_mm"),
    ],
)
def test_classify_positive(title, desc, expected):
    assert classify_model(title, desc) == expected


def test_alma_bb_wins_over_alma_in_balmain():
    """`balmain` nesmí matchnout `alma` — word boundary."""
    assert classify_model("Balmain torba", None) is None


def test_specific_beats_generic():
    """alma bb musí matchnout dřív než samotná 'alma' (ale my v configu
    sledujeme jen alma bb, takže fallback na 'alma' není)."""
    # title obsahuje obě, ale 'alma bb' je delší a vyhrá
    assert classify_model("Alma BB Monogram (NE alma 35)", None) == "alma_bb"


def test_title_wins_over_description():
    """Title se prohledává PŘED description."""
    assert (
        classify_model("Speedy 25 Monogram", "Možná i alma bb v popisu") == "speedy_25"
    )


def test_no_match_returns_none():
    assert classify_model("Random fashion bag", None) is None
    assert classify_model("", "") is None
    assert classify_model(None, None) is None


def test_falls_through_to_description():
    assert classify_model("Krásná kabelka", "Jedná se o Alma BB Monogram") == "alma_bb"

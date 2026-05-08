"""
Konfigurace projektu: sledované modely, retail ceny, scraping pravidla.

DŮLEŽITÉ: Retail ceny aktualizuj ručně cca 2× ročně z lv.com.
Fair value se počítá dynamicky z dat sebraných z profi obchodů (Luxurybags,
Armadio), retail je jen sanity check.
"""

from dataclasses import dataclass, field

# ============================================================================
# SLEDOVANÉ MODELY
# ============================================================================
# Každý model má:
# - normalized_id: stabilní ID pro DB
# - search_terms: seznam variant pro fulltext vyhledávání ve scrape výsledcích
# - retail_eur: aktuální retail z lv.com (ručně, květen 2026)
# - canvas_variants: které materiály sledujeme (Monogram, Damier Ebene...)


@dataclass
class Model:
    normalized_id: str
    display_name: str
    search_terms: list[str]
    retail_eur: int
    canvas_variants: list[str] = field(default_factory=list)

    @property
    def retail_czk(self) -> int:
        # Hrubá konverze; pro přesné porovnání použij aktuální kurz z DB
        return int(self.retail_eur * 25)


MODELS: list[Model] = [
    Model(
        normalized_id="alma_bb",
        display_name="Alma BB",
        search_terms=["alma bb", "alma-bb", "alma bb monogram", "alma bb damier"],
        retail_eur=1990,
        canvas_variants=["Monogram", "Damier Ebene", "Epi", "Vernis"],
    ),
    Model(
        normalized_id="speedy_25",
        display_name="Speedy 25",
        search_terms=["speedy 25", "speedy25", "speedy bandouliere 25"],
        retail_eur=1750,
        canvas_variants=["Monogram", "Damier Ebene", "Damier Azur"],
    ),
    Model(
        normalized_id="neverfull_mm",
        display_name="Neverfull MM",
        search_terms=["neverfull mm", "neverfull", "never full mm"],
        retail_eur=2000,
        canvas_variants=["Monogram", "Damier Ebene", "Damier Azur"],
    ),
    Model(
        normalized_id="pochette_metis",
        display_name="Pochette Métis",
        search_terms=["pochette metis", "pochette métis", "metis pochette"],
        retail_eur=2350,
        canvas_variants=["Monogram", "Monogram Reverse", "Empreinte"],
    ),
    Model(
        normalized_id="capucines_bb",
        display_name="Capucines BB",
        search_terms=["capucines bb", "capucines mini", "capucines"],
        retail_eur=6300,
        canvas_variants=["Taurillon"],
    ),
]

MODEL_BY_ID = {m.normalized_id: m for m in MODELS}


# ============================================================================
# SCRAPING ZDROJE
# ============================================================================

SOURCES = {
    # C2C — kde hledáme arbitráž
    "bazos_cz": {
        "type": "c2c",
        "base_url": "https://obleceni.bazos.cz",
        "search_url_tmpl": "https://obleceni.bazos.cz/inzeraty/{query_slug}/",
        "rate_limit_seconds": 2.5,
        "robots_check": True,
    },
    "bazos_sk": {
        "type": "c2c",
        "base_url": "https://oblecenie.bazos.sk",
        "search_url_tmpl": "https://oblecenie.bazos.sk/inzeraty/{query_slug}/",
        "rate_limit_seconds": 2.5,
        "robots_check": True,
    },
    "bazar_sk": {
        "type": "c2c",
        "base_url": "https://oblecenie.bazar.sk",
        "search_url_tmpl": "https://oblecenie.bazar.sk/kabelky/{query_slug}/predaj/",
        "rate_limit_seconds": 2.5,
        "robots_check": True,
    },
    "sbazar_cz": {
        "type": "c2c",
        "base_url": "https://www.sbazar.cz",
        "search_url_tmpl": "https://www.sbazar.cz/hledej/{query_slug}",
        "rate_limit_seconds": 2.5,
        "robots_check": True,
    },
    # Profi obchody — referenční ceny pro výpočet fair value
    "luxurybags": {
        "type": "professional",
        "base_url": "https://www.luxurybags.cz",
        "search_url_tmpl": "https://www.luxurybags.cz/vyhledavani?q={query}",
        "rate_limit_seconds": 3.0,
        "robots_check": True,
    },
    "armadio": {
        "type": "professional",
        "base_url": "https://www.armadio.cz",
        "search_url_tmpl": "https://www.armadio.cz/vyhledavani?q={query}",
        "rate_limit_seconds": 3.0,
        "robots_check": True,
    },
}


# ============================================================================
# OCEŇOVACÍ MODEL
# ============================================================================

# Multiplikátory pro výpočet fair value podle stavu (vůči profi obchodu, kde
# bývá průměrně "Excellent" stav).
CONDITION_MULTIPLIERS = {
    "new": 1.10,         # nová, s tagy → prémie nad běžnou tržní cenu
    "like_new": 1.00,
    "excellent": 0.92,
    "very_good": 0.80,
    "good": 0.65,
    "fair": 0.45,
    "poor": 0.25,
}

# Pokud cena inzerátu < (fair_value × ARBITRAGE_THRESHOLD), je to kandidát
# na arbitráž
ARBITRAGE_THRESHOLD = 0.70   # = 30 % pod fair value

# Pokud counterfeit_risk > tato hodnota, kandidáta neukazujeme jako příležitost
# (nemá smysl kupovat něco, co je z 80 % padělek, i když je to laciné)
MAX_COUNTERFEIT_RISK_FOR_ALERT = 0.55


# ============================================================================
# HEURISTIKY PRO RIZIKO PADĚLKU
# ============================================================================
# Každý faktor přispívá k counterfeit_risk skóre 0.0–1.0
COUNTERFEIT_SIGNALS = {
    "price_below_30pct_fair": 0.40,       # extrémně nízká cena
    "price_below_15pct_fair": 0.25,       # ještě extrémnější
    "no_data_code_mentioned": 0.10,
    "no_dust_bag_mentioned": 0.05,
    "seller_has_multi_brands": 0.20,      # "Chanel, Dior, LV za 50 %"
    "description_has_replica_words": 0.50, # "1:1", "kopie", "replika", "fake"
    "few_photos": 0.10,                    # < 3 fotky
    "stock_photos_only": 0.15,             # generické / produktové fotky
    "no_proof_of_purchase": 0.05,
}

REPLICA_KEYWORDS = [
    "1:1", "replika", "kopie", "kopia", "kópia",
    "fake", "imitace", "napodobenina", "napodobeniny",
    "z albanska", "albansko",
]


# ============================================================================
# SCRAPING ETIKETA
# ============================================================================

USER_AGENT = "lv-arbitrage-research/0.1 (personal research project; respects robots.txt)"

# Maximum stránek per scrape run (kvůli respektu k serveru)
MAX_PAGES_PER_RUN = 10

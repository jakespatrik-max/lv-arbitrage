# Claude Code: LV Arbitrage — Hlavní brief

## Co stavíme

Webová aplikace inspirovaná **Jakes.Estate** (https://prague-reality-scraper-production.up.railway.app/), ale pro **luxusní kabelky** místo nemovitostí. Stejná logika: monitoring sekundárního trhu, výpočet férové tržní hodnoty, vyhledávání podhodnocených inzerátů, oblíbené/vyřazené, deal scoring.

**Top 5 sledovaných modelů LV:** Alma BB, Speedy 25, Neverfull MM, Pochette Métis, Capucines BB.
**Sledované zdroje (CZ/SK):** Bazoš CZ/SK, Bazar.sk, Sbazar.cz (C2C — kde se hledá arbitráž), Luxurybags.cz, Armadio.cz (profi obchody — kde se počítá fair value).

## Architektura (zafixovaná)

- **Backend:** FastAPI (`src/api/app.py`) — JSON API + servíruje statický frontend
- **Frontend:** Vanilla JS + CSS v `static/` — žádný build step
- **DB:** SQLite na Railway persistent volume (`/data/listings.db` v produkci, `./data/listings.db` lokálně)
- **Scraping:** Daily Railway cron job spouští `python -m src.cli refresh`
- **Deploy:** Railway, jeden web service s persistent volume

## Hotové (HOTOVO, nesahej na to bez důvodu)

| Soubor | Co dělá |
|---|---|
| `src/config.py` | 5 modelů LV, retail ceny, definice zdrojů, oceňovací parametry |
| `src/db.py` | SQLite schema, upsert s historií cen, GDPR-clean (žádná osobní data) |
| `src/scrapers/base.py` | Abstraktní BaseScraper s rate limitingem, retry, robots.txt |
| `src/cli.py` | CLI orchestrace: init / scrape-all / analyze / refresh / stats |
| `src/api/app.py` | FastAPI s `/health`, `/api/meta`, `/api/listings`, `/api/listings/{id}`, `/api/stats`, favorite/dismiss endpointy |
| `static/index.html` + `app.js` + `styles.css` | Funkční SPA — taby, sortování, filtry, karty, modal s detailem, oblíbené/vyřazené |
| `Procfile`, `railway.toml`, `runtime.txt` | Railway deploy |

## Co implementuješ TY

### Úkol 1: Implementuj scrapery
Pořadí (od nejjednoduššího po nejtěžší):

1. **`src/scrapers/luxurybags.py`** (LuxuryBagsScraper) — profi obchod, čisté HTML, klíčové pro fair value benchmark
2. **`src/scrapers/armadio.py`** (ArmadioScraper) — profi obchod
3. **`src/scrapers/bazos_cz.py`** (BazosCzScraper) — C2C, hodně inzerátů
4. **`src/scrapers/bazos_sk.py`** (BazosSkScraper) — stejná struktura jako CZ
5. **`src/scrapers/bazar_sk.py`** (BazarSkScraper)
6. **`src/scrapers/sbazar_cz.py`** (SbazarCzScraper)

Každý scraper dědí od `BaseScraper` (`src/scrapers/base.py`) a implementuje pouze:
- `iter_listing_urls(query: str) -> Iterator[str]` — generuje URL detailů
- `parse_listing(url: str, html: str) -> dict | None` — vrací dict pro `upsert_listing`

`source_id` musí odpovídat klíči v `SOURCES` v `config.py`.

**Co musí parser vrátit:**
```python
{
    "source_id": "12345678",          # ID z URL inzerátu
    "url": url,
    "title": "...",
    "description": "...",             # plný text popisu
    "price_czk": 22000,               # int, vždy CZK (z EUR konverze 25.0)
    "price_original": 22000,
    "currency": "CZK",                # nebo "EUR"
    "model_raw": "Alma BB Monogram",  # syrový název z inzerátu
    "condition_raw": "skoro nová",    # text stavu, pokud uveden
    "photo_urls": ["https://...", ...],
    "location": "Praha",
    "posted_at": "2026-05-01",        # ISO datum, pokud lze parsnout
}
```

**Pozor:**
- Inzeráty bez ceny ("dohodou") — přeskočit (return None).
- Inzeráty s cenou < 1000 Kč — pravděpodobně pouzdro/peněženka, ne kabelka. Logni varování ale pokračuj (může to být zajímavá nabídka).
- `model_normalized`, `condition_score`, `fair_value_czk`, `arbitrage_pct`, `counterfeit_risk` NEPOČÍTEJ ve scraperu — tyhle počítá analyze fáze.

### Úkol 2: `src/analysis/normalize.py`
Funkce `classify_model(title: str, description: str) -> str | None` mapuje volný text na `model_normalized` z `config.MODELS` přes `search_terms`.

Implementační poznámky:
- Lowercase srovnání obou stran.
- Word boundary regex (`\balma bb\b`), aby `balmain` nematchovalo `alma`.
- Kontrola pořadí: nejprve specifičtější varianty (`alma bb` před `alma`).
- Funkce `classify_all_listings() -> int` projde `WHERE model_normalized IS NULL` a updatuje.

### Úkol 3: `src/analysis/conditions.py`
Funkce `score_condition(text: str) -> tuple[str, float]` vrací `("excellent", 0.85)` apod.

Klíčová slova (CS/SK), v pořadí kontroly (specifické před obecnými):
- `("nová", "nepoužitá", "s visačkou")` → `("new", 1.0)`
- `("jako nová", "ako nová", "minimálně nošená")` → `("like_new", 0.95)`
- `("výborný stav", "skvelý stav", "perfektní stav")` → `("excellent", 0.85)`
- `("velmi dobrý", "veľmi dobrý")` → `("very_good", 0.75)`
- `("dobrý stav", "známky používání", "pár krát")` → `("good", 0.60)`
- `("odřené", "patina", "škrábance", "praskliny")` → `("fair", 0.40)`
- `("poškozená", "rozbité", "vada")` → `("poor", 0.20)`

Default při neúspěchu: `("good", 0.60)` (neutrální).

### Úkol 4: `src/analysis/pricing.py`
- `compute_fair_value(model_id: str) -> int | None`:
  - Median ceny z aktivních inzerátů z `luxurybags` + `armadio` pro daný model.
  - Pokud je vzorek < 3 inzeráty, vrátit None (nedost dat).
- `update_all_arbitrage_scores() -> int`:
  - Pro každý aktivní inzerát s `model_normalized != NULL`:
    - `fair = compute_fair_value(model_id)`
    - `multiplier = CONDITION_MULTIPLIERS[condition_label]`
    - `adjusted_fair = fair * multiplier`
    - `arbitrage_pct = (adjusted_fair - price_czk) / adjusted_fair`
  - Update DB.

### Úkol 5: `src/analysis/authenticity.py`
- `compute_counterfeit_risk(listing: dict) -> float`:
  - Sčítá signály z `config.COUNTERFEIT_SIGNALS` na základě dat v inzerátu.
  - Klamp do `<0.0, 1.0>`.
- `update_all_counterfeit_risks() -> int`.

Pokud signál `description_has_replica_words` nebo `seller_has_multi_brands` matchne, ZDŮRAZŇUJ jejich váhu — to jsou nejsilnější indikátory.

### Úkol 6: Lokální test workflow
1. `pip install -r requirements.txt`
2. `python -m src.db init`
3. `python -m src.cli scrape-all` (nech proběhnout, ulož výstup)
4. `python -m src.cli analyze`
5. `uvicorn src.api.app:app --reload`
6. Otevřít http://localhost:8000 — měl bys vidět karty inzerátů s deal/risk badges.

### Úkol 7: Railway deploy
Až lokálně funguje:
1. `git init && git add . && git commit -m "MVP"`
2. Push na GitHub.
3. Railway → New Project → Deploy from GitHub.
4. V Railway projektu:
   - Volume → Mount path: `/data`
   - Variables → `DB_PATH=/data/listings.db`
   - Cron job (Settings → Cron Schedule): `0 6 * * *` (každý den v 6:00) → command: `python -m src.cli refresh`
5. Custom doména volitelně.

## Pravidla, která NESMÍŠ porušit

1. **GDPR:** NEUKLÁDAT jména prodávajících, telefony, emaily nikam do DB. Schema `listings` má jen URL, ceny, popis, fotky, lokalitu. Tečka.
2. **Rate limit:** min. 2.5 s mezi requesty per doména. Žádný paralelismus per doména.
3. **Robots.txt:** vždy respektovat. `BaseScraper.can_fetch()` to řeší.
4. **User-Agent:** identifikuj se jako research bot (`config.USER_AGENT`). Žádné maskování.
5. **Idempotence:** scrapery jdou pustit opakovaně bez duplicit (UPSERT podle URL).
6. **Měna:** EUR → CZK kurzem 25.0 (pro v0.1 stačí konstanta).
7. **Testy:** každý scraper má alespoň jeden integration test parse_listing s uloženým HTML fixture v `tests/fixtures/`.

## Co NEDĚLAT v MVP

- ❌ Vinted (anti-bot, řešíme později přes Apify).
- ❌ Vestiaire Collective (Cloudflare).
- ❌ AI analýza fotek (řešíme po MVP).
- ❌ Telegram bot (řešíme po MVP, viz `prompts/claude_code_telegram_bot.md`).
- ❌ Žádný login flow / cookies sessions.
- ❌ Žádný paralelismus / threading.
- ❌ Žádné notifikace ven (zatím).

## Acceptance criteria pro v0.1

- [ ] `python -m src.db init` vytvoří DB.
- [ ] `python -m src.cli refresh` proběhne bez chyby a stáhne min. 50 inzerátů.
- [ ] `uvicorn src.api.app:app` běží, frontend se načte na `/`.
- [ ] V dashboardu vidím karty s deal badges (`−25%`) pro inzeráty pod fair value.
- [ ] Inzerát s "1:1 replika" v popisu má `counterfeit_risk > 0.50` a vidím u něj `⚠ riziko` badge.
- [ ] Tlačítko ♡ na kartě funguje (UPSERT do DB), tab "♡ Oblíbené" je filtruje.
- [ ] Aplikace deployuje na Railway, `/health` vrací 200, frontend se načte z domény.

## Git workflow

- Commit per úkol (`feat: implement bazos_cz scraper`, `feat: pricing engine`, atd.)
- README.md průběžně updatuj.
- `data/listings.db` je v `.gitignore`.
- Před push tagem `v0.1` ověř všechna acceptance criteria.

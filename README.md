# LV Arbitrage — Sekundární trh luxusních kabelek (CZ/SK)

Webová aplikace pro monitoring a vyhodnocení arbitrážních příležitostí na sekundárním trhu luxusních kabelek v ČR a SR. Sleduje top 5 modelů Louis Vuitton: **Alma, Speedy, Neverfull, Pochette, Capucines**.

Inspirováno [Jakes.Estate](https://prague-reality-scraper-production.up.railway.app/) — stejný princip, jen pro kabelky místo nemovitostí.

## Co to dělá

1. **Scrapuje** veřejné inzeráty z Bazoš (CZ/SK), Bazar.sk, Sbazar.cz, Luxurybags.cz, Armadio.cz
2. **Vyhodnocuje** férovou tržní cenu z profi obchodů a srovnává s C2C inzeráty
3. **Skóruje** každý inzerát: arbitrážní potenciál + riziko padělku
4. **Zobrazuje** výsledky ve webové appce s filtry, oblíbenými, vyřazenými
5. **Cron job** na Railway scrapuje denně ráno

## Architektura

```
src/
├── api/app.py        # FastAPI: JSON API + servíruje statický frontend
├── scrapers/         # base.py + per platforma
├── analysis/         # normalize, conditions, pricing, authenticity
├── cli.py            # CLI orchestrace (refresh = scrape-all + analyze)
├── db.py             # SQLite schema + helpers
└── config.py         # Modely, zdroje, prahy
static/               # Frontend (HTML + vanilla JS + CSS)
data/listings.db      # SQLite DB (gitignored, na Railway na volume /data/)
```

## Quickstart (lokálně)

```bash
# 1. Setup
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Init DB
python -m src.db init

# 3. Scrape (záleží jaké scrapery jsou už hotové)
python -m src.cli refresh

# 4. Spustit web
uvicorn src.api.app:app --reload

# Otevřít http://localhost:8000
```

## Deploy na Railway

### 1. Push na GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/USER/lv-arbitrage.git
git push -u origin main
```

### 2. Railway projekt
- https://railway.com → **New Project** → **Deploy from GitHub repo**
- Vyber repo, deploy se spustí.

### 3. Persistent volume pro SQLite
- V Railway dashboardu projektu → service → tab **Volumes**
- **+ Volume** → mount path: `/data`
- Po vytvoření Variables → přidej `DB_PATH=/data/listings.db`
- Redeploy. Po startu by `/health` mělo vrátit `{"status":"ok","listings":0}`.

### 4. Cron job pro daily scrape
V Railway dashboardu projektu:
- **+ New** → **Empty Service** (nebo duplikuj web service)
- Tomuto service přidej:
  - Stejný GitHub repo
  - **Settings → Cron Schedule:** `0 6 * * *` (denně v 6:00 UTC)
  - **Settings → Custom Start Command:** `python -m src.cli refresh`
  - **Variables:** `DB_PATH=/data/listings.db` (stejný volume jako web)
  - **Volumes:** mount stejný volume na `/data`

Tím poběží web služba pořád a cron služba se probudí jen ráno na 5–10 minut na scrape.

### 5. Custom doména (volitelně)
- Web service → **Settings → Networking → Generate Domain** (zdarma `*.up.railway.app`)
- Nebo **Custom Domain** s vlastní doménou.

## Datový model (SQLite)

```sql
CREATE TABLE listings (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    description TEXT,
    price_czk INTEGER,
    price_original INTEGER,
    currency TEXT,
    model_normalized TEXT,
    model_raw TEXT,
    condition_score REAL,
    condition_raw TEXT,
    photo_urls TEXT,             -- JSON array
    location TEXT,
    posted_at TEXT,
    scraped_at TEXT NOT NULL,
    fair_value_czk INTEGER,
    arbitrage_pct REAL,
    counterfeit_risk REAL,
    is_active INTEGER DEFAULT 1,
    is_favorite INTEGER DEFAULT 0,
    is_dismissed INTEGER DEFAULT 0
);
-- Žádná osobní data prodávajících (jména, telefony, emaily)
```

## API endpointy

| Endpoint | Popis |
|---|---|
| `GET /health` | Health check pro Railway |
| `GET /api/meta` | Modely, zdroje, defaults pro frontend |
| `GET /api/listings` | Seznam s filtry (model, source, min_arbitrage_pct, max_counterfeit_risk, sort, limit, offset) |
| `GET /api/listings/{id}` | Detail + historie cen |
| `POST /api/listings/{id}/favorite` | Toggle oblíbeného |
| `POST /api/listings/{id}/dismiss` | Toggle vyřazeného |
| `GET /api/stats` | Souhrn (counts per model, source, recent runs) |

## Rate limiting a etika scrapingu

- 2.5–3 s mezi requesty per doména
- Respekt k `robots.txt`
- User-Agent identifikuje skript jako research bot
- Žádný paralelismus per doména
- Retry s exponenciálním backoffem na 429/503

## Roadmap

- **v0.1 (MVP):** 6 scraperů + analyze + web app + Railway deploy
- **v0.2:** Telegram bot pro alerty (viz `prompts/claude_code_telegram_bot.md`)
- **v0.3:** AI analýza fotek (Claude API) pro screening padělků
- **v0.4:** Vinted přes Apify
- **v0.5:** Trend tracking, predikce, retention dashboard

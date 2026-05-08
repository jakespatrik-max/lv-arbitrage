# Claude Code: Telegram Bot (v0.2)

## Kontext

LV Arbitrage v0.1 už běží na Railway, scrapuje denně, web app funguje. Teď přidáváme **Telegram bot**, který:
1. **Posílá alerty** uživateli, když denní scrape najde nový inzerát s arbitrage_pct > X% a counterfeit_risk < Y%
2. **Reaguje na příkazy** v chatu: `/opportunities`, `/stats`, `/watch <model>`, `/help`

## Architektura

Bot **nepoběží jako samostatný service**. Místo toho:
1. **Polling není potřeba** — bot reaguje na webhook z Telegramu přidaný do FastAPI appky
2. **Daily alerty** posílá `src/cli.py refresh` po dokončení analyze fáze (volá `notify_telegram()`)

Tím zůstává jeden web service + jedna cron služba, žádná navíc.

## Setup

### 1. Vytvoř bota
- V Telegramu: kontaktuj `@BotFather` → `/newbot` → získej **BOT_TOKEN**
- Spusť bota v Telegramu, klikni `/start`
- Tvé chat ID získáš přes: `https://api.telegram.org/bot<TOKEN>/getUpdates` → najdi `chat.id`

### 2. Přidej proměnné prostředí (Railway)
```
TELEGRAM_BOT_TOKEN=<token z BotFather>
TELEGRAM_CHAT_ID=<tvé chat ID>
TELEGRAM_WEBHOOK_SECRET=<random string, např. uuid4>
```

### 3. Registruj webhook
Po deployu spusť jednou (lokálně nebo přes Railway shell):
```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     -d "url=https://YOUR-APP.up.railway.app/api/telegram/webhook?secret=$TELEGRAM_WEBHOOK_SECRET"
```

## Implementace

### Soubor `src/notifications/telegram.py`

```python
import os
import httpx

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_message(text: str, chat_id: str | None = None) -> bool:
    """Pošle zprávu. Pokud chat_id None, posílá na default CHAT_ID."""
    if not TOKEN:
        return False
    target = chat_id or CHAT_ID
    if not target:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": target,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def format_listing_alert(listing: dict, base_url: str) -> str:
    """Naformátuje upozornění pro nový inzerát."""
    return (
        f"🚨 *Nová příležitost*\n"
        f"{listing['title']}\n\n"
        f"💰 {listing['price_czk']:,} Kč  "
        f"(fair: {listing['fair_value_czk']:,} Kč)\n"
        f"📉 *−{int(listing['arbitrage_pct']*100)}%* pod tržní cenou\n"
        f"⚠ Riziko padělku: {int(listing['counterfeit_risk']*100)}%\n"
        f"📍 {listing.get('location', '—')}\n\n"
        f"[Otevřít inzerát]({listing['url']}) · "
        f"[Detail v appce]({base_url}/?id={listing['id']})"
    )
```

### Integrace do `src/cli.py`

Přidat do `cmd_refresh()` po `cmd_analyze()`:

```python
# Po analyze: pošli alerty na nové příležitosti, které nebyly poslané
try:
    from src.notifications.telegram import send_alerts_for_new_opportunities
    sent = send_alerts_for_new_opportunities()
    logger.info(f"Telegram: posláno {sent} alertů")
except ImportError:
    pass
```

V `src/notifications/telegram.py` přidat:

```python
from src.db import get_conn

def send_alerts_for_new_opportunities() -> int:
    """
    Pošle alert pro každý inzerát, který:
    - má arbitrage_pct >= 0.20
    - má counterfeit_risk <= 0.55
    - ještě nebyl notifikován (sloupec alerted_at IS NULL)
    """
    base_url = os.environ.get("APP_BASE_URL", "")
    sent = 0
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM listings
            WHERE is_active = 1
              AND is_dismissed = 0
              AND arbitrage_pct >= 0.20
              AND (counterfeit_risk IS NULL OR counterfeit_risk <= 0.55)
              AND alerted_at IS NULL
            ORDER BY arbitrage_pct DESC
            LIMIT 10  -- max 10 alertů per den, ať nespamujeme
        """).fetchall()

        for row in rows:
            listing = dict(row)
            text = format_listing_alert(listing, base_url)
            if send_message(text):
                conn.execute(
                    "UPDATE listings SET alerted_at = ? WHERE id = ?",
                    (now_iso(), listing["id"])
                )
                sent += 1
    return sent
```

A do schema (`src/db.py`) přidat sloupec:
```sql
ALTER TABLE listings ADD COLUMN alerted_at TEXT;
```
(Idempotentně — handluj `OperationalError: duplicate column`.)

### Webhook endpoint v `src/api/app.py`

```python
import os
from fastapi import Request

WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, secret: str = ""):
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403)

    update = await request.json()
    message = update.get("message", {})
    text = message.get("text", "")
    chat_id = str(message.get("chat", {}).get("id"))

    if text == "/start" or text == "/help":
        from src.notifications.telegram import send_message
        send_message(
            "👜 *LV Arbitrage Bot*\n\n"
            "Příkazy:\n"
            "/opportunities — top 5 aktuálních příležitostí\n"
            "/stats — statistiky DB\n"
            "/help — tato nápověda",
            chat_id=chat_id,
        )
    elif text == "/opportunities":
        # query DB, pošli top 5
        ...
    elif text == "/stats":
        # query DB, pošli statistiky
        ...

    return {"ok": True}
```

## Acceptance criteria pro v0.2

- [ ] `/start` v Telegramu vrátí welcome zprávu
- [ ] `/opportunities` vrátí top 5 aktivních příležitostí jako Markdown formátovaný seznam
- [ ] `/stats` vrátí počet aktivních inzerátů, počet příležitostí, čas posledního scrapu
- [ ] Daily refresh job po analyze fázi pošle alerty (max 10 per den) na nové příležitosti
- [ ] Inzerát který už byl notifikován (alerted_at IS NOT NULL) NEPOSÍLÁ se znova
- [ ] Webhook secret check funguje (zkus 403 bez secretu)

## Bezpečnost

- **NEUKLÁDAT BOT_TOKEN do gitu** — pouze v Railway Variables.
- Webhook secret musí být v URL parameter, ne v body (Telegram nevolá body s authem).
- `chat_id` whitelist — pokud chceš jen sebe, kontroluj `chat_id == TELEGRAM_CHAT_ID` před odpovědí.

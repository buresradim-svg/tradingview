# Crypto Signal Dashboard

Flask app, která každou hodinu stáhne data z TradingView + CoinGecko,
nechá Claude vygenerovat analýzu a zobrazí vše na přehledném dashboardu.

## Lokální spuštění

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python app.py
# otevři http://localhost:5000
```

## Deploy na Render.com

Viz instrukce níže v README nebo follow steps z Claude.

## Proměnné prostředí

| Proměnná | Popis |
|---|---|
| `ANTHROPIC_API_KEY` | Tvůj Anthropic API klíč |
| `PORT` | Port (Render nastaví automaticky) |

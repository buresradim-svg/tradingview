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
| `COMPOSIO_API_KEY` | Composio API klíč (čtení newsletterů Patria z Gmailu) |
| `COMPOSIO_USER_ID` | Volitelné: user/entity ID v Composio, pod kterým je Gmail připojený |
| `COMPOSIO_GMAIL_ACCOUNT_ID` | Volitelné: ID připojeného Gmail účtu v Composio (osobní Gmail s newslettery) |
| `PATRIA_NEWS_QUERY` | Volitelné: Gmail dotaz na newslettery (výchozí: odesílatelé c.c@patria.cz a research@investovani.patria.cz, posledních 30 dní) |
| `PATRIA_NEWS_COUNT` | Volitelné: kolik posledních e-mailů zpracovat (výchozí 10) |

## Newslettery Patria v záložce Akcie CZ

Pod tabulkami se zobrazuje shrnutí newsletterů z Gmailu: souhrnná tabulka konkrétních
doporučení a po dnech přehled trhu, akciové nápady, události a rizika. Data se stahují
na pozadí při otevření záložky (nejvýš 1× za 4 hodiny) a shrnuje je Claude jen z textu e-mailu.

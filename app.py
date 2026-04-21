import os
import time
import requests
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

COINS = [
    {"id": "bitcoin",       "sym": "BTC",  "tv": "BINANCE:BTCUSDT"},
    {"id": "ethereum",      "sym": "ETH",  "tv": "BINANCE:ETHUSDT"},
    {"id": "solana",        "sym": "SOL",  "tv": "BINANCE:SOLUSDT"},
    {"id": "binancecoin",   "sym": "BNB",  "tv": "BINANCE:BNBUSDT"},
    {"id": "ripple",        "sym": "XRP",  "tv": "BINANCE:XRPUSDT"},
    {"id": "cardano",       "sym": "ADA",  "tv": "BINANCE:ADAUSDT"},
    {"id": "avalanche-2",   "sym": "AVAX", "tv": "BINANCE:AVAXUSDT"},
    {"id": "chainlink",     "sym": "LINK", "tv": "BINANCE:LINKUSDT"},
    {"id": "polkadot",      "sym": "DOT",  "tv": "BINANCE:DOTUSDT"},
    {"id": "uniswap",       "sym": "UNI",  "tv": "BINANCE:UNIUSDT"},
]

cache = {
    "data": None,
    "updated_at": None,
    "updating": False,
    "error": None,
}

# ── Stocks cache ────────────────────────────────────────────────────────────
stocks_cache = {
    "patria": None,
    "world": None,
    "patria_at": None,
    "world_at": None,
}

US_STOCKS = [
    "NVDA","MSFT","AAPL","AMZN","GOOGL","META","TSLA","JPM","V","JNJ",
    "WMT","XOM","UNH","MA","HD","NFLX","PYPL","INTC","AMD","DIS",
]

def fetch_patria_recommendations():
    """Scrape Patria.cz doporučení pro české a světové akcie."""
    url = "https://www.patria.cz/akcie/vyzkum/doporuceni.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "cs-CZ,cs;q=0.9",
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    html = r.text

    results_cz = []
    results_world = []

    # Parse Czech recommendations table
    import re
    # Find "Patria - Investiční doporučení - ČR" section
    cz_match = re.search(
        r'Patria - Investiční doporučení - ČR.*?<table[^>]*>(.*?)</table>',
        html, re.DOTALL
    )
    if cz_match:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', cz_match.group(1), re.DOTALL)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 5:
                name = re.sub(r'<[^>]+>', '', cells[0]).strip()
                rec_new = re.sub(r'<[^>]+>', '', cells[1]).strip()
                rec_old = re.sub(r'<[^>]+>', '', cells[2]).strip()
                price_cur = re.sub(r'<[^>]+>', '', cells[3]).strip().replace('\xa0', ' ')
                price_target = re.sub(r'<[^>]+>', '', cells[4]).strip().replace('\xa0', ' ')
                potential = re.sub(r'<[^>]+>', '', cells[5]).strip() if len(cells) > 5 else ''
                if name and rec_new and name not in ('Název CP', ''):
                    results_cz.append({
                        "name": name,
                        "rec": rec_new,
                        "rec_prev": rec_old,
                        "price": price_cur,
                        "target": price_target,
                        "potential": potential,
                        "source": "Patria",
                    })

    # Parse "Monitoring investičních doporučení" - global banks
    monitor_match = re.search(
        r'Monitoring investičních doporučení.*?<table[^>]*>(.*?)</table>',
        html, re.DOTALL
    )
    if monitor_match:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', monitor_match.group(1), re.DOTALL)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 4:
                name = re.sub(r'<[^>]+>', '', cells[0]).strip()
                company = re.sub(r'<[^>]+>', '', cells[1]).strip()
                rec_new = re.sub(r'<[^>]+>', '', cells[2]).strip()
                target_raw = re.sub(r'<[^>]+>', '', cells[4]).strip() if len(cells) > 4 else ''
                currency = re.sub(r'<[^>]+>', '', cells[5]).strip() if len(cells) > 5 else ''
                if name and rec_new and name not in ('Název CP', ''):
                    results_world.append({
                        "name": name,
                        "analyst": company,
                        "rec": rec_new,
                        "target": f"{target_raw} {currency}".strip(),
                        "source": "Patria/monitoring",
                    })

    return {
        "cz": results_cz[:20],
        "world_monitor": results_world[:20],
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_yahoo_recommendations(symbols):
    """Fetch analyst recommendations from Yahoo Finance."""
    results = []
    for sym in symbols:
        try:
            url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
            params = {"modules": "financialData,price"}
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            }
            r = requests.get(url, params=params, headers=headers, timeout=10)
            if r.status_code != 200:
                continue
            d = r.json().get("quoteSummary", {}).get("result", [{}])[0]
            fin = d.get("financialData", {})
            price_data = d.get("price", {})

            rec_mean = fin.get("recommendationMean", {}).get("raw")
            rec_key = fin.get("recommendationKey", "")
            target = fin.get("targetMeanPrice", {}).get("raw")
            current = fin.get("currentPrice", {}).get("raw") or                       price_data.get("regularMarketPrice", {}).get("raw")
            num_analysts = fin.get("numberOfAnalystOpinions", {}).get("raw", 0)
            name = price_data.get("longName") or price_data.get("shortName") or sym
            chg = price_data.get("regularMarketChangePercent", {}).get("raw", 0)

            if rec_key:
                label_map = {
                    "strong_buy": "Strong buy", "buy": "Buy",
                    "hold": "Hold", "underperform": "Underperform",
                    "sell": "Sell",
                }
                rec_label = label_map.get(rec_key, rec_key.replace("_", " ").title())
                potential = round((target - current) / current * 100, 1) if target and current else None
                results.append({
                    "sym": sym,
                    "name": name,
                    "price": round(current, 2) if current else None,
                    "change_24h": round(chg * 100, 2) if chg else 0,
                    "rec": rec_label,
                    "rec_score": round(rec_mean, 2) if rec_mean else None,
                    "target": round(target, 2) if target else None,
                    "potential": potential,
                    "analysts": num_analysts,
                })
        except Exception:
            continue
    return sorted(results, key=lambda x: x.get("rec_score") or 3)


def refresh_stocks():
    """Refresh both Patria and Yahoo Finance data."""
    try:
        patria = fetch_patria_recommendations()
        stocks_cache["patria"] = patria
        stocks_cache["patria_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        stocks_cache["patria"] = {"error": str(e), "cz": [], "world_monitor": []}

    try:
        world = fetch_yahoo_recommendations(US_STOCKS)
        stocks_cache["world"] = world
        stocks_cache["world_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        stocks_cache["world"] = []




def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def calc_ema(prices, period):
    if not prices:
        return 0
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def calc_macd(prices):
    if len(prices) < 26:
        return 0
    return calc_ema(prices, 12) - calc_ema(prices, 26)


def signal_from_indicators(rsi, macd, change_24h):
    score = 0
    if rsi < 35:
        score += 2
    elif rsi < 45:
        score += 1
    elif rsi > 65:
        score -= 2
    elif rsi > 55:
        score -= 1
    score += (1 if macd > 0 else -1)
    if change_24h > 4:
        score += 1
    elif change_24h < -4:
        score -= 1
    if score >= 2:
        return "LONG"
    if score <= -2:
        return "SHORT"
    return "NEUTRAL"


def fetch_market_data():
    ids = ",".join(c["id"] for c in COINS)
    url = (
        f"https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=usd&ids={ids}&order=market_cap_desc"
        f"&sparkline=true&price_change_percentage=24h"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return {item["id"]: item for item in r.json()}


def fetch_fear_greed():
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    r.raise_for_status()
    d = r.json()["data"][0]
    return {"value": int(d["value"]), "label": d["value_classification"]}


def fetch_tv_signal(tv_symbol):
    """
    Pulls TradingView technical analysis summary via tradingview-screener public API.
    Returns buy/sell/neutral counts and recommendation string.
    """
    try:
        payload = {
            "symbols": {"tickers": [tv_symbol]},
            "columns": [
                "Recommend.All",
                "Recommend.MA",
                "Recommend.Other",
            ],
        }
        r = requests.post(
            "https://scanner.tradingview.com/crypto/scan",
            json=payload,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                vals = data[0].get("d", [None, None, None])
                rec = vals[0]
                if rec is None:
                    return {"tv_rec": "N/A", "tv_score": 0}
                score = round(rec, 2)
                if score >= 0.5:
                    label = "Strong buy"
                elif score >= 0.1:
                    label = "Buy"
                elif score <= -0.5:
                    label = "Strong sell"
                elif score <= -0.1:
                    label = "Sell"
                else:
                    label = "Neutral"
                return {"tv_rec": label, "tv_score": score}
    except Exception:
        pass
    return {"tv_rec": "N/A", "tv_score": 0}


def fetch_tv_ideas(symbol_name):
    """
    Fetches community ideas from TradingView for a symbol.
    Uses the tradingview-scraper compatible public endpoint.
    """
    try:
        url = f"https://www.tradingview.com/symbols/{symbol_name}USD/ideas/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        api_url = f"https://www.tradingview.com/ideas/page-1/?symbol={symbol_name}USD&sort=popularity&type=1"
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            ideas = r.json() if r.headers.get("content-type", "").startswith("application/json") else []
            if isinstance(ideas, list) and ideas:
                bull = sum(1 for i in ideas[:20] if i.get("agree_count", 0) > i.get("disagree_count", 0))
                bear = len(ideas[:20]) - bull
                return {"bull": bull, "bear": bear, "total": len(ideas[:20])}
    except Exception:
        pass
    return {"bull": 0, "bear": 0, "total": 0}


def ask_claude(market_summary):
    if not ANTHROPIC_API_KEY:
        return "Nastavte ANTHROPIC_API_KEY pro AI analýzu."
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 600,
                "system": (
                    "Jsi krypto analytik. Píšeš stručně v češtině. "
                    "Na základě dat dej jasný přehled trhu (2-3 věty), "
                    "pak 2-3 konkrétní tipy s LONG/SHORT signálem a důvodem (1 věta každý). "
                    "Žádné obecné výhrady, jen konkrétní analýza."
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": f"Aktuální tržní data:\n{market_summary}\n\nZhodnoť trh a dej konkrétní doporučení.",
                    }
                ],
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    except Exception as e:
        return f"Chyba při volání Claude API: {e}"


def refresh_data():
    if cache["updating"]:
        return
    cache["updating"] = True
    cache["error"] = None
    try:
        market = fetch_market_data()
        fg = fetch_fear_greed()

        coins_out = []
        summary_lines = []

        for coin in COINS:
            cid = coin["id"]
            sym = coin["sym"]
            if cid not in market:
                continue
            m = market[cid]
            spark = (m.get("sparkline_in_7d") or {}).get("price", [])
            prices = spark[-30:] if len(spark) >= 15 else spark

            rsi = calc_rsi(prices) if len(prices) >= 15 else 50.0
            macd = calc_macd(prices) if len(prices) >= 26 else 0.0
            chg = round(m.get("price_change_percentage_24h") or 0, 2)
            price = m.get("current_price", 0)
            volume = m.get("total_volume", 0)

            tv = fetch_tv_signal(coin["tv"])
            signal = signal_from_indicators(rsi, macd, chg)

            coins_out.append({
                "sym": sym,
                "price": price,
                "change_24h": chg,
                "rsi": rsi,
                "macd": round(macd, 4),
                "signal": signal,
                "tv_rec": tv["tv_rec"],
                "tv_score": tv["tv_score"],
                "volume": volume,
                "market_cap": m.get("market_cap", 0),
            })

            summary_lines.append(
                f"{sym}: ${price:,.2f}, 24h={chg:+.1f}%, RSI={rsi}, "
                f"MACD={'↑' if macd > 0 else '↓'}, TV={tv['tv_rec']}, signal={signal}"
            )

            time.sleep(0.3)

        market_summary = "\n".join(summary_lines)
        market_summary += f"\nFear & Greed: {fg['value']} ({fg['label']})"

        ai_analysis = ask_claude(market_summary)

        cache["data"] = {
            "coins": coins_out,
            "fear_greed": fg,
            "ai_analysis": ai_analysis,
            "btc_dominance": None,
        }
        cache["updated_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        cache["error"] = str(e)
    finally:
        cache["updating"] = False


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Investiční dashboard</title>
<style>
:root{--bg:#f8f8f6;--card:#fff;--border:rgba(0,0,0,0.08);--text:#1a1a18;--muted:#6b6b68;--green-bg:#eaf3de;--green:#27500a;--red-bg:#fcebeb;--red:#791f1f;--amber-bg:#faeeda;--amber:#633806;--blue-bg:#e6f1fb;--blue:#0c447c}
@media(prefers-color-scheme:dark){:root{--bg:#1a1a18;--card:#242422;--border:rgba(255,255,255,0.09);--text:#e8e6df;--muted:#9a9890;--green-bg:#173404;--green:#c0dd97;--red-bg:#501313;--red:#f09595;--amber-bg:#412402;--amber:#fac775;--blue-bg:#042c53;--blue:#b5d4f4}}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);font-size:14px}
.wrap{max-width:980px;margin:0 auto;padding:20px 16px 40px}
.tabs{display:flex;gap:4px;margin-bottom:20px;border-bottom:1px solid var(--border);padding-bottom:0}
.tab{padding:8px 18px;cursor:pointer;border-radius:8px 8px 0 0;font-size:13px;font-weight:500;color:var(--muted);border:0.5px solid transparent;border-bottom:none;background:transparent;transition:all .15s}
.tab.active{background:var(--card);color:var(--text);border-color:var(--border);margin-bottom:-1px}
.tab-content{display:none}.tab-content.active{display:block}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:8px}
h1{font-size:18px;font-weight:500}
.meta{font-size:12px;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#639922;margin-right:5px}
.dot.updating{background:#ef9f27;animation:pulse .8s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.top-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:20px}
.metric{background:var(--card);border:0.5px solid var(--border);border-radius:10px;padding:12px 14px}
.metric .label{font-size:11px;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}
.metric .val{font-size:22px;font-weight:500}
.metric .sub{font-size:11px;color:var(--muted);margin-top:2px}
.section-title{font-size:11px;font-weight:500;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.table-wrap{overflow-x:auto;margin-bottom:20px}
table{width:100%;border-collapse:collapse;background:var(--card);border:0.5px solid var(--border);border-radius:10px;overflow:hidden}
th{font-size:11px;color:var(--muted);font-weight:500;padding:10px 12px;text-align:left;border-bottom:0.5px solid var(--border)}
td{padding:10px 12px;border-bottom:0.5px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(0,0,0,0.02)}
.badge{display:inline-block;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:500}
.badge.LONG,.badge.buy,.badge.strong-buy,.badge.Koupit,.badge.Akumulovat{background:var(--green-bg);color:var(--green)}
.badge.SHORT,.badge.sell,.badge.strong-sell,.badge.Prodat,.badge.Redukovat{background:var(--red-bg);color:var(--red)}
.badge.NEUTRAL,.badge.hold,.badge.neutral,.badge.Držet{background:var(--amber-bg);color:var(--amber)}
.badge.na{background:var(--border);color:var(--muted)}
.rsi-bar{width:60px;height:5px;background:var(--border);border-radius:3px;display:inline-block;vertical-align:middle;margin-left:4px}
.rsi-fill{height:100%;border-radius:3px}
.fg-bar-wrap{height:6px;background:var(--border);border-radius:3px;flex:1;overflow:hidden}
.fg-bar{height:100%;border-radius:3px}
.ai-box{background:var(--card);border:0.5px solid var(--border);border-radius:10px;padding:16px;line-height:1.65;white-space:pre-wrap}
.refresh-btn{font-size:12px;padding:6px 12px;border:0.5px solid var(--border);border-radius:7px;background:transparent;color:var(--text);cursor:pointer}
.refresh-btn:hover{background:var(--border)}
.error-box{background:var(--red-bg);color:var(--red);border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:13px}
.spinner{display:inline-block;width:12px;height:12px;border:2px solid var(--border);border-top-color:var(--muted);border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.chg.up{color:var(--green)}.chg.down{color:var(--red)}
.potential.pos{color:var(--green)}.potential.neg{color:var(--red)}
.info-note{font-size:11px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--card);border:0.5px solid var(--border);border-radius:8px;line-height:1.5}
@media(max-width:600px){.hide-mobile{display:none}td,th{padding:8px 8px}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div><h1>Investiční dashboard</h1><div class="meta" id="meta-global">—</div></div>
  </header>

  <div class="tabs">
    <button class="tab active" onclick="switchTab('crypto',this)">Krypto</button>
    <button class="tab" onclick="switchTab('stocks-cz',this)">Akcie CZ (Patria)</button>
    <button class="tab" onclick="switchTab('stocks-us',this)">Akcie US (Yahoo)</button>
  </div>

  <!-- ── TAB 1: CRYPTO ── -->
  <div id="tab-crypto" class="tab-content active">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">
      <div><span class="dot" id="dot"></span><span style="font-weight:500">Crypto signály</span> <span class="meta" id="crypto-meta"></span></div>
      <button class="refresh-btn" onclick="loadCrypto()">Obnovit ↻</button>
    </div>
    <div id="crypto-error"></div>
    <div class="top-grid">
      <div class="metric"><div class="label">BTC</div><div class="val" id="btc-price">—</div><div class="sub" id="btc-chg"></div></div>
      <div class="metric"><div class="label">ETH</div><div class="val" id="eth-price">—</div><div class="sub" id="eth-chg"></div></div>
      <div class="metric">
        <div class="label">Fear &amp; Greed</div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
          <span class="val" id="fg-val">—</span>
          <span class="fg-bar-wrap"><span class="fg-bar" id="fg-bar" style="width:0%"></span></span>
        </div>
        <div class="sub" id="fg-label"></div>
      </div>
      <div class="metric"><div class="label">Aktualizace</div><div class="val" style="font-size:14px" id="update-time">—</div><div class="sub" id="update-age"></div></div>
    </div>
    <div class="section-title">Signály</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Coin</th><th>Cena</th><th>24h</th><th class="hide-mobile">RSI</th><th class="hide-mobile">TV signal</th><th>Náš signal</th></tr></thead>
        <tbody id="coin-tbody"><tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted)"><span class="spinner"></span>Načítám...</td></tr></tbody>
      </table>
    </div>
    <div class="section-title" style="margin-top:20px">AI analýza (Claude)</div>
    <div class="ai-box" id="ai-box" style="color:var(--muted);font-style:italic"><span class="spinner"></span>Čekám na analýzu...</div>
    <div style="margin-top:12px;font-size:11px;color:var(--muted)">Refresh každou hodinu. Toto není finanční poradenství.</div>
  </div>

  <!-- ── TAB 2: STOCKS CZ ── -->
  <div id="tab-stocks-cz" class="tab-content">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">
      <div><span style="font-weight:500">Doporučení analytiků — ČR</span> <span class="meta" id="patria-meta"></span></div>
      <button class="refresh-btn" onclick="loadStocks(true)">Obnovit ↻</button>
    </div>
    <div id="stocks-error"></div>
    <div class="info-note">Data jsou stahována z <a href="https://www.patria.cz/akcie/vyzkum/doporuceni.html" target="_blank" style="color:var(--blue)">patria.cz/akcie/vyzkum/doporuceni.html</a> jednou denně. Zdroj: Patria Finance / KBC Securities.</div>
    <div class="section-title">Patria Finance — české akcie</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Akcie</th><th>Doporučení</th><th class="hide-mobile">Předchozí</th><th>Cena</th><th>Cíl 12M</th><th>Potenciál</th></tr></thead>
        <tbody id="patria-cz-tbody"><tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted)"><span class="spinner"></span>Načítám...</td></tr></tbody>
      </table>
    </div>
    <div class="section-title" style="margin-top:16px">Monitoring — světová doporučení analytiků</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Akcie</th><th>Analytik</th><th>Doporučení</th><th>Cílová cena</th></tr></thead>
        <tbody id="patria-world-tbody"><tr><td colspan="4" style="text-align:center;padding:24px;color:var(--muted)"><span class="spinner"></span>Načítám...</td></tr></tbody>
      </table>
    </div>
    <div style="font-size:11px;color:var(--muted)">Toto není finanční poradenství. Data z Patria.cz.</div>
  </div>

  <!-- ── TAB 3: STOCKS US ── -->
  <div id="tab-stocks-us" class="tab-content">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">
      <div><span style="font-weight:500">Konsenzus analytiků — US akcie</span> <span class="meta" id="yahoo-meta"></span></div>
      <button class="refresh-btn" onclick="loadStocks(true)">Obnovit ↻</button>
    </div>
    <div class="info-note">Data ze Yahoo Finance. Konsenzus ze všech sledujících analytiků. Skóre: 1 = Strong buy, 5 = Strong sell.</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Ticker</th><th class="hide-mobile">Název</th><th>Cena</th><th>24h</th><th>Konsenzus</th><th class="hide-mobile">Cíl. cena</th><th>Potenciál</th></tr></thead>
        <tbody id="yahoo-tbody"><tr><td colspan="7" style="text-align:center;padding:24px;color:var(--muted)"><span class="spinner"></span>Načítám...</td></tr></tbody>
      </table>
    </div>
    <div style="font-size:11px;color:var(--muted)">Toto není finanční poradenství. Refresh 1x denně.</div>
  </div>
</div>

<script>
function fmt(n){if(!n&&n!==0)return'—';if(n>=1000)return'$'+n.toLocaleString('cs-CZ',{maximumFractionDigits:0});if(n>=1)return'$'+n.toFixed(2);return'$'+n.toFixed(4)}
function fmtAge(iso){if(!iso)return'';const d=Math.round((Date.now()-new Date(iso))/60000);if(d<2)return'právě teď';if(d<60)return`před ${d} min`;if(d<1440)return`před ${Math.floor(d/60)} hod`;return`před ${Math.floor(d/1440)} dny`}
function rsiColor(r){return r<30?'#e24b4a':r<45?'#639922':r>70?'#e24b4a':r>55?'#ef9f27':'#888780'}
function badgeClass(r){if(!r||r==='N/A')return'na';const s=r.toLowerCase();if(s.includes('strong buy')||s.includes('koupit')||s.includes('akumulovat'))return'buy';if(s.includes('strong sell')||s.includes('prodat')||s.includes('redukovat'))return'sell';if(s.includes('buy'))return'buy';if(s.includes('sell'))return'sell';return'hold'}

function switchTab(id, btn){
  document.querySelectorAll('.tab-content').forEach(el=>el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el=>el.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  btn.classList.add('active');
  if(id==='stocks-cz'||id==='stocks-us') loadStocks();
}

// ── Crypto ──────────────────────────────────────────────────────────────────
async function loadCrypto(){
  document.getElementById('dot').className='dot updating';
  try{
    const r=await fetch('/api/data');
    const d=await r.json();
    if(d.error&&!d.coins){document.getElementById('crypto-error').innerHTML=`<div class="error-box">Chyba: ${d.error}</div>`;return}
    document.getElementById('crypto-error').innerHTML='';
    const btc=d.coins?.find(c=>c.sym==='BTC'),eth=d.coins?.find(c=>c.sym==='ETH');
    if(btc){document.getElementById('btc-price').textContent=fmt(btc.price);const bc=btc.change_24h;document.getElementById('btc-chg').innerHTML=`<span class="chg ${bc>=0?'up':'down'}">${bc>=0?'+':''}${bc.toFixed(2)}%</span>`}
    if(eth){document.getElementById('eth-price').textContent=fmt(eth.price);const ec=eth.change_24h;document.getElementById('eth-chg').innerHTML=`<span class="chg ${ec>=0?'up':'down'}">${ec>=0?'+':''}${ec.toFixed(2)}%</span>`}
    const fg=d.fear_greed||{};
    document.getElementById('fg-val').textContent=fg.value||'—';
    document.getElementById('fg-label').textContent=fg.label||'';
    const bar=document.getElementById('fg-bar');
    bar.style.width=(fg.value||0)+'%';
    bar.style.background=fg.value<30?'#e24b4a':fg.value<50?'#ef9f27':fg.value<75?'#639922':'#3b6d11';
    const ts=d.updated_at;
    const dt=new Date(ts);
    document.getElementById('update-time').textContent=dt.toLocaleTimeString('cs-CZ',{hour:'2-digit',minute:'2-digit'});
    document.getElementById('update-age').textContent=fmtAge(ts);
    document.getElementById('crypto-meta').textContent=fmtAge(ts);
    let rows='';
    for(const c of d.coins||[]){
      const cc=c.change_24h>=0?'up':'down',ct=(c.change_24h>=0?'+':'')+c.change_24h.toFixed(2)+'%';
      const rp=Math.min(Math.max(c.rsi,0),100);
      const tvCls=badgeClass(c.tv_rec);
      rows+=`<tr><td><strong>${c.sym}</strong></td><td>${fmt(c.price)}</td><td class="chg ${cc}">${ct}</td><td class="hide-mobile">${c.rsi}<span class="rsi-bar"><span class="rsi-fill" style="width:${rp}%;background:${rsiColor(c.rsi)}"></span></span></td><td class="hide-mobile"><span class="badge ${tvCls}">${c.tv_rec}</span></td><td><span class="badge ${c.signal}">${c.signal}</span></td></tr>`;
    }
    document.getElementById('coin-tbody').innerHTML=rows||'<tr><td colspan="6" style="text-align:center;color:var(--muted)">Žádná data</td></tr>';
    const ai=document.getElementById('ai-box');
    ai.style.fontStyle='normal';ai.style.color='var(--text)';
    ai.textContent=d.ai_analysis||'—';
    document.getElementById('dot').className='dot';
  }catch(e){document.getElementById('crypto-error').innerHTML=`<div class="error-box">Chyba: ${e.message}</div>`;document.getElementById('dot').className='dot'}
}

// ── Stocks ───────────────────────────────────────────────────────────────────
let stocksLoaded=false;
async function loadStocks(force){
  if(stocksLoaded&&!force)return;
  stocksLoaded=true;
  try{
    const r=await fetch('/api/stocks');
    const d=await r.json();

    // Patria CZ
    document.getElementById('patria-meta').textContent=fmtAge(d.patria_at);
    const czData=d.patria?.cz||[];
    let czRows='';
    if(d.patria?.error){czRows=`<tr><td colspan="6" style="color:var(--red);padding:12px">Chyba scrapingu: ${d.patria.error}</td></tr>`}
    else if(czData.length){
      for(const s of czData){
        const bc=badgeClass(s.rec);
        const pot=s.potential?`<span class="potential ${parseFloat(s.potential)>=0?'pos':'neg'}">${s.potential}%</span>`:'—';
        czRows+=`<tr><td><strong>${s.name}</strong></td><td><span class="badge ${bc}">${s.rec||'—'}</span></td><td class="hide-mobile" style="color:var(--muted);font-size:12px">${s.rec_prev||'—'}</td><td>${s.price||'—'}</td><td>${s.target||'—'}</td><td>${pot}</td></tr>`;
      }
    }else{czRows='<tr><td colspan="6" style="text-align:center;padding:20px;color:var(--muted)">Žádná data z Patrie (stránka může být chráněná)</td></tr>'}
    document.getElementById('patria-cz-tbody').innerHTML=czRows;

    // Patria world monitor
    const wData=d.patria?.world_monitor||[];
    let wRows='';
    if(wData.length){
      for(const s of wData){
        const bc=badgeClass(s.rec);
        wRows+=`<tr><td><strong>${s.name}</strong></td><td style="color:var(--muted);font-size:12px">${s.analyst||'—'}</td><td><span class="badge ${bc}">${s.rec||'—'}</span></td><td>${s.target||'—'}</td></tr>`;
      }
    }else{wRows='<tr><td colspan="4" style="text-align:center;padding:20px;color:var(--muted)">Žádná data</td></tr>'}
    document.getElementById('patria-world-tbody').innerHTML=wRows;

    // Yahoo US
    document.getElementById('yahoo-meta').textContent=fmtAge(d.world_at);
    const usData=d.world||[];
    let usRows='';
    if(usData.length){
      for(const s of usData){
        const cc=s.change_24h>=0?'up':'down',ct=(s.change_24h>=0?'+':'')+s.change_24h.toFixed(2)+'%';
        const bc=badgeClass(s.rec);
        const pot=s.potential!=null?`<span class="potential ${s.potential>=0?'pos':'neg'}">${s.potential>0?'+':''}${s.potential}%</span>`:'—';
        usRows+=`<tr><td><strong>${s.sym}</strong></td><td class="hide-mobile" style="font-size:12px;color:var(--muted)">${s.name||''}</td><td>${fmt(s.price)}</td><td class="chg ${cc}">${ct}</td><td><span class="badge ${bc}">${s.rec}</span></td><td class="hide-mobile">${s.target?fmt(s.target):'—'}</td><td>${pot}</td></tr>`;
      }
    }else{usRows='<tr><td colspan="7" style="text-align:center;padding:20px;color:var(--muted)">Načítám Yahoo Finance...</td></tr>'}
    document.getElementById('yahoo-tbody').innerHTML=usRows;

  }catch(e){
    document.getElementById('stocks-error').innerHTML=`<div class="error-box">Chyba: ${e.message}</div>`;
  }
}

loadCrypto();
setInterval(loadCrypto, 60*60*1000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/data")
def api_data():
    if cache["data"] is None and not cache["updating"]:
        refresh_data()
    if cache["error"] and cache["data"] is None:
        return jsonify({"error": cache["error"]})
    return jsonify({
        **(cache["data"] or {}),
        "updated_at": cache["updated_at"],
        "updating": cache["updating"],
        "error": cache["error"],
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    refresh_data()
    return jsonify({"ok": True})


@app.route("/api/stocks")
def api_stocks():
    # Refresh if never loaded or older than 24h
    from datetime import timedelta
    pat_at = stocks_cache.get("patria_at")
    needs_refresh = (
        stocks_cache["patria"] is None or
        (pat_at and (datetime.now(timezone.utc) - datetime.fromisoformat(pat_at)) > timedelta(hours=23))
    )
    if needs_refresh:
        refresh_stocks()
    return jsonify({
        "patria": stocks_cache.get("patria") or {},
        "world": stocks_cache.get("world") or [],
        "patria_at": stocks_cache.get("patria_at"),
        "world_at": stocks_cache.get("world_at"),
    })

@app.route("/api/stocks/refresh", methods=["POST"])
def api_stocks_refresh():
    refresh_stocks()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

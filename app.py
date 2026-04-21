import os
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Crypto config ────────────────────────────────────────────────────────────
COINS = [
    {"sym": "BTC",  "pair": "XBTUSD"},
    {"sym": "ETH",  "pair": "ETHUSD"},
    {"sym": "SOL",  "pair": "SOLUSD"},
    {"sym": "BNB",  "pair": "BNBUSD"},
    {"sym": "XRP",  "pair": "XRPUSD"},
    {"sym": "ADA",  "pair": "ADAUSD"},
    {"sym": "AVAX", "pair": "AVAXUSD"},
    {"sym": "LINK", "pair": "LINKUSD"},
    {"sym": "DOT",  "pair": "DOTUSD"},
    {"sym": "UNI",  "pair": "UNIUSD"},
]
KRAKEN = "https://api.kraken.com/0/public"

# ── Stocks config ────────────────────────────────────────────────────────────
US_STOCKS = [
    "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL",
    "META", "TSLA", "JPM", "V", "JNJ",
    "WMT", "XOM", "UNH", "HD", "NFLX",
    "PYPL", "INTC", "AMD", "DIS", "AMGN",
]

# ── Caches ───────────────────────────────────────────────────────────────────
crypto_cache = {"data": None, "updated_at": None, "updating": False, "error": None}
stocks_cache = {"patria": None, "world": None, "patria_at": None, "world_at": None}


# ════════════════════════════════════════════════════════════════════════════
# CRYPTO FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════

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
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 1)


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
    if rsi < 35:    score += 2
    elif rsi < 45:  score += 1
    elif rsi > 65:  score -= 2
    elif rsi > 55:  score -= 1
    score += (1 if macd > 0 else -1)
    if change_24h > 4:    score += 1
    elif change_24h < -4: score -= 1
    if score >= 2:  return "LONG"
    if score <= -2: return "SHORT"
    return "NEUTRAL"


def fetch_kraken_ticker(pair):
    r = requests.get(
        f"{KRAKEN}/Ticker",
        params={"pair": pair},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    r.raise_for_status()
    result = r.json().get("result", {})
    if not result:
        raise Exception(f"No ticker for {pair}")
    d = list(result.values())[0]
    price = float(d["c"][0])
    open_p = float(d["o"])
    change_24h = round((price - open_p) / open_p * 100, 2) if open_p else 0
    return {"price": price, "change_24h": change_24h, "volume": float(d["v"][1])}


def fetch_kraken_klines(pair, interval=240, limit=50):
    r = requests.get(
        f"{KRAKEN}/OHLC",
        params={"pair": pair, "interval": interval},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    r.raise_for_status()
    result = r.json().get("result", {})
    candles = [v for k, v in result.items() if k != "last"]
    if not candles:
        raise Exception(f"No candles for {pair}")
    return [float(c[4]) for c in candles[0][-limit:]]


def fetch_fear_greed():
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    r.raise_for_status()
    d = r.json()["data"][0]
    return {"value": int(d["value"]), "label": d["value_classification"]}


def fetch_tv_signal(pair):
    try:
        r = requests.post(
            "https://scanner.tradingview.com/crypto/scan",
            json={"symbols": {"tickers": [f"KRAKEN:{pair}"]}, "columns": ["Recommend.All"]},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                score = data[0].get("d", [None])[0]
                if score is not None:
                    if score >= 0.5:    label = "Strong buy"
                    elif score >= 0.1:  label = "Buy"
                    elif score <= -0.5: label = "Strong sell"
                    elif score <= -0.1: label = "Sell"
                    else:               label = "Neutral"
                    return {"tv_rec": label, "tv_score": round(score, 2)}
    except Exception:
        pass
    return {"tv_rec": "N/A", "tv_score": 0}


def ask_claude(market_summary):
    if not ANTHROPIC_API_KEY:
        return "Nastavte ANTHROPIC_API_KEY pro AI analyzu."
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": 600,
                "system": (
                    "Jsi krypto analytik. Pises strucne v cestine. "
                    "Dej jasny prehled trhu (2-3 vety), pak 2-3 konkretni tipy "
                    "s LONG/SHORT signalem a duvodem. Zadne obecne vyhrady."
                ),
                "messages": [{"role": "user", "content": f"Data:\n{market_summary}\n\nZhodnot trh."}],
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    except Exception as e:
        return f"Chyba Claude API: {e}"


def refresh_crypto():
    if crypto_cache["updating"]:
        return
    crypto_cache["updating"] = True
    crypto_cache["error"] = None
    try:
        fg = fetch_fear_greed()
        coins_out = []
        summary_lines = []
        for coin in COINS:
            sym, pair = coin["sym"], coin["pair"]
            try:
                ticker = fetch_kraken_ticker(pair)
                closes = fetch_kraken_klines(pair, interval=240, limit=50)
            except Exception:
                continue
            rsi = calc_rsi(closes) if len(closes) >= 15 else 50.0
            macd = calc_macd(closes) if len(closes) >= 26 else 0.0
            price, chg = ticker["price"], ticker["change_24h"]
            tv = fetch_tv_signal(pair)
            signal = signal_from_indicators(rsi, macd, chg)
            coins_out.append({
                "sym": sym, "price": price, "change_24h": chg,
                "rsi": rsi, "macd": round(macd, 4), "signal": signal,
                "tv_rec": tv["tv_rec"], "tv_score": tv["tv_score"],
                "volume": ticker["volume"],
            })
            summary_lines.append(
                f"{sym}: ${price:,.4f}, 24h={chg:+.1f}%, RSI={rsi}, "
                f"MACD={'up' if macd > 0 else 'dn'}, TV={tv['tv_rec']}, signal={signal}"
            )
        summary = "\n".join(summary_lines) + f"\nFear & Greed: {fg['value']} ({fg['label']})"
        crypto_cache["data"] = {"coins": coins_out, "fear_greed": fg, "ai_analysis": ask_claude(summary)}
        crypto_cache["updated_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        crypto_cache["error"] = str(e)
    finally:
        crypto_cache["updating"] = False


# ════════════════════════════════════════════════════════════════════════════
# STOCKS FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════

def fetch_patria_recommendations():
    url = "https://www.patria.cz/akcie/vyzkum/doporuceni.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "cs-CZ,cs;q=0.9",
        "Referer": "https://www.patria.cz/",
    }
    r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    html = r.text

    results_cz = []
    results_monitor = []

    # ── Czech recommendations ──
    cz_section = re.search(
        r'Patria\s*-\s*Investi[čc]n[ií]\s+doporu[čc]en[ií]\s*-\s*[Čč]R.*?(<table[^>]*>.*?</table>)',
        html, re.DOTALL | re.IGNORECASE
    )
    if cz_section:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', cz_section.group(1), re.DOTALL)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            clean = [re.sub(r'<[^>]+>', '', c).strip().replace('\xa0', ' ') for c in cells]
            # Columns: 0=Název, 1=Aktuální dop., 2=Předchozí dop., 3=Analýza(Ano/odkaz), 4=Cena, 5=Cíl 12M, 6=Potenciál
            if len(clean) >= 5 and clean[0] and clean[0] not in ('Název CP', '') and len(clean[0]) > 1:
                # Calculate potential from price and target if not provided or garbled
                price_str = clean[4] if len(clean) > 4 else ''
                target_str = clean[5] if len(clean) > 5 else ''
                potential_str = clean[6] if len(clean) > 6 else ''
                # Try to compute potential if we have both prices
                try:
                    price_num = float(price_str.replace(' ','').replace(' ','').replace(',','.'))
                    target_num = float(target_str.replace(' ','').replace(' ','').replace(',','.'))
                    if price_num > 0 and target_num > 0:
                        computed = round((target_num - price_num) / price_num * 100, 2)
                        potential_str = f"{computed:+.2f}%"
                except Exception:
                    pass
                results_cz.append({
                    "name": clean[0],
                    "rec": clean[1] if len(clean) > 1 else '',
                    "rec_prev": clean[2] if len(clean) > 2 else '',
                    "price": price_str,
                    "target": target_str,
                    "potential": potential_str,
                })

    # ── Monitoring: find the table with Název CP | Společnost | Nové doporučení columns ──
    # Split HTML into sections, find monitoring table specifically
    # The monitoring table appears after "Monitoring investičních doporučení" heading
    # and has a header row with "Název CP", "Společnost", "Nové doporučení"
    tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)
    for table in tables:
        # Check if this table has the monitoring header
        header_cells = re.findall(r'<th[^>]*>(.*?)</th>', table, re.DOTALL)
        header_text = ' '.join(re.sub(r'<[^>]+>', '', h).strip() for h in header_cells)
        if 'Spole' in header_text and 'doporu' in header_text.lower():
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.DOTALL)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                clean = [re.sub(r'<[^>]+>', '', c).strip().replace('\xa0', ' ') for c in cells]
                # Valid monitoring row: at least 5 cells, name length > 3, not a header
                if len(clean) >= 5 and len(clean[0]) > 3 and not clean[0].isdigit():
                    name = clean[0]
                    if name in ('Název CP', '') or len(name) < 2:
                        continue
                    analyst = clean[1] if len(clean) > 1 else ''
                    rec_new = clean[2] if len(clean) > 2 else ''
                    rec_prev = clean[3] if len(clean) > 3 else ''
                    target = clean[4] if len(clean) > 4 else ''
                    currency = clean[5] if len(clean) > 5 else ''
                    if rec_new and len(rec_new) > 1:
                        results_monitor.append({
                            "name": name,
                            "analyst": analyst,
                            "rec": rec_new,
                            "rec_prev": rec_prev,
                            "target": f"{target} {currency}".strip() if target and target != '0,00' else '',
                        })
            break

    return {
        "cz": results_cz[:25],
        "world_monitor": results_monitor[:25],
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_yahoo_recommendations(symbols):
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for sym in symbols:
        try:
            # Price + basic info
            r1 = requests.get(
                f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"interval": "1d", "range": "2d"},
                headers=headers, timeout=12,
            )
            if r1.status_code != 200:
                continue
            meta = r1.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("regularMarketPreviousClose")
            chg = round((price - prev) / prev * 100, 2) if price and prev else 0
            name = meta.get("longName") or meta.get("shortName") or sym

            # Analyst data
            r2 = requests.get(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}",
                params={"modules": "financialData"},
                headers=headers, timeout=12,
            )
            rec_label, target, potential, analysts = "N/A", None, None, 0
            if r2.status_code == 200:
                fin = r2.json().get("quoteSummary", {}).get("result", [{}])[0].get("financialData", {})
                rec_key = fin.get("recommendationKey", "")
                target_r = fin.get("targetMeanPrice", {})
                target = target_r.get("raw") if isinstance(target_r, dict) else None
                ana_r = fin.get("numberOfAnalystOpinions", {})
                analysts = ana_r.get("raw", 0) if isinstance(ana_r, dict) else 0
                label_map = {
                    "strong_buy": "Strong buy", "buy": "Buy", "hold": "Hold",
                    "underperform": "Underperform", "sell": "Sell",
                }
                if rec_key:
                    rec_label = label_map.get(rec_key, rec_key.replace("_", " ").title())
                if target and price:
                    potential = round((target - price) / price * 100, 1)

            if price:
                results.append({
                    "sym": sym, "name": name,
                    "price": round(price, 2),
                    "change_24h": chg,
                    "rec": rec_label,
                    "target": round(target, 2) if target else None,
                    "potential": potential,
                    "analysts": analysts,
                })
            time.sleep(0.4)
        except Exception:
            continue

    order = {"strong buy": 0, "buy": 1, "hold": 2, "underperform": 3, "sell": 4, "n/a": 5}
    results.sort(key=lambda x: order.get(x.get("rec", "").lower(), 5))
    return results


def refresh_stocks():
    try:
        stocks_cache["patria"] = fetch_patria_recommendations()
    except Exception as e:
        stocks_cache["patria"] = {"error": str(e), "cz": [], "world_monitor": []}
    stocks_cache["patria_at"] = datetime.now(timezone.utc).isoformat()

    try:
        stocks_cache["world"] = fetch_yahoo_recommendations(US_STOCKS)
    except Exception:
        stocks_cache["world"] = []
    stocks_cache["world_at"] = datetime.now(timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════════════════════
# HTML DASHBOARD
# ════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Investiční dashboard</title>
<style>
:root{--bg:#f8f8f6;--card:#fff;--border:rgba(0,0,0,0.08);--text:#1a1a18;--muted:#6b6b68;--green-bg:#eaf3de;--green:#27500a;--red-bg:#fcebeb;--red:#791f1f;--amber-bg:#faeeda;--amber:#633806;--blue:#0c447c}
@media(prefers-color-scheme:dark){:root{--bg:#1a1a18;--card:#242422;--border:rgba(255,255,255,0.09);--text:#e8e6df;--muted:#9a9890;--green-bg:#173404;--green:#c0dd97;--red-bg:#501313;--red:#f09595;--amber-bg:#412402;--amber:#fac775;--blue:#b5d4f4}}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);font-size:14px}
.wrap{max-width:980px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:18px;font-weight:500;margin-bottom:16px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);margin-bottom:20px}
.tab{padding:8px 18px;cursor:pointer;border-radius:8px 8px 0 0;font-size:13px;font-weight:500;color:var(--muted);border:0.5px solid transparent;border-bottom:none;background:transparent}
.tab.active{background:var(--card);color:var(--text);border-color:var(--border);margin-bottom:-1px}
.tab-content{display:none}.tab-content.active{display:block}
.top-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:20px}
.metric{background:var(--card);border:0.5px solid var(--border);border-radius:10px;padding:12px 14px}
.metric .lbl{font-size:11px;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}
.metric .val{font-size:22px;font-weight:500}
.metric .sub{font-size:11px;color:var(--muted);margin-top:2px}
.sec{font-size:11px;font-weight:500;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;margin-top:18px}
.tw{overflow-x:auto;margin-bottom:16px}
table{width:100%;border-collapse:collapse;background:var(--card);border:0.5px solid var(--border);border-radius:10px;overflow:hidden}
th{font-size:11px;color:var(--muted);font-weight:500;padding:9px 11px;text-align:left;border-bottom:0.5px solid var(--border)}
td{padding:9px 11px;border-bottom:0.5px solid var(--border);vertical-align:middle;font-size:13px}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:3px 8px;border-radius:5px;font-size:11px;font-weight:500}
.g{background:var(--green-bg);color:var(--green)}.r{background:var(--red-bg);color:var(--red)}.a{background:var(--amber-bg);color:var(--amber)}.n{opacity:.5}
.up{color:var(--green)}.dn{color:var(--red)}
.fg-w{height:6px;background:var(--border);border-radius:3px;flex:1;overflow:hidden}
.fg-f{height:100%;border-radius:3px}
.rbar{width:55px;height:5px;background:var(--border);border-radius:3px;display:inline-block;vertical-align:middle;margin-left:4px}
.rfill{height:100%;border-radius:3px}
.ai-box{background:var(--card);border:0.5px solid var(--border);border-radius:10px;padding:16px;line-height:1.65;white-space:pre-wrap;font-size:13px}
.btn{font-size:12px;padding:6px 12px;border:0.5px solid var(--border);border-radius:7px;background:transparent;color:var(--text);cursor:pointer}
.btn:hover{background:var(--border)}
.err{background:var(--red-bg);color:var(--red);border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:13px}
.info{font-size:11px;color:var(--muted);padding:8px 12px;background:var(--card);border:0.5px solid var(--border);border-radius:8px;line-height:1.5;margin-bottom:12px}
.note{font-size:11px;color:var(--muted);margin-top:10px}
.sp{display:inline-block;width:11px;height:11px;border:2px solid var(--border);border-top-color:var(--muted);border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:5px}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:#639922;margin-right:5px}
.dot.upd{background:#ef9f27;animation:pulse .8s infinite}
.hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px}
.hdr-l{display:flex;align-items:center;gap:6px;font-weight:500;font-size:14px}
.sm{font-size:12px;color:var(--muted);font-weight:400}
@media(max-width:600px){.hm{display:none}td,th{padding:7px 7px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>Investiční dashboard</h1>
  <div class="tabs">
    <button class="tab active" onclick="sw('crypto',this)">Krypto</button>
    <button class="tab" onclick="sw('cz',this)">Akcie CZ (Patria)</button>
    <button class="tab" onclick="sw('us',this)">Akcie US (Yahoo)</button>
  </div>

  <!-- CRYPTO -->
  <div id="tab-crypto" class="tab-content active">
    <div class="hdr">
      <div class="hdr-l"><span class="dot" id="dot"></span>Crypto signály <span class="sm" id="crypto-meta"></span></div>
      <button class="btn" onclick="loadCrypto()">Obnovit ↻</button>
    </div>
    <div id="cerr"></div>
    <div class="top-grid">
      <div class="metric"><div class="lbl">BTC</div><div class="val" id="btc-p">—</div><div class="sub" id="btc-c"></div></div>
      <div class="metric"><div class="lbl">ETH</div><div class="val" id="eth-p">—</div><div class="sub" id="eth-c"></div></div>
      <div class="metric">
        <div class="lbl">Fear &amp; Greed</div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
          <span class="val" id="fg-v">—</span>
          <span class="fg-w"><span class="fg-f" id="fg-b" style="width:0%"></span></span>
        </div>
        <div class="sub" id="fg-l"></div>
      </div>
      <div class="metric"><div class="lbl">Aktualizace</div><div class="val" style="font-size:14px" id="upd-t">—</div><div class="sub" id="upd-a"></div></div>
    </div>
    <div class="sec" style="margin-top:4px">Signály</div>
    <div class="tw"><table>
      <thead><tr><th>Coin</th><th>Cena</th><th>24h</th><th class="hm">RSI</th><th class="hm">TV signal</th><th>Signal</th></tr></thead>
      <tbody id="coin-tb"><tr><td colspan="6" style="text-align:center;padding:20px;color:var(--muted)"><span class="sp"></span>Načítám...</td></tr></tbody>
    </table></div>
    <div class="sec">AI analýza (Claude)</div>
    <div class="ai-box" id="ai-box" style="color:var(--muted);font-style:italic"><span class="sp"></span>Čekám...</div>
    <p class="note">Refresh každou hodinu · Kraken API · Toto není finanční poradenství.</p>
  </div>

  <!-- CZ STOCKS -->
  <div id="tab-cz" class="tab-content">
    <div class="hdr">
      <div class="hdr-l">Akcie CZ — Patria <span class="sm" id="patria-meta"></span></div>
      <button class="btn" onclick="loadStocks(true)">Obnovit ↻</button>
    </div>
    <div id="serr"></div>
    <div class="info">Data ze <a href="https://www.patria.cz/akcie/vyzkum/doporuceni.html" target="_blank" style="color:var(--blue)">patria.cz/akcie/vyzkum/doporuceni.html</a> · Refresh 1× denně · Zdroj: Patria Finance / KBC Securities</div>
    <div class="sec" style="margin-top:4px">Patria Finance — doporučení ČR</div>
    <div class="tw"><table>
      <thead><tr><th>Akcie</th><th>Doporučení</th><th class="hm">Předchozí</th><th>Cena</th><th>Cíl 12M</th><th>Potenciál</th></tr></thead>
      <tbody id="cz-tb"><tr><td colspan="6" style="text-align:center;padding:20px;color:var(--muted)"><span class="sp"></span>Načítám...</td></tr></tbody>
    </table></div>
    <div class="sec">Monitoring — doporučení globálních bank</div>
    <div class="tw"><table>
      <thead><tr><th>Akcie</th><th class="hm">Analytická firma</th><th>Doporučení</th><th class="hm">Předchozí</th><th>Cílová cena</th></tr></thead>
      <tbody id="mon-tb"><tr><td colspan="5" style="text-align:center;padding:20px;color:var(--muted)"><span class="sp"></span>Načítám...</td></tr></tbody>
    </table></div>
    <p class="note">Toto není finanční poradenství.</p>
  </div>

  <!-- US STOCKS -->
  <div id="tab-us" class="tab-content">
    <div class="hdr">
      <div class="hdr-l">Akcie US — Yahoo Finance <span class="sm" id="yahoo-meta"></span></div>
      <button class="btn" onclick="loadStocks(true)">Obnovit ↻</button>
    </div>
    <div class="info">Konsenzus analytiků · Yahoo Finance · Refresh 1× denně · Řazeno: nejsilnější doporučení nahoře</div>
    <div class="tw"><table>
      <thead><tr><th>Ticker</th><th class="hm">Název</th><th>Cena</th><th>24h</th><th>Konsenzus</th><th class="hm">Cíl. cena</th><th>Potenciál</th></tr></thead>
      <tbody id="us-tb"><tr><td colspan="7" style="text-align:center;padding:20px;color:var(--muted)"><span class="sp"></span>Načítám...</td></tr></tbody>
    </table></div>
    <p class="note">Toto není finanční poradenství.</p>
  </div>
</div>
<script>
function fp(n){if(!n&&n!==0)return'—';if(n>=1000)return'$'+n.toLocaleString('cs-CZ',{maximumFractionDigits:0});if(n>=1)return'$'+n.toFixed(2);return'$'+n.toFixed(4)}
function fage(iso){if(!iso)return'';const d=Math.round((Date.now()-new Date(iso))/60000);if(d<2)return'právě teď';if(d<60)return`před ${d} min`;if(d<1440)return`před ${Math.floor(d/60)} hod`;return`před ${Math.floor(d/1440)} dny`}
function rc(r){return r<30?'#e24b4a':r<45?'#639922':r>70?'#e24b4a':r>55?'#ef9f27':'#888780'}
function bc(r){
  if(!r||r==='N/A')return'n';
  const s=r.toLowerCase();
  if(s.includes('strong buy')||s==='koupit'||s==='akumulovat'||s.includes('buy'))return'g';
  if(s.includes('strong sell')||s==='prodat'||s==='redukovat'||s.includes('sell'))return'r';
  return'a';
}
function sw(id,btn){
  document.querySelectorAll('.tab-content').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  btn.classList.add('active');
  if(id==='cz'||id==='us')loadStocks();
}
async function loadCrypto(){
  document.getElementById('dot').className='dot upd';
  try{
    const d=await fetch('/api/data').then(r=>r.json());
    if(d.error&&!d.coins){document.getElementById('cerr').innerHTML=`<div class="err">${d.error}</div>`;document.getElementById('dot').className='dot';return}
    document.getElementById('cerr').innerHTML='';
    const btc=d.coins?.find(c=>c.sym==='BTC'),eth=d.coins?.find(c=>c.sym==='ETH');
    if(btc){document.getElementById('btc-p').textContent=fp(btc.price);const b=btc.change_24h;document.getElementById('btc-c').innerHTML=`<span class="${b>=0?'up':'dn'}">${b>=0?'+':''}${b.toFixed(2)}%</span>`}
    if(eth){document.getElementById('eth-p').textContent=fp(eth.price);const e=eth.change_24h;document.getElementById('eth-c').innerHTML=`<span class="${e>=0?'up':'dn'}">${e>=0?'+':''}${e.toFixed(2)}%</span>`}
    const fg=d.fear_greed||{};
    document.getElementById('fg-v').textContent=fg.value||'—';
    document.getElementById('fg-l').textContent=fg.label||'';
    const b=document.getElementById('fg-b');b.style.width=(fg.value||0)+'%';
    b.style.background=fg.value<30?'#e24b4a':fg.value<50?'#ef9f27':fg.value<75?'#639922':'#3b6d11';
    const dt=new Date(d.updated_at);
    document.getElementById('upd-t').textContent=dt.toLocaleTimeString('cs-CZ',{hour:'2-digit',minute:'2-digit'});
    document.getElementById('upd-a').textContent=fage(d.updated_at);
    document.getElementById('crypto-meta').textContent=fage(d.updated_at);
    let rows='';
    for(const c of d.coins||[]){
      const cc=c.change_24h>=0?'up':'dn',ct=(c.change_24h>=0?'+':'')+c.change_24h.toFixed(2)+'%';
      const rp=Math.min(Math.max(c.rsi,0),100);
      const sc=c.signal==='LONG'?'g':c.signal==='SHORT'?'r':'a';
      rows+=`<tr><td><strong>${c.sym}</strong></td><td>${fp(c.price)}</td><td class="${cc}">${ct}</td><td class="hm">${c.rsi}<span class="rbar"><span class="rfill" style="width:${rp}%;background:${rc(c.rsi)}"></span></span></td><td class="hm"><span class="badge ${bc(c.tv_rec)}">${c.tv_rec}</span></td><td><span class="badge ${sc}">${c.signal}</span></td></tr>`;
    }
    document.getElementById('coin-tb').innerHTML=rows||'<tr><td colspan="6" style="text-align:center;color:var(--muted)">Žádná data</td></tr>';
    const ai=document.getElementById('ai-box');ai.style.fontStyle='normal';ai.textContent=d.ai_analysis||'—';
    document.getElementById('dot').className='dot';
  }catch(e){document.getElementById('cerr').innerHTML=`<div class="err">Chyba: ${e.message}</div>`;document.getElementById('dot').className='dot'}
}
let sDone=false;
async function loadStocks(force){
  if(sDone&&!force)return;sDone=true;
  try{
    const d=await fetch('/api/stocks').then(r=>r.json());
    document.getElementById('patria-meta').textContent=fage(d.patria_at);
    document.getElementById('yahoo-meta').textContent=fage(d.world_at);
    const cz=d.patria?.cz||[];
    let czR='';
    if(d.patria?.error)czR=`<tr><td colspan="6" style="color:var(--muted);padding:12px;font-size:12px">${d.patria.error}</td></tr>`;
    else if(cz.length){for(const s of cz){const pot=s.potential?`<span class="${parseFloat(s.potential)>=0?'up':'dn'}">${s.potential}%</span>`:'—';czR+=`<tr><td><strong>${s.name}</strong></td><td><span class="badge ${bc(s.rec)}">${s.rec||'—'}</span></td><td class="hm" style="color:var(--muted)">${s.rec_prev||'—'}</td><td>${s.price||'—'}</td><td>${s.target||'—'}</td><td>${pot}</td></tr>`;}}
    else czR='<tr><td colspan="6" style="text-align:center;padding:20px;color:var(--muted)">Data z Patrie se nepodařilo načíst</td></tr>';
    document.getElementById('cz-tb').innerHTML=czR;
    const mon=d.patria?.world_monitor||[];
    let monR='';
    if(mon.length){for(const s of mon)monR+=`<tr><td><strong>${s.name}</strong></td><td class="hm" style="color:var(--muted);font-size:12px">${s.analyst||'—'}</td><td><span class="badge ${bc(s.rec)}">${s.rec||'—'}</span></td><td class="hm" style="color:var(--muted);font-size:12px">${s.rec_prev||'—'}</td><td>${s.target||'—'}</td></tr>`;}
    else monR='<tr><td colspan="5" style="text-align:center;padding:20px;color:var(--muted)">Žádná data</td></tr>';
    document.getElementById('mon-tb').innerHTML=monR;
    const us=d.world||[];
    let usR='';
    if(us.length){for(const s of us){const cc=s.change_24h>=0?'up':'dn',ct=(s.change_24h>=0?'+':'')+s.change_24h.toFixed(2)+'%';const pot=s.potential!=null?`<span class="${s.potential>=0?'up':'dn'}">${s.potential>0?'+':''}${s.potential}%</span>`:'—';usR+=`<tr><td><strong>${s.sym}</strong></td><td class="hm" style="font-size:12px;color:var(--muted)">${s.name||''}</td><td>${fp(s.price)}</td><td class="${cc}">${ct}</td><td><span class="badge ${bc(s.rec)}">${s.rec}</span></td><td class="hm">${s.target?fp(s.target):'—'}</td><td>${pot}</td></tr>`;}}
    else usR='<tr><td colspan="7" style="text-align:center;padding:20px;color:var(--muted)">Načítám Yahoo Finance (může trvat 1–2 min)...</td></tr>';
    document.getElementById('us-tb').innerHTML=usR;
  }catch(e){document.getElementById('serr').innerHTML=`<div class="err">Chyba: ${e.message}</div>`;}
}
loadCrypto();
setInterval(loadCrypto,60*60*1000);
</script>
</body>
</html>"""


# ════════════════════════════════════════════════════════════════════════════
# ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/data")
def api_data():
    if crypto_cache["data"] is None and not crypto_cache["updating"]:
        refresh_crypto()
    if crypto_cache["error"] and crypto_cache["data"] is None:
        return jsonify({"error": crypto_cache["error"]})
    return jsonify({
        **(crypto_cache["data"] or {}),
        "updated_at": crypto_cache["updated_at"],
        "updating": crypto_cache["updating"],
        "error": crypto_cache["error"],
    })


@app.route("/api/stocks")
def api_stocks():
    pat_at = stocks_cache.get("patria_at")
    needs = (
        stocks_cache["patria"] is None or
        (pat_at and (datetime.now(timezone.utc) - datetime.fromisoformat(pat_at)) > timedelta(hours=23))
    )
    if needs:
        refresh_stocks()
    return jsonify({
        "patria": stocks_cache.get("patria") or {},
        "world": stocks_cache.get("world") or [],
        "patria_at": stocks_cache.get("patria_at"),
        "world_at": stocks_cache.get("world_at"),
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    refresh_crypto()
    return jsonify({"ok": True})


@app.route("/api/stocks/refresh", methods=["POST"])
def api_stocks_refresh():
    refresh_stocks()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

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
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30, headers=headers)
            if r.status_code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            r.raise_for_status()
            return {item["id"]: item for item in r.json()}
        except requests.exceptions.RequestException:
            if attempt == 2:
                raise
            time.sleep(10)
    raise Exception("CoinGecko API nedostupne")


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
<title>Crypto signals</title>
<style>
:root {
  --bg: #f8f8f6;
  --card: #ffffff;
  --border: rgba(0,0,0,0.08);
  --text: #1a1a18;
  --muted: #6b6b68;
  --green-bg: #eaf3de; --green: #27500a;
  --red-bg: #fcebeb;   --red: #791f1f;
  --amber-bg: #faeeda; --amber: #633806;
  --blue-bg: #e6f1fb;  --blue: #0c447c;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1a1a18; --card: #242422; --border: rgba(255,255,255,0.09);
    --text: #e8e6df; --muted: #9a9890;
    --green-bg: #173404; --green: #c0dd97;
    --red-bg: #501313;   --red: #f09595;
    --amber-bg: #412402; --amber: #fac775;
    --blue-bg: #042c53;  --blue: #b5d4f4;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); font-size: 14px; }
.wrap { max-width: 960px; margin: 0 auto; padding: 20px 16px 40px; }
header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 8px; }
h1 { font-size: 18px; font-weight: 500; }
.meta { font-size: 12px; color: var(--muted); }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #639922; margin-right: 5px; }
.dot.updating { background: #ef9f27; animation: pulse .8s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

.top-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 20px; }
.metric { background: var(--card); border: 0.5px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.metric .label { font-size: 11px; color: var(--muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: .04em; }
.metric .val { font-size: 22px; font-weight: 500; }
.metric .sub { font-size: 11px; color: var(--muted); margin-top: 2px; }

.section-title { font-size: 11px; font-weight: 500; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px; }

.table-wrap { overflow-x: auto; margin-bottom: 20px; }
table { width: 100%; border-collapse: collapse; background: var(--card); border: 0.5px solid var(--border); border-radius: 10px; overflow: hidden; }
th { font-size: 11px; color: var(--muted); font-weight: 500; padding: 10px 12px; text-align: left; border-bottom: 0.5px solid var(--border); }
td { padding: 10px 12px; border-bottom: 0.5px solid var(--border); vertical-align: middle; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(0,0,0,0.02); }
.coin-sym { font-weight: 500; font-size: 14px; }
.price { font-weight: 500; }
.chg.up { color: var(--green); }
.chg.down { color: var(--red); }

.badge { display: inline-block; padding: 3px 9px; border-radius: 5px; font-size: 11px; font-weight: 500; }
.badge.LONG    { background: var(--green-bg); color: var(--green); }
.badge.SHORT   { background: var(--red-bg);   color: var(--red); }
.badge.NEUTRAL { background: var(--amber-bg); color: var(--amber); }
.badge.buy, .badge.strong-buy { background: var(--green-bg); color: var(--green); }
.badge.sell, .badge.strong-sell { background: var(--red-bg); color: var(--red); }
.badge.neutral { background: var(--amber-bg); color: var(--amber); }
.badge.na { background: var(--border); color: var(--muted); }

.rsi-bar { width: 60px; height: 5px; background: var(--border); border-radius: 3px; display: inline-block; vertical-align: middle; margin-left: 4px; }
.rsi-fill { height: 100%; border-radius: 3px; }

.ai-box { background: var(--card); border: 0.5px solid var(--border); border-radius: 10px; padding: 16px; line-height: 1.65; white-space: pre-wrap; }
.ai-box.loading { color: var(--muted); font-style: italic; }

.fg-bar-wrap { height: 6px; background: var(--border); border-radius: 3px; flex: 1; overflow: hidden; }
.fg-bar { height: 100%; border-radius: 3px; }

.refresh-btn { font-size: 12px; padding: 6px 12px; border: 0.5px solid var(--border); border-radius: 7px; background: transparent; color: var(--text); cursor: pointer; }
.refresh-btn:hover { background: var(--border); }

.error-box { background: var(--red-bg); color: var(--red); border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; font-size: 13px; }
.spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid var(--border); border-top-color: var(--muted); border-radius: 50%; animation: spin .7s linear infinite; vertical-align: middle; margin-right: 6px; }
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 600px) {
  .hide-mobile { display: none; }
  td, th { padding: 8px 8px; }
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1><span class="dot" id="dot"></span>Crypto signals</h1>
      <div class="meta" id="meta">Načítám...</div>
    </div>
    <button class="refresh-btn" onclick="load()">Obnovit ↻</button>
  </header>

  <div id="error-wrap"></div>

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
      <thead>
        <tr>
          <th>Coin</th>
          <th>Cena</th>
          <th>24h</th>
          <th class="hide-mobile">RSI</th>
          <th class="hide-mobile">TV signal</th>
          <th>Náš signal</th>
        </tr>
      </thead>
      <tbody id="coin-tbody">
        <tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted)"><span class="spinner"></span>Načítám data...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="section-title" style="margin-top:20px">AI analýza (Claude)</div>
  <div class="ai-box loading" id="ai-box"><span class="spinner"></span>Čekám na analýzu...</div>

  <div style="margin-top:12px;font-size:11px;color:var(--muted);line-height:1.5">
    Data se automaticky aktualizují každou hodinu. Toto není finanční poradenství — vždy proveď vlastní analýzu.
  </div>
</div>

<script>
function fmt(n) {
  if (!n && n !== 0) return '—';
  if (n >= 1000) return '$' + n.toLocaleString('cs-CZ', {maximumFractionDigits: 0});
  if (n >= 1) return '$' + n.toFixed(2);
  return '$' + n.toFixed(4);
}
function fmtAge(iso) {
  if (!iso) return '';
  const diff = Math.round((Date.now() - new Date(iso)) / 60000);
  if (diff < 2) return 'právě teď';
  if (diff < 60) return `před ${diff} min`;
  return `před ${Math.floor(diff/60)} hod`;
}
function tvBadgeClass(rec) {
  if (!rec || rec === 'N/A') return 'na';
  const r = rec.toLowerCase();
  if (r.includes('strong buy')) return 'strong-buy';
  if (r.includes('buy')) return 'buy';
  if (r.includes('strong sell')) return 'strong-sell';
  if (r.includes('sell')) return 'sell';
  return 'neutral';
}
function rsiColor(rsi) {
  if (rsi < 30) return '#e24b4a';
  if (rsi < 45) return '#639922';
  if (rsi > 70) return '#e24b4a';
  if (rsi > 55) return '#ef9f27';
  return '#888780';
}

async function load() {
  document.getElementById('dot').className = 'dot updating';
  document.getElementById('meta').textContent = 'Načítám...';
  try {
    const r = await fetch('/api/data');
    const d = await r.json();
    if (d.error) {
      document.getElementById('error-wrap').innerHTML = `<div class="error-box">Chyba: ${d.error}</div>`;
      document.getElementById('dot').className = 'dot';
      return;
    }
    document.getElementById('error-wrap').innerHTML = '';

    const btc = d.coins.find(c => c.sym === 'BTC');
    const eth = d.coins.find(c => c.sym === 'ETH');
    if (btc) {
      document.getElementById('btc-price').textContent = fmt(btc.price);
      const bc = btc.change_24h;
      document.getElementById('btc-chg').innerHTML = `<span class="${bc >= 0 ? 'chg up' : 'chg down'}">${bc >= 0 ? '+' : ''}${bc.toFixed(2)}%</span>`;
    }
    if (eth) {
      document.getElementById('eth-price').textContent = fmt(eth.price);
      const ec = eth.change_24h;
      document.getElementById('eth-chg').innerHTML = `<span class="${ec >= 0 ? 'chg up' : 'chg down'}">${ec >= 0 ? '+' : ''}${ec.toFixed(2)}%</span>`;
    }

    const fg = d.fear_greed;
    document.getElementById('fg-val').textContent = fg.value;
    document.getElementById('fg-label').textContent = fg.label;
    const bar = document.getElementById('fg-bar');
    bar.style.width = fg.value + '%';
    bar.style.background = fg.value < 30 ? '#e24b4a' : fg.value < 50 ? '#ef9f27' : fg.value < 75 ? '#639922' : '#3b6d11';

    const ts = d.updated_at;
    const dt = new Date(ts);
    document.getElementById('update-time').textContent = dt.toLocaleTimeString('cs-CZ', {hour:'2-digit',minute:'2-digit'});
    document.getElementById('update-age').textContent = fmtAge(ts);

    let rows = '';
    for (const c of d.coins) {
      const chgClass = c.change_24h >= 0 ? 'chg up' : 'chg down';
      const chgTxt = (c.change_24h >= 0 ? '+' : '') + c.change_24h.toFixed(2) + '%';
      const rsiPct = Math.min(Math.max(c.rsi, 0), 100);
      const tvClass = tvBadgeClass(c.tv_rec);
      rows += `<tr>
        <td><span class="coin-sym">${c.sym}</span></td>
        <td class="price">${fmt(c.price)}</td>
        <td class="${chgClass}">${chgTxt}</td>
        <td class="hide-mobile">
          ${c.rsi}
          <span class="rsi-bar"><span class="rsi-fill" style="width:${rsiPct}%;background:${rsiColor(c.rsi)}"></span></span>
        </td>
        <td class="hide-mobile"><span class="badge ${tvClass}">${c.tv_rec}</span></td>
        <td><span class="badge ${c.signal}">${c.signal}</span></td>
      </tr>`;
    }
    document.getElementById('coin-tbody').innerHTML = rows;

    const aiBox = document.getElementById('ai-box');
    aiBox.className = 'ai-box';
    aiBox.textContent = d.ai_analysis || '—';

    document.getElementById('dot').className = 'dot';
    document.getElementById('meta').textContent = `Aktualizováno ${fmtAge(ts)} · auto-refresh za ${timeToNextHour()} min`;

  } catch(e) {
    document.getElementById('error-wrap').innerHTML = `<div class="error-box">Nepodařilo se načíst data: ${e.message}</div>`;
    document.getElementById('dot').className = 'dot';
  }
}

function timeToNextHour() {
  const now = new Date();
  return 60 - now.getMinutes();
}

load();
setInterval(load, 60 * 60 * 1000);
setInterval(() => {
  const meta = document.getElementById('meta');
  if (!meta.textContent.includes('Načítám')) {
    meta.textContent = `Auto-refresh za ${timeToNextHour()} min`;
  }
}, 60000);
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

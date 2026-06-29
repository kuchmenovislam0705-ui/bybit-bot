"""
Веб-дашборд + TradingView Webhook — FastAPI JSON API + встроенный HTML.
Запускается в фоновом потоке из main.py.
URL: http://localhost:8080
Webhook: POST http://localhost:8080/webhook/tradingview
"""
import threading
import logging
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

import analytics
import config
import notifications

logger = logging.getLogger("webapp")

app = FastAPI(title="Bybit Bot Dashboard", docs_url=None, redoc_url=None)

# ── TradingView Webhook хранилище ─────────────────────────────────────────────
# Последний алерт по каждому символу — для boost в screeners.py
_tv_alerts: dict = {}   # symbol -> {"direction", "price", "ts", "message"}

# Глобальная ссылка на состояние бота (устанавливается из main.py)
_state  = None
_tickers_map: dict = {}
_signals_long:  list = []
_signals_short: list = []


def set_state(state, tickers_map=None, signals_long=None, signals_short=None):
    global _state, _tickers_map, _signals_long, _signals_short
    _state = state
    if tickers_map   is not None: _tickers_map   = tickers_map
    if signals_long  is not None: _signals_long  = signals_long
    if signals_short is not None: _signals_short = signals_short


# ── TradingView Webhook ───────────────────────────────────────────────────────

# Нормализация тикеров TradingView → Bybit символ
# TradingView шлёт "BYBIT:XAUUSDT.P", "XAUUSDT", "GOLD" и т.д.
_TV_TICKER_MAP = {
    "XAUUSDT": "XAUUSDT", "GOLD": "XAUUSDT", "XAUUSDT.P": "XAUUSDT",
    "XAGUSDT": "XAGUSDT", "SILVER": "XAGUSDT", "XAGUSDT.P": "XAGUSDT",
    "BTCUSDT": "BTCUSDT", "BTCUSD": "BTCUSDT",  "BTCUSDT.P": "BTCUSDT",
    "ETHUSDT": "ETHUSDT", "ETHUSD": "ETHUSDT",  "ETHUSDT.P": "ETHUSDT",
    "SOLUSDT": "SOLUSDT", "SOLUSD": "SOLUSDT",  "SOLUSDT.P": "SOLUSDT",
    "BNBUSDT": "BNBUSDT", "BNBUSD": "BNBUSDT",  "BNBUSDT.P": "BNBUSDT",
    "XRPUSDT": "XRPUSDT", "XRPUSD": "XRPUSDT",  "XRPUSDT.P": "XRPUSDT",
}


def _normalize_ticker(raw: str) -> Optional[str]:
    """BYBIT:XAUUSDT.P → XAUUSDT"""
    t = raw.upper().strip()
    # Убираем префикс биржи
    if ":" in t:
        t = t.split(":")[-1]
    # Убираем суффиксы (.P, .F, PERP и т.д.)
    t = t.replace(".PERP", "").replace(".F", "").replace(".P", "")
    # Если не оканчивается на USDT — добавляем
    if not t.endswith("USDT"):
        t = t + "USDT"
    return _TV_TICKER_MAP.get(t, t if t in config.SIGNAL_INSTRUMENTS else None)


@app.post("/webhook/tradingview")
async def tv_webhook(request: Request):
    """
    Принимает алерт от TradingView и отправляет в Telegram.

    Ожидаемый JSON (настраивается в алерте TradingView):
    {
      "symbol":    "{{ticker}}",
      "action":    "buy",           // buy / sell / close
      "price":     {{close}},
      "message":   "EMA cross",     // произвольный текст
      "secret":    "ВАШ_ТОКЕН"      // опционально, если задан в .env
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Проверка секретного токена (если задан в .env)
    secret = getattr(config, "TV_WEBHOOK_SECRET", "")
    if secret and body.get("secret") != secret:
        raise HTTPException(status_code=403, detail="Invalid secret")

    raw_ticker = str(body.get("symbol") or body.get("ticker") or "")
    if not raw_ticker:
        raise HTTPException(status_code=400, detail="symbol required")

    symbol = _normalize_ticker(raw_ticker)
    if not symbol:
        logger.warning(f"TV webhook: неизвестный тикер '{raw_ticker}'")
        return JSONResponse({"ok": False, "error": f"unknown symbol: {raw_ticker}"})

    action  = str(body.get("action") or body.get("side") or "").lower()
    price   = body.get("price") or body.get("close") or 0
    message = str(body.get("message") or body.get("comment") or "")
    interval = str(body.get("interval") or body.get("tf") or "")
    now_utc = datetime.now(timezone.utc)

    # Сохраняем для boost в скринере
    direction = "Buy" if action in ("buy", "long") else ("Sell" if action in ("sell", "short") else "")
    _tv_alerts[symbol] = {
        "direction": direction,
        "price":     float(price) if price else 0,
        "message":   message,
        "ts":        now_utc,
    }

    # ── Telegram уведомление ──────────────────────────────────────────────────
    names = {"XAUUSDT": "Золото", "XAGUSDT": "Серебро", "BTCUSDT": "Bitcoin",
             "ETHUSDT": "Ethereum", "SOLUSDT": "Solana", "BNBUSDT": "BNB", "XRPUSDT": "XRP"}
    emojis = {"Buy": "📈", "Sell": "📉", "": "📊"}

    dir_ru  = {"Buy": "ЛОНГ", "Sell": "ШОРТ", "": "АЛЕРТ"}
    arrow   = {"Buy": "▲", "Sell": "▼", "": "◆"}

    sym_name = names.get(symbol, symbol)
    em       = emojis.get(direction, "📊")
    tf_str   = f" [{interval}]" if interval else ""

    lines = [
        f"{em} <b>TradingView{tf_str} — {sym_name}</b>",
        f"<b>{arrow.get(direction, '◆')} {dir_ru.get(direction, 'АЛЕРТ')}</b>"
        + (f"  |  Цена: <code>{float(price):.2f}</code>" if price else ""),
    ]
    if message:
        lines.append(f"💬 {message}")
    lines.append(f"<i>⏰ {now_utc.strftime('%H:%M UTC')}</i>")

    notifications._send("\n".join(lines))
    logger.info(f"TV webhook: {symbol} {direction or 'alert'} price={price} msg={message!r}")

    return JSONResponse({"ok": True, "symbol": symbol, "direction": direction})


def get_tv_boost(symbol: str, direction: str, max_age_minutes: int = 20) -> int:
    """
    Возвращает +2 если TradingView прислал алерт в том же направлении
    в последние max_age_minutes минут. Используется в screeners.py.
    """
    alert = _tv_alerts.get(symbol)
    if not alert:
        return 0
    age = (datetime.now(timezone.utc) - alert["ts"]).total_seconds() / 60
    if age > max_age_minutes:
        return 0
    if not alert["direction"] or alert["direction"] == direction:
        return 2
    return -1   # алерт в противоположном направлении — штраф


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status():
    if not _state:
        return {"status": "initializing"}
    d = _state.daily
    history = _state.trade_history
    report  = analytics.full_report(history)
    return {
        "status":    "running",
        "balance":   round(_state.daily.get("start_equity", 0), 2),
        "day_pnl":   round(d.get("total_pnl", 0), 2),
        "positions": _state.open_count,
        "max_pos":   config.MAX_POSITIONS,
        "trades":    len(history),
        "wins":      d.get("wins", 0),
        "losses":    d.get("losses", 0),
        "analytics": report,
        "time":      datetime.utcnow().isoformat(),
    }


@app.get("/api/positions")
def api_positions():
    if not _state:
        return []
    out = []
    for symbol, pos in _state.positions.items():
        price = float(_tickers_map.get(symbol, {}).get("lastPrice", pos["entry_price"]))
        pnl   = ((price - pos["entry_price"]) * pos["qty"]
                 if pos["side"] == "Buy"
                 else (pos["entry_price"] - price) * pos["qty"])
        out.append({
            "symbol":    symbol,
            "side":      pos["side"],
            "entry":     pos["entry_price"],
            "current":   price,
            "qty":       pos["qty"],
            "sl":        pos.get("sl", 0),
            "tp":        pos.get("tp1", 0),
            "pnl":       round(pnl, 2),
            "signal":    pos.get("signal_type", "?"),
        })
    return out


@app.get("/api/signals")
def api_signals():
    longs  = [{"direction": "Buy",  **s} for s in _signals_long[:10]]
    shorts = [{"direction": "Sell", **s} for s in _signals_short[:10]]
    return {"long": longs, "short": shorts}


@app.get("/api/history")
def api_history():
    if not _state:
        return []
    return list(reversed(_state.trade_history[-50:]))


@app.get("/api/analytics")
def api_analytics():
    if not _state:
        return {}
    return analytics.full_report(_state.trade_history)


# ── HTML Dashboard ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=_HTML)


_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bybit Bot Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', monospace; }
.header { background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 24px;
          display: flex; align-items: center; justify-content: space-between; }
.header h1 { font-size: 18px; color: #58a6ff; }
.badge { background: #21262d; border: 1px solid #30363d; border-radius: 6px;
         padding: 4px 10px; font-size: 12px; color: #8b949e; }
.badge.live { border-color: #238636; color: #3fb950; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 16px; padding: 24px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }
.card .label { font-size: 12px; color: #8b949e; text-transform: uppercase; margin-bottom: 8px; }
.card .value { font-size: 28px; font-weight: 700; }
.green { color: #3fb950; } .red { color: #f85149; } .blue { color: #58a6ff; }
.yellow { color: #d29922; }
section { padding: 0 24px 24px; }
section h2 { font-size: 14px; color: #8b949e; text-transform: uppercase;
             margin-bottom: 12px; letter-spacing: 0.5px; }
table { width: 100%; border-collapse: collapse; background: #161b22;
        border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
th { background: #21262d; padding: 10px 14px; text-align: left; font-size: 12px;
     color: #8b949e; text-transform: uppercase; }
td { padding: 10px 14px; font-size: 13px; border-top: 1px solid #21262d; }
tr:hover td { background: #1c2128; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.tag.buy { background: #0d2a14; color: #3fb950; border: 1px solid #238636; }
.tag.sell { background: #2d0f0f; color: #f85149; border: 1px solid #da3633; }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
              gap: 12px; margin-bottom: 24px; }
.stat { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
        padding: 14px; text-align: center; }
.stat .s-label { font-size: 11px; color: #8b949e; }
.stat .s-val { font-size: 20px; font-weight: 700; margin-top: 4px; }
#last-update { font-size: 11px; color: #8b949e; }
</style>
</head>
<body>
<div class="header">
  <h1>⚡ Bybit Bot Dashboard</h1>
  <div style="display:flex;gap:12px;align-items:center">
    <span id="last-update">обновление...</span>
    <span class="badge live">● MAINNET</span>
  </div>
</div>

<div class="grid" id="kpi"></div>

<section>
  <h2>Аналитика</h2>
  <div class="stats-grid" id="analytics"></div>
</section>

<section>
  <h2>Открытые позиции</h2>
  <table>
    <thead><tr><th>Монета</th><th>Напр.</th><th>Вход</th><th>Текущ.</th><th>PnL</th><th>SL</th><th>TP</th><th>Сигнал</th></tr></thead>
    <tbody id="positions"><tr><td colspan="8" style="color:#8b949e;text-align:center">загрузка...</td></tr></tbody>
  </table>
</section>

<section>
  <h2>Текущие сигналы</h2>
  <table>
    <thead><tr><th>Монета</th><th>Тип</th><th>RSI</th><th>1H%</th><th>RVOL</th><th>OI%</th><th>ATR%</th><th>Паттерн</th></tr></thead>
    <tbody id="signals"><tr><td colspan="8" style="color:#8b949e;text-align:center">загрузка...</td></tr></tbody>
  </table>
</section>

<section>
  <h2>История сделок</h2>
  <table>
    <thead><tr><th>Время</th><th>Монета</th><th>Напр.</th><th>Вход</th><th>PnL</th><th>Исход</th></tr></thead>
    <tbody id="history"><tr><td colspan="6" style="color:#8b949e;text-align:center">загрузка...</td></tr></tbody>
  </table>
</section>

<script>
function fmt(n, d=4) { return n == null ? '—' : Number(n).toFixed(d); }
function pnlColor(v) { return v >= 0 ? 'green' : 'red'; }

async function refresh() {
  const [status, positions, signals, history] = await Promise.all([
    fetch('/api/status').then(r=>r.json()),
    fetch('/api/positions').then(r=>r.json()),
    fetch('/api/signals').then(r=>r.json()),
    fetch('/api/history').then(r=>r.json()),
  ]);

  // KPI cards
  const d = status;
  const wr = d.analytics?.win_rate ?? 0;
  document.getElementById('kpi').innerHTML = `
    <div class="card"><div class="label">Баланс</div><div class="value blue">$${fmt(d.balance,2)}</div></div>
    <div class="card"><div class="label">PnL сегодня</div><div class="value ${pnlColor(d.day_pnl)}">${d.day_pnl>=0?'+':''}${fmt(d.day_pnl,2)} $</div></div>
    <div class="card"><div class="label">Позиций</div><div class="value">${d.positions}/${d.max_pos}</div></div>
    <div class="card"><div class="label">Сделок (W/L)</div><div class="value"><span class="green">${d.wins}</span>/<span class="red">${d.losses}</span></div></div>
    <div class="card"><div class="label">Win Rate</div><div class="value ${wr>=50?'green':'red'}">${fmt(wr,1)}%</div></div>
  `;

  // Analytics
  const a = d.analytics || {};
  document.getElementById('analytics').innerHTML = `
    <div class="stat"><div class="s-label">Profit Factor</div><div class="s-val ${a.profit_factor>=1?'green':'red'}">${a.profit_factor === 'inf' ? '∞' : fmt(a.profit_factor,2)}</div></div>
    <div class="stat"><div class="s-label">Max Drawdown</div><div class="s-val red">-${fmt(a.max_drawdown,2)}$</div></div>
    <div class="stat"><div class="s-label">Sharpe</div><div class="s-val ${(a.sharpe||0)>=0?'green':'red'}">${fmt(a.sharpe,2)}</div></div>
    <div class="stat"><div class="s-label">Avg Win</div><div class="s-val green">+${fmt(a.avg_win,2)}$</div></div>
    <div class="stat"><div class="s-label">Avg Loss</div><div class="s-val red">${fmt(a.avg_loss,2)}$</div></div>
    <div class="stat"><div class="s-label">Total PnL</div><div class="s-val ${(a.total_pnl||0)>=0?'green':'red'}">${fmt(a.total_pnl,2)}$</div></div>
  `;

  // Positions
  const pos = positions;
  document.getElementById('positions').innerHTML = pos.length ? pos.map(p => `
    <tr>
      <td><b>${p.symbol.replace('USDT','')}</b></td>
      <td><span class="tag ${p.side==='Buy'?'buy':'sell'}">${p.side==='Buy'?'▲ ЛОНГ':'▼ ШОРТ'}</span></td>
      <td>${fmt(p.entry,6)}</td>
      <td>${fmt(p.current,6)}</td>
      <td class="${pnlColor(p.pnl)}">${p.pnl>=0?'+':''}${fmt(p.pnl,2)} $</td>
      <td class="red">${fmt(p.sl,6)}</td>
      <td class="green">${fmt(p.tp,6)}</td>
      <td>${p.signal}</td>
    </tr>`).join('') : '<tr><td colspan="8" style="color:#8b949e;text-align:center">нет открытых позиций</td></tr>';

  // Signals
  const allSig = [...(signals.long||[]).map(s=>({...s,dir:'Buy'})), ...(signals.short||[]).map(s=>({...s,dir:'Sell'}))];
  document.getElementById('signals').innerHTML = allSig.length ? allSig.map(s => `
    <tr>
      <td><b>${s.symbol.replace('USDT','')}</b></td>
      <td><span class="tag ${s.dir==='Buy'?'buy':'sell'}">${s.dir==='Buy'?'▲ ЛОНГ':'▼ ШОРТ'}</span></td>
      <td>${fmt(s.rsi,1)}</td>
      <td class="${(s.change_1h||0)>=0?'green':'red'}">${s.change_1h>=0?'+':''}${fmt(s.change_1h,2)}%</td>
      <td>${fmt(s.rvol,2)}x</td>
      <td>${fmt(s.oi_growth,2)}%</td>
      <td>${fmt(s.atr_pct,2)}%</td>
      <td>${s.candle_pat||'—'}</td>
    </tr>`).join('') : '<tr><td colspan="8" style="color:#8b949e;text-align:center">нет сигналов</td></tr>';

  // History
  document.getElementById('history').innerHTML = history.length ? history.slice(0,20).map(t => `
    <tr>
      <td style="color:#8b949e">${(t.time||'').slice(11,19)}</td>
      <td><b>${(t.symbol||'').replace('USDT','')}</b></td>
      <td><span class="tag ${t.side==='Buy'?'buy':'sell'}">${t.side==='Buy'?'▲':'▼'} ${t.side==='Buy'?'BUY':'SELL'}</span></td>
      <td>${fmt(t.entry_price,6)}</td>
      <td class="${pnlColor(t.pnl)}">${(t.pnl>=0?'+':'')+fmt(t.pnl,2)} $</td>
      <td>${t.reason||'—'}</td>
    </tr>`).join('') : '<tr><td colspan="6" style="color:#8b949e;text-align:center">нет сделок</td></tr>';

  document.getElementById('last-update').textContent = 'обновлено ' + new Date().toLocaleTimeString();
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


def start(port: int = 8080) -> None:
    """Запускает веб-сервер в фоновом потоке."""
    def _run():
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="error")
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info(f"Веб-дашборд: http://localhost:{port}")

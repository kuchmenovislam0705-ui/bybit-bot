"""
Макро-данные для золота: DXY, US10Y, EUR/USD, Oil, S&P500.
Источник: yfinance (Yahoo Finance, бесплатно, без API-ключа).

Корреляции с XAUUSDT:
  DXY     (обратная,  сильная)  — доллар падает → золото растёт
  US10Y   (обратная,  сильная)  — доходности падают → золото растёт
  EUR/USD (прямая,    сильная)  — евро растёт → доллар слабеет → золото растёт
  Oil/WTI (прямая,    умеренная)— нефть растёт → инфляция → золото растёт
  S&P500  (смешанная)           — risk-off → золото растёт; risk-on → может падать
"""
import logging
import time
from typing import Dict, Optional

import yfinance as yf

logger = logging.getLogger("macro")

_CACHE_TTL = 900  # 15 минут
_cache: Dict = {"ts": 0.0, "data": {}}

_TICKERS = {
    "dxy":    "DX-Y.NYB",   # US Dollar Index — ОБРАТНАЯ к XAU
    "us10y":  "^TNX",        # US 10Y Treasury yield — ОБРАТНАЯ
    "eurusd": "EURUSD=X",    # EUR/USD — ПРЯМАЯ
    "oil":    "CL=F",        # WTI нефть — умеренная ПРЯМАЯ
    "spx":    "^GSPC",       # S&P 500 — СМЕШАННАЯ
}


def _fetch(symbol: str) -> Optional[list]:
    """Получает последние 15M свечи через yfinance. Таймаут 8с."""
    import threading
    result = [None]
    def _do():
        try:
            tk = yf.Ticker(symbol)
            df = tk.history(period="1d", interval="15m")
            if df is not None and not df.empty:
                closes = df["Close"].dropna().tolist()
                if len(closes) >= 2:
                    result[0] = closes
        except Exception as e:
            logger.debug(f"yfinance {symbol}: {e}")
    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout=8)
    if t.is_alive():
        logger.debug(f"yfinance {symbol}: таймаут 8с")
    return result[0]


def _chg(closes: list, n: int = 1) -> float:
    if len(closes) < n + 1:
        return 0.0
    return (closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)] * 100


def get() -> Dict:
    """
    Возвращает макро-срез: price, chg_15m, chg_1h для каждого инструмента.
    Кешируется 15 минут.
    """
    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL and _cache["data"]:
        return _cache["data"]

    data: Dict = {}
    for key, sym in _TICKERS.items():
        closes = _fetch(sym)
        if closes and len(closes) >= 2:
            data[key] = {
                "price":   round(closes[-1], 4),
                "chg_15m": round(_chg(closes, 1), 3),
                "chg_1h":  round(_chg(closes, 4), 3),
            }

    _cache["ts"]   = now
    _cache["data"] = data

    if data:
        parts = []
        if "dxy"    in data: parts.append(f"DXY={data['dxy']['price']:.2f}({data['dxy']['chg_15m']:+.2f}%)")
        if "us10y"  in data: parts.append(f"10Y={data['us10y']['price']:.2f}%")
        if "eurusd" in data: parts.append(f"EUR={data['eurusd']['price']:.4f}")
        if "oil"    in data: parts.append(f"Oil={data['oil']['price']:.2f}({data['oil']['chg_15m']:+.2f}%)")
        logger.info(f"Макро: {' | '.join(parts)}")
    else:
        logger.warning("Макро-данные недоступны (yfinance не ответил)")

    return data


def gold_macro_bonus(direction: str, macro_data: Dict) -> int:
    """
    Бонус к скору за макро-подтверждение для XAU/XAG.

    Используем 1H изменения (не 15M) — более реалистичные пороги.
    DXY за 15M двигается на ±0.00-0.03% в спокойный рынок,
    поэтому 15M пороги (0.05-0.15%) никогда не достигались.
    1H пороги (0.08-0.25%) срабатывают в реальных условиях.

    Диапазон: -3 до +4
    """
    if not macro_data:
        return 0

    d = 1 if direction == "Buy" else -1
    bonus = 0

    # ── DXY: главный драйвер (обратная корреляция) ────────────────────────────
    dxy    = macro_data.get("dxy", {})
    dxy_1h = dxy.get("chg_1h", 0) or 0

    if d == 1:   # лонг XAU → DXY должен падать
        if   dxy_1h < -0.25:  bonus += 2   # DXY -0.25% за 1H — значительное ослабление $
        elif dxy_1h < -0.08:  bonus += 1   # DXY -0.08% за 1H — умеренное ослабление
        elif dxy_1h > 0.25:   bonus -= 1   # DXY растёт — против лонга XAU
    else:        # шорт XAU → DXY должен расти
        if   dxy_1h > 0.25:   bonus += 2
        elif dxy_1h > 0.08:   bonus += 1
        elif dxy_1h < -0.25:  bonus -= 1

    # ── EUR/USD: прямая корреляция ────────────────────────────────────────────
    eu_1h = macro_data.get("eurusd", {}).get("chg_1h", 0) or 0
    if d == 1:
        if   eu_1h > 0.05:   bonus += 1
        elif eu_1h < -0.05:  bonus -= 1
    else:
        if   eu_1h < -0.05:  bonus += 1
        elif eu_1h > 0.05:   bonus -= 1

    # ── US10Y: обратная корреляция ────────────────────────────────────────────
    y_1h = macro_data.get("us10y", {}).get("chg_1h", 0) or 0
    if d == 1:
        if   y_1h < -0.3:   bonus += 1   # доходности падают → золото растёт
        elif y_1h > 0.3:    bonus -= 1
    else:
        if   y_1h > 0.3:    bonus += 1
        elif y_1h < -0.3:   bonus -= 1

    # ── Oil/WTI: умеренная прямая ─────────────────────────────────────────────
    oil_1h = macro_data.get("oil", {}).get("chg_1h", 0) or 0
    if d == 1  and oil_1h > 0.5:    bonus += 1
    elif d == -1 and oil_1h < -0.5: bonus += 1

    return max(-3, min(4, bonus))


def btc_macro_bonus(direction: str, macro_data: Dict) -> int:
    """
    Макро-бонус для BTCUSDT.
    Тоже переходим на 1H изменения.
    """
    if not macro_data:
        return 0
    d = 1 if direction == "Buy" else -1
    bonus = 0
    dxy_1h = macro_data.get("dxy", {}).get("chg_1h", 0) or 0
    spx_1h = macro_data.get("spx", {}).get("chg_1h", 0) or 0
    if d == 1:
        if dxy_1h < -0.15: bonus += 1   # слабый $ → риск-он → BTC вверх
        if spx_1h > 0.3:   bonus += 1   # акции растут → BTC тоже
    else:
        if dxy_1h > 0.15:  bonus += 1
        if spx_1h < -0.3:  bonus += 1
    return max(-1, min(2, bonus))

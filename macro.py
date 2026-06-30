"""
Макро-данные: DXY, US10Y, EUR/USD, Oil, S&P500.

Источник (приоритет):
  1. TradingView (tvdatafeed) — real-time, без задержки
  2. yfinance — резервный вариант если TV недоступен

Корреляции с XAUUSDT:
  DXY     (обратная,  сильная)  — доллар падает → золото растёт
  US10Y   (обратная,  сильная)  — доходности падают → золото растёт
  EUR/USD (прямая,    сильная)  — евро растёт → доллар слабеет → золото растёт
  Oil/WTI (прямая,    умеренная)— нефть растёт → инфляция → золото растёт
  S&P500  (смешанная)           — risk-off → золото растёт; risk-on → может падать
"""
import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger("macro")

_CACHE_TTL = 900  # 15 минут
_cache: Dict = {"ts": 0.0, "data": {}}

# ── yfinance резерв ───────────────────────────────────────────────────────────
_YF_TICKERS = {
    "dxy":    "DX-Y.NYB",
    "us10y":  "^TNX",
    "eurusd": "EURUSD=X",
    "oil":    "CL=F",
    "spx":    "^GSPC",
}


def _fetch_yf(symbol: str) -> Optional[List[float]]:
    import threading, yfinance as yf
    result = [None]
    def _do():
        try:
            df = yf.Ticker(symbol).history(period="1d", interval="15m")
            if df is not None and not df.empty:
                closes = df["Close"].dropna().tolist()
                if len(closes) >= 2:
                    result[0] = closes
        except Exception as e:
            logger.debug(f"yfinance {symbol}: {e}")
    t = threading.Thread(target=_do, daemon=True)
    t.start(); t.join(timeout=8)
    return result[0]


# ── TV источник ───────────────────────────────────────────────────────────────

def _fetch_tv(key: str) -> Optional[List[float]]:
    try:
        import tv_client
        return tv_client.get_macro_closes(key, interval="15", n_bars=25)
    except Exception as e:
        logger.debug(f"TV macro {key}: {e}")
        return None


def _fetch(key: str) -> Optional[List[float]]:
    """TV → yfinance fallback."""
    closes = _fetch_tv(key)
    if closes and len(closes) >= 2:
        logger.debug(f"Макро {key}: TV ({len(closes)} баров)")
        return closes
    closes = _fetch_yf(_YF_TICKERS[key])
    if closes and len(closes) >= 2:
        logger.debug(f"Макро {key}: yfinance ({len(closes)} баров)")
    return closes


def _chg(closes: List[float], n: int = 1) -> float:
    if len(closes) < n + 1:
        return 0.0
    return (closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)] * 100


def get() -> Dict:
    """
    Возвращает макро-срез: price, chg_15m, chg_1h для каждого инструмента.
    Источник: TradingView (primary) / yfinance (fallback).
    Кешируется 15 минут. Все 5 символов фетчатся параллельно.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL and _cache["data"]:
        return _cache["data"]

    data: Dict = {}

    def _fetch_one(key: str):
        closes = _fetch(key)
        if closes and len(closes) >= 2:
            return key, {
                "price":   round(closes[-1], 4),
                "chg_15m": round(_chg(closes, 1), 3),
                "chg_1h":  round(_chg(closes, 4), 3),
            }
        return key, None

    with ThreadPoolExecutor(max_workers=5) as ex:
        for key, val in ex.map(_fetch_one, _YF_TICKERS.keys()):
            if val:
                data[key] = val

    _cache["ts"]   = now
    _cache["data"] = data

    if data:
        parts = []
        if "dxy"    in data: parts.append(f"DXY={data['dxy']['price']:.2f}({data['dxy']['chg_1h']:+.2f}%/1h)")
        if "us10y"  in data: parts.append(f"10Y={data['us10y']['price']:.2f}%")
        if "eurusd" in data: parts.append(f"EUR={data['eurusd']['price']:.4f}")
        if "oil"    in data: parts.append(f"Oil={data['oil']['price']:.1f}")
        if "spx"    in data: parts.append(f"SPX={data['spx']['price']:.0f}")
        logger.info(f"Макро (TV): {' | '.join(parts)}")
    else:
        logger.warning("Макро-данные недоступны (TV и yfinance не ответили)")

    return data


def gold_macro_bonus(direction: str, macro_data: Dict) -> int:
    """
    Бонус к скору за макро-подтверждение для XAU/XAG.
    Используем 1H изменения — более реалистичные пороги.
    Диапазон: -3 до +4
    """
    if not macro_data:
        return 0

    d = 1 if direction == "Buy" else -1
    bonus = 0

    # ── DXY: главный драйвер (обратная корреляция) ────────────────────────────
    dxy_1h = macro_data.get("dxy", {}).get("chg_1h", 0) or 0
    if d == 1:
        if   dxy_1h < -0.25: bonus += 2
        elif dxy_1h < -0.08: bonus += 1
        elif dxy_1h > 0.25:  bonus -= 1
    else:
        if   dxy_1h > 0.25:  bonus += 2
        elif dxy_1h > 0.08:  bonus += 1
        elif dxy_1h < -0.25: bonus -= 1

    # ── EUR/USD: прямая корреляция ────────────────────────────────────────────
    eu_1h = macro_data.get("eurusd", {}).get("chg_1h", 0) or 0
    if d == 1:
        if   eu_1h > 0.05:  bonus += 1
        elif eu_1h < -0.05: bonus -= 1
    else:
        if   eu_1h < -0.05: bonus += 1
        elif eu_1h > 0.05:  bonus -= 1

    # ── US10Y: обратная корреляция ────────────────────────────────────────────
    y_1h = macro_data.get("us10y", {}).get("chg_1h", 0) or 0
    if d == 1:
        if   y_1h < -0.3: bonus += 1
        elif y_1h > 0.3:  bonus -= 1
    else:
        if   y_1h > 0.3:  bonus += 1
        elif y_1h < -0.3: bonus -= 1

    # ── Oil/WTI: умеренная прямая ─────────────────────────────────────────────
    oil_1h = macro_data.get("oil", {}).get("chg_1h", 0) or 0
    if d == 1  and oil_1h > 0.5:    bonus += 1
    elif d == -1 and oil_1h < -0.5: bonus += 1

    return max(-3, min(4, bonus))


def btc_macro_bonus(direction: str, macro_data: Dict) -> int:
    """Макро-бонус для BTC/ETH/крипто."""
    if not macro_data:
        return 0
    d = 1 if direction == "Buy" else -1
    bonus = 0
    dxy_1h = macro_data.get("dxy", {}).get("chg_1h", 0) or 0
    spx_1h = macro_data.get("spx", {}).get("chg_1h", 0) or 0
    if d == 1:
        if dxy_1h < -0.15: bonus += 1
        if spx_1h > 0.3:   bonus += 1
    else:
        if dxy_1h > 0.15:  bonus += 1
        if spx_1h < -0.3:  bonus += 1
    return max(-1, min(2, bonus))

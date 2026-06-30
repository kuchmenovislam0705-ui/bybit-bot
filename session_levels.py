"""
Session open price levels for scalping.

Asia opens:   00:00 UTC (XAU: Sydney, BTC: crypto)
Europe opens: 07:00 UTC (London)
NY opens:     13:00 UTC (Comex opens 13:30, use 13:00 as proxy)

Session opens are KEY reference levels:
- Price returning to session open = high-probability scalp zone
- Price breaking from session open = momentum entry
"""
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import client

logger = logging.getLogger("session_levels")

# Session opens (UTC hours)
SESSION_HOURS = {"asia": 0, "europe": 7, "newyork": 13}

# {cache_key: {sess_open_fields..., "_ts": float}}
_cache: Dict = {}

_CACHE_TTL = 300  # 5 min


def _fetch_session_open(symbol: str, session_hour: int) -> Optional[float]:
    """Get the open price of the 1H candle that opened at session_hour UTC today."""
    try:
        raw = client.get_klines(symbol, interval="60", limit=30)
        if not raw:
            return None
        today = datetime.now(timezone.utc).date()
        target_ts_ms = int(
            datetime(today.year, today.month, today.day,
                     session_hour, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
        )
        for candle in raw:
            ts = int(candle[0])
            if abs(ts - target_ts_ms) < 1_800_000:  # 30 min tolerance
                return float(candle[1])  # open price
        return None
    except Exception as e:
        logger.debug(f"session_open {symbol} h={session_hour}: {e}")
        return None


def get(symbol: str) -> Dict:
    """
    Returns dict with session open prices.
    Keys: asia_open, europe_open, newyork_open (float or absent)
    """
    now_ts = time.time()
    cache_key = f"{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H')}"
    cached = _cache.get(cache_key)
    if cached and now_ts - cached.get("_ts", 0) < _CACHE_TTL:
        return {k: v for k, v in cached.items() if k != "_ts"}

    result: Dict = {}
    for sess_name, hour in SESSION_HOURS.items():
        price = _fetch_session_open(symbol, hour)
        if price:
            result[f"{sess_name}_open"] = price
    result["_ts"] = now_ts
    _cache[cache_key] = result
    return {k: v for k, v in result.items() if k != "_ts"}


def proximity_bonus(symbol: str, price: float, direction: str) -> Tuple[int, str]:
    """
    Bonus when price is near a session open level.
    Returns (bonus_int, description_str).

    - Within 0.05% of session open: zone is HOT (+2)
    - Within 0.20%: zone nearby (+1)
    - Just crossed in signal direction (+1)
    """
    levels = get(symbol)
    bonus = 0
    desc_parts = []
    d = 1 if direction == "Buy" else -1

    label_map = {"asia": "Азия", "europe": "Европа", "newyork": "NY"}
    for sess_name in SESSION_HOURS:
        lvl = levels.get(f"{sess_name}_open")
        if not lvl:
            continue
        pct = (price - lvl) / lvl * 100
        label = label_map.get(sess_name, sess_name)

        if abs(pct) < 0.05:
            bonus += 2
            desc_parts.append(f"⚡{label}={lvl:.2f}")
        elif abs(pct) < 0.20:
            bonus += 1
            desc_parts.append(f"↔{label}={lvl:.2f}")

        if d == 1 and 0.0 < pct < 0.10:
            bonus += 1
        elif d == -1 and -0.10 < pct < 0.0:
            bonus += 1

    return min(4, bonus), " ".join(desc_parts)


def get_bias(symbol: str, price: float) -> str:
    """Session bias: how many session opens price is above vs below."""
    levels = get(symbol)
    above = sum(1 for k in ("asia_open", "europe_open", "newyork_open")
                if levels.get(k) and price > levels[k])
    below = sum(1 for k in ("asia_open", "europe_open", "newyork_open")
                if levels.get(k) and price <= levels[k])
    if above >= 2:
        return "БЫЧИЙ"
    if below >= 2:
        return "МЕДВЕЖИЙ"
    return "НЕЙТР"

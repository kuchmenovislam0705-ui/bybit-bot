"""Расчёт размера позиции и округление до точности инструмента."""
import math

import config


def calc_qty(balance: float, price: float, sl_dist_pct: float) -> float:
    """
    Возвращает количество контрактов (базовая валюта).

    Логика:
      risk_usdt   = balance × RISK_PER_TRADE%
      notional    = risk_usdt / (sl_dist_pct / 100)   ← сколько USD нужно держать
      cap         = balance × MAX_POSITION_PCT% × LEVERAGE
      qty         = min(notional, cap) / price
    """
    if sl_dist_pct <= 0 or price <= 0 or balance <= 0:
        return 0.0

    risk_usdt    = balance * config.RISK_PER_TRADE / 100
    notional     = risk_usdt / (sl_dist_pct / 100)
    max_notional = balance * config.MAX_POSITION_PCT / 100 * config.LEVERAGE
    notional     = min(notional, max_notional)

    return notional / price


def round_qty(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    floored = math.floor(qty / step) * step
    return round(floored, _decimals(step))


def round_price(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    rounded = round(price / tick) * tick
    return round(rounded, _decimals(tick))


def _decimals(step: float) -> int:
    s = f"{step:.12f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def format_qty(qty: float, step: float) -> str:
    d = _decimals(step)
    return f"{qty:.{d}f}"


def format_price(price: float, tick: float) -> str:
    d = _decimals(tick)
    return f"{price:.{d}f}"

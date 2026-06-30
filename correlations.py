"""
Корреляционный анализ: XAU, XAG, BTC + альткоины (ETH, SOL, BNB, XRP).

Логика:
  XAU → ведущий рынок для XAG (более ликвидный)
  BTC → ведущий рынок для ETH/SOL/BNB/XRP (обычно корр. 0.7–0.95)

  corr_bonus для XAG = текущее 1H движение XAU
  corr_bonus для альткоинов = текущее 1H движение BTC
"""
import logging
import time
from typing import Dict, List, Optional

import client
import config

logger = logging.getLogger("correlations")

_LOOKBACK  = 24    # 24 часа на 1H свечах
_CACHE_TTL = 600   # обновляем каждые 10 минут
_cache: Dict = {"ts": 0.0, "data": {}}


def _pearson(x: list, y: list) -> float:
    n = min(len(x), len(y))
    if n < 6:
        return 0.0
    x, y = x[-n:], y[-n:]
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
    return round(num / den, 3) if den else 0.0


def _pct_returns(closes: list) -> list:
    return [(closes[i] - closes[i - 1]) / closes[i - 1] * 100
            for i in range(1, len(closes))]


def _chg(closes: list, n: int) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    return round((closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)] * 100, 3)


def get() -> Dict:
    """
    Возвращает корреляционный срез для всех отслеживаемых инструментов.
    Ключевые поля:
      corr_xau_xag          — Пирсон XAU-XAG (24H)
      corr_btc_xau          — Пирсон BTC-XAU
      corr_btc_{alt}        — Пирсон BTC с каждым альткоином (1H returns)
      change_1h_{sym}       — изменение цены за последний 1H
      change_4h_{sym}       — изменение за 4H
    """
    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL and _cache["data"]:
        return _cache["data"]

    all_symbols: List[str] = ["XAUUSDT", "XAGUSDT", "BTCUSDT"]
    closes_map: Dict[str, list] = {}

    for sym in all_symbols:
        try:
            raw = client.get_klines(sym, interval="60", limit=_LOOKBACK + 4)
            if raw and len(raw) >= 4:
                closes_map[sym] = [float(c[4]) for c in raw]
        except Exception as e:
            logger.debug(f"Correlation fetch {sym}: {e}")

    def corr(a: str, b: str) -> float:
        if a in closes_map and b in closes_map:
            return _pearson(_pct_returns(closes_map[a]), _pct_returns(closes_map[b]))
        return 0.0

    def last(sym: str) -> float:
        c = closes_map.get(sym, [])
        return c[-1] if c else 0.0

    data: Dict = {
        # Металлы
        "corr_xau_xag":   corr("XAUUSDT", "XAGUSDT"),
        "corr_btc_xau":   corr("BTCUSDT", "XAUUSDT"),
        "change_1h_xau":  _chg(closes_map.get("XAUUSDT", []), 1),
        "change_1h_xag":  _chg(closes_map.get("XAGUSDT", []), 1),
        "change_4h_xau":  _chg(closes_map.get("XAUUSDT", []), 4),
        "change_4h_xag":  _chg(closes_map.get("XAGUSDT", []), 4),
        "change_24h_xau": _chg(closes_map.get("XAUUSDT", []), 24),
        "change_24h_xag": _chg(closes_map.get("XAGUSDT", []), 24),
        "price_xau":      last("XAUUSDT"),
        "price_xag":      last("XAGUSDT"),
        # BTC
        "change_1h_btc":  _chg(closes_map.get("BTCUSDT", []), 1),
        "change_4h_btc":  _chg(closes_map.get("BTCUSDT", []), 4),
        "change_24h_btc": _chg(closes_map.get("BTCUSDT", []), 24),
        "price_btc":      last("BTCUSDT"),
    }

    # Альткоины: пропускаем (инструменты убраны из списка)

    _cache["ts"]   = now
    _cache["data"] = data

    # Лог
    xau_1h = data["change_1h_xau"] or 0.0
    xag_1h = data["change_1h_xag"] or 0.0
    btc_1h = data["change_1h_btc"] or 0.0
    r_xau  = data["corr_xau_xag"] or 0.0
    logger.info(
        f"Корреляции: XAU-XAG r={r_xau:+.2f} | "
        f"XAU 1H={xau_1h:+.3f}% | XAG 1H={xag_1h:+.3f}% | "
        f"BTC 1H={btc_1h:+.3f}%"
    )
    return data


def corr_bonus(symbol: str, direction: str, corr_data: Dict) -> int:
    """
    Бонус за корреляционное подтверждение.

    XAG: XAU — ведущий рынок. Движение XAU за 1H = главный сигнал.
    XAU: XAG как вторичное подтверждение.
    BTC: нет корр.бонуса (оценивается через macro DXY/SPX).
    Альткоины (ETH/SOL/BNB/XRP): BTC — ведущий рынок.

    Диапазон: -3 до +3
    """
    d = 1 if direction == "Buy" else -1

    # ── XAG: ориентируемся на XAU ────────────────────────────────────────────
    if symbol == "XAGUSDT":
        r      = corr_data.get("corr_xau_xag", 0) or 0
        xau_1h = corr_data.get("change_1h_xau") or 0
        xau_4h = corr_data.get("change_4h_xau") or 0
        bonus  = 0
        if abs(r) > 0.6:
            if d == 1:
                if   xau_1h > 0.25:                     bonus += 3
                elif xau_1h > 0.08:                     bonus += 2
                elif xau_1h > 0.02:                     bonus += 1
                elif xau_1h < -0.15:                    bonus -= 2
                elif xau_1h < -0.05 and xau_4h < -0.2: bonus -= 1
            else:
                if   xau_1h < -0.25:                    bonus += 3
                elif xau_1h < -0.08:                    bonus += 2
                elif xau_1h < -0.02:                    bonus += 1
                elif xau_1h > 0.15:                     bonus -= 2
                elif xau_1h > 0.05 and xau_4h > 0.2:   bonus -= 1
        return max(-3, min(3, bonus))

    # ── XAU: XAG как вторичное подтверждение ─────────────────────────────────
    if symbol == "XAUUSDT":
        r      = corr_data.get("corr_xau_xag", 0) or 0
        xag_1h = corr_data.get("change_1h_xag") or 0
        xag_4h = corr_data.get("change_4h_xag") or 0
        bonus  = 0
        if abs(r) > 0.6:
            if d == 1:
                if   xag_1h > 0.30:                     bonus += 2
                elif xag_1h > 0.10:                     bonus += 1
                elif xag_1h < -0.20 and xag_4h < -0.3: bonus -= 1
            else:
                if   xag_1h < -0.30:                    bonus += 2
                elif xag_1h < -0.10:                    bonus += 1
                elif xag_1h > 0.20 and xag_4h > 0.3:   bonus -= 1
        return max(-3, min(3, bonus))

    # ── Альткоины: убраны из инструментов ────────────────────────────────────
    if symbol in getattr(config, "ALTCOIN_SYMBOLS", []):
        key    = symbol.replace("USDT", "").lower()
        r      = corr_data.get(f"corr_btc_{key}", 0.7) or 0.7
        btc_1h = corr_data.get("change_1h_btc") or 0
        btc_4h = corr_data.get("change_4h_btc") or 0
        bonus  = 0
        # Чем сильнее корреляция с BTC, тем больше вес бонуса
        strong = abs(r) > 0.65
        if d == 1:   # лонг альткоина — нужно чтобы BTC тоже рос
            if   btc_1h > 0.5:                        bonus += 3 if strong else 2
            elif btc_1h > 0.2:                        bonus += 2 if strong else 1
            elif btc_1h > 0.05:                       bonus += 1
            elif btc_1h < -0.3:                       bonus -= 2
            elif btc_1h < -0.1 and btc_4h < -0.5:    bonus -= 1
        else:        # шорт альткоина — нужно чтобы BTC падал
            if   btc_1h < -0.5:                       bonus += 3 if strong else 2
            elif btc_1h < -0.2:                       bonus += 2 if strong else 1
            elif btc_1h < -0.05:                      bonus += 1
            elif btc_1h > 0.3:                        bonus -= 2
            elif btc_1h > 0.1 and btc_4h > 0.5:      bonus -= 1
        return max(-3, min(3, bonus))

    # BTCUSDT: BTC-XAU корреляция слишком слабая → оценивается через macro
    return 0

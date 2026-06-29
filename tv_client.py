"""
TradingView data client via tvdatafeed.
Используется для макро-инструментов (DXY, US10Y, EUR/USD, Oil, SPX)
и как источник данных OHLCV для XAU/XAG/крипто.
"""
import logging
import sys
import threading
from typing import Dict, List, Optional

logger = logging.getLogger("tv_client")

# ── SSL fix (macOS Python 3.14+ / Railway Linux) ─────────────────────────────
try:
    import certifi
    import websocket._http as _wh
    _orig_wrap = _wh._wrap_sni_socket

    def _patched_wrap(sock, sslopt, hostname, check_hostname):
        sslopt = dict(sslopt)
        sslopt.setdefault("ca_certs", certifi.where())
        return _orig_wrap(sock, sslopt, hostname, check_hostname)

    _wh._wrap_sni_socket = _patched_wrap
except Exception:
    pass

try:
    from tvDatafeed import TvDatafeed, Interval
    _TV_AVAILABLE = True
except ImportError:
    _TV_AVAILABLE = False
    logger.warning("tvdatafeed не установлен — TV данные недоступны")

# ── Интервалы ──────────────────────────────────────────────────────────────────
_INTERVAL_MAP: Dict = {}
if _TV_AVAILABLE:
    _INTERVAL_MAP = {
        "1":    Interval.in_1_minute,
        "5":    Interval.in_5_minute,
        "15":   Interval.in_15_minute,
        "60":   Interval.in_1_hour,
        "240":  Interval.in_4_hour,
        "D":    Interval.in_daily,
        "1440": Interval.in_daily,
    }

# ── Символы TradingView ────────────────────────────────────────────────────────
# Макро-инструменты (заменяют yfinance)
MACRO_SYMBOLS: Dict[str, tuple] = {
    "dxy":    ("DXY",    "TVC"),     # US Dollar Index
    "us10y":  ("US10Y",  "TVC"),     # US 10Y Treasury Yield
    "eurusd": ("EURUSD", "FX"),      # EUR/USD
    "oil":    ("USOIL",  "TVC"),     # WTI Oil
    "spx":    ("SPX",    "SP"),      # S&P 500
}

# Торговые инструменты (OHLCV)
TRADE_SYMBOLS: Dict[str, tuple] = {
    # Металлы — TV спот, точнее для анализа
    "XAUUSDT": ("XAUUSD", "OANDA"),
    "XAGUSDT": ("SILVER", "TVC"),
    # Крипто — Bybit (TV дублирует с задержкой)
    "BTCUSDT": ("BTCUSDT", "BYBIT"),
    "ETHUSDT": ("ETHUSDT", "BYBIT"),
    "SOLUSDT": ("SOLUSDT", "BYBIT"),
    "BNBUSDT": ("BNBUSDT", "BYBIT"),
    "XRPUSDT": ("XRPUSDT", "BYBIT"),
    # Индексы (TV only, Bybit не имеет реального перпа)
    "NAS100":  ("NQ1!",   "CME_MINI"),
    "SPX500":  ("SPX",    "SP"),
    # Форекс (TV only)
    "EURUSD":  ("EURUSD", "FX"),
    "USDJPY":  ("USDJPY", "FX"),
    "GBPUSD":  ("GBPUSD", "FX"),
    "AUDUSD":  ("AUDUSD", "FX"),
    "USDCAD":  ("USDCAD", "FX"),
    "USDCHF":  ("USDCHF", "OANDA"),
    "NZDUSD":  ("NZDUSD", "FX"),
}

# ── Singleton клиент ──────────────────────────────────────────────────────────
_lock = threading.Lock()
_tv = None


def _get_tv(reset: bool = False):
    global _tv
    if not _TV_AVAILABLE:
        return None
    with _lock:
        if _tv is None or reset:
            try:
                _tv = TvDatafeed()
            except Exception as e:
                logger.error(f"TvDatafeed init: {e}")
                _tv = None
    return _tv


# ── Публичный API ─────────────────────────────────────────────────────────────

def get_closes(tv_symbol: str, exchange: str,
               interval: str = "15", n_bars: int = 30) -> Optional[List[float]]:
    """
    Возвращает список цен закрытия (от старых к новым).
    При ошибке соединения пересоздаёт клиент и повторяет 1 раз.
    """
    intv = _INTERVAL_MAP.get(interval, _INTERVAL_MAP.get("15"))
    if intv is None:
        return None

    for attempt in range(2):
        tv = _get_tv(reset=(attempt > 0))
        if tv is None:
            return None
        result: List = [None]

        def _fetch(tv=tv):
            try:
                df = tv.get_hist(tv_symbol, exchange, interval=intv, n_bars=n_bars)
                if df is not None and not df.empty:
                    result[0] = df["close"].dropna().tolist()
            except Exception as e:
                logger.debug(f"TV {exchange}:{tv_symbol} {interval}: {e}")

        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=15)
        if result[0]:
            return result[0]
        logger.debug(f"TV {exchange}:{tv_symbol} попытка {attempt+1} не удалась")

    return None


def get_ohlcv(tv_symbol: str, exchange: str,
              interval: str = "15", n_bars: int = 120) -> Optional[List[List]]:
    """
    Возвращает OHLCV в формате Bybit: [[ts_ms, open, high, low, close, volume], ...]
    Порядок: новые → старые (как Bybit get_klines).
    При ошибке пересоздаёт клиент и повторяет 1 раз.
    """
    intv = _INTERVAL_MAP.get(interval, _INTERVAL_MAP.get("15"))
    if intv is None:
        return None

    for attempt in range(2):
        tv = _get_tv(reset=(attempt > 0))
        if tv is None:
            return None
        result: List = [None]

        def _fetch(tv=tv):
            try:
                df = tv.get_hist(tv_symbol, exchange, interval=intv, n_bars=n_bars)
                if df is None or df.empty:
                    return
                rows = []
                for ts, row in df.iterrows():
                    ts_ms = int(ts.timestamp() * 1000)
                    rows.append([
                        str(ts_ms),
                        str(row.get("open",  0)),
                        str(row.get("high",  0)),
                        str(row.get("low",   0)),
                        str(row.get("close", 0)),
                        str(row.get("volume", 0)),
                    ])
                result[0] = list(reversed(rows))
            except Exception as e:
                logger.debug(f"TV OHLCV {exchange}:{tv_symbol}: {e}")

        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=15)
        if result[0]:
            return result[0]
        logger.debug(f"TV OHLCV {exchange}:{tv_symbol} попытка {attempt+1} не удалась")

    return None


def get_macro_closes(key: str, interval: str = "15", n_bars: int = 30) -> Optional[List[float]]:
    """Возвращает close-цены макро-инструмента по ключу ('dxy', 'us10y', ...)."""
    entry = MACRO_SYMBOLS.get(key)
    if not entry:
        return None
    return get_closes(entry[0], entry[1], interval=interval, n_bars=n_bars)


def available() -> bool:
    return _TV_AVAILABLE and _get_tv() is not None

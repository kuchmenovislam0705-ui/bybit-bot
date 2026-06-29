"""Bybit V5 REST API — публичные и приватные эндпоинты."""
import hashlib
import hmac
import json
import time
from typing import Dict, List, Optional
from urllib.parse import urlencode

import requests

import config

BASE_URL    = "https://api.bybit.com"
TESTNET_URL = "https://api-testnet.bybit.com"

_session = requests.Session()
_session.headers.update({"User-Agent": "BybitBot/1.0", "Content-Type": "application/json"})
_adapter = requests.adapters.HTTPAdapter(pool_connections=30, pool_maxsize=30)
_session.mount("https://", _adapter)

# Разница между локальным временем и сервером Bybit (мс)
_time_offset_ms: int = 0


def _sync_time() -> None:
    """Синхронизирует локальные часы с сервером Bybit."""
    global _time_offset_ms
    try:
        local_before = int(time.time() * 1000)
        r = _session.get(f"{BASE_URL}/v5/market/time", timeout=5)
        local_after  = int(time.time() * 1000)
        server_ms    = int(r.json()["result"]["timeNano"]) // 1_000_000
        local_mid    = (local_before + local_after) // 2
        _time_offset_ms = server_ms - local_mid
    except Exception:
        _time_offset_ms = 0


# Синхронизируем время при загрузке модуля
_sync_time()


def _url(path: str) -> str:
    return f"{TESTNET_URL if config.TESTNET else BASE_URL}{path}"


def _sign(payload: str) -> str:
    return hmac.new(config.API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _ts_rw() -> tuple[str, str]:
    corrected_ms = int(time.time() * 1000) + _time_offset_ms
    return str(corrected_ms), "5000"


# ── Внутренние HTTP-методы ────────────────────────────────────────────────────

def _pub_get(path: str, params: dict) -> dict:
    for attempt in range(3):
        try:
            r = _session.get(_url(path), params=params, timeout=10)
            r.raise_for_status()
            body = r.json()
            if body["retCode"] == 0:
                return body["result"]
            raise ValueError(f"Bybit {body['retCode']}: {body['retMsg']}")
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _prv_get(path: str, params: dict) -> dict:
    for attempt in range(4):
        ts, rw = _ts_rw()
        qs = urlencode(params)
        headers = {
            "X-BAPI-API-KEY":     config.API_KEY,
            "X-BAPI-TIMESTAMP":   ts,
            "X-BAPI-RECV-WINDOW": rw,
            "X-BAPI-SIGN":        _sign(ts + config.API_KEY + rw + qs),
        }
        try:
            r = _session.get(_url(path), params=params, headers=headers, timeout=10)
            r.raise_for_status()
            body = r.json()
            if body["retCode"] == 0:
                return body["result"]
            if body["retCode"] == 10002:   # timestamp mismatch → ресинк
                _sync_time()
                continue
            raise ValueError(f"Bybit {body['retCode']}: {body['retMsg']}")
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _prv_post(path: str, payload: dict) -> dict:
    for attempt in range(4):
        ts, rw = _ts_rw()
        body_str = json.dumps(payload)
        headers = {
            "X-BAPI-API-KEY":     config.API_KEY,
            "X-BAPI-TIMESTAMP":   ts,
            "X-BAPI-RECV-WINDOW": rw,
            "X-BAPI-SIGN":        _sign(ts + config.API_KEY + rw + body_str),
        }
        try:
            r = _session.post(_url(path), data=body_str, headers=headers, timeout=10)
            r.raise_for_status()
            body = r.json()
            if body["retCode"] == 0:
                return body.get("result", {})
            if body["retCode"] == 10002:   # timestamp mismatch → ресинк
                _sync_time()
                continue
            raise ValueError(f"Bybit {body['retCode']}: {body['retMsg']}")
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unreachable")


# ── Публичные эндпоинты ───────────────────────────────────────────────────────

def get_tickers() -> List[Dict]:
    return _pub_get("/v5/market/tickers", {"category": "linear"})["list"]


def get_klines(symbol: str, interval: str = "15", limit: int = 100) -> List:
    return _pub_get("/v5/market/kline", {
        "category": "linear", "symbol": symbol, "interval": interval, "limit": limit,
    })["list"]


def get_open_interest(symbol: str, interval: str = "1h", limit: int = 3) -> List[Dict]:
    return _pub_get("/v5/market/open-interest", {
        "category": "linear", "symbol": symbol, "intervalTime": interval, "limit": limit,
    })["list"]


_instrument_cache: Dict[str, dict] = {}

def get_instrument_info(symbol: str) -> dict:
    if symbol not in _instrument_cache:
        items = _pub_get("/v5/market/instruments-info", {
            "category": "linear", "symbol": symbol,
        }).get("list", [])
        _instrument_cache[symbol] = items[0] if items else {}
    return _instrument_cache[symbol]


# ── Приватные эндпоинты ───────────────────────────────────────────────────────

def get_equity() -> float:
    """Полный equity в USDT (баланс + нереализованная прибыль)."""
    result = _prv_get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})
    return float(result["list"][0]["totalEquity"])


def get_positions() -> List[Dict]:
    """Все открытые позиции с ненулевым размером."""
    result = _prv_get("/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
    return [p for p in result["list"] if float(p.get("size", 0)) > 0]


def get_orderbook(symbol: str, depth: int = 25) -> Dict:
    """Стакан ордеров. Возвращает {'bid_vol': float, 'ask_vol': float, 'imbalance': float}."""
    try:
        data = _pub_get("/v5/market/orderbook", {"category": "linear", "symbol": symbol, "limit": depth})
        bids = data.get("b", [])
        asks = data.get("a", [])
        bid_vol = sum(float(b[1]) for b in bids)
        ask_vol = sum(float(a[1]) for a in asks)
        total   = bid_vol + ask_vol
        imbalance = bid_vol / total if total > 0 else 0.5
        return {"bid_vol": bid_vol, "ask_vol": ask_vol, "imbalance": round(imbalance, 3)}
    except Exception:
        return {"bid_vol": 0, "ask_vol": 0, "imbalance": 0.5}


def get_closed_pnl(symbol: Optional[str] = None, limit: int = 10) -> List[Dict]:
    """История закрытых позиций с PnL."""
    params: dict = {"category": "linear", "limit": limit}
    if symbol:
        params["symbol"] = symbol
    return _prv_get("/v5/position/closed-pnl", params).get("list", [])


def set_leverage(symbol: str, leverage: int) -> int:
    """Устанавливает плечо. Если монета не поддерживает — снижает до максимума. Возвращает реальное плечо."""
    import re
    for lev in [leverage, leverage // 2, 5, 3, 2, 1]:
        if lev < 1:
            lev = 1
        try:
            _prv_post("/v5/position/set-leverage", {
                "category":    "linear",
                "symbol":      symbol,
                "buyLeverage": str(lev),
                "sellLeverage": str(lev),
            })
            return lev
        except ValueError as e:
            err = str(e)
            if "110043" in err:   # плечо уже установлено
                return lev
            if "110013" in err:   # превышен максимум для этой монеты
                # пробуем следующее меньшее значение
                m = re.search(r'maxLeverage \[(\d+)\]', err)
                if m:
                    max_lev = int(m.group(1)) // 100
                    if max_lev >= 1:
                        try:
                            _prv_post("/v5/position/set-leverage", {
                                "category":    "linear",
                                "symbol":      symbol,
                                "buyLeverage": str(max_lev),
                                "sellLeverage": str(max_lev),
                            })
                            return max_lev
                        except Exception:
                            pass
                continue
            raise
    return 1


def place_order(symbol: str, side: str, qty: str, sl: str, tp: str = "") -> dict:
    """Открыть рыночный ордер. TP опциональный (используем отдельные limit-ордера)."""
    payload: dict = {
        "category":    "linear",
        "symbol":      symbol,
        "side":        side,
        "orderType":   "Market",
        "qty":         qty,
        "stopLoss":    sl,
        "slTriggerBy": "LastPrice",
        "timeInForce": "GTC",
        "positionIdx": 0,
    }
    if tp:
        payload["takeProfit"]  = tp
        payload["tpTriggerBy"] = "LastPrice"
    return _prv_post("/v5/order/create", payload)


def place_tp_limit_order(symbol: str, side: str, qty: str, price: str) -> dict:
    """Лимитный reduce-only ордер для частичного TP."""
    close_side = "Sell" if side == "Buy" else "Buy"
    return _prv_post("/v5/order/create", {
        "category":    "linear",
        "symbol":      symbol,
        "side":        close_side,
        "orderType":   "Limit",
        "qty":         qty,
        "price":       price,
        "reduceOnly":  True,
        "timeInForce": "GTC",
        "positionIdx": 0,
    })


def cancel_order(symbol: str, order_id: str) -> dict:
    """Отменить ордер по ID."""
    try:
        return _prv_post("/v5/order/cancel", {
            "category": "linear",
            "symbol":   symbol,
            "orderId":  order_id,
        })
    except Exception:
        return {}


def set_trading_stop(
    symbol:        str,
    sl:            str = "",
    trailing_stop: str = "",
    active_price:  str = "",
) -> dict:
    """
    Обновить SL или активировать trailing stop на открытой позиции.
    trailing_stop — расстояние в USDT.
    active_price  — цена активации трейлинга.
    """
    payload: dict = {"category": "linear", "symbol": symbol, "positionIdx": 0}
    if sl:
        payload["stopLoss"] = sl
    if trailing_stop:
        payload["trailingStop"] = trailing_stop
    if active_price:
        payload["activePrice"] = active_price
    return _prv_post("/v5/position/trading-stop", payload)


def close_position(symbol: str, side: str, qty: str) -> dict:
    """Закрыть позицию противоположным рыночным ордером."""
    return _prv_post("/v5/order/create", {
        "category":    "linear",
        "symbol":      symbol,
        "side":        "Sell" if side == "Buy" else "Buy",
        "orderType":   "Market",
        "qty":         qty,
        "reduceOnly":  True,
        "timeInForce": "GTC",
        "positionIdx": 0,
    })

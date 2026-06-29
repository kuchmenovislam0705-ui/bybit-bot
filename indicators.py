"""Технические индикаторы: RSI, ATR, RVOL, MACD, BB, Stochastic, ADX, S/R, свечные паттерны."""
import math
from typing import Dict, List, Optional, Tuple

import numpy as np


def parse_klines(raw: list) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Bybit даёт свечи от новых к старым — разворачиваем."""
    data    = list(reversed(raw))
    opens   = np.array([float(k[1]) for k in data])
    closes  = np.array([float(k[4]) for k in data])
    highs   = np.array([float(k[2]) for k in data])
    lows    = np.array([float(k[3]) for k in data])
    volumes = np.array([float(k[5]) for k in data])
    return closes, highs, lows, volumes, opens


# ── Базовые индикаторы ────────────────────────────────────────────────────────

def calc_rsi(closes: np.ndarray, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = float(gains[:period].mean())
    avg_l  = float(losses[:period].mean())
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + avg_g / avg_l), 2)


def calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    trs = np.array([
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]))
        for i in range(1, len(closes))
    ])
    return float(trs[-period:].mean())


def calc_atr_pct(highs, lows, closes, period=14) -> Optional[float]:
    atr = calc_atr(highs, lows, closes, period)
    if atr is None or closes[-1] == 0:
        return None
    return round(atr / closes[-1] * 100, 2)


def calc_rvol(volumes: np.ndarray, period: int = 20) -> Optional[float]:
    if len(volumes) < period + 1:
        return None
    avg = float(volumes[-(period + 1):-1].mean())
    return round(float(volumes[-1]) / avg, 2) if avg else None


def pct_change(closes: np.ndarray, n_bars: int) -> Optional[float]:
    if len(closes) < n_bars + 1:
        return None
    old = closes[-(n_bars + 1)]
    return round((float(closes[-1]) - float(old)) / float(old) * 100, 2) if old else None


def is_volume_falling(volumes: np.ndarray, window: int = 3) -> bool:
    if len(volumes) < window * 2:
        return False
    return float(volumes[-window:].mean()) < float(volumes[-(window * 2):-window].mean())


# ── Скользящие средние ────────────────────────────────────────────────────────

def _ema_series(arr: np.ndarray, period: int) -> np.ndarray:
    """Полный ряд EMA."""
    if len(arr) < period:
        return np.array([])
    k   = 2.0 / (period + 1)
    ema = float(arr[:period].mean())
    out = [ema]
    for v in arr[period:]:
        ema = float(v) * k + ema * (1 - k)
        out.append(ema)
    return np.array(out)


def calc_ema(closes: np.ndarray, period: int) -> Optional[float]:
    s = _ema_series(closes, period)
    return round(float(s[-1]), 10) if len(s) else None


def calc_sma(closes: np.ndarray, period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return round(float(closes[-period:].mean()), 10)


# ── MACD ──────────────────────────────────────────────────────────────────────

def calc_macd(closes: np.ndarray, fast: int = 12, slow: int = 26,
              signal: int = 9) -> Dict:
    """Возвращает {'macd': float, 'signal': float, 'hist': float, 'bull': bool}"""
    empty = {"macd": None, "signal": None, "hist": None, "bull": False}
    if len(closes) < slow + signal:
        return empty
    ema_f  = _ema_series(closes, fast)
    ema_s  = _ema_series(closes, slow)
    diff   = len(ema_f) - len(ema_s)
    macd_l = ema_f[diff:] - ema_s
    if len(macd_l) < signal:
        return empty
    sig_l  = _ema_series(macd_l, signal)
    d2     = len(macd_l) - len(sig_l)
    hist   = macd_l[d2:] - sig_l
    m, s_v, h = float(macd_l[-1]), float(sig_l[-1]), float(hist[-1])
    prev_h = float(hist[-2]) if len(hist) > 1 else h
    return {
        "macd":    round(m, 8),
        "signal":  round(s_v, 8),
        "hist":    round(h, 8),
        "bull":    h > 0,  # гистограмма положительная = MACD выше сигнала
    }


# ── Bollinger Bands ───────────────────────────────────────────────────────────

def calc_bollinger(closes: np.ndarray, period: int = 20,
                   num_std: float = 2.0) -> Dict:
    """
    Bollinger Bands + squeeze detection.
    squeeze: полосы сужены (ширина < 1% от цены) — готовится сильное движение.
    squeeze_bull/bear: сжатие + цена вышла за полосу = прорыв из сжатия.
    """
    empty = {"upper": None, "mid": None, "lower": None,
             "pct_b": 0.5, "near_lower": False, "near_upper": False,
             "squeeze": False, "squeeze_bull": False, "squeeze_bear": False,
             "width_pct": 0.0}
    if len(closes) < period:
        return empty
    w   = closes[-period:]
    mid = float(w.mean())
    std = float(w.std())
    upper = mid + num_std * std
    lower = mid - num_std * std
    price = float(closes[-1])
    pct_b = (price - lower) / (upper - lower) if upper != lower else 0.5
    width_pct = (upper - lower) / mid * 100 if mid else 0.0
    # Squeeze: текущая ширина < 1% цены (для золота ~$40 при $4000 — очень тесно)
    squeeze = width_pct < 1.0
    return {
        "upper":        round(upper, 10),
        "mid":          round(mid,   10),
        "lower":        round(lower, 10),
        "pct_b":        round(pct_b, 3),
        "near_lower":   pct_b <= 0.20,
        "near_upper":   pct_b >= 0.80,
        "width_pct":    round(width_pct, 3),
        "squeeze":      squeeze,
        "squeeze_bull": squeeze and pct_b >= 0.85,  # сжатие + у верхней полосы
        "squeeze_bear": squeeze and pct_b <= 0.15,  # сжатие + у нижней полосы
    }


# ── Stochastic ────────────────────────────────────────────────────────────────

def calc_stochastic(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                    k_period: int = 14, d_period: int = 3) -> Dict:
    """Возвращает {'k', 'd', 'oversold', 'overbought', 'bull_cross', 'bear_cross'}"""
    empty = {"k": None, "d": None, "oversold": False,
             "overbought": False, "bull_cross": False, "bear_cross": False}
    if len(closes) < k_period + d_period:
        return empty
    k_vals = []
    for i in range(k_period - 1, len(closes)):
        hh = float(highs[i - k_period + 1:i + 1].max())
        ll = float(lows[i  - k_period + 1:i + 1].min())
        k_vals.append((float(closes[i]) - ll) / (hh - ll) * 100 if hh != ll else 50.0)
    if len(k_vals) < d_period + 1:
        return empty
    k   = k_vals[-1]
    d   = sum(k_vals[-d_period:]) / d_period
    k_p = k_vals[-2]
    d_p = sum(k_vals[-(d_period + 1):-1]) / d_period
    return {
        "k":          round(k, 2),
        "d":          round(d, 2),
        "oversold":   k < 25,
        "overbought": k > 75,
        "bull_cross": k_p < d_p and k > d,  # K пересёк D снизу вверх
        "bear_cross": k_p > d_p and k < d,  # K пересёк D сверху вниз
    }


# ── ADX ───────────────────────────────────────────────────────────────────────

def _adx_compute(highs: np.ndarray, lows: np.ndarray,
                 closes: np.ndarray, period: int = 14) -> dict:
    """Вычисляет ADX + направленные индикаторы +DI/-DI."""
    empty = {"adx": None, "pdi": 0.0, "mdi": 0.0}
    if len(closes) < period * 2 + 1:
        return empty
    trs, pdm, mdm = [], [], []
    for i in range(1, len(closes)):
        tr = max(float(highs[i]) - float(lows[i]),
                 abs(float(highs[i]) - float(closes[i-1])),
                 abs(float(lows[i])  - float(closes[i-1])))
        up   = float(highs[i]) - float(highs[i-1])
        down = float(lows[i-1]) - float(lows[i])
        trs.append(tr)
        pdm.append(up   if up > down and up > 0 else 0)
        mdm.append(down if down > up and down > 0 else 0)

    def smooth(arr):
        s = sum(arr[:period])
        out = [s]
        for v in arr[period:]:
            s = s - s / period + v
            out.append(s)
        return out

    tr_s = smooth(trs); pdm_s = smooth(pdm); mdm_s = smooth(mdm)
    dx_vals, last_pdi, last_mdi = [], 0.0, 0.0
    for t, p, m in zip(tr_s, pdm_s, mdm_s):
        pdi = 100 * p / t if t else 0
        mdi = 100 * m / t if t else 0
        last_pdi, last_mdi = pdi, mdi
        s = pdi + mdi
        dx_vals.append(100 * abs(pdi - mdi) / s if s else 0)
    if len(dx_vals) < period:
        return empty
    return {
        "adx": round(sum(dx_vals[-period:]) / period, 2),
        "pdi": round(last_pdi, 2),
        "mdi": round(last_mdi, 2),
    }


def calc_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             period: int = 14) -> Optional[float]:
    """ADX — сила тренда. Для полных данных (+DI/-DI) используй _adx_compute()."""
    return _adx_compute(highs, lows, closes, period).get("adx")


def calc_ema_cross(closes: np.ndarray, fast: int = 9, slow: int = 21) -> dict:
    """
    EMA9/EMA21 cross detection — главный сигнал входа при 15M скальпинге.
    bull_trend:  EMA9 > EMA21 (бычий момент)
    fresh_bull:  EMA9 только что пересёк EMA21 снизу вверх (вход)
    fresh_bear:  EMA9 только что пересёк EMA21 сверху вниз (вход в шорт)
    """
    empty = {"bull_trend": False, "bear_trend": False,
             "fresh_bull": False, "fresh_bear": False,
             "ema9": None, "ema21": None}
    if len(closes) < slow + 2:
        return empty
    f_s = _ema_series(closes, fast)
    s_s = _ema_series(closes, slow)
    if len(f_s) < 2 or len(s_s) < 2:
        return empty
    bull_now  = float(f_s[-1]) > float(s_s[-1])
    bull_prev = float(f_s[-2]) > float(s_s[-2])
    return {
        "bull_trend":  bull_now,
        "bear_trend":  not bull_now,
        "fresh_bull":  bull_now and not bull_prev,
        "fresh_bear":  (not bull_now) and bull_prev,
        "ema9":        round(float(f_s[-1]), 8),
        "ema21":       round(float(s_s[-1]), 8),
    }


# ── Поддержка и Сопротивление (Pivot) ────────────────────────────────────────

def detect_support_resistance(highs: np.ndarray, lows: np.ndarray,
                               lookback: int = 5) -> Dict:
    """
    Ищет swing-пики (сопротивление) и swing-впадины (поддержка).
    lookback=5 на 15M = 75 мин на каждую сторону (реальный swing pivot).
    """
    supports, resistances = [], []
    n = len(highs)
    for i in range(lookback, n - lookback):
        if all(float(highs[i]) >= float(highs[i-j]) and
               float(highs[i]) >= float(highs[i+j]) for j in range(1, lookback + 1)):
            resistances.append(float(highs[i]))
        if all(float(lows[i]) <= float(lows[i-j]) and
               float(lows[i]) <= float(lows[i+j]) for j in range(1, lookback + 1)):
            supports.append(float(lows[i]))
    return {
        "supports":     sorted(supports),
        "resistances":  sorted(resistances, reverse=True),
    }


def near_level(price: float, levels: List[float], tolerance_pct: float = 0.35) -> bool:
    """Цена в пределах tolerance_pct% от любого уровня."""
    return any(l and abs(price - l) / l * 100 <= tolerance_pct for l in levels)


# ── Свечные паттерны (2 и 3 свечи) ───────────────────────────────────────────

def detect_candle_pattern(opens: np.ndarray, highs: np.ndarray,
                           lows: np.ndarray, closes: np.ndarray) -> str:
    """2-свечные паттерны на последних свечах."""
    if len(closes) < 2:
        return "none"
    o1, h1, l1, c1 = float(opens[-2]), float(highs[-2]), float(lows[-2]), float(closes[-2])
    o0, h0, l0, c0 = float(opens[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])
    body0         = abs(c0 - o0)
    range0        = h0 - l0
    if range0 == 0:
        return "none"
    upper_wick0   = h0 - max(o0, c0)
    lower_wick0   = min(o0, c0) - l0

    if c1 < o1 and c0 > o0 and o0 <= c1 and c0 >= o1:  return "bullish_engulfing"
    if c1 > o1 and c0 < o0 and o0 >= c1 and c0 <= o1:  return "bearish_engulfing"
    if body0 > 0 and lower_wick0 >= 2*body0 and upper_wick0 <= 0.3*body0 and c0 > o0:
        return "hammer"
    if body0 > 0 and upper_wick0 >= 2*body0 and lower_wick0 <= 0.3*body0 and c0 < o0:
        return "shooting_star"
    if lower_wick0 / range0 >= 0.60 and c0 >= o0:  return "pin_bar_bull"
    if upper_wick0 / range0 >= 0.60 and c0 <= o0:  return "pin_bar_bear"
    # Harami
    if c1 < o1 and c0 > o0 and o0 > c1 and c0 < o1:   return "bullish_harami"
    if c1 > o1 and c0 < o0 and o0 < c1 and c0 > o1:   return "bearish_harami"
    # Tweezer
    if abs(l0 - l1) / max(l0, l1, 1e-10) < 0.001 and c0 > o0: return "tweezer_bottom"
    if abs(h0 - h1) / max(h0, h1, 1e-10) < 0.001 and c0 < o0: return "tweezer_top"
    if body0 / range0 < 0.10:  return "doji"
    return "none"


def detect_multi_candle_pattern(opens: np.ndarray, highs: np.ndarray,
                                 lows: np.ndarray, closes: np.ndarray) -> str:
    """3-свечные паттерны."""
    if len(closes) < 3:
        return "none"
    o2,h2,l2,c2 = float(opens[-3]),float(highs[-3]),float(lows[-3]),float(closes[-3])
    o1,h1,l1,c1 = float(opens[-2]),float(highs[-2]),float(lows[-2]),float(closes[-2])
    o0,h0,l0,c0 = float(opens[-1]),float(highs[-1]),float(lows[-1]),float(closes[-1])
    range2 = h2 - l2

    # Morning Star
    if (c2 < o2 and abs(c1-o1) < range2*0.3 and c0 > o0 and c0 > (o2+c2)/2):
        return "morning_star"
    # Evening Star
    if (c2 > o2 and abs(c1-o1) < range2*0.3 and c0 < o0 and c0 < (o2+c2)/2):
        return "evening_star"
    # Three White Soldiers
    if (c2>o2 and c1>o1 and c0>o0 and c1>c2 and c0>c1):
        return "three_white_soldiers"
    # Three Black Crows
    if (c2<o2 and c1<o1 and c0<o0 and c1<c2 and c0<c1):
        return "three_black_crows"
    return "none"


def detect_fvg(highs: np.ndarray, lows: np.ndarray) -> dict:
    """Fair Value Gap (имбаланс) — трёхсвечной паттерн."""
    if len(highs) < 3:
        return {"type": "none", "gap_pct": 0.0}
    h2, l2 = float(highs[-3]), float(lows[-3])
    h0, l0 = float(highs[-1]), float(lows[-1])
    if h2 < l0:
        return {"type": "bullish", "gap_pct": round((l0-h2)/h2*100, 3)}
    if l2 > h0:
        return {"type": "bearish", "gap_pct": round((l2-h0)/l2*100, 3)}
    return {"type": "none", "gap_pct": 0.0}


# ── Скоринг сигнала ───────────────────────────────────────────────────────────

_BULL_PAT_2 = {"bullish_engulfing", "hammer", "pin_bar_bull",
               "bullish_harami", "tweezer_bottom"}
_BULL_PAT_3 = {"morning_star", "three_white_soldiers"}
_BEAR_PAT_2 = {"bearish_engulfing", "shooting_star", "pin_bar_bear",
               "bearish_harami", "tweezer_top"}
_BEAR_PAT_3 = {"evening_star", "three_black_crows"}


def score_long(data: dict) -> int:
    """
    Скоринг для лонга на 15M таймфрейме. Максимум ~30 баллов.

    TA-факторы:
      MACD гистограмма > 0             +2
      BB: цена у нижней полосы         +2
      BB: сжатие + нижняя полоса       +1  (squeeze breakout — редкий но сильный)
      Stoch перепродан (<25)           +2
      Stoch бычий крест                +1
      Цена у уровня поддержки          +3
      FVG бычий (зазор > 0.05%)        +2
      RSI 28-50 (зона восстановл.)     +1
      ADX ≥ 20 + +DI > -DI            +1  (тренд + его направление)
      Цена выше EMA20 (15M)            +1
      EMA9 > EMA21 (бычий 15M)        +1  (краткосрочная тенденция)
      EMA9 только что пересёк EMA21   +2  (свежий сигнал входа)
      Тренд 1H бычий                   +2
      Тренд 4H бычий                   +1
      RVOL ≥ 2.0                       +1
      Свеча закрылась в верхней 55%    +1  (бычье закрытие)
      2-свечной паттерн (тело > 20%)   +2
      3-свечной паттерн                +3
    """
    score = 0
    macd  = data.get("macd", {}) or {}
    bb    = data.get("bb",   {}) or {}
    sto   = data.get("stoch",{}) or {}
    sr    = data.get("sr",   {}) or {}
    fvg   = data.get("fvg",  {}) or {}
    price = data.get("price", 0)
    rsi   = data.get("rsi")
    adx   = data.get("adx")
    pdi   = data.get("pdi", 0.0)
    mdi   = data.get("mdi", 0.0)
    ema20 = data.get("ema20")
    rvol  = data.get("rvol", 1.0)

    if macd.get("bull"):                             score += 2
    if bb.get("near_lower"):                         score += 2
    if bb.get("squeeze_bear"):                       score += 1  # разворот из сжатия
    if sto.get("oversold"):                          score += 2
    if sto.get("bull_cross"):                        score += 1
    if near_level(price, sr.get("supports", [])):    score += 3
    if fvg.get("type") == "bullish" and (fvg.get("gap_pct") or 0) > 0.05:
        score += 2
    if rsi and 28 <= rsi <= 50:                      score += 1
    if adx and adx >= 20 and pdi > mdi:              score += 1  # ADX + направление
    if ema20 and price > ema20:                      score += 1
    if data.get("ema9_bull"):                        score += 1  # EMA9 > EMA21
    if data.get("ema_fresh_bull"):                   score += 2  # свежий крест вверх
    if data.get("trend_1h", 0) == 1:                 score += 2
    if data.get("trend_4h", 0) == 1:                 score += 1
    if rvol and rvol >= 1.8:                         score += 1
    if data.get("close_upper"):                      score += 1  # свеча закрылась бычьи
    # Паттерны: требуем тело свечи > 20% диапазона
    body_ok = (data.get("body_pct", 0) or 0) >= 0.20
    if body_ok and data.get("candle_pat") in _BULL_PAT_2:  score += 2
    if data.get("multi_pat") in _BULL_PAT_3:               score += 3
    return score


def _rsi_series(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Полный ряд RSI значений."""
    if len(closes) < period + 2:
        return np.array([])
    deltas = np.diff(closes.astype(float))
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = float(np.mean(gains[:period]))
    avg_l  = float(np.mean(losses[:period]))
    out = []
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i])  / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rs  = avg_g / avg_l if avg_l > 0 else 100.0
        out.append(100 - 100 / (1 + rs))
    return np.array(out)


def detect_divergence(closes: np.ndarray, lookback: int = 40) -> str:
    """
    Определяет RSI-дивергенцию с ценой на последних N свечах.
    'bullish_div' — цена → новый минимум, RSI → выше (разворот вверх)
    'bearish_div' — цена → новый максимум, RSI → ниже (разворот вниз)
    'none'        — нет дивергенции
    """
    if len(closes) < lookback + 20:
        return "none"
    rsi_all = _rsi_series(closes, 14)
    if len(rsi_all) < lookback:
        return "none"
    c = np.array(closes[-lookback:], dtype=float)
    r = rsi_all[-lookback:]
    w = 3  # swing window

    lows, highs = [], []
    for i in range(w, len(c) - w):
        if all(c[i] <= c[i-j] and c[i] <= c[i+j] for j in range(1, w+1)):
            lows.append(i)
        if all(c[i] >= c[i-j] and c[i] >= c[i+j] for j in range(1, w+1)):
            highs.append(i)

    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        if c[i2] < c[i1] * 0.999 and r[i2] > r[i1] + 2.5:
            return "bullish_div"

    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        if c[i2] > c[i1] * 1.001 and r[i2] < r[i1] - 2.5:
            return "bearish_div"

    return "none"


def calc_pivot_points(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """Стандартные pivot points из предыдущей дневной свечи."""
    pp = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * pp - prev_low
    r2 = pp + (prev_high - prev_low)
    r3 = r1 + (prev_high - prev_low)
    s1 = 2 * pp - prev_high
    s2 = pp - (prev_high - prev_low)
    s3 = s1 - (prev_high - prev_low)
    return {"pp": pp, "r1": r1, "r2": r2, "r3": r3,
            "s1": s1, "s2": s2, "s3": s3}


def calc_vwap(highs: np.ndarray, lows: np.ndarray,
              closes: np.ndarray, volumes: np.ndarray) -> Optional[float]:
    """Volume Weighted Average Price от начала торговой сессии."""
    if len(volumes) == 0 or np.sum(volumes) == 0:
        return None
    typical = (highs + lows + closes) / 3.0
    return float(np.sum(typical * volumes) / np.sum(volumes))


def pivot_bonus(price: float, pivots: dict, direction: str) -> int:
    """
    Бонус +3 если цена у PP, +2 если у S1/R1/S2/R2.
    Дальние уровни (S3/R3) не дают бонуса — слишком далеко от текущей цены.
    """
    if not pivots:
        return 0
    tol = price * 0.0015  # 0.15%
    if direction == "Buy":
        priority = [("pp", 3), ("s1", 2), ("s2", 2)]
    else:
        priority = [("pp", 3), ("r1", 2), ("r2", 2)]
    for key, pts in priority:
        if abs(price - pivots.get(key, 0)) <= tol:
            return pts
    return 0


def score_short(data: dict) -> int:
    """Скоринг для шорта. Зеркало score_long. Максимум ~30 баллов."""
    score = 0
    macd  = data.get("macd", {}) or {}
    bb    = data.get("bb",   {}) or {}
    sto   = data.get("stoch",{}) or {}
    sr    = data.get("sr",   {}) or {}
    fvg   = data.get("fvg",  {}) or {}
    price = data.get("price", 0)
    rsi   = data.get("rsi")
    adx   = data.get("adx")
    pdi   = data.get("pdi", 0.0)
    mdi   = data.get("mdi", 0.0)
    ema20 = data.get("ema20")
    rvol  = data.get("rvol", 1.0)

    if not macd.get("bull"):                                              score += 2
    if bb.get("near_upper"):                                              score += 2
    if bb.get("squeeze_bull"):                                            score += 1
    if sto.get("overbought"):                                             score += 2
    if sto.get("bear_cross"):                                             score += 1
    if near_level(price, sr.get("resistances", [])):                      score += 3
    if fvg.get("type") == "bearish" and (fvg.get("gap_pct") or 0) > 0.05:
        score += 2
    if rsi and 50 <= rsi <= 72:                                           score += 1
    if adx and adx >= 20 and mdi > pdi:                                   score += 1
    if ema20 and price < ema20:                                           score += 1
    if data.get("ema9_bear"):                                             score += 1
    if data.get("ema_fresh_bear"):                                        score += 2
    if data.get("trend_1h", 0) == -1:                                     score += 2
    if data.get("trend_4h", 0) == -1:                                     score += 1
    if rvol and rvol >= 1.8:                                              score += 1
    if data.get("close_lower"):                                           score += 1
    body_ok = (data.get("body_pct", 0) or 0) >= 0.20
    if body_ok and data.get("candle_pat") in _BEAR_PAT_2:                 score += 2
    if data.get("multi_pat") in _BEAR_PAT_3:                              score += 3
    return score

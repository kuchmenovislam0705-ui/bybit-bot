"""
Сигнальный скринер: XAU/XAG/BTC/ETH/SOL/BNB/XRP — скальпинг на 15M.
ТА (15M+1H+4H) + Pivot Points + VWAP + RSI-дивергенция + Макро + Гео + Корреляции + Сессия.
Грейдинг: 🅐 ≥18  🅑 14-17

Расписание:
  XAU/XAG — 24/5: только рабочие дни (пн-пт).
  Крипто  — 24/7: без ограничений.

Сессионный бонус XAU/XAG (рабочие дни):
  London  07:00-12:00 UTC → +2
  NY      12:00-20:00 UTC → +2
  Pre-LDN 05:00-07:00 UTC → +1
  Азия    20:00-05:00 UTC →  0
Крипто: сессионного бонуса нет (24/7).
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import client
import config
import correlations
import geo
import indicators
import macro
import news_monitor
import signal_tracker
import tv_client

# webapp импортируется лениво чтобы не тянуть uvicorn при старте
def _tv_boost(symbol: str, direction: str) -> int:
    try:
        import webapp
        return webapp.get_tv_boost(symbol, direction, max_age_minutes=20)
    except Exception:
        return 0

logger = logging.getLogger("screener")


def _grade(score: int) -> str:
    return "🅐" if score >= 18 else "🅑"


def _is_weekend_metals(now_utc: datetime) -> bool:
    """
    XAU/XAG торгуются только в рабочие дни (24/5).
    Спот-золото закрыто с пятницы 22:00 UTC до воскресенья 22:00 UTC.
    """
    wd = now_utc.weekday()   # 0=пн … 4=пт, 5=сб, 6=вс
    h  = now_utc.hour

    if wd == 5:              # суббота — весь день
        return True
    if wd == 6 and h < 22:  # воскресенье до 22:00 UTC
        return True
    if wd == 4 and h >= 22: # пятница с 22:00 UTC — рынок закрыт
        return True
    return False


def _session_bonus(utc_hour: int, symbol: str) -> int:
    """
    Сессионный бонус только для XAU/XAG — у металлов есть сессионные пики.
    Крипто (BTC/ETH/SOL/BNB/XRP): 24/7, сессионного преимущества нет → 0.
    """
    if symbol not in ("XAUUSDT", "XAGUSDT"):
        return 0
    # XAU / XAG
    if 7 <= utc_hour < 12:  return 2   # London: лучшая волатильность
    if 12 <= utc_hour < 20: return 2   # NY + overlap: пик объёма
    if 5 <= utc_hour < 7:   return 1   # Pre-London
    return 0                            # Азия: ADX отсеет слабые движения


def _session_name(utc_hour: int) -> str:
    if 7 <= utc_hour < 12:  return "🇬🇧 Лондон"
    if 12 <= utc_hour < 20: return "🇺🇸 Нью-Йорк"
    if 5 <= utc_hour < 7:   return "Пре-Лондон"
    if 20 <= utc_hour or utc_hour < 5: return "🌏 Азия"
    return ""


def _get_klines(symbol: str, interval: str, limit: int) -> list:
    """
    TV → Bybit fallback для OHLCV.
    TV-only символы (форекс/индексы): только TV, нет Bybit.
    Металлы: TV спот точнее для анализа, Bybit как резерв.
    Крипто: сразу Bybit.
    """
    tv_entry = tv_client.TRADE_SYMBOLS.get(symbol)
    if tv_entry and symbol in config.TV_ONLY_SYMBOLS:
        # Форекс/индексы — только TV, Bybit аналога нет
        return tv_client.get_ohlcv(tv_entry[0], tv_entry[1], interval=interval, n_bars=limit) or []
    if tv_entry and symbol in ("XAUUSDT", "XAGUSDT"):
        data = tv_client.get_ohlcv(tv_entry[0], tv_entry[1], interval=interval, n_bars=limit)
        if data and len(data) >= 60:
            return data
    return client.get_klines(symbol, interval=interval, limit=limit)


def _session_bonus_forex(utc_hour: int) -> int:
    """Сессионный бонус для форекс пар."""
    if 12 <= utc_hour < 17:  return 3   # Лондон+NY overlap — абсолютный пик
    if 7  <= utc_hour < 12:  return 2   # Лондон — основная форекс сессия
    if 17 <= utc_hour < 21:  return 1   # NY late
    if 0  <= utc_hour < 3:   return 1   # Токио — JPY пары активны
    return 0                             # Мёртвая зона


def _session_bonus_index(utc_hour: int) -> int:
    """Сессионный бонус для US индексов (NYSE 14:30-21:00 UTC)."""
    if 14 <= utc_hour < 21:  return 2   # Основная NYSE сессия
    if 12 <= utc_hour < 14:  return 1   # Pre-market
    return 0


def _macro_bonus_forex(symbol: str, direction: str, macro_data: dict) -> int:
    """
    Макро-бонус для форекс пар.
    Основана на DXY — главный драйвер USD пар.
    Диапазон: -2 до +2
    """
    if not macro_data:
        return 0
    d = 1 if direction == "Buy" else -1
    dxy_1h = macro_data.get("dxy", {}).get("chg_1h", 0) or 0
    bonus = 0
    # USD-quote пары (EURUSD, GBPUSD, AUDUSD, NZDUSD): DXY обратная
    if symbol in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"):
        if d == 1:
            if   dxy_1h < -0.15: bonus += 2
            elif dxy_1h < -0.05: bonus += 1
            elif dxy_1h > 0.15:  bonus -= 1
        else:
            if   dxy_1h > 0.15:  bonus += 2
            elif dxy_1h > 0.05:  bonus += 1
            elif dxy_1h < -0.15: bonus -= 1
    # USD-base пары (USDJPY, USDCAD, USDCHF): DXY прямая
    elif symbol in ("USDJPY", "USDCAD", "USDCHF"):
        if d == 1:
            if   dxy_1h > 0.15:  bonus += 2
            elif dxy_1h > 0.05:  bonus += 1
            elif dxy_1h < -0.15: bonus -= 1
        else:
            if   dxy_1h < -0.15: bonus += 2
            elif dxy_1h < -0.05: bonus += 1
            elif dxy_1h > 0.15:  bonus -= 1
    return max(-2, min(2, bonus))


def _macro_bonus_index(symbol: str, direction: str, macro_data: dict) -> int:
    """
    Макро-бонус для US индексов.
    Risk-on/off: DXY обратная, SPX само-подтверждение (для NAS100).
    Диапазон: -2 до +2
    """
    if not macro_data:
        return 0
    d = 1 if direction == "Buy" else -1
    dxy_1h = macro_data.get("dxy", {}).get("chg_1h", 0) or 0
    spx_1h = macro_data.get("spx", {}).get("chg_1h", 0) or 0
    bonus = 0
    # Слабый доллар → risk-on → индексы вверх
    if d == 1:
        if dxy_1h < -0.15: bonus += 1
        if spx_1h > 0.3:   bonus += 1
    else:
        if dxy_1h > 0.15:  bonus += 1
        if spx_1h < -0.3:  bonus += 1
    return max(-2, min(2, bonus))


def _analyze(symbol: str) -> Optional[Dict]:
    """
    Мультитаймфреймный анализ: 5M вход + 15M тренд + 1H тренд + 4H + Daily pivot.
    5M — основной таймфрейм для точки входа.
    15M/1H — тренд-фильтр (не входить против старшего тренда).
    """
    try:
        # ── 5M — основной таймфрейм для сигнала ──────────────────────────────
        raw_5 = _get_klines(symbol, "5", 120)
        if not raw_5 or len(raw_5) < 40:
            return None

        c5, h5, l5, v5, o5 = indicators.parse_klines(raw_5)
        price = float(c5[-1])

        rsi        = indicators.calc_rsi(c5, 14)
        atr        = indicators.calc_atr(h5, l5, c5, 14)
        atr_pct    = atr / price * 100 if atr and price else None
        rvol       = indicators.calc_rvol(v5, 20)
        macd       = indicators.calc_macd(c5)
        bb         = indicators.calc_bollinger(c5)
        stoch      = indicators.calc_stochastic(h5, l5, c5)
        adx_full   = indicators._adx_compute(h5, l5, c5)
        adx        = adx_full.get("adx")
        pdi        = adx_full.get("pdi", 0.0) or 0.0
        mdi        = adx_full.get("mdi", 0.0) or 0.0
        sr         = indicators.detect_support_resistance(h5, l5, lookback=5)
        candle_pat = indicators.detect_candle_pattern(o5, h5, l5, c5)
        multi_pat  = indicators.detect_multi_candle_pattern(o5, h5, l5, c5)
        fvg        = indicators.detect_fvg(h5, l5)
        ema20_5    = indicators.calc_ema(c5, 20)
        ema_cross  = indicators.calc_ema_cross(c5, fast=9, slow=21)

        change_15m = indicators.pct_change(c5, 3)   # 3 × 5M ≈ 15M
        change_1h  = indicators.pct_change(c5, 12)  # 12 × 5M = 1H
        change_4h  = indicators.pct_change(c5, 48)  # 48 × 5M = 4H

        h_last  = float(h5[-1]); l_last = float(l5[-1])
        o_last  = float(o5[-1]); c_last = float(c5[-1])
        rng     = h_last - l_last
        body    = abs(c_last - o_last)
        body_pct  = body / rng if rng > 0 else 0.0
        close_pos = (c_last - l_last) / rng if rng > 0 else 0.5
        close_upper = close_pos >= 0.55
        close_lower = close_pos <= 0.45

        if not rsi or not atr or not atr_pct or atr_pct < 0.01:
            return None

        # ── VWAP (по дневным 5M свечам) ───────────────────────────────────────
        vwap = None
        try:
            now_utc  = datetime.now(timezone.utc)
            midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            today_raw = [c for c in raw_5
                         if datetime.fromtimestamp(int(c[0])/1000, tz=timezone.utc) >= midnight]
            if len(today_raw) >= 4:
                tc, th, tl, tv, _ = indicators.parse_klines(today_raw)
                vwap = indicators.calc_vwap(th, tl, tc, tv)
        except Exception:
            pass

        # ── RSI дивергенция ───────────────────────────────────────────────────
        divergence = "none"
        try:
            divergence = indicators.detect_divergence(c5, lookback=30)
        except Exception:
            pass

        # ── 15M тренд (первый старший фильтр) ────────────────────────────────
        trend_1h = 0   # используем как 15M тренд
        try:
            raw_15 = _get_klines(symbol, "15", 60)
            if raw_15 and len(raw_15) >= 30:
                c15 = [float(c[4]) for c in raw_15]
                e20 = indicators.calc_ema(c15, 20)
                e50 = indicators.calc_ema(c15, 50)
                if e20 and e50:
                    if e20 > e50 and c15[-1] > e20:   trend_1h = 1
                    elif e20 < e50 and c15[-1] < e20: trend_1h = -1
        except Exception:
            pass

        # ── 4H тренд ─────────────────────────────────────────────────────────
        trend_4h = 0
        try:
            raw_4h = _get_klines(symbol, "240", 50)
            if raw_4h and len(raw_4h) >= 20:
                c4h = [float(c[4]) for c in raw_4h]
                e20 = indicators.calc_ema(c4h, 20)
                e50 = indicators.calc_ema(c4h, 50)
                if e20 and e50:
                    if e20 > e50 and c4h[-1] > e20:   trend_4h = 1
                    elif e20 < e50 and c4h[-1] < e20: trend_4h = -1
        except Exception:
            pass

        # ── Daily Pivot + дневной тренд ───────────────────────────────────────
        pivots      = {}
        daily_trend = 0
        daily_adx   = 0.0
        try:
            raw_d = _get_klines(symbol, "D", 15)
            if not raw_d:
                raw_d = _get_klines(symbol, "1440", 15)
            if raw_d and len(raw_d) >= 2:
                prev = list(reversed(raw_d))[1]
                prev_h, prev_l, prev_c = float(prev[2]), float(prev[3]), float(prev[4])
                pivots = indicators.calc_pivot_points(prev_h, prev_l, prev_c)
                if len(raw_d) >= 10:
                    closed_d = list(reversed(raw_d))[:-1]
                    c_d = [float(c[4]) for c in closed_d]
                    h_d = [float(c[2]) for c in closed_d]
                    l_d = [float(c[3]) for c in closed_d]
                    e5  = indicators.calc_ema(c_d, 5)
                    e10 = indicators.calc_ema(c_d, 10)
                    if e5 and e10:
                        if e5 > e10 and c_d[-1] > e5:    daily_trend = 1
                        elif e5 < e10 and c_d[-1] < e5:  daily_trend = -1
                    if len(c_d) >= 14:
                        _dadx = indicators._adx_compute(h_d, l_d, c_d, period=14)
                        daily_adx = float(_dadx.get("adx") or 0.0)
        except Exception:
            pass

        # ── Orderbook (только Bybit-инструменты) ──────────────────────────────
        ob_imbalance = 0.5
        if symbol not in config.TV_ONLY_SYMBOLS:
            try:
                ob = client.get_orderbook(symbol, depth=20)
                ob_imbalance = float(ob.get("imbalance") or 0.5)
            except Exception:
                pass

        return {
            "symbol":        symbol,
            "price":         price,
            "change_15m":    change_15m,
            "change_1h":     change_1h,
            "change_4h":     change_4h,
            "rsi":           rsi,
            "atr_abs":       atr,
            "atr_pct":       atr_pct,
            "rvol":          float(rvol or 1.0),
            "macd":          macd,
            "bb":            bb,
            "stoch":         stoch,
            "adx":           adx,
            "pdi":           pdi,
            "mdi":           mdi,
            "sr":            sr,
            "candle_pat":    candle_pat,
            "multi_pat":     multi_pat,
            "fvg":           fvg,
            "ema20":         ema20_5,
            "ema9_bull":     ema_cross.get("bull_trend", False),
            "ema9_bear":     ema_cross.get("bear_trend", False),
            "ema_fresh_bull":ema_cross.get("fresh_bull", False),
            "ema_fresh_bear":ema_cross.get("fresh_bear", False),
            "ema9":          ema_cross.get("ema9"),
            "ema21":         ema_cross.get("ema21"),
            "trend_1h":      trend_1h,
            "trend_4h":      trend_4h,
            "vwap":          vwap,
            "pivots":        pivots,
            "divergence":    divergence,
            "body_pct":      round(body_pct, 3),
            "close_upper":   close_upper,
            "close_lower":   close_lower,
            "close_pos":     round(close_pos, 3),
            "daily_trend":   daily_trend,
            "daily_adx":     round(daily_adx, 1),
            "ob_imbalance":  round(ob_imbalance, 3),
            # совместимость
            "oi_growth":     0.0,
            "oi_falling":    False,
            "vol_falling":   False,
            "funding":       0.0,
            "volume_24h":    0.0,
            "change_24h":    change_4h,
        }
    except Exception as e:
        logger.debug(f"Анализ {symbol}: {e}")
        return None


def run_all() -> Tuple[List[Dict], List[Dict]]:
    """
    Анализирует XAU, XAG, BTC. Возвращает (longs, shorts).
    XAU/XAG: 24/5 (пн-пт). BTC: 24/7.
    """
    now_utc   = datetime.now(timezone.utc)
    utc_hour  = now_utc.hour
    sess_name = _session_name(utc_hour)

    # Адаптивный порог (повышается при плохой статистике)
    adaptive_min = signal_tracker.get_adaptive_score()

    # Внешние данные (общие для всех символов)
    geo_score, geo_headlines = geo.get_geo_score()
    corr_data  = correlations.get()
    macro_data = macro.get()

    geo_dir   = ("БЫЧИЙ ↑"   if geo_score > 0.15
                 else "МЕДВЕЖИЙ ↓" if geo_score < -0.15
                 else "нейтр ↔")
    news_sent = float(news_monitor.get_news_sentiment() or 0.0)

    is_asian_hour = utc_hour < 7 or utc_hour >= 20
    adx_thresh    = 15 if is_asian_hour else config.MIN_ADX
    # Азия: ADX порог ниже → компенсируем требованием +1 к скору
    score_thresh  = adaptive_min + (1 if is_asian_hour else 0)
    corr_xau_xag  = float(corr_data.get("corr_xau_xag") or 0.0)
    logger.info(
        f"Гео: {geo_dir} ({geo_score:+.2f}) | "
        f"Новости={news_sent:+.2f} | "
        f"XAU-XAG={corr_xau_xag:+.2f} | "
        f"Макро: {'OK' if macro_data else 'нет'} | "
        f"Мин.скор={score_thresh} ADX≥{adx_thresh}"
    )

    longs:  List[Dict] = []
    shorts: List[Dict] = []

    for symbol in config.SIGNAL_INSTRUMENTS:
        is_comm = symbol in ("XAUUSDT", "XAGUSDT")
        wd = now_utc.weekday()  # 0=пн, 5=сб, 6=вс

        # ── Выходные: металлы и форекс/индексы закрыты (24/5) ────────────────
        if is_comm and _is_weekend_metals(now_utc):
            logger.debug(f"{symbol} пропуск — выходной (металлы)")
            continue
        if symbol in config.TV_ONLY_SYMBOLS and wd >= 5:
            logger.debug(f"{symbol} пропуск — выходной (форекс/индексы)")
            continue

        data = _analyze(symbol)
        if data is None:
            continue

        is_tv   = symbol in config.TV_ONLY_SYMBOLS
        is_fx   = symbol in config.FOREX_SYMBOLS
        is_idx  = symbol in config.INDEX_SYMBOLS

        adx     = float(data.get("adx") or 0.0)
        atr_pct = float(data.get("atr_pct") or 0.0)
        rvol    = float(data.get("rvol") or 1.0)

        # ── Форекс: свои пороги (ADX/ATR структурно ниже чем у крипто) ──────
        if is_fx:
            if adx < config.FOREX_MIN_ADX:
                logger.debug(f"{symbol} пропуск ADX={adx:.1f} < {config.FOREX_MIN_ADX}")
                continue
            if atr_pct < config.FOREX_ATR_MIN:
                logger.debug(f"{symbol} пропуск ATR%={atr_pct:.4f}% < {config.FOREX_ATR_MIN}%")
                continue
            # TV тик-объём для форекс ненадёжен → не фильтруем по RVOL
        else:
            # ── ADX фильтр (крипто / металлы / индексы) ──────────────────────
            if adx < adx_thresh:
                logger.debug(f"{symbol} пропуск ADX={adx:.1f} < {adx_thresh}")
                continue
            # ── ATR минимум ───────────────────────────────────────────────────
            if atr_pct < 0.015:
                logger.debug(f"{symbol} пропуск ATR%={atr_pct:.3f}% < 0.015%")
                continue
            # ── RVOL минимум ──────────────────────────────────────────────────
            if rvol < 0.45:
                logger.debug(f"{symbol} пропуск RVOL={rvol:.2f} < 0.45")
                continue

        price   = data["price"]
        atr     = data["atr_abs"]
        sl_dist = atr * config.SL_ATR_MULT
        tp_dist = sl_dist * config.TP_RR
        # Форекс: цена нужна с 5 знаками (0.56538), JPY — с 3, индексы — с 1
        if is_fx:
            _p_dec = 3 if symbol == "USDJPY" else 5
        elif is_idx:
            _p_dec = 1
        else:
            _p_dec = 2
        pivots  = data.get("pivots", {}) or {}
        vwap    = data.get("vwap")
        diverg  = data.get("divergence", "none")

        # Сессионный бонус по типу инструмента
        if is_fx:
            sess_b = _session_bonus_forex(utc_hour)
        elif is_idx:
            sess_b = _session_bonus_index(utc_hour)
        else:
            sess_b = _session_bonus(utc_hour, symbol)

        # Гео: только металлы. Форекс/индексы — через макро.
        if is_comm:
            geo_bonus = max(-1, min(1, int(round(geo_score * 2))))
        else:
            geo_bonus = 0

        daily_trend = data.get("daily_trend", 0)
        daily_adx   = float(data.get("daily_adx") or 0.0)

        # Новостной бонус: только металлы
        if is_comm:
            if news_sent >= 0.4:    news_bonus = 1
            elif news_sent <= -0.4: news_bonus = -1
            else:                   news_bonus = 0
        else:
            news_bonus = 0

        # RVOL бонус: форекс пропускаем (TV тик-объём ненадёжен)
        rvol_bonus = 0 if is_fx else (1 if rvol >= 1.5 else (-1 if rvol < 0.7 else 0))

        # Orderbook (только Bybit)
        ob_imb = float(data.get("ob_imbalance") or 0.5)

        c_long  = correlations.corr_bonus(symbol, "Buy",  corr_data) if not is_tv else 0
        c_short = correlations.corr_bonus(symbol, "Sell", corr_data) if not is_tv else 0

        if is_fx:
            m_long  = _macro_bonus_forex(symbol, "Buy",  macro_data)
            m_short = _macro_bonus_forex(symbol, "Sell", macro_data)
        elif is_idx:
            m_long  = _macro_bonus_index(symbol, "Buy",  macro_data)
            m_short = _macro_bonus_index(symbol, "Sell", macro_data)
        elif is_comm:
            m_long  = macro.gold_macro_bonus("Buy",  macro_data)
            m_short = macro.gold_macro_bonus("Sell", macro_data)
        else:
            m_long  = macro.btc_macro_bonus("Buy",  macro_data)
            m_short = macro.btc_macro_bonus("Sell", macro_data)

        long_ta  = indicators.score_long(data)
        short_ta = indicators.score_short(data)
        trend_1h = data.get("trend_1h", 0)
        trend_4h = data.get("trend_4h", 0)

        piv_long  = indicators.pivot_bonus(price, pivots, "Buy")
        piv_short = indicators.pivot_bonus(price, pivots, "Sell")

        vwap_long = vwap_short = 0
        if vwap:
            if price > vwap * 1.0005:   vwap_long  = 1
            elif price < vwap * 0.9995: vwap_short = 1

        div_long  = 3 if diverg == "bullish_div" else 0
        div_short = 3 if diverg == "bearish_div" else 0

        rsi = float(data.get("rsi") or 50)

        # ── ЛОНГ ─────────────────────────────────────────────────────────────
        min_score  = config.FOREX_MIN_SCORE if is_fx else score_thresh
        # Форекс: тренд-фильтр мягче — достаточно нейтрального 15M
        trend_ok_l = (trend_1h >= 0) if is_fx else ((trend_1h == 1) or (trend_4h == 1 and trend_1h >= 0))
        rsi_ok_l   = rsi < 65

        if trend_ok_l and rsi_ok_l:
            daily_b_l = 2 if daily_trend == 1 else (-1 if daily_trend == -1 else 0)
            ob_b_l    = 1 if ob_imb >= 0.62 else 0
            tv_b_l    = _tv_boost(symbol, "Buy")
            total = (long_ta + max(0, geo_bonus) + c_long + m_long
                     + piv_long + vwap_long + div_long + sess_b
                     + daily_b_l + rvol_bonus + ob_b_l + news_bonus + tv_b_l)
            if total >= min_score:
                sig = data.copy()
                sig.update({
                    "direction":      "Buy",
                    "signal_type":    ("FX" if is_fx else "IDX" if is_idx else "COMM" if is_comm else "BTC"),
                    "tv_only":        is_tv,
                    "ta_score":       long_ta,
                    "geo_score":      geo_score,
                    "geo_headlines":  geo_headlines,
                    "geo_bonus":      max(0, geo_bonus),
                    "corr_bonus":     c_long,
                    "macro_bonus":    m_long,
                    "pivot_bonus":    piv_long,
                    "vwap_bonus":     vwap_long,
                    "div_bonus":      div_long,
                    "session_bonus":  sess_b,
                    "session_name":   sess_name,
                    "daily_bonus":    daily_b_l,
                    "rvol_bonus":     rvol_bonus,
                    "ob_bonus":       ob_b_l,
                    "news_bonus":     news_bonus,
                    "tv_bonus":       tv_b_l,
                    "daily_trend":    daily_trend,
                    "daily_adx":      daily_adx,
                    "total_score":    total,
                    "grade":          _grade(total),
                    "corr_data":      corr_data,
                    "macro_data":     macro_data,
                    "suggested_sl":   round(price - sl_dist, _p_dec),
                    "suggested_tp":   round(price + tp_dist, _p_dec),
                })
                longs.append(sig)
                tv_str = f" tv={tv_b_l:+d}" if tv_b_l != 0 else ""
                logger.info(
                    f"[{_grade(total)}] {symbol} ЛОНГ  total={total} "
                    f"ta={long_ta} sess={sess_b:+d} day={daily_b_l:+d} "
                    f"geo={max(0,geo_bonus):+d} corr={c_long:+d} macro={m_long:+d} "
                    f"piv={piv_long:+d} vwap={vwap_long:+d} div={div_long:+d} "
                    f"rvol={rvol_bonus:+d} ob={ob_b_l:+d} news={news_bonus:+d}{tv_str} "
                    f"rsi={rsi:.0f} adx={adx:.1f} d_adx={daily_adx:.0f}"
                )

        # ── ШОРТ ─────────────────────────────────────────────────────────────
        trend_ok_s = (trend_1h <= 0) if is_fx else ((trend_1h == -1) or (trend_4h == -1 and trend_1h <= 0))
        rsi_ok_s   = rsi > 35

        if trend_ok_s and rsi_ok_s:
            daily_b_s  = 2 if daily_trend == -1 else (-1 if daily_trend == 1 else 0)
            ob_b_s     = 1 if ob_imb <= 0.38 else 0
            news_b_s   = -news_bonus
            tv_b_s     = _tv_boost(symbol, "Sell")
            total = (short_ta + max(0, -geo_bonus) + c_short + m_short
                     + piv_short + vwap_short + div_short + sess_b
                     + daily_b_s + rvol_bonus + ob_b_s + news_b_s + tv_b_s)
            if total >= min_score:
                sig = data.copy()
                sig.update({
                    "direction":      "Sell",
                    "signal_type":    ("FX" if is_fx else "IDX" if is_idx else "COMM" if is_comm else "BTC"),
                    "tv_only":        is_tv,
                    "ta_score":       short_ta,
                    "geo_score":      geo_score,
                    "geo_headlines":  geo_headlines,
                    "geo_bonus":      max(0, -geo_bonus),
                    "corr_bonus":     c_short,
                    "macro_bonus":    m_short,
                    "pivot_bonus":    piv_short,
                    "vwap_bonus":     vwap_short,
                    "div_bonus":      div_short,
                    "session_bonus":  sess_b,
                    "session_name":   sess_name,
                    "daily_bonus":    daily_b_s,
                    "rvol_bonus":     rvol_bonus,
                    "ob_bonus":       ob_b_s,
                    "news_bonus":     news_b_s,
                    "tv_bonus":       tv_b_s,
                    "daily_trend":    daily_trend,
                    "daily_adx":      daily_adx,
                    "total_score":    total,
                    "grade":          _grade(total),
                    "corr_data":      corr_data,
                    "macro_data":     macro_data,
                    "suggested_sl":   round(price + sl_dist, _p_dec),
                    "suggested_tp":   round(price - tp_dist, _p_dec),
                })
                shorts.append(sig)
                tv_str = f" tv={tv_b_s:+d}" if tv_b_s != 0 else ""
                logger.info(
                    f"[{_grade(total)}] {symbol} ШОРТ  total={total} "
                    f"ta={short_ta} sess={sess_b:+d} day={daily_b_s:+d} "
                    f"geo={max(0,-geo_bonus):+d} corr={c_short:+d} macro={m_short:+d} "
                    f"piv={piv_short:+d} vwap={vwap_short:+d} div={div_short:+d} "
                    f"rvol={rvol_bonus:+d} ob={ob_b_s:+d} news={news_b_s:+d}{tv_str} "
                    f"rsi={rsi:.0f} adx={adx:.1f} d_adx={daily_adx:.0f}"
                )

    # ── Дедупликация: одно направление на символ ─────────────────────────────
    all_sigs: List[Dict] = longs + shorts
    best: dict = {}
    for sig in all_sigs:
        sym = sig["symbol"]
        if sym not in best or sig["total_score"] > best[sym]["total_score"]:
            best[sym] = sig

    longs  = sorted([s for s in best.values() if s["direction"] == "Buy"],
                    key=lambda x: x["total_score"], reverse=True)
    shorts = sorted([s for s in best.values() if s["direction"] == "Sell"],
                    key=lambda x: x["total_score"], reverse=True)
    return longs, shorts

"""Telegram сигналы — полный скальпинг-формат: ТА + Макро + Корреляции + Гео."""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import requests

import config

logger = logging.getLogger("notifications")

_EMOJI  = {
    "XAUUSDT": "🥇", "XAGUSDT": "🥈", "BTCUSDT": "₿",
    "ETHUSDT": "Ξ",  "SOLUSDT": "◎",
    "NAS100":  "📊", "SPX500":  "📈",
    "EURUSD":  "🇪🇺", "USDJPY": "🇯🇵", "GBPUSD": "🇬🇧",
    "AUDUSD":  "🇦🇺", "USDCAD": "🇨🇦", "USDCHF": "🇨🇭",
    "NZDUSD":  "🇳🇿",
}
_NAME   = {
    "XAUUSDT": "ЗОЛОТО",   "XAGUSDT": "СЕРЕБРО",  "BTCUSDT": "БИТКОИН",
    "ETHUSDT": "ЭФИРИУМ",  "SOLUSDT": "СОЛАНА",
    "NAS100":  "NASDAQ 100", "SPX500": "S&P 500",
    "EURUSD":  "EUR/USD",  "USDJPY":  "USD/JPY",  "GBPUSD":  "GBP/USD",
    "AUDUSD":  "AUD/USD",  "USDCAD":  "USD/CAD",  "USDCHF":  "USD/CHF",
    "NZDUSD":  "NZD/USD",
}

# Пары корреляций для каждого инструмента
_CORR_PAIRS = {
    "XAUUSDT": [
        ("Серебро",  "corr_xau_xag", "change_24h_xag", "trend_xag", "прямая"),
        ("Биткоин",  "corr_btc_xau", "change_24h_btc", "trend_btc", "прямая"),
    ],
    "XAGUSDT": [
        ("Золото",   "corr_xau_xag", "change_24h_xau", "trend_xau", "прямая"),
        ("Биткоин",  "corr_btc_xag", "change_24h_btc", "trend_btc", "прямая"),
    ],
    "BTCUSDT": [
        ("Эфириум",  "corr_eth_btc", "change_24h_eth", "trend_eth", "прямая"),
        ("Золото",   "corr_btc_xau", "change_24h_xau", "trend_xau", "прямая"),
    ],
    "ETHUSDT": [
        ("Биткоин",  "corr_eth_btc", "change_24h_btc", "trend_btc", "прямая"),
        ("Солана",   "corr_sol_eth", "change_24h_sol", "trend_sol", "прямая"),
    ],
    "SOLUSDT": [
        ("Эфириум",  "corr_sol_eth", "change_24h_eth", "trend_eth", "прямая"),
        ("Биткоин",  "corr_sol_btc", "change_24h_btc", "trend_btc", "прямая"),
    ],
}


def _send(text: str) -> None:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        if not r.ok:
            logger.warning(f"Telegram {r.status_code}: {r.text[:80]}")
    except Exception as e:
        logger.debug(f"Telegram: {e}")


def _fmt(sym: str, p: float) -> str:
    if sym == "XAUUSDT": return f"${p:,.2f}"
    if sym == "XAGUSDT": return f"${p:,.3f}"
    if sym == "BTCUSDT": return f"${p:,.0f}"
    if sym == "ETHUSDT": return f"${p:,.2f}"
    if sym == "SOLUSDT": return f"${p:,.3f}"
    if sym in ("NAS100", "SPX500"): return f"{p:,.1f}"
    if sym == "USDJPY":  return f"{p:.3f}"
    if sym in ("EURUSD","GBPUSD","AUDUSD","NZDUSD"): return f"{p:.5f}"
    if sym in ("USDCAD","USDCHF"): return f"{p:.5f}"
    return f"{p:.4f}"


def _rsi_icon(v: float) -> str:
    if v < 25:   return "🔵🔵 глубоко перепродан"
    if v < 35:   return "🔵 перепродан"
    if v > 75:   return "🔴🔴 глубоко перекуплен"
    if v > 65:   return "🔴 перекуплен"
    if v < 45:   return "⚪ слабый"
    if v > 55:   return "⚪ сильный"
    return "⚪ нейтральный"


def _macd_s(m: Optional[dict]) -> str:
    if not m: return "—"
    return "бычий 📈" if m.get("bull") else "медвежий 📉"


def _stoch_s(s: Optional[dict]) -> str:
    if not s: return "—"
    if s.get("oversold"):   return "перепродан 🔵"
    if s.get("overbought"): return "перекуплен 🔴"
    if s.get("bull_cross"): return "бычий крест ↑"
    if s.get("bear_cross"): return "медвежий крест ↓"
    return f"K={s.get('k',0):.0f}"


def _bb_s(b: Optional[dict], d: str) -> str:
    if not b: return "—"
    if d == "Buy"  and b.get("near_lower"): return "у нижней полосы 🟢"
    if d == "Sell" and b.get("near_upper"): return "у верхней полосы 🔴"
    return f"середина {b.get('pct_b',0.5)*100:.0f}%"


def _trend_s(t: int) -> str:
    return "⬆️ бычий" if t == 1 else "⬇️ медвежий" if t == -1 else "↔️ нейтральный"


def _confirm(r: Optional[float], direction: str, other_trend: int, chg: Optional[float]) -> str:
    """Иконка подтверждения коррелятом."""
    chg_s = f"{chg:+.2f}%" if chg is not None else ""
    if r is None:
        return f"❓ нет данных {chg_s}"
    d = 1 if direction == "Buy" else -1
    expected = d if r > 0 else -d
    if other_trend == expected:
        return f"✅ {chg_s} подтверждает"
    if other_trend == -expected:
        return f"⚠️ {chg_s} ДИВЕРГЕНЦИЯ"
    return f"↔ {chg_s} нейтрально"


def _dxy_line(direction: str, dxy: dict) -> str:
    """Форматирует строку DXY с иконкой влияния на золото (1H изменение)."""
    chg = dxy.get("chg_1h") or 0
    p   = dxy.get("price") or 0
    if direction == "Buy":
        icon = "🟢" if chg < -0.08 else "🔴" if chg > 0.08 else "⚪"
        note = "слабее $ → 🥇↑" if chg < -0.08 else "сильнее $ → против" if chg > 0.08 else "нейтрально"
    else:
        icon = "🟢" if chg > 0.08 else "🔴" if chg < -0.08 else "⚪"
        note = "сильнее $ → 🥇↓" if chg > 0.08 else "слабее $ → против" if chg < -0.08 else "нейтрально"
    return f"▸ DXY 1H: {p:.2f} ({chg:+.3f}%) {icon} {note}"


def _us10y_line(direction: str, us10y: dict) -> str:
    p   = us10y.get("price") or 0
    chg = us10y.get("chg_1h") or 0
    if direction == "Buy":
        icon = "🟢" if chg < -0.3 else "🔴" if chg > 0.3 else "⚪"
    else:
        icon = "🟢" if chg > 0.3 else "🔴" if chg < -0.3 else "⚪"
    return f"▸ US10Y 1H: {p:.2f}% ({chg:+.2f}) {icon}"


_GRADE_LABEL = {
    "🅐": "МАКСИМУМ — полный размер (≥18 очков)",
    "🅑": "СТАНДАРТ — обычный размер (10-17 очков)",
}


def send_signal(signal: dict) -> None:
    sym   = signal["symbol"]
    side  = signal["direction"]
    price = signal["price"]
    emoji = _EMOJI.get(sym, "📊")
    name  = _NAME.get(sym, sym)
    dlbl  = "ЛОНГ 📈" if side == "Buy" else "ШОРТ 📉"
    grade = signal.get("grade", "🅒")
    now_s = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    sl = signal.get("suggested_sl", 0)
    tp = signal.get("suggested_tp", 0)
    sl_pct = abs(price - sl) / price * 100 if price else 0
    tp_pct = abs(tp - price) / price * 100 if price else 0
    rr     = tp_pct / sl_pct if sl_pct else 0

    ta      = signal.get("ta_score", 0)
    geo_b   = signal.get("geo_bonus", 0)
    corr_b  = signal.get("corr_bonus", 0)
    mac_b   = signal.get("macro_bonus", 0)
    piv_b   = signal.get("pivot_bonus", 0)
    vwap_b  = signal.get("vwap_bonus", 0)
    div_b   = signal.get("div_bonus", 0)
    sess_b  = signal.get("session_bonus", 0)
    sess_nm = signal.get("session_name", "")
    total   = signal.get("total_score", ta)
    diverg  = signal.get("divergence", "none")
    vwap    = signal.get("vwap")
    pivots  = signal.get("pivots", {})
    rvol    = signal.get("rvol", 1.0)

    is_tv = signal.get("tv_only", False)
    sig_type = signal.get("signal_type", "BTC")
    type_label = {"FX": "ФОРЕКС", "IDX": "ИНДЕКС", "COMM": "МЕТАЛЛ", "BTC": "КРИПТО"}.get(sig_type, "")
    sess_line = f"  {sess_nm}" if sess_nm else ""
    tv_note = "\n⚠️ <i>TV-сигнал — торговать через TradingView / брокера</i>" if is_tv else ""

    daily_trend_val = signal.get("daily_trend", 0)
    daily_adx_val   = signal.get("daily_adx", 0.0)
    if daily_trend_val == 1:
        daily_trend_str = f"⬆️ БЫЧИЙ (ADX={daily_adx_val:.0f})"
    elif daily_trend_val == -1:
        daily_trend_str = f"⬇️ МЕДВЕЖИЙ (ADX={daily_adx_val:.0f})"
    else:
        daily_trend_str = "↔️ НЕЙТР"
    session_bias = signal.get("session_bias", "НЕЙТР")

    lines = [
        f"{grade} {emoji} <b>{name} — {dlbl}  [{type_label} 1M/5M]{sess_line}</b>",
        f"<i>{_GRADE_LABEL.get(grade, '')}</i>",
        f"📅 Тренд дня: {daily_trend_str}  |  Сессия: {session_bias}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 Цена:       <code>{_fmt(sym, price)}</code>",
        f"🛑 Стоп:       <code>{_fmt(sym, sl)}</code>  <i>(-{sl_pct:.2f}%)</i>",
        f"🎯 Цель ({config.TP_RR}R): <code>{_fmt(sym, tp)}</code>  <i>(+{tp_pct:.2f}%)</i>",
        f"📐 R:R = 1:{rr:.1f}",
        tv_note,
        "",
    ]

    # ── Дивергенция (выводим первым если есть) ────────────────────────────────
    if diverg == "bullish_div":
        lines.append("📊 <b>Бычья RSI-дивергенция</b> — цена ↓, RSI ↑ → разворот вверх 🔄")
        lines.append("")
    elif diverg == "bearish_div":
        lines.append("📊 <b>Медвежья RSI-дивергенция</b> — цена ↑, RSI ↓ → разворот вниз 🔄")
        lines.append("")

    # ── VWAP ─────────────────────────────────────────────────────────────────
    if vwap:
        vwap_pct = (price - vwap) / vwap * 100
        vwap_icon = "⬆️ выше" if price > vwap else "⬇️ ниже"
        lines.append(f"📏 VWAP: <code>{_fmt(sym, vwap)}</code>  цена {vwap_icon} VWAP ({vwap_pct:+.2f}%)")

    # ── Pivot Points ──────────────────────────────────────────────────────────
    if pivots:
        pp   = pivots.get("pp", 0)
        tol  = price * 0.003  # 0.3% для показа "около уровня"
        if side == "Buy":
            near = [(k, v) for k, v in [("S1", pivots.get("s1",0)),
                                         ("S2", pivots.get("s2",0)),
                                         ("PP", pp)]
                    if abs(price - v) <= tol]
        else:
            near = [(k, v) for k, v in [("R1", pivots.get("r1",0)),
                                         ("R2", pivots.get("r2",0)),
                                         ("PP", pp)]
                    if abs(price - v) <= tol]

        if near:
            k, v = near[0]
            lines.append(f"📍 Pivot {k}: <code>{_fmt(sym, v)}</code>  — цена у ключевого уровня!")
        else:
            lines.append(
                f"📍 Pivots: PP={_fmt(sym, pp)}"
                + (f"  R1={_fmt(sym, pivots.get('r1',0))}" if side == "Buy" else "")
                + (f"  S1={_fmt(sym, pivots.get('s1',0))}" if side == "Sell" else "")
            )

    # Объём спайк
    if rvol and rvol >= 2.0:
        lines.append(f"📊 Объём: RVOL={rvol:.1f}x — <b>спайк объёма!</b> 🔥")

    if vwap or pivots or (rvol and rvol >= 2.0):
        lines.append("")

    # ── Технический анализ (15M) ──────────────────────────────────────────────
    lines.append("🔬 <b>ТА (15M)</b>")

    rsi = signal.get("rsi")
    if rsi: lines.append(f"▸ RSI(14): {rsi:.1f} — {_rsi_icon(rsi)}")

    adx = signal.get("adx")
    if adx: lines.append(f"▸ ADX(14): {adx:.1f} — {'сильный тренд 💪' if adx>22 else 'умеренный' if adx>15 else 'слабый ⚠️'}")

    macd = signal.get("macd")
    if macd: lines.append(f"▸ MACD: {_macd_s(macd)}")

    bb = signal.get("bb")
    if bb: lines.append(f"▸ Bollinger: {_bb_s(bb, side)}")

    stoch = signal.get("stoch")
    if stoch: lines.append(f"▸ Stoch: {_stoch_s(stoch)}")

    lines.append(f"▸ Тренд 1H: {_trend_s(signal.get('trend_1h', 0))}")
    lines.append(f"▸ Тренд 4H: {_trend_s(signal.get('trend_4h', 0))}")

    ema9  = signal.get("ema9")
    ema21 = signal.get("ema21")
    if ema9 and ema21:
        cross_tag = ""
        if signal.get("ema_fresh_bull"): cross_tag = " 🔔 свежий крест вверх"
        elif signal.get("ema_fresh_bear"): cross_tag = " 🔔 свежий крест вниз"
        ema_dir = "EMA9>EMA21 📈" if signal.get("ema9_bull") else "EMA9<EMA21 📉"
        lines.append(f"▸ EMA9/21: {ema_dir}{cross_tag}")

    pdi = signal.get("pdi", 0); mdi = signal.get("mdi", 0)
    if pdi or mdi:
        adx_dir = "+DI>-DI 🟢" if pdi > mdi else "-DI>+DI 🔴"
        lines.append(f"▸ ADX направл.: {adx_dir} (+DI={pdi:.1f} / -DI={mdi:.1f})")

    pat = signal.get("multi_pat") or signal.get("candle_pat", "")
    if pat and pat not in ("none", ""):
        lines.append(f"▸ Паттерн: <b>{pat}</b> 🕯")

    fvg = signal.get("fvg", {})
    if fvg and fvg.get("type") not in (None, "none", ""):
        lines.append(f"▸ FVG: {fvg['type']} ({fvg.get('gap_pct',0):.2f}%)")

    active_fvgs = signal.get("active_fvgs", [])
    if active_fvgs:
        fvg_d = active_fvgs[0]
        fvg_dir = "↑" if fvg_d["type"] == "bullish" else "↓"
        lines.append(f"📐 FVG актив: {fvg_d['type']}{fvg_dir} [{fvg_d['bottom']:.2f}–{fvg_d['top']:.2f}] "
                     f"({fvg_d['gap_pct']:.2f}%)")

    ob = signal.get("order_block", {})
    if ob and ob.get("type") not in (None, "none", ""):
        ob_dir = "🟢" if ob["type"] == "bull" else "🔴"
        lines.append(f"🧱 OB ({ob['type'].upper()}): {ob_dir} [{ob.get('bottom',0):.2f}–{ob.get('top',0):.2f}] "
                     f"сила={ob.get('strength',0):.2f}%")

    desc_1m = signal.get("desc_1m", "")
    if desc_1m:
        lines.append(f"⚡ 1M: {desc_1m}")

    sess_prox = signal.get("sess_prox_desc", "")
    if sess_prox:
        lines.append(f"🕐 Уровни сессий: {sess_prox}")

    # Детализация скора
    score_parts = [f"TA={ta}"]
    if sess_b:  score_parts.append(f"Сесс={sess_b:+d}")
    if geo_b:   score_parts.append(f"Гео={geo_b:+d}")
    if corr_b:  score_parts.append(f"Корр={corr_b:+d}")
    if mac_b:   score_parts.append(f"Макро={mac_b:+d}")
    if piv_b:   score_parts.append(f"Pivot={piv_b:+d}")
    if vwap_b:  score_parts.append(f"VWAP={vwap_b:+d}")
    if div_b:   score_parts.append(f"Div={div_b:+d}")
    lines.append(f"▸ Итог: <b>{total}⭐</b>  [{' '.join(score_parts)}]")
    lines.append("")

    # ── Макро-корреляты (DXY, EUR/USD, US10Y, Oil) ────────────────────────────
    macro_data   = signal.get("macro_data", {})
    corr_data_mg = signal.get("corr_data", {})
    is_commodity = sym in ("XAUUSDT", "XAGUSDT")

    if macro_data and is_commodity:
        lines.append("💵 <b>Макро (1H изменения)</b>")

        if "dxy" in macro_data:
            dxy_d = macro_data["dxy"]
            chg   = dxy_d.get("chg_1h", 0)
            p     = dxy_d.get("price", 0)
            if side == "Buy":
                icon = "🟢" if chg < -0.08 else "🔴" if chg > 0.08 else "⚪"
                note = "слабее $ → 🥇↑" if chg < -0.08 else "сильнее $ → против" if chg > 0.08 else "нейтрально"
            else:
                icon = "🟢" if chg > 0.08 else "🔴" if chg < -0.08 else "⚪"
                note = "сильнее $ → 🥇↓" if chg > 0.08 else "слабее $ → против" if chg < -0.08 else "нейтрально"
            lines.append(f"▸ DXY: {p:.2f} ({chg:+.3f}% 1H) {icon} {note}")

        if "eurusd" in macro_data:
            eu    = macro_data["eurusd"]
            chg   = eu.get("chg_1h", 0)
            icon  = "🟢" if (side=="Buy" and chg>0.05) or (side=="Sell" and chg<-0.05) else "🔴" if (side=="Buy" and chg<-0.05) or (side=="Sell" and chg>0.05) else "⚪"
            lines.append(f"▸ EUR/USD: {eu.get('price',0):.4f} ({chg:+.3f}% 1H) {icon}")

        if "us10y" in macro_data:
            y   = macro_data["us10y"]
            chg = y.get("chg_1h", 0)
            icon = "🟢" if (side=="Buy" and chg<-0.3) or (side=="Sell" and chg>0.3) else "🔴" if (side=="Buy" and chg>0.3) or (side=="Sell" and chg<-0.3) else "⚪"
            lines.append(f"▸ US10Y: {y.get('price',0):.2f}% ({chg:+.2f} 1H) {icon}")

        if "oil" in macro_data:
            oil = macro_data["oil"]
            lines.append(f"▸ Oil/WTI: ${oil.get('price',0):.2f} ({oil.get('chg_1h',0):+.2f}% 1H)")

        # Движение коррелята XAU/XAG
        if sym == "XAGUSDT" and corr_data_mg:
            xau_1h = corr_data_mg.get("change_1h_xau")
            if xau_1h is not None:
                icon = "🟢" if (side=="Buy" and xau_1h>0.05) or (side=="Sell" and xau_1h<-0.05) else "🔴" if (side=="Buy" and xau_1h<-0.05) or (side=="Sell" and xau_1h>0.05) else "⚪"
                lines.append(f"▸ Золото 1H: {xau_1h:+.3f}% {icon}  ← ведущий рынок")

        if sym == "XAUUSDT" and corr_data_mg:
            xag_1h = corr_data_mg.get("change_1h_xag")
            if xag_1h is not None:
                icon = "🟢" if (side=="Buy" and xag_1h>0.08) or (side=="Sell" and xag_1h<-0.08) else "🔴" if (side=="Buy" and xag_1h<-0.08) or (side=="Sell" and xag_1h>0.08) else "⚪"
                lines.append(f"▸ Серебро 1H: {xag_1h:+.3f}% {icon}  ← подтверждение")

        lines.append("")

    elif macro_data and not is_commodity:
        lines.append("💵 <b>Макро (1H)</b>")
        if "dxy" in macro_data:
            d_d = macro_data["dxy"]
            lines.append(f"▸ DXY: {d_d.get('price',0):.2f} ({d_d.get('chg_1h',0):+.3f}% 1H)")
        if "spx" in macro_data:
            s = macro_data["spx"]
            lines.append(f"▸ S&P500: {s.get('price',0):,.0f} ({s.get('chg_1h',0):+.2f}% 1H)")
        lines.append("")

    # ── Рыночные корреляции (XAG, BTC на Bybit) ──────────────────────────────
    corr_data = signal.get("corr_data", {})
    pairs     = _CORR_PAIRS.get(sym, [])
    if pairs and corr_data:
        lines.append("🔗 <b>Рыночные корреляты</b>")
        for other_name, corr_key, chg_key, trend_key, _ in pairs:
            r     = corr_data.get(corr_key)
            chg   = corr_data.get(chg_key)
            trend = corr_data.get(trend_key, 0)
            r_s   = f"r={r:+.2f}" if r is not None else "r=?"
            conf  = _confirm(r, side, trend, chg)
            lines.append(f"▸ {other_name}: {r_s}  {conf}")
        lines.append("")

    # ── Геополитика ───────────────────────────────────────────────────────────
    geo_score = signal.get("geo_score")
    geo_h     = signal.get("geo_headlines", 0)
    if geo_score is not None:
        geo_icon = ("⚠️ ВЫСОКИЙ риск" if geo_score > 0.3
                    else "↑ умеренный" if geo_score > 0.1
                    else "✅ низкий" if geo_score < -0.1
                    else "↔ нейтральный")
        lines.append(f"🌍 Геополитика: {geo_icon} ({geo_score:+.2f})  заголовков={geo_h}")
        lines.append("")

    lines.append(f"⏰ {now_s}")
    _send("\n".join(lines))


def on_geo_update(geo_score: float, headlines: int) -> None:
    icon = ("⚠️ ВЫСОКИЙ" if geo_score > 0.3 else "↑ умеренный" if geo_score > 0.1
            else "✅ низкий" if geo_score < -0.1 else "↔ нейтральный")
    effect = ("ЛОНГ XAU/XAG ↑" if geo_score > 0.1
              else "ШОРТ XAU/XAG ↓" if geo_score < -0.1
              else "нейтрально")
    _send(
        f"🌍 <b>Геополитическое обновление</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Геориск: {icon} ({geo_score:+.2f})\n"
        f"Заголовков: {headlines}\n"
        f"Влияние: {effect}\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )


def on_error(msg: str) -> None:
    _send(f"⚠️ <b>Ошибка бота</b>\n{msg}")


# Заглушки для совместимости
def on_signal(*a, **k): pass
def on_daily_stop(*a, **k): pass
def on_close(*a, **k): pass
def on_daily_summary(*a, **k): pass

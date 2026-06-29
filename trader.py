"""Исполнение ордеров: открытие, частичный TP, trailing stop, мониторинг."""
import logging
from typing import Dict, List

import client
import config
import notifications
import risk
import state as state_module

logger = logging.getLogger("trader")


def open_position(signal: dict, balance: float, bot_state: state_module.BotState) -> bool:
    """
    Открывает позицию по сигналу:
    - Рыночный ордер с SL (без TP — TP ставятся отдельными limit-ордерами)
    - TP1 (50% qty) на 2R, TP2 (50% qty) на 4R
    """
    symbol   = signal["symbol"]
    side     = signal["direction"]
    price    = signal["price"]
    atr      = signal.get("atr_abs") or 0.0
    sig_type = signal.get("signal_type", "?")

    if atr <= 0:
        logger.warning(f"[{symbol}] ATR=0, пропускаю")
        return False

    # ── SL / TP расчёт ────────────────────────────────────────────────────────
    sl_dist  = atr * config.SL_ATR_MULT
    tp_dist  = sl_dist * config.TP_RR
    # Буфер 0.3% на проскальзывание: SL чуть дальше чтобы не получить ошибку
    # "StopLoss should be greater/less than base_price" при быстром рынке
    slippage = price * 0.003

    if side == "Buy":
        sl_price  = price - sl_dist - slippage
        tp1_price = price + tp_dist
    else:
        sl_price  = price + sl_dist + slippage
        tp1_price = price - tp_dist
    tp2_price = tp1_price  # единственный TP

    sl_dist_pct = sl_dist / price * 100

    # ── Размер позиции ────────────────────────────────────────────────────────
    qty_total = risk.calc_qty(balance, price, sl_dist_pct)

    # ── Точность инструмента ──────────────────────────────────────────────────
    info = client.get_instrument_info(symbol)
    try:
        qty_step  = float(info["lotSizeFilter"]["qtyStep"])
        tick_size = float(info["priceFilter"]["tickSize"])
        min_qty   = float(info["lotSizeFilter"]["minOrderQty"])
    except (KeyError, ValueError, TypeError):
        logger.error(f"[{symbol}] Нет данных инструмента")
        return False

    qty_total  = risk.round_qty(qty_total, qty_step)

    sl_price   = risk.round_price(sl_price,  tick_size)
    tp1_price  = risk.round_price(tp1_price, tick_size)
    tp2_price  = tp1_price

    if qty_total < min_qty:
        logger.warning(f"[{symbol}] qty={qty_total} < min={min_qty}")
        return False

    qty_str = risk.format_qty(qty_total, qty_step)
    sl_str  = risk.format_price(sl_price,  tick_size)
    tp1_str = risk.format_price(tp1_price, tick_size)

    pat = signal.get("candle_pat", "")
    fvg = signal.get("fvg", {})
    fvg_str = f"FVG={fvg.get('type','?')}({fvg.get('gap_pct',0):.2f}%)" if fvg.get("type") != "none" else ""
    _sig_line = (
        f"[{sig_type}] {side} {symbol}  qty={qty_str}  entry≈{price:.6f}  "
        f"sl={sl_str}  tp={tp1_str}  R:R 1:{config.TP_RR}"
        + (f"  pat={pat}" if pat and pat != "none" else "")
        + (f"  {fvg_str}" if fvg_str else "")
    )

    # ── Paper mode ────────────────────────────────────────────────────────────
    if config.PAPER_MODE:
        bot_state.add_position(
            symbol, side, price, qty_total,
            sl_price, tp1_price, tp2_price, sl_dist, sig_type,
        )
        logger.info(f"[PAPER] {_sig_line}")
        return True

    # ── Реальный ордер ────────────────────────────────────────────────────────
    try:
        client.set_leverage(symbol, config.LEVERAGE)
        result = client.place_order(symbol, side, qty_str, sl_str)  # без TP
        order_id = result.get("orderId", "?")
        logger.info(_sig_line)  # логируем только при успехе
        logger.info(f"Рыночный ордер orderId={order_id}")

        # Один TP ордер на всю позицию (скальпинг)
        tp1_result = client.place_tp_limit_order(symbol, side, qty_str, tp1_str)
        tp1_oid    = tp1_result.get("orderId", "")
        tp2_oid    = ""
        logger.info(f"TP orderId={tp1_oid}")

        bot_state.add_position(
            symbol, side, price, qty_total,
            sl_price, tp1_price, tp2_price, sl_dist, sig_type,
            tp1_order_id=tp1_oid, tp2_order_id=tp2_oid,
        )
        bot_state.mark_opened(symbol)
        notifications.on_signal(
            symbol, side, price, sl_price, tp1_price, sig_type,
            pat=signal.get("candle_pat", ""),
            fvg_type=signal.get("fvg", {}).get("type", ""),
            signal=signal,
        )
        return True
    except Exception as e:
        err = str(e)
        if "110007" in err:
            logger.warning(f"[{symbol}] Недостаточно маржи — пропускаю")
        else:
            logger.error(f"[{symbol}] Ошибка ордера: {e}")
        return False


def monitor_positions(
    bot_state:       state_module.BotState,
    bybit_positions: List[Dict],
    tickers_map:     dict,
) -> None:
    """
    Вызывается каждый цикл.
    Real mode: проверяет break-even и активирует trailing stop через Bybit API.
    Paper mode: делегирует в state.update_paper_positions().
    """
    if config.PAPER_MODE or not bot_state.positions:
        return

    bybit_map = {p["symbol"]: p for p in bybit_positions}

    for symbol, pos in list(bot_state.positions.items()):
        bybit_pos = bybit_map.get(symbol)
        if not bybit_pos:
            continue

        cur     = float(tickers_map.get(symbol, {}).get("lastPrice", 0))
        side    = pos["side"]
        entry   = pos["entry_price"]
        sl_dist = pos.get("sl_dist", 0)
        if not cur or sl_dist <= 0:
            continue

        favor = (cur - entry) if side == "Buy" else (entry - cur)

        # ── Break-even + Trailing (активируем при +1R) ────────────────────────
        if not pos.get("breakeven_set") and favor >= sl_dist * config.BREAKEVEN_R:
            try:
                trail_dist  = sl_dist * config.TRAILING_ATR_MULT
                active_price = risk.round_price(
                    entry + sl_dist if side == "Buy" else entry - sl_dist,
                    _tick(symbol)
                )
                client.set_trading_stop(
                    symbol,
                    sl=risk.format_price(entry, _tick(symbol)),
                    trailing_stop=risk.format_price(trail_dist, _tick(symbol)),
                    active_price=risk.format_price(active_price, _tick(symbol)),
                )
                bot_state.update_field(symbol, breakeven_set=True, trailing_active=True,
                                       current_sl=entry)
                logger.info(f"{symbol} Break-even + Trailing активированы. SL → {entry:.6f}")
            except Exception as e:
                err = str(e)
                if "34040" in err:  # not modified — трейлинг уже выставлен, игнорируем
                    bot_state.update_field(symbol, breakeven_set=True, current_sl=entry)
                elif "10001" in err or "TrailingProfit" in err:
                    # Цена уже прошла точку активации — просто обновляем SL в безубыток
                    try:
                        client.set_trading_stop(symbol, sl=risk.format_price(entry, _tick(symbol)))
                        bot_state.update_field(symbol, breakeven_set=True, current_sl=entry)
                        logger.info(f"{symbol} Break-even (без трейлинга). SL → {entry:.6f}")
                    except Exception:
                        pass
                else:
                    logger.error(f"[{symbol}] Trailing stop error: {e}")

        # ── Проверяем частичный TP1 (позиция уменьшилась вдвое) ──────────────
        if not pos.get("tp1_hit"):
            bybit_qty = float(bybit_pos.get("size", pos["qty"]))
            if bybit_qty <= pos["qty"] * 0.6:  # -40% → TP1 исполнился
                bot_state.update_field(symbol, tp1_hit=True)
                logger.info(f"{symbol} TP1 исполнен (50% закрыто). Остаток идёт к TP2.")


def _tick(symbol: str) -> float:
    info = client.get_instrument_info(symbol)
    try:
        return float(info["priceFilter"]["tickSize"])
    except (KeyError, ValueError, TypeError):
        return 0.0001


def check_closed_positions(bot_state: state_module.BotState, tickers_map: dict) -> List[dict]:
    """Обнаруживает позиции, закрытые Bybit (SL/TP). Возвращает закрытые."""
    if config.PAPER_MODE:
        closed = bot_state.update_paper_positions(tickers_map)
        for c in closed:
            sign = "+" if c["pnl"] >= 0 else ""
            logger.info(
                f"[PAPER] Закрыто {c['side']} {c['symbol']}  "
                f"pnl={sign}{c['pnl']:.2f} USDT  причина={c['reason']}"
            )
        return closed

    try:
        bybit_syms = {p["symbol"] for p in client.get_positions()}
    except Exception as e:
        logger.error(f"Ошибка получения позиций: {e}")
        return []

    closed = []
    for symbol in list(bot_state.positions.keys()):
        if symbol not in bybit_syms:
            # Позиция только что открыта — Bybit ещё не показывает её, не закрываем
            if bot_state.recently_opened(symbol, cooldown_sec=30):
                continue
            pos    = bot_state.positions.get(symbol, {})
            record = _fetch_closed_record(symbol)
            pnl    = float(record.get("closedPnl", 0)) if record else 0.0
            reason = _detect_close_reason(pos, record)

            # Отменяем оставшиеся TP-ордера
            for oid_key in ("tp1_order_id", "tp2_order_id"):
                oid = pos.get(oid_key, "")
                if oid:
                    client.cancel_order(symbol, oid)

            bot_state.close_position(symbol, pnl, reason)
            sign = "+" if pnl >= 0 else ""
            logger.info(
                f"Закрыто {pos.get('side','?')} {symbol}  "
                f"pnl={sign}{pnl:.2f} USDT  причина={reason}"
            )
            notifications.on_close(symbol, pos.get("side", "?"), pnl, reason)
            closed.append({"symbol": symbol, "pnl": pnl, "reason": reason})

    return closed


def _fetch_closed_record(symbol: str) -> dict:
    try:
        records = client.get_closed_pnl(symbol, limit=1)
        return records[0] if records else {}
    except Exception:
        return {}


def _detect_close_reason(pos: dict, record: dict) -> str:
    """
    Определяет причину закрытия позиции:
      SL     — цена закрытия совпадает с SL (±0.5%)
      TP     — цена закрытия совпадает с TP1 или TP2
      Manual — всё остальное (ручное закрытие трейдером)
    """
    if not record or not pos:
        return "Manual"

    exit_price = float(record.get("avgExitPrice", 0))
    if not exit_price:
        pnl = float(record.get("closedPnl", 0))
        return "TP" if pnl >= 0 else "SL"

    sl   = pos.get("sl",  0)
    tp1  = pos.get("tp1", 0)
    tp2  = pos.get("tp2", 0)

    def near(a, b, pct=0.5) -> bool:
        return b and abs(a - b) / b * 100 <= pct

    if near(exit_price, sl):
        return "SL"
    if near(exit_price, tp1) or near(exit_price, tp2):
        return "TP"
    return "Manual"


def process_signals(
    signals_long:  List[dict],
    signals_short: List[dict],
    bot_state:     state_module.BotState,
    balance:       float,
) -> int:
    """Обрабатывает сигналы и открывает до 3 позиций параллельно."""
    import concurrent.futures as _cf

    slots = min(config.MAX_POSITIONS - bot_state.open_count, 3)
    if slots <= 0:
        return 0

    candidates: List[dict] = []
    for sig in signals_long[:5]:
        sym = sig["symbol"]
        if sym not in bot_state.open_symbols and not bot_state.recently_opened(sym):
            candidates.append(sig)
    for sig in signals_short[:5]:
        sym = sig["symbol"]
        if sym not in bot_state.open_symbols and not bot_state.recently_opened(sym):
            candidates.append(sig)

    def score(s: dict) -> float:
        return s.get("ta_score") or 0

    candidates.sort(key=score, reverse=True)
    batch = candidates[:slots]

    if not batch:
        return 0

    # Резервируем символы ДО запуска потоков — предотвращает дубли при параллельном открытии
    for sig in batch:
        bot_state.mark_opened(sig["symbol"])

    results = []
    with _cf.ThreadPoolExecutor(max_workers=len(batch)) as pool:
        futures = [pool.submit(open_position, sig, balance, bot_state) for sig in batch]
        for f in _cf.as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                logger.error(f"Ошибка открытия позиции: {e}")

    return sum(1 for r in results if r)

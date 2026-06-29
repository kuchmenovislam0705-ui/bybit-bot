"""Состояние бота: открытые позиции, дневная статистика, история сделок."""
import json
import os
from datetime import date, datetime
from typing import Dict, List, Optional

import config


class BotState:
    def __init__(self) -> None:
        self.positions:       Dict[str, dict]    = {}
        self.daily:           dict               = {}
        self.trade_history:   List[dict]         = []
        self.last_sl_time:    Optional[datetime] = None
        self.start_equity:    float              = 0.0
        self._recently_opened: Dict[str, datetime] = {}   # symbol → время открытия
        self._load()

    # ── Персистентность ───────────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(config.STATE_FILE):
            self._reset_daily()
            return

        with open(config.STATE_FILE) as f:
            data = json.load(f)

        today = str(date.today())
        self.positions = data.get("positions", {})

        daily = data.get("daily", {})
        if daily.get("date") == today:
            self.daily         = daily
            self.trade_history = data.get("trade_history", [])
            self.start_equity  = daily.get("start_equity", 0.0)
        else:
            self._reset_daily()

        sl_ts = data.get("last_sl_time")
        self.last_sl_time = datetime.fromisoformat(sl_ts) if sl_ts else None

    def save(self) -> None:
        data = {
            "positions":     self.positions,
            "daily":         self.daily,
            "trade_history": self.trade_history[-100:],
            "last_sl_time":  self.last_sl_time.isoformat() if self.last_sl_time else None,
        }
        with open(config.STATE_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _reset_daily(self) -> None:
        self.daily = {
            "date":         str(date.today()),
            "start_equity": 0.0,
            "trades":       0,
            "wins":         0,
            "losses":       0,
            "gross_pnl":    0.0,
        }
        self.trade_history = []

    # ── Управление позициями ──────────────────────────────────────────────────

    def add_position(self, symbol: str, side: str, entry: float,
                     qty: float, sl: float, tp1: float, tp2: float,
                     sl_dist: float, signal_type: str,
                     tp1_order_id: str = "", tp2_order_id: str = "") -> None:
        self.positions[symbol] = {
            "symbol":         symbol,
            "side":           side,
            "entry_price":    entry,
            "qty":            qty,
            "sl":             sl,
            "tp1":            tp1,
            "tp2":            tp2,
            "sl_dist":        sl_dist,    # абсолютное расстояние SL (для трейлинга)
            "signal_type":    signal_type,
            "opened_at":      datetime.now().isoformat(),
            # Частичный TP
            "tp1_order_id":   tp1_order_id,
            "tp2_order_id":   tp2_order_id,
            "tp1_hit":        False,
            # Trailing / Break-even
            "breakeven_set":  False,
            "trailing_active": False,
            "current_sl":     sl,        # актуальный SL (обновляется при трейлинге)
        }
        self.save()

    def close_position(self, symbol: str, pnl: float, reason: str = "") -> None:
        pos = self.positions.pop(symbol, None)
        if pos:
            trade = {**pos, "pnl": pnl, "reason": reason,
                     "closed_at": datetime.now().isoformat()}
            self.trade_history.append(trade)
            self.daily["trades"]    += 1
            self.daily["gross_pnl"] += pnl
            if pnl >= 0:
                self.daily["wins"]   += 1
            else:
                self.daily["losses"] += 1
            # Кулдаун только при реальном стопе, не при ручном закрытии
            if reason == "SL":
                self.last_sl_time = datetime.now()
        self.save()

    def mark_opened(self, symbol: str) -> None:
        """Запоминаем время открытия чтобы не открыть тот же символ снова в ближайшие 120 сек."""
        self._recently_opened[symbol] = datetime.utcnow()

    def recently_opened(self, symbol: str, cooldown_sec: int = 300) -> bool:
        ts = self._recently_opened.get(symbol)
        if not ts:
            return False
        elapsed = (datetime.utcnow() - ts).total_seconds()
        if elapsed > cooldown_sec:
            del self._recently_opened[symbol]
            return False
        return True

    @property
    def open_symbols(self) -> set:
        return set(self.positions.keys())

    @property
    def open_count(self) -> int:
        return len(self.positions)

    # ── Ограничения ───────────────────────────────────────────────────────────

    def set_start_equity(self, equity: float) -> None:
        if self.daily.get("start_equity", 0) == 0 and equity > 0:
            self.daily["start_equity"] = equity
            self.start_equity = equity
            self.save()

    def daily_loss_hit(self, current_equity: float) -> bool:
        start = self.daily.get("start_equity", 0)
        if start <= 0:
            return False
        loss_pct = (start - current_equity) / start * 100
        return loss_pct >= config.DAILY_LOSS_LIMIT

    def daily_loss_pct(self, current_equity: float) -> float:
        start = self.daily.get("start_equity", 0)
        if start <= 0:
            return 0.0
        return (current_equity - start) / start * 100

    def in_cooldown(self) -> bool:
        if not self.last_sl_time:
            return False
        elapsed = (datetime.now() - self.last_sl_time).total_seconds() / 60
        return elapsed < config.COOLDOWN_AFTER_SL

    def cooldown_remaining_min(self) -> int:
        if not self.last_sl_time:
            return 0
        elapsed = (datetime.now() - self.last_sl_time).total_seconds() / 60
        return max(0, int(config.COOLDOWN_AFTER_SL - elapsed))

    def update_field(self, symbol: str, **kwargs) -> None:
        if symbol in self.positions:
            self.positions[symbol].update(kwargs)
            self.save()

    # ── Paper mode: SL / частичный TP / trailing ──────────────────────────────

    def update_paper_positions(self, tickers_map: dict) -> List[dict]:
        """
        Для paper mode — проверяет каждую позицию:
          Break-even при +1R, trailing stop, частичный TP1(2R)/TP2(4R), SL.
        Возвращает список полностью закрытых позиций.
        """
        import config as _cfg
        closed = []

        for symbol in list(self.positions.keys()):
            pos     = self.positions[symbol]
            cur     = float(tickers_map.get(symbol, {}).get("lastPrice", 0))
            if not cur:
                continue
            side    = pos["side"]
            entry   = pos["entry_price"]
            qty     = pos["qty"]
            sl_dist = pos.get("sl_dist", 0)
            cur_sl  = pos.get("current_sl", pos["sl"])
            tp1     = pos.get("tp1", entry + sl_dist * _cfg.TP1_RR if side == "Buy" else entry - sl_dist * _cfg.TP1_RR)
            tp2     = pos.get("tp2", entry + sl_dist * _cfg.TP2_RR if side == "Buy" else entry - sl_dist * _cfg.TP2_RR)
            favor   = (cur - entry) if side == "Buy" else (entry - cur)

            # Break-even при +1R
            if sl_dist > 0 and not pos.get("breakeven_set") and favor >= sl_dist * _cfg.BREAKEVEN_R:
                self.update_field(symbol, breakeven_set=True, trailing_active=True, current_sl=entry)
                cur_sl = entry

            # Обновляем trailing SL
            if pos.get("trailing_active") and sl_dist > 0:
                trail = sl_dist * _cfg.TRAILING_ATR_MULT
                if side == "Buy":
                    new_sl = cur - trail
                    if new_sl > cur_sl:
                        self.update_field(symbol, current_sl=new_sl)
                        cur_sl = new_sl
                else:
                    new_sl = cur + trail
                    if new_sl < cur_sl:
                        self.update_field(symbol, current_sl=new_sl)
                        cur_sl = new_sl

            # Проверяем SL
            sl_hit = (side == "Buy" and cur <= cur_sl) or (side == "Sell" and cur >= cur_sl)
            if sl_hit:
                rem = qty * 0.5 if pos.get("tp1_hit") else qty
                pnl = (cur_sl - entry) * rem if side == "Buy" else (entry - cur_sl) * rem
                if pos.get("tp1_hit"):
                    pnl += (tp1 - entry) * (qty * 0.5) if side == "Buy" else (entry - tp1) * (qty * 0.5)
                self.close_position(symbol, pnl, "SL")
                closed.append({**pos, "pnl": pnl, "reason": "SL"})
                continue

            # TP1 (2R) — закрываем 50%
            if not pos.get("tp1_hit"):
                if (side == "Buy" and cur >= tp1) or (side == "Sell" and cur <= tp1):
                    self.update_field(symbol, tp1_hit=True, breakeven_set=True,
                                      trailing_active=True, current_sl=entry)
                    continue

            # TP2 (4R) — закрываем остаток
            if pos.get("tp1_hit"):
                if (side == "Buy" and cur >= tp2) or (side == "Sell" and cur <= tp2):
                    pnl1 = (tp1 - entry) * (qty * 0.5) if side == "Buy" else (entry - tp1) * (qty * 0.5)
                    pnl2 = (tp2 - entry) * (qty * 0.5) if side == "Buy" else (entry - tp2) * (qty * 0.5)
                    self.close_position(symbol, pnl1 + pnl2, "TP2")
                    closed.append({**pos, "pnl": pnl1 + pnl2, "reason": "TP2"})

        return closed

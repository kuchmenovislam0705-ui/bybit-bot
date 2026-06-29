"""
Трекер исходов сигналов и адаптивный обучатель.

Каждый отправленный сигнал записывается в signals_db.json.
Фоновый поток каждые 60с проверяет: достигнута ли цена TP или SL.
После 4H без исхода — оцениваем по направлению движения.

Адаптивный скор:
  win rate < 30%  → мин.скор +4  (резкое ужесточение)
  win rate < 40%  → мин.скор +3
  win rate < 50%  → мин.скор +2
  win rate < 60%  → мин.скор +1
  win rate ≥ 60%  → базовый мин.скор (хорошо работаем)
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config

logger = logging.getLogger("tracker")

_DB   = os.path.join(os.path.dirname(__file__), "signals_db.json")
_lock = threading.Lock()


# ── Хранилище ─────────────────────────────────────────────────────────────────

def _load() -> List[Dict]:
    try:
        with open(_DB, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(sigs: List[Dict]) -> None:
    with open(_DB, "w", encoding="utf-8") as f:
        json.dump(sigs[-300:], f, indent=2, default=str, ensure_ascii=False)


# ── Запись сигнала ─────────────────────────────────────────────────────────────

def record(sig: dict) -> None:
    """Сохраняет новый сигнал для последующего трекинга исхода."""
    now = datetime.now(timezone.utc)
    with _lock:
        sigs = _load()
        rec = {
            "id":        f"{sig['symbol']}_{sig['direction']}_{now.strftime('%Y%m%d_%H%M')}",
            "symbol":    sig["symbol"],
            "direction": sig["direction"],
            "price":     sig["price"],
            "sl":        sig.get("suggested_sl", 0),
            "tp":        sig.get("suggested_tp", 0),
            "atr":       sig.get("atr_abs", 0),
            "score":     sig.get("total_score", 0),
            "ta":        sig.get("ta_score", 0),
            "rsi":       sig.get("rsi", 0),
            "adx":       sig.get("adx", 0),
            "trend_1h":  sig.get("trend_1h", 0),
            "trend_4h":  sig.get("trend_4h", 0),
            "session":   sig.get("session_name", ""),
            "pattern":   sig.get("candle_pat", "none"),
            "divergence":sig.get("divergence", "none"),
            "timestamp": now.isoformat(),
            "outcome":   None,   # win / loss / neutral
            "pnl_r":     None,   # в R-мультиплах
            "checked":   False,
        }
        sigs.append(rec)
        _save(sigs)
    logger.info(f"Трекер: записан {rec['id']} SL={rec['sl']} TP={rec['tp']}")


# ── Фоновый трекер исходов ─────────────────────────────────────────────────────

def _fetch_price(symbol: str) -> Optional[float]:
    """Берём последнюю цену из Bybit без импорта client (избегаем циклических import)."""
    import requests
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=6,
        )
        data = r.json()
        if data.get("retCode") == 0:
            lst = data["result"].get("list", [])
            if lst:
                return float(lst[0]["lastPrice"])
    except Exception:
        pass
    return None


def _check_loop() -> None:
    while True:
        try:
            with _lock:
                sigs = _load()

            changed = False
            now = datetime.now(timezone.utc)

            for sig in sigs:
                if sig.get("checked"):
                    continue

                ts = datetime.fromisoformat(sig["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_h = (now - ts).total_seconds() / 3600

                price_now = _fetch_price(sig["symbol"])
                if price_now is None:
                    continue

                entry = float(sig["price"])
                sl    = float(sig["sl"])
                tp    = float(sig["tp"])
                d     = sig["direction"]

                def _mark(outcome: str, pnl_r: float) -> None:
                    nonlocal changed
                    sig["outcome"] = outcome
                    sig["pnl_r"]   = round(pnl_r, 2)
                    sig["checked"] = True
                    changed = True
                    icon = "✅ WIN" if outcome == "win" else ("❌ LOSS" if outcome == "loss" else "➖ НЕЙТР")
                    logger.info(
                        f"Трекер {icon}: {sig['symbol']} {'ЛОНГ' if d=='Buy' else 'ШОРТ'} "
                        f"entry={entry:.1f} now={price_now:.1f} R={pnl_r:.2f}"
                    )

                if d == "Buy":
                    sl_valid = sl > 0 and sl < entry
                    tp_valid = tp > entry
                    sl_dist  = entry - sl if sl_valid else entry * 0.003

                    if tp_valid and price_now >= tp:
                        _mark("win", 2.0)
                    elif sl_valid and price_now <= sl:
                        _mark("loss", -1.0)
                    elif age_h >= 4.0:
                        move  = price_now - entry
                        pnl_r = move / sl_dist if sl_dist > 0 else 0
                        outcome = "win" if pnl_r > 0.4 else "loss" if pnl_r < -0.4 else "neutral"
                        _mark(outcome, pnl_r)

                else:  # Sell
                    sl_valid = sl > entry
                    tp_valid = tp > 0 and tp < entry
                    sl_dist  = sl - entry if sl_valid else entry * 0.003

                    if tp_valid and price_now <= tp:
                        _mark("win", 2.0)
                    elif sl_valid and price_now >= sl:
                        _mark("loss", -1.0)
                    elif age_h >= 4.0:
                        move  = entry - price_now
                        pnl_r = move / sl_dist if sl_dist > 0 else 0
                        outcome = "win" if pnl_r > 0.4 else "loss" if pnl_r < -0.4 else "neutral"
                        _mark(outcome, pnl_r)

            if changed:
                with _lock:
                    _save(sigs)

        except Exception as e:
            logger.debug(f"Трекер ошибка: {e}")

        time.sleep(60)


def start() -> None:
    """Запускает фоновый поток трекинга исходов."""
    t = threading.Thread(target=_check_loop, daemon=True, name="signal-tracker")
    t.start()
    logger.info("Трекер сигналов запущен (проверка каждые 60с)")


# ── Статистика ─────────────────────────────────────────────────────────────────

def get_stats() -> Dict:
    """Полная статистика производительности бота."""
    sigs     = _load()
    resolved = [s for s in sigs if s.get("outcome") in ("win", "loss")]
    recent   = resolved[-20:]
    pending  = [s for s in sigs if not s.get("checked")]

    wins_all = sum(1 for s in resolved if s["outcome"] == "win")
    wins_rec = sum(1 for s in recent  if s["outcome"] == "win")

    # По сессии
    by_sess: Dict[str, List[int]] = {}
    for s in resolved:
        k = s.get("session") or "?"
        if k not in by_sess:
            by_sess[k] = [0, 0]
        by_sess[k][1] += 1
        if s["outcome"] == "win":
            by_sess[k][0] += 1

    # По символу
    by_sym: Dict[str, List[int]] = {}
    for s in resolved:
        k = s["symbol"]
        if k not in by_sym:
            by_sym[k] = [0, 0]
        by_sym[k][1] += 1
        if s["outcome"] == "win":
            by_sym[k][0] += 1

    # Последние 5 с исходом
    last5 = []
    for s in reversed(sigs[-10:]):
        out  = s.get("outcome") or "open"
        icon = {"win": "✅", "loss": "❌", "neutral": "➖", "open": "🕐"}.get(out, "🕐")
        d    = "▲" if s["direction"] == "Buy" else "▼"
        ts   = str(s["timestamp"])[:16].replace("T", " ")
        sc   = s.get("score", 0)
        last5.append(f"{icon} {d} {s['symbol']} @{s['price']:.1f}  скор={sc}  [{ts}]")

    return {
        "total":      len(resolved),
        "wins":       wins_all,
        "losses":     len(resolved) - wins_all,
        "win_rate":   wins_all / len(resolved) if resolved else 0.0,
        "recent_n":   len(recent),
        "recent_wins":wins_rec,
        "recent_wr":  wins_rec / len(recent) if recent else 0.0,
        "by_sess":    by_sess,
        "by_sym":     by_sym,
        "last5":      last5,
        "pending":    len(pending),
        "adaptive":   get_adaptive_score(),
    }


# ── Адаптивный скор ────────────────────────────────────────────────────────────

def get_adaptive_score() -> int:
    """
    Адаптивный минимальный скор на основе последних 8 закрытых сигналов.
    Чем хуже статистика — тем выше требования к новым сигналам.
    Данных < 5 → возвращает базовый порог из config.
    """
    sigs     = _load()
    resolved = [s for s in sigs if s.get("outcome") in ("win", "loss")]
    recent   = resolved[-8:]

    if len(recent) < 5:
        return config.SIGNAL_MIN_SCORE

    wr   = sum(1 for s in recent if s["outcome"] == "win") / len(recent)
    base = config.SIGNAL_MIN_SCORE

    if wr < 0.30:
        adj, reason = base + 4, f"критически низкий {wr:.0%}"
    elif wr < 0.40:
        adj, reason = base + 3, f"очень низкий {wr:.0%}"
    elif wr < 0.50:
        adj, reason = base + 2, f"ниже нормы {wr:.0%}"
    elif wr < 0.60:
        adj, reason = base + 1, f"нормальный {wr:.0%}"
    else:
        adj, reason = base,     f"хороший {wr:.0%} — базовый порог"

    if adj != base:
        logger.info(f"Адаптив (win rate {reason}): мин.скор={adj} (база={base})")
    return adj

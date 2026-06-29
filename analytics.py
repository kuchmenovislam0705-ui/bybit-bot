"""Модуль аналитики: Win Rate, Profit Factor, Max Drawdown, Sharpe Ratio."""
from typing import List, Dict


def win_rate(history: List[Dict]) -> float:
    total = len(history)
    if not total:
        return 0.0
    wins = sum(1 for t in history if t.get("pnl", 0) >= 0)
    return round(wins / total * 100, 1)


def profit_factor(history: List[Dict]) -> float:
    gross_profit = sum(t.get("pnl", 0) for t in history if t.get("pnl", 0) > 0)
    gross_loss   = abs(sum(t.get("pnl", 0) for t in history if t.get("pnl", 0) < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return round(gross_profit / gross_loss, 2)


def max_drawdown(history: List[Dict]) -> float:
    """Максимальная просадка в USDT от пика до дна."""
    if not history:
        return 0.0
    equity = 0.0
    peak   = 0.0
    max_dd = 0.0
    for t in history:
        equity += t.get("pnl", 0)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def sharpe_ratio(history: List[Dict]) -> float:
    """Упрощённый Sharpe по сделкам (без учёта безрисковой ставки)."""
    if len(history) < 2:
        return 0.0
    import math
    pnls = [t.get("pnl", 0) for t in history]
    mean = sum(pnls) / len(pnls)
    variance = sum((p - mean) ** 2 for p in pnls) / len(pnls)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return round(mean / std, 2)


def avg_win(history: List[Dict]) -> float:
    wins = [t.get("pnl", 0) for t in history if t.get("pnl", 0) > 0]
    return round(sum(wins) / len(wins), 2) if wins else 0.0


def avg_loss(history: List[Dict]) -> float:
    losses = [t.get("pnl", 0) for t in history if t.get("pnl", 0) < 0]
    return round(sum(losses) / len(losses), 2) if losses else 0.0


def full_report(history: List[Dict]) -> Dict:
    return {
        "total_trades":  len(history),
        "win_rate":      win_rate(history),
        "profit_factor": profit_factor(history),
        "max_drawdown":  max_drawdown(history),
        "sharpe":        sharpe_ratio(history),
        "avg_win":       avg_win(history),
        "avg_loss":      avg_loss(history),
        "total_pnl":     round(sum(t.get("pnl", 0) for t in history), 2),
    }

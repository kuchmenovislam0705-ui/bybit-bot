"""Терминальный интерфейс бота на Rich."""
from datetime import datetime
from typing import Dict, List, Optional

from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
import state as state_module


def _fmt_price(p: float) -> str:
    if p >= 100:   return f"${p:,.2f}"
    if p >= 1:     return f"${p:.3f}"
    if p >= 0.01:  return f"${p:.4f}"
    return f"${p:.6f}"


def _fmt_vol(v: float) -> str:
    if v >= 1_000_000_000: return f"${v / 1e9:.1f}B"
    return f"${v / 1e6:.0f}M"


def _coin(sym: str) -> str:
    return sym[:-4] if sym.endswith("USDT") else sym


def _sign_str(v: float) -> str:
    return f"+{v:.2f}" if v >= 0 else f"{v:.2f}"


# ── Шапка ─────────────────────────────────────────────────────────────────────

def _header(bot_state: state_module.BotState, equity: float,
            status: str, iteration: int) -> Panel:
    now    = datetime.now().strftime("%H:%M:%S")
    mode   = "[bold red]MAINNET[/bold red]" if not config.TESTNET else "[yellow]TESTNET[/yellow]"
    paper  = "  [dim]│[/dim]  [cyan]PAPER[/cyan]" if config.PAPER_MODE else ""

    daily_pnl = bot_state.daily.get("gross_pnl", 0.0)
    dpct      = bot_state.daily_loss_pct(equity)
    dc        = "green" if dpct >= 0 else "red"
    dsign     = "+" if dpct >= 0 else ""
    trades    = bot_state.daily.get("trades", 0)
    wins      = bot_state.daily.get("wins", 0)
    losses    = bot_state.daily.get("losses", 0)

    line1 = (
        f"[bold cyan]BYBIT TRADING BOT[/bold cyan]"
        f"  [dim]│[/dim]  {mode}{paper}"
        f"  [dim]│[/dim]  [white]{now}[/white]"
        f"  [dim]│[/dim]  итерация #{iteration}"
    )
    line2 = (
        f"Баланс: [bold white]${equity:,.2f}[/bold white]"
        f"  [dim]│[/dim]  "
        f"День: [{dc}]{dsign}{daily_pnl:.2f} USDT ({dsign}{dpct:.1f}%)[/{dc}]"
        f"  [dim]│[/dim]  "
        f"Сделки: [white]{trades}[/white]  "
        f"[green]W:{wins}[/green]  [red]L:{losses}[/red]"
        f"  [dim]│[/dim]  "
        f"Позиций: [yellow]{bot_state.open_count}/{config.MAX_POSITIONS}[/yellow]"
        f"  [dim]│[/dim]  [dim]{status}[/dim]"
    )
    return Panel(Align.center(f"{line1}\n{line2}"), border_style="cyan", padding=(0, 1))


# ── Открытые позиции ──────────────────────────────────────────────────────────

def _positions_table(bot_state: state_module.BotState,
                     bybit_positions: List[Dict],
                     tickers_map: dict) -> Table:
    t = Table(
        title="[bold white]ОТКРЫТЫЕ ПОЗИЦИИ[/bold white]",
        box=box.ROUNDED, border_style="white",
        show_header=True, header_style="bold",
    )
    t.add_column("Монета",  style="bold cyan", min_width=10)
    t.add_column("Напр.",   justify="center",  min_width=6)
    t.add_column("Сигнал",  justify="center",  min_width=6)
    t.add_column("Вход",    justify="right",   min_width=10)
    t.add_column("Текущ.",  justify="right",   min_width=10)
    t.add_column("PnL",     justify="right",   min_width=12)
    t.add_column("SL",      justify="right",   min_width=10)
    t.add_column("TP",      justify="right",   min_width=10)
    t.add_column("Плечо",   justify="center",  min_width=5)

    if not bot_state.positions:
        t.add_row("[dim]нет открытых позиций[/dim]", *[""] * 8)
        return t

    # Данные с Bybit (для real mode PnL)
    bybit_map = {p["symbol"]: p for p in bybit_positions}

    for symbol, pos in bot_state.positions.items():
        side   = pos["side"]
        entry  = pos["entry_price"]
        qty    = pos["qty"]
        sl  = pos.get("sl", 0)
        tp1 = pos.get("tp1", 0)
        sig    = pos.get("signal_type", "?")

        # Текущая цена
        cur_price = float(tickers_map.get(symbol, {}).get("lastPrice", entry))

        # PnL
        if symbol in bybit_map and not config.PAPER_MODE:
            pnl_usdt = float(bybit_map[symbol].get("unrealisedPnl", 0))
            leverage  = bybit_map[symbol].get("leverage", config.LEVERAGE)
        else:
            pnl_usdt = (cur_price - entry) * qty if side == "Buy" else (entry - cur_price) * qty
            leverage  = config.LEVERAGE

        margin   = (qty * entry) / config.LEVERAGE
        pnl_pct  = (pnl_usdt / margin * 100) if margin else 0
        pnl_c    = "green" if pnl_usdt >= 0 else "red"
        side_c   = "green" if side == "Buy" else "red"
        side_sym = "▲ ЛОНГ" if side == "Buy" else "▼ ШОРТ"

        t.add_row(
            _coin(symbol),
            f"[{side_c}]{side_sym}[/{side_c}]",
            f"[cyan]{sig}[/cyan]",
            _fmt_price(entry),
            _fmt_price(cur_price),
            f"[{pnl_c}]{_sign_str(pnl_usdt)} ({_sign_str(pnl_pct)}%)[/{pnl_c}]",
            f"[red]{_fmt_price(sl)}[/red]",
            f"[green]{_fmt_price(tp1)}[/green]",
            f"{leverage}x",
        )
    return t


# ── Сигналы скринера ──────────────────────────────────────────────────────────

def _signals_table(s1: List[Dict], s2: List[Dict]) -> Table:
    t = Table(
        title="[bold white]СИГНАЛЫ СЕЙЧАС[/bold white]",
        box=box.ROUNDED, border_style="dim",
        show_header=True, header_style="bold",
    )
    t.add_column("Монета",  style="bold cyan",  min_width=9)
    t.add_column("Тип",     justify="center",   min_width=7)
    t.add_column("Скор",    justify="center",   min_width=5)
    t.add_column("RSI",     justify="right",    min_width=5)
    t.add_column("1H%",     justify="right",    min_width=6)
    t.add_column("RVOL",    justify="right",    min_width=6)
    t.add_column("Паттерн", justify="left",     min_width=16)

    all_sigs = [(s, "S1") for s in s1[:5]] + [(s, "S2") for s in s2[:5]]

    if not all_sigs:
        t.add_row("[dim]нет сигналов[/dim]", *[""] * 6)
        return t

    for sig, stype in all_sigs:
        side_c = "green" if stype == "S1" else "red"
        label  = "[green]▲ ЛОНГ[/green]" if stype == "S1" else "[red]▼ ШОРТ[/red]"
        rsi    = sig.get("rsi") or 0
        rc     = "green" if 55 <= rsi <= 70 else "yellow" if rsi < 80 else "red"
        ch1    = sig.get("change_1h") or 0
        rvol   = sig.get("rvol") or 0
        score  = sig.get("ta_score", 0)
        sc     = "green" if score >= 6 else "yellow" if score >= 4 else "white"
        pat2   = sig.get("candle_pat", "none")
        pat3   = sig.get("multi_pat", "none")
        pat    = pat3 if pat3 != "none" else pat2 if pat2 != "none" else "—"
        t.add_row(
            _coin(sig["symbol"]),
            label,
            f"[{sc}]{score}★[/{sc}]",
            f"[{rc}]{rsi:.0f}[/{rc}]",
            f"[{side_c}]{'+' if ch1>=0 else ''}{ch1:.1f}%[/{side_c}]",
            f"[yellow]{rvol:.1f}x[/yellow]",
            f"[dim]{pat}[/dim]",
        )
    return t


# ── Горячие монеты ────────────────────────────────────────────────────────────

def _hot_table(s3: List[Dict]) -> Table:
    t = Table(
        title="[bold yellow]★ Горячие монеты[/bold yellow]",
        box=box.ROUNDED, border_style="yellow",
        show_header=True, header_style="bold",
    )
    t.add_column("#",      justify="right",   min_width=3)
    t.add_column("Монета", style="bold cyan", min_width=9)
    t.add_column("Цена",   justify="right",   min_width=9)
    t.add_column("24H%",   justify="right",   min_width=7)
    t.add_column("Объём",  justify="right",   min_width=7)

    for i, r in enumerate(s3[:10], 1):
        c    = "green" if r["change_24h"] > 0 else "red"
        sign = "+" if r["change_24h"] > 0 else ""
        t.add_row(
            str(i), _coin(r["symbol"]), _fmt_price(r["price"]),
            f"[{c}]{sign}{r['change_24h']:.1f}%[/{c}]",
            _fmt_vol(r["volume_24h"]),
        )
    return t


# ── История сделок ────────────────────────────────────────────────────────────

def _history_table(bot_state: state_module.BotState) -> Table:
    t = Table(
        title="[bold white]ИСТОРИЯ СДЕЛОК (сегодня)[/bold white]",
        box=box.ROUNDED, border_style="dim",
        show_header=True, header_style="bold",
    )
    t.add_column("Время",   min_width=8)
    t.add_column("Монета",  style="bold cyan", min_width=9)
    t.add_column("Напр.",   justify="center",  min_width=6)
    t.add_column("Вход",    justify="right",   min_width=9)
    t.add_column("PnL",     justify="right",   min_width=12)
    t.add_column("Исход",   justify="center",  min_width=5)

    history = list(reversed(bot_state.trade_history))

    if not history:
        t.add_row("[dim]сделок пока нет[/dim]", *[""] * 5)
        return t

    for trade in history[:10]:
        pnl    = trade.get("pnl", 0)
        reason = trade.get("reason", "?")
        side   = trade.get("side", "?")
        closed = trade.get("closed_at", "")[:19]
        time_s = closed[11:19] if len(closed) >= 19 else "?"
        side_c = "green" if side == "Buy" else "red"
        pnl_c  = "green" if pnl >= 0 else "red"
        res_c  = "green" if reason == "TP" else "red"
        t.add_row(
            time_s,
            _coin(trade.get("symbol", "?")),
            f"[{side_c}]{'▲' if side=='Buy' else '▼'} {side}[/{side_c}]",
            _fmt_price(trade.get("entry_price", 0)),
            f"[{pnl_c}]{_sign_str(pnl)} USDT[/{pnl_c}]",
            f"[{res_c}]{reason}[/{res_c}]",
        )
    return t


# ── Предупреждение о рисках ───────────────────────────────────────────────────

def _risk_panel(bot_state: state_module.BotState, equity: float) -> Panel:
    txt = Text()
    start = bot_state.daily.get("start_equity", equity)
    limit_usdt = start * config.DAILY_LOSS_LIMIT / 100
    current_loss = max(0, start - equity)
    remaining = max(0, limit_usdt - current_loss)

    txt.append("Риск-параметры:\n", style="bold white")
    txt.append(f"  Плечо:          {config.LEVERAGE}x\n")
    txt.append(f"  Риск/сделку:    {config.RISK_PER_TRADE}%\n")
    txt.append(f"  Мах позиций:    {config.MAX_POSITIONS}\n")
    txt.append(f"  Стоп дня:       -{config.DAILY_LOSS_LIMIT}%\n")
    txt.append(f"  Запас стопа:    ${remaining:.2f}\n", style="green" if remaining > 0 else "red")
    txt.append(f"  SL = ATR×{config.SL_ATR_MULT}   TP = SL×{config.TP_RR}\n")

    if bot_state.in_cooldown():
        txt.append(
            f"\n  ⏸ КУЛДАУН: {bot_state.cooldown_remaining_min()} мин\n",
            style="bold yellow"
        )
    return Panel(txt, title="[bold white]Риск[/bold white]", border_style="white", padding=(0, 1))


# ── Главный рендер ────────────────────────────────────────────────────────────

def render(
    bot_state:       state_module.BotState,
    bybit_positions: List[Dict],
    equity:          float,
    s1:              List[Dict],
    s2:              List[Dict],
    s3:              List[Dict],
    tickers_map:     dict,
    console:         Console,
    status:          str   = "",
    iteration:       int   = 0,
) -> None:
    console.print(_header(bot_state, equity, status, iteration))
    console.print()
    console.print(_positions_table(bot_state, bybit_positions, tickers_map))
    console.print()
    console.print(Columns([_signals_table(s1, s2), _hot_table(s3)], expand=True))
    console.print()
    console.print(Columns([_history_table(bot_state), _risk_panel(bot_state, equity)], expand=True))
    console.print()

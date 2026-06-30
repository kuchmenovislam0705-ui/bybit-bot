#!/usr/bin/env python3
"""
╔════════════════════════════════════════════════════════════╗
║  СКАЛЬПИНГ СИГНАЛЫ — XAU / XAG / BTC  [15M]              ║
╠════════════════════════════════════════════════════════════╣
║  ТА (15M+1H+4H) + Макро (DXY/10Y/EUR) + Гео + Корр       ║
║  + Экономический календарь (блокировка CPI/NFP/FOMC)      ║
║  24/7 без ограничений по времени                          ║
╠════════════════════════════════════════════════════════════╣
║  Запуск: python3 main.py  |  Стоп: bash stop_bot.sh       ║
╚════════════════════════════════════════════════════════════╝
"""
import logging
import os
import sys
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table
from rich import box

import calendar_events
import config
import news_monitor
import notifications
import screeners
import signal_tracker
import telegram_bot

_PID_FILE = "/tmp/bybit_bot.pid"


def _kill_old_instance() -> None:
    """Убивает предыдущий экземпляр бота если он запущен."""
    if not os.path.exists(_PID_FILE):
        return
    try:
        old_pid = int(open(_PID_FILE).read().strip())
        if old_pid == os.getpid():
            return
        try:
            os.kill(old_pid, 0)   # проверяем что процесс существует
            os.kill(old_pid, 9)   # убиваем
            time.sleep(1.5)
            print(f"[yellow]Убит старый экземпляр PID={old_pid}[/yellow]")
        except ProcessLookupError:
            pass   # уже не существует
    except (ValueError, OSError):
        pass
    finally:
        try:
            os.remove(_PID_FILE)
        except FileNotFoundError:
            pass


def _write_pid() -> None:
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _clear_pid() -> None:
    try:
        os.remove(_PID_FILE)
    except FileNotFoundError:
        pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    handlers=[logging.FileHandler(config.LOG_FILE, encoding="utf-8")],
)
logger = logging.getLogger("main")
console = Console()


def _banner() -> None:
    tg = "[green]✓[/green]" if config.TELEGRAM_TOKEN else "[red]✗[/red]"
    console.print(
        f"\n[bold yellow]СКАЛЬП-СИГНАЛЫ — 16 инструментов[/bold yellow]\n"
        f"  Таймфрейм:   5M вход + 15M/1H тренд + 4H макро\n"
        f"  Макро:       DXY · EUR/USD · US10Y · Oil · S&P500\n"
        f"  Pivot+VWAP:  ежедневные уровни + внутридневной VWAP\n"
        f"  Дивергенция: RSI бычья/медвежья (+3 к скору)\n"
        f"  Грейд:       🅐≥18  🅑10-17  |  Сессия: London/NY +2, Азия -3\n"
        f"  Telegram:    {tg}  |  Команды: /status /macro /geo /pause /resume\n"
        f"  ADX ≥{config.MIN_ADX}  |  Мин.скор: {config.SIGNAL_MIN_SCORE}  |  SL: ATR×{config.SL_ATR_MULT}  TP: {config.TP_RR}R\n"
    )
    if not config.TELEGRAM_TOKEN:
        console.print("[red]ОШИБКА:[/red] Telegram не настроен!\n")
        sys.exit(1)


def _render(longs, shorts, iteration: int, macro_s: str, cal_s: str, blocked: bool) -> None:
    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.clear()
    console.print(
        f"[bold yellow]СКАЛЬП-СИГНАЛЫ[/bold yellow]  [dim]{now_s}[/dim]  #{iteration}"
    )
    console.print(f"[dim]{macro_s}[/dim]")

    if blocked:
        console.print(f"[bold red]⛔ БЛОКИРОВКА: {cal_s}[/bold red]")
    elif cal_s:
        console.print(f"[yellow]📅 {cal_s}[/yellow]")

    console.print()

    all_sigs = [(s, "🟢 ЛОНГ") for s in longs] + [(s, "🔴 ШОРТ") for s in shorts]
    if all_sigs and not blocked:
        t = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        t.add_column("Символ",   style="cyan", width=10)
        t.add_column("Направл.", width=10)
        t.add_column("Цена",     justify="right", width=13)
        t.add_column("Скор",     justify="right", width=6)
        t.add_column("ADX",      justify="right", width=6)
        t.add_column("RSI",      justify="right", width=6)
        t.add_column("1H",       width=11)
        t.add_column("4H",       width=11)
        for sig, lbl in all_sigs:
            t1 = {1: "⬆ бычий", -1: "⬇ медвежий", 0: "↔ нейтр"}.get(sig.get("trend_1h", 0), "")
            t4 = {1: "⬆ бычий", -1: "⬇ медвежий", 0: "↔ нейтр"}.get(sig.get("trend_4h", 0), "")
            t.add_row(
                sig["symbol"], lbl,
                f"{sig['price']:,.2f}",
                str(sig.get("total_score", "?")),
                f"{sig.get('adx', 0):.1f}",
                f"{sig.get('rsi', 0):.1f}",
                t1, t4,
            )
        console.print(t)
    elif blocked:
        console.print("[dim]Сигналы заблокированы — ждём окончания события[/dim]\n")
    else:
        console.print("[dim]Нет сигналов (порог не достигнут)[/dim]\n")

    console.print(f"[dim]Следующий скан через {config.SCAN_INTERVAL}с...[/dim]")


def _notify_block(ev: dict) -> None:
    """Telegram-предупреждение о предстоящем важном событии."""
    now = datetime.now(timezone.utc)
    dt  = ev["dt_utc"]
    mins_left = int((dt - now).total_seconds() / 60)
    icon = "🔴" if ev["impact"] == "High" else "🟡"
    forecast = f"\nПрогноз: {ev['forecast']}  Пред: {ev.get('previous','?')}" if ev.get("forecast") else ""

    if mins_left > 0:
        text = (
            f"⛔ <b>СТОП — важное событие через {mins_left} мин!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{icon} <b>{ev['title']}</b> ({ev['country']})\n"
            f"Время: {dt.strftime('%H:%M UTC')}{forecast}\n\n"
            f"⚠️ <i>Сигналы заблокированы. Не открывай новые позиции!\n"
            f"Возобновление через 15 мин после выхода данных.</i>"
        )
    else:
        mins_ago = -mins_left
        text = (
            f"⏳ <b>Событие вышло {mins_ago} мин назад — ждём 15 мин</b>\n"
            f"{icon} <b>{ev['title']}</b> ({ev['country']})\n"
            f"Сигналы возобновятся через {15 - mins_ago} мин."
        )

    import notifications as _n
    _n._send(text)


def _notify_upcoming_events() -> None:
    """Уведомление только о HIGH-impact событиях в ближайший час."""
    upcoming = calendar_events.get_upcoming(hours=1.5)
    # Только High impact — Medium не спамим
    high = [ev for ev in upcoming if ev.get("impact") == "High"]
    if not high:
        return
    now = datetime.now(timezone.utc)
    lines = ["🔴 <b>Важные события (1.5ч):</b>"]
    for ev in high:
        mins = int((ev["dt_utc"] - now).total_seconds() / 60)
        fc   = f" | прогноз: {ev['forecast']}" if ev.get("forecast") else ""
        lines.append(f"🔴 <b>{ev['title']}</b> ({ev['country']}) — через {mins}м{fc}")
    import notifications as _n
    _n._send("\n".join(lines))


# Торговые сессии: (название, UTC-час открытия, текст предупреждения)
_SESSIONS = [
    (7,  "🔔 <b>Лондон открывается через 15 мин (07:45 UTC)</b> — ожидай усиленную волатильность XAU/XAG!"),
    (13, "🔔 <b>Нью-Йорк открывается через 15 мин (13:45 UTC)</b> — главная сессия, высокая ликвидность!"),
    (0,  "🌙 <b>Азиатская сессия началась (00:00 UTC)</b> — умеренная волатильность, следи за XAU"),
]
_SESSION_NOTIFIED: set = set()


def _notify_session(utc_hour: int, utc_minute: int) -> None:
    """Уведомление за 15 мин до открытия сессии."""
    for session_hour, msg in [(7, _SESSIONS[0][1]), (13, _SESSIONS[1][1])]:
        if utc_hour == session_hour - 1 and utc_minute >= 45:
            key = f"{session_hour}:{utc_hour}"
            if key not in _SESSION_NOTIFIED:
                import notifications as _n
                _n._send(msg)
                _SESSION_NOTIFIED.add(key)
                # сбрасываем ключ через 2 скана
                if len(_SESSION_NOTIFIED) > 10:
                    _SESSION_NOTIFIED.clear()
    # Азиатская сессия — ровно в 00:00
    if utc_hour == 0 and utc_minute < 20:
        key = "asian:0"
        if key not in _SESSION_NOTIFIED:
            import notifications as _n
            _n._send(_SESSIONS[2][1])
            _SESSION_NOTIFIED.add(key)


def main() -> None:
    _kill_old_instance()   # убиваем старый экземпляр если есть
    _write_pid()
    console.clear()
    _banner()

    # ── Трекер сигналов ───────────────────────────────────────────────────────
    signal_tracker.start()

    # ── Монитор новостей и высказываний лидеров ───────────────────────────────
    news_monitor.start()

    # ── Веб-сервер + TradingView Webhook ─────────────────────────────────────
    if config.WEBAPP_ENABLED:
        try:
            import webapp as _webapp
            _webapp.start(config.WEBAPP_PORT)
            logger.info(
                f"Webhook TradingView: http://localhost:{config.WEBAPP_PORT}/webhook/tradingview"
            )
        except Exception as e:
            logger.warning(f"Webapp не запущен: {e}")

    # ── Запуск Telegram command handler ──────────────────────────────────────
    try:
        _tg_stop = telegram_bot.start()
    except Exception as e:
        logger.warning(f"Telegram commands: {e}")
        _tg_stop = None

    bc = telegram_bot.control  # разделяемый BotControl

    iteration      = 0
    macro_summary  = ""
    last_geo_h     = -1
    last_cal_h     = -1
    last_block_ev  = None
    # Кулдаун: {symbol: datetime последнего сигнала}
    last_signal_ts: dict = {}

    # Загружаем календарь при старте
    try:
        cal_events = calendar_events.get_upcoming(hours=24)
        logger.info(f"Календарь: {len(cal_events)} событий на следующие 24ч")
        _notify_upcoming_events()
    except Exception as e:
        logger.warning(f"Календарь при старте: {e}")

    logger.info(f"Скальпинг-бот запущен. Инструменты: {config.SIGNAL_INSTRUMENTS}")

    while True:
        iteration += 1
        try:
            now_utc    = datetime.now(timezone.utc)
            utc_hour   = now_utc.hour
            utc_minute = now_utc.minute

            # ── Сессионные уведомления ────────────────────────────────────────
            try:
                _notify_session(utc_hour, utc_minute)
            except Exception:
                pass

            # ── Пауза по команде /pause ───────────────────────────────────────
            if bc.paused:
                logger.info(f"#{iteration} ⏸ пауза (команда /pause)")
                _render([], [], iteration, macro_summary, "⏸ ПАУЗА — /resume чтобы продолжить", False)
                time.sleep(config.SCAN_INTERVAL)
                continue

            # ── Экономический календарь ───────────────────────────────────────
            blocked, block_ev = calendar_events.is_blocked()

            # Предупреждение о блокировке (отправляем только 1 раз на событие)
            if blocked and block_ev:
                ev_key = block_ev["dt_utc"].isoformat()
                if last_block_ev != ev_key:
                    _notify_block(block_ev)
                    last_block_ev = ev_key
                    logger.warning(
                        f"БЛОКИРОВКА: {block_ev['title']} ({block_ev['country']}) "
                        f"в {block_ev['dt_utc'].strftime('%H:%M UTC')}"
                    )
            elif not blocked:
                last_block_ev = None  # сброс после снятия блокировки

            # Текст для дашборда о календаре
            if blocked and block_ev:
                mins = int((block_ev["dt_utc"] - now_utc).total_seconds() / 60)
                if mins > 0:
                    cal_status = f"{block_ev['title']} через {mins}м — СТОП"
                else:
                    cal_status = f"{block_ev['title']} — ждём ещё {15+mins}м"
            else:
                # Ближайшее предстоящее событие (для инфо)
                upcoming_2h = calendar_events.get_upcoming(hours=2)
                if upcoming_2h:
                    ev = upcoming_2h[0]
                    mins = int((ev["dt_utc"] - now_utc).total_seconds() / 60)
                    icon = "🔴" if ev["impact"] == "High" else "🟡"
                    cal_status = f"{icon} {ev['title']} через {mins}м"
                else:
                    cal_status = ""

            # ── Скринеры (пропускаем если заблокировано) ─────────────────────
            if blocked:
                longs, shorts = [], []
            else:
                longs, shorts = screeners.run_all()

            # ── Макро-дашборд ─────────────────────────────────────────────────
            try:
                import macro as _macro
                md = _macro.get()
                parts = []
                if "dxy"    in md: parts.append(f"DXY={md['dxy'].get('price') or 0:.2f}({md['dxy'].get('chg_1h') or 0:+.2f}%/1h)")
                if "eurusd" in md: parts.append(f"EUR={md['eurusd'].get('price') or 0:.4f}")
                if "us10y"  in md: parts.append(f"10Y={md['us10y'].get('price') or 0:.2f}%")
                if "oil"    in md: parts.append(f"Oil={md['oil'].get('price') or 0:.1f}")
                macro_summary = "Макро: " + " | ".join(parts) if parts else "Макро: нет данных"
            except Exception:
                macro_summary = "Макро: нет данных"

            # ── Гео-обновление раз в 2 часа ───────────────────────────────────
            if utc_hour % 2 == 0 and utc_hour != last_geo_h:
                try:
                    import geo as _geo
                    gs, gh = _geo.get_geo_score()
                    notifications.on_geo_update(gs, gh)
                    last_geo_h = utc_hour
                except Exception:
                    pass

            # ── Сводка событий раз в час ──────────────────────────────────────
            if utc_hour != last_cal_h:
                try:
                    _notify_upcoming_events()
                    last_cal_h = utc_hour
                except Exception:
                    pass

            # ── Сигналы в Telegram ────────────────────────────────────────────
            sent = 0
            cooldown_h = config.SIGNAL_COOLDOWN_HOURS
            if not blocked:
                for sig in longs + shorts:
                    sym = sig["symbol"]
                    direction = sig.get("direction", "Buy")
                    # Кулдаун независимый для ЛОНГ и ШОРТ одного инструмента
                    cd_key = f"{sym}_{direction}"
                    if cooldown_h > 0 and cd_key in last_signal_ts:
                        age_h = (now_utc - last_signal_ts[cd_key]).total_seconds() / 3600
                        if age_h < cooldown_h:
                            logger.info(
                                f"#{iteration} {sym} {'ЛОНГ' if direction=='Buy' else 'ШОРТ'} кулдаун: {age_h*60:.1f}м"
                            )
                            continue
                    sig["sent_at"] = now_utc.strftime("%H:%M UTC")
                    notifications.send_signal(sig)
                    bc.add_signal(sig)
                    signal_tracker.record(sig)
                    last_signal_ts[cd_key] = now_utc
                    sent += 1
                    logger.info(
                        f"✉ [{sig.get('grade','?')}] {sig['symbol']} "
                        f"{'ЛОНГ' if sig['direction']=='Buy' else 'ШОРТ'} "
                        f"скор={sig.get('total_score')} цена={sig['price']:.2f}"
                    )
                if sent:
                    logger.info(f"Отправлено {sent} сигналов #{iteration}")
                else:
                    logger.info(f"#{iteration}: сигналов нет (мин.скор={config.SIGNAL_MIN_SCORE})")

            # ── Дашборд ───────────────────────────────────────────────────────
            _render(longs, shorts, iteration, macro_summary, cal_status, blocked)

            time.sleep(config.SCAN_INTERVAL)

        except KeyboardInterrupt:
            console.print("\n[yellow]Остановлено.[/yellow]")
            logger.info("Бот остановлен")
            _clear_pid()
            sys.exit(0)

        except Exception as exc:
            err_s = str(exc)
            logger.error(f"Ошибка: {exc}", exc_info=True)
            console.print(f"\n[red]Ошибка:[/red] {exc}")
            time.sleep(60 if "403" in err_s else 30)


if __name__ == "__main__":
    main()

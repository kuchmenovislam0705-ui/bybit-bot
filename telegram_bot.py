"""
Telegram command handler — фоновый поток для обработки команд.
Команды: /status /macro /geo /calendar /signals /pause /resume /help
"""
import logging
import threading
import time
from datetime import datetime, timezone
from typing import List

import requests

import config

logger = logging.getLogger("tg_bot")


class BotControl:
    """Разделяемое состояние между main-циклом и обработчиком команд."""
    def __init__(self):
        self.paused      = False
        self.last_signals: List[dict] = []
        self._lock       = threading.Lock()

    def pause(self):
        with self._lock:
            self.paused = True

    def resume(self):
        with self._lock:
            self.paused = False

    def add_signal(self, sig: dict):
        """Запоминаем последние 10 сигналов для /signals."""
        with self._lock:
            self.last_signals = ([sig] + self.last_signals)[:10]


# Глобальный экземпляр — импортируем в main.py и screeners.py
control = BotControl()


# ── Отправка ─────────────────────────────────────────────────────────────────

def _send(text: str, chat_id: str = None) -> None:
    cid = chat_id or config.TELEGRAM_CHAT_ID
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception as e:
        logger.debug(f"send: {e}")


# ── Хендлеры команд ───────────────────────────────────────────────────────────

def _cmd_status(chat_id: str) -> None:
    try:
        import client
        tickers = {t["symbol"]: t for t in client.get_tickers()}
        lines = ["📊 <b>Текущий рынок</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for sym, em, name in [("XAUUSDT","🥇","Золото"),
                               ("XAGUSDT","🥈","Серебро"),
                               ("BTCUSDT","₿","Биткоин")]:
            t    = tickers.get(sym, {})
            p    = float(t.get("lastPrice",    0))
            chg  = float(t.get("price24hPcnt", 0)) * 100
            icon = "↑" if chg >= 0 else "↓"
            lines.append(f"{em} <b>{name}</b>: {p:,.2f}  {icon}{abs(chg):.2f}%")

        paused_s = "⏸ ПАУЗА" if control.paused else "✅ активен"
        lines += ["", f"Бот: {paused_s}",
                  f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"]
        _send("\n".join(lines), chat_id)
    except Exception as e:
        _send(f"Ошибка: {e}", chat_id)


def _cmd_macro(chat_id: str) -> None:
    try:
        import macro
        md = macro.get()
        if not md:
            _send("Макро данные временно недоступны", chat_id)
            return
        lines = ["💵 <b>Макро данные</b>", "━━━━━━━━━━━━━━━━━━━━"]
        specs = [
            ("dxy",    "DXY (US Dollar)",  "↑DXY = ↓XAU",  ".2f"),
            ("us10y",  "US 10Y Yield",     "↑Yield = ↓XAU", ".2f%"),
            ("eurusd", "EUR/USD",          "↑EUR = ↑XAU",   ".4f"),
            ("oil",    "WTI Oil",          "↑Oil = ↑XAU",   ".2f"),
            ("spx",    "S&P 500",          "risk-on",        ",.0f"),
        ]
        for key, name, corr, fmt in specs:
            if key not in md:
                continue
            v   = md[key]
            p   = v["price"]
            c15 = v["chg_15m"]
            p_s = (f"{p:{fmt}}" if "%" not in fmt else f"{p:.2f}%")
            dir_icon = "↑" if c15 >= 0 else "↓"
            lines.append(f"▸ <b>{name}</b>: {p_s}  {dir_icon}{abs(c15):.3f}%  <i>[{corr}]</i>")
        lines.append(f"\n⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
        _send("\n".join(lines), chat_id)
    except Exception as e:
        _send(f"Ошибка: {e}", chat_id)


def _cmd_geo(chat_id: str) -> None:
    try:
        import geo
        score, headlines = geo.get_geo_score()
        if score > 0.3:   icon = "⚠️ ВЫСОКИЙ риск"
        elif score > 0.1: icon = "↑ умеренный"
        elif score < -0.1: icon = "↓ низкий"
        else:              icon = "↔ нейтральный"
        effect = "ЛОНГ XAU ↑" if score > 0.1 else "ШОРТ XAU ↓" if score < -0.1 else "нейтрально"
        _send(
            f"🌍 <b>Геополитика</b>\n"
            f"Риск: {icon} ({score:+.2f})\n"
            f"Заголовков: {headlines}\n"
            f"Влияние: {effect}\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
            chat_id,
        )
    except Exception as e:
        _send(f"Ошибка: {e}", chat_id)


def _cmd_calendar(chat_id: str) -> None:
    try:
        import calendar_events
        events = calendar_events.get_upcoming(hours=24)
        if not events:
            _send("📅 Важных событий нет на 24ч", chat_id)
            return
        now   = datetime.now(timezone.utc)
        lines = ["📅 <b>Календарь (24ч)</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for ev in events[:8]:
            mins = int((ev["dt_utc"] - now).total_seconds() / 60)
            icon = "🔴" if ev["impact"] == "High" else "🟡"
            fc   = f" | прогноз: {ev['forecast']}" if ev.get("forecast") else ""
            time_s = ev["dt_utc"].strftime("%H:%M UTC")
            lines.append(f"{icon} <b>{ev['title']}</b> ({ev['country']})\n  {time_s} — через {mins}м{fc}")
        _send("\n".join(lines), chat_id)
    except Exception as e:
        _send(f"Ошибка: {e}", chat_id)


def _cmd_signals(chat_id: str) -> None:
    sigs = control.last_signals
    if not sigs:
        _send("📭 Нет последних сигналов", chat_id)
        return
    lines = [f"📋 <b>Последние сигналы</b>"]
    for s in sigs[:5]:
        sym   = s.get("symbol", "?")
        d     = "ЛОНГ" if s.get("direction") == "Buy" else "ШОРТ"
        p     = s.get("price", 0)
        sc    = s.get("total_score", 0)
        grade = s.get("grade", "?")
        ts    = s.get("sent_at", "")
        lines.append(f"[{grade}] {sym} {d}  {p:,.2f}  скор={sc}  {ts}")
    _send("\n".join(lines), chat_id)


def _cmd_stats(chat_id: str) -> None:
    """Статистика производительности бота + адаптивное обучение."""
    try:
        import signal_tracker as st
        s = st.get_stats()

        # Общая статистика
        total = s["total"]
        if total == 0:
            _send(
                "📊 <b>Статистика</b>\n"
                "Ещё нет закрытых сигналов.\n"
                f"В ожидании: {s['pending']} сигналов (закроются через ≤4ч)",
                chat_id,
            )
            return

        wr_all = s["win_rate"] * 100
        wr_rec = s["recent_wr"] * 100
        adp    = s["adaptive"]

        import config as cfg
        adj    = adp - cfg.SIGNAL_MIN_SCORE
        adj_s  = f"+{adj}" if adj > 0 else ("✅ базовый" if adj == 0 else str(adj))

        lines = [
            "📊 <b>Производительность бота</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Всего закрыто: <b>{total}</b>  (✅{s['wins']}  ❌{s['losses']})",
            f"Win rate всё время: <b>{wr_all:.0f}%</b>",
            f"Win rate последние {s['recent_n']}: <b>{wr_rec:.0f}%</b>",
            f"",
            f"🧠 <b>Адаптивный порог</b>: {adp} ({adj_s})",
            f"В ожидании исхода: {s['pending']} сигналов",
        ]

        # По сессии
        if s["by_sess"]:
            lines.append("\n📅 <b>По сессии:</b>")
            for sess, (w, t) in sorted(s["by_sess"].items(), key=lambda x: -x[1][1]):
                wr = w / t * 100 if t else 0
                bar = "🟢" if wr >= 55 else ("🟡" if wr >= 40 else "🔴")
                lines.append(f"  {bar} {sess}: {w}/{t}  ({wr:.0f}%)")

        # По символу
        if s["by_sym"]:
            lines.append("\n💹 <b>По инструменту:</b>")
            for sym, (w, t) in sorted(s["by_sym"].items(), key=lambda x: -x[1][1]):
                wr = w / t * 100 if t else 0
                bar = "🟢" if wr >= 55 else ("🟡" if wr >= 40 else "🔴")
                lines.append(f"  {bar} {sym}: {w}/{t}  ({wr:.0f}%)")

        # Последние 5 исходов
        if s["last5"]:
            lines.append("\n🕐 <b>Последние результаты:</b>")
            lines.extend(f"  {x}" for x in s["last5"])

        _send("\n".join(lines), chat_id)
    except Exception as e:
        _send(f"Ошибка: {e}", chat_id)


def _cmd_news(chat_id: str) -> None:
    """Последние новостные алерты с импактом на рынок."""
    try:
        import news_monitor as nm
        alerts = nm.get_latest_alerts(5)
        sentiment = nm.get_news_sentiment()

        if not alerts:
            sent_s = f"{sentiment:+.2f}" if sentiment else "нет данных"
            _send(
                f"📰 <b>Новостной монитор</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Ещё нет сохранённых алертов.\n"
                f"Сентимент XAU: {sent_s}\n"
                f"Сканирование каждые 10 мин — жди алертов.",
                chat_id,
            )
            return

        sent_icon = "🟢" if sentiment > 0.1 else ("🔴" if sentiment < -0.1 else "⚪")
        lines = [
            "📰 <b>Последние новостные алерты</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"{sent_icon} Сентимент XAU: {sentiment:+.2f}",
            "",
        ]

        for a in alerts:
            imp    = a["impact"]
            ts     = str(a["ts"])[:16].replace("T", " ")
            pers_s = " · ".join(a["persons"][:2]) if a["persons"] else ""
            xau_s  = f"{'↑' if imp['xau']>0 else '↓'}{abs(imp['xau'])}" if imp["xau"] else "→"
            btc_s  = f"{'↑' if imp['btc']>0 else '↓'}{abs(imp['btc'])}" if imp["btc"] else "→"
            lines.append(
                f"📌 <b>{a['title'][:100]}</b>\n"
                f"   {pers_s}  |  XAU {xau_s}  BTC {btc_s}  [{ts}]"
            )

        _send("\n".join(lines), chat_id)
    except Exception as e:
        _send(f"Ошибка: {e}", chat_id)


def _cmd_learn(chat_id: str) -> None:
    """Текущее состояние адаптивного обучения."""
    try:
        import signal_tracker as st
        import config as cfg

        adp    = st.get_adaptive_score()
        adj    = adp - cfg.SIGNAL_MIN_SCORE
        sigs   = st._load()
        resolved = [s for s in sigs if s.get("outcome") in ("win", "loss")]
        recent   = resolved[-8:]

        lines = [
            "🧠 <b>Адаптивное обучение</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Базовый мин.скор (config): {cfg.SIGNAL_MIN_SCORE}",
            f"ADX фильтр: ≥{cfg.MIN_ADX}",
            f"Кулдаун: {cfg.SIGNAL_COOLDOWN_HOURS}ч",
        ]

        if len(recent) < 5:
            lines.append(f"\n⏳ Данных мало ({len(recent)}/5) — нужно ≥5 закрытых сигналов")
            lines.append(f"Текущий порог: <b>{adp}</b> (базовый)")
        else:
            wr = sum(1 for s in recent if s["outcome"] == "win") / len(recent) * 100
            icon = "🟢" if wr >= 60 else ("🟡" if wr >= 40 else "🔴")
            lines.append(f"\n{icon} Win rate последних {len(recent)}: <b>{wr:.0f}%</b>")
            if adj > 0:
                lines.append(f"📈 Порог повышен до <b>{adp}</b> (+{adj}) — плохая серия")
            else:
                lines.append(f"✅ Порог нормальный: <b>{adp}</b> — работаем хорошо")

            # Что можно улучшить
            if wr < 40:
                lines.append("\n⚠️ <b>Рекомендации:</b>")
                lines.append("• Торгуй только Grade 🅐 сигналы (≥18)")
                lines.append("• Избегай периоды без сильного тренда")
                lines.append("• Проверяй дневной тренд перед входом")
            elif wr >= 60:
                lines.append("\n💪 <b>Отличная серия</b> — стратегия работает")

        lines.append(f"\n⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
        _send("\n".join(lines), chat_id)
    except Exception as e:
        _send(f"Ошибка: {e}", chat_id)


def _handle(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    text    = (msg.get("text") or "").strip()
    chat_id = str(msg["chat"]["id"])

    if chat_id != str(config.TELEGRAM_CHAT_ID):
        _send("⛔ Нет доступа", chat_id)
        return

    cmd = text.split()[0].lower().split("@")[0] if text else ""

    if cmd == "/pause":
        control.pause()
        _send("⏸ <b>Сигналы приостановлены.</b>\n/resume — возобновить", chat_id)

    elif cmd == "/resume":
        control.resume()
        _send("▶️ <b>Сигналы возобновлены.</b>", chat_id)

    elif cmd == "/status":
        _cmd_status(chat_id)

    elif cmd == "/macro":
        _cmd_macro(chat_id)

    elif cmd == "/geo":
        _cmd_geo(chat_id)

    elif cmd == "/calendar":
        _cmd_calendar(chat_id)

    elif cmd == "/signals":
        _cmd_signals(chat_id)

    elif cmd == "/stats":
        _cmd_stats(chat_id)

    elif cmd == "/news":
        _cmd_news(chat_id)

    elif cmd == "/learn":
        _cmd_learn(chat_id)

    elif cmd in ("/help", "/start"):
        _send(
            "🤖 <b>Скальпинг-сигналы XAU/XAG/BTC</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "/status    — цены XAU/XAG/BTC прямо сейчас\n"
            "/macro     — DXY · EUR/USD · US10Y · Oil · SPX\n"
            "/geo       — геополитический скор\n"
            "/calendar  — ближайшие события (24ч)\n"
            "/signals   — последние 5 сигналов\n"
            "/stats     — 📊 WIN rate, статистика по сессиям\n"
            "/news      — 📰 последние рыночные новости и алерты\n"
            "/learn     — 🧠 адаптивное обучение, текущий порог\n"
            "/pause     — ⏸ пауза сигналов\n"
            "/resume    — ▶️ возобновить сигналы\n"
            "/help      — эта справка",
            chat_id,
        )


# ── Polling thread ────────────────────────────────────────────────────────────

def _poll(stop_event: threading.Event) -> None:
    offset = 0
    while not stop_event.is_set():
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 25,
                        "allowed_updates": ["message"]},
                timeout=30,
            )
            if r.ok:
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    try:
                        _handle(upd)
                    except Exception as e:
                        logger.debug(f"handle: {e}")
        except Exception as e:
            logger.debug(f"poll: {e}")
            time.sleep(5)


def start() -> threading.Event:
    """Запускает фоновый поток обработки команд. Возвращает stop_event."""
    stop_event = threading.Event()
    t = threading.Thread(
        target=_poll, args=(stop_event,),
        daemon=True, name="tg-commands"
    )
    t.start()
    logger.info("Telegram commands: /status /macro /geo /calendar /signals /stats /news /learn /pause /resume")
    return stop_event

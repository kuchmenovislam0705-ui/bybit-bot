"""
Экономический календарь для gold-скальпинга.
Источник: Forex Factory публичный API (бесплатно, без ключа).

Логика:
  - За 30 мин до HIGH/MEDIUM события → блокируем сигналы + Telegram предупреждение
  - Через 15 мин после события → возобновляем сигналы
  - События USD/EUR/GBP/CNY — влияют на XAU напрямую
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import requests

logger = logging.getLogger("calendar")

_URL_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_URL_NEXTWEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

_CACHE_TTL = 3600   # обновляем список событий раз в час
_cache: Dict = {"ts": 0.0, "events": []}

# Валюты, события которых влияют на золото
_GOLD_CURRENCIES = {"USD", "EUR", "GBP", "CNY", "JPY"}

# Только HIGH и MEDIUM impact события
_IMPORTANT_IMPACTS = {"High", "Medium"}

# Ключевые события, которые двигают gold на 0.5%+
_CRITICAL_KEYWORDS = [
    "CPI", "PCE", "Inflation",
    "NFP", "Non-Farm", "Employment", "Unemployment", "Jobless",
    "FOMC", "Fed", "Federal Reserve", "Interest Rate",
    "GDP", "Retail Sales",
    "Powell", "Lagarde", "ECB",
    "PMI", "ISM",
    "PPI", "Producer Price",
]

# Блокировка сигналов: за сколько минут до события
_BLOCK_BEFORE_MIN  = 30
# Возобновление сигналов: через сколько минут после события
_RESUME_AFTER_MIN  = 15


def _parse_events(raw: list) -> List[Dict]:
    """Парсит JSON в список событий с datetime UTC."""
    events = []
    for item in raw:
        try:
            impact  = item.get("impact", "Low")
            country = item.get("country", "")
            title   = item.get("title", "")
            date_s  = item.get("date", "")

            if impact not in _IMPORTANT_IMPACTS:
                continue
            if country not in _GOLD_CURRENCIES:
                continue

            # Парсим дату (формат ISO с timezone offset)
            # "2026-06-22T08:30:00-04:00"
            from datetime import datetime
            dt = datetime.fromisoformat(date_s).astimezone(timezone.utc)

            events.append({
                "title":    title,
                "country":  country,
                "impact":   impact,
                "dt_utc":   dt,
                "forecast": item.get("forecast", ""),
                "previous": item.get("previous", ""),
            })
        except Exception:
            continue
    return sorted(events, key=lambda x: x["dt_utc"])


def _load_events() -> List[Dict]:
    """Загружает события текущей + следующей недели."""
    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL and _cache["events"]:
        return _cache["events"]

    all_raw = []
    for url in [_URL_THISWEEK, _URL_NEXTWEEK]:
        try:
            r = requests.get(url, timeout=8,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.ok:
                all_raw.extend(r.json())
        except Exception as e:
            logger.debug(f"Calendar fetch {url}: {e}")

    events = _parse_events(all_raw)
    _cache["ts"]     = now
    _cache["events"] = events

    if events:
        logger.info(f"Календарь загружен: {len(events)} событий (HIGH/MEDIUM, USD/EUR/GBP/CNY/JPY)")
    else:
        logger.warning("Календарь: не удалось загрузить события")

    return events


def get_upcoming(hours: float = 2.0) -> List[Dict]:
    """Возвращает события в ближайшие N часов."""
    events = _load_events()
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    return [e for e in events if now <= e["dt_utc"] <= cutoff]


def is_blocked() -> tuple[bool, Optional[Dict]]:
    """
    Возвращает (заблокировано, событие_или_None).
    Блокировка: за 30 мин до события ИЛИ через 15 мин после.
    """
    events = _load_events()
    now    = datetime.now(timezone.utc)

    for ev in events:
        dt    = ev["dt_utc"]
        before = (dt - now).total_seconds() / 60
        after  = (now - dt).total_seconds() / 60

        # За X минут до события
        if 0 <= before <= _BLOCK_BEFORE_MIN:
            return True, ev

        # В течение X минут после события
        if 0 <= after <= _RESUME_AFTER_MIN:
            return True, ev

    return False, None


def format_event(ev: Dict, now_utc: Optional[datetime] = None) -> str:
    """Форматирует событие для Telegram."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    dt     = ev["dt_utc"]
    mins   = int((dt - now_utc).total_seconds() / 60)
    impact_icon = "🔴" if ev["impact"] == "High" else "🟡"

    time_str = dt.strftime("%H:%M UTC")
    if mins > 0:
        when = f"через {mins} мин ({time_str})"
    else:
        when = f"{-mins} мин назад ({time_str})"

    parts = [f"{impact_icon} <b>{ev['title']}</b> — {ev['country']}"]
    if ev.get("forecast"):
        parts.append(f"Прогноз: {ev['forecast']}  Пред: {ev.get('previous', '?')}")
    parts.append(when)
    return "\n".join(parts)


def format_upcoming_summary() -> str:
    """Краткое резюме предстоящих событий для дашборда."""
    upcoming = get_upcoming(hours=4)
    if not upcoming:
        return "Важных событий нет (4ч)"

    now = datetime.now(timezone.utc)
    lines = []
    for ev in upcoming[:5]:
        mins = int((ev["dt_utc"] - now).total_seconds() / 60)
        icon = "🔴" if ev["impact"] == "High" else "🟡"
        lines.append(f"{icon} {ev['title']} ({ev['country']}) — через {mins}м")
    return "\n".join(lines)

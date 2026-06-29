"""Геополитический анализ: парсинг бесплатных RSS-лент для оценки риска."""
import logging
import time
import xml.etree.ElementTree as ET
from typing import Dict

import requests

logger = logging.getLogger("geo")

# ── Источники новостей (бесплатные RSS, без API-ключа) ────────────────────────
_FEEDS = [
    # Поиск по теме "gold price geopolitics"
    "https://news.google.com/rss/search?q=gold+price+geopolitics&hl=en-US&gl=US&ceid=US:en",
    # Поиск по теме "oil sanctions war"
    "https://news.google.com/rss/search?q=oil+sanctions+war+ceasefire&hl=en-US&gl=US&ceid=US:en",
    # Мировые новости BBC
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    # The Guardian — мировая политика
    "https://www.theguardian.com/world/rss",
]

# ── Ключевые слова → БЫЧИЙ сигнал для золота/серебра ─────────────────────────
# (геополитическая напряжённость = рост золота = "страх")
_BULLISH = [
    "war", "attack", "military strike", "invasion", "conflict", "escalat",
    "sanction", "embargo", "nuclear", "terror", "missile", "explosion",
    "crisis", "recession", "inflation surge", "stagflation", "bank failure",
    "de-dollarization", "central bank gold", "gold reserve", "currency crisis",
    "geopolit", "tension", "threat", "coup", "civil war", "blockade",
]

# ── Ключевые слова → МЕДВЕЖИЙ сигнал для золота/серебра ──────────────────────
# (снижение напряжённости = падение золота = "риск-он")
_BEARISH = [
    "ceasefire", "peace deal", "peace agreement", "truce", "withdrawal",
    "diplomatic", "negotiation", "agreement signed", "de-escalat",
    "rate hike", "interest rate hike", "hawkish fed", "strong dollar",
    "risk-on", "economic growth", "record high gdp", "surplus",
    "oversupply", "inventory build",
]

# Кеш чтобы не спамить запросами каждые 15 секунд
_cache: Dict = {"timestamp": 0.0, "score": 0.0, "headline_count": 0}
_CACHE_TTL = 1800  # обновляем геоскор каждые 30 минут


def _fetch(url: str, timeout: int = 10) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; GeoBot/1.0)"}
        r = requests.get(url, timeout=timeout, headers=headers)
        r.raise_for_status()
        return r.text
    except Exception:
        return ""


def _parse_items(xml_text: str) -> list[str]:
    """Извлекает заголовки и описания из RSS XML."""
    texts = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item")[:25]:
            title = item.findtext("title") or ""
            desc  = item.findtext("description") or ""
            texts.append(f"{title} {desc}")
    except ET.ParseError:
        pass
    return texts


def _score_texts(texts: list[str]) -> float:
    """Вычисляет скор от -1.0 до +1.0."""
    if not texts:
        return 0.0
    combined = " ".join(texts).lower()
    bull = sum(1 for kw in _BULLISH  if kw in combined)
    bear = sum(1 for kw in _BEARISH if kw in combined)
    total = bull + bear
    if total == 0:
        return 0.0
    raw = (bull - bear) / max(total, 1)
    # Нормализуем чтобы не было экстремальных значений
    return max(-1.0, min(1.0, raw))


def get_geo_score() -> tuple[float, int]:
    """
    Возвращает (геополитический_скор, кол-во_заголовков).

    Скор:
     +1.0 = высокий геополитический риск → золото растёт (предпочитаем ЛОНГ XAU/XAG)
     -1.0 = спокойно, риск-он → золото падает (предпочитаем ШОРТ XAU/XAG)
      0.0 = нейтрально

    Кешируется на 30 минут.
    """
    now = time.time()
    if now - _cache["timestamp"] < _CACHE_TTL:
        return _cache["score"], _cache["headline_count"]

    all_texts: list[str] = []
    for url in _FEEDS:
        xml_text = _fetch(url)
        if xml_text:
            all_texts.extend(_parse_items(xml_text))

    score = _score_texts(all_texts)
    _cache["timestamp"]      = now
    _cache["score"]          = score
    _cache["headline_count"] = len(all_texts)

    direction = "↑ БЫЧИЙ" if score > 0.1 else "↓ МЕДВЕЖИЙ" if score < -0.1 else "↔ нейтральный"
    logger.info(
        f"Геополитика: скор={score:+.2f} {direction}  "
        f"заголовков={len(all_texts)}  "
        f"(обновление через {_CACHE_TTL//60} мин)"
    )
    return score, len(all_texts)

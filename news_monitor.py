"""
Монитор рыночных новостей и высказываний лидеров.

Отслеживает:
  — Высказывания президентов, глав ЦБ, министров (Трамп, Пауэлл, Путин, Си и др.)
  — Геополитику: войны, санкции, эскалации, перемирия
  — Монетарную политику: ставки ФРС/ЕЦБ, QE, тейперинг
  — Торговые войны: тарифы, пошлины, ограничения
  — Реакцию доллара DXY
  — ОПЕК, нефть, энергетику
  — Инфляцию, рецессию, кризисы

Алерты отправляются в Telegram при обнаружении значимого события.
Сканирует 12 RSS-лент каждые 10 минут.
"""
import hashlib
import json
import logging
import os
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

import config

logger = logging.getLogger("news")

_SEEN_FILE = os.path.join(os.path.dirname(__file__), "news_seen.json")
_ALERT_LOCK = threading.Lock()
_LAST_ALERTS: List[Dict] = []      # кеш последних алертов (для /news команды)
_MAX_ALERTS_PER_HOUR = 6           # антиспам
_alert_times: List[float] = []


# ── RSS-источники ──────────────────────────────────────────────────────────────
# Отсортированы по актуальности (проверено: ForexLive ~7 мин, SeekingAlpha ~9 мин)

_FEEDS = [
    # ── ФОРЕКС / ЦБ / Ставки (самые свежие, ~7 мин) ─────────────────────────
    "https://www.forexlive.com/feed",              # главная лента — всё важное
    "https://www.forexlive.com/feed/forex",        # только форекс движения
    "https://www.forexlive.com/feed/centralbank",  # ФРС, ЕЦБ, BoJ, BoE, RBA
    # ── РЫНКИ США (9-23 мин) ─────────────────────────────────────────────────
    "https://seekingalpha.com/market_currents.xml", # market currents — быстро
    "https://feeds.marketwatch.com/marketwatch/topstories/",  # MarketWatch
    # ── ФИНАНСЫ / ЭКОНОМИКА (61 мин) ─────────────────────────────────────────
    "https://feeds.bloomberg.com/markets/news.rss",     # Bloomberg Markets
    "https://feeds.bloomberg.com/technology/news.rss",  # Bloomberg Tech
    "https://feeds.bbci.co.uk/news/business/rss.xml",   # BBC Business
    "https://www.theguardian.com/business/rss",         # Guardian Business
    # ── ГЕОПОЛИТИКА / МИР ────────────────────────────────────────────────────
    "https://feeds.bbci.co.uk/news/world/rss.xml",      # BBC World
    "https://www.theguardian.com/world/rss",            # Guardian World
]


# ── Ключевые персоны ───────────────────────────────────────────────────────────
# Формат: "keyword" → (emoji + имя, роль, категория)

_PERSONS: Dict[str, Tuple[str, str, str]] = {
    # США
    "trump":           ("🇺🇸 Трамп",           "President USA",      "trade_policy"),
    "donald trump":    ("🇺🇸 Трамп",           "President USA",      "trade_policy"),
    "powell":          ("🏦 Пауэлл",           "Глава ФРС",          "monetary"),
    "jerome powell":   ("🏦 Пауэлл",           "Глава ФРС",          "monetary"),
    "yellen":          ("🇺🇸 Йеллен",          "Минфин США",         "fiscal"),
    "janet yellen":    ("🇺🇸 Йеллен",          "Минфин США",         "fiscal"),
    "fed":             ("🏦 ФРС",              "Federal Reserve",    "monetary"),
    "federal reserve": ("🏦 ФРС",              "Federal Reserve",    "monetary"),
    "fomc":            ("🏦 FOMC",             "Fed Committee",      "monetary"),
    # Европа
    "lagarde":         ("🏦 Лагард",           "Глава ЕЦБ",          "monetary"),
    "ecb":             ("🏦 ЕЦБ",              "European Central Bank", "monetary"),
    "macron":          ("🇫🇷 Макрон",          "Президент Франции",  "geopolitics"),
    "scholz":          ("🇩🇪 Шольц",           "Канцлер Германии",   "geopolitics"),
    "bailey":          ("🏦 Бейли",            "Глава BoE",          "monetary"),
    "bank of england": ("🏦 BoE",              "Bank of England",    "monetary"),
    # Россия
    "putin":           ("🇷🇺 Путин",           "Президент России",   "geopolitics"),
    "vladimir putin":  ("🇷🇺 Путин",           "Президент России",   "geopolitics"),
    "kremlin":         ("🇷🇺 Кремль",          "Russia Gov",         "geopolitics"),
    "lavrov":          ("🇷🇺 Лавров",          "МИД России",         "diplomacy"),
    # Китай
    "xi jinping":      ("🇨🇳 Си Цзиньпин",    "Президент КНР",      "china"),
    "xi":              ("🇨🇳 Си",              "Президент КНР",      "china"),
    "pboc":            ("🏦 PBOC",             "ЦБ Китая",           "monetary"),
    "li qiang":        ("🇨🇳 Ли Цян",          "Премьер КНР",        "china"),
    # Ближний Восток
    "netanyahu":       ("🇮🇱 Нетаньяху",       "Премьер Израиля",    "middle_east"),
    "hamas":           ("🔴 ХАМАС",            "Gaza",               "middle_east"),
    "hezbollah":       ("🔴 Хезболла",         "Lebanon",            "middle_east"),
    "mbs":             ("🇸🇦 МБС",             "Наследный принц КСА","oil"),
    "saudi":           ("🇸🇦 Саудовская Аравия","Saudi Arabia",       "oil"),
    "opec":            ("🛢️ ОПЕК",             "Oil Cartel",         "oil"),
    # Япония
    "boj":             ("🏦 BoJ",              "Банк Японии",        "monetary"),
    "ueda":            ("🏦 Уэда",             "Глава BoJ",          "monetary"),
    # Институты
    "imf":             ("💰 МВФ",              "IMF",                "global"),
    "world bank":      ("💰 Мировой Банк",     "World Bank",         "global"),
    "bis":             ("🏦 BIS",              "Bank for Intl Settlements", "global"),
}


# ── Правила импакта на рынок ───────────────────────────────────────────────────
# Формат: keyword → {xau, xag, btc, dxy, duration_min, text}
# Значения: +3 сильный бычий, +2 бычий, +1 слабый, 0 нейтр, -1/-2/-3 медвежий

_IMPACT_RULES: Dict[str, Dict] = {
    # ── Монетарная политика (очень высокий импакт) ──────────────────────────────
    "rate hike":         {"xau": -3, "xag": -2, "btc": -2, "dxy": +3, "min": 120, "text": "Повышение ставки ФРС — ↑DXY → ↓↓XAU"},
    "rate cut":          {"xau": +3, "xag": +2, "btc": +2, "dxy": -3, "min": 120, "text": "Снижение ставки ФРС — ↓DXY → ↑↑XAU"},
    "interest rate":     {"xau": -1, "xag": 0,  "btc": -1, "dxy": +1, "min": 60,  "text": "Ставка: смотри направление"},
    "hawkish":           {"xau": -2, "xag": -1, "btc": -1, "dxy": +2, "min": 60,  "text": "Ястребиный тон → ↓XAU"},
    "dovish":            {"xau": +2, "xag": +1, "btc": +1, "dxy": -2, "min": 60,  "text": "Голубиный тон → ↑XAU"},
    "quantitative easing":{"xau": +3,"xag": +2, "btc": +3, "dxy": -3, "min": 180, "text": "QE — печатание денег → ↑↑XAU ↑BTC"},
    "qe":                {"xau": +3, "xag": +2, "btc": +3, "dxy": -3, "min": 180, "text": "QE → ↑↑XAU ↑BTC"},
    "tapering":          {"xau": -2, "xag": -1, "btc": -2, "dxy": +2, "min": 90,  "text": "Сворачивание QE → ↓XAU"},
    "pause":             {"xau": +1, "xag": +1, "btc": +1, "dxy": -1, "min": 45,  "text": "Пауза ФРС → умеренный ↑XAU"},
    "pivot":             {"xau": +2, "xag": +1, "btc": +2, "dxy": -2, "min": 90,  "text": "ФРС разворот → ↑XAU"},
    "emergency":         {"xau": +2, "xag": +1, "btc": -1, "dxy": -1, "min": 60,  "text": "Экстренное заседание — волатильность"},
    # ── Торговые войны и тарифы ─────────────────────────────────────────────────
    "tariff":            {"xau": +2, "xag": +1, "btc": -1, "dxy": +1, "min": 60,  "text": "Тарифы — неопределённость → ↑XAU"},
    "tariffs":           {"xau": +2, "xag": +1, "btc": -1, "dxy": +1, "min": 60,  "text": "Тарифы → ↑XAU риск-офф"},
    "trade war":         {"xau": +2, "xag": +1, "btc": -2, "dxy": +1, "min": 90,  "text": "Торговая война → ↑XAU"},
    "import ban":        {"xau": +1, "xag": +1, "btc": -1, "dxy": +1, "min": 45,  "text": "Импортный запрет — риски"},
    "trade deal":        {"xau": -1, "xag": -1, "btc": +1, "dxy": -1, "min": 45,  "text": "Торговая сделка → риск-он ↓XAU"},
    # ── Санкции и геополитика ───────────────────────────────────────────────────
    "sanction":          {"xau": +2, "xag": +1, "btc": 0,  "dxy": +1, "min": 60,  "text": "Санкции → геориск → ↑XAU"},
    "sanctions":         {"xau": +2, "xag": +1, "btc": 0,  "dxy": +1, "min": 60,  "text": "Санкции → ↑XAU"},
    "embargo":           {"xau": +2, "xag": +1, "btc": 0,  "dxy": 0,  "min": 60,  "text": "Эмбарго → риск → ↑XAU"},
    "military strike":   {"xau": +3, "xag": +2, "btc": -1, "dxy": +2, "min": 30,  "text": "Военный удар → резкий ↑↑XAU"},
    "airstrike":         {"xau": +3, "xag": +2, "btc": -1, "dxy": +2, "min": 30,  "text": "Авиаудар → ↑↑XAU"},
    "invasion":          {"xau": +3, "xag": +2, "btc": -2, "dxy": +2, "min": 60,  "text": "Вторжение → ↑↑↑XAU"},
    "war":               {"xau": +2, "xag": +2, "btc": -1, "dxy": +1, "min": 60,  "text": "Война → ↑XAU"},
    "nuclear":           {"xau": +3, "xag": +2, "btc": -2, "dxy": +2, "min": 60,  "text": "Ядерная угроза → ↑↑↑XAU"},
    "missile":           {"xau": +2, "xag": +1, "btc": -1, "dxy": +1, "min": 30,  "text": "Ракетный удар → ↑XAU"},
    "attack":            {"xau": +2, "xag": +1, "btc": -1, "dxy": +1, "min": 30,  "text": "Атака → риск → ↑XAU"},
    "terror":            {"xau": +2, "xag": +1, "btc": -1, "dxy": +1, "min": 30,  "text": "Теракт → ↑XAU"},
    "coup":              {"xau": +2, "xag": +1, "btc": -1, "dxy": 0,  "min": 60,  "text": "Переворот — нестабильность"},
    # ── Деэскалация (медвежий для XAU) ─────────────────────────────────────────
    "ceasefire":         {"xau": -2, "xag": -1, "btc": +1, "dxy": -1, "min": 45,  "text": "Перемирие → риск-он → ↓XAU"},
    "peace deal":        {"xau": -2, "xag": -1, "btc": +1, "dxy": -1, "min": 45,  "text": "Мирное соглашение → ↓XAU"},
    "peace talks":       {"xau": -1, "xag": -1, "btc": +1, "dxy": -1, "min": 30,  "text": "Мирные переговоры → умеренно ↓XAU"},
    "de-escalat":        {"xau": -2, "xag": -1, "btc": +1, "dxy": -1, "min": 45,  "text": "Деэскалация → ↓XAU"},
    "withdrawal":        {"xau": -1, "xag": -1, "btc": 0,  "dxy": 0,  "min": 30,  "text": "Вывод войск → риск снижается"},
    # ── Доллар DXY ─────────────────────────────────────────────────────────────
    "dollar weakness":   {"xau": +2, "xag": +1, "btc": +1, "dxy": -2, "min": 60,  "text": "Слабый доллар → ↑XAU"},
    "dollar strength":   {"xau": -2, "xag": -1, "btc": -1, "dxy": +2, "min": 60,  "text": "Сильный доллар → ↓XAU"},
    "dollar rally":      {"xau": -2, "xag": -1, "btc": -1, "dxy": +2, "min": 45,  "text": "Ралли доллара → ↓XAU"},
    "dollar crash":      {"xau": +3, "xag": +2, "btc": +2, "dxy": -3, "min": 90,  "text": "Обвал доллара → ↑↑XAU"},
    "dedollarization":   {"xau": +3, "xag": +2, "btc": +2, "dxy": -3, "min": 180, "text": "Дедолларизация → ↑↑XAU долгосрочно"},
    "de-dollarization":  {"xau": +3, "xag": +2, "btc": +2, "dxy": -3, "min": 180, "text": "Дедолларизация → ↑↑XAU"},
    # ── Инфляция ────────────────────────────────────────────────────────────────
    "inflation surge":   {"xau": +2, "xag": +1, "btc": 0,  "dxy": +1, "min": 60,  "text": "Рост инфляции → ↑XAU как хедж"},
    "inflation high":    {"xau": +1, "xag": +1, "btc": 0,  "dxy": +1, "min": 45,  "text": "Высокая инфляция → ↑XAU"},
    "stagflation":       {"xau": +3, "xag": +2, "btc": -2, "dxy": -1, "min": 120, "text": "Стагфляция → ↑↑↑XAU классический сценарий"},
    "hyperinflation":    {"xau": +3, "xag": +3, "btc": +1, "dxy": -3, "min": 180, "text": "Гиперинфляция → ↑↑↑XAU"},
    "deflation":         {"xau": -1, "xag": -1, "btc": -2, "dxy": +2, "min": 90,  "text": "Дефляция → ↓XAU (снижение инфл.ожиданий)"},
    # ── Кризис и рецессия ───────────────────────────────────────────────────────
    "recession":         {"xau": +1, "xag": 0,  "btc": -2, "dxy": +1, "min": 90,  "text": "Рецессия → ↑XAU (безопасная гавань)"},
    "default":           {"xau": +2, "xag": +1, "btc": -2, "dxy": -2, "min": 90,  "text": "Дефолт → ↑XAU паника"},
    "debt ceiling":      {"xau": +1, "xag": +1, "btc": -1, "dxy": -1, "min": 60,  "text": "Потолок долга США → ↑XAU неопределённость"},
    "bank failure":      {"xau": +2, "xag": +1, "btc": +1, "dxy": -1, "min": 90,  "text": "Банковский кризис → ↑XAU ↑BTC"},
    "banking crisis":    {"xau": +2, "xag": +1, "btc": +1, "dxy": -1, "min": 90,  "text": "Банковский кризис → ↑XAU"},
    "stock market crash":{"xau": +1, "xag": 0,  "btc": -3, "dxy": +1, "min": 60,  "text": "Обвал рынков → ↑XAU ↓↓BTC"},
    "market crash":      {"xau": +1, "xag": 0,  "btc": -3, "dxy": +1, "min": 60,  "text": "Обвал рынков → ↑XAU ↓↓BTC"},
    # ── Нефть и энергетика (через инфляцию) ────────────────────────────────────
    "oil production cut":{"xau": +1, "xag": 0,  "btc": 0,  "dxy": 0,  "min": 60,  "text": "Сокращение добычи нефти → ↑инфляция"},
    "opec cut":          {"xau": +1, "xag": 0,  "btc": 0,  "dxy": 0,  "min": 60,  "text": "ОПЕК сокращает добычу → ↑нефть → ↑инфляция"},
    "energy crisis":     {"xau": +2, "xag": +1, "btc": -1, "dxy": 0,  "min": 90,  "text": "Энергокризис → ↑инфляция → ↑XAU"},
    # ── Китай ───────────────────────────────────────────────────────────────────
    "china stimulus":    {"xau": +1, "xag": +1, "btc": +1, "dxy": -1, "min": 60,  "text": "Китайский стимул → риск-он → ↑XAG"},
    "yuan devaluation":  {"xau": +2, "xag": +1, "btc": +1, "dxy": +1, "min": 60,  "text": "Девальвация юаня → ↑DXY → двоякий для XAU"},
    "china slowdown":    {"xau": -1, "xag": -1, "btc": -1, "dxy": +1, "min": 60,  "text": "Замедление КНР → ↓спрос XAG (промышленность)"},
    # ── Золото напрямую ─────────────────────────────────────────────────────────
    "central bank gold": {"xau": +2, "xag": +1, "btc": 0,  "dxy": -1, "min": 90,  "text": "ЦБ покупают золото → ↑XAU долгосрочно"},
    "gold reserve":      {"xau": +1, "xag": 0,  "btc": 0,  "dxy": 0,  "min": 60,  "text": "Золотые резервы ЦБ — следи за направлением"},
    "gold ban":          {"xau": -1, "xag": 0,  "btc": 0,  "dxy": 0,  "min": 60,  "text": "Ограничение золота"},
    # ── Крипто ─────────────────────────────────────────────────────────────────
    "bitcoin etf":       {"xau": 0,  "xag": 0,  "btc": +2, "dxy": -1, "min": 60,  "text": "Bitcoin ETF → ↑BTC институциональный приток"},
    "crypto ban":        {"xau": 0,  "xag": 0,  "btc": -3, "dxy": +1, "min": 60,  "text": "Запрет крипто → ↓↓BTC"},
    "crypto regulation": {"xau": 0,  "xag": 0,  "btc": -1, "dxy": 0,  "min": 45,  "text": "Регулирование крипто — неопределённость"},
    "crypto":            {"xau": 0,  "xag": 0,  "btc": -1, "dxy": 0,  "min": 30,  "text": "Крипто-регулирование"},
    "sec":               {"xau": 0,  "xag": 0,  "btc": -1, "dxy": 0,  "min": 30,  "text": "SEC — регулятор крипто"},
}


# ── Перевод заголовков на русский ─────────────────────────────────────────────

_translate_cache: Dict[str, str] = {}


def _translate_ru(text: str) -> str:
    """
    Переводит английский текст на русский через MyMemory API.
    Бесплатно, без ключа, лимит ~1000 запросов/день.
    При ошибке возвращает оригинал.
    """
    if not text:
        return text
    cache_key = text[:120]
    if cache_key in _translate_cache:
        return _translate_cache[cache_key]
    try:
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:500], "langpair": "en|ru"},
            timeout=8,
        )
        data = r.json()
        result = data.get("responseData", {}).get("translatedText", "")
        # Иногда API возвращает "QUERY LENGTH LIMIT EXCEDEED" или оригинал
        if result and "QUERY LENGTH" not in result and result.lower() != text.lower():
            _translate_cache[cache_key] = result
            return result
    except Exception as e:
        logger.debug(f"Перевод не удался: {e}")
    _translate_cache[cache_key] = text
    return text


# ── Дедупликация (seen stories) ────────────────────────────────────────────────

def _load_seen() -> set:
    try:
        with open(_SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_seen(seen: set) -> None:
    lst = sorted(seen)[-1000:]  # храним последние 1000
    with open(_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(lst, f)


def _story_id(title: str, url: str = "") -> str:
    raw = (title + url)[:200]
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ── Парсинг RSS ────────────────────────────────────────────────────────────────

def _fetch_feed(url: str) -> List[Dict]:
    """Загружает RSS и возвращает список {title, link, pub_date}."""
    stories = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MarketBot/2.0)",
            "Accept": "application/rss+xml, application/xml, text/xml",
        }
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        for item in root.findall(".//item")[:15]:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            desc  = (item.findtext("description") or "").strip()
            if title:
                stories.append({
                    "title": title,
                    "link":  link,
                    "pub":   pub,
                    "desc":  desc[:200],
                    "text":  f"{title} {desc}".lower(),
                })
    except Exception as e:
        logger.debug(f"Фид {url[:60]}…: {e}")
    return stories


# ── Обнаружение персон ─────────────────────────────────────────────────────────

def _detect_persons(text_low: str) -> List[Tuple[str, str, str]]:
    """Возвращает список (emoji+имя, роль, категория) персон найденных в тексте."""
    found = []
    seen_keys = set()
    for keyword, (name, role, cat) in _PERSONS.items():
        if keyword in text_low and keyword not in seen_keys:
            found.append((name, role, cat))
            # Помечаем более короткие ключи чтобы не дублировать
            seen_keys.add(keyword)
    return found


# ── Оценка импакта ─────────────────────────────────────────────────────────────

def _calc_impact(text_low: str) -> Dict:
    """
    Вычисляет суммарный импакт на XAU/XAG/BTC/DXY.
    Возвращает dict с суммарными значениями и списком триггеров.
    """
    xau = xag = btc = dxy = 0
    duration = 30
    triggers = []

    for keyword, rule in _IMPACT_RULES.items():
        if keyword in text_low:
            xau += rule["xau"]
            xag += rule.get("xag", 0)
            btc += rule["btc"]
            dxy += rule["dxy"]
            duration = max(duration, rule["min"])
            triggers.append(rule["text"])

    # Зажимаем в [-4, +4]
    xau = max(-4, min(4, xau))
    xag = max(-4, min(4, xag))
    btc = max(-4, min(4, btc))
    dxy = max(-4, min(4, dxy))

    return {
        "xau": xau, "xag": xag, "btc": btc, "dxy": dxy,
        "duration": duration,
        "triggers": triggers[:3],  # топ-3 причины
        "total_abs": abs(xau) + abs(xag) + abs(btc) + abs(dxy),
    }


# ── Форматирование Telegram-алерта ────────────────────────────────────────────

def _fmt_arrow(v: int) -> str:
    if v >= 3:    return "↑↑↑ СИЛЬНЫЙ"
    if v == 2:    return "↑↑ бычий"
    if v == 1:    return "↑ слабый"
    if v == 0:    return "→ нейтрально"
    if v == -1:   return "↓ слабый"
    if v == -2:   return "↓↓ медвежий"
    return "↓↓↓ СИЛЬНЫЙ"


def _impact_level(total_abs: int) -> Tuple[str, str]:
    """Возвращает (emoji заголовок, уровень)."""
    if total_abs >= 8:   return "🚨 КРИТИЧНО", "critical"
    if total_abs >= 5:   return "⚡ ВАЖНАЯ НОВОСТЬ", "high"
    if total_abs >= 3:   return "📰 РЫНОЧНАЯ НОВОСТЬ", "medium"
    return "🌍 ГЕО", "low"


def _build_alert(title: str, persons: list, impact: Dict, source: str) -> Optional[str]:
    """Формирует Telegram-сообщение. Возвращает None если импакт слишком мал."""
    total = impact["total_abs"]
    if total < 3:
        return None

    header, level = _impact_level(total)
    now_s = datetime.now(timezone.utc).strftime("%H:%M UTC")

    lines = [
        f"{header}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Персоны
    if persons:
        pers_str = " · ".join(f"{n} <i>({r})</i>" for n, r, _ in persons[:3])
        lines.append(f"👤 {pers_str}")

    # Заголовок новости (переводим на русский)
    title_ru = _translate_ru(title[:250])
    lines.append(f"\n📰 <b>{title_ru[:220]}</b>")
    if title_ru.lower() != title[:220].lower():
        lines.append(f"<i>🇬🇧 {title[:140]}</i>")
    lines.append(f"🕐 {now_s}  |  {source}")

    # Реакция рынков
    lines.append("\n📊 <b>Реакция рынков:</b>")
    xau_s = _fmt_arrow(impact["xau"])
    xag_s = _fmt_arrow(impact["xag"])
    btc_s = _fmt_arrow(impact["btc"])
    dxy_s = _fmt_arrow(impact["dxy"])

    if impact["xau"] != 0:
        lines.append(f"  🥇 <b>XAU (Золото):</b>  {xau_s}  ({impact['xau']:+d})")
    if impact["xag"] != 0:
        lines.append(f"  🥈 <b>XAG (Серебро):</b>  {xag_s}  ({impact['xag']:+d})")
    if impact["btc"] != 0:
        lines.append(f"  ₿ <b>BTC:</b>  {btc_s}  ({impact['btc']:+d})")
    if impact["dxy"] != 0:
        lines.append(f"  💵 <b>DXY (Доллар):</b>  {dxy_s}  ({impact['dxy']:+d})")

    # Причины
    if impact["triggers"]:
        lines.append("\n💡 <i>" + "  |  ".join(impact["triggers"][:2]) + "</i>")

    lines.append(f"⏱ Ожидаемый импакт: ~{impact['duration']} мин")

    return "\n".join(lines)


# ── Отправка Telegram ─────────────────────────────────────────────────────────

def _can_send_alert() -> bool:
    """Антиспам: не более MAX алертов в час."""
    now = time.time()
    _alert_times[:] = [t for t in _alert_times if now - t < 3600]
    return len(_alert_times) < _MAX_ALERTS_PER_HOUR


def _send_alert(text: str) -> None:
    try:
        import requests as _rq
        _rq.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        _alert_times.append(time.time())
        logger.info("Новостной алерт отправлен")
    except Exception as e:
        logger.debug(f"Алерт не отправлен: {e}")


# ── Основное сканирование ──────────────────────────────────────────────────────

# Кеш последнего сентимента для use в screeners
_sentiment_cache: Dict = {"score": 0.0, "ts": 0.0}


def _scan_once(cold_start: bool = False) -> None:
    """
    Однократное сканирование всех RSS-лент.
    cold_start=True: только строим seen-базу без отправки алертов.
    """
    seen = _load_seen()
    new_seen = set()
    sent_count = 0

    # Определяем источник из URL
    def _src(url: str) -> str:
        if "forexlive" in url:
            if "centralbank" in url: return "ForexLive/CentralBank"
            if "forex" in url:       return "ForexLive/FX"
            return "ForexLive"
        if "seekingalpha" in url:    return "SeekingAlpha"
        if "marketwatch" in url:     return "MarketWatch"
        if "bloomberg" in url:       return "Bloomberg"
        if "bbc" in url:             return "BBC"
        if "guardian" in url:        return "Guardian"
        return "News"

    high_impact_stories = []
    xau_total = 0

    for url in _FEEDS:
        stories = _fetch_feed(url)
        src = _src(url)

        for story in stories:
            sid = _story_id(story["title"], story["link"])
            if sid in seen:
                continue
            new_seen.add(sid)

            text_low  = story["text"]
            persons   = _detect_persons(text_low)
            impact    = _calc_impact(text_low)
            xau_total += impact["xau"]

            # Только значимые события отправляем в Telegram (не при холодном старте)
            min_impact = 4 if not persons else 3
            if not cold_start and impact["total_abs"] >= min_impact and _can_send_alert():
                alert_text = _build_alert(story["title"], persons, impact, src)
                if alert_text:
                    _send_alert(alert_text)
                    sent_count += 1
                    with _ALERT_LOCK:
                        _LAST_ALERTS.insert(0, {
                            "title":   story["title"],
                            "persons": [n for n, _, _ in persons],
                            "impact":  impact,
                            "source":  src,
                            "ts":      datetime.now(timezone.utc).isoformat(),
                        })
                        _LAST_ALERTS[:] = _LAST_ALERTS[:20]

    # Обновляем seen
    seen.update(new_seen)
    _save_seen(seen)

    # Обновляем сентимент-кеш
    n = max(1, len(_FEEDS))
    _sentiment_cache["score"] = max(-1.0, min(1.0, xau_total / (n * 2)))
    _sentiment_cache["ts"]    = time.time()

    if new_seen:
        logger.info(f"Новости: {len(new_seen)} новых историй, {sent_count} алертов отправлено")


def _monitor_loop() -> None:
    """Фоновый поток: сканирование каждые 10 минут."""
    logger.info("Монитор новостей запущен (каждые 10 мин)")
    # Первый скан — только строим базу seen, алерты НЕ отправляем
    # Это предотвращает спам при каждом перезапуске бота
    try:
        _scan_once(cold_start=True)
        logger.info("Монитор новостей: база seen построена, алерты активны")
    except Exception as e:
        logger.warning(f"Монитор холодный старт: {e}")

    while True:
        time.sleep(600)
        try:
            _scan_once()
        except Exception as e:
            logger.warning(f"Монитор новостей ошибка: {e}")


def start() -> None:
    """Запускает фоновый поток мониторинга новостей."""
    t = threading.Thread(target=_monitor_loop, daemon=True, name="news-monitor")
    t.start()


# ── Публичный API ─────────────────────────────────────────────────────────────

def get_latest_alerts(n: int = 5) -> List[Dict]:
    """Возвращает последние N новостных алертов для /news команды."""
    with _ALERT_LOCK:
        return _LAST_ALERTS[:n]


def get_news_sentiment() -> float:
    """
    Текущий новостной сентимент для XAU (-1..+1).
    +1 = много бычьих новостей, -1 = медвежьих.
    Кеш 10 мин.
    """
    return _sentiment_cache.get("score", 0.0)

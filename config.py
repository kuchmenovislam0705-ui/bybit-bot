"""Настройки сигнального скальпинг-бота."""
import os


def _load_env() -> None:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# ── API ───────────────────────────────────────────────────────────────────────
API_KEY    = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TESTNET    = os.getenv("TESTNET", "false").lower() == "true"
PAPER_MODE = False

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Инструменты ───────────────────────────────────────────────────────────────
# XAU/XAG: 24/5 — только рабочие дни (пн-пт), спот-рынок закрыт на выходных
# Крипто:  24/7 — без ограничений по времени
#
# Логика выбора:
#   ETHUSDT  — #2 по капитализации, коррелирует с NASDAQ, лучший объём
#   SOLUSDT  — топ-5, отличная волатильность, популярен у скальперов
#   BNBUSDT  — стабильный, менее шумный, хорошие тренды
#   XRPUSDT  — огромный объём в трендах, быстрые движения
SIGNAL_INSTRUMENTS = ["XAUUSDT", "XAGUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
COMMODITY_SYMBOLS  = ["XAUUSDT", "XAGUSDT"]
ALTCOIN_SYMBOLS    = ["ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# ── Скальпинг-параметры ───────────────────────────────────────────────────────
SL_ATR_MULT  = 1.5    # ATR × 1.5 — тайтовый стоп для скальпа
TP_RR        = 2.0    # 2R — реалистично для 15M скальпа
TP1_RR       = TP_RR
TP2_RR       = TP_RR

# Минимальный суммарный скор — Grade A (≥18) или сильный B (14+)
# Меньше 14 = слишком слабое подтверждение, исторически убыточно
SIGNAL_MIN_SCORE = 14

# ADX — фильтр бокового рынка. 20+ = стандартный порог тренда
MIN_ADX = 20

# Кулдаун между сигналами по ОДНОМУ инструменту — 2 часа
# Предотвращает "усреднение" убыточной позиции
SIGNAL_COOLDOWN_HOURS = 2

# ── Технический анализ ────────────────────────────────────────────────────────
RSI_PERIOD  = 14
ATR_PERIOD  = 14
RVOL_PERIOD = 20
KLINE_LIMIT = 120   # 120 × 15M = 30 часов истории

# ── Приложение ────────────────────────────────────────────────────────────────
SCAN_INTERVAL = 900   # 15 минут (один скан = одна свеча 15M)
MAX_WORKERS   = 4
STATE_FILE    = os.path.join(os.path.dirname(__file__), "state.json")
LOG_FILE      = os.path.join(os.path.dirname(__file__), "bot.log")
WEBAPP_PORT    = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "8080")))
WEBAPP_ENABLED = True
# Секретный токен для TradingView вебхука (опционально, можно оставить пустым)
# Если задан — TradingView должен слать {"secret": "ВАШ_ТОКЕН"} в JSON
TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "")

# ── Совместимость ─────────────────────────────────────────────────────────────
LEVERAGE         = 10
S1_MIN_VOLUME_24H = 0
BTC_TREND_FILTER  = False
BTC_TREND_EMA     = 21
S3_TOP_N          = 0
S3_MIN_CHANGE_24H = 5.0
S3_MAX_CHANGE_24H = 20.0
MAX_POSITIONS     = 0
DAILY_LOSS_LIMIT  = 99.0
RISK_PER_TRADE    = 1.0
MAX_POSITION_PCT  = 40.0
COOLDOWN_AFTER_SL = 0
BREAKEVEN_R       = 0.5
TRAILING_ATR_MULT = 1.0
TRADE_HOURS_UTC   = (0, 24)

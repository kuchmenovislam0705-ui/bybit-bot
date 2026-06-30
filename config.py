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
COMMODITY_SYMBOLS  = ["XAUUSDT", "XAGUSDT"]
TV_ONLY_SYMBOLS    = []
FOREX_SYMBOLS      = []
INDEX_SYMBOLS      = []

SIGNAL_INSTRUMENTS = ["XAUUSDT", "XAGUSDT", "BTCUSDT"]

# ── Скальпинг-параметры ───────────────────────────────────────────────────────
SL_ATR_MULT  = 1.2
TP_RR        = 1.5
TP1_RR       = TP_RR
TP2_RR       = TP_RR

# Минимальный скор для сигнала
SIGNAL_MIN_SCORE = 5    # высокочастотный скальпинг — больше сигналов

# Металлы — пониженный ADX (XAU/XAG структурно медленнее BTC)
COMM_MIN_ADX  = 10      # XAU/XAG ADX часто 8-14 при реальном тренде
FOREX_MIN_SCORE = 8     # совместимость (не используется без форекс)
FOREX_MIN_ADX   = 10
FOREX_ATR_MIN   = 0.003

# ADX — фильтр флэта для BTC
MIN_ADX = 15

# Кулдаун между сигналами по одному инструменту
SIGNAL_COOLDOWN_HOURS = 0.083  # 5 минут — высокочастотный скальпинг

# ── Технический анализ ────────────────────────────────────────────────────────
RSI_PERIOD  = 14
ATR_PERIOD  = 14
RVOL_PERIOD = 20
KLINE_LIMIT = 120   # 120 × 5M = 10 часов истории

# ── Приложение ────────────────────────────────────────────────────────────────
SCAN_INTERVAL = 30    # 30 секунд — максимальная частота скальпинга
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

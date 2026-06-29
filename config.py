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
# Bybit-инструменты (OHLCV + ордербук через Bybit API)
COMMODITY_SYMBOLS  = ["XAUUSDT", "XAGUSDT"]
ALTCOIN_SYMBOLS    = ["ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# TV-only инструменты (данные только через TradingView, торговля вручную)
INDEX_SYMBOLS = ["NAS100", "SPX500"]
FOREX_SYMBOLS = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]
TV_ONLY_SYMBOLS = INDEX_SYMBOLS + FOREX_SYMBOLS

SIGNAL_INSTRUMENTS = (
    ["XAUUSDT", "XAGUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    + INDEX_SYMBOLS
    + FOREX_SYMBOLS
)

# ── Скальпинг-параметры ───────────────────────────────────────────────────────
SL_ATR_MULT  = 1.2    # ATR × 1.2 — тайтый стоп для 5M скальпа
TP_RR        = 1.5    # 1.5R — реалистично для 5M скальпа
TP1_RR       = TP_RR
TP2_RR       = TP_RR

# Минимальный скор для сигнала
# 10+ = достаточное подтверждение для 5M скальпа
SIGNAL_MIN_SCORE = 10

# ADX — фильтр флэта. 15+ = есть направление
MIN_ADX = 15

# Кулдаун между сигналами по одному инструменту — 30 минут
SIGNAL_COOLDOWN_HOURS = 0.5

# ── Технический анализ ────────────────────────────────────────────────────────
RSI_PERIOD  = 14
ATR_PERIOD  = 14
RVOL_PERIOD = 20
KLINE_LIMIT = 120   # 120 × 5M = 10 часов истории

# ── Приложение ────────────────────────────────────────────────────────────────
SCAN_INTERVAL = 300   # 5 минут — один скан = одна свеча 5M
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

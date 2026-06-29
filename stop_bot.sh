#!/bin/bash
# Корректная остановка бота по PID-файлу
PID_FILE="/tmp/bybit_bot.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    echo "Останавливаю бот (PID=$PID)..."
    kill "$PID" 2>/dev/null && echo "Готово." || echo "Процесс не найден."
    rm -f "$PID_FILE"
else
    echo "PID-файл не найден. Убиваю все Python-процессы с main.py..."
    pgrep -f "bybit_bot/main.py" | xargs kill 2>/dev/null
    pgrep -f " main.py" | xargs kill 2>/dev/null
    echo "Готово."
fi

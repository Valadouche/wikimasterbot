#!/usr/bin/env bash
# Démarre le bot en arrière-plan (logs dans logs/bot.log).
cd "$(dirname "$0")"
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
  echo "Le bot tourne déjà (PID $(cat bot.pid))."; exit 1
fi
mkdir -p logs
nohup .venv/bin/python bot.py "$@" >> logs/stdout.log 2>&1 &
BOT_PID=$!
echo "$BOT_PID" > bot.pid
# Empêche la mise en veille automatique du Mac tant que le bot tourne
if command -v caffeinate >/dev/null; then nohup caffeinate -i -w "$BOT_PID" >/dev/null 2>&1 & fi
echo "Bot démarré (PID $(cat bot.pid)). Suivi : tail -f logs/bot.log"

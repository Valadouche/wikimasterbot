#!/usr/bin/env bash
# Arrête proprement le bot lancé par start.sh.
cd "$(dirname "$0")"
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
  kill -TERM "$(cat bot.pid)" && echo "Arrêt demandé (PID $(cat bot.pid))."
else
  echo "Le bot ne tourne pas."
fi
rm -f bot.pid

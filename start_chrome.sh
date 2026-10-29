#!/usr/bin/env bash
# Lance Chrome avec le port de débogage CDP sur un profil dédié (persistant).
# Chrome >= 136 refuse le débogage distant sur le profil par défaut : connectez-vous
# une seule fois à WikiMasters dans cette fenêtre, la session est ensuite conservée.
PROFILE="${WIKIMASTERS_PROFILE:-$HOME/.wikimasters-chrome}"
PORT="${CDP_PORT:-9222}"
open -na "Google Chrome" --args --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" \
  --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding "$@"
echo "Chrome lancé (profil $PROFILE, CDP sur le port $PORT)."

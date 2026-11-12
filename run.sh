#!/usr/bin/env bash
# Start Erik's personal HUD on 127.0.0.1:${PERSONAL_HUD_PORT:-8792} (local only). Watch-only + chat with Mona.
# Password: env PERSONAL_HUD_PASSWORD, else read from ./.password (chmod 600). Never printed or logged.
set -e
cd "$(dirname "$0")"
PY=../.venv/bin/python
PORT="${PERSONAL_HUD_PORT:-8792}"
mkdir -p data
alive(){ [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
if alive data/hud.pid; then echo "personal HUD already running (pid $(cat data/hud.pid)) on http://127.0.0.1:$PORT"; exit 0; fi
if [ -z "${PERSONAL_HUD_PASSWORD:-}" ] && [ -f .password ]; then PERSONAL_HUD_PASSWORD="$(cat .password)"; fi
if [ -z "${PERSONAL_HUD_PASSWORD:-}" ]; then echo "PERSONAL_HUD_PASSWORD is not set and ./.password is missing; refusing to start." >&2; exit 1; fi
export PERSONAL_HUD_PASSWORD
nohup $PY -m uvicorn phud.server:app --host 127.0.0.1 --port "$PORT" --no-server-header --no-access-log >> data/hud.log 2>&1 &
echo $! > data/hud.pid
ok=0; for i in $(seq 1 30); do sleep 0.5; kill -0 "$(cat data/hud.pid)" 2>/dev/null || break; curl -fs -o /dev/null "http://127.0.0.1:$PORT/healthz" && { ok=1; break; }; done
if [ "$ok" = 1 ] && kill -0 "$(cat data/hud.pid)" 2>/dev/null; then echo "personal HUD pid $(cat data/hud.pid) on http://127.0.0.1:$PORT (local only)"
else echo "personal HUD failed to start; see data/hud.log" >&2; rm -f data/hud.pid; exit 1; fi

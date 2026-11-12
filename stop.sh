#!/usr/bin/env bash
# Stop only the personal HUD (never touches the orchestrator or the main HUD on 8787).
cd "$(dirname "$0")"
if [ -f data/hud.pid ]; then
  pid="$(cat data/hud.pid)"
  if grep -q "phud.server" "/proc/$pid/cmdline" 2>/dev/null; then kill "$pid"; for i in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done; echo "stopped personal HUD pid $pid"; else echo "pid $pid is not the personal HUD; not killing"; fi
  rm -f data/hud.pid
else echo "personal HUD not running (no data/hud.pid)"; fi

#!/usr/bin/env bash
# Stop only the push agent.
cd "$(dirname "$0")"
if [ -f data/push_agent.pid ]; then
  pid="$(cat data/push_agent.pid)"
  if grep -q "push_agent.py" "/proc/$pid/cmdline" 2>/dev/null; then kill "$pid"; for i in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done; echo "stopped push agent pid $pid"
  else echo "pid $pid is not the push agent; not killing"; fi
  rm -f data/push_agent.pid
else echo "push agent not running"; fi

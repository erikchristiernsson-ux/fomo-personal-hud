#!/usr/bin/env bash
# Start the box -> Render mirror push agent (every 60 s). Needs RENDER_URL + RELAY_TOKEN in ./.relay.env.
set -e
cd "$(dirname "$0")"
mkdir -p data
if [ -f data/push_agent.pid ] && kill -0 "$(cat data/push_agent.pid)" 2>/dev/null; then echo "push agent already running (pid $(cat data/push_agent.pid))"; exit 0; fi
[ -f .relay.env ] || { echo ".relay.env missing; refusing." >&2; exit 1; }
grep -q '^RENDER_URL=..*' .relay.env || { echo "RENDER_URL is empty in .relay.env; set it after the Render service exists." >&2; exit 1; }
nohup ../.venv/bin/python tools/push_agent.py >> data/push_agent.log 2>&1 &
echo $! > data/push_agent.pid
sleep 2
if kill -0 "$(cat data/push_agent.pid)" 2>/dev/null; then echo "push agent pid $(cat data/push_agent.pid); log data/push_agent.log"; tail -n 2 data/push_agent.log
else echo "push agent exited; see data/push_agent.log" >&2; tail -n 5 data/push_agent.log >&2; rm -f data/push_agent.pid; exit 1; fi

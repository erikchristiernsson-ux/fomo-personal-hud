# Erik's personal HUD (watch-only + chat with Mona)

Separate FastAPI app; does not touch the orchestrator, the main HUD (8787) or firm.db (read-only connection).
No trading, deposit, funding, wallet or auto-trade controls exist. The only write is a chat message into
`/workspace/trader/team/hud_chat/chat.db`.

- URL: http://127.0.0.1:8792 (local to the box). Port 8790 was already taken by the box's egress-tunnel service.
- Start/stop: `./run.sh` / `./stop.sh` (pid in `data/hud.pid`, log in `data/hud.log`).
- Password: env `PERSONAL_HUD_PASSWORD`, else `./.password` (chmod 600). Never printed or logged.
  Auth reuses `firm/firm/hud/auth.py` (scrypt check, HMAC-signed HttpOnly SameSite=Strict cookie, 5-tries lockout)
  with its own cookie names; every POST needs `X-CSRF-Token` + same Origin.
- Data: `phud/data.py` builds a read-only snapshot: balance from `team/RULES.md` (or `config.json` override),
  shadow equity/history/closed positions/events from `firm/data/firm.db`, bot status from `team/reports/*.md`
  (mtime, first State/Status line, last `##` heading), handoff sparklines from `team/inbox/*/*.json` mtimes.
  Anything unreadable is `null` and shown as "missing".
- Polling: `/api/state` every 15 s, `/api/chat` every 5 s. Toasts for new events, report updates, handoffs, Mona replies.

## Chat relay (bots can't receive HTTP)
```
python3 /workspace/trader/team/hud_chat/chat.py pending        # print queued Erik messages, mark delivered (--peek = don't mark)
python3 /workspace/trader/team/hud_chat/chat.py reply "text"   # Mona's answer; marks open Erik messages answered (--to ID for one)
python3 /workspace/trader/team/hud_chat/chat.py history -n 30
python3 /workspace/trader/team/hud_chat/chat.py status
```
States: queued -> delivered (relay ran `pending`) -> answered (Mona ran `reply`). `pending` prints `NO_PENDING` when empty.

## Tests / screenshots
```
../.venv/bin/python -m pytest -q tests
../.venv/bin/python tools/shots.py [toast]     # writes shots/*.png (Chrome at /usr/bin/google-chrome)
```

## Phone version: MIRROR mode on Render
`PHUD_MODE=mirror` runs the same UI and login, but every value comes from an in-memory snapshot that the box pushes.
- Relay endpoints (mirror only, `Authorization: Bearer $RELAY_TOKEN`, constant-time compare; 404 in box mode):
  `POST /api/relay/snapshot` · `GET /api/relay/pending` (Erik's phone messages; marked relayed, re-served until the box confirms) ·
  `POST /api/relay/chat` (box chat history: Erik statuses + Mona replies; replaces the mirror view).
- Snapshot older than 3 min (`PHUD_STALE_S`) -> red "Box offline / data stale" banner and red status dot. No snapshot yet -> everything "missing".
- Phone chat states: `waiting_box` -> `relayed` -> (box) `queued` -> `delivered` -> `answered`.
- Memory only: a Render restart forgets the snapshot and any phone message not yet pulled (<= 1 min); the box re-sends the rest.
- Needs `PERSONAL_HUD_PASSWORD` (16+), `RELAY_TOKEN` (32+), optional `PERSONAL_HUD_SESSION_SECRET`; refuses to start otherwise.

Box side: `./run_agent.sh` / `./stop_agent.sh` run `tools/push_agent.py` every 60 s (pid `data/push_agent.pid`, log `data/push_agent.log`).
Each cycle: POST snapshot (box file paths stripped) -> pull phone messages into chat.db as `queued` (idempotent by mirror_id) -> push chat history.
Secrets live in `.relay.env` (chmod 600, gitignored): RENDER_URL, RELAY_TOKEN, PERSONAL_HUD_SESSION_SECRET, PERSONAL_HUD_PASSWORD (Render login).
Packaging: `Dockerfile` (mirror mode, port $PORT) + `render.yaml` (free web service `fomo-personal-hud`, health check `/healthz`).
`phud/auth.py` is a vendored copy of `firm/firm/hud/auth.py` (a test checks they match on the box).

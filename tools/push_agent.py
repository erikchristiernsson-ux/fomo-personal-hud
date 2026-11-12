#!/usr/bin/env python3
"""Box -> Render mirror push agent for Erik's personal HUD.
Every cycle (default 60 s):
  1. POST /api/relay/snapshot   with phud.data.public_snapshot() (read-only view of RULES.md, firm.db, reports, inbox)
  2. GET  /api/relay/pending    Erik's phone messages -> chat.db as normal 'queued' messages (idempotent by mirror_id),
                                so the existing `chat.py pending` -> Mona -> `chat.py reply` flow is unchanged
  3. POST /api/relay/chat       chat.db history (Erik statuses + Mona replies) -> mirror
Config: RENDER_URL and RELAY_TOKEN from personal_hud/.relay.env (chmod 600). The token is never printed or logged.
Usage: push_agent.py [--once] [--interval 60] [--env-file PATH]"""
import argparse, importlib.util, json, os, re, signal, sys, time, urllib.error, urllib.parse, urllib.request
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from phud import data

CHAT_PY = os.environ.get("PHUD_CHAT_PY", "/workspace/trader/team/hud_chat/chat.py")
_spec = importlib.util.spec_from_file_location("hud_chat", CHAT_PY); chat = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(chat)
TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

def log(msg): print(time.strftime("%Y-%m-%d %H:%M:%S %Z"), msg, flush=True)

def read_env(path):
  out = {}
  with open(path) as f:
    for line in f:
      line = line.strip()
      if not line or line.startswith("#") or "=" not in line: continue
      k, v = line.split("=", 1); out[k.strip()] = v.strip().strip('"').strip("'")
  return out

def check_url(url):
  u = urllib.parse.urlsplit(url)
  if u.scheme == "https" and u.netloc: return url.rstrip("/")
  if u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"): return url.rstrip("/")
  raise SystemExit("RENDER_URL must be https:// (plain http only for 127.0.0.1/localhost tests)")

class Relay:
  def __init__(self, url, token, timeout=90):
    self.url, self._tok, self.timeout = url, token, timeout
  def call(self, method, path, body=None):
    req = urllib.request.Request(self.url + path, method=method, data=None if body is None else json.dumps(body).encode(),
      headers={"Authorization": "Bearer " + self._tok, "Content-Type": "application/json", "User-Agent": "phud-push-agent/1"})
    try:
      with urllib.request.urlopen(req, timeout=self.timeout) as r: return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
      raise RuntimeError(f"{method} {path} -> HTTP {e.code}") from None
    except Exception as e:
      raise RuntimeError(f"{method} {path} -> {type(e).__name__}: {str(e)[:160]}") from None

def cycle(relay):
  res = {"snapshot": None, "pulled": 0, "pushed": 0, "errors": []}
  try:
    r = relay.call("POST", "/api/relay/snapshot", {"snapshot": data.public_snapshot()}); res["snapshot"] = r.get("received")
  except Exception as e: res["errors"].append(f"snapshot: {e}")
  try:
    msgs = relay.call("GET", "/api/relay/pending").get("messages", [])
    for m in msgs:
      text, mid = m.get("text"), m.get("mirror_id")
      if not isinstance(text, str) or not mid: continue
      ts = m.get("ts") if isinstance(m.get("ts"), str) and TS_RE.match(m["ts"]) else None
      try: chat.add_erik(text, mirror_id=str(mid)[:64], ts=ts); res["pulled"] += 1
      except ValueError as e: res["errors"].append(f"message {mid}: {e}")
  except Exception as e: res["errors"].append(f"pending: {e}")
  try:
    hist = chat.history(n=150)
    r = relay.call("POST", "/api/relay/chat", {"messages": hist}); res["pushed"] = r.get("messages", 0)
  except Exception as e: res["errors"].append(f"chat: {e}")
  return res

def main(argv=None):
  ap = argparse.ArgumentParser()
  ap.add_argument("--once", action="store_true"); ap.add_argument("--interval", type=int, default=60)
  ap.add_argument("--env-file", default=os.path.join(HERE, ".relay.env"))
  a = ap.parse_args(argv)
  env = read_env(a.env_file)
  url, tok = env.get("RENDER_URL", ""), env.get("RELAY_TOKEN", "")
  if not url: raise SystemExit(f"RENDER_URL is empty in {a.env_file}; set it once the Render service exists.")
  if len(tok) < 32: raise SystemExit(f"RELAY_TOKEN missing or too short in {a.env_file}.")
  relay = Relay(check_url(url), tok); del tok, env
  stop = {"v": False}
  signal.signal(signal.SIGTERM, lambda *_: stop.update(v=True))
  log(f"push agent started -> {relay.url} every {a.interval}s")
  while True:
    t0 = time.time(); r = cycle(relay)
    log(f"cycle: snapshot={'ok' if r['snapshot'] else 'FAILED'} pulled={r['pulled']} chat_pushed={r['pushed']}" + (f" errors={r['errors']}" if r["errors"] else ""))
    if a.once: return 0 if not r["errors"] else 1
    while not stop["v"] and time.time() - t0 < a.interval: time.sleep(1)
    if stop["v"]: log("push agent stopped"); return 0

if __name__ == "__main__": sys.exit(main())

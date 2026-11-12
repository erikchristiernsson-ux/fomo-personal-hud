import os, subprocess, sys
from conftest import login, PW
CHAT = "/workspace/trader/team/hud_chat/chat.py"

# ---------- auth ----------
def test_api_requires_auth(client):
  for p in ("/api/state", "/api/chat", "/api/session"): assert client.get(p).status_code == 401
  assert client.post("/api/chat", json={"text": "hi"}).status_code == 401
  r = client.get("/", follow_redirects=False); assert r.status_code == 303 and r.headers["location"] == "/login"
  assert client.get("/healthz").json() == {"ok": True}

def test_wrong_password_rejected(client):
  r = login(client, "nope"); assert r.status_code == 401 and "Wrong password" in r.text
  assert client.get("/api/state").status_code == 401

def test_login_sets_own_cookie_and_security_headers(client):
  r = login(client); assert r.status_code == 303
  assert "phud_session" in r.headers.get("set-cookie", "") and "HttpOnly" in r.headers["set-cookie"]
  r = client.get("/api/state"); assert r.status_code == 200
  assert r.headers["x-frame-options"] == "DENY" and "script-src 'self'" in r.headers["content-security-policy"]

def test_refuses_to_start_without_password(env, monkeypatch):
  monkeypatch.delenv("PERSONAL_HUD_PASSWORD")
  import importlib, phud.server as s, pytest
  s = importlib.reload(s); s.AUTH = None
  with pytest.raises(SystemExit): s._startup()
  from fastapi.testclient import TestClient
  assert TestClient(s.app).get("/api/state").status_code == 503   # even if started, nothing is served without a password

def test_csrf_required_on_post(authed):
  tok = authed.headers.pop("X-CSRF-Token")
  assert authed.post("/api/chat", json={"text": "x"}).status_code == 403
  assert authed.post("/api/chat", json={"text": "x"}, headers={"X-CSRF-Token": "bad"}).status_code == 403
  assert authed.post("/api/chat", json={"text": "x"}, headers={"X-CSRF-Token": tok, "Origin": "https://evil.example"}).status_code == 403
  assert authed.post("/api/chat", json={"text": "x"}, headers={"X-CSRF-Token": tok}).status_code == 200

# ---------- state ----------
def test_state_maps_real_data(authed):
  s = authed.get("/api/state").json()
  assert s["firm"] == "Test Firm"
  assert s["balance"]["usd"] == 4.0 and "RULES.md" in s["balance"]["source"]
  assert s["rules"]["rules_version"] == "1.1" and s["rules"]["alert_mode"] is True and s["rules"]["held"] == []
  assert s["goal"]["target_usd"] == 40.0 and s["goal"]["current_multiple"] == 1.0 and s["goal"]["ticks"] == [2, 5, 10]
  e = s["engine"]; assert e["equity"]["equity"] == 3.9 and [x["equity"] for x in e["equity_history"]] == [4.0, 4.1, 3.9]
  assert e["alive"] is False and e["status"] == "down"     # pid 999999 is not an orchestrator
  assert len(e["open_positions"]) == 1
  c = s["closed"]; assert c["count"] == 2 and c["wins"] == 1 and {i["symbol"] for i in c["items"]} == {"WIN", "LOSS"}
  assert round(c["items"][0]["pnl_pct"], 1) in (-13.0, 20.0)
  bots = {b["key"]: b for b in s["bots"]}; assert len(bots) == 9
  assert bots["FILTER"]["action"] == "State: active under v1.1." and bots["FILTER"]["latest_section"] == "Screen 12:40"
  assert bots["FILTER"]["engine"]["last_output"].startswith("PASS 0") and bots["FILTER"]["tag"] == "live"
  assert bots["SCOUT"]["action"] == "Quick scan: nothing found."
  assert bots["MANAGER"]["tag"] == "missing" and bots["MANAGER"]["action"] is None   # never invented
  assert sum(bots["FILTER"]["handoffs_12h"]) == 1 and sum(bots["SCOUT"]["handoffs_12h"]) == 1
  ids = [f["id"] for f in s["feed"]]
  assert "ev:1" in ids and "ev:2" not in ids               # auth events never toast
  assert any(i.startswith("ib:FILTER:") for i in ids) and any(i.startswith("rp:FILTER:") for i in ids)
  assert s["chat"]["counts"] == {}

def test_config_balance_override(authed, env):
  (env / "config.json").write_text('{"fomo_balance_usd": 8.0, "fomo_balance_set": "2026-11-13", "goal_start_usd": 4.0, "goal_multiple": 10}')
  s = authed.get("/api/state").json(); assert s["balance"]["usd"] == 8.0 and s["goal"]["current_multiple"] == 2.0

def test_missing_sources_show_missing(authed, env):
  os.remove(env / "RULES.md"); os.remove(env / "firm.db")
  s = authed.get("/api/state").json()
  assert s["balance"]["usd"] is None and s["rules"]["held"] is None and s["engine"]["available"] is False and s["closed"]["available"] is False

def test_no_trading_or_funding_routes(server):
  paths = {getattr(r, "path", "") for r in server.app.routes}
  assert not any(w in p for p in paths for w in ("trade", "deposit", "fund", "wallet", "auto", "order", "buy", "sell"))

# ---------- chat via API ----------
def test_chat_post_and_states(authed, env):
  assert authed.post("/api/chat", json={"text": "   "}).status_code == 400
  assert authed.post("/api/chat", json={"text": "x" * 4001}).status_code == 400
  r = authed.post("/api/chat", json={"text": "Hej Mona"}).json(); assert r["status"] == "queued"
  m = authed.get("/api/chat").json()["messages"]; assert m[-1]["text"] == "Hej Mona" and m[-1]["status"] == "queued"
  envv = {**os.environ}
  out = subprocess.run([sys.executable, CHAT, "pending"], capture_output=True, text=True, env=envv).stdout
  assert "Hej Mona" in out and "chat.py reply" in out
  assert authed.get("/api/chat").json()["messages"][-1]["status"] == "delivered"
  subprocess.run([sys.executable, CHAT, "reply", "Hej Erik!"], check=True, env=envv, capture_output=True)
  m = authed.get("/api/chat").json()["messages"]
  assert m[-2]["status"] == "answered" and m[-1]["sender"] == "mona" and m[-1]["text"] == "Hej Erik!"
  assert authed.get("/api/state").json()["chat"]["counts"] == {"answered": 1}

def test_chat_rate_limit(authed, server):
  import time; server._RATE[:] = [time.time()] * 60
  assert authed.post("/api/chat", json={"text": "hi"}).status_code == 429

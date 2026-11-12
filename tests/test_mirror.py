import os, socket, subprocess, sys, time, json, filecmp, urllib.request
import pytest
from conftest import TOKEN, MPW, login
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
B = {"Authorization": "Bearer " + TOKEN}

def snap():
  sys.path.insert(0, HERE)
  from phud import data
  return data.public_snapshot()

# ---------- relay auth ----------
def test_relay_requires_token(mclient):
  for m, p in (("POST", "/api/relay/snapshot"), ("GET", "/api/relay/pending"), ("POST", "/api/relay/chat")):
    assert mclient.request(m, p, json={}).status_code == 401                                    # session cookie is not enough
    assert mclient.request(m, p, json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert mclient.request(m, p, json={}, headers={"Authorization": "Bearer " + TOKEN[:-1] + "X"}).status_code == 401
    assert mclient.request(m, p, json={}, headers={"Authorization": TOKEN}).status_code == 401    # no Bearer prefix

def test_relay_absent_in_box_mode(authed):
  assert authed.get("/api/relay/pending", headers=B).status_code == 404

def test_mirror_ui_still_needs_login(mserver):
  from fastapi.testclient import TestClient
  with TestClient(mserver.app) as c:
    assert c.get("/api/state").status_code == 401 and c.get("/api/state", headers=B).status_code == 401
    assert login(c, "wrong-password-xx").status_code == 401

def test_mirror_refuses_weak_config(env, monkeypatch):
  import importlib, phud.server as s
  monkeypatch.setenv("PHUD_MODE", "mirror"); monkeypatch.setenv("PERSONAL_HUD_PASSWORD", MPW); monkeypatch.setenv("RELAY_TOKEN", "short")
  s = importlib.reload(s); s.AUTH = None
  with pytest.raises(SystemExit): s._startup()
  monkeypatch.setenv("RELAY_TOKEN", TOKEN); monkeypatch.setenv("PERSONAL_HUD_PASSWORD", "only-12-char")
  s = importlib.reload(s); s.AUTH = None
  with pytest.raises(SystemExit): s._startup()

# ---------- snapshot + stale ----------
def test_empty_mirror_is_stale_and_missing(mclient):
  s = mclient.get("/api/state").json()
  assert s["mirror"]["mode"] == "mirror" and s["mirror"]["stale"] is True and s["mirror"]["age_s"] is None
  assert s["balance"]["usd"] is None and s["bots"] == [] and s["engine"]["available"] is False

def test_snapshot_round_trip_and_stale(mclient, mserver):
  sn = snap()
  assert "report_path" not in sn["bots"][0] and "source" not in sn["rules"]
  assert mclient.post("/api/relay/snapshot", json={"snapshot": {"x": 1}}, headers=B).status_code == 400
  r = mclient.post("/api/relay/snapshot", json={"snapshot": sn}, headers=B); assert r.status_code == 200
  s = mclient.get("/api/state").json()
  assert s["mirror"]["stale"] is False and s["mirror"]["age_s"] <= 1
  assert s["balance"]["usd"] == 4.0 and s["closed"]["count"] == 2 and len(s["bots"]) == 9 and s["firm"] == "Test Firm"
  mserver.MIR.received -= 181                                                    # older than 3 minutes
  s = mclient.get("/api/state").json(); assert s["mirror"]["stale"] is True and s["mirror"]["age_s"] >= 181
  assert s["balance"]["usd"] == 4.0                                              # last values kept, flagged stale

def test_mirror_has_no_csrf_bypass(mclient):
  tok = mclient.headers.pop("X-CSRF-Token")
  assert mclient.post("/api/chat", json={"text": "x"}).status_code == 403

# ---------- chat in memory ----------
def test_mirror_chat_states(mclient):
  r = mclient.post("/api/chat", json={"text": "från mobilen"}).json(); assert r["status"] == "waiting_box"
  m = mclient.get("/api/chat").json()["messages"]; assert m[-1]["status"] == "waiting_box"
  p = mclient.get("/api/relay/pending", headers=B).json()["messages"]; assert [x["text"] for x in p] == ["från mobilen"]
  mid = p[0]["mirror_id"]
  assert mclient.get("/api/chat").json()["messages"][-1]["status"] == "relayed"
  assert len(mclient.get("/api/relay/pending", headers=B).json()["messages"]) == 1     # re-served until the box confirms
  box = [{"id": 7, "ts": "2026-11-12T12:00:00Z", "sender": "erik", "text": "från mobilen", "status": "delivered", "mirror_id": mid},
         {"id": 8, "ts": "2026-11-12T12:01:00Z", "sender": "mona", "text": "Hej!", "status": "sent", "mirror_id": None},
         {"id": 9, "ts": "x", "sender": "hacker", "text": "<script>", "status": "sent"}]
  r = mclient.post("/api/relay/chat", json={"messages": box}, headers=B).json(); assert r["messages"] == 2 and r["waiting"] == 0
  c = mclient.get("/api/chat").json()
  assert [(x["sender"], x["status"]) for x in c["messages"]] == [("erik", "delivered"), ("mona", "sent")]
  assert c["counts"] == {"delivered": 1}
  assert mclient.get("/api/relay/pending", headers=B).json()["messages"] == []

# ---------- end to end: real uvicorn mirror + push agent subprocess ----------
def free_port():
  s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

@pytest.fixture()
def live_mirror(env, tmp_path):
  port = free_port(); e = {**os.environ, "PHUD_MODE": "mirror", "RELAY_TOKEN": TOKEN, "PERSONAL_HUD_PASSWORD": MPW, "PHUD_DATA": str(tmp_path / "mdata")}
  e.pop("HUD_CHAT_DB", None)
  p = subprocess.Popen([PY, "-m", "uvicorn", "phud.server:app", "--host", "127.0.0.1", "--port", str(port)], cwd=HERE, env=e,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  url = f"http://127.0.0.1:{port}"
  for _ in range(60):
    try: urllib.request.urlopen(url + "/healthz", timeout=1); break
    except Exception: time.sleep(0.2)
  yield url
  p.terminate(); p.wait(5)

def req(url, path, method="GET", body=None):
  r = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers={**B, "Content-Type": "application/json"})
  with urllib.request.urlopen(r, timeout=5) as x: return json.loads(x.read())

def agent(url, tmp_path):
  envf = tmp_path / "relay.env"; envf.write_text(f"RENDER_URL={url}\nRELAY_TOKEN={TOKEN}\n")
  return subprocess.run([PY, os.path.join(HERE, "tools", "push_agent.py"), "--once", "--env-file", str(envf)], capture_output=True, text=True, env=os.environ, timeout=60)

def test_push_agent_chat_round_trip(live_mirror, tmp_path):
  from fastapi.testclient import TestClient  # noqa  (UI checks go over real HTTP below)
  import importlib.util
  spec = importlib.util.spec_from_file_location("c2", "/workspace/trader/team/hud_chat/chat.py"); chat = importlib.util.module_from_spec(spec); spec.loader.exec_module(chat)
  import httpx
  with httpx.Client(base_url=live_mirror) as c:
    r = c.get("/login"); import re; pre = re.search(r'name="pre" value="([^"]+)"', r.text).group(1)
    assert c.post("/login", data={"pre": pre, "password": MPW}).status_code in (200, 303)
    csrf = c.get("/api/session").json()["csrf"]
    assert c.post("/api/chat", json={"text": "Hi Mona from the phone"}, headers={"X-CSRF-Token": csrf}).status_code == 200
    a = agent(live_mirror, tmp_path); assert a.returncode == 0, a.stdout + a.stderr
    assert "pulled=1" in a.stdout and TOKEN not in a.stdout + a.stderr
    rows = chat.history()                                      # landed in chat.db as a normal queued message
    assert rows[-1]["text"] == "Hi Mona from the phone" and rows[-1]["status"] == "queued" and rows[-1]["mirror_id"]
    st = c.get("/api/state").json(); assert st["mirror"]["stale"] is False and st["balance"]["usd"] == 4.0 and len(st["bots"]) == 9
    assert c.get("/api/chat").json()["messages"][-1]["status"] == "queued"
    # existing relay flow on the box
    out = subprocess.run([PY, "/workspace/trader/team/hud_chat/chat.py", "pending"], capture_output=True, text=True).stdout; assert "Hi Mona from the phone" in out
    subprocess.run([PY, "/workspace/trader/team/hud_chat/chat.py", "reply", "Hej Erik, all good."], check=True, capture_output=True)
    a = agent(live_mirror, tmp_path); assert a.returncode == 0 and "pulled=0" in a.stdout       # no duplicate pull
    msgs = c.get("/api/chat").json()["messages"]
    assert [(m["sender"], m["status"]) for m in msgs] == [("erik", "answered"), ("mona", "sent")] and msgs[-1]["text"] == "Hej Erik, all good."
    assert len(chat.history()) == 2

def test_push_agent_wrong_token(live_mirror, tmp_path):
  envf = tmp_path / "bad.env"; envf.write_text(f"RENDER_URL={live_mirror}\nRELAY_TOKEN={'x' * 40}\n")
  a = subprocess.run([PY, os.path.join(HERE, "tools", "push_agent.py"), "--once", "--env-file", str(envf)], capture_output=True, text=True, timeout=60)
  assert a.returncode == 1 and "HTTP 401" in a.stdout and "x" * 40 not in a.stdout

def test_push_agent_rejects_plain_http_remote(tmp_path):
  envf = tmp_path / "r.env"; envf.write_text(f"RENDER_URL=http://example.com\nRELAY_TOKEN={TOKEN}\n")
  a = subprocess.run([PY, os.path.join(HERE, "tools", "push_agent.py"), "--once", "--env-file", str(envf)], capture_output=True, text=True, timeout=60)
  assert a.returncode != 0 and "https" in a.stderr

def test_vendored_auth_matches_box():
  box = "/workspace/trader/firm/firm/hud/auth.py"
  if not os.path.exists(box): pytest.skip("box original not present")
  assert filecmp.cmp(box, os.path.join(HERE, "phud", "auth.py"), shallow=False)

def test_repo_files_have_no_secrets():
  for f in (".password", ".relay.env"):
    p = os.path.join(HERE, f)
    if os.path.exists(p): assert oct(os.stat(p).st_mode & 0o777) == "0o600"
  gi = open(os.path.join(HERE, ".gitignore")).read().split()
  for f in (".password", ".relay.env", "data/", "shots/", "*.db"): assert f in gi

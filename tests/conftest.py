import os, re, sqlite3, sys, importlib, pytest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
PW = "test-password-123"

def make_firm(tmp):
  db = tmp / "firm.db"; c = sqlite3.connect(db)
  c.executescript("""CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE equity(id INTEGER PRIMARY KEY, ts TEXT, equity REAL, cash REAL, unrealized REAL);
    CREATE TABLE events(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, actor TEXT, detail TEXT);
    CREATE TABLE role_status(role TEXT PRIMARY KEY, name TEXT, persona TEXT, state TEXT, last_output TEXT, updated TEXT);
    CREATE TABLE positions(id INTEGER PRIMARY KEY, ticket_id INT, mint TEXT, symbol TEXT, opened TEXT, size_usd REAL, fee_in_usd REAL, tokens_raw TEXT, decimals INT, entry_price REAL, stop_price REAL, high_price REAL, trail_armed INT, trail_stop REAL, last_price REAL, last_value_usd REAL, last_check TEXT, status TEXT, closed TEXT, exit_price REAL, exit_value_usd REAL, fee_out_usd REAL, pnl_usd REAL, exit_reason TEXT, period TEXT, rules_version TEXT);""")
  c.execute("INSERT INTO kv VALUES('meta', ?)", ('{"pid": 999999, "rules_version": "1.1", "mode": "PAPER", "rules_mode": "small_balance", "starting_equity": 4.0}',))
  c.execute("INSERT INTO kv VALUES('orch_heartbeat', '\"2026-11-12T11:00:00Z\"')")
  for i, e in enumerate([4.0, 4.1, 3.9]): c.execute("INSERT INTO equity(ts,equity,cash,unrealized) VALUES(?,?,?,0)", (f"2026-11-12T10:0{i}:00Z", e, e))
  c.execute("INSERT INTO events(ts,kind,actor,detail) VALUES('2026-11-12T10:00:00Z','orchestrator_start','orchestrator','pid 1')")
  c.execute("INSERT INTO events(ts,kind,actor,detail) VALUES('2026-11-12T10:01:00Z','auth','hud','login from 127.0.0.1')")
  c.execute("INSERT INTO role_status VALUES('FILTER','Bruno','','done','PASS 0, WAIT 1, REJECT 19','2026-11-12T10:00:00Z')")
  c.execute("""INSERT INTO positions(symbol,mint,opened,closed,size_usd,status,pnl_usd,exit_reason,rules_version) VALUES
    ('WIN','m1','2026-11-12T09:00:00Z','2026-11-12T09:30:00Z',4.0,'closed',0.8,'trail','1.1'),
    ('LOSS','m2','2026-11-12T09:40:00Z','2026-11-12T09:50:00Z',4.0,'closed',-0.52,'stop','1.1'),
    ('OPEN','m3','2026-11-12T10:00:00Z',NULL,4.0,'open',NULL,NULL,'1.1')""")
  c.commit(); c.close(); return db

@pytest.fixture()
def env(tmp_path, monkeypatch):
  (tmp_path / "reports").mkdir(); (tmp_path / "inbox" / "FILTER").mkdir(parents=True); (tmp_path / "data").mkdir()
  (tmp_path / "reports" / "FILTER.md").write_text("# FILTER — Bruno\nUpdated: x\n\nState: active under v1.1.\n\n## Screen 12:40\nok\n")
  (tmp_path / "reports" / "SCOUT.md").write_text("# SCOUT\n\n> **Quick scan:** nothing found.\n")
  (tmp_path / "inbox" / "FILTER" / "20261112-1238-scout.json").write_text("{}")
  (tmp_path / "RULES.md").write_text("# rules\n## ALERT MODE (x)\n- Erik's real Fomo balance for sizing: $4.00 unless Erik reports a new number.\n## RULES v1.1 — SMALL\n- HELD list: (empty) — Erik sold all $Max.\n")
  (tmp_path / "config.json").write_text('{"firm_name":"Test Firm","fomo_balance_usd":null,"goal_start_usd":4.0,"goal_multiple":10}')
  vals = {"PHUD_FIRM_DB": str(make_firm(tmp_path)), "PHUD_REPORTS": str(tmp_path / "reports"), "PHUD_INBOX": str(tmp_path / "inbox"),
    "PHUD_RULES": str(tmp_path / "RULES.md"), "PHUD_CONFIG": str(tmp_path / "config.json"), "HUD_CHAT_DB": str(tmp_path / "chat.db"),
    "PHUD_DATA": str(tmp_path / "data"), "PERSONAL_HUD_PASSWORD": PW}
  for k, v in vals.items(): monkeypatch.setenv(k, v)
  for k in ("PHUD_MODE", "RELAY_TOKEN", "PERSONAL_HUD_PUBLIC", "PERSONAL_HUD_SESSION_SECRET"): monkeypatch.delenv(k, raising=False)
  return tmp_path

TOKEN = "t" * 20 + "relay-token-for-tests-0123456789"
MPW = "mirror-password-1234"

@pytest.fixture()
def mserver(env, monkeypatch):
  monkeypatch.setenv("PHUD_MODE", "mirror"); monkeypatch.setenv("RELAY_TOKEN", TOKEN); monkeypatch.setenv("PERSONAL_HUD_PASSWORD", MPW)
  import phud.server as s
  s = importlib.reload(s); s.AUTH = None; s._RATE.clear()
  return s

@pytest.fixture()
def mclient(mserver):
  from fastapi.testclient import TestClient
  with TestClient(mserver.app) as c:
    r = login(c, MPW); assert r.status_code == 303
    c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf"]
    yield c

@pytest.fixture()
def server(env):
  import phud.server as s
  s = importlib.reload(s); s.AUTH = None; s._RATE.clear()
  return s

@pytest.fixture()
def client(server):
  from fastapi.testclient import TestClient
  with TestClient(server.app) as c: yield c

def login(c, pw=PW):
  r = c.get("/login"); pre = re.search(r'name="pre" value="([^"]+)"', r.text).group(1)
  return c.post("/login", data={"pre": pre, "password": pw}, follow_redirects=False)

@pytest.fixture()
def authed(client):
  r = login(client); assert r.status_code == 303
  client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf"]
  return client

import os, subprocess, sys, importlib.util
CHAT = "/workspace/trader/team/hud_chat/chat.py"
def run(env, *a):
  return subprocess.run([sys.executable, CHAT, *a], capture_output=True, text=True, env={**os.environ, "HUD_CHAT_DB": str(env)})
def mod():
  s = importlib.util.spec_from_file_location("hud_chat_t", CHAT); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def test_queue_states(tmp_path, monkeypatch):
  db = tmp_path / "c.db"; monkeypatch.setenv("HUD_CHAT_DB", str(db)); c = mod()
  assert run(db, "pending").stdout.strip() == "NO_PENDING"
  a = c.add_erik("first"); b = c.add_erik("second")
  assert c.counts() == {"queued": 2}
  out = run(db, "pending", "--peek").stdout; assert "first" in out and c.counts() == {"queued": 2}   # peek doesn't mark
  out = run(db, "pending").stdout; assert "2 new message(s)" in out and f"#{a}" in out and f"#{b}" in out
  assert c.counts() == {"delivered": 2}
  assert run(db, "pending").stdout.strip() == "NO_PENDING"                                           # delivered once only
  r = run(db, "reply", "--to", str(a), "answer to first"); assert r.returncode == 0 and f"#{a}" in r.stdout
  assert c.counts() == {"answered": 1, "delivered": 1}
  r = run(db, "reply", "general answer"); assert r.returncode == 0 and f"#{b}" in r.stdout
  assert c.counts() == {"answered": 2}
  h = run(db, "history").stdout; assert "Erik [answered]: first" in h and "Mona: general answer" in h
  assert oct(os.stat(db).st_mode & 0o777) == "0o600"

def test_reply_errors(tmp_path):
  db = tmp_path / "c.db"
  assert run(db, "reply", "").returncode == 2
  assert run(db, "reply", "--to", "42", "x").returncode == 2
  r = run(db, "reply", "unprompted note"); assert r.returncode == 0 and "none open" in r.stdout

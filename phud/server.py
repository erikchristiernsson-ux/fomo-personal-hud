"""Erik's personal HUD: watch-only view of the firm + chat with Mona · Manager.
No trading, deposit, wallet or auto-trade controls exist here.

Two modes (env PHUD_MODE):
- box (default): runs on the box, reads RULES.md / firm.db (read-only) / reports, and writes Erik's chat messages to
  team/hud_chat/chat.db, which a routine relays to Mona (bots can't receive HTTP).
- mirror: runs on Render for the phone. Same UI and auth, but all data is an in-memory snapshot that the box's
  tools/push_agent.py POSTs every 60 s. Chat typed on the phone waits in memory until the push agent pulls it into chat.db.
  Relay endpoints (/api/relay/*) need `Authorization: Bearer <RELAY_TOKEN>` (constant-time compare).
Run: uvicorn phud.server:app --host 127.0.0.1 --port 8792   (password from env PERSONAL_HUD_PASSWORD)"""
import hmac, importlib.util, itertools, os, sys, threading, time
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from phud import auth as authmod   # vendored copy of firm/firm/hud/auth.py (a test checks it matches the box original)
from phud import data

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODE = os.environ.get("PHUD_MODE", "box").strip().lower()
if MODE not in ("box", "mirror"): raise SystemExit("PHUD_MODE must be 'box' or 'mirror'")
STALE_S = int(os.environ.get("PHUD_STALE_S", "180"))
MAX_BODY = 2_000_000

chat = None
if MODE == "box":
  CHAT_PY = os.environ.get("PHUD_CHAT_PY", "/workspace/trader/team/hud_chat/chat.py")
  _spec = importlib.util.spec_from_file_location("hud_chat", CHAT_PY); chat = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(chat)

COOKIE, PRE = "phud_session", "phud_pre"   # own names: cookies are shared across ports on 127.0.0.1
STATIC = os.path.join(HERE, "static")
app = FastAPI(title="Erik's personal HUD", docs_url=None, redoc_url=None, openapi_url=None)
AUTH = None
def get_auth():
  global AUTH
  if AUTH is None:
    env = {"HUD_PASSWORD": os.environ.get("PERSONAL_HUD_PASSWORD", ""), "HUD_PUBLIC": os.environ.get("PERSONAL_HUD_PUBLIC", "")}
    if os.environ.get("PERSONAL_HUD_SESSION_SECRET"): env["HUD_SESSION_SECRET"] = os.environ["PERSONAL_HUD_SESSION_SECRET"]
    AUTH = authmod.Auth(os.environ.get("PHUD_DATA", os.path.join(HERE, "data")), env=env)
  return AUTH

@app.on_event("startup")
def _startup():
  if not get_auth().enabled: raise SystemExit("PERSONAL_HUD_PASSWORD is not set; refusing to start without a password.")
  if MODE == "mirror":
    if len(os.environ.get("RELAY_TOKEN", "")) < 32: raise SystemExit("mirror mode needs RELAY_TOKEN (32+ characters).")
    if len(os.environ.get("PERSONAL_HUD_PASSWORD", "")) < 8: raise SystemExit("mirror mode needs PERSONAL_HUD_PASSWORD of 8+ characters.")

OPEN = {("GET", "/login"), ("POST", "/login"), ("GET", "/healthz"), ("GET", "/static/hud.css")}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
SEC = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
  "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"}

def same_origin(request):
  o = request.headers.get("origin")
  if not o or o == "null": return o is None
  return urlsplit(o).netloc == request.headers.get("host")

def relay_ok(request):
  tok = os.environ.get("RELAY_TOKEN", "")
  h = request.headers.get("authorization", "")
  got = h[7:] if h[:7].lower() == "bearer " else ""
  return len(tok) >= 32 and hmac.compare_digest(got.encode(), tok.encode())

def sec(r):
  for k, v in SEC.items(): r.headers[k] = v
  return r

@app.middleware("http")
async def guard(request: Request, call_next):
  a = get_auth(); path, m = request.url.path, request.method
  if int(request.headers.get("content-length") or 0) > MAX_BODY: return sec(JSONResponse({"error": "body too large"}, 413))
  if path.startswith("/api/relay/"):
    if MODE != "mirror": return sec(JSONResponse({"error": "not found"}, 404))
    if not relay_ok(request): return sec(JSONResponse({"error": "relay token required"}, 401))
    resp = await call_next(request)
    for k, v in SEC.items(): resp.headers.setdefault(k, v)
    return resp
  if (m, path) not in OPEN:
    if not a.enabled: return JSONResponse({"error": "PERSONAL_HUD_PASSWORD not set"}, 503)
    sess = a.session(request.cookies.get(COOKIE))
    if not sess:
      r = JSONResponse({"error": "authentication required"}, 401) if path.startswith("/api") or m != "GET" else RedirectResponse("/login", 303)
      for k, v in SEC.items(): r.headers[k] = v
      return r
    if m in UNSAFE:
      tok = request.headers.get("x-csrf-token", "")
      if not (tok and hmac.compare_digest(tok, sess["csrf"]) and same_origin(request)):
        r = JSONResponse({"error": "CSRF check failed"}, 403)
        for k, v in SEC.items(): r.headers[k] = v
        return r
    request.state.session = sess
  resp = await call_next(request)
  for k, v in SEC.items(): resp.headers.setdefault(k, v)
  return resp

def secure_cookie(request):
  return get_auth().public or request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

def login_page(request, msg="", status=200):
  t, signed = get_auth().new_pre(request.cookies.get(PRE))
  import html as h
  page = open(os.path.join(STATIC, "login.html")).read().replace("{{PRE}}", t).replace("{{MSG}}", h.escape(msg))
  r = HTMLResponse(page, status)
  r.set_cookie(PRE, signed, max_age=1800, httponly=True, secure=secure_cookie(request), samesite="strict", path="/login")
  return r

@app.get("/healthz")
def healthz(): return {"ok": True}

@app.get("/login")
def login_get(request: Request):
  if get_auth().session(request.cookies.get(COOKIE)): return RedirectResponse("/", 303)
  return login_page(request)

@app.post("/login")
async def login_post(request: Request):
  a = get_auth(); ip = authmod.client_ip(request)
  wait = a.blocked(ip)
  if wait: return login_page(request, f"Too many attempts. Try again in {wait // 60 + 1} min.", 429)
  form = await request.form()
  if not same_origin(request) or not a.pre_ok(request.cookies.get(PRE), form.get("pre")):
    return login_page(request, "Session expired. Please try again.", 400)
  pw = form.get("password")
  ok = await run_in_threadpool(a.check_password, pw if isinstance(pw, str) else None)
  del pw
  if not ok:
    left = a.record_fail(ip)
    return login_page(request, "Wrong password." + (f" {left} tries left." if left else " Locked for 15 min."), 401 if left else 429)
  a.record_ok(ip)
  r = RedirectResponse("/", 303)
  r.set_cookie(COOKIE, a.new_session(), max_age=authmod.TTL, httponly=True, secure=secure_cookie(request), samesite="strict", path="/")
  r.delete_cookie(PRE, path="/login")
  return r

@app.post("/logout")
def logout():
  r = JSONResponse({"ok": True}); r.delete_cookie(COOKIE, path="/"); return r

@app.get("/")
def index(): return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-store"})

@app.get("/static/{name}")
def static(name: str):
  if name not in ("hud.css", "hud.js"): raise HTTPException(404)
  return FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-store"})

@app.get("/api/session")
def session(request: Request): return {"csrf": request.state.session["csrf"]}

RELAY_NOTE = "Mona can't receive web messages instantly. A relay checks every 5 minutes; her reply appears here when she answers."

# ---------------- mirror store (in memory; the box is the source of truth for chat) ----------------
class Mirror:
  def __init__(self):
    self.lock = threading.Lock(); self.snap = None; self.received = None
    self.box_msgs = []          # chat history as last pushed by the box (Erik + Mona, with box statuses)
    self.pending = {}           # mirror_id -> msg typed on the phone, not yet seen in box history
    self.seq = itertools.count(1); self.boot = format(int(time.time()), "x")
    self.last_pull = None
  def age(self): return None if self.received is None else int(time.time() - self.received)
MIR = Mirror()

def _iso(t=None): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or time.time()))

def mirror_meta():
  age = MIR.age()
  return {"mode": "mirror", "received": _iso(MIR.received) if MIR.received else None, "age_s": age,
    "stale": age is None or age > STALE_S, "stale_after_s": STALE_S, "last_pull": _iso(MIR.last_pull) if MIR.last_pull else None}

def mirror_counts():
  c = {}
  for m in MIR.box_msgs:
    if m.get("sender") == "erik": c[m["status"]] = c.get(m["status"], 0) + 1
  for m in MIR.pending.values(): c[m["status"]] = c.get(m["status"], 0) + 1
  return c

def chat_view(after=0, n=80):
  if MODE == "mirror":
    with MIR.lock:
      msgs = [dict(m) for m in MIR.box_msgs][-n:] + [dict(m) for m in sorted(MIR.pending.values(), key=lambda x: x["ts"])]
      return {"messages": msgs, "counts": mirror_counts(), "relay_note": RELAY_NOTE + " From the phone, add up to 1 minute for the box to pick it up.", "mirror": mirror_meta()}
  msgs = chat.history(n=n, after=after)
  return {"messages": msgs, "counts": chat.counts(), "relay_note": RELAY_NOTE}

@app.get("/api/state")
def state():
  if MODE == "mirror":
    with MIR.lock:
      s = dict(MIR.snap) if MIR.snap else data.empty_snapshot()
      s["chat"] = {"counts": mirror_counts()}
    s["mirror"] = mirror_meta()
    return s
  s = data.snapshot()
  try: s["chat"] = {"counts": chat.counts()}
  except Exception: s["chat"] = {"counts": None, "error": "chat store unavailable"}
  s["mirror"] = {"mode": "box"}
  return s

@app.get("/api/chat")
def chat_get(after: int = 0):
  if after < 0: raise HTTPException(400, "after must be >= 0")
  return chat_view(after)

class Msg(BaseModel):
  text: str

_RATE = []
@app.post("/api/chat")
def chat_post(b: Msg):
  now = time.time()
  while _RATE and _RATE[0] < now - 3600: _RATE.pop(0)
  if len(_RATE) >= 60: raise HTTPException(429, "Too many messages this hour.")
  text = (b.text or "").strip()
  if not text: raise HTTPException(400, "empty message")
  if len(text) > 4000: raise HTTPException(400, "message longer than 4000 characters")
  if MODE == "mirror":
    with MIR.lock:
      if len(MIR.pending) >= 200: raise HTTPException(503, "Too many messages waiting for the box; it looks offline.")
      mid = f"{MIR.boot}-{next(MIR.seq)}"
      MIR.pending[mid] = {"id": f"m:{mid}", "mirror_id": mid, "ts": _iso(now), "sender": "erik", "text": text, "status": "waiting_box"}
    _RATE.append(now)
    return {"ok": True, "id": f"m:{mid}", "status": "waiting_box"}
  try: mid = chat.add_erik(text)
  except ValueError as e: raise HTTPException(400, str(e))
  _RATE.append(now)
  return {"ok": True, "id": mid, "status": "queued"}

# ---------------- relay endpoints (mirror only; bearer RELAY_TOKEN, enforced in guard) ----------------
class Snap(BaseModel):
  snapshot: dict

@app.post("/api/relay/snapshot")
def relay_snapshot(b: Snap):
  s = b.snapshot
  for k in ("balance", "goal", "rules", "engine", "bots", "closed", "feed"):
    if k not in s: raise HTTPException(400, f"snapshot missing '{k}'")
  with MIR.lock: MIR.snap = s; MIR.received = time.time()
  return {"ok": True, "received": _iso(MIR.received)}

@app.get("/api/relay/pending")
def relay_pending():
  """Erik's phone messages not yet confirmed by the box. Marked 'relayed'; served again until the box's chat history
  contains their mirror_id (the box insert is idempotent), so a crashed push cycle can't lose a message."""
  with MIR.lock:
    MIR.last_pull = time.time()
    out = [{"mirror_id": m["mirror_id"], "ts": m["ts"], "text": m["text"]} for m in sorted(MIR.pending.values(), key=lambda x: x["ts"])]
    for m in MIR.pending.values(): m["status"] = "relayed"
  return {"messages": out}

class BoxChat(BaseModel):
  messages: list

ALLOWED_STATUS = {"queued", "delivered", "answered", "sent"}
@app.post("/api/relay/chat")
def relay_chat(b: BoxChat):
  """The box pushes its chat history (Erik's messages with their status, Mona's replies). It replaces the mirror's view."""
  clean = []
  for m in b.messages[-200:]:
    if not isinstance(m, dict): continue
    if m.get("sender") not in ("erik", "mona") or m.get("status") not in ALLOWED_STATUS or not isinstance(m.get("text"), str): continue
    clean.append({"id": int(m.get("id") or 0), "ts": str(m.get("ts") or ""), "sender": m["sender"], "text": m["text"][:4000],
      "status": m["status"], "mirror_id": m.get("mirror_id")})
  with MIR.lock:
    MIR.box_msgs = clean
    acked = {m["mirror_id"] for m in clean if m.get("mirror_id")}
    for k in [k for k in MIR.pending if k in acked]: MIR.pending.pop(k)
  return {"ok": True, "messages": len(clean), "acked": len(acked), "waiting": len(MIR.pending)}

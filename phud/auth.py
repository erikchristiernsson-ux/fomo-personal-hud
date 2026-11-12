"""HUD authentication: password login, signed session cookie, CSRF, login rate limiting.
- Password comes only from env HUD_PASSWORD. It is never logged, printed or written anywhere. At startup it is
  turned into an in-memory scrypt hash (random salt); logins are checked with scrypt + hmac.compare_digest.
- Session: HMAC-SHA256 signed cookie (HttpOnly, Secure, SameSite=Strict), 12 h lifetime; secret in data/session_secret (0600).
- CSRF: every POST/PUT/PATCH/DELETE needs header X-CSRF-Token equal to the session's token, and a same-origin Origin if sent.
- Login: 5 failed tries per IP per 15 min -> locked for 15 min; plus a global cap of 30 failures / 15 min."""
import base64, hashlib, hmac, json, os, secrets, threading, time
from collections import defaultdict, deque

COOKIE, PRE = "hud_session", "hud_pre"
TTL = 12 * 3600
MIN_PUBLIC_LEN = 10
WINDOW, PER_IP, GLOBAL = 15 * 60, 5, 30

class Auth:
  def __init__(self, data_dir, env=os.environ):
    pw = env.get("HUD_PASSWORD")
    self.enabled = bool(pw)
    self.public = env.get("HUD_PUBLIC") == "1"
    self.pw_len_ok = bool(pw) and len(pw) >= MIN_PUBLIC_LEN
    if self.public and not self.pw_len_ok:
      raise SystemExit(f"Refusing to start in public mode: HUD_PASSWORD must be set and at least {MIN_PUBLIC_LEN} characters.")
    self._salt = secrets.token_bytes(16)
    self._hash = self._kdf(pw) if pw else None
    del pw
    os.makedirs(data_dir, exist_ok=True)
    if env.get("HUD_SESSION_SECRET") and len(env["HUD_SESSION_SECRET"]) >= 32:   # optional: keeps sessions across cloud restarts
      self.secret = env["HUD_SESSION_SECRET"].encode()
      self.fail_ip = defaultdict(deque); self.fail_all = deque(); self.locked = {}; self.lock = threading.Lock(); return
    p = os.path.join(data_dir, "session_secret")
    if not os.path.exists(p):
      fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
      with os.fdopen(fd, "w") as f: f.write(secrets.token_hex(32))
    os.chmod(p, 0o600)
    self.secret = open(p).read().strip().encode()
    self.fail_ip = defaultdict(deque); self.fail_all = deque(); self.locked = {}; self.lock = threading.Lock()

  def _kdf(self, pw):
    return hashlib.scrypt(pw.encode(), salt=self._salt, n=2 ** 14, r=8, p=1, dklen=32)

  def check_password(self, pw):
    if not self._hash or not isinstance(pw, str) or len(pw) > 1024: 
      self._kdf("x" * 12); return False   # same cost either way
    return hmac.compare_digest(self._kdf(pw), self._hash)

  # ---- signed tokens ----
  def _sign(self, obj):
    b = base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")
    s = hmac.new(self.secret, b.encode(), hashlib.sha256).hexdigest()
    return f"{b}.{s}"
  def _verify(self, tok):
    try:
      b, s = tok.rsplit(".", 1)
      if not hmac.compare_digest(hmac.new(self.secret, b.encode(), hashlib.sha256).hexdigest(), s): return None
      obj = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
      return obj if obj.get("exp", 0) > time.time() else None
    except Exception: return None

  def new_session(self):
    return self._sign({"sid": secrets.token_hex(16), "csrf": secrets.token_hex(16), "exp": int(time.time()) + TTL, "k": "s"})
  def session(self, cookie):
    s = self._verify(cookie) if cookie else None
    return s if s and s.get("k") == "s" else None
  def new_pre(self, existing=None):
    """Reuse a still-valid pre-login token (a page loaded twice must not invalidate the first form)."""
    p = self._verify(existing) if existing else None
    if p and p.get("k") == "p" and p.get("exp", 0) - time.time() > 300: return p["pre"], existing
    t = secrets.token_hex(16); return t, self._sign({"pre": t, "exp": int(time.time()) + 1800, "k": "p"})
  def pre_ok(self, cookie, form_token):
    p = self._verify(cookie) if cookie else None
    return bool(p and p.get("k") == "p" and isinstance(form_token, str) and hmac.compare_digest(p["pre"], form_token))

  # ---- rate limiting ----
  def _trim(self, dq, now):
    while dq and dq[0] < now - WINDOW: dq.popleft()
  def blocked(self, ip):
    now = time.time()
    with self.lock:
      until = self.locked.get(ip, 0)
      if until > now: return int(until - now)
      self._trim(self.fail_all, now)
      if len(self.fail_all) >= GLOBAL: return int(self.fail_all[0] + WINDOW - now) + 1
      return 0
  def record_fail(self, ip):
    now = time.time()
    with self.lock:
      dq = self.fail_ip[ip]; self._trim(dq, now); dq.append(now); self.fail_all.append(now)
      if len(dq) >= PER_IP: self.locked[ip] = now + WINDOW
      return max(0, PER_IP - len(dq))
  def record_ok(self, ip):
    with self.lock: self.fail_ip.pop(ip, None); self.locked.pop(ip, None)

def client_ip(request):
  """Behind a trusted platform proxy (TRUST_PROXY=1, e.g. Render/HF/Koyeb) use the proxy's client-IP header, else the
  right-most X-Forwarded-For entry (the one the proxy appended; left-most entries can be forged by the client).
  Without TRUST_PROXY, only a local tunnel (peer 127.0.0.1) may supply CF-Connecting-IP."""
  peer = request.client.host if request.client else "?"
  if os.environ.get("TRUST_PROXY") == "1":
    h = request.headers
    xff = [x.strip() for x in h.get("x-forwarded-for", "").split(",") if x.strip()]
    return h.get("cf-connecting-ip") or h.get("true-client-ip") or (xff[-1] if xff else peer)
  if peer in ("127.0.0.1", "::1"):
    return request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip() or peer
  return peer

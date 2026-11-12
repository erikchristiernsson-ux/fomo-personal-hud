"""Read-only snapshot of the firm for Erik's personal HUD. Never writes to firm.db; never invents values:
anything we can't read is returned as None and the UI shows 'missing'."""
import datetime as dt, glob, json, os, re, sqlite3, time

TRADER = "/workspace/trader"
def P(name, default): return os.environ.get(name) or default
def firm_db(): return P("PHUD_FIRM_DB", f"{TRADER}/firm/data/firm.db")
def reports_dir(): return P("PHUD_REPORTS", f"{TRADER}/team/reports")
def inbox_dir(): return P("PHUD_INBOX", f"{TRADER}/team/inbox")
def rules_path(): return P("PHUD_RULES", f"{TRADER}/team/RULES.md")
def config_path(): return P("PHUD_CONFIG", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"))

BOTS = [  # order shown in the HUD
  {"key": "MANAGER",   "name": "Mona",   "role": "Manager · runs the team",       "report": "MANAGER.md",   "engine": None},
  {"key": "TRADER",    "name": "Trader", "role": "Desk lead · BUY alerts",         "report": "TRADER.md",    "engine": "LOGGER"},
  {"key": "SCOUT",     "name": "Sasha",  "role": "Scout · market sweep",           "report": "SCOUT.md",     "engine": "SCOUT"},
  {"key": "SOCIALS",   "name": "Gigi",   "role": "Socials · X & news",             "report": "SOCIALS.md",   "engine": "SOCIALS"},
  {"key": "FILTER",    "name": "Bruno",  "role": "Filter · safety gates",          "report": "FILTER.md",    "engine": "FILTER"},
  {"key": "RUGHUNTER", "name": "Rex",    "role": "Rug Hunter · SAFE/WAIT/RUG",     "report": "RUGHUNTER.md", "engine": None},
  {"key": "ANALYST",   "name": "Quinn",  "role": "Analyst · rubric score",         "report": "ANALYST.md",   "engine": "ANALYST"},
  {"key": "RISK",      "name": "Viktor", "role": "Risk · veto & exits",            "report": "RISK.md",      "engine": "RISK"},
  {"key": "BUILDER",   "name": "Bob",    "role": "Builder · system & tools",       "report": "BUILDER.md",   "engine": None},
]
# handoff file names: inbox/<TO_ROLE>/<YYYYMMDD-HHMM>-<from>.json ; map <from> to a bot key
FROM_ALIASES = {"scout": "SCOUT", "sasha": "SCOUT", "gigi": "SOCIALS", "socials": "SOCIALS", "filter": "FILTER", "bruno": "FILTER",
  "rughunter": "RUGHUNTER", "rex": "RUGHUNTER", "analyst": "ANALYST", "quinn": "ANALYST", "risk": "RISK", "viktor": "RISK",
  "trader": "TRADER", "mona": "MANAGER", "manager": "MANAGER", "bob": "BUILDER", "builder": "BUILDER"}
NAME = {b["key"]: b["name"] for b in BOTS}

def iso(ts): return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def parse_iso(s):
  try: return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
  except Exception: return None

# ---------------- RULES.md ----------------
def read_rules():
  out = {"balance_usd": None, "balance_source": None, "rules_version": None, "held": None, "alert_mode": False, "source": rules_path()}
  try: txt = open(rules_path(), encoding="utf-8", errors="replace").read()
  except Exception: return out
  m = re.search(r"Fomo balance for sizing:\s*\$([0-9]+(?:\.[0-9]+)?)", txt)
  if m: out["balance_usd"] = float(m.group(1)); out["balance_source"] = "team/RULES.md"
  vs = re.findall(r"^#+\s*RULES\s+v(\d+(?:\.\d+)*)", txt, re.M) or re.findall(r"team rules \(v(\d+(?:\.\d+)*)", txt)
  if vs: out["rules_version"] = vs[-1]
  out["alert_mode"] = bool(re.search(r"^##\s*ALERT MODE", txt, re.M))
  hm = re.findall(r"^-\s*HELD list:\s*(.+)$", txt, re.M)
  if hm:
    h = hm[-1].strip()
    if re.match(r"\(?\s*empty\s*\)?", h, re.I) or h.lower().startswith("none"):
      out["held"] = []; out["held_note"] = re.sub(r"^\(?\s*empty\s*\)?\s*[—-]*\s*", "", h, flags=re.I)
    else:
      out["held"] = [x.strip() for x in re.split(r"[;,]", h) if x.strip()]; out["held_note"] = ""
  return out

def read_config():
  try: return json.load(open(config_path()))
  except Exception: return {}

# ---------------- firm.db (read-only) ----------------
def ro():
  p = firm_db()
  if not os.path.exists(p): return None
  c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5); c.row_factory = sqlite3.Row
  return c

def q(c, sql, args=()):
  try: return [dict(r) for r in c.execute(sql, args)]
  except Exception: return []

def kv(c, k):
  r = q(c, "SELECT v FROM kv WHERE k=?", (k,))
  try: return json.loads(r[0]["v"]) if r else None
  except Exception: return None

def pid_alive(pid, needle=b"firm.orchestrator"):
  try:
    pid = int(pid); os.kill(pid, 0)
    with open(f"/proc/{pid}/cmdline", "rb") as f: return needle in f.read()
  except Exception: return False

def engine(c):
  if c is None: return {"available": False}
  meta = kv(c, "meta") or {}; hb = kv(c, "orch_heartbeat"); rhb = kv(c, "risk_heartbeat")
  eq = q(c, "SELECT ts,equity,cash,unrealized FROM equity ORDER BY id DESC LIMIT 1")
  hist = q(c, "SELECT ts,equity FROM equity ORDER BY id DESC LIMIT 120")[::-1]
  open_pos = q(c, "SELECT id,symbol,mint,opened,size_usd,entry_price,last_price,last_value_usd FROM positions WHERE status='open' ORDER BY id")
  age = (time.time() - parse_iso(hb)) if hb and parse_iso(hb) else None
  alive = pid_alive(meta.get("pid", 0))
  return {"available": True, "alive": alive, "pid": meta.get("pid"), "heartbeat": hb, "heartbeat_age_s": int(age) if age is not None else None,
    "risk_heartbeat": rhb, "rules_version": meta.get("rules_version"), "rules_mode": meta.get("rules_mode"), "mode": meta.get("mode"),
    "equity": eq[0] if eq else None, "equity_history": hist, "open_positions": open_pos,
    "starting_equity": meta.get("starting_equity"), "active_role": kv(c, "active_role"),
    "status": ("ok" if alive and age is not None and age < 600 else "stale" if alive else "down")}

def closed_trades(c, n=30):
  if c is None: return {"available": False, "items": [], "count": None}
  items = q(c, """SELECT id,symbol,mint,opened,closed,size_usd,entry_price,exit_price,fee_in_usd,fee_out_usd,pnl_usd,exit_reason,rules_version
    FROM positions WHERE status='closed' ORDER BY closed DESC, id DESC LIMIT ?""", (n,))
  cnt = q(c, "SELECT COUNT(*) n, SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w, SUM(pnl_usd) p FROM positions WHERE status='closed'")
  for i in items:
    i["pnl_pct"] = (i["pnl_usd"] / i["size_usd"] * 100) if i.get("pnl_usd") is not None and i.get("size_usd") else None
  s = cnt[0] if cnt else {}
  return {"available": True, "items": items, "count": s.get("n") or 0, "wins": s.get("w") or 0, "pnl_total": s.get("p")}

def role_status(c):
  return {r["role"]: r for r in q(c, "SELECT role,name,state,last_output,updated FROM role_status")} if c else {}

# ---------------- reports + handoffs ----------------
STRIP = re.compile(r"[*_`>#]+")
def action_line(path, max_len=170):
  """First meaningful status line of a report: prefer State:/Status: lines, else the first non-heading line."""
  try:
    with open(path, encoding="utf-8", errors="replace") as f: head = f.read(6000)
  except Exception: return None
  lines = [l.strip() for l in head.splitlines()]
  def clean(l):
    l = STRIP.sub("", l).strip(" -|")
    l = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", l)
    return re.sub(r"\s+", " ", l).strip()
  cand = None
  for l in lines:
    if not l or l.startswith("#") or l.startswith("|") or l.startswith("```"): continue
    c = clean(l)
    if not c or re.match(r"^(updated|mode|role|desk)\s*:", c, re.I): continue
    if re.match(r"^(state|status)\s*:", c, re.I): cand = c; break
    if cand is None: cand = c
  if cand and len(cand) > max_len: cand = cand[:max_len - 1].rstrip() + "…"
  return cand

def last_section(path, max_len=90):
  """Heading of the last '## ' section (many reports append newest sections at the bottom)."""
  try:
    with open(path, "rb") as f:
      f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 60000)); tail = f.read().decode("utf-8", "replace")
  except Exception: return None
  hs = re.findall(r"^##\s+(.+)$", tail, re.M)
  if not hs: return None
  h = STRIP.sub("", hs[-1]).strip()
  return h if len(h) <= max_len else h[:max_len - 1] + "…"

def handoffs(hours=12):
  """Handoff files from inbox/<TO>/<YYYYMMDD-HHMM>-<from>.json; times come from the file mtime (real), not the name."""
  out = []; cutoff = time.time() - hours * 3600
  for p in glob.glob(os.path.join(inbox_dir(), "*", "*.json")):
    try: mt = os.path.getmtime(p)
    except Exception: continue
    to = os.path.basename(os.path.dirname(p)).upper()
    m = re.match(r"^\d{8}-\d{4}-(.+)\.json$", os.path.basename(p))
    frm = FROM_ALIASES.get((m.group(1) if m else "").lower())
    out.append({"path": p, "file": os.path.basename(p), "to": to, "from": frm, "mtime": mt, "recent": mt >= cutoff})
  return sorted(out, key=lambda x: x["mtime"])

def bots(c, hs):
  rs = role_status(c); now = time.time(); hours = 12
  out = []
  for b in BOTS:
    p = os.path.join(reports_dir(), b["report"])
    mt = os.path.getmtime(p) if os.path.exists(p) else None
    age = int(now - mt) if mt else None
    tag = "missing" if mt is None else "live" if age < 45 * 60 else "idle" if age < 4 * 3600 else "stale"
    spark = [0] * hours
    for h in hs:
      if h["from"] == b["key"] or h["to"] == b["key"]:
        i = int((now - h["mtime"]) // 3600)
        if 0 <= i < hours: spark[hours - 1 - i] += 1
    eng = rs.get(b["engine"]) if b["engine"] else None
    out.append({**b, "report_path": p, "report_exists": mt is not None, "updated": iso(mt) if mt else None, "age_s": age, "tag": tag,
      "action": action_line(p) if mt else None, "latest_section": last_section(p) if mt else None, "handoffs_12h": spark, "handoffs_total_12h": sum(spark),
      "engine": {"role": b["engine"], "state": eng["state"], "last_output": eng["last_output"], "updated": eng["updated"]} if eng else None})
  return out

# ---------------- events feed (for toasts) ----------------
def feed(c, hs, n=40):
  items = []
  for e in q(c, "SELECT id,ts,kind,actor,detail FROM events WHERE kind!='auth' ORDER BY id DESC LIMIT ?", (n,)) if c else []:
    items.append({"id": f"ev:{e['id']}", "ts": e["ts"], "kind": e["kind"], "title": e["kind"].replace("_", " ").title(), "detail": e["detail"], "who": e["actor"]})
  for b in BOTS:
    p = os.path.join(reports_dir(), b["report"])
    if os.path.exists(p):
      mt = os.path.getmtime(p)
      items.append({"id": f"rp:{b['key']}:{int(mt)}", "ts": iso(mt), "kind": "report", "title": f"{b['name']} updated their report",
        "detail": action_line(p) or "", "who": b["name"]})
  for h in hs[-25:]:
    items.append({"id": f"ib:{h['to']}:{h['file']}", "ts": iso(h["mtime"]), "kind": "handoff",
      "title": f"Handoff {NAME.get(h['from'], h['from'] or '?')} → {NAME.get(h['to'], h['to'].title())}", "detail": h["file"], "who": NAME.get(h["from"]) or ""})
  items.sort(key=lambda x: x["ts"] or "", reverse=True)
  return items[:n]

def snapshot():
  rules = read_rules(); cfg = read_config()
  bal, src = rules["balance_usd"], rules["balance_source"]
  if isinstance(cfg.get("fomo_balance_usd"), (int, float)):
    bal, src = float(cfg["fomo_balance_usd"]), f"config.json (set {cfg.get('fomo_balance_set') or 'date missing'})"
  start = cfg.get("goal_start_usd"); mult = cfg.get("goal_multiple", 10)
  c = None
  try: c = ro()
  except Exception: c = None
  try:
    eng = engine(c); hs = handoffs(); closed = closed_trades(c)
    data = {"generated": iso(time.time()), "firm": cfg.get("firm_name", "FOMO Trading Firm"),
      "balance": {"usd": bal, "source": src},
      "goal": {"start_usd": start, "multiple": mult, "target_usd": (start * mult) if start else None,
               "current_multiple": (bal / start) if (bal is not None and start) else None,
               "ticks": [2, 5, 10], "label": f"Goal: {mult}× the ${start:.2f} starting Fomo balance" if start else "Goal: missing start balance"},
      "rules": rules, "engine": eng, "bots": bots(c, hs), "closed": closed, "feed": feed(c, hs)}
  finally:
    if c: c.close()
  return data

def empty_snapshot():
  """What the mirror serves before the box has pushed anything: every value missing, never invented."""
  return {"generated": None, "firm": "FOMO Trading Firm", "balance": {"usd": None, "source": None},
    "goal": {"start_usd": None, "multiple": 10, "target_usd": None, "current_multiple": None, "ticks": [2, 5, 10], "label": "Goal: missing (no data from the box yet)"},
    "rules": {"balance_usd": None, "rules_version": None, "held": None, "alert_mode": None},
    "engine": {"available": False}, "bots": [], "closed": {"available": False, "items": [], "count": None}, "feed": []}

def public_snapshot():
  """snapshot() minus box file paths, for pushing to the Render mirror."""
  s = snapshot()
  s["rules"].pop("source", None)
  for b in s["bots"]: b.pop("report_path", None)
  return s

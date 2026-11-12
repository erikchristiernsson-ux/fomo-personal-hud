"""Headless screenshots of the running personal HUD (desktop 1280x800 + phone 390x844). Reads the password from
env PERSONAL_HUD_PASSWORD or ./.password; never prints it."""
import os, sys
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get("PHUD_URL", "http://127.0.0.1:8792")
pw = os.environ.get("PERSONAL_HUD_PASSWORD") or open(os.path.join(HERE, ".password")).read().strip()
out = os.path.join(HERE, "shots"); os.makedirs(out, exist_ok=True)
PFX = os.environ.get("PHUD_SHOT_PREFIX", "")
with sync_playwright() as p:
  b = p.chromium.launch(executable_path=os.environ.get("CHROME", "/usr/bin/google-chrome"), args=["--no-sandbox"])
  for name, vp, mobile in (("desktop", {"width": 1280, "height": 800}, False), ("phone", {"width": 390, "height": 844}, True)):
    ctx = b.new_context(viewport=vp, device_scale_factor=2 if mobile else 1, is_mobile=mobile, has_touch=mobile)
    pg = ctx.new_page(); errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL + "/login"); pg.screenshot(path=f"{out}/{PFX}{name}_login.png")
    pg.fill("#pw", pw); pg.click("button[type=submit]"); pg.wait_for_url(URL + "/"); pg.wait_for_selector("#bots > *", timeout=10000)
    pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{out}/{PFX}{name}.png")
    pg.screenshot(path=f"{out}/{PFX}{name}_full.png", full_page=True)
    if len(sys.argv) > 1 and sys.argv[1] == "toast":
      pg.evaluate("toast('Handoff Sasha → Bruno','20261112-1238-scout.json (demo toast)')"); pg.wait_for_timeout(600); pg.screenshot(path=f"{out}/{PFX}{name}_toast.png")
    if mobile:
      for sec in ("#bots", "#chatCard", ".closed"):
        pg.eval_on_selector(sec, "e=>e.scrollIntoView({block:'start'})"); pg.evaluate("window.scrollBy(0,-110)"); pg.wait_for_timeout(300)
        pg.screenshot(path=f"{out}/{PFX}{name}_{sec.strip('#.').lower()}.png")
    print(name, "console errors:", errs or "none")
    ctx.close()
  b.close()

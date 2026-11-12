# Erik's personal HUD, MIRROR mode (phone version on Render). Watch-only + chat relay; no trading code, no keys.
# Data arrives from the box's push agent (tools/push_agent.py) via /api/relay/* with a RELAY_TOKEN bearer header.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PHUD_MODE=mirror PERSONAL_HUD_PUBLIC=1 TRUST_PROXY=1 PORT=10000 PHUD_DATA=/tmp/phud
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m -u 1000 app
COPY --chown=app:app phud ./phud
COPY --chown=app:app static ./static
USER app
EXPOSE 10000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','10000'),timeout=4)" || exit 1
CMD ["sh", "-c", "exec python -m uvicorn phud.server:app --host 0.0.0.0 --port ${PORT:-10000} --no-server-header --no-access-log --proxy-headers --forwarded-allow-ips='*'"]

#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh — runs ON the VM (Linux). Uploaded by deploy.ps1 from Windows, or
# run manually after SSH-ing in:   cd ~/meatyboys && bash deploy.sh
#
# Sets up a venv, installs deps, (re)starts gunicorn serving api.index:app,
# fronts it with an nginx reverse proxy on :80, and installs a cron that pings
# the in-app scheduler (/api/cron/tick).
# ─────────────────────────────────────────────────────────────────────────────
set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

# ── env ───────────────────────────────────────────────────────────────────────
[ -f .env ] || { echo "[error] .env not found in $APP_DIR (copy .env.example)"; exit 1; }
# Export only KEY=VALUE lines — strip CR and ignore comments/blanks/stray text so
# a Windows-edited .env can't break sourcing (e.g. a 'python -c ...' comment line).
set -a
. <(sed 's/\r$//' .env | grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=')
set +a
APP_HOST="${HOST:-127.0.0.1}"
APP_PORT="${PORT:-5000}"

# ── virtual environment ───────────────────────────────────────────────────────
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    echo "[setup] Installing python3-venv..."
    apt-get update -qq && apt-get install -y python3-venv
fi
[ -f .venv/bin/activate ] || { echo "[setup] Creating venv..."; rm -rf .venv; python3 -m venv .venv; }
source .venv/bin/activate

echo "[setup] Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# ── database ──────────────────────────────────────────────────────────────────
# The real DB is SCP'd by deploy.ps1. If it's genuinely missing, seed a mock one
# so the app still boots.
DB_FILE="${DB_PATH:-fantasy_2025_26.db}"
if [ "${DB_TYPE:-sqlite}" = "sqlite" ] && [ ! -f "$DB_FILE" ]; then
    echo "[setup] $DB_FILE missing — seeding a mock DB..."
    DB_PATH="$DB_FILE" python3 -m api.seed_mock
fi

# ── cron: ping the unified tick endpoint (scheduler decides what's due) ────────
echo "[setup] Installing cron..."
(crontab -l 2>/dev/null | grep -v 'meatyboys-cron') | crontab - || true
(crontab -l 2>/dev/null; cat <<CRON
# meatyboys-cron — the in-app scheduler decides which ingestion jobs run.
*/10 * * * * curl -fsS -m 90 -H "Authorization: Bearer ${CRON_SECRET}" http://127.0.0.1:${APP_PORT}/api/cron/tick >> ${APP_DIR}/cron.log 2>&1
CRON
) | crontab -

# ── (re)start gunicorn ────────────────────────────────────────────────────────
echo "[deploy] Restarting gunicorn on ${APP_HOST}:${APP_PORT}..."
pkill -f "gunicorn.*api.index:app" 2>/dev/null || true
sleep 1
# 1 worker + threads keeps SQLite writes single-process safe.
nohup .venv/bin/gunicorn \
    -w 1 --threads 4 \
    -b "${APP_HOST}:${APP_PORT}" \
    --access-logfile "$APP_DIR/gunicorn.log" \
    api.index:app \
    >> "$APP_DIR/gunicorn.log" 2>&1 &
sleep 1

# ── nginx reverse proxy on :80 ────────────────────────────────────────────────
echo "[setup] Configuring nginx (port 80 → gunicorn ${APP_PORT})..."
command -v nginx >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y nginx; }
sed "s/__APP_PORT__/${APP_PORT}/g" "$APP_DIR/nginx/meatyboys.conf" \
    > /etc/nginx/sites-available/meatyboys
ln -sf /etc/nginx/sites-available/meatyboys /etc/nginx/sites-enabled/meatyboys
rm -f /etc/nginx/sites-enabled/default
nginx -t                                            # fail fast on bad config
systemctl enable nginx >/dev/null 2>&1 || true
systemctl reload nginx 2>/dev/null || systemctl start nginx

echo ""
echo "✓ Deployed."
echo "  Internal: http://${APP_HOST}:${APP_PORT}"
echo "  Public:   http://$(hostname -I | awk '{print $1}')/"
echo "  App log:  tail -f $APP_DIR/gunicorn.log"
echo "  Cron log: tail -f $APP_DIR/cron.log"

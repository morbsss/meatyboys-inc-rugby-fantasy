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
# Parse KEY=VALUE lines WITHOUT executing them, so values with spaces or other
# shell-special characters are safe (e.g. DOMAIN=meatyboys.com www.meatyboys.com).
# Sourcing would try to run the space-separated remainder as a command. Strips CR,
# skips comments/blanks/stray lines, and trims one layer of surrounding quotes.
while IFS= read -r _line || [ -n "$_line" ]; do
    _line="${_line%$'\r'}"
    case "$_line" in ''|'#'*) continue ;; esac
    case "$_line" in [A-Za-z_]*=*) ;; *) continue ;; esac
    _key="${_line%%=*}"; _val="${_line#*=}"
    case "$_val" in
        \"*\") _val="${_val#\"}"; _val="${_val%\"}" ;;
        \'*\') _val="${_val#\'}"; _val="${_val%\'}" ;;
    esac
    export "$_key=$_val"
done < .env
unset _line _key _val
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
# Strip BOTH the marker comment and the curl line itself. Filtering on
# 'meatyboys-cron' alone only matched the comment, so every deploy left the
# previous schedule behind and the crontab grew by one line each time (18 by the
# time it was noticed, 6 of them still using a rotated CRON_SECRET → 401s).
(crontab -l 2>/dev/null | grep -v 'meatyboys-cron' | grep -v '/api/cron/tick') | crontab - || true
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

# ── nginx reverse proxy + optional HTTPS ──────────────────────────────────────
echo "[setup] Configuring nginx (port 80 → gunicorn ${APP_PORT})..."
command -v nginx >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y nginx; }
NGINX_CONF=/etc/nginx/sites-available/meatyboys
SERVER_NAME="${DOMAIN:-_}"

# Once certbot has taken over the config (HTTPS), leave it untouched on redeploys
# so the TLS server block isn't clobbered. Delete the file + redeploy to reset.
if grep -q "managed by Certbot" "$NGINX_CONF" 2>/dev/null; then
    echo "[setup] nginx config is certbot-managed (HTTPS) — leaving it in place."
else
    sed -e "s/__APP_PORT__/${APP_PORT}/g" -e "s|__SERVER_NAME__|${SERVER_NAME}|g" \
        "$APP_DIR/nginx/meatyboys.conf" > "$NGINX_CONF"
    ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/meatyboys
    rm -f /etc/nginx/sites-enabled/default
fi
nginx -t                                            # fail fast on bad config
systemctl enable nginx >/dev/null 2>&1 || true
systemctl reload nginx 2>/dev/null || systemctl start nginx

# Open the firewall for web traffic (no-op if ufw isn't installed).
command -v ufw >/dev/null 2>&1 && { ufw allow 80/tcp; ufw allow 443/tcp; } >/dev/null 2>&1 || true

# ── HTTPS via Let's Encrypt (first-time setup only) ───────────────────────────
# Runs once — when a real DOMAIN is set and the config isn't yet certbot-managed.
# CERTBOT_EMAIL is OPTIONAL: given → used for renewal-failure alerts; blank →
# registers without an email (auto-renewal still works via the systemd timer, but
# you won't be emailed if a renewal ever fails). certbot rewrites nginx for TLS +
# an HTTP→HTTPS redirect. Non-fatal: a failure leaves the site up on HTTP.
if [ -n "${DOMAIN:-}" ] && [ "${DOMAIN}" != "_" ] \
   && ! grep -q "managed by Certbot" "$NGINX_CONF" 2>/dev/null; then
    echo "[setup] Obtaining HTTPS certificate for: ${DOMAIN}"
    command -v certbot >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y certbot python3-certbot-nginx; }
    CB_ARGS=""; for d in ${DOMAIN}; do CB_ARGS="${CB_ARGS} -d ${d}"; done
    if [ -n "${CERTBOT_EMAIL:-}" ]; then
        EMAIL_ARG="-m ${CERTBOT_EMAIL}"
    else
        EMAIL_ARG="--register-unsafely-without-email"
        echo "[setup] No CERTBOT_EMAIL set — registering without an email (no renewal-failure alerts)."
    fi
    certbot --nginx ${CB_ARGS} --non-interactive --agree-tos ${EMAIL_ARG} --redirect \
        || echo "[warn] certbot failed — site still on HTTP. Check DNS + ports 80/443, then re-run deploy."
    systemctl reload nginx 2>/dev/null || true
fi

echo ""
echo "✓ Deployed."
echo "  Internal: http://${APP_HOST}:${APP_PORT}"
echo "  Public:   http://$(hostname -I | awk '{print $1}')/"
echo "  App log:  tail -f $APP_DIR/gunicorn.log"
echo "  Cron log: tail -f $APP_DIR/cron.log"

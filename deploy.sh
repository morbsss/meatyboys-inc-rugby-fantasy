#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh - runs ON the VM (Linux). Uploaded by deploy.ps1 from Windows, or
# run manually after SSH-ing in:   cd ~/meatyboys && bash deploy.sh
#
# Sets up a venv, installs deps, (re)starts gunicorn serving api.index:app,
# fronts it with an nginx reverse proxy on :80, and installs a cron that pings
# the in-app scheduler (/api/cron/tick).
# ─────────────────────────────────────────────────────────────────────────────
set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

# ── deploy source guard ───────────────────────────────────────────────────────
# Deploys come from CI only (.github/workflows/deploy.yml), so whatever is
# running on the VM always corresponds to a commit on main that passed its
# tests. A laptop deploy would overwrite that with someone's working tree, with
# no record of what actually shipped.
#
# Break-glass for an outage when CI itself is unavailable:
#     ALLOW_MANUAL_DEPLOY=1 bash deploy.sh
# Use it only to restore service, then push the same code through CI so the VM
# and main agree again.
if [ "${CI_DEPLOY:-}" != "1" ] && [ "${ALLOW_MANUAL_DEPLOY:-}" != "1" ]; then
    echo "[blocked] Manual deploys are disabled."
    echo "          Merge to main and let CI deploy (.github/workflows/deploy.yml),"
    echo "          or re-run the workflow from the Actions tab."
    echo "          Break-glass (outage only): ALLOW_MANUAL_DEPLOY=1 bash deploy.sh"
    exit 1
fi
if [ "${CI_DEPLOY:-}" = "1" ]; then
    echo "[deploy] Source: CI"
else
    echo "[deploy] Source: MANUAL BREAK-GLASS - push this code through CI afterwards."
fi

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

# Analysis job deps (numpy/pandas/scipy/sklearn). Separate from the web deps
# because only the out-of-process predict job imports them. Non-fatal: if the
# wheels won't build on this box the site still deploys, and only the Analysis
# page goes stale - the scheduler logs the failure to job_runs.
if [ -f requirements-analysis.txt ]; then
    echo "[setup] Installing analysis dependencies (this can take a while)..."
    pip install -q -r requirements-analysis.txt \
        || echo "[warn] analysis deps failed to install - predictions will not run."
fi

# ── database ──────────────────────────────────────────────────────────────────
# CI never ships a database - the live data stays on the VM and is excluded from
# the deploy sync. If the file is genuinely missing (a fresh box), seed a mock
# one so the app still boots.
DB_FILE="${DB_PATH:-fantasy_2025_26.db}"
if [ "${DB_TYPE:-sqlite}" = "sqlite" ] && [ ! -f "$DB_FILE" ]; then
    echo "[setup] $DB_FILE missing - seeding a mock DB..."
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
# meatyboys-cron - the in-app scheduler decides which ingestion jobs run.
*/10 * * * * curl -fsS -m 90 -H "Authorization: Bearer ${CRON_SECRET}" http://127.0.0.1:${APP_PORT}/api/cron/tick >> ${APP_DIR}/cron.log 2>&1
CRON
) | crontab -

# ── log rotation ──────────────────────────────────────────────────────────────
# gunicorn.log and cron.log had no rotation and grew unbounded (~31 MB in the
# first month). Nothing was going to clear them, so on a 25 GB disk they were a
# slow leak. Rotated weekly, or sooner if one spikes past 20 MB.
#
# copytruncate matters here: gunicorn holds its access log open for the life of
# the process (--access-logfile, no pidfile to signal). A plain rename would
# leave it writing to the old inode and the fresh file would stay empty, so the
# log would silently stop updating until the next deploy restarted it.
echo "[setup] Installing logrotate rule..."
cat > /etc/logrotate.d/meatyboys <<LOGROTATE
${APP_DIR}/gunicorn.log ${APP_DIR}/cron.log {
    weekly
    maxsize 20M
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
LOGROTATE
# No delaycompress: that exists for the rename-and-signal pattern, where a
# process may still be writing to the rotated file. copytruncate leaves .1 as a
# finished copy, so compressing it straight away is safe and reclaims the space
# a cycle sooner (~90% of it - these are highly compressible access logs).
# Fail the deploy on a malformed rule rather than discovering it weeks later.
logrotate -d /etc/logrotate.d/meatyboys >/dev/null 2>&1 \
    || echo "[warn] logrotate rule failed validation - logs will keep growing."

# ── (re)start gunicorn ────────────────────────────────────────────────────────
echo "[deploy] Restarting gunicorn on ${APP_HOST}:${APP_PORT}..."
pkill -f "gunicorn.*api.index:app" 2>/dev/null || true
sleep 2
# setsid + closed stdin detaches gunicorn from the invoking session. With a bare
# `nohup ... &` the new process stays in the SSH session's process group and is
# killed when the connection closes - which leaves the site down after a remote
# deploy, silently, because the script has already exited 0 by then.
# 1 worker + threads keeps SQLite writes single-process safe.
setsid .venv/bin/gunicorn \
    -w 1 --threads 4 \
    -b "${APP_HOST}:${APP_PORT}" \
    --access-logfile "$APP_DIR/gunicorn.log" \
    api.index:app \
    < /dev/null >> "$APP_DIR/gunicorn.log" 2>&1 &
disown 2>/dev/null || true

# Don't report success until it actually serves: a deploy that leaves the app
# down should fail the pipeline, not pass quietly.
echo -n "[deploy] Waiting for the app to respond"
for _i in $(seq 1 30); do
    if curl -fsS -m 2 -o /dev/null "http://${APP_HOST}:${APP_PORT}/" 2>/dev/null; then
        echo " - up."
        break
    fi
    if [ "$_i" -eq 30 ]; then
        echo ""
        echo "[error] gunicorn did not come up within 30s. Last log lines:"
        tail -20 "$APP_DIR/gunicorn.log"
        exit 1
    fi
    echo -n "."
    sleep 1
done

# ── nginx reverse proxy + optional HTTPS ──────────────────────────────────────
echo "[setup] Configuring nginx (port 80 → gunicorn ${APP_PORT})..."
command -v nginx >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y nginx; }
NGINX_CONF=/etc/nginx/sites-available/meatyboys
SERVER_NAME="${DOMAIN:-_}"

# Once certbot has taken over the config (HTTPS), leave it untouched on redeploys
# so the TLS server block isn't clobbered. Delete the file + redeploy to reset.
if grep -q "managed by Certbot" "$NGINX_CONF" 2>/dev/null; then
    echo "[setup] nginx config is certbot-managed (HTTPS) - leaving it in place."
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
# Runs once - when a real DOMAIN is set and the config isn't yet certbot-managed.
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
        echo "[setup] No CERTBOT_EMAIL set - registering without an email (no renewal-failure alerts)."
    fi
    certbot --nginx ${CB_ARGS} --non-interactive --agree-tos ${EMAIL_ARG} --redirect \
        || echo "[warn] certbot failed - site still on HTTP. Check DNS + ports 80/443, then re-run deploy."
    systemctl reload nginx 2>/dev/null || true
fi

echo ""
echo "✓ Deployed."
echo "  Internal: http://${APP_HOST}:${APP_PORT}"
echo "  Public:   http://$(hostname -I | awk '{print $1}')/"
echo "  App log:  tail -f $APP_DIR/gunicorn.log"
echo "  Cron log: tail -f $APP_DIR/cron.log"

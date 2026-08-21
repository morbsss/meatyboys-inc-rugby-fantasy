# -----------------------------------------------------------------------------
# deploy.ps1 - Deploy Meaty Boys (Rugby Fantasy) from Windows to your Linux VM.
#
# Run from the project root in PowerShell:
#   .\deploy.ps1
#
# Requires OpenSSH on Windows (default on Win10/11 - verify with:  ssh -V).
# -----------------------------------------------------------------------------

# -- VM CONNECTION - fill these in --------------------------------------------
$VM_IP       = "45.32.106.113"        # <-- your VM's public IP
$VM_USER     = "root"                 # <-- SSH username (check your VM provider)
$VM_SSH_PORT = 22                     # <-- SSH port (22 is default)
$VM_APP_DIR  = "~/meatyboys"          # <-- where the app lives on the VM
$DB_FILE     = "fantasy_2025_26.db"   # <-- SQLite DB to ship (or mock_fantasy.db)
# -----------------------------------------------------------------------------

$VM = "${VM_USER}@${VM_IP}"
function SSH-Run($cmd) { ssh -p $VM_SSH_PORT $VM $cmd }

Write-Host ""
Write-Host "=== Meaty Boys - Deploying to $VM_IP ===" -ForegroundColor Cyan
Write-Host "You'll be prompted for the VM password each step (set up SSH keys to skip - see bottom)."
Write-Host ""

# -- STEP 1: directory structure on the VM ------------------------------------
Write-Host "[1/4] Creating app directories on VM..."
SSH-Run "mkdir -p ${VM_APP_DIR}/api ${VM_APP_DIR}/tools ${VM_APP_DIR}/nginx"

# -- STEP 2: application code -------------------------------------------------
Write-Host ""
Write-Host "[2/4] Copying application files..."
scp -P $VM_SSH_PORT requirements.txt deploy.sh "${VM}:${VM_APP_DIR}/"
scp -P $VM_SSH_PORT -r api   "${VM}:${VM_APP_DIR}/"
scp -P $VM_SSH_PORT -r tools "${VM}:${VM_APP_DIR}/"
scp -P $VM_SSH_PORT -r nginx "${VM}:${VM_APP_DIR}/"

# -- STEP 3: production .env + database ----------------------------------------
# .env.production holds the real SECRET_KEY/CRON_SECRET/PORT etc. It is gitignored
# and lands on the VM as .env. The DB is only shipped on the first deploy so the
# cron-updated data on the VM isn't clobbered on later deploys.
Write-Host ""
Write-Host "[3/4] Copying .env.production -> .env..."
scp -P $VM_SSH_PORT .env.production "${VM}:${VM_APP_DIR}/.env"

$dbThere = SSH-Run "test -f ${VM_APP_DIR}/${DB_FILE} && echo yes"
if ($dbThere -ne 'yes') {
    Write-Host "    Shipping $DB_FILE (first deploy)..."
    scp -P $VM_SSH_PORT $DB_FILE "${VM}:${VM_APP_DIR}/"
} else {
    Write-Host "    $DB_FILE already on VM - leaving it (preserves cron-updated data)."
}

# -- STEP 4: run setup + (re)start the app on the VM ---------------------------
Write-Host ""
Write-Host "[4/4] Running deploy.sh on VM (venv, deps, gunicorn, nginx, cron)..."
# Strip any CRLF from deploy.sh on the VM (bash chokes on carriage returns).
SSH-Run "sed -i 's/\r//' ${VM_APP_DIR}/deploy.sh; cd ${VM_APP_DIR}; bash deploy.sh"

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Green
Write-Host "App:  http://${VM_IP}/"
Write-Host "Logs: ssh -p $VM_SSH_PORT $VM 'tail -f ${VM_APP_DIR}/gunicorn.log'"
Write-Host ""

# -----------------------------------------------------------------------------
# OPTIONAL: passwordless deploys via SSH keys (run once, enter VM password):
#   ssh-keygen -t ed25519 -C "meatyboys-deploy"
#   Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" | ssh root@45.32.106.113 "mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys"
#
# OPTIONAL: HTTPS - install certbot on the VM, point a domain at it, then:
#   apt-get install -y certbot python3-certbot-nginx; certbot --nginx -d yourdomain.com
#   (after that, set FLASK_ENV=production in .env and re-run deploy.sh)
# -----------------------------------------------------------------------------

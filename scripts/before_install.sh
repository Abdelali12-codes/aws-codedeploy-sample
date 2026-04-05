#!/bin/bash
# BeforeInstall — runs BEFORE CodeDeploy copies files
set -e

LOG=/var/log/my-app-deploy.log
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] BeforeInstall: starting" >> $LOG

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv iproute2 curl

# ── Remove the deploy.lock sentinel ───────────────────────────────────────
# appspec.yml has file_exists_behavior: DISALLOW on config/deploy.lock.
# If we do NOT remove it here, CodeDeploy will hit the DISALLOW rule and
# fail the deployment — which is the intended behaviour if stop_app.sh
# did not run (e.g. first deploy, or ApplicationStop was skipped).
# We only remove it here after confirming the app is actually stopped.
if systemctl is-active --quiet my-app 2>/dev/null; then
    echo "[$TIMESTAMP] BeforeInstall: ERROR — app is still running, cannot proceed" >> $LOG
    exit 1
fi
rm -f /var/run/my-app/deploy.lock
echo "[$TIMESTAMP] BeforeInstall: lock file removed" >> $LOG

# ── Back up hotfix files before OVERWRITE could touch them ────────────────
# Even though appspec uses RETAIN for hotfix/, we back them up here as an
# extra safety net in case the appspec is ever changed to OVERWRITE by mistake.
HOTFIX_DIR=/var/www/my-app/hotfix
HOTFIX_BACKUP=/var/www/my-app-hotfix-backup
if [ -d "$HOTFIX_DIR" ] && [ "$(ls -A $HOTFIX_DIR 2>/dev/null)" ]; then
    cp -r "$HOTFIX_DIR" "$HOTFIX_BACKUP"
    echo "[$TIMESTAMP] BeforeInstall: backed up hotfix files to $HOTFIX_BACKUP" >> $LOG
fi

# ── Prepare app directory ──────────────────────────────────────────────────
# Only remove app code — NOT hotfix/ (RETAIN handles that, but belt-and-suspenders)
find /var/www/my-app -maxdepth 1 -not -name 'hotfix' -not -path '/var/www/my-app' -exec rm -rf {} + 2>/dev/null || true
mkdir -p /var/www/my-app
mkdir -p /var/www/my-app/hotfix

echo "[$TIMESTAMP] BeforeInstall: done" >> $LOG

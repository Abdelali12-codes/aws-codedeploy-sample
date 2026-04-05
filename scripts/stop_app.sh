#!/bin/bash
# ApplicationStop — gracefully stop the running application
set -e

LOG=/var/log/my-app-deploy.log
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] ApplicationStop: stopping my-app" >> $LOG

# Stop the app
systemctl stop my-app || true

# Wait up to 10s for the process to fully exit
WAITED=0
while systemctl is-active --quiet my-app 2>/dev/null && [ $WAITED -lt 10 ]; do
    sleep 1
    WAITED=$((WAITED + 1))
done

# Write a sentinel lock file to signal the app has stopped.
# appspec.yml uses file_exists_behavior: DISALLOW on this file —
# if it still exists when CodeDeploy tries to copy it, the deployment
# fails fast, signalling that ApplicationStop did not complete cleanly.
# BeforeInstall removes this file after confirming the app is stopped.
mkdir -p /var/run/my-app
echo "stopped_at=$TIMESTAMP" > /var/run/my-app/deploy.lock

echo "[$TIMESTAMP] ApplicationStop: done, lock file written" >> $LOG

#!/bin/bash
# ApplicationStop — gracefully stop the running application
set -e

LOG=/var/log/my-app-deploy.log
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] ApplicationStop: stopping my-app" >> $LOG

systemctl stop my-app || true

# Wait up to 10s for the process to fully exit
WAITED=0
while systemctl is-active --quiet my-app 2>/dev/null && [ $WAITED -lt 10 ]; do
    sleep 1
    WAITED=$((WAITED + 1))
done

echo "[$TIMESTAMP] ApplicationStop: done" >> $LOG

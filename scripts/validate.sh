#!/bin/bash
# ValidateService — smoke test to confirm the app is healthy
# If this exits non-zero, CodeDeploy rolls back the deployment
set -e

# Confirm the service is actually running before hitting the endpoint
if ! systemctl is-active --quiet my-app; then
    echo "my-app service is not running:"
    systemctl status my-app --no-pager
    journalctl -u my-app --no-pager -n 50
    exit 1
fi

# Give the app time to bind to the port
sleep 10

curl -f http://localhost:8080/health || exit 1
echo "Validation passed"

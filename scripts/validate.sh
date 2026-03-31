#!/bin/bash
# ValidateService — smoke test to confirm the app is healthy
# If this exits non-zero, CodeDeploy rolls back the deployment
set -e
sleep 5
curl -f http://localhost:8080/health || exit 1
echo "Validation passed"

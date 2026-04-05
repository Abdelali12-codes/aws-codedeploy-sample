#!/bin/bash
# ValidateService — smoke test to confirm the app is healthy
set -e

LOG=/var/log/my-app-deploy.log
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] ValidateService: starting smoke tests" >> $LOG

# 1. Confirm systemd unit is active
if ! systemctl is-active --quiet my-app; then
    echo "[$TIMESTAMP] ValidateService: FAILED — my-app not active" >> $LOG
    journalctl -u my-app --no-pager -n 50 >> $LOG 2>&1 || true
    exit 1
fi

# 2. Wait for port 8080 to be bound (up to 15s)
WAITED=0
while [ $WAITED -lt 15 ]; do
    ss -tlnp | grep -q ':8080' && break
    sleep 1
    WAITED=$((WAITED + 1))
done
if ! ss -tlnp | grep -q ':8080'; then
    echo "[$TIMESTAMP] ValidateService: FAILED — nothing on :8080 after 15s" >> $LOG
    exit 1
fi

# 3. Health endpoint must return 200
HTTP_CODE=$(curl -s -o /tmp/health.json -w "%{http_code}" http://localhost:8080/health)
if [ "$HTTP_CODE" -ne 200 ]; then
    echo "[$TIMESTAMP] ValidateService: FAILED — /health returned $HTTP_CODE" >> $LOG
    exit 1
fi
echo "[$TIMESTAMP] ValidateService: /health OK — $(cat /tmp/health.json)" >> $LOG

# 4. Validate hotfix is active — score above 100 must be capped to 100.
#    If the hotfix file is missing (e.g. after a bad rollback), the import
#    in main.py will fail and the app will not start — caught by check 1.
#    This test confirms the hotfix logic is actually running correctly.
SCORE_RESP=$(curl -s -X POST http://localhost:8080/score \
    -H "Content-Type: application/json" \
    -d '{"score": 150}')
FIXED=$(echo "$SCORE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['score'])")
if [ "$FIXED" -ne 100 ]; then
    echo "[$TIMESTAMP] ValidateService: FAILED — hotfix not applied, score=$FIXED expected 100" >> $LOG
    exit 1
fi
echo "[$TIMESTAMP] ValidateService: hotfix check OK — score capped at $FIXED" >> $LOG

# 5. Confirm hotfix file exists on disk (RETAIN protection check)
if [ ! -f /var/www/my-app/hotfix/hotfix_patch.py ]; then
    echo "[$TIMESTAMP] ValidateService: FAILED — hotfix_patch.py missing from disk" >> $LOG
    exit 1
fi
echo "[$TIMESTAMP] ValidateService: hotfix file present on disk" >> $LOG

echo "[$TIMESTAMP] ValidateService: all checks passed" >> $LOG

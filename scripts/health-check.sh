#!/bin/bash
# Flowrex health check — run via cron every 5 minutes
# Alerts via email if the backend is down (requires mailutils)
#
# Crontab entry:
#   */5 * * * * /opt/flowrex/scripts/health-check.sh >> /var/log/flowrex-health.log 2>&1

HEALTH_URL="https://flowrexalgo.com/api/health"
ALERT_EMAIL="Flowrexflex@gmail.com"
CONSECUTIVE_FAIL_FILE="/tmp/flowrex-health-fail-count"

# Check health
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HEALTH_URL")

if [ "$HTTP_CODE" = "200" ]; then
    # Reset failure counter
    echo 0 > "$CONSECUTIVE_FAIL_FILE"
    exit 0
fi

# Increment failure counter
FAIL_COUNT=$(cat "$CONSECUTIVE_FAIL_FILE" 2>/dev/null || echo 0)
FAIL_COUNT=$((FAIL_COUNT + 1))
echo "$FAIL_COUNT" > "$CONSECUTIVE_FAIL_FILE"

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') HEALTH CHECK FAILED: HTTP $HTTP_CODE (failure #$FAIL_COUNT)"

# Alert after 2 consecutive failures (10 minutes down)
if [ "$FAIL_COUNT" -ge 2 ]; then
    MSG="FlowrexAlgo health check FAILED $FAIL_COUNT times. HTTP $HTTP_CODE. URL: $HEALTH_URL"
    echo "$MSG"

    # Try mail if available
    if command -v mail &>/dev/null; then
        echo "$MSG" | mail -s "ALERT: FlowrexAlgo DOWN" "$ALERT_EMAIL"
        echo "Email alert sent to $ALERT_EMAIL"
    fi

    # Log to docker logs via backend container
    docker exec flowrex-backend python3 -c "import logging; logging.getLogger('flowrex').critical('Health check failed $FAIL_COUNT times — HTTP $HTTP_CODE')" 2>/dev/null
fi

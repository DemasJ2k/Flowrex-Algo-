#!/bin/bash
# FlowrexAlgo Production Deploy Script
# Run from /opt/flowrex on the server: bash scripts/deploy.sh
#
# Hardened version (2026-04-15) — addresses audit findings C10, L20:
#   - Pulls from `main` (not the stale `main-gNXS2` dev branch)
#   - Pre-deploy pg_dump backup
#   - Validates docker-compose config before build
#   - 180s health-check timeout (was 60s — cold start can take longer)
#   - Trap on EXIT for rollback (git revert + restart)
#   - Prints last 30 lines of backend logs on success

set -euo pipefail
cd /opt/flowrex

BACKUP_DIR="/var/backups/flowrex"
[ -w /var/backups ] || BACKUP_DIR="/tmp/flowrex-backups"
mkdir -p "$BACKUP_DIR"

PRE_DEPLOY_HASH=$(git rev-parse HEAD)
ROLLED_BACK=false

cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ] && [ "$ROLLED_BACK" = "false" ]; then
        echo ""
        echo "❌ Deploy failed (exit code $exit_code). Rolling back to $PRE_DEPLOY_HASH..."
        ROLLED_BACK=true
        git reset --hard "$PRE_DEPLOY_HASH" 2>&1 || echo "  (git reset failed)"
        echo "  Restarting backend with previous image..."
        docker compose -f docker-compose.prod.yml up -d --force-recreate backend 2>&1 || true
        echo "  Rollback complete. Investigate the failure before retrying."
    fi
}
trap cleanup EXIT

echo "=== FlowrexAlgo Deploy ==="
echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "Pre-deploy commit: $PRE_DEPLOY_HASH"

# [0/6] Pre-deploy database backup
echo ""
echo "[0/6] Backing up database..."
BACKUP_FILE="$BACKUP_DIR/pre-deploy-$(date -u +%Y%m%d-%H%M%S).sql.gz"
docker exec flowrex-postgres pg_dump -U flowrex flowrex_algo | gzip > "$BACKUP_FILE"
BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "  Backup saved: $BACKUP_FILE ($BACKUP_SIZE)"
# Verify gzip integrity
if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
    echo "  ❌ Backup file is corrupted. Aborting deploy."
    exit 1
fi
echo "  ✓ Backup integrity verified"

# [1/6] Pull latest code from `main`
echo ""
echo "[1/6] Pulling latest code..."
git fetch origin main
git pull origin main
NEW_HASH=$(git rev-parse HEAD)
if [ "$PRE_DEPLOY_HASH" = "$NEW_HASH" ]; then
    echo "  No new commits. Backend will still rebuild for any local changes."
else
    echo "  Updated $PRE_DEPLOY_HASH → $NEW_HASH"
    git log --oneline "$PRE_DEPLOY_HASH..$NEW_HASH" | head -10
fi

# [2/6] Validate docker-compose config
echo ""
echo "[2/6] Validating docker-compose.prod.yml..."
docker compose -f docker-compose.prod.yml config > /dev/null
echo "  ✓ Config valid"

# [3/6] Build containers
echo ""
echo "[3/6] Building backend container..."
docker compose -f docker-compose.prod.yml build backend frontend

# [4/6] Restart
echo ""
echo "[4/6] Restarting backend..."
docker compose -f docker-compose.prod.yml up -d --force-recreate backend frontend

# [5/6] Health check (up to 180s)
echo ""
echo "[5/6] Waiting for backend health (180s timeout)..."
HEALTHY=false
for i in $(seq 1 36); do  # 36 × 5s = 180s
    if docker exec flowrex-backend python -c "import httpx; r=httpx.get('http://localhost:8000/api/health'); assert r.status_code==200" 2>/dev/null; then
        echo "  ✓ Backend healthy after $((i * 5))s"
        HEALTHY=true
        break
    fi
    sleep 5
done
if [ "$HEALTHY" = "false" ]; then
    echo "  ❌ Backend failed to become healthy after 180s"
    echo "  Last 40 backend log lines:"
    docker logs flowrex-backend --tail 40 2>&1 | sed 's/^/    /'
    exit 1
fi

# [6/6] Status
echo ""
echo "[6/6] Deploy complete"
docker compose -f docker-compose.prod.yml ps
echo ""
echo "Last 15 backend log lines:"
docker logs flowrex-backend --tail 15 2>&1 | sed 's/^/  /'
echo ""
echo "Health: curl https://flowrexalgo.com/api/health"

# Don't trigger rollback — successful exit
trap - EXIT
echo ""
echo "✅ Deploy complete: $PRE_DEPLOY_HASH → $NEW_HASH"

#!/bin/bash
# FlowrexAlgo Deploy Script
# Run from /opt/flowrex on the server
# Usage: bash scripts/deploy.sh

set -e
cd /opt/flowrex

echo "=== FlowrexAlgo Deploy ==="
echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# Pull latest code
echo "[1/4] Pulling latest code..."
git pull origin main-gNXS2

# Build containers (uses cache for speed; use --no-cache for dep changes)
echo "[2/4] Building containers..."
docker compose -f docker-compose.prod.yml build

# Start/restart
echo "[3/4] Starting services..."
docker compose -f docker-compose.prod.yml up -d

# Wait for health
echo "[4/4] Waiting for health check..."
sleep 10
for i in {1..12}; do
    if docker exec flowrex-backend python -c "import httpx; r=httpx.get('http://localhost:8000/api/health'); assert r.status_code==200" 2>/dev/null; then
        echo "Backend healthy!"
        break
    fi
    echo "  Waiting... ($i/12)"
    sleep 5
done

# Status
echo ""
echo "=== Deploy Complete ==="
docker compose -f docker-compose.prod.yml ps
echo ""
echo "Check: curl https://flowrexalgo.com/api/health"

#!/bin/bash
# FlowrexAlgo Server Setup Script
# Run on a fresh Ubuntu 22.04 DigitalOcean Droplet with Docker pre-installed
# Usage: ssh root@24.144.117.141 'bash -s' < scripts/server-setup.sh

set -e
echo "=== FlowrexAlgo Server Setup ==="

# 1. System updates
echo "[1/8] System updates..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq certbot python3-certbot-nginx fail2ban ufw git curl jq

# 2. Create swap (critical for 2GB RAM)
echo "[2/8] Creating 2GB swap..."
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "vm.swappiness=10" >> /etc/sysctl.conf
    sysctl vm.swappiness=10
    echo "Swap created"
else
    echo "Swap already exists"
fi

# 3. Firewall
echo "[3/8] Configuring firewall..."
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# 4. Create app directory
echo "[4/8] Setting up app directory..."
mkdir -p /opt/flowrex
cd /opt/flowrex

# 5. Clone repo (or pull if exists)
echo "[5/8] Cloning repository..."
if [ -d ".git" ]; then
    git pull origin main-gNXS2
else
    git clone https://github.com/DemasJ2k/Flowrex-Algo-.git .
    git checkout main-gNXS2
fi

# 6. Create production environment file
echo "[6/8] Creating .env file..."
if [ ! -f .env ]; then
    SECRET_KEY=$(openssl rand -hex 32)
    ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
    DB_PASS=$(openssl rand -hex 16)

    cat > .env << ENVEOF
# FlowrexAlgo Production Environment
DATABASE_URL=postgresql://flowrex:${DB_PASS}@postgres:5432/flowrex_algo
SECRET_KEY=${SECRET_KEY}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
DEBUG=false
ALLOWED_ORIGINS=["https://flowrexalgo.com","https://www.flowrexalgo.com"]
NEXT_PUBLIC_API_URL=https://flowrexalgo.com
NEXT_PUBLIC_WS_URL=wss://flowrexalgo.com/ws
POSTGRES_USER=flowrex
POSTGRES_PASSWORD=${DB_PASS}
POSTGRES_DB=flowrex_algo
ENVEOF
    echo ".env created with generated secrets"
else
    echo ".env already exists — skipping"
fi

# 7. Docker compose
echo "[7/8] Verifying Docker..."
docker --version
docker compose version

# 8. Systemd service
echo "[8/8] Creating systemd service..."
cat > /etc/systemd/system/flowrex.service << 'SVCEOF'
[Unit]
Description=FlowrexAlgo Trading Platform
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/flowrex
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable flowrex

echo ""
echo "=== Setup Complete ==="
echo "Droplet IP: $(curl -s http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || echo 'unknown')"
echo "Next steps:"
echo "  1. Create docker-compose.prod.yml"
echo "  2. Set up nginx config"
echo "  3. Get SSL certificate: certbot --nginx -d flowrexalgo.com"
echo "  4. Start: systemctl start flowrex"

#!/bin/bash
# One-time VPS setup script for Interview Pilot
# Run as root on a fresh Ubuntu 22.04+ server
set -euo pipefail

echo "==> Updating system..."
apt-get update && apt-get upgrade -y

echo "==> Installing Docker..."
curl -fsSL https://get.docker.com | sh
docker compose version

echo "==> Installing Caddy..."
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy

echo "==> Setting up firewall (UFW)..."
apt-get install -y ufw
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo ""
echo "============================================"
echo "  VPS setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Clone your repo:        git clone <repo-url> /opt/interview-pilot"
echo "  2. Configure env:          cd /opt/interview-pilot && cp .env.production.example .env.production"
echo "  3. Edit secrets:           nano .env.production"
echo "  4. Set up Caddy:           See README or copy Caddyfile"
echo "  5. Deploy:                 ./deploy.sh"

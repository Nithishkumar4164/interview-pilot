#!/bin/bash
# One-time server setup script for Interview Pilot
# Tested on Oracle Cloud Ubuntu 22.04+ (ARM/x86)
# Run with: sudo bash setup-vps.sh
set -euo pipefail

echo "==> Updating system..."
apt-get update && apt-get upgrade -y

# --- Docker ---
echo "==> Installing Docker..."
curl -fsSL https://get.docker.com | sh

# Add the default 'ubuntu' user to the docker group (Oracle Cloud default user)
if id "ubuntu" &>/dev/null; then
    usermod -aG docker ubuntu
    echo "    Added 'ubuntu' user to docker group"
fi

docker compose version

# --- Caddy ---
echo "==> Installing Caddy..."
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy

# --- Oracle Cloud iptables (their images block ports 80/443 by default) ---
echo "==> Opening ports 80 and 443 in iptables..."
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
netfilter-persistent save

echo ""
echo "============================================"
echo "  Server setup complete!"
echo "============================================"
echo ""
echo "IMPORTANT: You must also open ports 80 and 443 in the Oracle Cloud Console:"
echo "  Networking → Virtual Cloud Networks → your VCN → Public Subnet"
echo "  → Default Security List → Add Ingress Rules:"
echo "    Source: 0.0.0.0/0  Port: 80   Protocol: TCP"
echo "    Source: 0.0.0.0/0  Port: 443  Protocol: TCP"
echo ""
echo "Next steps (run as 'ubuntu' user, not root):"
echo "  1. Log out and back in:    exit && ssh ubuntu@<ip>"
echo "  2. Clone repo:             git clone https://github.com/Nithishkumar4164/interview-pilot.git /opt/interview-pilot"
echo "  3. Configure env:          cd /opt/interview-pilot && cp .env.production.example .env.production"
echo "  4. Edit secrets:           nano .env.production"
echo "  5. Set up Caddy:           sudo cp Caddyfile /etc/caddy/Caddyfile && sudo systemctl restart caddy"
echo "  6. Update DuckDNS:         Point interviewpilot.duckdns.org to this server's public IP"
echo "  7. Deploy:                 ./deploy.sh"

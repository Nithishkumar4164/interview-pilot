# SSH into the Droplet
ssh -i ~/.ssh/interview-pilot-digital-ocean root@209.38.123.216

# Create a deploy user

adduser deploy
usermod -aG sudo deploy
# Copy SSH keys so you can SSH as deploy
cp -r ~/.ssh /home/deploy/.ssh
chown -R deploy:deploy /home/deploy/.ssh

Switch to the new user:

su - deploy

# Install Docker

  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker deploy

  docker compose version

  ---
# Install Caddy (HTTPS reverse proxy)

  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update && sudo apt-get install -y caddy

  ---

# Set up DNS
1. Go to https://www.duckdns.org, sign in
  2. Create a subdomain (e.g. interviewpilot)
  3. Set the IP to your droplet's public IP

7. Clone the repo

  git clone https://github.com/Nithishkumar4164/interview-pilot.git /opt/interview-pilot
  cd /opt/interview-pilot

  ---
  8. Configure environment

  cp .env.production.example .env.production
  nano .env.production

  Replace every CHANGE_ME value:

  # Generate a strong password for Postgres
  openssl rand -hex 16

  # Generate JWT secret
  openssl rand -hex 32

  Key changes to make:

  ┌─────────────────────────────────────┬────────────────────────────────────────────────┐
  │              Variable               │                  What to set                   │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ POSTGRES_PASSWORD                   │ Output of openssl rand -hex 16                 │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ MINIO_ROOT_USER                     │ Something like minio-admin                     │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ MINIO_ROOT_PASSWORD                 │ Output of openssl rand -hex 16                 │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_DATABASE_URL        │ Update the password to match POSTGRES_PASSWORD │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_DATABASE_URL_SYNC   │ Same password                                  │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_JWT_SECRET_KEY      │ Output of openssl rand -hex 32                 │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_S3_ACCESS_KEY       │ Must match MINIO_ROOT_USER                     │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_S3_SECRET_KEY       │ Must match MINIO_ROOT_PASSWORD                 │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_ANTHROPIC_API_KEY   │ Your Anthropic key                             │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_DEEPGRAM_API_KEY    │ Your Deepgram key                              │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_BACKEND_URL         │ https://YOUR_DOMAIN                            │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_FRONTEND_URL        │ https://YOUR_DOMAIN                            │
  ├─────────────────────────────────────┼────────────────────────────────────────────────┤
  │ INTERVIEW_PILOT_GOOGLE_REDIRECT_URI │ https://YOUR_DOMAIN/auth/callback              │
  └─────────────────────────────────────┴────────────────────────────────────────────────┘

  ---
  9. Configure Caddy

sudo tee /etc/caddy/Caddyfile <<EOF
interviewpilot.duckdns.org {
    reverse_proxy localhost:3000
}
EOF

  sudo systemctl restart caddy

  Replace YOUR_DOMAIN with your actual domain (e.g. interviewpilot.duckdns.org). Caddy will automatically provision a TLS certificate from Let's Encrypt.

  ---
  10. (Optional) DigitalOcean Firewall

  In the DO console: Networking → Firewalls → Create Firewall:

  ┌───────────┬──────────┬──────┬─────────┐
  │ Direction │ Protocol │ Port │ Source  │
  ├───────────┼──────────┼──────┼─────────┤
  │ Inbound   │ TCP      │ 22   │ Your IP │
  ├───────────┼──────────┼──────┼─────────┤
  │ Inbound   │ TCP      │ 80   │ All     │
  ├───────────┼──────────┼──────┼─────────┤
  │ Inbound   │ TCP      │ 443  │ All     │
  ├───────────┼──────────┼──────┼─────────┤
  │ Outbound  │ All      │ All  │ All     │
  └───────────┴──────────┴──────┴─────────┘

  Attach it to your droplet.

  ---
  11. Deploy

  cd /opt/interview-pilot
  chmod +x deploy.sh
  ./deploy.sh

  This will:
  1. Build all Docker images
  2. Start Postgres + Redis
  3. Run Alembic migrations
  4. Start all services (backend, frontend/nginx, minio)
  5. Run a health check

  ---
  12. Verify

  # All containers running?
  docker compose -f docker-compose.prod.yml ps

  # Health check
  curl http://localhost:3000/health

  # HTTPS working?
  curl https://interviewpilot.duckdns.org/health

  Then open https://YOUR_DOMAIN in a browser — you should see the Interview Pilot frontend.

  ---
  Ongoing Operations

  View logs:
  cd /opt/interview-pilot
  docker compose -f docker-compose.prod.yml logs -f backend    # backend only
  docker compose -f docker-compose.prod.yml logs -f             # all services

  Redeploy after code changes:
  cd /opt/interview-pilot
  git pull
  ./deploy.sh

  Restart a single service:
  docker compose -f docker-compose.prod.yml restart backend

  Check disk/memory usage:
  df -h
  free -h
  docker system df

  Prune old Docker images (reclaim disk):
  docker system prune -af

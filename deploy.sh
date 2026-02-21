#!/bin/bash
set -euo pipefail

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.production"

# Check for production env file
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE not found."
    echo "Copy .env.production.example to .env.production and fill in your values."
    exit 1
fi

# Check docker access
if ! docker info &>/dev/null; then
    echo "Error: Cannot connect to Docker."
    echo "Make sure Docker is installed and your user is in the 'docker' group."
    echo "Run: sudo usermod -aG docker \$USER && exit"
    echo "Then SSH back in and retry."
    exit 1
fi

COMPOSE="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

echo "==> Building images..."
$COMPOSE build

echo "==> Starting database and redis first..."
$COMPOSE up -d postgres redis
echo "    Waiting for postgres to be ready..."
sleep 10

echo "==> Running database migrations..."
$COMPOSE run --rm backend alembic upgrade head

echo "==> Starting all services..."
$COMPOSE up -d

echo "==> Waiting for services to start..."
sleep 5

echo "==> Service status:"
$COMPOSE ps

echo ""
echo "==> Verifying health..."
curl -sf http://localhost:3000/health && echo " OK" || echo " FAILED"

echo ""
echo "Deployment complete!"
echo "  Local check:  curl http://localhost:3000/health"
echo "  Public check:  curl https://interviewpilot.duckdns.org/health"

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

COMPOSE="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

echo "==> Building images..."
$COMPOSE build

echo "==> Running database migrations..."
$COMPOSE run --rm backend alembic upgrade head

echo "==> Starting services..."
$COMPOSE up -d

echo "==> Waiting for services to start..."
sleep 5

echo "==> Service status:"
$COMPOSE ps

echo ""
echo "Deployment complete. Verify with:"
echo "  curl http://localhost/health"

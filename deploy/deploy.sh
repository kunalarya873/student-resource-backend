#!/usr/bin/env bash
# ==============================================================================
# Production Deployment Script executed on AWS EC2 by GitHub Actions
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Use explicit APP_DIR if provided, otherwise default to current repo directory (or /home/ubuntu/app if it exists)
if [ -z "${APP_DIR:-}" ]; then
    if [ -d "/home/ubuntu/app" ] && [ -f "/home/ubuntu/app/docker-compose.prod.yml" ]; then
        APP_DIR="/home/ubuntu/app"
    else
        APP_DIR="$REPO_DIR"
    fi
fi

COMPOSE_FILE="docker-compose.prod.yml"
HEALTHCHECK_URL="http://127.0.0.1:8000/healthz"
MAX_HEALTH_ATTEMPTS=15
HEALTH_INTERVAL_SECONDS=3

echo "=============================================================================="
echo "🚀 Starting Automated Backend Deployment on EC2"
echo "Target Directory: $APP_DIR"
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "=============================================================================="

if [ ! -d "$APP_DIR" ]; then
    echo "❌ Error: Application directory $APP_DIR does not exist."
    exit 1
fi

cd "$APP_DIR"

# 1. Pull latest repository changes
echo "===> [1/5] Fetching latest changes from Git repository..."
if [ -d ".git" ]; then
    git fetch origin main
    git reset --hard origin/main
else
    echo "⚠️ Git repository not found in $APP_DIR. Assuming workspace files copied directly."
fi

# 2. Verify Docker daemon
echo "===> [2/5] Checking Docker service..."
if ! docker info > /dev/null 2>&1; then
    echo "❌ Error: Docker daemon is not running or current user does not have permission."
    exit 1
fi

# 3. Execute zero-downtime database migrations
echo "===> [3/5] Applying Alembic database migrations..."
if [ -f "$COMPOSE_FILE" ]; then
    # Run migrations using the backend image
    docker compose -f "$COMPOSE_FILE" run --rm backend alembic upgrade head || {
        echo "⚠️ Alembic upgrade in container failed or image not yet built. Proceeding to build..."
    }
fi

# 4. Rebuild and launch updated backend container
echo "===> [4/5] Building and recreating backend container..."
docker compose -f "$COMPOSE_FILE" up -d --build --no-deps backend

# 5. Smoke test health verification
echo "===> [5/5] Verifying backend healthcheck at $HEALTHCHECK_URL..."
ATTEMPT=1
HEALTHY=false

while [ "$ATTEMPT" -le "$MAX_HEALTH_ATTEMPTS" ]; do
    echo "Healthcheck attempt $ATTEMPT/$MAX_HEALTH_ATTEMPTS..."
    if curl -s -f "$HEALTHCHECK_URL" > /dev/null; then
        echo "✅ Healthcheck passed! Service is live and responding."
        HEALTHY=true
        break
    fi
    sleep "$HEALTH_INTERVAL_SECONDS"
    ATTEMPT=$((ATTEMPT + 1))
done

if [ "$HEALTHY" = false ]; then
    echo "❌ Critical: Healthcheck failed after $MAX_HEALTH_ATTEMPTS attempts."
    echo "Fetching container logs for debugging:"
    docker compose -f "$COMPOSE_FILE" logs --tail=100 backend
    exit 1
fi

# Housekeeping
echo "===> Cleaning up dangling Docker images..."
docker image prune -f > /dev/null 2>&1 || true

echo "=============================================================================="
echo "🎉 Deployment Succeeded!"
echo "Backend running on port 8000"
echo "=============================================================================="

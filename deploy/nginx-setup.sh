#!/usr/bin/env bash
# ==============================================================================
# Automated Nginx Reverse Proxy Setup for Backend
# Usage: sudo ./deploy/nginx-setup.sh [domain_name]
# ==============================================================================

set -euo pipefail

DOMAIN="${1:-_}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NGINX_CONF_SOURCE="${SCRIPT_DIR}/nginx.conf"
NGINX_TARGET="/etc/nginx/sites-available/backend.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/backend.conf"

echo "===> [1/4] Installing Nginx..."
sudo apt-get update -y
sudo apt-get install -y nginx

echo "===> [2/4] Configuring Nginx reverse proxy..."
if [ -f "$NGINX_CONF_SOURCE" ]; then
    sudo cp "$NGINX_CONF_SOURCE" "$NGINX_TARGET"
else
    echo "❌ Error: $NGINX_CONF_SOURCE not found."
    exit 1
fi

# Set domain if provided
if [ "$DOMAIN" != "_" ]; then
    echo "Setting server_name to $DOMAIN..."
    sudo sed -i "s/server_name _;/server_name $DOMAIN;/g" "$NGINX_TARGET"
fi

# Enable site and disable default site
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf "$NGINX_TARGET" "$NGINX_ENABLED"

echo "===> [3/4] Validating Nginx configuration..."
sudo nginx -t

echo "===> [4/4] Reloading Nginx service..."
sudo systemctl enable nginx
sudo systemctl restart nginx

echo "=============================================================================="
echo "✅ Nginx Reverse Proxy is Active (HTTP)!"
echo "Port 80 is now proxying to backend on http://127.0.0.1:8000"
echo "You can access your backend at: http://<EC2-PUBLIC-IP>/"
echo "=============================================================================="

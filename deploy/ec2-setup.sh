#!/usr/bin/env bash
# ==============================================================================
# AWS EC2 Initial Setup Script for AMU Student Resource Management System
# Target OS: Ubuntu 22.04 LTS / 24.04 LTS
# Usage: curl -sSL https://raw.githubusercontent.com/.../deploy/ec2-setup.sh | bash
#   or: chmod +x ec2-setup.sh && ./ec2-setup.sh
# ==============================================================================

set -euo pipefail

echo "===> [1/6] Updating system packages..."
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    ufw \
    jq

echo "===> [2/6] Installing Docker Engine & Docker Compose Plugin..."
sudo install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
fi

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "===> [3/6] Configuring Docker user group for non-root execution..."
sudo usermod -aG docker "$USER"

echo "===> [4/6] Configuring UFW Firewall..."
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp comment 'HTTP'
sudo ufw allow 443/tcp comment 'HTTPS'
sudo ufw allow 8000/tcp comment 'Backend API'
sudo ufw --force enable

echo "===> [5/6] Creating deployment directory structure..."
APP_DIR="/home/$USER/app"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

echo "===> [6/6] Verifying installations..."
docker --version
docker compose version

echo "=============================================================================="
echo "✅ EC2 Setup Completed Successfully!"
echo "Target Application Directory: $APP_DIR"
echo "NOTE: Log out and log back in (or run 'newgrp docker') to apply docker group changes."
echo "=============================================================================="

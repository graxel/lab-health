#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "Setting up Lab Health Node Agent (Target Node)"
echo "=================================================="

# Check for required configuration files
MISSING_CONFIG=0

if [ ! -f "settings.env" ]; then
    echo "❌ Error: 'settings.env' is missing."
    echo "   Please copy 'example_settings.env' to 'settings.env' and edit it with your parameters:"
    echo "   cp example_settings.env settings.env"
    MISSING_CONFIG=1
fi

if [ ! -f "secrets.env" ]; then
    echo "❌ Error: 'secrets.env' is missing."
    echo "   Please copy 'example_secrets.env' to 'secrets.env' and edit it with your parameters:"
    echo "   cp example_secrets.env secrets.env"
    MISSING_CONFIG=1
fi

if [ ! -f "machines.json" ]; then
    echo "❌ Error: 'machines.json' is missing."
    echo "   Please copy 'example_machines.json' to 'machines.json' and edit it with your topology parameters:"
    echo "   cp example_machines.json machines.json"
    MISSING_CONFIG=1
fi

if [ "$MISSING_CONFIG" -eq 1 ]; then
    echo ""
    echo "Please create the missing configuration file(s) and re-run this setup script."
    exit 1
fi

# 1. Ensure uv is installed
if ! command -v uv &> /dev/null; then
    echo "Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# 2. Sync python virtual environment
echo "Syncing Python dependencies with uv..."
uv sync

# 3. Add user to docker group if Docker is installed
if command -v docker &> /dev/null; then
    echo "Granting docker socket access to $USER..."
    sudo usermod -aG docker "$USER" || true
fi

# 4. Copy and enable systemd reporter service
echo "Installing lab-health-reporter systemd service..."
SERVICE_PATH="/etc/systemd/system/lab-health-reporter.service"

sudo cp service_files/lab-health-reporter.service "$SERVICE_PATH"

USER_HOME="$HOME"
USER_NAME="$USER"

sudo sed -i "s|User=graxel|User=$USER_NAME|g" "$SERVICE_PATH"
sudo sed -i "s|/home/graxel/repos/lab-health|$PWD|g" "$SERVICE_PATH"
sudo sed -i "s|/home/graxel/.cargo/bin/uv|$USER_HOME/.cargo/bin/uv|g" "$SERVICE_PATH"

sudo systemctl daemon-reload
sudo systemctl enable --now lab-health-reporter.service

echo ""
echo "✅ Target Node setup complete!"
echo "STATUS:"
sudo systemctl status lab-health-reporter.service --no-pager

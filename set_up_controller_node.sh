#!/usr/bin/env bash
set -euo pipefail

echo "========================================================="
echo "Setting up Lab Health Controller & UI (sumtingwong)"
echo "========================================================="

# Check for required configuration files
MISSING_CONFIG=0

if [ ! -f "settings.env" ]; then
    echo ""
    echo "Error: 'settings.env' is missing."
    echo "Please copy 'example_settings.env' to 'settings.env' and edit it with your parameters:"
    echo "  cp example_settings.env settings.env"
    echo "  nano settings.env"
    MISSING_CONFIG=1
fi

if [ ! -f "secrets.env" ]; then
    echo ""
    echo "Error: 'secrets.env' is missing."
    echo "Please copy 'example_secrets.env' to 'secrets.env' and edit it with your parameters:"
    echo "  cp example_secrets.env secrets.env"
    echo "  nano secrets.env"
    MISSING_CONFIG=1
fi

if [ ! -f "machines.json" ]; then
    echo ""
    echo "Error: 'machines.json' is missing."
    echo "Please copy 'example_machines.json' to 'machines.json' and edit it with your topology parameters:"
    echo "  cp example_machines.json machines.json"
    echo "  nano machines.json"
    MISSING_CONFIG=1
fi

if [ "$MISSING_CONFIG" -eq 1 ]; then
    echo ""
    echo "Please create the missing configuration file(s) and re-run this setup script."
    exit 1
fi

# 1. Ensure uv is installed and in PATH
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v uv &> /dev/null; then
    echo "Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# 2. Ensure PostgreSQL & system build libraries are installed
echo "Installing PostgreSQL & system build libraries..."
sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib libpq-dev libffi-dev gcc python3-dev
sudo systemctl enable --now postgresql

# Ensure PostgreSQL listens on all interfaces (localhost and LAN IP)
PG_CONF=$(ls /etc/postgresql/*/*/postgresql.conf 2>/dev/null | head -n 1 || true)
if [ -n "$PG_CONF" ]; then
    sudo sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/g" "$PG_CONF"
    sudo sed -i "s/listen_addresses = 'localhost'/listen_addresses = '*'/g" "$PG_CONF"
    PG_HBA=$(dirname "$PG_CONF")/pg_hba.conf
    if [ -f "$PG_HBA" ] && ! sudo grep -q "0.0.0.0/0" "$PG_HBA"; then
        echo "host    all             all             0.0.0.0/0               scram-sha-256" | sudo tee -a "$PG_HBA" > /dev/null
    fi
    sudo systemctl restart postgresql
fi

# 3. Sync python dependencies
echo "Syncing Python dependencies with uv..."
uv sync

# Load DB configuration parameters from settings.env & secrets.env
export $(grep -v '^#' settings.env | xargs)
export $(grep -v '^#' secrets.env | xargs)

DB_NAME_VAL="${DB_NAME:-lab_health}"
DB_USER_VAL="${DB_USER:-postgres}"
DB_PASS_VAL="${DB_PASSWORD:-}"

# 4. Create Database & User if not existing using settings.env values
echo "Setting up PostgreSQL database '$DB_NAME_VAL'..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME_VAL'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME_VAL;"

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER_VAL'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER $DB_USER_VAL WITH PASSWORD '$DB_PASS_VAL';"

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME_VAL TO $DB_USER_VAL;"
sudo -u postgres psql -c "ALTER DATABASE $DB_NAME_VAL OWNER TO $DB_USER_VAL;"

# 5. Initialize Schema
echo "Initializing database tables & indexes..."
uv run python database_setup.py

# 6. Install Systemd Services
USER_HOME="$HOME"
USER_NAME="$USER"
REPO_DIR="$PWD"
UV_BIN="$(command -v uv || echo "$USER_HOME/.local/bin/uv")"

echo "Installing systemd service units..."
for svc in lab-health-reporter lab-health-monitor lab-health-ui; do
    SERVICE_PATH="/etc/systemd/system/${svc}.service"
    sudo cp "service_files/${svc}.service" "$SERVICE_PATH"
    sudo sed -i "s|User=graxel|User=$USER_NAME|g" "$SERVICE_PATH"
    sudo sed -i "s|/home/graxel/repos/lab-health|$REPO_DIR|g" "$SERVICE_PATH"
    sudo sed -i "s|/home/graxel/.cargo/bin/uv|$UV_BIN|g" "$SERVICE_PATH"
done

sudo systemctl daemon-reload
sudo systemctl restart lab-health-reporter.service || sudo systemctl enable --now lab-health-reporter.service
sudo systemctl restart lab-health-monitor.service || sudo systemctl enable --now lab-health-monitor.service
sudo systemctl restart lab-health-ui.service || sudo systemctl enable --now lab-health-ui.service

echo ""
echo "Controller Node setup complete!"
echo "Lab Health Dashboard is running at http://localhost:8080"
echo ""
echo "STATUS SUMMARY:"
sudo systemctl status lab-health-monitor.service --no-pager

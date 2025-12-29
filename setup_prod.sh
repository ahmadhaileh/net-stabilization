#!/bin/bash
# =============================================================================
# Net Stabilization - Production Setup Script
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="net-stabilization"

echo "=========================================="
echo "Net Stabilization - Production Setup"
echo "=========================================="
echo ""

# Check if running as root for systemd installation
if [ "$EUID" -eq 0 ]; then
    IS_ROOT=true
else
    IS_ROOT=false
fi

# Check prerequisites
echo "[1/5] Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed. Please install Docker first."
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "ERROR: Docker daemon is not running or you don't have permission."
    echo "       Try: sudo systemctl start docker"
    echo "       Or add user to docker group: sudo usermod -aG docker $USER"
    exit 1
fi

echo "  ✓ Docker is available"

# Check for .env file
echo ""
echo "[2/5] Checking configuration..."

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "  Creating .env from .env.example..."
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "  ⚠ Please edit .env with your settings before starting"
else
    echo "  ✓ .env file exists"
fi

# Ensure data directory exists with proper permissions
echo ""
echo "[3/5] Setting up data directory..."
mkdir -p "$SCRIPT_DIR/data"
chmod 755 "$SCRIPT_DIR/data"
echo "  ✓ Data directory ready"

# Build the container
echo ""
echo "[4/5] Building Docker image..."
cd "$SCRIPT_DIR"
docker compose build --quiet
echo "  ✓ Docker image built"

# Install systemd service (if root)
echo ""
echo "[5/5] Setting up auto-start..."

if [ "$IS_ROOT" = true ]; then
    cp "$SCRIPT_DIR/net-stabilization.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
    echo "  ✓ Systemd service installed and enabled"
else
    echo "  ⚠ Not running as root - skipping systemd installation"
    echo "    To install systemd service, run:"
    echo "    sudo cp $SCRIPT_DIR/net-stabilization.service /etc/systemd/system/"
    echo "    sudo systemctl daemon-reload"
    echo "    sudo systemctl enable $SERVICE_NAME"
fi

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Quick Start Commands:"
echo "  Start:   docker compose up -d"
echo "  Stop:    docker compose down"
echo "  Logs:    docker compose logs -f"
echo "  Status:  docker compose ps"
echo ""
echo "Access Points:"
echo "  Dashboard: http://localhost:8080/"
echo "  API:       http://localhost:8080/api/status"
echo "  Health:    http://localhost:8080/health"
echo ""
echo "AwesomeMiner Setup:"
echo "  1. Ensure AwesomeMiner is running on this machine"
echo "  2. Enable Remote API in AwesomeMiner:"
echo "     Options -> Remote API -> Enable Remote Access"
echo "  3. Set port to 17790 (default)"
echo "  4. (Optional) Enable API Key authentication"
echo ""

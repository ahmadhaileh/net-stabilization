#!/bin/bash
# =============================================================================
# Remote Access Setup - Reverse SSH Tunnel
# Grid Stabilization - Mining Fleet Control
#
# Creates a persistent reverse SSH tunnel from this server to a VPS,
# allowing remote access even behind CGNAT / SIM-based routers.
#
# Architecture:
#   [Your Phone/PC] --SSH--> [VPS:2222] --tunnel--> [Mining Server:22]
#   [Your Phone/PC] --HTTP-> [VPS:8080] --tunnel--> [Mining Server:8080]
#
# Prerequisites:
#   1. A VPS with a public IP (Oracle Cloud Free Tier recommended)
#   2. SSH access to the VPS
# =============================================================================

set -euo pipefail

# ---- Configuration ----
# CHANGE THESE to your VPS details
VPS_IP="${VPS_IP:-YOUR_VPS_PUBLIC_IP}"
VPS_USER="${VPS_USER:-ubuntu}"
VPS_SSH_PORT="${VPS_SSH_PORT:-22}"

# Tunnel port mappings (remote port on VPS → local service)
TUNNEL_SSH_PORT=2222        # VPS:2222 → Mining Server:22
TUNNEL_DASHBOARD_PORT=8080  # VPS:8080 → Mining Server:8080

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; }
info() { echo -e "${CYAN}[i]${NC} $1"; }

# ---- Validation ----
if [[ "$VPS_IP" == "YOUR_VPS_PUBLIC_IP" ]]; then
    err "Please set your VPS IP first!"
    echo ""
    echo "Usage:"
    echo "  VPS_IP=1.2.3.4 VPS_USER=ubuntu ./setup_remote_access.sh"
    echo ""
    echo "Or edit the variables at the top of this script."
    exit 1
fi

echo ""
echo "========================================"
echo "  Remote Access Setup"
echo "  Grid Stabilization"
echo "========================================"
echo ""
info "VPS: ${VPS_USER}@${VPS_IP}:${VPS_SSH_PORT}"
info "Tunnel SSH:       VPS:${TUNNEL_SSH_PORT} → localhost:22"
info "Tunnel Dashboard: VPS:${TUNNEL_DASHBOARD_PORT} → localhost:8080"
echo ""

# ---- Step 1: Install autossh ----
log "Installing autossh..."
if ! command -v autossh &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq autossh
    log "autossh installed"
else
    log "autossh already installed"
fi

# ---- Step 2: Generate SSH key (if not exists) ----
KEY_FILE="/root/.ssh/id_tunnel"
if [[ ! -f "$KEY_FILE" ]]; then
    log "Generating SSH key for tunnel..."
    sudo mkdir -p /root/.ssh
    sudo chmod 700 /root/.ssh
    sudo ssh-keygen -t ed25519 -f "$KEY_FILE" -N "" -C "grid-stabilization-tunnel"
    log "SSH key generated: ${KEY_FILE}"
else
    log "SSH key already exists: ${KEY_FILE}"
fi

echo ""
echo "========================================"
echo "  IMPORTANT: Copy this public key to your VPS"
echo "========================================"
echo ""
echo "Run this command on your VPS (${VPS_IP}):"
echo ""
echo "  echo '$(sudo cat ${KEY_FILE}.pub)' >> ~/.ssh/authorized_keys"
echo ""
echo "Or copy it manually:"
echo ""
sudo cat "${KEY_FILE}.pub"
echo ""
echo "========================================"
echo ""
read -p "Have you added the key to your VPS? (y/n): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    warn "Add the key to your VPS first, then run this script again."
    exit 0
fi

# ---- Step 3: Test SSH connection ----
log "Testing SSH connection to VPS..."
if sudo ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
    -p "$VPS_SSH_PORT" "${VPS_USER}@${VPS_IP}" "echo 'Connection OK'"; then
    log "SSH connection successful!"
else
    err "SSH connection failed. Check your VPS IP, user, and key."
    exit 1
fi

# ---- Step 4: Configure VPS for reverse tunnels ----
log "Configuring VPS sshd for tunnel access..."
sudo ssh -i "$KEY_FILE" -p "$VPS_SSH_PORT" "${VPS_USER}@${VPS_IP}" bash -s <<'REMOTE_SETUP'
    # Enable GatewayPorts so tunnels are accessible from outside
    if ! grep -q "^GatewayPorts yes" /etc/ssh/sshd_config; then
        echo "GatewayPorts yes" | sudo tee -a /etc/ssh/sshd_config
        echo "ClientAliveInterval 30" | sudo tee -a /etc/ssh/sshd_config
        echo "ClientAliveCountMax 3" | sudo tee -a /etc/ssh/sshd_config
        sudo systemctl reload sshd || sudo systemctl reload ssh
        echo "VPS sshd configured and reloaded"
    else
        echo "VPS sshd already configured"
    fi
REMOTE_SETUP
log "VPS configured"

# ---- Step 5: Install systemd service ----
log "Installing systemd service..."
sudo tee /etc/systemd/system/grid-tunnel.service > /dev/null <<EOF
[Unit]
Description=Grid Stabilization - Reverse SSH Tunnel
Documentation=https://github.com/ahmadhaileh/net-stabilization
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment="AUTOSSH_GATETIME=0"
Environment="AUTOSSH_POLL=30"
Environment="AUTOSSH_LOGLEVEL=7"
Environment="AUTOSSH_LOGFILE=/var/log/grid-tunnel.log"

ExecStart=/usr/bin/autossh -M 0 -N \
    -o "ServerAliveInterval=30" \
    -o "ServerAliveCountMax=3" \
    -o "ExitOnForwardFailure=yes" \
    -o "StrictHostKeyChecking=accept-new" \
    -o "ConnectTimeout=10" \
    -i ${KEY_FILE} \
    -R 0.0.0.0:${TUNNEL_SSH_PORT}:localhost:22 \
    -R 0.0.0.0:${TUNNEL_DASHBOARD_PORT}:localhost:8080 \
    -p ${VPS_SSH_PORT} \
    ${VPS_USER}@${VPS_IP}

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable grid-tunnel.service
sudo systemctl start grid-tunnel.service
log "Systemd service installed and started"

# ---- Step 6: Verify tunnel ----
sleep 3
if sudo systemctl is-active --quiet grid-tunnel.service; then
    log "Tunnel service is RUNNING"
else
    err "Tunnel service failed to start. Check logs:"
    echo "  sudo journalctl -u grid-tunnel -n 20"
    exit 1
fi

# ---- Step 7: VPS firewall rules ----
log "Opening VPS firewall ports..."
sudo ssh -i "$KEY_FILE" -p "$VPS_SSH_PORT" "${VPS_USER}@${VPS_IP}" bash -s <<FIREWALL
    # Open tunnel ports (works for both iptables and ufw)
    if command -v ufw &>/dev/null; then
        sudo ufw allow ${TUNNEL_SSH_PORT}/tcp
        sudo ufw allow ${TUNNEL_DASHBOARD_PORT}/tcp
        echo "UFW rules added"
    else
        sudo iptables -I INPUT -p tcp --dport ${TUNNEL_SSH_PORT} -j ACCEPT 2>/dev/null || true
        sudo iptables -I INPUT -p tcp --dport ${TUNNEL_DASHBOARD_PORT} -j ACCEPT 2>/dev/null || true
        echo "iptables rules added"
    fi
FIREWALL
log "Firewall configured"

echo ""
echo "========================================"
echo "  Setup Complete!"
echo "========================================"
echo ""
log "Remote access is now available:"
echo ""
echo "  📡 SSH to mining server:"
echo "     ssh dkk@${VPS_IP} -p ${TUNNEL_SSH_PORT}"
echo ""
echo "  🖥  Dashboard:"
echo "     http://${VPS_IP}:${TUNNEL_DASHBOARD_PORT}"
echo ""
echo "  🔧 Manage tunnel:"
echo "     sudo systemctl status grid-tunnel"
echo "     sudo systemctl restart grid-tunnel"
echo "     sudo journalctl -u grid-tunnel -f"
echo ""
echo "  📋 The tunnel auto-reconnects on network drops."
echo "     It starts automatically on boot."
echo ""
echo "========================================"

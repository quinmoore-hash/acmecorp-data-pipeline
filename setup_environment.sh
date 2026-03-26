#!/bin/bash
# ============================================
# Environment Setup Script
# Installs dependencies and configures the pipeline environment
# Run once on new servers or after major updates
#
# Author: jthompson
# Created: 2020-06-10
# Modified: 2023-03-22 (added aws cli v2)
#
# Usage: sudo ./setup_environment.sh
# ============================================

set -e

echo "============================================"
echo "AcmeCorp Pipeline Environment Setup"
echo "============================================"
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (sudo)"
    exit 1
fi

INSTALL_DIR="/opt/acmecorp/pipeline"
DATA_BASE="/opt/acmecorp/data"
LOG_BASE="/var/log/acmecorp/pipeline"
BACKUP_BASE="/opt/acmecorp/backups"
SERVICE_USER="etl_service"

# Step 1: Create service account
echo "[1/8] Creating service account..."
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd -r -s /bin/bash -d /home/${SERVICE_USER} -m "$SERVICE_USER"
    echo "Created user: $SERVICE_USER"
else
    echo "User $SERVICE_USER already exists"
fi

# Step 2: Install system packages
echo "[2/8] Installing system packages..."
if command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq \
        curl \
        wget \
        jq \
        postgresql-client \
        mailutils \
        gzip \
        coreutils \
        gawk \
        sed \
        grep \
        bc
elif command -v yum &>/dev/null; then
    yum install -y -q \
        curl \
        wget \
        jq \
        postgresql \
        mailx \
        gzip \
        coreutils \
        gawk \
        sed \
        grep \
        bc
else
    echo "WARNING: Unknown package manager. Please install dependencies manually."
fi

# Step 3: Install AWS CLI v2
echo "[3/8] Installing AWS CLI..."
if ! command -v aws &>/dev/null; then
    curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
    unzip -q /tmp/awscliv2.zip -d /tmp/
    /tmp/aws/install
    rm -rf /tmp/awscliv2.zip /tmp/aws
    echo "AWS CLI installed: $(aws --version)"
else
    echo "AWS CLI already installed: $(aws --version)"
fi

# Step 4: Create directory structure
echo "[4/8] Creating directory structure..."
for dir in \
    "${DATA_BASE}/incoming" \
    "${DATA_BASE}/processed" \
    "${DATA_BASE}/archive" \
    "${DATA_BASE}/staging" \
    "${DATA_BASE}/errors" \
    "${DATA_BASE}/ftp_incoming" \
    "${LOG_BASE}" \
    "${BACKUP_BASE}/database/full" \
    "${BACKUP_BASE}/database/incremental" \
    "/var/run/acmecorp" \
    "/tmp/acmecorp_locks"; do
    mkdir -p "$dir"
    chown "$SERVICE_USER":"$SERVICE_USER" "$dir"
done
echo "Directories created"

# Step 5: Deploy pipeline scripts
echo "[5/8] Deploying pipeline scripts..."
SCRIPT_SOURCE="$(cd "$(dirname "$0")" && pwd)/.."

if [[ "$SCRIPT_SOURCE" != "$INSTALL_DIR" ]]; then
    mkdir -p "$INSTALL_DIR"
    cp -r "${SCRIPT_SOURCE}"/* "$INSTALL_DIR/"
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
fi

# Make scripts executable
chmod +x "${INSTALL_DIR}/scripts/"*.sh
chmod +x "${INSTALL_DIR}/utils/"*.sh
echo "Scripts deployed to $INSTALL_DIR"

# Step 6: Configure AWS profile
echo "[6/8] Configuring AWS profile..."
sudo -u "$SERVICE_USER" mkdir -p /home/${SERVICE_USER}/.aws

if [[ ! -f /home/${SERVICE_USER}/.aws/config ]]; then
    cat > /home/${SERVICE_USER}/.aws/config <<EOF
[profile prod-etl]
region = us-east-1
output = json
EOF
    chown "$SERVICE_USER":"$SERVICE_USER" /home/${SERVICE_USER}/.aws/config
    echo "AWS profile configured (credentials must be added manually)"
else
    echo "AWS config already exists"
fi

# Step 7: Setup log rotation
echo "[7/8] Configuring logrotate..."
cat > /etc/logrotate.d/acmecorp-pipeline <<EOF
${LOG_BASE}/*.log {
    daily
    missingok
    rotate 90
    compress
    delaycompress
    notifempty
    create 0644 ${SERVICE_USER} ${SERVICE_USER}
}
EOF
echo "Logrotate configured"

# Step 8: Install cron jobs
echo "[8/8] Installing cron jobs..."
sudo -u "$SERVICE_USER" "${INSTALL_DIR}/scripts/cron_scheduler.sh" install

echo ""
echo "============================================"
echo "Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Configure AWS credentials: aws configure --profile prod-etl"
echo "  2. Verify database connectivity: ${INSTALL_DIR}/scripts/health_check.sh"
echo "  3. Review configs in ${INSTALL_DIR}/configs/"
echo "  4. Run a test: sudo -u ${SERVICE_USER} ${INSTALL_DIR}/scripts/etl_master.sh"
echo ""

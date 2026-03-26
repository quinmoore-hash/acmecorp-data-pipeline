#!/bin/bash
# HOTFIX DEPLOYMENT SCRIPT
# 
# Quick and dirty deployment script. Does NOT use any proper CI/CD.
# Just SCPs files to prod and restarts crons. 
# Written at 3am during an incident. TODO: replace with proper deployment.
#
# Usage: ./deploy_hotfix.sh [script_name]
#        ./deploy_hotfix.sh --all
#
# WARNING: No rollback mechanism. Take a backup first!

PROD_SERVERS="prod-etl-01.acmecorp.internal prod-etl-02.acmecorp.internal"
DEPLOY_USER="deploy"
DEPLOY_KEY="/home/deploy/.ssh/acme_deploy_rsa"
INSTALL_DIR="/opt/acmecorp/pipeline"

TARGET="$1"

if [ -z "$TARGET" ]; then
    echo "Usage: $0 <script_name|--all>"
    echo ""
    echo "Examples:"
    echo "  $0 etl_master.sh"
    echo "  $0 --all"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo "  HOTFIX DEPLOYMENT"
echo "  $(date)"
echo "============================================"
echo ""

for server in $PROD_SERVERS; do
    echo "--- Deploying to $server ---"
    
    # Check connectivity
    ssh -i "$DEPLOY_KEY" -o ConnectTimeout=5 ${DEPLOY_USER}@${server} "echo ok" > /dev/null 2>&1
    if [ $? -ne 0 ]; then
        echo "ERROR: Cannot connect to $server"
        continue
    fi
    
    if [ "$TARGET" == "--all" ]; then
        echo "Copying all scripts..."
        scp -i "$DEPLOY_KEY" -r "${BASE_DIR}/scripts/"*.sh ${DEPLOY_USER}@${server}:${INSTALL_DIR}/scripts/
        scp -i "$DEPLOY_KEY" -r "${BASE_DIR}/utils/"*.sh ${DEPLOY_USER}@${server}:${INSTALL_DIR}/utils/
        scp -i "$DEPLOY_KEY" -r "${BASE_DIR}/configs/"* ${DEPLOY_USER}@${server}:${INSTALL_DIR}/configs/
    else
        # figure out which directory the script is in
        if [ -f "${BASE_DIR}/scripts/${TARGET}" ]; then
            echo "Copying scripts/${TARGET}..."
            scp -i "$DEPLOY_KEY" "${BASE_DIR}/scripts/${TARGET}" ${DEPLOY_USER}@${server}:${INSTALL_DIR}/scripts/
        elif [ -f "${BASE_DIR}/utils/${TARGET}" ]; then
            echo "Copying utils/${TARGET}..."
            scp -i "$DEPLOY_KEY" "${BASE_DIR}/utils/${TARGET}" ${DEPLOY_USER}@${server}:${INSTALL_DIR}/utils/
        else
            echo "ERROR: Script not found: $TARGET"
            exit 1
        fi
    fi
    
    # Make executable
    ssh -i "$DEPLOY_KEY" ${DEPLOY_USER}@${server} "chmod +x ${INSTALL_DIR}/scripts/*.sh ${INSTALL_DIR}/utils/*.sh"
    
    # Reinstall crons (just in case)
    ssh -i "$DEPLOY_KEY" ${DEPLOY_USER}@${server} "${INSTALL_DIR}/scripts/cron_scheduler.sh install"
    
    echo "Done: $server"
    echo ""
done

echo "============================================"
echo "  Deployment complete"
echo "  REMEMBER: There is no rollback. If something"
echo "  breaks, manually revert the files."
echo "============================================"

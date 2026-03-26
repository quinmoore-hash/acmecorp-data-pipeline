#!/bin/bash
# ============================================
# S3 Data Sync
# Uploads processed data to S3 data lake
# Also handles downloading reference data
# Author: asingh
# Created: 2022-11-03
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/configs/pipeline.env"

ACTION="${1:-upload}"
SOURCE_DIR="${2:-$DATA_OUTPUT_DIR}"

case "$ACTION" in
    upload)
        log_info "Uploading processed data to S3..."
        
        DATE_PREFIX=$(date +%Y/%m/%d)
        
        aws s3 sync "$SOURCE_DIR" \
            "s3://${S3_BUCKET}/${S3_PREFIX}/${DATE_PREFIX}/" \
            --profile "$AWS_PROFILE" \
            --exclude "*.tmp" \
            --exclude "*.lock" \
            --exclude ".DS_Store" \
            2>&1
        
        if [[ $? -ne 0 ]]; then
            log_error "S3 upload failed"
            exit 1
        fi
        
        # Count uploaded files
        UPLOADED=$(aws s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/${DATE_PREFIX}/" \
            --profile "$AWS_PROFILE" 2>/dev/null | wc -l)
        log_info "Uploaded ${UPLOADED} files to s3://${S3_BUCKET}/${S3_PREFIX}/${DATE_PREFIX}/"
        ;;
    
    download)
        DOWNLOAD_PREFIX="${3:-reference}"
        DEST_DIR="${4:-${DATA_INPUT_DIR}/reference}"
        mkdir -p "$DEST_DIR"
        
        log_info "Downloading reference data from S3..."
        
        aws s3 sync \
            "s3://${S3_BUCKET}/${DOWNLOAD_PREFIX}/" \
            "$DEST_DIR/" \
            --profile "$AWS_PROFILE" \
            --exclude "*.tmp" \
            2>&1
        
        if [[ $? -ne 0 ]]; then
            log_error "S3 download failed"
            exit 1
        fi
        
        DOWNLOADED=$(find "$DEST_DIR" -type f | wc -l)
        log_info "Downloaded ${DOWNLOADED} reference files"
        ;;
    
    list)
        PREFIX="${2:-${S3_PREFIX}}"
        aws s3 ls "s3://${S3_BUCKET}/${PREFIX}/" \
            --profile "$AWS_PROFILE" \
            --recursive \
            --summarize 2>&1
        ;;
    
    *)
        echo "Usage: $0 {upload|download|list} [path] [prefix] [dest]"
        exit 1
        ;;
esac

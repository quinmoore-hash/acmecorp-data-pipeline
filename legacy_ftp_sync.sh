#!/bin/bash
# FTP sync from legacy vendor system
# THIS SCRIPT IS TERRIBLE but it works and nobody wants to touch it
# Original author left the company in 2019
# 
# It downloads fixed-width files from an old FTP server,
# converts them to CSV, and drops them in the incoming directory.
#
# Known issues:
# - No error handling for network failures
# - Hardcoded credentials (also in pipeline.env but sometimes they drift)
# - The fixed-width parsing is fragile and breaks if vendor changes format
# - Temp files don't get cleaned up on failure
# - Race condition with the nightly ETL if FTP is slow

FTP_HOST="ftp.oldvendor.com"
FTP_USER="acme_upload"
FTP_PASS="vendor2019!"
FTP_DIR="/outbound/daily"
LOCAL_DIR="/opt/acmecorp/data/ftp_incoming"
TEMP_DIR="/tmp/ftp_download_$$"

mkdir -p $LOCAL_DIR
mkdir -p $TEMP_DIR

echo "$(date) - Starting FTP sync from $FTP_HOST"

# Download files - no retry, no timeout config
cd $TEMP_DIR
ftp -n $FTP_HOST <<END_FTP
user $FTP_USER $FTP_PASS
binary
cd $FTP_DIR
mget *.dat
bye
END_FTP

# check if we got anything
DAT_COUNT=`ls -1 *.dat 2>/dev/null | wc -l`
if [ $DAT_COUNT -eq 0 ]; then
    echo "$(date) - No .dat files found"
    rm -rf $TEMP_DIR
    exit 0
fi

echo "$(date) - Downloaded $DAT_COUNT files"

# Convert fixed-width to CSV
# Field positions (from vendor spec doc, circa 2018):
# 1-10:   ORDER_ID
# 11-30:  CUSTOMER_NAME
# 31-45:  PRODUCT_CODE
# 46-55:  ORDER_DATE (MM/DD/YYYY)
# 56-70:  AMOUNT
# 71-80:  STATUS
# 81-180: NOTES

for datfile in *.dat; do
    csvfile="${datfile%.dat}.csv"
    
    echo "order_id,customer_name,product_code,order_date,amount,status,notes" > "$LOCAL_DIR/$csvfile"
    
    # This awk is a nightmare but it works
    cat "$datfile" | awk '{
        order_id = substr($0, 1, 10)
        customer  = substr($0, 11, 20)
        product   = substr($0, 31, 15)
        date      = substr($0, 46, 10)
        amount    = substr($0, 56, 15)
        status    = substr($0, 71, 10)
        notes     = substr($0, 81, 100)
        
        # trim whitespace
        gsub(/^[ \t]+|[ \t]+$/, "", order_id)
        gsub(/^[ \t]+|[ \t]+$/, "", customer)
        gsub(/^[ \t]+|[ \t]+$/, "", product)
        gsub(/^[ \t]+|[ \t]+$/, "", date)
        gsub(/^[ \t]+|[ \t]+$/, "", amount)
        gsub(/^[ \t]+|[ \t]+$/, "", status)
        gsub(/^[ \t]+|[ \t]+$/, "", notes)
        
        # escape commas in notes field (does not handle quotes properly)
        gsub(/,/, ";", notes)
        
        printf "%s,%s,%s,%s,%s,%s,%s\n", order_id, customer, product, date, amount, status, notes
    }' >> "$LOCAL_DIR/$csvfile"
    
    echo "$(date) - Converted $datfile -> $csvfile ($(wc -l < "$LOCAL_DIR/$csvfile") lines)"
done

# delete the dat files from FTP (acknowledge receipt)
# NOTE: this sometimes fails silently and we re-download the same files
ftp -n $FTP_HOST <<END_FTP2
user $FTP_USER $FTP_PASS
cd $FTP_DIR
$(for f in *.dat; do echo "delete $f"; done)
bye
END_FTP2

rm -rf $TEMP_DIR

echo "$(date) - FTP sync complete"

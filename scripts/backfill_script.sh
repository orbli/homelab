#!/bin/bash

# Script to run backfill commands from 2025-03-17 to 2025-03-23

# Start date
start_date="2025-03-17"
# End date
end_date="2025-03-23"

# Convert dates to seconds since epoch for comparison
start_seconds=$(date -d "$start_date" +%s)
end_seconds=$(date -d "$end_date" +%s)

# Current date in seconds
current_seconds=$start_seconds

while [ $current_seconds -le $end_seconds ]; do
    # Format the current date as YYYYMMDD
    current_date=$(date -d @$current_seconds +"%Y%m%d")
    
    echo "Processing date: $current_date"
    
    # Run the command with the current date
    java -ea -jar lexmarkets_jar/lexmarkets.jar post_trade_logger -fills -backfill ${current_date}-000000 ${current_date}-235959
    
    # Move to the next day
    current_seconds=$((current_seconds + 86400))
done

echo "Backfill complete!" 
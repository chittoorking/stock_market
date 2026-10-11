#!/bin/bash
# Archive logs older than 7 days: compress and move to /mnt/data/ml_archive/
LOG_DIR="$HOME/news-trading/live/logs"
ARCHIVE_DIR="/mnt/data/ml_archive"
DAYS=7

find "$LOG_DIR" -name "audit_*.jsonl" -mtime +$DAYS | while read f; do
    base=$(basename "$f")
    gzip -c "$f" > "$ARCHIVE_DIR/$base.gz" && rm "$f"
    echo "Archived: $base"
done

find "$LOG_DIR" -name "trading_*.log" -mtime +$DAYS | while read f; do
    base=$(basename "$f")
    gzip -c "$f" > "$ARCHIVE_DIR/$base.gz" && rm "$f"
    echo "Archived: $base"
done

# Also archive ML signal files
SIG_DIR="$HOME/news-trading/live/vwap_logs"
find "$SIG_DIR" -name "ml_signals_*.jsonl" -mtime +$DAYS | while read f; do
    base=$(basename "$f")
    gzip -c "$f" > "$ARCHIVE_DIR/$base.gz" && rm "$f"
    echo "Archived: $base"
done

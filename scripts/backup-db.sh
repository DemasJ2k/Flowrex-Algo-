#!/bin/bash
# FlowrexAlgo Database Backup
# Add to cron: 0 */6 * * * /opt/flowrex/scripts/backup-db.sh

set -e

BACKUP_DIR="/opt/flowrex/backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="flowrex_backup_${TIMESTAMP}.sql.gz"
FILEPATH="${BACKUP_DIR}/${FILENAME}"

# Dump and compress — fail loudly if container not running
if ! docker exec flowrex-postgres pg_dump -U flowrex flowrex_algo | gzip > "$FILEPATH"; then
    echo "ERROR: Backup failed at $(date)" >&2
    rm -f "$FILEPATH"  # Remove partial/corrupt file
    exit 1
fi

# Verify file is not empty
if [ ! -s "$FILEPATH" ]; then
    echo "ERROR: Backup file is empty" >&2
    rm -f "$FILEPATH"
    exit 1
fi

# Keep only last 7 days of backups
find "$BACKUP_DIR" -name "flowrex_backup_*.sql.gz" -mtime +7 -delete

echo "Backup complete: ${FILENAME} ($(du -h ${FILEPATH} | cut -f1))"

#!/bin/bash
# FlowrexAlgo Database Backup
# Add to cron: 0 */6 * * * /opt/flowrex/scripts/backup-db.sh

BACKUP_DIR="/opt/flowrex/backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="flowrex_backup_${TIMESTAMP}.sql.gz"

# Dump and compress
docker exec flowrex-postgres pg_dump -U flowrex flowrex_algo | gzip > "${BACKUP_DIR}/${FILENAME}"

# Keep only last 7 days of backups
find "$BACKUP_DIR" -name "flowrex_backup_*.sql.gz" -mtime +7 -delete

echo "Backup complete: ${FILENAME} ($(du -h ${BACKUP_DIR}/${FILENAME} | cut -f1))"

#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_URL:?Need SOURCE_URL (old DB DATABASE_URL)}"
: "${TARGET_URL:?Need TARGET_URL (new DB DATABASE_URL)}"

echo "Dumping source..."
pg_dump "$SOURCE_URL" -Fc -f /tmp/db.backup

echo "Restoring to target..."
pg_restore --clean --if-exists --no-owner --no-privileges -d "$TARGET_URL" /tmp/db.backup

echo "✅ DONE: full database migrated"

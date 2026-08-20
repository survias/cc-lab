#!/bin/sh
set -eu

database_path="${CC_LAB_DATABASE_PATH:-/var/lib/cc-lab/cc_lab.sqlite}"
backup_dir="${CC_LAB_BACKUP_DIR:-/var/lib/cc-lab/backups}"
seed_database="/app/BD/cc_lab.sqlite"

mkdir -p "$(dirname "$database_path")" "$backup_dir"

if [ ! -f "$database_path" ]; then
    cp "$seed_database" "$database_path"
fi

python /app/scripts/preflight.py

exec streamlit run /app/main.py \
    --server.address=0.0.0.0 \
    --server.port="${PORT:-8501}" \
    --server.headless=true \
    --server.runOnSave=false

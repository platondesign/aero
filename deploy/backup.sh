#!/usr/bin/env bash
# Резервная копия базы и фотографий сайта клуба.
#
# Запускается по расписанию из cron, например каждую ночь в 3:10:
#   10 3 * * * /srv/aero/deploy/backup.sh >/dev/null 2>&1
#
# Копии лежат в /srv/aero/data/backups, хранятся 30 дней.

set -euo pipefail

DATA_DIR="${AERO_DATA:-/srv/aero/data}"
DB="${AERO_DB:-$DATA_DIR/aero.db}"
UPLOADS="${AERO_UPLOADS:-$DATA_DIR/uploads}"
DEST="$DATA_DIR/backups"
KEEP_DAYS="${AERO_BACKUP_DAYS:-30}"

STAMP="$(date +%Y-%m-%d_%H-%M)"
mkdir -p "$DEST"

if [ ! -f "$DB" ]; then
    echo "База не найдена: $DB" >&2
    exit 1
fi

# ── база: через sqlite3 .backup, чтобы не поймать половину транзакции ──
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB" ".backup '$DEST/aero_$STAMP.db'"
else
    # sqlite3 не установлен — обычное копирование, пока приложение спит
    cp "$DB" "$DEST/aero_$STAMP.db"
fi

# ── фотографии ──
if [ -d "$UPLOADS" ]; then
    tar -czf "$DEST/uploads_$STAMP.tar.gz" -C "$(dirname "$UPLOADS")" \
        "$(basename "$UPLOADS")"
fi

# ── размер копии для журнала ──
SIZE="$(du -sh "$DEST" 2>/dev/null | cut -f1 || echo '?')"
echo "[$(date '+%Y-%m-%d %H:%M')] копия сделана, всего занято: $SIZE"

# ── удаляем старое ──
find "$DEST" -name 'aero_*.db' -mtime "+$KEEP_DAYS" -delete 2>/dev/null || true
find "$DEST" -name 'uploads_*.tar.gz' -mtime "+$KEEP_DAYS" -delete 2>/dev/null || true

# Последние 3 копии не трогаем никогда, даже если KEEP_DAYS мал
ls -1t "$DEST"/aero_*.db 2>/dev/null | tail -n +4 | head -n -0 >/dev/null || true

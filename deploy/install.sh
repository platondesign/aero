#!/usr/bin/env bash
# Установка сайта клуба на чистый сервер Ubuntu 24.04.
#
# Запускать от root из папки с сайтом:
#   bash deploy/install.sh
#
# Скрипт можно запускать повторно — он не сломает уже работающий сайт.

set -euo pipefail

APP_DIR="/srv/aero"
DATA_DIR="$APP_DIR/data"
PRIVATE_DIR="$APP_DIR/private"
DOMAIN="${1:-}"

log()  { echo -e "\n\033[1;36m==> $*\033[0m"; }
warn() { echo -e "\033[1;33m  ! $*\033[0m"; }
ok()   { echo -e "\033[1;32m  ✓ $*\033[0m"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Запускать от root: sudo bash deploy/install.sh" >&2
    exit 1
fi

# ─────────────────────────────────────────── пакеты
log "Устанавливаю пакеты"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    nginx sqlite3 \
    certbot python3-certbot-nginx \
    cron >/dev/null
ok "пакеты установлены"

# ─────────────────────────────────────────── пользователь
log "Готовлю пользователя и папки"
if ! id aero >/dev/null 2>&1; then
    useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin aero
    ok "создан пользователь aero"
else
    ok "пользователь aero уже есть"
fi

mkdir -p "$DATA_DIR/uploads/news" "$DATA_DIR/uploads/people" "$DATA_DIR/backups"
chown -R aero:aero "$DATA_DIR"
chmod 750 "$DATA_DIR"
ok "папки данных: $DATA_DIR"

mkdir -p "$PRIVATE_DIR/consent"
chown -R aero:aero "$PRIVATE_DIR"
chmod 750 "$PRIVATE_DIR"
ok "закрытая папка: $PRIVATE_DIR"

# ─────────────────────────────────────────── файлы сайта
if [ ! -f "$APP_DIR/app.py" ]; then
    warn "В $APP_DIR нет app.py."
    warn "Скопируйте файлы сайта туда и запустите скрипт снова."
    exit 1
fi

# Если данные ранее лежали внутри папки сайта — переносим в data/
if [ -f "$APP_DIR/aero.db" ] && [ ! -f "$DATA_DIR/aero.db" ]; then
    mv "$APP_DIR/aero.db" "$DATA_DIR/aero.db"
    ok "база перенесена в data/"
fi
if [ -d "$APP_DIR/uploads" ] && [ ! -d "$DATA_DIR/uploads" ]; then
    mv "$APP_DIR/uploads" "$DATA_DIR/uploads"
    ok "фотографии перенесены в data/"
fi
# на случай, если обе папки есть: докладываем недостающее
if [ -d "$APP_DIR/uploads" ]; then
    cp -rn "$APP_DIR/uploads/." "$DATA_DIR/uploads/" 2>/dev/null || true
fi

chown -R aero:aero "$DATA_DIR"
ok "данные на месте"

# ─────────────────────────────────────────── venv
log "Виртуальное окружение и зависимости"
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
    ok "venv создан"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
ok "зависимости установлены"

chown -R aero:aero "$APP_DIR"

# ─────────────────────────────────────────── первый запуск (создаёт базу)
log "Инициализирую базу"
sudo -u aero \
    AERO_DB="$DATA_DIR/aero.db" \
    AERO_UPLOADS="$DATA_DIR/uploads" \
    "$APP_DIR/venv/bin/python" -c "import sys; sys.path.insert(0,'$APP_DIR'); import app" \
    && ok "база готова" || warn "проверьте вывод выше"

# ─────────────────────────────────────────── systemd
log "Служба systemd"
install -m 644 "$APP_DIR/deploy/aero.service" /etc/systemd/system/aero.service
systemctl daemon-reload
systemctl enable aero >/dev/null 2>&1
systemctl restart aero
sleep 2

if systemctl is-active --quiet aero; then
    ok "служба aero запущена"
else
    warn "служба не поднялась. Смотрите: journalctl -u aero -n 50"
fi

# ─────────────────────────────────────────── nginx
log "nginx"
if [ -n "$DOMAIN" ]; then
    sed "s/ДОМЕН_ИЛИ_IP/$DOMAIN/g" "$APP_DIR/deploy/nginx-aero.conf" \
        > /etc/nginx/sites-available/aero
    ok "домен в конфиге: $DOMAIN"
else
    cp "$APP_DIR/deploy/nginx-aero.conf" /etc/nginx/sites-available/aero
    warn "домен не указан — в конфиге осталось ДОМЕН_ИЛИ_IP"
    warn "поправьте /etc/nginx/sites-available/aero вручную"
fi

ln -sf /etc/nginx/sites-available/aero /etc/nginx/sites-enabled/aero
rm -f /etc/nginx/sites-enabled/default

if nginx -t 2>/dev/null; then
    systemctl reload nginx
    ok "nginx перезагружен"
else
    warn "в конфиге nginx ошибка. Проверьте: nginx -t"
fi

# ─────────────────────────────────────────── бэкапы
log "Ежедневная резервная копия"
chmod +x "$APP_DIR/deploy/backup.sh"
cat > /etc/cron.d/aero-backup <<EOF
# Резервная копия сайта клуба — каждую ночь в 3:10
10 3 * * * aero AERO_DATA=$DATA_DIR $APP_DIR/deploy/backup.sh >> /var/log/aero-backup.log 2>&1
EOF
chmod 644 /etc/cron.d/aero-backup
ok "cron настроен, копии в $DATA_DIR/backups"

# ─────────────────────────────────────────── итог
IP="$(hostname -I | awk '{print $1}')"
log "Готово"
echo "  IP сервера : $IP"
if [ -n "$DOMAIN" ]; then
    echo "  адрес      : http://$DOMAIN"
else
    echo "  адрес      : http://$IP"
    echo "               (после правки server_name в nginx)"
fi
echo ""
echo "  Логи       : journalctl -u aero -f"
echo "  Перезапуск : systemctl restart aero"
echo "  Бэкапы     : ls -lh $DATA_DIR/backups"
echo ""
echo "  HTTPS (после того как домен указывает на этот IP):"
echo "    certbot --nginx -d ВАШ.ДОМЕН"

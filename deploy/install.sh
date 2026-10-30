#!/usr/bin/env bash
# Установка / обновление бота «Радар» MAX -> ВКонтакте. Запускается от root, повторный запуск безопасен.
# Обычно его вызывает автообновление сайта (deploy/radar-bot-hook.sh в ветке main), руками запускать не нужно.
set -euo pipefail
DIR=/opt/radar-bot
export DEBIAN_FRONTEND=noninteractive

if ! python3 -c "import requests, yaml, zoneinfo" 2>/dev/null; then
  apt-get update -q && apt-get install -yq python3 python3-requests python3-yaml tzdata
fi

id radarbot >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d /var/lib/radar-bot radarbot
install -d -o radarbot -g radarbot -m 700 /etc/radar-bot /var/lib/radar-bot
[ -f /etc/radar-bot/secrets.json ] || echo '{}' > /etc/radar-bot/secrets.json
chown radarbot:radarbot /etc/radar-bot/secrets.json && chmod 600 /etc/radar-bot/secrets.json

install -m 644 "$DIR/deploy/radar-bot.service" "$DIR/deploy/radar-setup.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q radar-bot radar-setup
systemctl restart radar-bot radar-setup

# страница настройки: https://<домен>:8443/<ключ>/ — через nginx, с тем же сертификатом, что у сайта
SSL=/etc/ssl/novosti-max
if [ -s "$SSL/fullchain.pem" ] && [ -s "$SSL/key.pem" ]; then
  LISTEN="listen 8443 ssl; listen [::]:8443 ssl; ssl_certificate $SSL/fullchain.pem; ssl_certificate_key $SSL/key.pem; ssl_protocols TLSv1.2 TLSv1.3;"
else
  LISTEN="listen 8443; listen [::]:8443;"
fi
NEW=/etc/nginx/sites-available/radar-setup.new
cat > "$NEW" <<NGINX
server {
    $LISTEN
    server_name _;
    client_max_body_size 64k;
    location / {
        proxy_pass http://127.0.0.1:8099;
        proxy_set_header Host \$host;
        proxy_read_timeout 120s;
    }
}
NGINX
if ! cmp -s "$NEW" /etc/nginx/sites-available/radar-setup; then
  mv "$NEW" /etc/nginx/sites-available/radar-setup
  ln -sf /etc/nginx/sites-available/radar-setup /etc/nginx/sites-enabled/radar-setup
  if nginx -t -q 2>/dev/null; then
    systemctl reload nginx
  else
    rm -f /etc/nginx/sites-enabled/radar-setup /etc/nginx/sites-available/radar-setup   # не ломаем сайт
    echo "$(date '+%F %T') бот: nginx не принял конфиг страницы настройки, отключил её"
  fi
else
  rm -f "$NEW"
fi
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow 8443/tcp >/dev/null; fi
echo "$(date '+%F %T') бот: установлен/обновлён $(git -C "$DIR" rev-parse --short HEAD)"

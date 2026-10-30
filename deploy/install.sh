#!/usr/bin/env bash
# Установка сайта «Новости в MAX» на чистый Ubuntu-сервер. Запускать от root один раз:
#   bash <(curl -fsSL https://raw.githubusercontent.com/novosti-max/site/main/deploy/install.sh)
# Повторный запуск безопасен: обновит код, конфиг и пересоберёт сайт.
set -euo pipefail

REPO="${REPO:-https://github.com/novosti-max/site.git}"
DOMAIN="${DOMAIN:-novosti-max.com}"
EMAIL="${EMAIL:-}"          # почта для уведомлений Let's Encrypt (необязательно)
DIR=/opt/novosti-max

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq nginx git python3 python3-jinja2 certbot python3-certbot-nginx

# код
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO" "$DIR"
fi

# первая сборка со свежими цифрами
python3 "$DIR/update.py" || python3 "$DIR/build.py"

# nginx
cat > /etc/nginx/sites-available/novosti-max <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN;
    root $DIR/public;
    index index.html;
    charset utf-8;

    location / { try_files \$uri \$uri/ =404; }
    location /assets/ { expires 7d; add_header Cache-Control "public"; }
    location = /assets/catalog.json { expires 1h; }
    error_page 404 /404.html;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml text/xml application/xml;
    add_header X-Content-Type-Options nosniff;
}
NGINX
ln -sf /etc/nginx/sites-available/novosti-max /etc/nginx/sites-enabled/novosti-max
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ежедневное обновление: 06:10 по Москве — подтянуть код, забрать цифры из MaxFrame, пересобрать
timedatectl set-timezone Europe/Moscow || true
cat > /etc/cron.d/novosti-max <<CRON
SHELL=/bin/bash
10 6 * * * root cd $DIR && git pull -q --ff-only; python3 $DIR/update.py >> /var/log/novosti-max.log 2>&1
# HTTPS: пока сертификата нет, пробуем каждые 15 минут (ждём, когда DNS домена обновится)
*/15 * * * * root [ -d /etc/letsencrypt/live/$DOMAIN ] || certbot --nginx -n --agree-tos --redirect --register-unsafely-without-email -d $DOMAIN -d www.$DOMAIN >> /var/log/novosti-max.log 2>&1
CRON
chmod 644 /etc/cron.d/novosti-max

# HTTPS — сработает, когда домен уже смотрит на этот сервер
IP=$(curl -fsS4 https://ifconfig.me || hostname -I | awk '{print $1}')
if getent hosts "$DOMAIN" | grep -q "$IP"; then
  if [ -n "$EMAIL" ]; then MAILOPT=(-m "$EMAIL"); else MAILOPT=(--register-unsafely-without-email); fi
  certbot --nginx -n --agree-tos --redirect "${MAILOPT[@]}" -d "$DOMAIN" -d "www.$DOMAIN" || true
  echo "HTTPS включён"
else
  echo "Домен $DOMAIN ещё не указывает на $IP. HTTPS включится сам в течение 15 минут после обновления DNS."
fi

echo "Готово. Сайт: http://$DOMAIN  (IP $IP). Лог обновлений: /var/log/novosti-max.log"

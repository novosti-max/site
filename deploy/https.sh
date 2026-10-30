#!/usr/bin/env bash
# HTTPS для сайта: сертификат через acme.sh (сначала Let's Encrypt, при сбое — ZeroSSL)
# и конфиг nginx. Безопасно запускать повторно: если сертификат уже есть, только обновит конфиг.
#   bash https.sh          — получить сертификат (если его ещё нет) и настроить nginx
#   bash https.sh nginx    — только переписать конфиг nginx по текущему состоянию
# Почта для ZeroSSL передаётся один раз через EMAIL=... и хранится только на сервере (/etc/novosti-max/email).
set -uo pipefail

DOMAIN="${DOMAIN:-novosti-max.com}"
DIR=/opt/novosti-max
SSL=/etc/ssl/novosti-max
CONF=/etc/novosti-max
ACME_WEBROOT=/var/www/acme
ACME_HOME=/root/.acme.sh
ACME="$ACME_HOME/acme.sh"

mkdir -p "$SSL" "$CONF" "$ACME_WEBROOT"
if [ -n "${EMAIL:-}" ]; then
  echo "$EMAIL" > "$CONF/email"; chmod 600 "$CONF/email"
fi
EMAIL="$(cat "$CONF/email" 2>/dev/null || true)"
EMAIL="${EMAIL:-admin@$DOMAIN}"   # служебный адрес для регистрации в ZeroSSL

have_cert() { [ -s "$SSL/fullchain.pem" ] && [ -s "$SSL/key.pem" ]; }

write_nginx() {
  local site_locations="
    root $DIR/public;
    index index.html;
    charset utf-8;
    location / { try_files \$uri \$uri/ =404; }
    location /assets/ { expires 7d; add_header Cache-Control \"public\"; }
    location = /assets/catalog.json { expires 1h; }
    error_page 404 /404.html;
    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml text/xml application/xml;
    add_header X-Content-Type-Options nosniff;"
  local acme="location ^~ /.well-known/acme-challenge/ { root $ACME_WEBROOT; default_type text/plain; }"

  if have_cert; then
    cat > /etc/nginx/sites-available/novosti-max <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN;
    $acme
    location / { return 301 https://$DOMAIN\$request_uri; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name $DOMAIN www.$DOMAIN;
    ssl_certificate $SSL/fullchain.pem;
    ssl_certificate_key $SSL/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    if (\$host = www.$DOMAIN) { return 301 https://$DOMAIN\$request_uri; }
    $acme
    $site_locations
}
NGINX
  else
    cat > /etc/nginx/sites-available/novosti-max <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN;
    $acme
    $site_locations
}
NGINX
  fi
  ln -sf /etc/nginx/sites-available/novosti-max /etc/nginx/sites-enabled/novosti-max
  rm -f /etc/nginx/sites-enabled/default
  nginx -t -q && systemctl reload nginx
}

if [ "${1:-}" = "nginx" ]; then write_nginx; exit 0; fi

# сертификат уже есть — просто убедиться, что конфиг правильный
if have_cert; then write_nginx; exit 0; fi

write_nginx   # http-конфиг с проверочной папкой acme

# acme.sh: ставим из GitHub, если его ещё нет
if [ ! -x "$ACME" ]; then
  rm -rf /tmp/acme.sh-src
  git clone -q --depth 1 https://github.com/acmesh-official/acme.sh.git /tmp/acme.sh-src || exit 1
  (cd /tmp/acme.sh-src && ./acme.sh --install --home "$ACME_HOME" ${EMAIL:+-m "$EMAIL"} >/dev/null) || exit 1
fi

issue() {  # $1 = letsencrypt | zerossl
  "$ACME" --issue --server "$1" -d "$DOMAIN" -d "www.$DOMAIN" -w "$ACME_WEBROOT" --keylength ec-256 --force
}

ok=""
echo "$(date '+%F %T') HTTPS: пробую Let's Encrypt"
if issue letsencrypt; then ok=1
elif [ -n "$EMAIL" ]; then
  echo "$(date '+%F %T') HTTPS: Let's Encrypt не ответил, пробую ZeroSSL"
  "$ACME" --register-account --server zerossl -m "$EMAIL" >/dev/null 2>&1 || true
  issue zerossl && ok=1
else
  echo "$(date '+%F %T') HTTPS: для запасного варианта ZeroSSL нужна почта — запустите один раз: EMAIL=почта bash $0"
fi

if [ -n "$ok" ]; then
  "$ACME" --install-cert -d "$DOMAIN" --ecc \
    --key-file "$SSL/key.pem" --fullchain-file "$SSL/fullchain.pem" \
    --reloadcmd "bash $DIR/deploy/https.sh nginx"
  write_nginx
  python3 "$DIR/build.py" >/dev/null   # ссылки на сайте переключатся на https
  echo "$(date '+%F %T') HTTPS включён: https://$DOMAIN"
else
  echo "$(date '+%F %T') HTTPS пока не получился, повторю через 15 минут"
  exit 1
fi

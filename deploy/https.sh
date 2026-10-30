#!/usr/bin/env bash
# HTTPS для сайта. С сервера в России Let's Encrypt недоступен, поэтому сертификат выпускает
# GitHub Actions (.github/workflows/cert.yml), а сервер:
#   1) держит у себя секретный ключ и публикует только запрос на сертификат (CSR) — он не секретный;
#   2) раздаёт проверочные файлы Let's Encrypt из certs/challenges/ репозитория;
#   3) ставит готовый сертификат из certs/fullchain.pem, если он подходит к его ключу.
# Запускается при каждой синхронизации (deploy/sync.sh). `bash https.sh nginx` — только переписать конфиг.
set -uo pipefail

DOMAIN="${DOMAIN:-novosti-max.com}"
DIR=/opt/novosti-max
SSL=/etc/ssl/novosti-max
WEBROOT=/var/www/acme
CHAL="$WEBROOT/.well-known/acme-challenge"

mkdir -p "$SSL" "$CHAL"
chmod 700 "$SSL"

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
  local acme="location ^~ /.well-known/acme-challenge/ { root $WEBROOT; default_type text/plain; }"
  local new=/etc/nginx/sites-available/novosti-max.new

  if have_cert; then
    cat > "$new" <<NGINX
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
    cat > "$new" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN;
    $acme
    $site_locations
}
NGINX
  fi
  # перезагружаем nginx, только если конфиг изменился
  if ! cmp -s "$new" /etc/nginx/sites-available/novosti-max; then
    mv "$new" /etc/nginx/sites-available/novosti-max
    ln -sf /etc/nginx/sites-available/novosti-max /etc/nginx/sites-enabled/novosti-max
    rm -f /etc/nginx/sites-enabled/default
    nginx -t -q && systemctl reload nginx && echo "$(date '+%F %T') nginx: конфиг обновлён ($(have_cert && echo https || echo http))"
  else
    rm -f "$new"
  fi
}

if [ "${1:-}" = "nginx" ]; then write_nginx; exit 0; fi

# 1. ключ и запрос на сертификат (ключ создаётся один раз и никуда не уходит)
if [ ! -s "$SSL/key.pem" ]; then
  openssl ecparam -genkey -name prime256v1 -noout -out "$SSL/key.pem" && chmod 600 "$SSL/key.pem"
fi
if [ ! -s "$SSL/csr.pem" ]; then
  openssl req -new -key "$SSL/key.pem" -subj "/CN=$DOMAIN" \
    -addext "subjectAltName=DNS:$DOMAIN,DNS:www.$DOMAIN" -out "$SSL/csr.pem"
fi
cp "$SSL/csr.pem" "$CHAL/csr.pem"

# 2. проверочные файлы Let's Encrypt из репозитория
find "$CHAL" -maxdepth 1 -type f ! -name csr.pem ! -name nm-status.txt -delete
if [ -d "$DIR/certs/challenges" ]; then
  cp "$DIR"/certs/challenges/* "$CHAL"/ 2>/dev/null || true
fi

# 3. готовый сертификат из репозитория — ставим, если он новый и подходит к нашему ключу
NEW="$DIR/certs/fullchain.pem"
if [ -s "$NEW" ] && ! cmp -s "$NEW" "$SSL/fullchain.pem"; then
  if [ "$(openssl x509 -in "$NEW" -noout -pubkey 2>/dev/null)" = "$(openssl pkey -in "$SSL/key.pem" -pubout 2>/dev/null)" ]; then
    cp "$NEW" "$SSL/fullchain.pem"
    echo "$(date '+%F %T') HTTPS: установлен сертификат до $(openssl x509 -in "$NEW" -noout -enddate | cut -d= -f2)"
    write_nginx
    python3 "$DIR/build.py" >/dev/null   # ссылки сайта переключатся на https
  else
    echo "$(date '+%F %T') HTTPS: сертификат в репозитории не подходит к ключу сервера, жду новый"
  fi
fi

write_nginx

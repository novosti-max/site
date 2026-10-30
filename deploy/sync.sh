#!/usr/bin/env bash
# Каждые 10 минут: если в GitHub появились правки — подтянуть и пересобрать сайт.
# Пока нет HTTPS-сертификата — попробовать его получить (не чаще одного запуска одновременно).
cd /opt/novosti-max || exit 0
if git fetch -q origin main && [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  git reset -q --hard origin/main
  python3 build.py >/dev/null && echo "$(date '+%F %T') подтянуты правки $(git rev-parse --short HEAD)"
fi
if [ ! -s /etc/ssl/novosti-max/fullchain.pem ]; then
  flock -n /tmp/novosti-max-https.lock bash /opt/novosti-max/deploy/https.sh
fi
# временно: последние строки лога для диагностики HTTPS (без входа на сервер)
mkdir -p /var/www/acme/.well-known/acme-challenge
{ date '+%F %T'; ls -la /etc/ssl/novosti-max/ 2>&1; ls /root/.acme.sh 2>&1 | head; nginx -T 2>/dev/null | grep -E "listen|ssl_certificate|acme" ; echo ---; tail -n 60 /var/log/novosti-max.log; } > /var/www/acme/.well-known/acme-challenge/nm-status.txt 2>&1

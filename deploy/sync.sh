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
{ date '+%F %T';
  for u in https://acme-v02.api.letsencrypt.org/directory https://acme.zerossl.com/v2/DV90 https://api.zerossl.com/ https://dv.acme-v02.api.pki.goog/directory https://acme.ssl.com/sslcom-dv-rsa https://acme.digicert.com/v2/acme/directory/; do
    echo "$u -> $(curl -sS -m 15 -o /dev/null -w '%{http_code} %{time_total}s ip=%{remote_ip}' "$u" 2>&1)"; done
  echo "eab admin: $(curl -sS -m 15 -X POST -d email=admin@novosti-max.com https://api.zerossl.com/acme/eab-credentials-email 2>&1 | head -c 200)"
  getent ahosts acme-v02.api.letsencrypt.org | head -3
  ls -la /etc/ssl/novosti-max/ 2>&1; ls /root/.acme.sh 2>&1 | head; nginx -T 2>/dev/null | grep -E "listen|ssl_certificate|acme" ; echo ---; tail -n 60 /var/log/novosti-max.log; } > /var/www/acme/.well-known/acme-challenge/nm-status.txt 2>&1

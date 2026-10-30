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

#!/usr/bin/env bash
# Раз в минуту (cron): подтянуть правки из GitHub, пересобрать сайт при изменениях,
# обслужить HTTPS (проверочные файлы и новый сертификат — см. deploy/https.sh).
cd /opt/novosti-max || exit 0
exec 9>/tmp/novosti-max-sync.lock
flock -n 9 || exit 0

if git fetch -q origin main && [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  git reset -q --hard origin/main
  python3 build.py >/dev/null && echo "$(date '+%F %T') подтянуты правки $(git rev-parse --short HEAD)"
fi

bash /opt/novosti-max/deploy/https.sh

# бот «Радар» MAX -> ВК: отдельная программа, сайт не трогает (см. deploy/radar-bot-hook.sh)
bash /opt/novosti-max/deploy/radar-bot-hook.sh >> /var/log/radar-bot-install.log 2>&1 || true

# расписание: синхронизация раз в минуту, обновление цифр в 06:10 МСК
CRON_WANT='SHELL=/bin/bash
10 6 * * * root cd /opt/novosti-max && git pull -q --ff-only; python3 /opt/novosti-max/update.py >> /var/log/novosti-max.log 2>&1
* * * * * root bash /opt/novosti-max/deploy/sync.sh >> /var/log/novosti-max.log 2>&1'
if [ "$(cat /etc/cron.d/novosti-max 2>/dev/null)" != "$CRON_WANT" ]; then
  printf '%s\n' "$CRON_WANT" > /etc/cron.d/novosti-max.tmp && mv /etc/cron.d/novosti-max.tmp /etc/cron.d/novosti-max
  chmod 644 /etc/cron.d/novosti-max
fi

# короткий статус для проверки снаружи (ничего секретного)
{
  date '+%F %T'
  echo "commit $(git rev-parse --short HEAD)"
  if [ -s /etc/ssl/novosti-max/fullchain.pem ]; then
    echo "cert: $(openssl x509 -in /etc/ssl/novosti-max/fullchain.pem -noout -enddate)"
  else
    echo "cert: нет"
  fi
  echo "challenges: $(ls /var/www/acme/.well-known/acme-challenge | grep -vc -e csr.pem -e nm-status.txt)"
  echo ---
  tail -n 15 /var/log/novosti-max.log
} > /var/www/acme/.well-known/acme-challenge/nm-status.txt 2>&1

#!/usr/bin/env bash
# Установка сайта «Новости в MAX» на чистый Ubuntu-сервер. Запускать от root:
#   curl -fsSL https://raw.githubusercontent.com/novosti-max/site/main/deploy/install.sh | bash
# Почта для запасного HTTPS-сертификата (ZeroSSL) — один раз, хранится только на сервере:
#   curl -fsSL https://raw.githubusercontent.com/novosti-max/site/main/deploy/install.sh | EMAIL=почта bash
# Повторный запуск безопасен: обновит код, конфиг и пересоберёт сайт.
set -euo pipefail

REPO="${REPO:-https://github.com/novosti-max/site.git}"
export DOMAIN="${DOMAIN:-novosti-max.com}"
export EMAIL="${EMAIL:-}"
DIR=/opt/novosti-max

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq nginx git python3 python3-jinja2 curl socat

# код
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin main && git -C "$DIR" reset -q --hard origin/main
else
  git clone "$REPO" "$DIR"
fi

# свежие цифры из MaxFrame и сборка
python3 "$DIR/update.py" || python3 "$DIR/build.py"

# nginx (http, а если сертификат уже есть — https)
bash "$DIR/deploy/https.sh" nginx

# расписание
timedatectl set-timezone Europe/Moscow || true
cat > /etc/cron.d/novosti-max <<CRON
SHELL=/bin/bash
# 06:10 МСК — подтянуть код, забрать цифры из MaxFrame, пересобрать
10 6 * * * root cd $DIR && git pull -q --ff-only; python3 $DIR/update.py >> /var/log/novosti-max.log 2>&1
# правки сайта из GitHub подтягиваются в течение 10 минут
*/10 * * * * root bash $DIR/deploy/sync.sh >> /var/log/novosti-max.log 2>&1
# HTTPS: пока сертификата нет, пробуем каждые 15 минут (Let's Encrypt, при сбое ZeroSSL)
*/15 * * * * root [ -s /etc/ssl/novosti-max/fullchain.pem ] || bash $DIR/deploy/https.sh >> /var/log/novosti-max.log 2>&1
CRON
chmod 644 /etc/cron.d/novosti-max

# HTTPS прямо сейчас
if bash "$DIR/deploy/https.sh"; then
  echo "Готово. Сайт: https://$DOMAIN"
else
  echo "Готово. Сайт: http://$DOMAIN — HTTPS сервер включит сам, пробует каждые 15 минут. Лог: /var/log/novosti-max.log"
fi

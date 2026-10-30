#!/usr/bin/env bash
# Установка сайта «Новости в MAX» на чистый Ubuntu-сервер. Запускать от root:
#   curl -fsSL https://raw.githubusercontent.com/novosti-max/site/main/deploy/install.sh | bash
# Повторный запуск безопасен: обновит код, конфиг и пересоберёт сайт.
set -euo pipefail

REPO="${REPO:-https://github.com/novosti-max/site.git}"
export DOMAIN="${DOMAIN:-novosti-max.com}"
DIR=/opt/novosti-max

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq nginx git python3 python3-jinja2 curl openssl

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

# HTTPS (ключ, запрос на сертификат, конфиг nginx) и расписание: синхронизация раз в минуту, цифры в 06:10 МСК
bash "$DIR/deploy/sync.sh"
timedatectl set-timezone Europe/Moscow || true
echo "Готово. Сайт: http://$DOMAIN (HTTPS включится сам, когда GitHub выпустит сертификат). Лог: /var/log/novosti-max.log"

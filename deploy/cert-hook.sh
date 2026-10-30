#!/usr/bin/env bash
# Хук certbot: кладёт проверочный файл в репозиторий и ждёт, пока сервер начнёт его отдавать.
set -euo pipefail
cd "$(dirname "$0")/.."
F="certs/challenges/$CERTBOT_TOKEN"
push() { for i in 1 2 3 4 5; do git pull -q --rebase origin main && git push -q origin HEAD:main && return 0; sleep 5; done; return 1; }

if [ "$1" = "auth" ]; then
  printf '%s' "$CERTBOT_VALIDATION" > "$F"
  git add "$F" && git commit -qm "HTTPS: проверка $CERTBOT_DOMAIN" && push
  URL="http://$CERTBOT_DOMAIN/.well-known/acme-challenge/$CERTBOT_TOKEN"
  for i in $(seq 1 90); do      # до 15 минут
    if [ "$(curl -fsS -m 10 "$URL" 2>/dev/null)" = "$CERTBOT_VALIDATION" ]; then
      echo "Сервер отдаёт $URL"; sleep 5; exit 0
    fi
    sleep 10
  done
  echo "Сервер так и не начал отдавать $URL"; exit 1
else
  if [ -f "$F" ]; then git rm -q "$F" && git commit -qm "HTTPS: убрать проверку $CERTBOT_DOMAIN" && push || true; fi
fi

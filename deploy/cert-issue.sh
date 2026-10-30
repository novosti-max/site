#!/usr/bin/env bash
# Запускается в GitHub Actions. Выпускает сертификат Let's Encrypt по CSR, который публикует сервер.
set -euo pipefail
D=novosti-max.com
OUT=certs/fullchain.pem

if [ -s "$OUT" ] && openssl x509 -checkend $((30*86400)) -noout -in "$OUT" >/dev/null; then
  echo "Сертификат действует ещё больше 30 дней — продлевать рано"; exit 0
fi

git config user.name "novosti-max cert"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# ждём, пока сервер опубликует запрос на сертификат (до 20 минут — сервер синхронизируется по расписанию)
for i in $(seq 1 120); do
  curl -fsS -m 10 "http://$D/.well-known/acme-challenge/csr.pem" -o /tmp/csr.pem 2>/dev/null && grep -q "BEGIN CERTIFICATE REQUEST" /tmp/csr.pem && break
  sleep 10
done
grep -q "BEGIN CERTIFICATE REQUEST" /tmp/csr.pem
openssl req -in /tmp/csr.pem -noout -text | grep -E "Subject:|DNS:"

W=/tmp/le; mkdir -p $W
certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --manual --preferred-challenges http \
  --manual-auth-hook "$PWD/deploy/cert-hook.sh auth" \
  --manual-cleanup-hook "$PWD/deploy/cert-hook.sh cleanup" \
  --csr /tmp/csr.pem \
  --cert-path $W/cert.pem --chain-path $W/chain.pem --fullchain-path $W/fullchain.pem \
  --config-dir $W/conf --work-dir $W/work --logs-dir $W/logs

git pull -q --rebase origin main
cp $W/fullchain.pem "$OUT"
find certs/challenges -type f ! -name .keep -delete   # убрать оставшиеся проверочные файлы
git add -A certs
git commit -m "HTTPS: сертификат до $(openssl x509 -in "$OUT" -noout -enddate | cut -d= -f2)" && git push -q origin HEAD:main
echo "Готово: $(openssl x509 -in "$OUT" -noout -subject -enddate)"

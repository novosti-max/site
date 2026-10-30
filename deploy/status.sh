#!/usr/bin/env bash
# Короткий статус бота для проверки снаружи (ничего секретного: ключей в журнале нет).
OUT=/var/www/acme/.well-known/acme-challenge/rb-status.txt
[ -d "$(dirname "$OUT")" ] || exit 0
{
  date '+%F %T'
  echo "commit $(git -C /opt/radar-bot rev-parse --short HEAD 2>/dev/null)"
  echo "bot: $(systemctl is-active radar-bot)  setup: $(systemctl is-active radar-setup)"
  echo "keys: max=$(python3 -c "import json;d=json.load(open('/etc/radar-bot/secrets.json'));print('да' if d.get('max_token') else 'нет', 'vk=%d' % len(d.get('vk',{})), 'пробный' if d.get('dry_run') else 'боевой')" 2>/dev/null)"
  echo ---
  tail -n 60 /var/lib/radar-bot/bot.log 2>/dev/null
  echo --- diag
  timeout 60 python3 /opt/radar-bot/deploy/diag.py 2>&1 | head -20
  echo --- journal
  journalctl -u radar-bot -n 25 --no-pager -o cat 2>/dev/null | grep -v -i token
  echo --- install
  tail -n 10 /var/log/radar-bot-install.log 2>/dev/null
} > "$OUT.tmp" 2>&1 && mv "$OUT.tmp" "$OUT"

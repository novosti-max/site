#!/usr/bin/env bash
# Короткий статус бота для проверки снаружи (ничего секретного: ключей в журнале нет).
OUT=/var/www/acme/.well-known/acme-challenge/rb-status.txt
[ -d "$(dirname "$OUT")" ] || exit 0
LOG=/var/lib/radar-bot/bot.log
{
  date '+%F %T'
  echo "commit $(git -C /opt/radar-bot rev-parse --short HEAD 2>/dev/null)"
  echo "bot: $(systemctl is-active radar-bot)  setup: $(systemctl is-active radar-setup)"
  echo "keys: max=$(python3 -c "import json;d=json.load(open('/etc/radar-bot/secrets.json'));print('да' if d.get('max_token') else 'нет', 'vk=%d' % len(d.get('vk',{})), 'пробный' if d.get('dry_run') else 'боевой')" 2>/dev/null)"
  # итог с последнего запуска бота — для проверки после выкладки
  if [ -f "$LOG" ]; then
    SINCE=$(grep -n " старт: " "$LOG" | tail -n 1 | cut -d: -f1)
    tail -n +"${SINCE:-1}" "$LOG" > /tmp/rb-since.log
    echo "с запуска $(head -c 14 /tmp/rb-since.log): опубликовано $(grep -c ' опубликовано в ' /tmp/rb-since.log)," \
         "пропусков $(grep -c ' пропуск' /tmp/rb-since.log), ошибок $(grep -c -E ' ошибка|Traceback|упал' /tmp/rb-since.log)," \
         "предупреждений $(grep -c -E 'не загрузилось|не прочитана|не прошёл|журнал постов:|переподключаюсь' /tmp/rb-since.log);" \
         "в журнале постов: $(wc -l < /var/lib/radar-bot/corpus.jsonl 2>/dev/null || echo 0)"
    rm -f /tmp/rb-since.log
  fi
  echo ---
  tail -n 60 "$LOG" 2>/dev/null
  echo --- journal
  journalctl -u radar-bot -n 25 --no-pager -o cat 2>/dev/null | grep -v -i token
  echo --- install
  tail -n 10 /var/log/radar-bot-install.log 2>/dev/null
} > "$OUT.tmp" 2>&1 && mv "$OUT.tmp" "$OUT"

# Пробный режим нового правила: файлы в папке shadow/ ветки radar-bot боевой бот НЕ использует.
# Сервер прогоняет через них журнал настоящих постов MAX (tools/replay.py, ВК и сеть отключены)
# и выкладывает отчёт rb-shadow.txt: какие посты вышли бы иначе. Раз в 30 мин или сразу после изменения shadow/.
# Идёт в фоне с низким приоритетом — бот и выкладку не задерживает.
SH=/opt/radar-bot/shadow
REP=$(dirname "$OUT")/rb-shadow.txt
if [ -n "$(find "$SH" -maxdepth 1 \( -name '*.py' -o -name '*.yaml' \) 2>/dev/null)" ] && [ -f /opt/radar-bot/tools/replay.py ]; then
  SIG=$(cat "$SH"/*.py "$SH"/*.yaml 2>/dev/null | sha256sum | cut -c1-12)
  if [ ! -f "$REP" ] || [ "$(cat /var/lib/radar-bot/.shadow.sig 2>/dev/null)" != "$SIG" ] || [ -n "$(find "$REP" -mmin +30)" ]; then
    echo "$SIG" > /var/lib/radar-bot/.shadow.sig
    setsid bash -c "cd /tmp && flock -n /tmp/radar-shadow.lock runuser -u radarbot -- timeout 300 nice -n 15 \
      python3 /opt/radar-bot/tools/replay.py --old /opt/radar-bot --new $SH --corpus /var/lib/radar-bot/corpus.jsonl \
      > $REP.tmp 2>&1; echo \"(shadow $SIG, commit \$(git -C /opt/radar-bot rev-parse --short HEAD))\" >> $REP.tmp; mv $REP.tmp $REP" \
      >/dev/null 2>&1 < /dev/null 8>&- &   # 8>&- : не держать замок автообновления (radar-bot-hook.sh)
  fi
elif [ ! -f "$REP" ] || ! grep -q "не проверяется" "$REP"; then
  echo "$(date '+%F %T') Сейчас новое правило не проверяется: папки shadow/ с файлами нет." > "$REP"
fi
exit 0

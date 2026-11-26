#!/usr/bin/env bash
# Короткий статус бота для проверки снаружи (ничего секретного: ключей в журнале нет).
# Вызывается раз в минуту двумя путями (11.11): автообновлением (radar-bot-hook.sh из main) и своим таймером
# radar-status.timer (deploy/install.sh) — так страница обновляется, даже если автообновление зависло
# (11.11 rb-status.txt стоял с 07:14 больше 4 часов). Весь скрипт ограничен по времени — сам зависнуть не может.
if [ -z "${RB_STATUS_T:-}" ]; then RB_STATUS_T=1 exec timeout -k 5 90 bash "$0" "$@"; fi
OUT=/var/www/acme/.well-known/acme-challenge/rb-status.txt
[ -d "$(dirname "$OUT")" ] || exit 0
exec 7>/tmp/radar-status.lock
flock -n 7 || exit 0                       # уже пишется другим путём
LOG=/var/lib/radar-bot/bot.log
DIR=/opt/radar-bot
NOTE=""
# git fetch без ограничения времени может висеть часами на оборванном соединении и держать замок автообновления —
# тогда ни выкладки, ни статуса. Для папки бота: медленнее 1 КБ/с дольше 60 с — git сам прерывает загрузку.
git -C "$DIR" config http.lowSpeedLimit 1000 2>/dev/null
git -C "$DIR" config http.lowSpeedTime 60 2>/dev/null
# Зависший больше 10 минут git в папке бота (fetch автообновления) — останавливаем, на следующей минуте всё пойдёт заново.
for p in $(pgrep -x git 2>/dev/null; pgrep -f 'git-remote-https' 2>/dev/null); do
  [ "$(readlink "/proc/$p/cwd" 2>/dev/null)" = "$DIR" ] || continue
  age=$(ps -o etimes= -p "$p" 2>/dev/null | tr -d ' ')
  if [ -n "$age" ] && [ "$age" -gt 600 ]; then
    kill "$p" 2>/dev/null && NOTE="$NOTE зависший git в $DIR ($((age / 60)) мин) остановлен;"
  fi
done
# Замок автообновления: вызов из автообновления держит его сам (fd 8), из таймера — смотрим, не занят ли давно.
BUSY=/var/lib/radar-bot/.hook-busy-since
if [ ! -e /proc/$$/fd/8 ]; then
  if flock -n /tmp/radar-bot-hook.lock true 2>/dev/null; then
    rm -f "$BUSY"
  else
    [ -f "$BUSY" ] || date '+%s' > "$BUSY"
    m=$(( ($(date '+%s') - $(cat "$BUSY")) / 60 ))
    [ "$m" -ge 5 ] && NOTE="$NOTE ⚠ автообновление занято $m мин (с $(date -d @"$(cat "$BUSY")" '+%H:%M'));"
  fi
else
  rm -f "$BUSY"
fi
REMOTE=$(timeout 20 git -C "$DIR" ls-remote -q origin radar-bot 2>/dev/null | cut -c1-7)
HEAD=$(git -C "$DIR" rev-parse --short=7 HEAD 2>/dev/null)
{
  date '+%F %T'
  echo "commit $HEAD$([ -n "$REMOTE" ] && [ "$REMOTE" != "$HEAD" ] && echo "  ⚠ в GitHub уже $REMOTE — ещё не выложен")"
  [ -n "$NOTE" ] && echo "автообновление:$NOTE"
  if [ -f "$LOG" ]; then
    LAST=$(stat -c %Y "$LOG"); AGE=$(( ($(date '+%s') - LAST) / 60 ))
    echo "журнал бота: последняя запись $AGE мин назад$([ "$AGE" -gt 45 ] && echo "  ⚠ БОТ МОЛЧИТ")"
  fi
  CHK=$(dirname "$OUT")/rb-check.txt
  [ -f "$CHK" ] && echo "проверка постов (rb-check.txt): $(sed -n 2p "$CHK")"
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
  echo --- posts
  # 04.11: последние посты каждого канала MAX из журнала постов (текст, вложения, куда вышло) — видно, что пришло из MAX,
  # даже если бот пост пропустил молча. Ключей тут нет: только текст публичных каналов.
  python3 - /var/lib/radar-bot/corpus.jsonl <<'PY' 2>/dev/null
import json, sys, time
rows = []
for line in open(sys.argv[1], encoding="utf-8").readlines()[-600:]:
    try:
        rows.append(json.loads(line))
    except Exception:
        pass
last = {}
for r in rows:
    last.setdefault(r.get("src"), []).append(r)
for src in sorted(last, key=str):
    for r in last[src][-3:]:
        t = time.strftime("%d.%m %H:%M", time.localtime((r.get("t") or 0) / 1000 or r.get("rt") or 0))
        atts = ",".join(a.get("type", "?") for a in r.get("atts") or [])
        out = ",".join(f"{o[0]}{'+' + o[2] if o[2] else ''}" for o in r.get("out") or []) or "-"
        text = (r.get("text") or "").replace("\n", " / ")[:90]
        print(f"{t} {src} [{atts}] -> {out}: {text}")
PY
  echo --- journal
  journalctl -u radar-bot -n 25 --no-pager -o cat 2>/dev/null | grep -v -i token
  echo --- install
  tail -n 10 /var/log/radar-bot-install.log 2>/dev/null
} > "$OUT.tmp" 2>&1; mv "$OUT.tmp" "$OUT"   # 11.11: пишем всегда, даже если последняя команда блока не удалась

# Проверка постов на сервере (tools/check.py, 11.11): вместо просмотра стен ВК в браузере. Отчёт rb-check.txt —
# рядом с rb-status.txt. ВК и сеть не трогает. Не дольше 40 с.
if [ -f "$DIR/tools/check.py" ]; then
  timeout 40 nice -n 10 python3 "$DIR/tools/check.py" --out "$(dirname "$OUT")/rb-check.txt" >/dev/null 2>&1 \
    || echo "$(date '+%F %T') проверка постов: tools/check.py не отработал" > "$(dirname "$OUT")/rb-check.txt"
fi

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
      >/dev/null 2>&1 < /dev/null 7>&- 8>&- 9>&- &   # не держать замки статуса (7), автообновления бота (8) и сайта (9)
  fi
elif [ ! -f "$REP" ] || ! grep -q "не проверяется" "$REP"; then
  echo "$(date '+%F %T') Сейчас новое правило не проверяется: папки shadow/ с файлами нет." > "$REP"
fi
exit 0

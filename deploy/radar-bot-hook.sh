#!/usr/bin/env bash
# Бот «Радар» MAX -> ВКонтакте — отдельная программа рядом с сайтом (папка /opt/radar-bot, ветка radar-bot).
# Сайт не трогает. Вызывается из sync.sh раз в минуту: ставит бота при первом запуске
# и обновляет, когда в ветке radar-bot появляются правки.
set -uo pipefail
DIR=/opt/radar-bot
REPO=https://github.com/novosti-max/site.git
exec 8>/tmp/radar-bot-hook.lock
flock -n 8 || exit 0

if [ ! -d "$DIR/.git" ]; then
  git clone -q -b radar-bot --single-branch "$REPO" "$DIR" && bash "$DIR/deploy/install.sh"
elif git -C "$DIR" fetch -q origin radar-bot && [ "$(git -C "$DIR" rev-parse HEAD)" != "$(git -C "$DIR" rev-parse origin/radar-bot)" ]; then
  git -C "$DIR" reset -q --hard origin/radar-bot && bash "$DIR/deploy/install.sh"
fi
[ -f "$DIR/deploy/status.sh" ] && bash "$DIR/deploy/status.sh"
exit 0

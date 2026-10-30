#!/usr/bin/env bash
# Каждые 10 минут: если в GitHub появились правки — подтянуть и пересобрать сайт.
cd /opt/novosti-max || exit 0
git fetch -q origin main || exit 0
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  git reset -q --hard origin/main
  python3 build.py && echo "$(date '+%F %T') подтянуты правки $(git rev-parse --short HEAD)"
fi

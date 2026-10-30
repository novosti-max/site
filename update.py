#!/usr/bin/env python3
"""Ежедневное обновление цифр из MaxFrame и пересборка сайта.

Берёт публичную страницу сети https://maxframe.ru/group-profile/132/ (вход не нужен),
вытаскивает по каждому каналу: название, подписчиков, охват за 24 ч, аватарку;
и по сети: цену размещения во всей сетке. Пишет data/stats.json и запускает build.py.

Если страница не открылась или каналов распозналось подозрительно мало,
старые цифры не трогаем — сайт продолжает показывать предыдущий день.

Запуск: python3 update.py            (обычный, по cron раз в сутки)
        python3 update.py --no-ava   (без скачивания аватарок)
"""
import csv, json, re, sys, time, datetime, urllib.request, subprocess
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
AVA = DATA / "ava"
SITE = json.loads((DATA / "site.json").read_text("utf-8"))
URL = SITE["maxframe_group"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 novosti-max-site"
MSK = datetime.timezone(datetime.timedelta(hours=3))


def log(*a):
    print(datetime.datetime.now(MSK).strftime("%Y-%m-%d %H:%M"), *a, flush=True)


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:
            log(f"не открылось {url}: {e}")
            time.sleep(10 * (i + 1))
    return None


class GroupTable(HTMLParser):
    """Строки таблицы «Каналы сетки»: ссылка /channel-profile/<id>/, затем ячейки."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell, self.in_td, self.in_tbody = [], None, [], False, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tbody":
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self.row = {"cells": [], "id": None, "title": None, "img": None}
        elif self.row is not None:
            if tag == "td":
                self.in_td, self.cell = True, []
            elif tag == "a" and not self.row["id"]:
                m = re.search(r"/channel-profile/(\d+)/", a.get("href", ""))
                if m:
                    self.row["id"], self.row["title"] = m.group(1), a.get("title")
            elif tag == "img" and not self.row["img"] and a.get("src", "").startswith("http"):
                self.row["img"] = a["src"]

    def handle_endtag(self, tag):
        if tag == "td" and self.row is not None:
            self.row["cells"].append(" ".join("".join(self.cell).split()))
            self.in_td = False
        elif tag == "tr" and self.row is not None:
            if self.row["id"]:
                self.rows.append(self.row)
            self.row = None
        elif tag == "tbody":
            self.in_tbody = False

    def handle_data(self, d):
        if self.in_td:
            self.cell.append(d)


def to_int(s):
    s = re.sub(r"[^\d-]", "", s or "")
    return int(s) if s not in ("", "-") else None


def parse(html):
    p = GroupTable()
    p.feed(html)
    channels = {}
    for r in p.rows:
        c = r["cells"]
        # колонки: название | подписчики | +24ч | +7д | +30д | охват 24ч | категория | тип | действия
        if len(c) < 6 or r["id"] in channels:
            continue
        subs = to_int(c[1])
        if subs is None:
            continue
        channels[r["id"]] = {"name": (r["title"] or "").strip() or None, "subs": subs,
                             "views24": to_int(c[5]), "img": r["img"]}
    price = None
    m = re.search(r"Цена рекламы.{0,400}?([\d\s  ]{5,})\s*₽", html, re.S)
    if m:
        price = to_int(m.group(1))
    return channels, price


def main():
    raw = get(URL)
    if not raw:
        log("MaxFrame недоступен, оставляю вчерашние данные")
        return 1
    html = raw.decode("utf-8", "replace")
    channels, price = parse(html)

    with open(DATA / "channels.csv", encoding="utf-8-sig") as f:
        known = {row["MaxFrame"].rstrip("/").split("/")[-1]: row["Канал"] for row in csv.DictReader(f, delimiter=";")}
    if len(channels) < max(10, len(known) * 0.6):
        log(f"распознано всего {len(channels)} каналов из {len(known)} — похоже, MaxFrame поменял вёрстку. Данные не обновляю")
        return 2

    new = [f"{v['name']} ({k})" for k, v in channels.items() if k not in known]
    gone = [f"{v} ({k})" for k, v in known.items() if k not in channels]
    if new:
        log("в сетке MaxFrame есть каналы, которых нет в data/channels.csv:", "; ".join(new))
    if gone:
        log("каналы из data/channels.csv не найдены в сетке MaxFrame:", "; ".join(gone))

    if "--no-ava" not in sys.argv:
        AVA.mkdir(exist_ok=True)
        for cid, c in channels.items():
            if cid in known and c["img"]:
                img = get(c["img"], tries=1)
                if img and len(img) > 500:
                    (AVA / f"{cid}.jpg").write_bytes(img)

    stats = {
        "updated": datetime.datetime.now(MSK).replace(microsecond=0).isoformat(),
        "source": URL,
        "network": {"count": len(channels), "subs": sum(c["subs"] for c in channels.values()),
                    "views24": sum(c["views24"] or 0 for c in channels.values()), "price": price},
        "channels": {k: {"name": v["name"], "subs": v["subs"], "views24": v["views24"]} for k, v in channels.items()},
        "new_in_maxframe": new,
        "missing_in_maxframe": gone,
    }
    tmp = DATA / "stats.json.tmp"
    tmp.write_text(json.dumps(stats, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(DATA / "stats.json")
    log(f"обновлено: {len(channels)} каналов, {stats['network']['subs']} подписок, "
        f"{stats['network']['views24']} просмотров за 24 ч, цена сети {price}")

    return subprocess.call([sys.executable, str(ROOT / "build.py")])


if __name__ == "__main__":
    sys.exit(main())

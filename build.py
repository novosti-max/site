#!/usr/bin/env python3
"""Сборка сайта-медиакита «Новости в MAX».

Источники:
  data/channels.csv  — список каналов сети (тип, регион, название, подписчики, ссылки)
  data/stats.json    — свежие цифры из MaxFrame (пишет update.py), перекрывают CSV
  data/regions.json  — регион → страница города (slug, название, «в городе»)
  data/site.json     — домен, контакт, цена пакета, коды Вебмастера/Метрики

Результат: папка public/ — главная, страницы городов /reklama/<город>/, sitemap.xml, robots.txt.
"""
import csv, json, re, shutil, datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent
DATA, SRC, OUT = ROOT / "data", ROOT / "src", ROOT / "public"
MSK = datetime.timezone(datetime.timedelta(hours=3))

TYPES = {
    "Радар": ("radar", "Радар"),
    "Город сейчас": ("city", "Городские новости"),
    "Медиа / другое": ("media", "Медиа"),
}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def num(n):
    """1702345 → «1 702 345» (неразрывные пробелы)."""
    return f"{n:,}".replace(",", " ") if n is not None else "—"


def short(n):
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " млн"
    if n >= 10_000:
        return f"{n / 1000:.0f} тыс."
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " тыс."
    return str(n)


def ru_date(dt):
    return f"{dt.day} {MONTHS[dt.month - 1]} {dt.year}"


def plural(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def initials(name):
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", name)
    if not words:
        return "•"
    if words[0].lower() == "радар" and len(words) > 1:
        return "Р" + words[1][0].upper()
    return words[0][0].upper() + (words[1][0].upper() if len(words) > 1 and len(words[1]) > 2 else "")


def load():
    site = json.loads((DATA / "site.json").read_text("utf-8"))
    # на сервере с выданным сертификатом все ссылки сайта — на https
    if Path("/etc/ssl/novosti-max/fullchain.pem").exists():
        site["domain"] = site["domain"].replace("http://", "https://")
    regions = {k: v for k, v in json.loads((DATA / "regions.json").read_text("utf-8")).items() if not k.startswith("_")}
    stats_path = DATA / "stats.json"
    stats = json.loads(stats_path.read_text("utf-8")) if stats_path.exists() else {}
    fresh = stats.get("channels", {})

    channels = []
    with open(DATA / "channels.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            mf = row["MaxFrame"].strip()
            cid = re.search(r"(\d+)/?$", mf).group(1)
            tkey, tlabel = TYPES.get(row["Тип"].strip(), ("media", "Медиа"))
            reg = regions.get(row["Регион"].strip())
            if reg is None:
                raise SystemExit(f"Регион «{row['Регион']}» не описан в data/regions.json")
            s = fresh.get(cid, {})
            channels.append({
                "id": cid,
                "type": tkey,
                "type_label": tlabel,
                "name": (s.get("name") or row["Канал"]).strip(),
                "region": reg["name"],
                "region_slug": reg["slug"],
                "federal": bool(reg.get("federal")),
                "subs": int(s.get("subs") or row["Подписчики"] or 0),
                "views": s.get("views24"),
                "max_url": row["Ссылка"].strip(),
                "mf_url": mf,
                "initials": initials(row["Канал"]),
                "ava": (DATA / "ava" / f"{cid}.jpg").exists(),
            })
    channels.sort(key=lambda c: -c["subs"])

    updated = stats.get("updated")
    updated_dt = datetime.datetime.fromisoformat(updated).astimezone(MSK) if updated else \
        datetime.datetime.fromtimestamp((DATA / "channels.csv").stat().st_mtime, MSK)
    price = (stats.get("network") or {}).get("price")
    if price:
        site["package_price"] = price  # цена всей сетки — как опубликована в MaxFrame
    return site, regions, channels, updated_dt


def build():
    site, regions, channels, updated = load()

    # регионы-страницы (без дублей: «Россия» и «Федеральный» → одна страница)
    pages = {}
    for c in channels:
        p = pages.setdefault(c["region_slug"], {
            "slug": c["region_slug"], "channels": [], "federal": c["federal"],
            **next(v for v in regions.values() if v["slug"] == c["region_slug"]),
        })
        p["channels"].append(c)
    for p in pages.values():
        p["subs"] = sum(c["subs"] for c in p["channels"])
        vs = [c["views"] for c in p["channels"] if c["views"] is not None]
        p["views"] = sum(vs) if vs else None
        p["count"] = len(p["channels"])
        p["has_city"] = any(c["type"] == "city" for c in p["channels"])
    region_pages = sorted((p for p in pages.values() if not p["federal"]), key=lambda p: p["name"])
    federal_page = pages.get("rossiya")
    top_regions = sorted(region_pages, key=lambda p: -p["subs"])[:9]

    views_all = [c["views"] for c in channels if c["views"] is not None]
    totals = {
        "count": len(channels),
        "subs": sum(c["subs"] for c in channels),
        "views": sum(views_all) if views_all else None,
        "regions": len(region_pages),
        "radar": sum(c["type"] == "radar" for c in channels),
        "city": sum(c["type"] == "city" for c in channels),
        "media": sum(c["type"] == "media" for c in channels),
    }

    env = Environment(loader=FileSystemLoader(SRC / "templates"), autoescape=select_autoescape(["html"]))
    env.filters.update(num=num, short=short)
    env.globals.update(plural=plural)
    ctx = dict(site=site, totals=totals, updated=updated, updated_human=ru_date(updated),
               updated_iso=updated.isoformat(), region_pages=region_pages, federal_page=federal_page,
               top_regions=top_regions, year=updated.year, has_views=totals["views"] is not None)

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    for f in (SRC / "static").iterdir():
        shutil.copy(f, OUT / "assets" / f.name)
    # файлы, которые должны лежать в корне сайта (подтверждения Яндекса/Google и т.п.)
    if (SRC / "root").exists():
        for f in (SRC / "root").iterdir():
            shutil.copy(f, OUT / f.name)
    if (DATA / "ava").exists():
        shutil.copytree(DATA / "ava", OUT / "assets" / "ava")

    catalog = [{k: c[k] for k in ("id", "type", "name", "region", "region_slug", "subs", "views", "max_url")}
               for c in channels]
    (OUT / "assets" / "catalog.json").write_text(json.dumps(
        {"updated": updated.isoformat(), "channels": catalog}, ensure_ascii=False), "utf-8")

    def render(tpl, out, **extra):
        target = OUT / out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(env.get_template(tpl).render(**ctx, **extra), "utf-8")

    render("index.html", "index.html", channels=channels, path="/")
    render("regions.html", "reklama/index.html", path="/reklama/")
    for p in pages.values():
        others = [o for o in top_regions if o["slug"] != p["slug"]][:6]
        render("region.html", f"reklama/{p['slug']}/index.html", page=p, others=others,
               path=f"/reklama/{p['slug']}/")
    render("404.html", "404.html", path="/404")

    urls = ["/", "/reklama/"] + [f"/reklama/{s}/" for s in sorted(pages)]
    lastmod = updated.date().isoformat()
    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        pr = "1.0" if u == "/" else "0.8" if u.count("/") > 2 else "0.6"
        sitemap.append(f"  <url><loc>{site['domain']}{u}</loc><lastmod>{lastmod}</lastmod>"
                       f"<changefreq>daily</changefreq><priority>{pr}</priority></url>")
    sitemap.append("</urlset>")
    (OUT / "sitemap.xml").write_text("\n".join(sitemap) + "\n", "utf-8")
    (OUT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nHost: {site['domain']}\nSitemap: {site['domain']}/sitemap.xml\n", "utf-8")

    print(f"Готово: {totals['count']} каналов, {len(pages)} страниц регионов, "
          f"данные на {ru_date(updated)} → {OUT}")


if __name__ == "__main__":
    build()

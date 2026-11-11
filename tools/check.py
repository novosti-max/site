"""Проверка постов бота на сервере — без браузера и без входа во ВК (11.11, решение Никиты: ВК заморозил
страницу админа, «думает что робот»; проверять по-другому, на сервере).

Читает журнал постов (corpus.jsonl: что пришло из MAX и что вышло во ВК) и журнал бота (bot.log) и пишет
короткий отчёт rb-check.txt рядом с rb-status.txt:
  - жив ли бот (сколько минут назад была последняя запись журнала);
  - за последний час: сколько опубликовано, ошибки публикации по группам с кодом ВК;
  - ⚠ подозрительные посты: метки рубрик (FIX-26), карточка у новости, вышедшая реклама,
    оповещение своего канала, которое не вышло в свою группу;
  - список всех публикаций за последние часы: время, группа, номер поста (vk.ru/wall-<gid>_<номер>), карточка, текст.
ВК, MAX и сеть не трогает, ничего не публикует. Ключей в отчёте нет: только тексты публичных каналов.

  python3 tools/check.py [--corpus corpus.jsonl] [--log bot.log] [--config config.yaml] [--hours 1] [--list-hours 3]
                         [--now "2026-11-11 10:00"] [--out rb-check.txt]
"""
import argparse, json, os, re, sys, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from textproc import clean_text, classify, card_kind, is_ad   # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
STATE = "/var/lib/radar-bot"
LABELS = re.compile(r"^\W{0,3}(?:🌍\s*МИР|⚔️?\s*СВО|🏛\s*ПОЛИТИКА)\s*\|", re.IGNORECASE)
LOG_LINE = re.compile(r"^(\d\d)\.(\d\d) (\d\d):(\d\d):(\d\d) (.*)$")
ERR = re.compile(r"ошибка|Traceback|упал", re.IGNORECASE)
PUB_ERR = re.compile(r"ошибка публикации в (\S+): (.*)$")


def norm_link(s):
    s = (s or "").strip().lower()
    for p in ("https://", "http://", "www."):
        if s.startswith(p):
            s = s[len(p):]
    return s.rstrip("/")


def load_config(path):
    import yaml
    return yaml.safe_load(open(path, encoding="utf-8"))


def row_time(r):
    t = r.get("t")
    return t / 1000 if t else (r.get("rt") or 0)


def read_corpus(path):
    rows = []
    try:
        for line in open(path, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    except OSError:
        pass
    return rows


def read_log(path, now):
    """Строки bot.log (и bot.log.1 — если час попал на ротацию) -> [(время, текст)]. В журнале нет года: берём год now,
    а дата «из будущего» — прошлый год (на стыке 31.12/01.01)."""
    out = []
    for p in (path + ".1", path):
        try:
            lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for line in lines:
            m = LOG_LINE.match(line)
            if not m:
                if out and line.strip():          # продолжение Traceback — к предыдущей строке
                    out.append((out[-1][0], line))
                continue
            d, mo, h, mi, s, msg = m.groups()
            try:
                t = datetime(now.year, int(mo), int(d), int(h), int(mi), int(s), tzinfo=TZ)
            except ValueError:
                continue
            if t > now + timedelta(days=1):
                t = t.replace(year=now.year - 1)
            out.append((t, msg))
    return out


def has_url_buttons(r):
    return any(a.get("type") == "inline_keyboard" and any(b.get("url") for row in ((a.get("payload") or {}).get("buttons") or [])
                                                          for b in row)
               for a in r.get("atts") or [])


def check(rows, log_lines, cfg, now, hours=1.0, list_hours=3.0):
    groups = [g for g in cfg.get("groups") or [] if g.get("enabled")]
    by_slug = {g["slug"]: g for g in groups}
    since, list_since = now.timestamp() - hours * 3600, now.timestamp() - list_hours * 3600
    lines = [f"{now:%Y-%m-%d %H:%M:%S} проверка постов бота (tools/check.py), окно {hours:g} ч"]

    # --- жив ли бот
    if log_lines:
        last_t = log_lines[-1][0]
        age = (now - last_t).total_seconds() / 60
        lines.append(f"журнал бота: последняя запись {last_t:%d.%m %H:%M}, {age:.0f} мин назад"
                     + ("  ⚠ БОТ МОЛЧИТ (раз в 30 мин он пишет «приём MAX»)" if age > 45 else ""))
        starts = [m for t, m in log_lines if m.startswith("старт: ")]
        if starts:
            lines.append(f"последний старт: {starts[-1]}")
    else:
        lines.append("⚠ журнал бота не прочитан")

    # --- ошибки за окно
    win = [(t, m) for t, m in log_lines if t.timestamp() >= since]
    published = [m for t, m in win if m.startswith("опубликовано в ")]
    errors = [(t, m) for t, m in win if ERR.search(m)]
    pub_err = {}
    for t, m in errors:
        pm = PUB_ERR.search(m)
        if pm:
            pub_err.setdefault(pm.group(1), []).append(f"{t:%H:%M} {pm.group(2)[:120]}")
    lines.append(f"за {hours:g} ч: опубликовано {len(published)}, ошибок {len(errors)}"
                 f" (публикации: {sum(len(v) for v in pub_err.values())})")
    for vk, items in sorted(pub_err.items()):
        lines.append(f"  ⚠ ошибки публикации {vk} ×{len(items)}: " + "; ".join(items[-4:]))
    for t, m in errors:
        if not PUB_ERR.search(m):
            lines.append(f"  ⚠ {t:%H:%M:%S} {m[:160]}")

    # --- подозрительные посты за окно
    warn, listing = [], []
    for r in rows:
        rt = row_time(r)
        if rt < list_since or r.get("dry"):
            continue
        text = r.get("text") or ""
        when = datetime.fromtimestamp(rt, TZ).strftime("%H:%M")
        out = r.get("out") or []
        for o in out:
            slug, pid, mark, otext = (list(o) + [None, None, "", ""])[:4]
            g = by_slug.get(slug) or {}
            ref = f"wall-{g.get('gid')}_{pid}" if g.get("gid") and pid else f"{slug} №{pid}"
            one = (otext or "").replace("\n", " / ")[:100]
            listing.append((rt, f"{when} {g.get('vk', slug):<24} {ref:<22} {mark or '-':<11} {one}"))
            if rt < since:
                continue
            if LABELS.search(otext or ""):
                warn.append(f"{when} {ref}: метка рубрики (FIX-26 — меток быть не должно): {one[:70]}")
            if mark == "с карточкой" and not any((r.get("ocr") or {}).values()):
                full = text + ("\n" + ((r.get("link") or {}).get("text") or "") if (r.get("link") or {}).get("text") else "")
                if card_kind(classify(full), full) is None:
                    warn.append(f"{when} {ref}: карточка у поста, который по правилам идёт без карточки: {one[:70]}")
        if rt < since:
            continue
        if out and is_ad(text, has_url_buttons(r)):
            warn.append(f"{when} {r.get('src')}: вышла реклама в {', '.join(str(o[0]) for o in out)}: "
                        f"{text.replace(chr(10), ' / ')[:70]}")
        if (r.get("link") or {}).get("type") == "forward":
            continue
        kind = classify(text)
        if card_kind(kind, text) is None or kind not in ("danger", "clear", "attention"):
            continue
        done = {o[0] for o in out}
        for g in groups:
            if norm_link(g.get("source")) == r.get("src") and g["slug"] not in done:
                why = pub_err.get(g["vk"])
                warn.append(f"{when} оповещение канала {r.get('src')} не вышло в {g['vk']}"
                            f"{' (ошибка публикации)' if why else ''}: {clean_text(text).replace(chr(10), ' / ')[:70]}")

    lines.append(f"⚠ подозрительных постов: {len(warn)}" if warn else "подозрительных постов нет")
    lines += ["  ⚠ " + w for w in warn]
    lines.append("")
    lines.append(f"--- публикации за {list_hours:g} ч (время, группа, пост vk.ru/…, карточка, текст)")
    lines += [s for _, s in sorted(listing)] or ["(нет)"]
    lines.append("")
    lines.append("--- группы (vk.ru/wall-<gid>_<номер>)")
    lines.append(", ".join(f"{g['slug']}={g['vk']}:{g.get('gid')}" for g in groups))
    verdict = "ОК" if not warn and not errors and log_lines and (now - log_lines[-1][0]).total_seconds() <= 45 * 60 else "ЕСТЬ ЗАМЕЧАНИЯ"
    lines.insert(1, f"ИТОГ: {verdict}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.path.join(STATE, "corpus.jsonl"))
    ap.add_argument("--log", default=os.path.join(STATE, "bot.log"))
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--list-hours", type=float, default=3.0)
    ap.add_argument("--now", help="для тестов: «2026-11-11 10:00» (МСК)")
    ap.add_argument("--out")
    a = ap.parse_args()
    now = datetime.strptime(a.now, "%Y-%m-%d %H:%M").replace(tzinfo=TZ) if a.now else datetime.now(TZ)
    rep = check(read_corpus(a.corpus), read_log(a.log, now), load_config(a.config), now, a.hours, a.list_hours)
    if a.out:
        tmp = a.out + ".tmp"
        open(tmp, "w", encoding="utf-8").write(rep)
        os.replace(tmp, a.out)
    else:
        sys.stdout.write(rep)


if __name__ == "__main__":
    main()

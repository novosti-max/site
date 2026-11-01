"""Проверка нового правила на НАСТОЯЩИХ постах MAX, до того как оно начнёт публиковать (защита от «косяков»).

Бот пишет каждый пост MAX своих каналов в журнал постов (state-папка/corpus.jsonl, последние 1500).
Этот скрипт прогоняет журнал через две версии кода — текущую и новую — и показывает, какие посты во ВК
вышли бы иначе. ВК, MAX и интернет в прогоне отключены: ничего не публикуется, боевой бот не трогается.

  python3 tools/replay.py --old <папка текущего кода> --new <папка с новыми файлами> [--corpus corpus.jsonl]
                          [--hours 24] [--out отчёт.txt]

--new — папка, где лежат только изменённые файлы (например одна textproc.py): они кладутся поверх --old.
Пробный режим на сервере: новые файлы кладут в папку shadow/ ветки radar-bot, сервер раз в 30 минут
прогоняет через них журнал и выкладывает отчёт rb-shadow.txt рядом с rb-status.txt (deploy/status.sh).

Ограничения: состояние (серии карточек, лимиты «тихо» и рубрик) в прогоне начинается с нуля — первые часы
журнала могут отличаться от реальности, одинаково для обеих версий. Картинки-плашки MAX заново не
распознаются: берётся то, что OCR показал вживую (если вживую OCR не запускался — «не распознано»).
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
CODE_FILES = ("radar_bot.py", "textproc.py", "vkid.py", "config.yaml")


# ---------------------------------------------------------------- прогон одной версии (отдельный процесс)
def worker(code, corpus, out):
    tmp = tempfile.mkdtemp(prefix="replay-")
    os.environ.update(RADAR_STATE=os.path.join(tmp, "state.json"), RADAR_SECRETS=os.path.join(tmp, "secrets.json"),
                      RADAR_VKID=os.path.join(tmp, "vkid.json"))
    sys.path.insert(0, code)
    os.chdir(code)
    import logging
    import requests

    def no_net(*a, **k):
        raise requests.ConnectionError("сеть отключена в прогоне")
    requests.get = requests.post = no_net
    requests.Session.request = no_net
    clock = [0.0]
    time.time = lambda: clock[0]          # время поста из журнала: серии карточек, лимиты, паузы — как вживую

    import radar_bot
    rows = [json.loads(l) for l in open(corpus, encoding="utf-8")]
    ocr = {}
    for r in rows:
        ocr.update({u: v for u, v in (r.get("ocr") or {}).items() if v})
    radar_bot.alert_from_image = lambda data, fb=False: tuple(ocr[data.decode()]) if ocr.get(data.decode()) else None

    cfg = radar_bot.load_config()
    secrets = {"max_token": "x", "vk": {g["slug"]: "k" for g in cfg["groups"] if g.get("enabled")}}
    b = radar_bot.Bot(cfg, secrets, dry=False)
    b.state.save = lambda: None
    if hasattr(b, "record_corpus"):
        b.record_corpus = lambda m: None   # прогон журнал не пишет
    b.max.chat = lambda cid: None
    b.fetch_image = lambda u: (u or "").encode()
    b.media_attachments = lambda g, atts: ["фото"] if any(a.get("type") == "image" for a in atts) else []
    card_of = {}
    for g in cfg["groups"]:
        for k, v in (g.get("cards") or {}).items():
            card_of[(g["slug"], v)] = k
    posts, logs = [], []

    def post(g, text, att=None):
        kind = card_of.get((g["slug"], att), "фото" if att else "")
        posts.append([g["slug"], kind, text])
    b.post = post

    class H(logging.Handler):
        def emit(self, rec):
            logs.append(rec.getMessage()[:200])
    lg = logging.getLogger("radar")
    lg.handlers[:] = [H()]
    lg.setLevel(logging.INFO)
    lg.propagate = False

    res = {}
    for r in rows:
        clock[0] = float(r.get("rt") or 0)
        b.chat_to_source[r["chat"]] = r["src"]
        msg = {"recipient": {"chat_id": r["chat"], "chat_type": "channel"}, "timestamp": r.get("t"),
               "body": {"mid": r.get("mid"), "text": r.get("text") or "", "attachments": r.get("atts") or []}}
        if r.get("link"):
            msg["link"] = {"type": r["link"].get("type"), "message": {"text": r["link"].get("text") or ""}}
        posts.clear()
        logs.clear()
        err = ""
        try:
            b.handle(msg)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:300]
        res[r["key"]] = {"posts": [list(p) for p in posts], "log": list(logs), "err": err}
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)


# ---------------------------------------------------------------- сравнение
def load_corpus(path, hours=None):
    rows, seen = [], set()
    for i, l in enumerate(open(path, encoding="utf-8")):
        try:
            r = json.loads(l)
        except Exception:
            continue
        if not r.get("chat") or not r.get("src"):
            continue
        key = r.get("mid") or f"line{i}"
        if key in seen:        # MAX иногда присылает пост повторно — бот его пропускает, прогон тоже
            continue
        seen.add(key)
        r["key"] = key
        rows.append(r)
    rows.sort(key=lambda r: float(r.get("rt") or 0))
    if hours and rows:
        edge = float(rows[-1].get("rt") or 0) - hours * 3600
        rows = [r for r in rows if float(r.get("rt") or 0) >= edge]
    return rows


def run_version(code, corpus_file, timeout=600):
    out = tempfile.mktemp(suffix=".json")
    p = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "--worker", code, corpus_file, out],
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 or not os.path.exists(out):
        raise RuntimeError("прогон не прошёл:\n" + (p.stderr or p.stdout)[-2000:])
    res = json.load(open(out, encoding="utf-8"))
    os.remove(out)
    return res


def build_new(old, new):
    d = tempfile.mkdtemp(prefix="replay-new-")
    for f in CODE_FILES:
        if os.path.exists(os.path.join(old, f)):
            shutil.copy(os.path.join(old, f), d)
    changed = []
    for f in sorted(os.listdir(new)):
        if f.endswith((".py", ".yaml")) and os.path.isfile(os.path.join(new, f)):
            shutil.copy(os.path.join(new, f), d)
            changed.append(f)
    return d, changed


def when(r):
    try:
        return datetime.fromtimestamp(float(r.get("rt")), TZ).strftime("%d.%m %H:%M")
    except Exception:
        return "?"


def short(t, n=90):
    t = " / ".join(x.strip() for x in (t or "").splitlines() if x.strip())
    return t if len(t) <= n else t[:n - 1] + "…"


CARD_RU = {"danger": "«опасность»", "clear": "«отбой»", "missile": "«ракеты»", "missile_clear": "«отбой ракет»",
           "mvsh": "«МВШ»", "mvsh_clear": "«отбой МВШ»", "attention": "«внимание»", "share": "«перешлите»"}


def describe(p):
    slug, kind, text = p
    how = "с фото" if kind == "фото" else f"с карточкой {CARD_RU.get(kind, kind)}" if kind else "без карточки"
    return f"{how}: «{short(text, 110)}»"


def why_skip(logs, slug, cfg_vk):
    vk = cfg_vk.get(slug, slug)
    for l in logs:
        if "пропуск" in l and (vk in l or slug in l):
            return l.split(":", 1)[0]
    for l in logs:
        if l.startswith("пропуск"):
            return l.split(":", 1)[0]
    return "не вышло"


def compare(rows, old, new, cfg_vk, limit=60):
    changes, stats = [], {"plus": 0, "minus": 0, "text": 0, "card": 0, "err": 0}
    by_group = {}
    for r in rows:
        k = r["key"]
        a, b = old.get(k, {"posts": [], "log": []}), new.get(k, {"posts": [], "log": []})
        if b.get("err") and not a.get("err"):
            stats["err"] += 1
        pa = {p[0]: p for p in a["posts"]}
        pb = {p[0]: p for p in b["posts"]}
        lines = []
        for slug in sorted(set(pa) | set(pb)):
            x, y = pa.get(slug), pb.get(slug)
            if x == y:
                continue
            g = by_group.setdefault(slug, [0, 0, 0])
            if x and not y:
                stats["minus"] += 1; g[1] += 1
                lines.append(f"   {slug}: было {describe(x)}\n      стало: НЕ ВЫХОДИТ ({why_skip(b['log'], slug, cfg_vk)})")
            elif y and not x:
                stats["plus"] += 1; g[0] += 1
                lines.append(f"   {slug}: было: не выходил ({why_skip(a['log'], slug, cfg_vk)})\n      стало {describe(y)}")
            else:
                g[2] += 1
                if x[1] != y[1]:
                    stats["card"] += 1
                if x[2] != y[2]:
                    stats["text"] += 1
                lines.append(f"   {slug}: было {describe(x)}\n      стало {describe(y)}")
        if b.get("err") != a.get("err") and b.get("err"):
            lines.append(f"   ОШИБКА в новом коде: {b['err']}")
        if lines:
            changes.append(f"{len(changes) + 1}) {when(r)} · {r['src']}\n   MAX: «{short(r.get('text'))}»\n" + "\n".join(lines))
    return changes, stats, by_group


def fidelity(rows, old):
    """Совпадает ли прогон ТЕКУЩЕГО кода с тем, что бот реально опубликовал (проверка самого прогона)."""
    total = same = 0
    bad = []
    for r in rows:
        if r.get("dry") or "out" not in r:
            continue
        total += 1
        real = sorted((o[0], "карт" if "карточк" in (o[2] or "") else "фото" if "фото" in (o[2] or "") else "") for o in r["out"])
        rep = sorted((p[0], "" if not p[1] else "фото" if p[1] == "фото" else "карт") for p in old.get(r["key"], {}).get("posts", []))
        if real == rep:
            same += 1
        elif len(bad) < 8:
            bad.append(f"   {when(r)} «{short(r.get('text'), 60)}»: вживую {real or 'ничего'}, в прогоне {rep or 'ничего'}")
    return total, same, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", nargs=3)
    ap.add_argument("--old")
    ap.add_argument("--new")
    ap.add_argument("--corpus", default="/var/lib/radar-bot/corpus.jsonl")
    ap.add_argument("--hours", type=float)
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.worker:
        return worker(*a.worker)
    old = os.path.abspath(a.old)
    rows = load_corpus(a.corpus, a.hours)
    head = [f"Проверка нового правила на настоящих постах MAX — {datetime.now(TZ).strftime('%d.%m %H:%M')}"]
    if not rows:
        head.append("Журнал постов пуст: бот ещё не записал ни одного поста. Проверять не на чем.")
        return emit("\n".join(head), a.out)
    newdir, changed = build_new(old, os.path.abspath(a.new))
    cf = tempfile.mktemp(suffix=".jsonl")
    with open(cf, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    try:
        ro = run_version(old, cf)
        rn = run_version(newdir, cf)
    finally:
        os.remove(cf)
        shutil.rmtree(newdir, ignore_errors=True)
    import yaml
    cfg = yaml.safe_load(open(os.path.join(old, "config.yaml"), encoding="utf-8"))
    cfg_vk = {g["slug"]: g["vk"] for g in cfg["groups"]}
    changes, st, by_group = compare(rows, ro, rn, cfg_vk)
    total, same, bad = fidelity(rows, ro)
    n_posts_old = sum(len(v["posts"]) for v in ro.values())
    head += [f"Постов MAX в журнале: {len(rows)} ({when(rows[0])} — {when(rows[-1])}); публикаций во ВК по текущему коду: {n_posts_old}",
             f"Новые файлы: {', '.join(changed) or '—'}",
             f"Прогон текущего кода совпал с тем, что реально вышло: {same} из {total}" if total else
             "Сверки с реальностью нет (в журнале ещё нет записей о вышедших постах)"]
    if not changes:
        head.append("\nИТОГ: новое правило не меняет ни одного поста. ✅")
    else:
        head.append(f"\nИТОГ: новое правило меняет посты MAX: {len(changes)}. Публикаций: +{st['plus']} новых, "
                    f"−{st['minus']} пропадает, у {st['text']} другой текст, у {st['card']} другая карточка."
                    + (f" ОШИБКИ нового кода: {st['err']}." if st["err"] else ""))
        head.append("По группам (+ появится / − пропадёт / ~ изменится): " +
                    ", ".join(f"{s} +{v[0]} −{v[1]} ~{v[2]}" for s, v in sorted(by_group.items())))
        head.append("\nЧТО ИЗМЕНИТСЯ:\n" + "\n\n".join(changes[:60]) +
                    (f"\n\n…и ещё {len(changes) - 60}" if len(changes) > 60 else ""))
    if bad:
        head.append("\nГде прогон не совпал с реальностью (первые часы журнала — это нормально, состояние начинается с нуля):\n" + "\n".join(bad))
    emit("\n".join(head), a.out)


def emit(text, out):
    if out:
        open(out, "w", encoding="utf-8").write(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()

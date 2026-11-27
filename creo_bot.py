"""Крео-бот (27.11): отвечает в личке бота «Радар» людям из списка — готовое крео для закупа и переливов.

Работает отдельным сервисом radar-creo рядом с ботом тревог и НЕ читает события MAX сам (их читает radar_bot.py,
а creo_hook.py пишет личку в creo_inbox.jsonl и посты каналов в creo_feed.jsonl). Этот сервис только отправляет
сообщения тем же токеном, поэтому с ботом тревог не конфликтует. Нейросети на сервере нет: факты — из постов наших
каналов, тексты — из банка creo_templates.yaml.

Запросы (в любом виде): «закуп Питер Андрей», «перелив вк питер илья», «последствия москва», «вк питер», «ещё».
Запуск: python3 creo_bot.py        Проверка без MAX: python3 creo_bot.py --demo "закуп питер андрей"
"""
import argparse
import datetime as dt
import json
import logging
import logging.handlers
import os
import re
import sys
import time

import requests
import yaml
from zoneinfo import ZoneInfo

import creo_render

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS_FILE = os.environ.get("RADAR_SECRETS", "/etc/radar-bot/secrets.json")
STATE_DIR = os.environ.get("RADAR_STATE_DIR", "/var/lib/radar-bot")
INBOX = os.path.join(STATE_DIR, "creo_inbox.jsonl")
FEED = os.path.join(STATE_DIR, "creo_feed.jsonl")
STATE = os.path.join(STATE_DIR, "creo_state.json")
AVATARS = os.path.join(STATE_DIR, "creo_avatars")
log = logging.getLogger("creo")

DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]

HELP = ("Крео-бот сети. Напишите, что нужно, например:\n"
        "• закуп Питер Андрей — картинка + текст для закупа в MAX и строка кнопки\n"
        "• последствия Питер — короткий пост «Последствия атаки… Всё велось в…»\n"
        "• вк Питер Илья — нативный пост для ВК\n"
        "• перелив вк Питер — пост из группы ВК в канал MAX\n"
        "• ещё — другой вариант текста к прошлому запросу\n\n"
        "Факты бот берёт только из постов наших каналов за последние часы. Перед публикацией проверьте цифры "
        "по списку «Факты из ленты» под крео.")


def norm_link(s):
    s = (s or "").strip().lower()
    for p in ("https://", "http://", "www."):
        if s.startswith(p):
            s = s[len(p):]
    return s.rstrip("/")


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


class SafeDict(dict):
    def __missing__(self, k):
        return ""


# ---------------------------------------------------------------- факты из ленты
SKIP_RE = re.compile(r"приветствуем|благодарност|благодарим|реклама|erid|розыгрыш|подпишитесь на наш|доброе утро|"
                     r"спокойной ночи|памятк|как вести себя", re.I)
DANGER_RE = re.compile(r"(?<!без)опасност|угроз|тревог|фиксац|работа пво|сбит|сбил|атак|ракетн|бпла|беспилотн|дрон|"
                       r"укрыт|отой[дт]\w* от окон", re.I)
CLEAR_RE = re.compile(r"отбой|отмена (?:непосредственной )?опасности", re.I)
SHOT_RES = [
    re.compile(r"(\d{1,4})\s*(?:украинск\w*\s+|вражеск\w*\s+)?(?:БПЛА|беспилотник\w*|дрон\w*)[^.\n]{0,60}?"
               r"(?:сбит|сбил|уничтож|перехвач|подавл)", re.I),
    re.compile(r"(?:сбит\w*|сбил\w*|уничтож\w*|перехвач\w*)\s+(?:\S+\s+){0,3}?(\d{1,4})\s*(?:украинск\w*\s+|вражеск\w*\s+)?"
               r"(?:БПЛА|беспилотник|дрон)", re.I),
]
QUOTE_RE = re.compile(r"(отой[дт]\w* от окон|займите укрыти\w*|укройтесь|не подходите к окнам)", re.I)
AIRPORT_RE = re.compile(r"аэропорт|росавиац|рейс|ограничени", re.I)  # само название аэропорта в тревоге — не факт ограничений
FLIGHTS_RE = re.compile(r"(\d{1,3})\s+рейс", re.I)


def clean_lines(text):
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or "подпис" in s.lower() or re.search(r"https?://|max\.ru/", s):
            continue
        s = re.sub(r"^[^\w«\"(]+", "", s).strip()
        if s:
            out.append(s)
    return out


def post_text(row):
    return row.get("text") or row.get("fwd_text") or ""


def shot_of(text):
    for rx in SHOT_RES:
        m = rx.search(text)
        if m:
            return int(m.group(1))
    return None


def attr_of(text):
    t = text.lower()
    if "минобороны" in t or "мо рф" in t:
        return " по данным Минобороны"
    if "губернатор" in t or "дрозденко" in t or "собянин" in t or "мэр" in t:
        return " по данным губернатора"
    return ""


def quote_of(text):
    for line in clean_lines(text):
        m = QUOTE_RE.search(line)
        if m:
            sent = re.split(r"(?<=[.!])\s+", line)
            s = next((x for x in sent if QUOTE_RE.search(x)), line).strip()
            if len(s) > 42:
                s = m.group(1)
            s = s[0].upper() + s[1:]
            return s if s.endswith(("!", ".")) else s + "!"
    return None


def first_sentence(text, limit=60):
    lines = clean_lines(text)
    s = " ".join(l if l.endswith((".", "!", "?", "…")) else l + "." for l in lines)
    s = re.split(r"(?<=[.!?])\s+", s)[0] if s else ""
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


class Facts:
    """Что было в канале за последние часы: серия оповещений, сбитые, аэропорт, цитата, уведомления для картинки."""

    def __init__(self, ch, rows, now, hours):
        self.ch, self.now = ch, now
        lo = now - hours * 3600
        rows = [r for r in rows if r.get("t") and lo <= r["t"] / 1000 <= now + 60]
        rows = [r for r in rows if post_text(r).strip() and not SKIP_RE.search(post_text(r))]
        rows.sort(key=lambda r: r["t"])
        self.rows = rows
        dangers = [r for r in rows if DANGER_RE.search(post_text(r)) and not CLEAR_RE.search(post_text(r))]
        clears = [r for r in rows if CLEAR_RE.search(post_text(r))]
        self.series = []
        if dangers:
            chain = [dangers[-1]]
            for r in reversed(dangers[:-1]):
                if chain[0]["t"] - r["t"] <= 4 * 3600 * 1000:
                    chain.insert(0, r)
                else:
                    break
            start = chain[0]["t"]
            self.series = [r for r in rows if r["t"] >= start]
        last_d = dangers[-1]["t"] if dangers else None
        after = [c for c in clears if last_d and c["t"] > last_d]
        self.clear = after[0] if after else None
        self.ongoing = bool(last_d) and not self.clear
        self.first = self.series[0] if self.series else None
        self.shot, self.shot_row = None, None
        for r in reversed(self.series):
            n = shot_of(post_text(r))
            if n:
                self.shot, self.shot_row = n, r
                break
        alts = [a for a in (ch.get("airport") or "").split("|") if a]
        self.airport_row, self.airport_name = None, alts[0] if alts else ""
        for r in reversed(self.series):
            t = post_text(r)
            if AIRPORT_RE.search(t):
                self.airport_row = r
                self.airport_name = next((a for a in alts if a.lower() in t.lower()), self.airport_name)
                break
        self.quote, self.quote_row = None, None
        for r in reversed(self.series):
            q = quote_of(post_text(r))
            if q:
                self.quote, self.quote_row = q, r
                break

    def hm(self, r, tz):
        return dt.datetime.fromtimestamp(r["t"] / 1000, tz).strftime("%H:%M")

    def ago(self, r):
        m = int((self.now - r["t"] / 1000) // 60)
        if m < 2:
            return "сейчас"
        if m < 60:
            return f"{m} мин"
        return f"{m // 60} ч"

    def note(self, r):
        t = post_text(r)
        lines = clean_lines(t)
        ch = self.ch
        if r is self.shot_row:
            sub = "Боевая работа продолжается" if "продолжа" in t.lower() else attr_of(t).strip().capitalize()
            return {"title": f"{self.shot} БПЛА сбиты над {ch['region_ins']}", "sub": sub, "red": True}
        if r is self.airport_row:
            m = FLIGHTS_RE.search(t)
            title = (f"{self.airport_name}: задержаны {m.group(1)} рейс{plural(m.group(1), '', 'а', 'ов')}"
                     if m and "задерж" in t.lower() else f"{self.airport_name or 'Аэропорт'}: ограничения")
            subs = []
            if "отмен" in t.lower():
                subs.append("есть отменённые рейсы")
            if "запасн" in t.lower():
                subs.append("самолёты уходили на запасной")
            sub = ", ".join(subs) if subs else "Ограничения на приём и выпуск рейсов"
            return {"title": title, "sub": sub[0].upper() + sub[1:], "red": True}
        if shot_of(t):  # более ранняя сводка сбитых: показываем только то, что в ней ещё важного (угроза городу)
            if re.search(r"угроз", t, re.I) and re.search(ch["city_loc"][:6], t, re.I):
                sub = "Возможны сбои мобильного интернета" if "интернет" in t.lower() else attr_of(t).strip().capitalize()
                return {"title": f"Угроза БПЛА объявлена в {ch['city_loc']}", "sub": sub, "red": True}
            return {"title": "", "sub": "", "red": False}
        if r is self.quote_row:
            sub = "Все районы города" if "все районы" in t.lower() else (lines[0] if lines else "")
            if sub.lower().rstrip("!") == self.quote.lower().rstrip("!"):
                sub = ""
            return {"title": self.quote, "sub": sub, "red": True}
        if CLEAR_RE.search(t):
            place = next((l for l in lines if not CLEAR_RE.search(l)), ch["place"])
            return {"title": "Отбой опасности по БПЛА", "sub": place, "red": False}
        ev = [l for l in lines if DANGER_RE.search(l)]
        places = [l for l in lines if not DANGER_RE.search(l)]
        if places:
            title = places[0]
            if len(places) > 1 and len(title) < 22 and not re.search(r"област|край|республик", places[1], re.I):
                title = f"{title}, {places[1]}"
            sub = ev[-1] if ev else (places[1] if len(places) > 1 else "")
        else:
            title, sub = (ev[0], ev[1] if len(ev) > 1 else "") if ev else (first_sentence(t, 40), "")
        red = bool(re.search(r"угроз", t, re.I) and re.search(ch["city"][:4], t, re.I))
        return {"title": title, "sub": sub, "red": red}

    def notes(self, k=5):
        pool = self.series or self.rows[-k:]
        if not pool:
            return []
        score = {}
        for i, r in enumerate(pool):
            t = post_text(r)
            s = i * 0.5  # свежее — выше
            if r is self.shot_row:
                s += 100
            elif r is self.airport_row:
                s += 90
            elif r is self.quote_row:
                s += 85
            elif CLEAR_RE.search(t):
                s += 70
            elif DANGER_RE.search(t):
                s += 40
            score[id(r)] = s
        top = sorted(pool, key=lambda r: score[id(r)], reverse=True)
        chosen, titles = [], set()
        for r in [pool[-1]] + top:
            if r in chosen:
                continue
            n = self.note(r)
            if not n["title"] or n["title"].lower() in titles:
                continue
            n["ago"], n["t"] = self.ago(r), r["t"]
            chosen.append(n)
            titles.add(n["title"].lower())
            if len(chosen) >= k:
                break
        chosen.sort(key=lambda n: n["t"], reverse=True)
        return chosen

    def used_posts(self, tz):
        rs = [r for r in (self.first, self.shot_row, self.airport_row, self.quote_row, self.clear) if r]
        seen, out = set(), []
        for r in sorted(rs, key=lambda r: r["t"]):
            if id(r) in seen:
                continue
            seen.add(id(r))
            out.append(f"{self.hm(r, tz)} — {first_sentence(post_text(r), 90)}")
        return out


# ---------------------------------------------------------------- тексты
class Writer:
    def __init__(self, tpl, rot):
        self.tpl, self.rot = tpl, rot

    def pick(self, key, items, have):
        ok = [x for x in items if all(n in have for n in (x.get("need") or []))] if items and isinstance(items[0], dict) \
            else list(items)
        if not ok:
            return ""
        i = self.rot.get(key, 0) % len(ok)
        self.rot[key] = self.rot.get(key, 0) + 1
        x = ok[i]
        return x["text"] if isinstance(x, dict) else x

    @staticmethod
    def vars(ch, f, tz):
        v = dict(ch)
        v["link"] = norm_link(ch["link"])
        if f.shot:
            v.update(shot=str(f.shot), shot_word=plural(f.shot, "беспилотник", "беспилотника", "беспилотников"),
                     shot_verb=plural(f.shot, "сбит", "сбиты", "сбиты"),
                     shot_adj=plural(f.shot, "сбитый беспилотник", "сбитых беспилотника", "сбитых беспилотников"))
            attr = attr_of(post_text(f.shot_row))
            v["shot_sentence"] = f". Над областью {v['shot_verb']} {f.shot} {v['shot_word']}{attr}."
            v["shot_sentence_cap"] = f"Над {ch['region_ins']} {v['shot_verb']} {f.shot} {v['shot_word']}{attr}. "
        else:
            v["shot_sentence"], v["shot_sentence_cap"] = ".", ""
        v["ongoing_sentence"] = " Боевая работа продолжается." if f.ongoing and f.shot and "продолжа" in post_text(f.shot_row).lower() else ""
        v["first_time"] = f.hm(f.first, tz) if f.first else ""
        v["clear_time"] = f.hm(f.clear, tz) if f.clear else ""
        v["airport_name"] = f.airport_name
        v["airport_cta"] = f" и ограничения в {f.airport_name}" if f.airport_name else ""
        v["airport_sentence"] = (f"В аэропорту {f.airport_name} вводили ограничения на рейсы.\n\n"
                                 if f.airport_row else "")
        if f.quote:
            v["quote"], v["quote_time"] = f.quote, f.hm(f.quote_row, tz)
            v["quote_sentence"] = f"Жителям писали: «{f.quote}»\n\n"
            v["quote_lead"] = (f"В {v['quote_time']} у подписчиков «{ch['name']}» в MAX высветилось: «{f.quote}».\n\n")
        else:
            v["quote_sentence"], v["quote_lead"] = "", ""
        big = (f.shot or 0) >= 20
        v["attack"], v["attack_gen"] = ("массированной", "массированной") if big else ("ночной", "ночной")
        for k in ("city", "region_ins", "quote", "shot_word", "airport_name", "place"):
            v[k.upper()] = (v.get(k) or "").upper()
        return SafeDict(v)

    def have(self, f):
        h = set()
        if f.shot:
            h.add("shot")
        if f.quote:
            h.add("quote")
        if f.airport_row and f.airport_name:
            h.add("airport")
        if f.ongoing:
            h.add("ongoing")
        if f.clear:
            h.add("clear")
        return h

    def max_zakup(self, ch, f, tz):
        v, net, t = self.vars(ch, f, tz), ch["network"], self.tpl["max_zakup"]
        head = self.pick(f"mz:h:{ch['key']}", t["headline"][net], self.have(f)).format_map(v)
        body = []
        if f.first:
            body.append(f"С {v['first_time']} в {ch['region_loc']} действовала опасность по БПЛА."
                        if not f.ongoing else f"С {v['first_time']} в {ch['region_loc']} опасность по БПЛА.")
        if f.quote:
            body.append(f"Жителям писали: «{f.quote}»")
        facts = []
        if f.shot:
            facts.append(f"<b>Над {ch['region_ins']} {v['shot_verb']} {f.shot} {v['shot_word']}"
                         f"{attr_of(post_text(f.shot_row))}.</b>")
        if f.airport_row:
            facts.append(f"В {f.airport_name} вводили ограничения на рейсы.")
        if f.clear:
            facts.append(f"Отбой дали только в {v['clear_time']}.")
        elif f.ongoing and f.shot and "продолжа" in post_text(f.shot_row).lower():
            facts.append("<b>Боевая работа продолжается прямо сейчас.</b>")
        if facts:
            body.append(" ".join(facts))
        cta = self.pick(f"mz:c:{ch['key']}", t["cta"][net], set()).format_map(v)
        return "\n\n".join([head] + body + [cta, t["link"].format_map(v)])

    def summary(self, ch, f, tz):
        v, net, t = self.vars(ch, f, tz), ch["network"], self.tpl["summary"]
        head = self.pick(f"su:h:{ch['key']}", t["headline"][net], set()).format_map(v)
        b = []
        if f.shot:
            b.append(f"▪️ {f.shot} {v['shot_adj']} за ночь")
        if f.quote:
            b.append(f"▪️ «{f.quote}» — предупреждение жителям")
        if f.airport_row:
            b.append(f"▪️ {f.airport_name}: ограничения на рейсы")
        if f.first:
            b.append(f"▪️ Тревога с {v['first_time']}" + (f" до {v['clear_time']}" if f.clear else ", отбоя пока нет"))
        cta = self.pick(f"su:c:{ch['key']}", t["cta"][net], set()).format_map(v)
        return "\n\n".join([head, "\n".join(b), cta, t["link"].format_map(v)])

    def vk(self, kind, ch, f, tz):
        v = self.vars(ch, f, tz)
        text = self.pick(f"{kind}:{ch['key']}", self.tpl[kind][ch["network"]], set()).format_map(v)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- запрос
def parse_request(text, cfg, user_nets):
    t = " " + (text or "").lower().replace("ё", "е") + " "
    if re.search(r"\bещ[её]\b|другой вариант", t):
        return {"again": True}
    if t.strip() in ("/start", "start", "помощь", "/help", "help", "привет"):
        return {"help": True}
    net = "andrey" if "андре" in t else "ilya" if re.search(r"\bиль|\bилья|илюх", t) else None
    if "последств" in t or "итог" in t:
        kind = "summary"
    elif "перелив" in t and re.search(r"\bвк\b|вконт", t):
        kind = "vk_pereliv"
    elif re.search(r"\bвк\b|вконт", t):
        kind = "vk_story"
    else:
        kind = "max_zakup"
    chans = [c for c in cfg["channels"] if c["network"] in user_nets and (not net or c["network"] == net)]
    hit = [c for c in chans if any(w in t for w in c.get("words", []))]
    return {"kind": kind, "net": net, "channels": hit}


# ---------------------------------------------------------------- MAX
class MaxApi:
    def __init__(self, api, token):
        self.api = api.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = token

    def chat(self, chat_id):
        r = self.s.get(f"{self.api}/chats/{chat_id}", timeout=20)
        return r.json() if r.status_code == 200 else {}

    def upload_image(self, data):
        r = self.s.post(f"{self.api}/uploads", params={"type": "image"}, timeout=20)
        r.raise_for_status()
        url = r.json()["url"]
        up = requests.post(url, files={"data": ("creo.jpg", data, "image/jpeg")}, timeout=60)
        up.raise_for_status()
        j = up.json()
        if j.get("token"):
            return j["token"]
        photos = j.get("photos") or {}
        return next(iter(photos.values()))["token"]

    def send(self, chat_id, text, image=None, html=True):
        body = {"text": text}
        if html:
            body["format"] = "html"
        if image:
            body["attachments"] = [{"type": "image", "payload": {"token": self.upload_image(image)}}]
        for attempt in range(5):
            r = self.s.post(f"{self.api}/messages", params={"chat_id": chat_id}, json=body, timeout=30)
            if r.status_code == 200:
                return r.json()
            if "not.ready" in r.text or r.status_code == 429:  # картинка ещё обрабатывается / слишком часто
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"MAX /messages {r.status_code}: {r.text[:200]}")
        raise RuntimeError("MAX /messages: не отправилось после повторов")


# ---------------------------------------------------------------- сервис
class Creo:
    def __init__(self, cfg, tpl, max_api, tz):
        self.cfg, self.tpl, self.max, self.tz = cfg, tpl, max_api, tz
        try:
            self.st = json.load(open(STATE, encoding="utf-8"))
        except Exception:
            self.st = {}
        for k, d in (("offset", 0), ("done", []), ("last", {}), ("rot", {}), ("noaccess", {})):
            self.st.setdefault(k, d)
        self.writer = Writer(tpl, self.st["rot"])

    def save(self):
        tmp = STATE + ".tmp"
        json.dump(self.st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, STATE)

    def user(self, uid):
        return next((u for u in self.cfg.get("users") or [] if str(u.get("id")) == str(uid)), None)

    def feed(self, ch):
        src = norm_link(ch["link"])
        rows = []
        try:
            for line in open(FEED, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if norm_link(r.get("src")) == src:
                    rows.append(r)
        except FileNotFoundError:
            pass
        return rows

    def avatar(self, chat_id):
        if not chat_id or not self.max:
            return None
        os.makedirs(AVATARS, exist_ok=True)
        p = os.path.join(AVATARS, f"{chat_id}.jpg")
        try:
            if not os.path.exists(p) or time.time() - os.path.getmtime(p) > 86400:
                icon = ((self.max.chat(chat_id) or {}).get("icon") or {}).get("url")
                if icon:
                    open(p, "wb").write(requests.get(icon, timeout=20).content)
            from PIL import Image
            return Image.open(p) if os.path.exists(p) else None
        except Exception as e:
            log.warning("аватар %s: %s", chat_id, e)
            return None

    def build(self, req, ch, now=None):
        """-> список сообщений [(текст, картинка|None, html)]"""
        now = now or time.time()
        rows = self.feed(ch)
        f = Facts(ch, rows, now, float(self.cfg.get("feed_hours", 12)))
        if not f.series:
            return [(f"За последние {self.cfg.get('feed_hours', 12)} ч в «{ch['name']}» не было оповещений — "
                     f"собрать крео по атаке не из чего. Если бот не админ в этом канале, он не видит его посты.",
                     None, False)]
        local = dt.datetime.fromtimestamp(now, self.tz)
        img = None
        if req["kind"] in ("max_zakup", "vk_story", "vk_pereliv"):
            chat_id = (rows[-1] if rows else {}).get("chat")
            img = creo_render.lockscreen(f.notes(), ch["name"], self.avatar(chat_id), local.strftime("%H:%M"),
                                         f"{DAYS[local.weekday()]}, {local.day} {MONTHS[local.month - 1]}")
        button = f"{ch['button']}👈 - https://{norm_link(ch['link'])}"
        facts = "Факты из ленты «" + ch["name"] + "» (проверьте перед публикацией):\n" + "\n".join(f.used_posts(self.tz))
        if req["kind"] == "max_zakup":
            return [(self.writer.max_zakup(ch, f, self.tz), img, True), (button, None, False), (facts, None, False)]
        if req["kind"] == "summary":
            return [(self.writer.summary(ch, f, self.tz), None, True), (button, None, False), (facts, None, False)]
        return [(self.writer.vk(req["kind"], ch, f, self.tz), img, False), (facts, None, False)]

    def answer(self, row):
        uid, chat_id, text = row.get("user_id"), row.get("chat_id"), row.get("text") or ""
        u = self.user(uid)
        if not u:
            last = self.st["noaccess"].get(str(uid), 0)
            if time.time() - last > 6 * 3600:
                self.st["noaccess"][str(uid)] = time.time()
                return [(f"Нет доступа к крео-боту. Ваш ID: {uid} — перешлите его Никите.", None, False)]
            return []
        nets = u.get("networks") or list(self.cfg["networks"])
        req = parse_request(text, self.cfg, nets)
        if req.get("help"):
            return [(HELP, None, False)]
        if req.get("again"):
            prev = self.st["last"].get(str(uid))
            if not prev:
                return [("Сначала напишите запрос, например «закуп Питер Андрей».", None, False)]
            req = parse_request(prev, self.cfg, nets)
        else:
            self.st["last"][str(uid)] = text
        chans = req["channels"]
        if not chans:
            names = ", ".join(f"{c['name']} ({self.cfg['networks'][c['network']]})"
                              for c in self.cfg["channels"] if c["network"] in nets)
            return [("Не понял, какой канал. Доступны: " + names + "\n\n" + HELP, None, False)]
        if len({c["network"] for c in chans}) > 1:
            return [("Уточните сеть: «… Андрей» или «… Илья».", None, False)]
        return self.build(req, chans[0])

    def process(self):
        try:
            size = os.path.getsize(INBOX)
        except FileNotFoundError:
            return
        if size < self.st["offset"]:
            self.st["offset"] = 0  # очередь обрезали — читаем заново, повторы отсекаются по mid
        if size == self.st["offset"]:
            return
        with open(INBOX, encoding="utf-8") as fh:
            fh.seek(self.st["offset"])
            data = fh.read()
            self.st["offset"] = fh.tell()
        for line in data.splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            mid = row.get("mid")
            if not mid or mid in self.st["done"]:
                continue
            self.st["done"] = (self.st["done"] + [mid])[-300:]
            if row.get("t") and time.time() - row["t"] / 1000 > 1800:
                continue  # старше 30 минут (сервис стоял) — не отвечаем
            try:
                msgs = self.answer(row)
                for text, img, html in msgs:
                    self.max.send(row["chat_id"], text, img, html)
                log.info("ответ %s (%s): %s → %d сообщ.", row.get("user_id"), row.get("name"),
                         (row.get("text") or "")[:60].replace("\n", " "), len(msgs))
            except Exception as e:
                log.exception("ошибка ответа %s: %s", row.get("user_id"), e)
                try:
                    self.max.send(row["chat_id"], "Не получилось собрать крео, ошибка записана в журнал.", None, False)
                except Exception:
                    pass
        self.save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", help="собрать крео по запросу без MAX (картинка в demo.jpg)")
    ap.add_argument("--net", default="andrey,ilya")
    ap.add_argument("--now", help="для --demo: «ГГГГ-ММ-ДД ЧЧ:ММ» по МСК вместо текущего времени")
    a = ap.parse_args()
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(os.path.join(STATE_DIR, "creo.log"), maxBytes=1_000_000,
                                                             backupCount=1, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%d.%m %H:%M:%S",
                        handlers=handlers)
    cfg = yaml.safe_load(open(os.path.join(HERE, "creo.yaml"), encoding="utf-8"))
    tpl = yaml.safe_load(open(os.path.join(HERE, "creo_templates.yaml"), encoding="utf-8"))
    tz = ZoneInfo(cfg.get("timezone", "Europe/Moscow"))
    if a.demo:
        c = Creo(cfg, tpl, None, tz)
        req = parse_request(a.demo, cfg, a.net.split(","))
        now = dt.datetime.strptime(a.now, "%Y-%m-%d %H:%M").replace(tzinfo=tz).timestamp() if a.now else None
        for i, (text, img, html) in enumerate(c.build(req, req["channels"][0], now)):
            print(f"--- сообщение {i + 1}{' + картинка demo.jpg' if img else ''}\n{text}\n")
            if img:
                open("demo.jpg", "wb").write(img)
        return
    if not cfg.get("enabled", True):
        log.info("крео-бот выключен в creo.yaml")
        while True:
            time.sleep(3600)
    while True:
        try:
            token = json.load(open(SECRETS_FILE, encoding="utf-8")).get("max_token")
        except Exception:
            token = None
        if token:
            break
        time.sleep(30)
    creo = Creo(cfg, tpl, MaxApi(cfg.get("api", "https://platform-api.max.ru"), token), tz)
    log.info("крео-бот запущен: %d польз., %d каналов", len(cfg.get("users") or []), len(cfg["channels"]))
    while True:
        try:
            creo.process()
        except Exception as e:
            log.exception("ошибка цикла: %s", e)
        time.sleep(2)


if __name__ == "__main__":
    main()

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


REGION_RE = re.compile(r"\w+(?:ск|цк)(?:ой|ая|ую|ий|ого|ом|ие|их)\s+(?:област|кра[йюяе]|республик)|республик\w*\s+\w+", re.I)
ALERT_RE = re.compile(r"(?<!без)опасност|угроз|тревог|фиксац|работа пво|сбити|сбит|сбил|укрыт|отой[дт]\w* от окон|"
                      r"ракетн|атак|бпла|беспилотн|дрон", re.I)


def own_words(ch):
    return [w.strip() for w in ch.get("words", []) if len(w.strip()) >= 3]


def is_federal(text):
    """27.11: сводка МО «318 БПЛА над территориями: Брянской…, Ленинградской…» — не про наш регион, в крео не берём."""
    t = text.lower()
    regions = {m.group(0).split()[0] for m in REGION_RE.finditer(t)}
    return "над территори" in t or "регионами россии" in t or len(regions) >= 3


def is_own(ch, text):
    if ch.get("all_regions"):  # «Радар по всей России»: свой регион — вся страна
        return True
    """Пост про наш регион: есть слово региона, или вообще нет чужой области (местные «Пулково / Опасность»).
    Смотрим без подписи канала: «Радар Санкт-Петербург и ЛО - Подписаться» есть под каждым постом."""
    t = " ".join(clean_lines(text)).lower()
    if any(w in t for w in own_words(ch)):
        return True
    return not REGION_RE.search(t)


def is_alert(ch, text):
    """Оповещение нашего канала: короткое, про наш регион, со словами тревоги. Новости и сводки — нет."""
    body = " ".join(clean_lines(text))
    fed_ok = ch.get("all_regions") or not is_federal(text)
    return len(body) <= 300 and ALERT_RE.search(body) is not None and fed_ok and is_own(ch, text)


def own_shot(ch, text):
    """Сбитые — только в предложении про наш регион и не из общероссийской сводки."""
    if ch.get("all_regions"):
        return shot_of(" ".join(clean_lines(text)))
    if is_federal(text):
        return None
    for sent in re.split(r"(?<=[.!?])\s+", "\n".join(clean_lines(text))):
        if any(w in sent.lower() for w in own_words(ch)):
            n = shot_of(sent)
            if n:
                return n
    return None


class Facts:
    """Что было в канале за последние часы: серия оповещений, сбитые, аэропорт, цитата, уведомления для картинки."""

    def __init__(self, ch, rows, now, hours):
        self.ch, self.now = ch, now
        lo = now - hours * 3600
        rows = [r for r in rows if r.get("t") and lo <= r["t"] / 1000 <= now + 60]
        rows = [r for r in rows if post_text(r).strip() and not SKIP_RE.search(post_text(r))]
        rows.sort(key=lambda r: r["t"])
        self.rows = rows
        alerts = [r for r in rows if is_alert(ch, post_text(r))]
        dangers = [r for r in alerts if not CLEAR_RE.search(post_text(r))]
        clears = [r for r in alerts if CLEAR_RE.search(post_text(r))]
        self.series = []
        if dangers:
            chain = [dangers[-1]]
            for r in reversed(dangers[:-1]):
                if chain[0]["t"] - r["t"] <= 4 * 3600 * 1000:
                    chain.insert(0, r)
                else:
                    break
            start = chain[0]["t"]
            self.series = [r for r in rows if r["t"] >= start and (ch.get("all_regions") or not is_federal(post_text(r))) and is_own(ch, post_text(r))]
        last_d = dangers[-1]["t"] if dangers else None
        after = [c for c in clears if last_d and c["t"] > last_d]
        self.clear = after[0] if after else None
        # «атака идёт» — только если последнее оповещение свежее 3 ч и отбоя после него не было
        self.ongoing = bool(last_d) and not self.clear and now - last_d / 1000 <= 3 * 3600
        self.first = self.series[0] if self.series else None
        self.shot, self.shot_row = None, None
        for r in reversed(self.series):
            n = own_shot(ch, post_text(r))
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
        # районы из оповещений серии: «Всеволожский район», «Кировский район»… (до 3, по первому упоминанию)
        self.places = []
        for r in self.series:
            for line in clean_lines(post_text(r)):
                m = re.match(r"^([А-ЯЁ][а-яё-]+(?:ский|цкий|ской|цкой))\s+район", line)
                if m and m.group(1) not in self.places:
                    self.places.append(m.group(1))
        self.places = self.places[:3]

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
        if own_shot(ch, t):  # более ранняя сводка сбитых: показываем только то, что в ней ещё важного (угроза городу)
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
        ok = [x for x in items or [] if not isinstance(x, dict) or all(n in have for n in (x.get("need") or []))]
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
        v["name"] = ch["name"]
        v["area"] = "всем регионам страны" if ch.get("all_regions") else f"{ch['city_dat']} и области"
        if f.shot:
            v.update(shot=str(f.shot), shot_word=plural(f.shot, "беспилотник", "беспилотника", "беспилотников"),
                     shot_verb=plural(f.shot, "сбит", "сбиты", "сбиты"),
                     shot_adj=plural(f.shot, "сбитый беспилотник", "сбитых беспилотника", "сбитых беспилотников"))
            attr = attr_of(post_text(f.shot_row))
            v["shot_attr"] = attr
            v["shot_sentence"] = f". Над областью {v['shot_verb']} {f.shot} {v['shot_word']}{attr}."
            v["shot_sentence_cap"] = f"Над {ch['region_ins']} {v['shot_verb']} {f.shot} {v['shot_word']}{attr}. "
        else:
            v["shot_sentence"], v["shot_sentence_cap"], v["shot_attr"] = ".", "", ""
        v["ongoing_sentence"] = " Опасность сохраняется." if f.ongoing else ""
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
        # когда началось и сколько длилось
        h = dt.datetime.fromtimestamp(f.first["t"] / 1000, tz).hour if f.first else 3
        when = "ночью" if h < 6 else "утром" if h < 12 else "днём" if h < 18 else "вечером"
        v["when"], v["When"] = when, when.capitalize()
        end = f.clear["t"] / 1000 if f.clear else f.now
        hours = int((end - f.first["t"] / 1000) // 3600) if f.first else 0
        v["dur_nom"] = f"{hours} {plural(hours, 'час', 'часа', 'часов')}" if hours >= 2 else ""
        v["dur_gen"] = f"{hours} {plural(hours, 'часа', 'часов', 'часов')}" if hours >= 2 else ""
        big = (f.shot or 0) >= 20
        adj = {"ночью": "ночн", "утром": "утренн", "днём": "дневн", "вечером": "вечерн"}[when]
        v["attack"] = "массированной" if big else adj + "ей"          # «атаки» — род. п.
        v["attack_nom"] = ("Массированная" if big else (adj + "яя").capitalize())
        p = f.places
        v["places_list"] = (", ".join(p[:-1]) + " и " + p[-1]) if len(p) > 1 else (p[0] if p else "")
        v["places"] = (", ".join(p[:-1]) + " и " + p[-1] + " районы") if len(p) > 1 else (p[0] + " район" if p else "")
        for k in ("city", "region_ins", "region_loc", "quote", "shot_word", "shot_verb", "shot_adj", "airport_name",
                  "place", "when", "dur_nom", "places", "city_loc"):
            v[k.upper()] = (v.get(k) or "").upper()
        return SafeDict(v)

    def have(self, f, v=None):
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
        else:
            h.add("active")  # отбоя не было — можно писать «опасность» в настоящем времени
        if f.places:
            h.add("places")
        if not (v or {}).get("all_regions"):
            h.add("regional")
        if f.first and ((f.clear["t"] if f.clear else f.now * 1000) - f.first["t"]) >= 2 * 3600 * 1000:
            h.add("dur")
        if v is not None and v["when"] == "ночью":
            h.add("night")
        return h

    def line(self, key, items, have, v):
        t = self.pick(key, items or [], have)
        return t.format_map(v) if t else ""

    def max_zakup(self, ch, f, tz):
        v, net, t, have = self.vars(ch, f, tz), ch["network"], self.tpl["max_zakup"], None
        have = self.have(f, v)
        k = ch["key"]
        head = self.line(f"mz:h:{k}", t["headline"][net], have, v)
        body = [self.line(f"mz:lead:{k}", t["lead"][net], have, v)]
        if f.places:
            body.append(self.line(f"mz:pl:{k}", t["places"][net], have, v))
        if f.quote:
            body.append(self.line(f"mz:q:{k}", t["quote"][net], have, v))
        facts = []
        if f.shot:
            facts.append(self.line(f"mz:s:{k}", t["shot"][net], have, v))
        if f.airport_row:
            facts.append(self.line(f"mz:a:{k}", t["airport"][net], have, v))
        if f.ongoing:
            facts.append(self.line(f"mz:on:{k}", t["ongoing"][net], have, v))
        if facts:
            body.append(" ".join(x for x in facts if x))
        cta = self.line(f"mz:c:{k}", t["cta"][net], have, v)
        return "\n\n".join(x for x in [head] + body + [cta, t["link"].format_map(v)] if x)

    def summary(self, ch, f, tz):
        v, net, t, have = self.vars(ch, f, tz), ch["network"], self.tpl["summary"], None
        have = self.have(f, v)
        head = self.line(f"su:h:{ch['key']}", t["headline"][net], have, v)
        b = []
        if f.shot:
            b.append(f"▪️ {f.shot} {v['shot_adj']} {v['when'] if v['when'] != 'ночью' else 'за ночь'}")
        if f.places:
            b.append(f"▪️ Фиксации: {v['places']}")
        if f.quote:
            b.append(f"▪️ «{f.quote}» — предупреждение жителям")
        if f.airport_row:
            b.append(f"▪️ {f.airport_name}: ограничения на рейсы")
        if f.first:
            b.append(f"▪️ Тревога с {v['first_time']}" + (f" до {v['clear_time']}" if f.clear else ", отбоя пока нет"))
        cta = self.line(f"su:c:{ch['key']}", t["cta"][net], have, v)
        return "\n\n".join([head, "\n".join(b), cta, t["link"].format_map(v)])

    def evergreen(self, ch, f, tz):
        v = self.vars(ch, f, tz)
        t = self.tpl["evergreen"]
        text = self.line(f"ev:{ch['key']}", t[ch["network"]], self.have(f, v), v)
        return text + "\n\n" + t["link"].format_map(v)

    def vk(self, kind, ch, f, tz):
        v = self.vars(ch, f, tz)
        text = self.line(f"{kind}:{ch['key']}", self.tpl[kind][ch["network"]], self.have(f, v), v)
        text = "\n".join(l.rstrip() for l in text.splitlines())
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
        button = f"{ch['button']}👈 - https://{norm_link(ch['link'])}"
        if not f.series:
            note = (f"За последние {self.cfg.get('feed_hours', 12)} ч в «{ch['name']}» не было оповещений, поэтому крео "
                    f"без цифр атаки — на тихое время." if rows else
                    f"Бот пока не видит постов «{ch['name']}» — добавьте «Помощник сети Радар» админом без прав в этот "
                    f"канал. Пока — крео без цифр атаки, на тихое время.")
            return [(self.writer.evergreen(ch, f, self.tz), None, True), (button, None, False), (note, None, False)]
        local = dt.datetime.fromtimestamp(now, self.tz)
        img = None
        if req["kind"] in ("max_zakup", "vk_story", "vk_pereliv"):
            chat_id = (rows[-1] if rows else {}).get("chat")
            img = creo_render.lockscreen(f.notes(), ch["name"], self.avatar(chat_id), local.strftime("%H:%M"),
                                         f"{DAYS[local.weekday()]}, {local.day} {MONTHS[local.month - 1]}")
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

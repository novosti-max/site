"""Тесты крео-бота: python3 tests/test_creo.py (из корня ветки radar-bot). Сеть не нужна.
Лента — посты Питера 27.11 (tests/creo_feed_fixture.jsonl)."""
import json, os, shutil, sys, tempfile, time, datetime as dt
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_ST = tempfile.mkdtemp()
shutil.copy(os.path.join(HERE, "creo_feed_fixture.jsonl"), os.path.join(_ST, "creo_feed.jsonl"))
os.environ["RADAR_STATE_DIR"] = _ST
sys.path.insert(0, ROOT)
import yaml, creo_bot, creo_hook

cfg = yaml.safe_load(open(os.path.join(ROOT, "creo.yaml"), encoding="utf-8"))
tpl = yaml.safe_load(open(os.path.join(ROOT, "creo_templates.yaml"), encoding="utf-8"))
tz = ZoneInfo("Europe/Moscow")
ok = 0
def check(cond, name):
    global ok
    if not cond:
        print("ОШИБКА:", name); sys.exit(1)
    ok += 1

# запросы
r = creo_bot.parse_request("закуп Питер Андрей", cfg, ["andrey", "ilya"])
check(r["kind"] == "max_zakup" and [c["key"] for c in r["channels"]] == ["spb_andrey"], "закуп питер андрей")
r = creo_bot.parse_request("перелив вк питер", cfg, ["ilya"])
check(r["kind"] == "vk_pereliv" and r["channels"][0]["key"] == "spb_ilya", "перелив вк у пользователя сети Ильи")
r = creo_bot.parse_request("последствия москва", cfg, ["andrey", "ilya"])
check(r["kind"] == "summary" and r["channels"][0]["key"] == "msk_andrey", "последствия москва")
r = creo_bot.parse_request("закуп питер", cfg, ["andrey", "ilya"])
check(len({c["network"] for c in r["channels"]}) == 2, "без сети — два канала, бот попросит уточнить")
check(creo_bot.parse_request("ещё", cfg, ["andrey"]).get("again"), "ещё")
check(creo_bot.plural(44, "беспилотник", "беспилотника", "беспилотников") == "беспилотника", "plural 44")
check(creo_bot.plural(45, "беспилотник", "беспилотника", "беспилотников") == "беспилотников", "plural 45")

# факты
check(creo_bot.shot_of("В небе над Ленинградской областью сбито 44 БПЛА противника.") == 44, "сбито 44 БПЛА")
check(creo_bot.shot_of("30 БПЛА сбиты над Ленобластью") == 30, "30 БПЛА сбиты")
check(creo_bot.shot_of("18 БПЛА сбили над Ленинградской областью") == 18, "18 БПЛА сбили")
check(creo_bot.shot_of("Опасность по БПЛА сохраняется") is None, "нет числа — нет сбитых")
check(creo_bot.quote_of("Санкт-Петербург\nОтойти от окон!\nВсе районы города!") == "Отойти от окон!", "цитата")

c = creo_bot.Creo(cfg, tpl, None, tz)
spb = next(x for x in cfg["channels"] if x["key"] == "spb_andrey")
now = dt.datetime(2026, 11, 27, 7, 16, tzinfo=tz).timestamp()
f = creo_bot.Facts(spb, c.feed(spb), now, 12)
check(f.shot == 44 and f.ongoing and f.clear is None, "до отбоя: 44 сбиты, атака идёт, будущий отбой не виден")
check(f.airport_row is None, "«Пулково / Опасность по БПЛА» — не факт ограничений")
msgs = c.build({"kind": "max_zakup"}, spb, now)
check(len(msgs) == 3 and msgs[0][1] and msgs[1][0] == "Радар Питер👈 - https://max.ru/novostniknovo", "закуп: картинка, текст, кнопка")
check("ограничения на рейсы" not in msgs[0][0], "не выдумывает ограничения в аэропорту")
for bad in ("VPN", "белые списки", "белых списк", "обязан"):
    check(bad.lower() not in msgs[0][0].lower(), "нет слова " + bad)
f2 = creo_bot.Facts(spb, c.feed(spb), dt.datetime(2026, 11, 27, 8, 50, tzinfo=tz).timestamp(), 12)
check(f2.clear is not None and not f2.ongoing, "после 08:37 — отбой")

# 27.11: сводка МО «318 БПЛА над территориями…» и новость про другой регион не ломают крео Питера
f3 = creo_bot.Facts(spb, c.feed(spb), dt.datetime(2026, 11, 27, 11, 9, tzinfo=tz).timestamp(), 12)
check(f3.shot == 44, "318 из общероссийской сводки не берём")
check(f3.clear is not None and not f3.ongoing, "новость про Кстово в 10:57 — не тревога, атака закончилась отбоем 08:37")
check(creo_bot.is_federal("уничтожено 318 БПЛА над территориями: Брянской области; Ленинградской области;"), "сводка МО — федеральная")
check(not creo_bot.is_own(spb, "Беспилотники атаковали Нижегородскую область"), "чужая область — не наша")
check(creo_bot.is_own(spb, "Пулково\nОпасность по БПЛА"), "местное без области — наше")

# банк текстов: ни один кусок не оставляет «{…}», нет запрещённых слов, сети не повторяют друг друга
import re as _re
_v = creo_bot.Writer.vars(spb, f, tz)
def _walk(x):
    if isinstance(x, dict):
        if "text" in x:
            yield x["text"]
        else:
            for y in x.values():
                yield from _walk(y)
    elif isinstance(x, list):
        for y in x:
            yield from _walk(y)
    elif isinstance(x, str):
        yield x
_all = list(_walk(tpl))
for _t in _all:
    _out = _t.format_map(_v)
    check(not _re.search(r"[{}]", _out), "банк: всё подставилось — " + _t[:40])
    for bad in ("vpn", "белые списки", "белых списк", "обязан"):
        check(bad not in _out.lower(), "банк без «" + bad + "»")
_a = set(_walk(tpl["max_zakup"]["cta"]["andrey"])); _i = set(_walk(tpl["max_zakup"]["cta"]["ilya"]))
check(not (_a & _i), "призывы сетей не совпадают")
# повторов подряд нет
_w = creo_bot.Writer(tpl, {})
_heads = [ _w.max_zakup(spb, f, tz).split("\n")[0] for _ in range(4)]
check(len(set(_heads)) >= 3, "заголовки чередуются")

# хук: пишет личку и ленту, ошибки не выпускает
with tempfile.TemporaryDirectory() as d:
    creo_hook.record({"recipient": {"chat_type": "dialog", "chat_id": 5}, "sender": {"user_id": 7, "name": "А"},
                      "body": {"mid": "m1", "text": "закуп питер"}, "timestamp": 1}, {}, d)
    creo_hook.record({"recipient": {"chat_type": "channel", "chat_id": -1}, "body": {"mid": "m2", "text": "Отбой"},
                      "timestamp": 2}, {-1: "max.ru/x"}, d)
    creo_hook.record({"recipient": {"chat_type": "dialog"}, "sender": {"is_bot": True}, "body": {"mid": "m3"}}, {}, d)
    creo_hook.record(None, None, "/нет/такой/папки")  # не падает
    inbox = [json.loads(l) for l in open(os.path.join(d, "creo_inbox.jsonl"), encoding="utf-8")]
    feed = [json.loads(l) for l in open(os.path.join(d, "creo_feed.jsonl"), encoding="utf-8")]
    check(len(inbox) == 1 and inbox[0]["user_id"] == 7 and feed[0]["src"] == "max.ru/x", "хук: личка и лента")

# доступ
row = {"user_id": 999, "chat_id": 1, "text": "закуп питер", "mid": "x"}
check("Ваш ID: 999" in c.answer(row)[0][0], "чужому — ID")
print(f"ВСЁ ПРОЙДЕНО ({ok} проверок)")

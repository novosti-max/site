"""Проверка логики без сети: python -m pytest tests -q  (или python tests/test_bot.py)"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from textproc import clean_text, classify, card_kind, is_ad, matches_filter

MOSCOW = "Раменский МО \nРабота ПВО\n\n❗️Радар Москва - Подписаться\n🇷🇺 Новости Москва - Подписаться"
SAMARA_DANGER = "🔴Самарская область\n❗️Ракетная опасность! Оставайтесь в укрытиях.\n\nРадар Самара - Подписаться"
SAMARA_CLEAR = "🟢Отбой ракетной опасности по Самарской области\n\nРадар Самара - Подписаться"
QUIET = "Пока по обстановке тихо, в случае появления угрозы незамедлительно сообщим"
AIRPORT = "🔻Аэропорт САМАРА: введены временные ограничения на приём и выпуск воздушных судов"
AD1 = "🔥 Лучшие находки с Wildberries — переходи и подписывайся! https://max.ru/wbsniper"
AD2 = "Реклама. ООО «Ромашка». erid: 2VtzqwABC\nКурс по заработку"
AD3 = "Смотрите, что творится в регионе 👇 max.ru/someone_else"
PLUS = "Чувашская Республика\nБеспилотная опасность. Чебоксары, Новочебоксарск"


# Настоящие посты сети (MaxFrame, 28–29.10.2026) и ожидаемый тип
REAL = [
    ("Троицкий МО\nПодольский МО \nДомодедово \nБронницы \nМосковская область\nМеры безопасности! \n\n❗Радар Москва - Подписаться \n🇷🇺 Новости Москва - Подписаться", "danger"),
    ("‼️ Продолжаются фиксации через Калужскую область и далее на Московский регион \n\nПри работе по БПЛА соблюдайте меры безопасности! \nБерегите себя! \n\nМосква и область - Подписаться", "danger"),
    ("✅Обстановка в регионе стабильная!\nВ случае угрозы оповестим немедленно.\n\nЖелаем вам безопасной и мирной ночи 🌙\nС уважением, команда админов🫶\n\n❗️Радар Санкт-Петербург и ЛО - Подписаться", "quiet"),
    ("❗️Владимир Путин заявил, что Россия поддерживает контакт с Илоном Маском по вопросам низкоорбитальных группировок.\n\n❗️Радар Санкт-Петербург и ЛО - Подписаться", "news"),
    ("‼️🇷🇺🇪🇺Европейцев не собираются приглашать за стол переговоров по Украине, - помощник президента России\n\n❗️Радар Санкт-Петербург и ЛО - Подписаться", "news"),
    ("Оренбургская область \nОпасность по БПЛА \n\nРеспублика Башкортостан \nКировская область \nОпасность по БПЛА \n\n❗️Радар ПЛЮС 24/7 - Подписаться", "danger"),
    ("🔻Аэропорт САМАРА (Курумоч)\n\n🚫 ВВЕДЕНЫ временные ограничения на прием и выпуск воздушных судов.\n\n❗️Радар Самара - Подписаться", "topic"),
    ("🚨Новокуйбышевск\nСамарская область \nСирены на заводах\n\n❗️Радар Самара - Подписаться", "danger"),
    ("🔴Самарская область \n\n❗️Радар Самара - Подписаться", "danger"),
    ("🚨Чернава\nЛипецкая область \nПролёт от 2 БПЛА в сторону Елец.\n\n❗️Радар Липецк - Подписаться", "danger"),
    ("⚡ В течение дня дежурными силами ПВО перехвачены и уничтожены 90 украинских беспилотных летательных средств\n\n❗️Липецк 48 | Радар БПЛА - Подписаться", "topic"),
    ("🚨Тверская область \nОпасность по БПЛА еще действует!\nСоблюдаем меры безопасности \n\n❗️Тверь 69 | Радар БПЛА - Подписаться", "danger"),
]


def test_real_posts():
    for text, want in REAL:
        assert not is_ad(text), text
        assert classify(text) == want, (classify(text), text)
        assert "Подписаться" not in clean_text(text)


def test_card_kinds():
    cases = [
        (SAMARA_DANGER, "missile"),                      # «Ракетная опасность!»
        (SAMARA_CLEAR, "missile_clear"),
        (MOSCOW, "danger"),
        ("🟢Отбой опасности БПЛА по Тверской области", "clear"),
        ("Опасность МВШ\nТверская область", "mvsh"),
        ("Отбой опасности МВШ по Липецкой области", "mvsh_clear"),
        ("❗️Внимание! Возможна атака БПЛА на Самарскую область", "attention"),
        ("Внимание! Опасность по БПЛА в Самарской области", "danger"),
        (AIRPORT, None),
    ]
    for text, want in cases:
        got = card_kind(classify(text), text)
        assert got == want, (got, want, text)


def test_clean():
    assert clean_text(MOSCOW) == "Раменский МО\nРабота ПВО"
    assert "Подписаться" not in clean_text(SAMARA_DANGER)
    assert "max.ru" not in clean_text("Текст\nНаш канал в MAX — max.ru/club7174")


def test_classify():
    assert classify(MOSCOW) == "danger"
    assert classify(SAMARA_DANGER) == "danger"
    assert classify(SAMARA_CLEAR) == "clear"
    assert classify(QUIET) == "quiet"
    assert classify(AIRPORT) == "topic"
    assert classify(PLUS) == "danger"


def test_ads():
    for t in (MOSCOW, SAMARA_DANGER, SAMARA_CLEAR, QUIET, AIRPORT, PLUS):
        assert not is_ad(t), t
    for t in (AD1, AD2, AD3):
        assert is_ad(t), t
    assert is_ad(QUIET, has_url_buttons=True)


def test_filter():
    assert matches_filter(PLUS, ["чуваш", "чебоксар"])
    assert not matches_filter(MOSCOW, ["чуваш", "чебоксар"])
    assert matches_filter("Тульская область: работа ПВО", ["тула", "тульск"])
    assert not matches_filter("Статула и Отула", ["тула"])   # не путаем с частью другого слова
    assert matches_filter(MOSCOW, None)


def test_bot_flow():
    """Сухой прогон: ПЛЮС -> Россия; реклама и чужие каналы не проходят; «тихо» лимитируется; карточки из альбомов."""
    import radar_bot, tempfile
    radar_bot.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")
    cfg = radar_bot.load_config()
    for g in cfg["groups"]:
        if g["slug"] == "chuvashiya":       # включим группу с фильтром для проверки
            g["enabled"] = True
            g["gid"] = 1
            g["cards"] = {"danger": "photo-1_1"}
    secrets = {"max_token": "x", "vk": {g["slug"]: "k" for g in cfg["groups"]}}
    b = radar_bot.Bot(cfg, secrets, dry=True)
    posted = []
    b.post = lambda g, text, att=None: posted.append((g["slug"], text, att))
    b.chat_to_source = {1: "max.ru/channel_radar_plus_bpla", 2: "max.ru/club7174"}
    b.max.chat = lambda cid: {3: {"link": "https://max.ru/stranger", "title": "чужой"},
                              5: {"link": "https://max.ru/channel_radar_msk", "title": "Подмосковье"}}.get(cid)

    def msg(chat, text, mid, **extra):
        m = {"recipient": {"chat_id": chat, "chat_type": "channel"}, "body": {"mid": mid, "text": text}}
        m.update(extra)
        return m

    b.handle(msg(1, PLUS, "m1"))
    assert sorted(s for s, _, _ in posted) == ["chuvashiya", "russia"], posted
    assert all(att and att.startswith("photo-") for _, _, att in posted)

    posted.clear(); b.handle(msg(1, PLUS, "m1"))          # дубль
    assert posted == []
    b.handle(msg(2, AD1, "m2")); b.handle(msg(3, SAMARA_DANGER, "m3"))
    assert posted == []                                   # реклама и чужой канал
    b.handle(msg(2, SAMARA_CLEAR, "m4"))                  # отбой ракетной -> карточка missile_clear Самары
    samara = next(g for g in cfg["groups"] if g["slug"] == "samara")
    assert posted[-1][0] == "samara" and posted[-1][2] == samara["cards"]["missile_clear"]
    posted.clear()
    for i in range(4):
        b.handle(msg(2, QUIET, f"q{i}"))
    assert len(posted) == 1                               # «тихо» не чаще лимита
    posted.clear()
    b.handle(msg(2, SAMARA_DANGER, "f1", link={"type": "forward"}))
    assert posted == []                                   # репосты пропускаем
    b.rules["post_news"] = False
    b.handle(msg(2, REAL[3][0], "n1"))
    assert posted == []                                   # при post_news: false политика не идёт
    b.rules["post_news"] = True
    b.handle(msg(2, REAL[3][0], "n2"))
    assert posted[-1][2] is None and "Подписаться" not in posted[-1][1]   # при true — идёт без карточки
    posted.clear()
    b.handle(msg(2, REAL[6][0], "t1"))
    assert posted[-1][0] == "samara" and posted[-1][2] is None   # аэропорт — без карточки

    # канал, которого бот ещё не знал: узнаёт по первому посту и запоминает
    posted.clear()
    b.handle(msg(5, MOSCOW, "p1"))
    assert posted and posted[-1][0] == "podmoskovie", posted
    assert b.state.d["chats"]["5"] == "max.ru/channel_radar_msk" and b.state.d["chats"]["3"] == "max.ru/stranger"

    # группа без ключа не работает
    b2 = radar_bot.Bot(cfg, {"max_token": "x", "vk": {"samara": "k"}}, dry=True)
    assert [g["slug"] for g in b2.groups] == ["samara"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)

"""Разбор поста из MAX: реклама или нет, тип (опасность / отбой / тихо / по теме / посторонняя новость), чистка текста для ВК.

Правки 31.10.2026 (помечены FIX):
FIX-1  «безопасности» не считается «опасностью» (карточка «ОПАСНОСТЬ» на «Приветствуем…», «Аэропорт закрыт… безопасности»).
FIX-2  Чистка не съедает оповещение, стоящее в одной строке с «… - Подписаться»; висящая подпись — только у подписей сети.
FIX-3  region_ok(): в региональную группу не идут посты без своего региона, но с чужой географией (Одесса, Германия…).
FIX-4  card_decision(): карточка только у начала тревоги (опасность / ракеты / МВШ / внимание) и у отбоя; продолжение — без карточки.
FIX-5  carry_source_photos(): картинки-плашки оповещений из MAX в ВК не переносятся.
FIX-6  complete_alert(): «🔴Республика Татарстан» → дописываем «Опасность по БПЛА»; «✅ Регион» — это отбой.
FIX-7  Памятки («🚨План действий при угрозе удара БПЛА», «что делать», «правила поведения») идут без карточки.
FIX-3b Без своего региона не идут и федеральные новости без географии (ВСУ, МИД, перемирие, заявления).
FIX-8  «Опасность сохраняется / продолжается / ещё действует» — продолжение той же серии, без новой карточки.
FIX-5b Фото из MAX: у чистого оповещения (это плашка канала) не переносим, у новостного поста (последствия, итоги,
       длинный текст — то, на что карточка не положена) фото переносим.
FIX-11 is_place_only(): плашку читаем только у поста «одно название места»; короткая новость с фото идёт как новость.
FIX-16 headline(): у новости (пост без карточки) первая строка — заголовок: значок ⚡️, если его нет, и пустая строка после.
FIX-17 region_ok() проверяет текст БЕЗ подписи канала (у Рязани/Саратова подпись «Рязань…/Саратов… - Подписаться»
       пропускала любую федеральную новость); series_key(): в группе «Россия» серия карточек — своя у каждого региона.
FIX-18 region_ok(): новости о налётах на Россию (НПЗ Кстово, «сбито N БПЛА над областями») идут во все группы;
       чужие оповещения, зарубежье/Украина, политика и удары ВС РФ — нет.
FIX-19 region_ok(): политика/зарубежье не проходят и при упоминании своего города, если речь не о налёте.
FIX-20 news_rubric(): зарубежье/Украина, политика и СВО (решение Никиты 01.11) идут в региональные группы с меткой рубрики
       («🌍 МИР |», «🏛 ПОЛИТИКА |», «⚔️ СВО |»), без карточки, с лимитом в день и паузой на время тревоги в регионе.
FIX-21 «Опасность по ударным БПЛА» — подтверждённая опасность (не «Внимание»); рубрика и «чужая тема» решаются по ЗАГОЛОВКУ (01.11 19:20 «Подросток готовил теракт в Царицыно» вышел как «🌍 МИР»).
FIX-10 alert_from_image(): пост MAX «Липецкая область» + картинка-плашка «ОТБОЙ» — плашку читаем (OCR tesseract),
       пишем текст сами («Отбой опасности по БПЛА»), дальше пост идёт как обычное оповещение с НАШЕЙ карточкой.
"""
import re

# FIX-1: «опасност» только как начало слова (не «безопасност»)
OPAS = r"(?<![а-яё])опасност"

# Строки-подписи сети: «❗️Радар Москва - Подписаться», «Москва и область - Подписаться» и т.п.
SUBSCRIBE_LINE = re.compile(r"^.*\bподписаться\b.*$", re.IGNORECASE | re.MULTILINE)
# FIX-2: строку с «Подписаться» удаляем целиком (как раньше), КРОМЕ случая, когда в той же строке перед подписью
# стоит само оповещение: «Отбой опасности по БПЛА | Радар Воронеж - Подписаться» → «Отбой опасности по БПЛА».
_ALERT_WORDS = re.compile(r"((?<![а-яё])опасност|отбой|угроз|тревог|сирен|фиксаци|пролёт|пролет|работ\w* пво)", re.IGNORECASE)
_SIGN_SPLIT = re.compile(r"\s*(?:[|•·]|\s[-–—]\s)\s*(?=[^|•·]*подписаться)", re.IGNORECASE)


def _strip_subscribe(text: str) -> str:
    out = []
    for line in (text or "").split("\n"):
        if re.search(r"\bподписаться\b", line, re.IGNORECASE):
            head = _SIGN_SPLIT.split(line, maxsplit=1)[0] if _SIGN_SPLIT.search(line) else ""
            if head and _ALERT_WORDS.search(head) and not re.search(r"подписаться", head, re.IGNORECASE):
                out.append(head.rstrip(" -–—|"))
            continue
        out.append(line)
    return "\n".join(out)


LINK = re.compile(r"(https?://\S+|\b(?:max\.ru|t\.me|vk\.(?:com|ru)|telegram\.me)/\S+)", re.IGNORECASE)
AD_WORDS = re.compile(
    r"(\bреклам|\berid\b|промокод|скидк|розыгрыш|разыгрыва|переходи|подписывай|"
    r"заработ|казино|букмекер|бонус|купить|закажи|оформи заказ)", re.IGNORECASE)

# Метки контентщиков в начале поста: 🔴 🚨 — тревога, 🟢 — отбой
DANGER_MARK = re.compile(r"^\s*(?:🔴|🚨|⚠️|❗️?\s*🚨)")
CLEAR_MARK = re.compile(r"^\s*🟢")
CHECK_MARK = re.compile(r"^\s*✅")   # FIX-6: «✅ Воронежская область» — отбой, если это не «✅ Обстановка стабильная» (тихо)

CLEAR = re.compile(r"(\bотбой\b|\bотмен[аеыё]|отменен|отменён|снят[аы]? (?:режим|угроз|опасност|ограничени)|угроза миновала)", re.IGNORECASE)
QUIET = re.compile(r"(тихо\b|не наблюда|фиксаций нет|спокойн|стабильн|без происшествий)", re.IGNORECASE)
DANGER = re.compile(rf"({OPAS}|угроз|воздушн\w* тревог|ракетн|\bатак|работ\w* пво|сирен|взрыв|ковёр|ковер|"
                    r"меры безопасности|фиксаци|пролёт|пролет|на подлёте|на подлете|\bкурсом\b)", re.IGNORECASE)
# «Внимание / возможна атака» — предупреждение, ещё не опасность
ATTENTION = re.compile(r"(\bвнимание\b|возможн\w* (?:атак|угроз|опасност|пролёт|пролет|появлен))", re.IGNORECASE)
CONFIRMED = re.compile(rf"((?<!возможна )(?<!возможны ){OPAS}ь (?:по )?(?:[а-яё]+ ){{0,2}}(?:бпла|беспилот|мвш)|ракетная опасность|воздушн\w* тревог|работ\w* пво|сирен|взрыв)", re.IGNORECASE)
# Чем угрожают: ракеты, маловысотные шары или беспилотники
MISSILE = re.compile(r"ракетн", re.IGNORECASE)
MVSH = re.compile(r"(\bмвш\b|маловысотн\w* шар|аэростат|\bшар(?:ы|ов|а|ам|ами|ах)?\b)", re.IGNORECASE)

# Пост по теме радара (без карточки): аэропорты, сбитые БПЛА, итоги ночи
ON_TOPIC = re.compile(r"(бпла|беспилот|пво|аэропорт|ограничени|ракет|дрон|рэб|сбит|перехвач|уничтож)", re.IGNORECASE)

# FIX-2: «висящая подпись» — только подпись сети («❗️Радар ПЛЮС 24/7 -», «Радар Москва -»), а не любая строка на « -»
DANGLING_SIGN = re.compile(r"^\W{0,4}(?:радар|новости|112)[^\n]{0,60}\s[-–—]\s*$", re.MULTILINE | re.IGNORECASE)


def clean_text(text: str) -> str:
    """Убирает подписи «… - Подписаться» и висящие подписи сети, все ссылки, лишние пробелы и пустые строки.
    FIX-2: строка удаляется целиком, только если после удаления подписи в ней ничего не осталось."""
    t = _strip_subscribe(text)
    t = DANGLING_SIGN.sub("", t)
    t = LINK.sub("", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def is_ad(text: str, has_url_buttons: bool = False) -> bool:
    """Реклама: рекламные слова, кнопки-ссылки или ссылки, оставшиеся после удаления подписей сети."""
    if has_url_buttons:
        return True
    body = SUBSCRIBE_LINE.sub("", text or "")
    if AD_WORDS.search(body):
        return True
    return bool(LINK.search(body))


def classify(text: str) -> str:
    """danger / clear / quiet / attention / topic (по теме, без карточки) / news (посторонняя новость — в ВК не идёт)."""
    t = text or ""
    if CLEAR_MARK.search(t) or CLEAR.search(t):
        return "clear"
    if QUIET.search(t) and not DANGER_MARK.search(t):
        return "quiet"
    if CHECK_MARK.search(t) and not DANGER.search(t):
        return "clear"
    if ATTENTION.search(t) and not CONFIRMED.search(t):
        return "attention"
    if DANGER_MARK.search(t) or DANGER.search(t):
        return "danger"
    if ON_TOPIC.search(t):
        return "topic"
    return "news"


def matches_filter(text: str, words) -> bool:
    if not words:
        return True
    low = (text or "").lower()
    return any(re.search(r"(?<![а-яё])" + re.escape(w.lower()), low) for w in words)


# FIX-3: основа названия региона — см. REGION_WORDS ниже
_STOP = {"республика", "область", "области", "город", "край", "аэропорты", "регионы", "вся", "все"}


# FIX-3: слова своего региона для каждой группы. В config.yaml у групп есть только city (без sub),
# поэтому основы задаём явно: «Москва» должна узнавать «Московская область», «Тверь» — «Тверская», Казань — «Татарстан».
REGION_WORDS = {
    "samara": ["самар", "тольятт", "сызран", "новокуйбыш", "чапаевск", "жигулёвск", "жигулевск"],
    "spb": ["петербург", "спб", "ленинград", "ленобласт", "питер", "мурино", "кудрово", "гатчин", "всеволожск", "кронштадт", "выборг"],
    "moskva": ["москв", "моск", "подмосков", "внуково", "шереметьев", "домодедов", "жуковск"],
    "podmoskovie": ["москв", "моск", "подмосков", "внуково", "шереметьев", "домодедов", "жуковск"],
    "saratov": ["саратов", "энгельс", "балаков", "вольск", "балашов"],
    "lipetsk": ["липец", "елец", "ельц", "грязи", "данков", "усмань", "задонск", "чернав"],
    "voronezh": ["воронеж", "лиски", "острогожск", "россош", "борисоглебск", "нововоронеж", "богуча", "семилук"],
    "kazan": ["казан", "татарст", "набережн", "челны", "нижнекамск", "альметьевск", "елабуг", "зеленодольск", "бугульм"],
    "tver": ["твер", "ржев", "кимры", "вышний волоч", "конаково", "торжок", "удомл"],
    "volgograd": ["волгоград", "волжск", "камышин", "михайловк", "урюпинск"],
    "ryazan": ["рязан", "касимов", "скопин", "сасово", "кораблин"],
    "nnovgorod": ["нижегород", "нижний новгород", "нижнего новгород", "нижнем новгород", "дзержинск", "арзамас", "саров", "кстов"],
}
ALL_REGIONS = {"russia"}


def region_stems(g) -> list:
    if g.get("topic_filter") is not None:          # можно задать вручную в config.yaml
        return g["topic_filter"]
    if g.get("filter"):                            # у групп из общего канала уже есть фильтр
        return g["filter"]
    if g.get("slug") in REGION_WORDS:
        return REGION_WORDS[g["slug"]]
    words = re.findall(r"[а-яё]{4,}", (g.get("city", "") + " " + g.get("sub", "")).lower())
    return sorted({w[:4] for w in words if w not in _STOP})


# Признаки «чужой географии» в посте: другие области/края/республики, Украина и её города
GEO = re.compile(r"(област|\bкра[йяюе]\b|краев|республик|украин|киев|одесс|харьков|запорож|херсон|днепр|николаев|"
                 r"львов|полтав|кременчуг|винниц|чернигов|сумск|\bсумы\b|"
                 r"герман|европ|евросоюз|\bсша\b|америк|трамп|зеленск|\bнато\b|франц|британ|польш|абу-даби|пентагон|кремл|алешк|лубин|\bждун)",
                 re.IGNORECASE)


# FIX-3b: признаки федеральной/политической новости — без своего региона в региональную группу не идёт
# (31.10 15:01 «ВСУ ударили по Алешкам и нарушили режим перемирия — МИД РФ» ушло в Воронеж, Тверь, Казань, Самару)
NEWSY = re.compile(r"(\bвсу\b|\bмид\b|перемири|переговор|санкци|заявил|заявлени|\bсми\b|президент|министр|"
                   r"песков|захаров|ударил|нанесл|поражен|минобороны|генштаб|госдум|правительств|путин|ушаков)", re.IGNORECASE)


# FIX-18: что из «чужой» новости можно пускать в региональную группу
FOREIGN = re.compile(r"(украин|киев|одесс|харьков|запорож|херсон|днепр|николаев|львов|полтав|кременчуг|винниц|чернигов|"
                     r"сумск|\bсумы\b|борисп|герман|немц|европ|евросоюз|\bес\b|\bсша\b|америк|трамп|зеленск|\bнато\b|"
                     r"франц|британ|польш|туск|абу-даби|пентагон|алешк|лубин|\bждун)", re.IGNORECASE)
POLITICS = re.compile(r"(\bмид\b|перемири|прекращени\w* огня|переговор|санкци|песков|захаров|госдум|правительств|путин|"
                      r"ушаков|кремл|президент)", re.IGNORECASE)
RF_STRIKES = re.compile(r"(\bвс рф\b|вооруж\w* сил\w* (?:российск|рф)|нанесени\w* удар|удар\w* возмезди)", re.IGNORECASE)
RU_ATTACK = re.compile(r"(бпла|беспилот|дрон|\bатак|ракет|\bпво\b|нпз|нефтеза|сбит|сбили|перехвач|уничтож|теракт|диверси)",
                       re.IGNORECASE)


def _head(text: str) -> str:
    """FIX-21: заголовок поста — первая непустая строка (если она короче 30 знаков, то и вторая)."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return ""
    return lines[0] + (" " + lines[1] if len(lines[0]) < 30 and len(lines) > 1 else "")


def region_ok(text: str, g, kind: str = None) -> bool:
    """Для постов «по теме»: пропускаем, если есть свой регион или в посте вообще нет географии
    (приветствие, благодарность ПВО). Не пропускаем, если упомянута только чужая география (Одесса, Киев…).
    Группа «Россия» (все регионы) — без ограничения."""
    if g.get("slug") in ALL_REGIONS or "все регионы" in (g.get("sub") or "").lower() or (g.get("city") or "") == "Россия":
        return True
    stems = region_stems(g)
    if not stems or matches_filter(text, stems):
        # FIX-19: свой город в политике («пусть приезжает в Москву — Ушаков», 01.11 Москва №1709, Подмосковье №3828)
        # не делает её новостью радара: политика/зарубежье проходят, только если речь о налёте/БПЛА
        t0 = text or ""
        h0 = _head(t0)   # FIX-21: решает заголовок, а не упоминание Украины где-то в тексте
        if FOREIGN.search(h0) or POLITICS.search(h0) or RF_STRIKES.search(h0):
            return bool(RU_ATTACK.search(t0))
        return True
    # в боевом config post_news: true, поэтому политика (kind=news) проверяется так же строго
    t = text or ""
    # FIX-18 (решение Никиты 01.11): новости о налётах на Россию — общее новостное поле, идут во все группы
    # («атака БПЛА на НПЗ в Кстово», «за ночь сбито 235 БПЛА над областями»). Но не зарубежье/Украина, не политика,
    # не удары ВС РФ и не чужие оповещения (у них карточка и они про другой регион).
    h = _head(t)     # FIX-21: «Подросток готовил теракт в Царицыно» с «украинскими кураторами» в тексте — не «МИР»
    if FOREIGN.search(h) or POLITICS.search(h) or RF_STRIKES.search(h):
        return False
    if not (GEO.search(t) or NEWSY.search(t)):
        return True                                   # приветствие, благодарность ПВО, «тихо» — без географии
    if kind not in (None, "news", "topic", "quiet") and card_kind(kind, t) is not None:
        return False                                  # оповещение другого региона
    return bool(RU_ATTACK.search(t))


# FIX-6: оповещение, где смысл только в значке и картинке MAX («🔴Республика Татарстан», «🟢Республика Татарстан»,
# «✅ Воронежская область»): во ВК значок мелкий, а картинку MAX бот не переносит — текст выглядит пустым.
# Дописываем словами, что это за оповещение.
ALERT_LINE = {"danger": "Опасность по БПЛА", "clear": "Отбой опасности по БПЛА", "attention": "Внимание! Возможна опасность по БПЛА",
              "missile": "Ракетная опасность", "missile_clear": "Отбой ракетной опасности",
              "mvsh": "Опасность: маловысотные шары", "mvsh_clear": "Отбой опасности по шарам"}


def complete_alert(kind: str, clean: str, text: str) -> str:
    if kind not in ("danger", "clear", "attention"):
        return clean
    if NEWS_REPORT.search(clean) or len(clean) > 200:   # новость о налёте — не дописываем (01.11 08:11 Кстово)
        return clean
    if not is_place_only(clean):   # FIX-11: дописываем только к «🔴Регион» / «✅ Регион», не к новости («Последствия атаки…»)
        return clean
    words = re.sub(r"[^\w\s]", " ", clean)
    if (CLEAR.search(words) if kind == "clear" else (ALERT.search(words) or ATTENTION.search(words))):
        return clean
    th = threat(text)
    key = kind if th == "bpla" or kind == "attention" else (th if kind == "danger" else th + "_clear")
    return f"{clean}\n{ALERT_LINE[key]}"


def threat(text: str) -> str:
    """missile / mvsh / bpla — от этого зависит карточка."""
    t = text or ""
    if MISSILE.search(t):
        return "missile"
    if MVSH.search(t):
        return "mvsh"
    return "bpla"


# Новость о последствиях (погибшие, пострадавшие, заявления) — без карточки «Опасность», даже если в ней слово «атака»
AFTERMATH = re.compile(r"(последстви|погиб|пострадал|ранен|соболезн|скончал|госпитализ|губернатор\s+\S+\s+сообщил|трагеди)", re.IGNORECASE)
ALERT = re.compile(rf"({OPAS}|угроз|тревог|сирен|меры безопасности|фиксаци|пролёт|пролет|на подлёте|на подлете|работ\w* пво|отбой)", re.IGNORECASE)


# FIX-9: новостное сообщение о налёте («ВСУ атаковали Кстово… целью атаки мог стать НПЗ», «по словам губернатора»)
# — это новость, а не оповещение: без карточки (01.11 08:11 Нижний Новгород вышел с карточкой)
NEWS_REPORT = re.compile(r"(\bвсу\b|по словам|по данным|целью|\bатаковал|сообщил|заявил|губернатор|мэр\b|глава региона|"
                         r"минобороны|пострадавш|повреждени|обломк)", re.IGNORECASE)


# FIX-7: памятки и инструкции — не оповещение, карточка «Опасность» на них не нужна
MEMO = re.compile(r"(план действий|памятк|что делать|как действовать|правила поведени|рекомендаци\w* (?:при|для)|алгоритм действий)", re.IGNORECASE)


def card_kind(kind: str, text: str):
    """Какую карточку приложить: None — пост без карточки.
    Карточка только у коротких постов-оповещений; длинная новость о последствиях идёт без карточки."""
    if kind == "attention":
        return "attention"
    if kind not in ("danger", "clear"):
        return None
    body = clean_text(text)
    if MEMO.search(body) or NEWS_REPORT.search(body):
        return None
    if AFTERMATH.search(body) or (len(body) > 450 and not DANGER_MARK.search(text or "")) or not (ALERT.search(body) or DANGER_MARK.search(text or "") or CLEAR_MARK.search(text or "") or CHECK_MARK.search(text or "")):
        return None
    th = threat(text)
    if th == "bpla":
        return kind
    return th if kind == "danger" else th + "_clear"


# FIX-4: когда вешать карточку (правило Никиты, 31.10): карточка только у НАЧАЛА тревоги.
#   • «Опасность БПЛА» — карточка у первого поста; дальше, пока не было отбоя, посты идут без карточки
#     (раньше через 45 мин карточка ставилась снова: Москва 13:39 «сохраняется» и 15:11 «Работа ПВО» — обе с карточкой).
#   • Ракетная опасность, МВШ, «Внимание» — свои карточки, тоже только при начале (смене вида).
#   • Отбой — одна зелёная карточка после тревоги; повторные «отбой» без карточки.
#   • Если оповещений не было series_reset_hours (по умолчанию 6 ч), а отбоя так и не было — следующая тревога считается новой.
# FIX-8: «сохраняется / продолжается» — никогда не начало серии
CONTINUE = re.compile(r"(сохраня|продолжа|ещ[её] действ|по-прежнему|оста[её]тся в силе|не отменен|не отменён)", re.IGNORECASE)


def card_decision(group_state: dict, ck, now: float, rules: dict, text: str = "") -> bool:
    if not ck:
        return False
    last = group_state.get("_last")
    seen = float(group_state.get("_seen", float("-inf")))      # время последнего оповещения в группе
    reset = float(rules.get("series_reset_hours", 6)) * 3600
    new_series = (now - seen) >= reset
    group_state["_seen"] = now
    if CONTINUE.search(text or "") and not new_series:
        # FIX-23: «…сохраняется» серию не меняет. 02.11 Воронеж: «✅Отмена непосредственной опасности… Режим опасности
        # сохраняется» ставил серии вид «отбой», и настоящий «Отбой опасности по БПЛА» в 05:59 вышел без зелёной карточки.
        return False
    else:
        ok = new_series or ck != last
    group_state["_last"] = ck
    if ok:
        group_state["_t"] = now
    return ok


# FIX-5: фото из поста MAX у оповещений (опасность / отбой / внимание) — это картинки-плашки самого канала
# («ОПАСНОСТЬ ПО БПЛА»). Раньше, если нашу карточку не вешали, бот переносил их — визуально те же «карточки подряд».
def carry_source_photos(kind: str, rules: dict, text: str = "") -> bool:
    if kind not in ("danger", "clear", "attention") or rules.get("alert_source_photos", False):
        return True
    # FIX-5b: чистое оповещение (на него положена наша карточка) — картинка в MAX это плашка, не переносим;
    # новостной пост тревожного типа (последствия, итоги ночи, памятка, длинный текст) — фото настоящее, переносим.
    return card_kind(kind, text) is None


# FIX-11: пост MAX — только название места(-мест), без самой новости («Липецкая область», «Кстово / Нижегородская область»).
# Смысл такого поста — в картинке. Любой другой короткий пост с фото — это новость, она идёт как обычно.
_GEO_WORDS = {"область", "области", "обл", "край", "края", "республика", "республики", "район", "районы", "района",
              "округ", "го", "мо", "ао", "город", "г", "и", "близлежащие", "близлежащих", "окрестности", "городской",
              "муниципальный", "по", "в", "на", "вся", "все", "всей"}


def is_place_only(clean: str) -> bool:
    lines = [l for l in (clean or "").splitlines() if l.strip()]
    if not lines or len(lines) > 6:
        return False
    for line in lines:
        if re.search(r"[!?:;…]|\.\s*\S", line.strip().rstrip(".")):
            return False
        words = re.findall(r"[A-Za-zА-Яа-яЁё][\w\-ёЁ]*", line)
        if not words or len(words) > 5:
            return False
        for w in words:
            if w.lower() in _GEO_WORDS or w[0].isupper():
                continue
            return False
    return True


# FIX-17: регион оповещения — для группы «Россия», где идут оповещения всех регионов: серия карточек у каждого региона своя
# (01.11 14:26 «🔴Северная часть Ростовской области / Опасность по БПЛА» вышла без карточки, потому что утром была серия
# по Смоленской области).
_REGION_RE = re.compile(r"([а-яё]{4,}?)(?:ская|ской|ский|кая|кой|кий)\s+(?:област|кра[йяе]|округ|автономн)|"
                        r"республик[аиеу]\s+([а-яё\-]{4,})", re.IGNORECASE)


def series_key(text: str):
    m = _REGION_RE.search((text or "").replace("ё", "е"))
    if not m:
        return None
    return (m.group(1) or m.group(2)).lower()[:6]


# FIX-20: рубрики для «чужих» новостей в региональных группах
SVO = re.compile(r"(\bвсу\b|\bвс рф\b|вооруж\w* сил|удар|обстрел|фронт|\bсво\b|наступлени|освобожд\w* (?:сел|пос|город))",
                 re.IGNORECASE)
RUBRIC = {"svo": "⚔️ СВО", "politics": "🏛 ПОЛИТИКА", "world": "🌍 МИР"}


def news_rubric(text: str):
    """Рубрика новости, которая не про свой регион: svo / politics / world или None (тогда в группу не идёт).
    FIX-21: по заголовку — упоминание Украины в середине российской новости не делает её «МИРОМ»."""
    t = _head(text)
    if RF_STRIKES.search(t) or ((FOREIGN.search(t) or POLITICS.search(t)) and SVO.search(t)):
        return "svo"
    if POLITICS.search(t):
        return "politics"
    if FOREIGN.search(t):
        return "world"
    return None


def with_rubric(text: str, rubric: str) -> str:
    """«❗️Путин никогда…» -> «🏛 ПОЛИТИКА | Путин никогда…» (значки в начале первой строки убираем)."""
    lines = (text or "").split("\n")
    lines[0] = f"{RUBRIC[rubric]} | " + re.sub(r"^[^\w«\"(]+", "", lines[0]).strip()
    return "\n".join(lines)


# FIX-16: заголовок новости. Жирный шрифт ВК боту не даёт (и «жирных» русских букв в Юникоде нет), поэтому выделяем
# первую строку значком и отделяем пустой строкой. Только если после заголовка есть текст.
def headline(text: str) -> str:
    lines = (text or "").strip("\n").split("\n")
    first = lines[0].strip()
    rest = "\n".join(lines[1:]).strip("\n")
    if not first or not rest.strip() or len(first) > 150 or is_place_only(first):
        return text
    if re.match(r"[\w«\"(]", first) and not re.search("[\u2600-\u27bf\U0001F000-\U0001FAFF]", first):
        first = "⚡️ " + first                     # значка в строке нет — ставим
    return first + ("\n" if first.endswith(":") else "\n\n") + rest   # «…над территориями:» — список сразу


# FIX-10: распознавание картинки-плашки MAX («ОПАСНОСТЬ ПО БПЛА», «ОТБОЙ», «РАКЕТНАЯ ОПАСНОСТЬ», «ВНИМАНИЕ»).
# Способ — OCR (tesseract, rus). Если tesseract на сервере нет, по цвету плашки — только при rules.image_color_fallback
# (красный не отличает ракеты от БПЛА, поэтому по умолчанию выключено). Не уверены — None: пост пропускается, как раньше.
IMAGE_LINE = {"danger": "Опасность по БПЛА", "clear": "Отбой опасности по БПЛА",
              "missile": "Ракетная опасность", "missile_clear": "Отбой ракетной опасности",
              "mvsh": "Опасность: маловысотные шары", "mvsh_clear": "Отбой опасности: маловысотные шары",
              "attention": "Внимание! Возможна атака БПЛА"}


def _ocr_variants(data: bytes):
    """Картинка → набор PNG для OCR: увеличенная (мелкие плашки MAX ~200 px), ч/б по светлому тексту и по тёмному."""
    try:
        from PIL import Image, ImageOps
        import io
        im = Image.open(io.BytesIO(data)).convert("RGB")
        if im.width < 900:
            k = max(2, min(5, 900 // max(im.width, 1)))
            im = im.resize((im.width * k, im.height * k), Image.LANCZOS)
        g = im.convert("L")
        out = []
        for img in (g.point(lambda p: 255 if p > 170 else 0), g.point(lambda p: 255 if p > 110 else 0), g):
            b = io.BytesIO()
            img.save(b, "PNG")
            out.append(b.getvalue())
        return out
    except Exception:
        return [data]


def ocr_text(data: bytes, timeout: int = 20) -> str:
    import shutil, subprocess
    if not shutil.which("tesseract"):
        return None
    words = []
    for v in _ocr_variants(data):
        try:
            r = subprocess.run(["tesseract", "stdin", "stdout", "-l", "rus+eng", "--psm", "6"],
                               input=v, capture_output=True, timeout=timeout)
            words.append(r.stdout.decode("utf-8", "ignore"))
        except Exception:
            continue
        if _ocr_kind(" ".join(words)):
            break
    return " ".join(words)


def _ocr_kind(t: str):
    t = re.sub(r"\s+", " ", (t or "").lower().replace("ё", "е"))
    if not t.strip():
        return None
    if re.search(r"(тихо|штатн|стабильн|чистое небо)", t):
        return None
    clear = re.search(r"(отбо|отмен)", t)
    missile = re.search(r"ракет", t)
    mvsh = re.search(r"(мвш|шар|аэростат)", t)
    if clear:
        return "missile_clear" if missile else ("mvsh_clear" if mvsh else "clear")
    if missile:
        return "missile"
    if mvsh and re.search(r"(опасн|угроз)", t):
        return "mvsh"
    if re.search(r"(вниман|возможн)", t) and not re.search(r"(?<![а-я])опасност", t):
        return "attention"
    if re.search(r"((?<![а-я])опасн|угроз|тревог|бпла)", t):
        return "danger"
    return None


def _color_kind(data: bytes):
    """Запасной способ без OCR: плашка — это крупная однотонная заливка. Красная → опасность, зелёная → отбой."""
    try:
        from PIL import Image
        import io, colorsys
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((120, 120))
        px = list(im.getdata())
        red = green = 0
        for r, g, b in px:
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s < 0.45 or v < 0.35:
                continue
            if h < 0.03 or h > 0.95:
                red += 1
            elif 0.25 < h < 0.45:
                green += 1
        n = len(px) or 1
        if red / n > 0.45 and green / n < 0.05:
            return "danger"
        if green / n > 0.45 and red / n < 0.05:
            return "clear"
    except Exception:
        pass
    return None


def alert_from_image(data: bytes, color_fallback: bool = False):
    """→ (вид, строка текста, способ) или None. Вид — ключ карточки: danger / clear / missile / … / attention."""
    if not data:
        return None
    t = ocr_text(data)
    if t is not None:
        k = _ocr_kind(t)
        return (k, IMAGE_LINE[k], "OCR") if k else None
    k = _color_kind(data) if color_fallback else None
    return (k, IMAGE_LINE[k], "цвет") if k else None

"""Разбор поста из MAX: реклама или нет, тип (опасность / отбой / тихо / по теме / посторонняя новость), чистка текста для ВК.

Правки 31.10.2026 (помечены FIX):
FIX-1  «безопасности» не считается «опасностью» (карточка «ОПАСНОСТЬ» на «Приветствуем…», «Аэропорт закрыт… безопасности»).
FIX-2  Чистка не съедает оповещение, стоящее в одной строке с «… - Подписаться»; висящая подпись — только у подписей сети.
FIX-3  region_ok(): в региональную группу не идут посты без своего региона, но с чужой географией (Одесса, Германия…).
FIX-4  card_decision(): одна серия оповещений — одна карточка (раньше таймер был у каждого вида карточки).
FIX-5  carry_source_photos(): картинки-плашки оповещений из MAX в ВК не переносятся.
FIX-6  complete_alert(): «🔴Республика Татарстан» → дописываем «Опасность по БПЛА»; «✅ Регион» — это отбой.
FIX-7  Памятки («🚨План действий при угрозе удара БПЛА», «что делать», «правила поведения») идут без карточки.
FIX-3b Без своего региона не идут и федеральные новости без географии (ВСУ, МИД, перемирие, заявления).
FIX-8  «Опасность сохраняется / продолжается / ещё действует» — продолжение той же серии, без новой карточки.
FIX-5b Фото из MAX: у чистого оповещения (это плашка канала) не переносим, у новостного поста (последствия, итоги,
       длинный текст — то, на что карточка не положена) фото переносим.
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

CLEAR = re.compile(r"(\bотбой\b|отменен|отменён|снят[аы]? (?:режим|угроз|опасност|ограничени)|угроза миновала)", re.IGNORECASE)
QUIET = re.compile(r"(тихо\b|не наблюда|фиксаций нет|спокойн|стабильн|без происшествий)", re.IGNORECASE)
DANGER = re.compile(rf"({OPAS}|угроз|воздушн\w* тревог|ракетн|\bатак|работ\w* пво|сирен|взрыв|ковёр|ковер|"
                    r"меры безопасности|фиксаци|пролёт|пролет|на подлёте|на подлете|\bкурсом\b)", re.IGNORECASE)
# «Внимание / возможна атака» — предупреждение, ещё не опасность
ATTENTION = re.compile(r"(\bвнимание\b|возможн\w* (?:атак|угроз|опасност|пролёт|пролет|появлен))", re.IGNORECASE)
CONFIRMED = re.compile(rf"({OPAS}ь (?:по )?(?:бпла|беспилот|мвш)|ракетная опасность|воздушн\w* тревог|работ\w* пво|сирен|взрыв)", re.IGNORECASE)
# Чем угрожают: ракеты, маловысотные шары или беспилотники
MISSILE = re.compile(r"ракетн", re.IGNORECASE)
MVSH = re.compile(r"(\bмвш\b|маловысотн\w* шар|аэростат|\bшар(?:ы|ов|а)?\b)", re.IGNORECASE)

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


# FIX-3: основа названия региона из city/sub группы: «Воронеж» + «Воронежская область» → ["ворон"]
_STOP = {"республика", "область", "области", "город", "край", "аэропорты", "регионы", "вся", "все"}


def region_stems(g) -> list:
    if g.get("topic_filter") is not None:          # можно задать вручную в config.yaml
        return g["topic_filter"]
    if g.get("filter"):                            # у групп из общего канала уже есть фильтр
        return g["filter"]
    words = re.findall(r"[а-яё]{5,}", (g.get("city", "") + " " + g.get("sub", "")).lower())
    stems = sorted({w[:5] for w in words if w not in _STOP})
    return stems


# Признаки «чужой географии» в посте: другие области/края/республики, Украина и её города
GEO = re.compile(r"(област|\bкра[йяюе]\b|краев|республик|украин|киев|одесс|харьков|запорож|херсон|днепр|николаев|"
                 r"львов|полтав|кременчуг|винниц|чернигов|сумск|\bсумы\b|"
                 r"герман|европ|евросоюз|\bсша\b|америк|трамп|зеленск|\bнато\b|франц|британ|польш|абу-даби|пентагон|кремл)",
                 re.IGNORECASE)


# FIX-3b: признаки федеральной/политической новости — без своего региона в региональную группу не идёт
# (31.10 15:01 «ВСУ ударили по Алешкам и нарушили режим перемирия — МИД РФ» ушло в Воронеж, Тверь, Казань, Самару)
NEWSY = re.compile(r"(\bвсу\b|\bмид\b|перемири|переговор|санкци|заявил|заявлени|\bсми\b|президент|министр|"
                   r"песков|захаров|ударил|нанесл|поражен|минобороны|генштаб|госдум|правительств)", re.IGNORECASE)


def region_ok(text: str, g, kind: str = None) -> bool:
    """Для постов «по теме»: пропускаем, если есть свой регион или в посте вообще нет географии
    (приветствие, благодарность ПВО). Не пропускаем, если упомянута только чужая география (Одесса, Киев…).
    Группа «Россия» (все регионы) — без ограничения."""
    if "все регионы" in (g.get("sub") or "").lower():
        return True
    stems = region_stems(g)
    if not stems or matches_filter(text, stems):
        return True
    if kind == "news":                   # обычные новости идут только при post_news: true — там решает настройка
        return not GEO.search(text or "")
    return not (GEO.search(text or "") or NEWSY.search(text or ""))


# FIX-6: оповещение, где смысл только в значке и картинке MAX («🔴Республика Татарстан», «🟢Республика Татарстан»,
# «✅ Воронежская область»): во ВК значок мелкий, а картинку MAX бот не переносит — текст выглядит пустым.
# Дописываем словами, что это за оповещение.
ALERT_LINE = {"danger": "Опасность по БПЛА", "clear": "Отбой опасности по БПЛА", "attention": "Внимание! Возможна опасность по БПЛА",
              "missile": "Ракетная опасность", "missile_clear": "Отбой ракетной опасности",
              "mvsh": "Опасность: маловысотные шары", "mvsh_clear": "Отбой опасности по шарам"}


def complete_alert(kind: str, clean: str, text: str) -> str:
    if kind not in ("danger", "clear", "attention"):
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
AFTERMATH = re.compile(r"(погиб|пострадал|ранен|соболезн|скончал|госпитализ|губернатор\s+\S+\s+сообщил|трагеди)", re.IGNORECASE)
ALERT = re.compile(rf"({OPAS}|угроз|тревог|сирен|меры безопасности|фиксаци|пролёт|пролет|на подлёте|на подлете|работ\w* пво|отбой)", re.IGNORECASE)


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
    if MEMO.search(body):
        return None
    if AFTERMATH.search(body) or (len(body) > 450 and not DANGER_MARK.search(text or "")) or not (ALERT.search(body) or DANGER_MARK.search(text or "") or CLEAR_MARK.search(text or "") or CHECK_MARK.search(text or "")):
        return None
    th = threat(text)
    if th == "bpla":
        return kind
    return th if kind == "danger" else th + "_clear"


# FIX-4: когда вешать карточку. Было: свой 45-минутный таймер у каждого вида карточки (danger / clear / attention /
# missile / mvsh …), поэтому в серии «внимание → опасность → опасность (аэропорт) → отбой» карточки шли подряд.
# Стало: одна запись на группу — карточка, только если
#   (а) вид оповещения сменился (опасность → отбой и т.п.) и с прошлой карточки прошло не меньше card_min_interval_minutes, или
#   (б) с прошлой карточки прошло больше card_gap_minutes (новая серия).
# FIX-8: продолжение уже идущей тревоги — не новая серия
CONTINUE = re.compile(r"(сохраня|продолжа|ещ[её] действ|по-прежнему|оста[её]тся в силе|не отменен|не отменён)", re.IGNORECASE)


def card_decision(group_state: dict, ck, now: float, rules: dict, text: str = "") -> bool:
    if not ck:
        return False
    last, last_t = group_state.get("_last"), float(group_state.get("_t", float("-inf")))
    if ck == last and CONTINUE.search(text or ""):
        return False                     # «Опасность сохраняется» после «Опасность» — без новой карточки
    gap = float(rules.get("card_gap_minutes", 45)) * 60
    min_iv = float(rules.get("card_min_interval_minutes", 15)) * 60
    since = now - last_t
    ok = since >= gap or (ck != last and since >= min_iv)
    group_state["_last"] = ck            # вид серии запоминаем и без карточки, чтобы её не «догнала» карточка позже
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

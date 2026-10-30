"""Разбор поста из MAX: реклама или нет, тип (опасность / отбой / тихо / по теме / посторонняя новость), чистка текста для ВК."""
import re

# Строки-подписи сети: «❗️Радар Москва - Подписаться», «Москва и область - Подписаться» и т.п.
SUBSCRIBE_LINE = re.compile(r"^.*\bподписаться\b.*$", re.IGNORECASE | re.MULTILINE)
LINK = re.compile(r"(https?://\S+|\b(?:max\.ru|t\.me|vk\.(?:com|ru)|telegram\.me)/\S+)", re.IGNORECASE)
AD_WORDS = re.compile(
    r"(\bреклам|\berid\b|промокод|скидк|розыгрыш|разыгрыва|переходи|подписывай|"
    r"заработ|казино|букмекер|бонус|купить|закажи|оформи заказ)", re.IGNORECASE)

# Метки контентщиков в начале поста: 🔴 🚨 — тревога, 🟢 — отбой
DANGER_MARK = re.compile(r"^\s*(?:🔴|🚨|⚠️|❗️?\s*🚨)")
CLEAR_MARK = re.compile(r"^\s*🟢")

CLEAR = re.compile(r"(\bотбой\b|отменен|отменён|снят[аы]? (?:режим|угроз|опасност|ограничени)|угроза миновала)", re.IGNORECASE)
QUIET = re.compile(r"(тихо\b|не наблюда|фиксаций нет|спокойн|стабильн|без происшествий)", re.IGNORECASE)
DANGER = re.compile(r"(опасност|угроз|воздушн\w* тревог|ракетн|\bатак|работ\w* пво|сирен|взрыв|ковёр|ковер|"
                    r"меры безопасности|фиксаци|пролёт|пролет|на подлёте|на подлете|\bкурсом\b)", re.IGNORECASE)
# «Внимание / возможна атака» — предупреждение, ещё не опасность
ATTENTION = re.compile(r"(\bвнимание\b|возможн\w* (?:атак|угроз|опасност|пролёт|пролет|появлен))", re.IGNORECASE)
CONFIRMED = re.compile(r"(опасность (?:по )?(?:бпла|беспилот|мвш)|ракетная опасность|воздушн\w* тревог|работ\w* пво|сирен|взрыв)", re.IGNORECASE)
# Чем угрожают: ракеты, маловысотные шары или беспилотники
MISSILE = re.compile(r"ракетн", re.IGNORECASE)
MVSH = re.compile(r"(\bмвш\b|маловысотн\w* шар|аэростат|\bшар(?:ы|ов|а)?\b)", re.IGNORECASE)

# Пост по теме радара (без карточки): аэропорты, сбитые БПЛА, итоги ночи
ON_TOPIC = re.compile(r"(бпла|беспилот|пво|аэропорт|ограничени|ракет|дрон|рэб|сбит|перехвач|уничтож)", re.IGNORECASE)


def clean_text(text: str) -> str:
    """Убирает строки «Подписаться», все ссылки, лишние пробелы и пустые строки."""
    t = SUBSCRIBE_LINE.sub("", text or "")
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


def threat(text: str) -> str:
    """missile / mvsh / bpla — от этого зависит карточка."""
    t = text or ""
    if MISSILE.search(t):
        return "missile"
    if MVSH.search(t):
        return "mvsh"
    return "bpla"


def card_kind(kind: str, text: str):
    """Какую карточку приложить: None — пост без карточки."""
    if kind == "attention":
        return "attention"
    if kind not in ("danger", "clear"):
        return None
    th = threat(text)
    if th == "bpla":
        return kind
    return th if kind == "danger" else th + "_clear"

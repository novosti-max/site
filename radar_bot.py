"""Бот-переносчик: новые посты из MAX-каналов сети -> группы ВКонтакте.

Ключи (токен MAX-бота и ключи групп ВК) лежат только на сервере в /etc/radar-bot/secrets.json,
их вводят на странице настройки (setup_server.py). В репозитории ключей нет.

Запуск:            python3 radar_bot.py
Пробный режим:     python3 radar_bot.py --dry-run      (ничего не публикует, только пишет в лог)
Список каналов:    python3 radar_bot.py --list-chats   (какие каналы видит MAX-бот)
"""
import argparse, datetime as dt, json, logging, logging.handlers, os, sys, time
import requests, yaml
from zoneinfo import ZoneInfo

from textproc import clean_text, classify, card_kind, is_ad, matches_filter

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS_FILE = os.environ.get("RADAR_SECRETS", "/etc/radar-bot/secrets.json")
STATE_FILE = os.environ.get("RADAR_STATE", "/var/lib/radar-bot/state.json")
LOG_FILE = os.path.join(os.path.dirname(STATE_FILE), "bot.log")
MAX_API = "https://platform-api.max.ru"
VK_API = "https://api.vk.ru/method/"
VK_V = "5.199"

log = logging.getLogger("radar")


def norm_link(s: str) -> str:
    s = (s or "").strip().lower()
    for p in ("https://", "http://", "www."):
        if s.startswith(p):
            s = s[len(p):]
    return s.rstrip("/")


def load_secrets():
    try:
        return json.load(open(SECRETS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def load_config():
    return yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8"))


# ---------------------------------------------------------------- состояние
class State:
    def __init__(self):
        try:
            self.d = json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            self.d = {}
        self.d.setdefault("marker", None)
        self.d.setdefault("quiet", {})      # slug -> [timestamps]
        self.d.setdefault("seen", [])       # последние id сообщений MAX, чтобы не дублировать
        self.d.setdefault("promo", {})      # slug -> "2026-10-29"
        self.d.setdefault("cards", {})      # slug -> {вид карточки: время} — чтобы не вешать картинку на каждый пост серии
        self.d.setdefault("chats", {})      # chat_id (строкой) -> ссылка канала; узнаём из событий MAX

    def save(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        json.dump(self.d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- ВКонтакте (ключи сообществ)
def vk_call(method, token, **params):
    for attempt in range(4):
        params.update(access_token=token, v=VK_V)
        r = requests.post(VK_API + method, data=params, timeout=30).json()
        if "error" not in r:
            return r["response"]
        code = r["error"].get("error_code")
        if code in (6, 9, 10):                  # слишком часто / временная ошибка
            time.sleep(1 + attempt)
            continue
        raise RuntimeError(f"VK {method}: {r['error'].get('error_msg')} ({code})")
    raise RuntimeError(f"VK {method}: не получилось после повторов")


# ---------------------------------------------------------------- MAX
class MAX:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers["Authorization"] = token

    def me(self):
        r = self.s.get(MAX_API + "/me", timeout=20)
        r.raise_for_status()
        return r.json()

    def chats(self):
        out, marker = [], None
        while True:
            params = {"count": 100}
            if marker:
                params["marker"] = marker
            r = self.s.get(MAX_API + "/chats", params=params, timeout=30)
            r.raise_for_status()
            r = r.json()
            out += r.get("chats", [])
            marker = r.get("marker")
            if not marker:
                return out

    def chat(self, chat_id):
        r = self.s.get(f"{MAX_API}/chats/{chat_id}", timeout=20)
        return r.json() if r.status_code == 200 else None

    def updates(self, marker):
        params = {"timeout": 30, "limit": 100, "types": "message_created,bot_added"}
        if marker is not None:
            params["marker"] = marker
        r = self.s.get(MAX_API + "/updates", params=params, timeout=45)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------- логика
class Bot:
    def __init__(self, cfg, secrets, dry):
        self.cfg, self.dry = cfg, dry
        self.rules = cfg.get("rules", {})
        self.tz = ZoneInfo(cfg.get("timezone", "Europe/Moscow"))
        self.state = State()
        self.apply_secrets(secrets)
        self.chat_to_source = {}   # chat_id -> нормализованная ссылка канала

    def apply_secrets(self, secrets):
        self.secrets = secrets
        self.max = MAX(secrets.get("max_token", ""))
        vk = secrets.get("vk", {})
        # работают только включённые группы, для которых введён ключ
        self.groups = [g for g in self.cfg["groups"] if g.get("enabled") and vk.get(g["slug"])]
        off = [g["slug"] for g in self.cfg["groups"] if g.get("enabled") and not vk.get(g["slug"])]
        if off:
            log.warning("нет ключа ВК для: %s — эти группы пропускаю", ", ".join(off))

    def token(self, g):
        return self.secrets["vk"][g["slug"]]

    # --- подготовка
    def resolve_chats(self):
        """Списка каналов у MAX API нет: chat_id канала узнаём из событий (бот добавлен / новый пост)
        и запоминаем. Здесь только подхватываем уже известные."""
        self.chat_to_source = {int(k): v for k, v in self.state.d["chats"].items() if v}
        wanted = {norm_link(g["source"]) for g in self.groups}
        missing = wanted - set(self.chat_to_source.values())
        if missing:
            log.info("каналы, где бот ещё не видел постов (появятся с первым постом): %s", ", ".join(sorted(missing)))

    def learn_chat(self, chat_id):
        """Неизвестный chat_id — спрашиваем у MAX ссылку канала и запоминаем."""
        if chat_id is None or str(chat_id) in self.state.d["chats"]:
            return self.chat_to_source.get(chat_id)
        link = ""
        try:
            c = self.max.chat(chat_id) or {}
            link = norm_link(c.get("link") or "")
            log.info("новый канал: %s (%s)", c.get("title"), link or "без ссылки")
        except Exception as e:
            log.warning("не узнал канал %s: %s", chat_id, e)
            return None
        self.state.d["chats"][str(chat_id)] = link
        self.state.save()
        if link:
            self.chat_to_source[chat_id] = link
        return link or None

    # --- публикация
    def post(self, g, text, attachment=None):
        if self.dry:
            log.info("[ПРОБНО] %s <- %s%s", g["vk"], text.replace("\n", " / ")[:120], f"  +{attachment}" if attachment else "")
            return
        params = {"owner_id": -int(g["gid"]), "from_group": 1, "message": text}
        if attachment:
            params["attachments"] = attachment
        res = vk_call("wall.post", self.token(g), **params)
        log.info("опубликовано в %s (пост %s)%s: %s", g["vk"], res.get("post_id"), " с карточкой" if attachment else "",
                 text.replace("\n", " / ")[:90])

    def quiet_allowed(self, slug):
        now = time.time()
        per_day = int(self.rules.get("quiet_per_day", 2))
        gap = float(self.rules.get("quiet_min_gap_hours", 6)) * 3600
        stamps = [t for t in self.state.d["quiet"].get(slug, []) if now - t < 86400]
        if len(stamps) >= per_day or (stamps and now - max(stamps) < gap):
            return False
        self.state.d["quiet"][slug] = stamps + [now]
        return True

    def handle(self, msg):
        rec, body = msg.get("recipient", {}), msg.get("body", {}) or {}
        if rec.get("chat_type") != "channel":
            return
        mid = body.get("mid")
        if mid and mid in self.state.d["seen"]:
            return
        source = self.chat_to_source.get(rec.get("chat_id")) or self.learn_chat(rec.get("chat_id"))
        targets = [g for g in self.groups if source and norm_link(g["source"]) == source]
        if not targets:
            return
        text = body.get("text") or ""
        atts = body.get("attachments") or []
        url_buttons = any(a.get("type") == "inline_keyboard" and any(
            b.get("type") == "link" or b.get("url") for row in (a.get("payload", {}).get("buttons") or []) for b in row)
            for a in atts)
        if self.rules.get("skip_forwards", True) and (msg.get("link") or {}).get("type") == "forward":
            log.info("пропуск: репост из %s", source)
            return
        if is_ad(text, url_buttons):
            log.info("пропуск: реклама в %s: %s", source, text[:60].replace("\n", " "))
            return
        kind = classify(text)
        if kind == "news" and not self.rules.get("post_news", False):
            log.info("пропуск: посторонняя новость в %s: %s", source, text[:60].replace("\n", " "))
            return
        clean = clean_text(text)
        if not clean:
            return
        for g in targets:
            if not matches_filter(text, g.get("filter")):
                continue
            if kind == "quiet" and not self.quiet_allowed(g["slug"]):
                log.info("пропуск «тихо» для %s (лимит)", g["vk"])
                continue
            try:
                ck = card_kind(kind, text)
                att = None
                if ck:
                    used = self.state.d["cards"].setdefault(g["slug"], {})
                    gap = float(self.rules.get("card_gap_minutes", 45)) * 60
                    if time.time() - used.get(ck, 0) >= gap:   # карточка только у первого поста серии
                        att = (g.get("cards") or {}).get(ck)
                        used[ck] = time.time()
                self.post(g, clean, att)
            except Exception as e:
                log.error("ошибка публикации в %s: %s", g["vk"], e)
        if mid:
            self.state.d["seen"] = (self.state.d["seen"] + [mid])[-500:]
        self.state.save()

    def promo(self):
        """Раз в день в каждой группе — пост с приглашением в MAX (единственный пост со ссылкой)."""
        now = dt.datetime.now(self.tz)
        if now.hour != int(self.rules.get("promo_hour", 20)):
            return
        today = now.date().isoformat()
        for g in self.groups:
            if self.state.d["promo"].get(g["slug"]) == today:
                continue
            text = (f"🔔 Хотите узнавать об опасности первыми?\n\n"
                    f"В нашем канале в MAX пишем раньше и подробнее: где именно, куда летит, фото и видео очевидцев.\n\n"
                    f"Подписывайтесь: https://{g['max']}")
            try:
                self.post(g, text)
                if not self.dry:   # пробный прогон не «съедает» сегодняшний пост-перелив
                    self.state.d["promo"][g["slug"]] = today
            except Exception as e:
                log.error("ошибка поста-перелива в %s: %s", g["vk"], e)
        self.state.save()

    def run(self):
        self.resolve_chats()
        log.info("старт: %d групп, %d каналов видно%s", len(self.groups), len(self.chat_to_source), " (ПРОБНЫЙ РЕЖИМ)" if self.dry else "")
        secrets_mtime = os.path.getmtime(SECRETS_FILE) if os.path.exists(SECRETS_FILE) else 0
        while True:
            if os.path.exists(SECRETS_FILE) and os.path.getmtime(SECRETS_FILE) != secrets_mtime:
                log.info("ключи изменены на странице настройки — перезапускаюсь")
                sys.exit(0)            # systemd сразу запустит заново уже с новыми ключами
            try:
                r = self.max.updates(self.state.d["marker"])
                for u in r.get("updates", []):
                    if u.get("update_type") == "message_created":
                        self.handle(u.get("message") or {})
                    elif u.get("update_type") == "bot_added":
                        self.learn_chat(u.get("chat_id"))
                if r.get("marker") is not None:
                    self.state.d["marker"] = r["marker"]
                    self.state.save()
                self.promo()
            except requests.RequestException as e:
                log.warning("сеть: %s, повтор через 5 с", e)
                time.sleep(5)
            except Exception as e:
                log.exception("ошибка: %s", e)
                time.sleep(5)


def wait_for_secrets():
    """Пока ключи не введены на странице настройки — ждём."""
    warned = False
    while True:
        s = load_secrets()
        if s.get("max_token") and s.get("vk"):
            return s
        if not warned:
            log.info("жду ключи: откройте страницу настройки и введите токен MAX и ключи групп ВК")
            warned = True
        time.sleep(15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list-chats", action="store_true")
    a = ap.parse_args()
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%d.%m %H:%M:%S", handlers=handlers)
    cfg = load_config()
    secrets = wait_for_secrets()
    if a.list_chats:
        for c in MAX(secrets["max_token"]).chats():
            print(c.get("chat_id"), c.get("type"), c.get("title"), c.get("link"))
        return
    dry = a.dry_run or bool(secrets.get("dry_run"))
    try:
        Bot(cfg, secrets, dry).run()
    except SystemExit:
        raise
    except Exception:
        log.exception("бот упал")
        time.sleep(30)
        raise


if __name__ == "__main__":
    main()

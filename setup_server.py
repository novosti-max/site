"""Страница настройки бота: сюда вставляют токен MAX-бота и ключи групп ВК.

Ключи сохраняются только на сервере (/etc/radar-bot/secrets.json, доступ только у пользователя radarbot)
и обратно на страницу не выводятся. Страница открывается по секретной ссылке
https://<домен>:8443/<ключ-страницы>/ (в репозитории только хэш ключа — setup_key.sha256).
"""
import hashlib, html, json, os, secrets as pyrand, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests, yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS_FILE = os.environ.get("RADAR_SECRETS", "/etc/radar-bot/secrets.json")
# В репозитории лежит только хэш ключа страницы, сам ключ знает владелец.
KEY_HASH_FILE = os.path.join(HERE, "setup_key.sha256")
LOG_FILE = os.environ.get("RADAR_LOG", "/var/lib/radar-bot/bot.log")
PORT = int(os.environ.get("RADAR_SETUP_PORT", "8099"))
MAX_API = "https://platform-api.max.ru"
VK_API = "https://api.vk.ru/method/"


def cfg():
    return yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8"))


def load():
    try:
        return json.load(open(SECRETS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save(d):
    tmp = SECRETS_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, SECRETS_FILE)


def norm(s):
    s = (s or "").strip().lower()
    for p in ("https://", "http://", "www."):
        if s.startswith(p):
            s = s[len(p):]
    return s.rstrip("/")


def vk(method, token, **p):
    p.update(access_token=token, v="5.199")
    return requests.post(VK_API + method, data=p, timeout=20).json()


def check_max(token):
    if not token:
        return None, "не задан", []
    try:
        h = {"Authorization": token}
        me = requests.get(MAX_API + "/me", headers=h, timeout=15)
        if me.status_code != 200:
            return False, f"MAX не принял токен (код {me.status_code})", []
        me = me.json()
        chats, marker = [], None
        while True:
            params = {"count": 100}
            if marker:
                params["marker"] = marker
            r = requests.get(MAX_API + "/chats", headers=h, params=params, timeout=20).json()
            chats += r.get("chats", [])
            marker = r.get("marker")
            if not marker:
                break
        name = me.get("name") or me.get("first_name") or "бот"
        return True, f"{name} (@{me.get('username', '')})", [norm(c.get("link")) for c in chats if c.get("link")]
    except Exception as e:
        return False, f"не удалось проверить: {e}", []


def check_vk(g, token):
    if not token:
        return None, "ключ не задан"
    try:
        r = vk("groups.getById", token, group_id=g["gid"])
        if "error" in r:
            return False, "ВК не принял ключ: " + r["error"].get("error_msg", "")
        perms = vk("groups.getTokenPermissions", token).get("response", {})
        names = {p.get("name") for p in perms.get("permissions", [])}
        if "wall" not in names:
            return False, "у ключа нет права «Стена» — создайте ключ заново с галочкой «Стена»"
        return True, "ключ работает"
    except Exception as e:
        return False, f"не удалось проверить: {e}"


def test_post(g, token):
    """Отложенный пост на завтра (его видят только админы) и сразу удаление — проверка права публиковать."""
    r = vk("wall.post", token, owner_id=-int(g["gid"]), from_group=1,
           message="Проверка связи бота. Запись удалится автоматически.", publish_date=int(time.time()) + 86400)
    if "error" in r:
        return False, "публикация не прошла: " + r["error"].get("error_msg", "")
    pid = r["response"]["post_id"]
    d = vk("wall.delete", token, owner_id=-int(g["gid"]), post_id=pid)
    return True, "публикация работает" + ("" if "error" not in d else " (тестовую запись удалите в отложенных вручную)")


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#f3f5f8;color:#1b1f24}
main{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0 4px} h2{font-size:17px;margin:22px 0 8px}
.card{background:#fff;border-radius:14px;padding:14px 16px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,.06)}
label{display:block;font-weight:600;margin-bottom:6px}
input[type=password]{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #cdd3dc;border-radius:10px;font-size:15px}
.ok{color:#14833b}.bad{color:#c4271d}.none{color:#7a8594}.small{font-size:13px;color:#5d6876}
button{background:#2763d8;color:#fff;border:0;border-radius:10px;padding:11px 18px;font-size:15px;font-weight:600;cursor:pointer}
button.sec{background:#e8eef9;color:#2763d8;padding:7px 12px;font-size:13px}
.row{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
pre{white-space:pre-wrap;font-size:12px;background:#0f1724;color:#d6e2f5;border-radius:10px;padding:12px;max-height:340px;overflow:auto}
a{color:#2763d8}
.msg{background:#e7f6ec;border-radius:10px;padding:10px 12px}
"""


def mark(ok):
    return {True: "<span class=ok>✓</span>", False: "<span class=bad>✗</span>", None: "<span class=none>—</span>"}[ok]


def page(key, msg=""):
    c, s = cfg(), load()
    groups = [g for g in c["groups"] if g.get("enabled")]
    mok, mtext, links = check_max(s.get("max_token", ""))
    out = [f"<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Настройка бота Радар</title><style>{CSS}</style></head><body><main>",
           "<h1>Настройка бота MAX → ВКонтакте</h1>",
           "<p class=small>Ключи сохраняются только на этом сервере и обратно на страницу не выводятся. "
           "Пустое поле — ключ остаётся прежним.</p>"]
    if msg:
        out.append(f"<div class=msg>{msg}</div>")
    out.append(f"<form method=post action='/{key}/save'>")
    out.append("<h2>1. Токен MAX-бота</h2><div class=card>"
               f"<label>Токен из «MAX для партнёров» → бот → токен</label>"
               f"<input type=password name=max_token autocomplete=off placeholder='{'задан — вставьте новый, чтобы заменить' if s.get('max_token') else 'вставьте токен'}'>"
               f"<p>{mark(mok)} {html.escape(mtext)}</p></div>")
    out.append("<h2>2. Ключи групп ВКонтакте</h2>"
               "<p class=small>Для каждой группы: откройте ссылку «создать ключ» → «Создать ключ» → "
               "отметьте <b>Управление сообществом</b>, <b>Фотографии</b> и <b>Стена</b> → подтвердите кодом → "
               "скопируйте ключ и вставьте в поле.</p>")
    vkkeys = s.get("vk", {})
    for g in groups:
        ok, text = check_vk(g, vkkeys.get(g["slug"]))
        seen = norm(g["source"]) in links
        out.append(f"<div class=card><div class=row><label>{html.escape(g['city'])} — vk.com/{g['vk']}</label>"
                   f"<a href='https://vk.com/club{g['gid']}?act=tokens' target=_blank rel=noopener>создать ключ ↗</a></div>"
                   f"<input type=password name='vk_{g['slug']}' autocomplete=off placeholder='{'задан' if vkkeys.get(g['slug']) else 'вставьте ключ группы'}'>"
                   f"<p class=small>{mark(ok)} ВК: {html.escape(text)} &nbsp; {mark(seen if mok else None)} "
                   f"MAX-канал {html.escape(g['source'])}: {'бот видит канал' if seen else 'бот не админ в канале' if mok else 'проверю после токена'}</p>"
                   + "</div>")
    dry = s.get("dry_run", False)
    out.append(f"<h2>3. Режим</h2><div class=card><label><input type=checkbox name=dry_run {'checked' if dry else ''}> "
               "Пробный режим — ничего не публиковать, только писать в журнал, что бот выложил бы</label></div>")
    out.append("<p><button>Сохранить и проверить</button></p></form>")
    try:
        tail = open(LOG_FILE, encoding="utf-8").read().splitlines()[-40:]
    except Exception:
        tail = ["журнал пока пуст"]
    out.append("<h2>Журнал бота (последние строки)</h2><pre>" + html.escape("\n".join(tail)) + "</pre>")
    out.append("<p class=small>Обновите страницу, чтобы увидеть свежие записи.</p></main></body></html>")
    return "".join(out)


class H(BaseHTTPRequestHandler):
    key_value = ""

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def parts(self):
        p = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        want = open(KEY_HASH_FILE).read().strip()
        if not p or not pyrand.compare_digest(hashlib.sha256(p[0].encode()).hexdigest(), want):
            return None
        self.key_value = p[0]
        return p[1:]

    def key(self):
        return self.key_value

    def do_GET(self):
        p = self.parts()
        if p is None:
            return self.send(404, "Not found", "text/plain")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.send(200, page(self.key(), html.escape(q.get("m", [""])[0])))

    def do_POST(self):
        p = self.parts()
        if p is None:
            return self.send(404, "Not found", "text/plain")
        n = int(self.headers.get("Content-Length", 0) or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8")).items()}
        s = load()
        s.setdefault("vk", {})
        if form.get("max_token", "").strip():
            s["max_token"] = form["max_token"].strip()
        for k, v in form.items():
            if k.startswith("vk_") and v.strip():
                s["vk"][k[3:]] = v.strip()
        s["dry_run"] = "dry_run" in form
        save(s)
        msg = "Сохранено. Бот перезапустится с новыми настройками в течение минуты."
        if len(p) == 2 and p[0] == "test":
            g = next((g for g in cfg()["groups"] if g["slug"] == p[1]), None)
            if g and s["vk"].get(g["slug"]):
                ok, text = test_post(g, s["vk"][g["slug"]])
                msg = f"{g['city']}: {'✓' if ok else '✗'} {text}"
        self.send(303, "", headers={"Location": f"/{self.key()}/?m=" + urllib.parse.quote(msg)})

    def log_message(self, *a):
        pass   # не пишем адреса страниц (в них ключ страницы) в журнал


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

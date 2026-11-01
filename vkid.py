"""Вход администратора групп через VK ID и загрузка фото на стену группы его ключом (FIX-12).

Зачем: фото, загруженное ключом сообщества через сервер сообщений, на стене ВК не видно (01.11, Липецк №456).
Видимое фото на стене загружается только ключом пользователя-админа: photos.getWallUploadServer -> photos.saveWallPhoto.

Как входят: страница настройки -> «Войти через VK ID» (новая вкладка) -> VK ID (OAuth 2.1, PKCE) -> пустая страница
https://oauth.vk.com/blank.html?code=… -> её адрес вставляют в поле на странице настройки -> обмен кода на ключ.
(Адрес с портом 8443 VK не принимает, а .html из папки rb-status.txt сайт не отдаёт — поэтому так.)
Код из адреса одноразовый, живёт 10 минут и без code_verifier (он есть только в памяти страницы настройки) бесполезен.
Ключи лежат только на сервере: /var/lib/radar-bot/vkid.json (права 600). На страницу, в журнал и в репозиторий не выводятся.
Ключ доступа живёт ~1 час, бот сам продлевает его по refresh_token (тот действует 180 дней и обновляется при продлении).
"""
import base64, hashlib, json, os, secrets, threading, time, urllib.parse

import requests

APP_ID = int(os.environ.get("RADAR_VKID_APP", "54866180"))   # приложение VK ID «Радар постинг» (номер не секретный)
ID_HOST = os.environ.get("RADAR_VKID_HOST", "https://id.vk.ru")
REDIRECT = os.environ.get("RADAR_VKID_REDIRECT", "https://oauth.vk.com/blank.html")
SCOPE = "photos wall groups"
VK_API = "https://api.vk.ru/method/"
FILE = os.environ.get("RADAR_VKID", "/var/lib/radar-bot/vkid.json")
_pending = {}          # state -> (code_verifier, ключ страницы настройки, время)
_lock = threading.Lock()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def load() -> dict:
    try:
        return json.load(open(FILE, encoding="utf-8"))
    except Exception:
        return {}


def save(d: dict):
    tmp = FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, FILE)


def start(page_key: str = "") -> str:
    """Ссылка входа VK ID. code_verifier остаётся в памяти страницы настройки (15 мин)."""
    verifier = _b64(secrets.token_bytes(48))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    state = _b64(secrets.token_bytes(24))
    now = time.time()
    with _lock:
        for s, v in list(_pending.items()):
            if now - v[2] > 900:
                _pending.pop(s, None)
        _pending[state] = (verifier, page_key, now)
    q = {"response_type": "code", "client_id": APP_ID, "redirect_uri": REDIRECT, "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256", "scope": SCOPE}
    return ID_HOST + "/authorize?" + urllib.parse.urlencode(q)


def _err(r) -> str:
    if not isinstance(r, dict):
        return "непонятный ответ"
    return str(r.get("error_description") or r.get("error") or "нет ключа в ответе")[:200]


def parse_return(url: str) -> dict:
    """Адрес пустой страницы после входа (code, state, device_id в ?… или #…) -> параметры."""
    u = urllib.parse.urlparse((url or "").strip())
    q = dict(urllib.parse.parse_qsl(u.query))
    q.update(urllib.parse.parse_qsl(u.fragment))
    return q


def finish(params: dict):
    """Ответ VK ID (code, state, device_id) -> (ok, текст, ключ страницы настройки)."""
    state = params.get("state", "")
    with _lock:
        p = _pending.pop(state, None)
    if not p or time.time() - p[2] > 900:
        return False, "ссылка входа устарела — нажмите «Войти через VK ID» ещё раз", (p[1] if p else "")
    verifier, page_key, _ = p
    if params.get("error"):
        return False, "VK ID: " + str(params.get("error_description") or params["error"])[:200], page_key
    code, device_id = params.get("code"), params.get("device_id")
    if not code or not device_id:
        return False, "VK ID не вернул код входа", page_key
    try:
        r = requests.post(ID_HOST + "/oauth2/auth", data={
            "grant_type": "authorization_code", "code_verifier": verifier, "redirect_uri": REDIRECT,
            "code": code, "client_id": APP_ID, "device_id": device_id, "state": state}, timeout=20).json()
    except Exception as e:
        return False, f"VK ID не ответил: {type(e).__name__}", page_key
    if not r.get("access_token"):
        return False, "VK ID не выдал ключ: " + _err(r), page_key
    now = time.time()
    save({"access_token": r["access_token"], "refresh_token": r.get("refresh_token", ""),
          "expires_at": now + int(r.get("expires_in") or 3600), "device_id": device_id,
          "user_id": r.get("user_id"), "scope": r.get("scope", ""), "login_at": now, "error": ""})
    return True, f"вход выполнен (VK id {r.get('user_id', '?')}, доступы: {r.get('scope') or '—'})", page_key


def token():
    """Действующий ключ админа или None. Продлевает сам; при ошибке продления 10 мин не повторяет."""
    d = load()
    if not d.get("access_token"):
        return None
    now = time.time()
    if float(d.get("expires_at", 0)) > now + 120:
        return d["access_token"]
    if not d.get("refresh_token") or now - float(d.get("refresh_failed", 0)) < 600:
        return None
    try:
        r = requests.post(ID_HOST + "/oauth2/auth", data={
            "grant_type": "refresh_token", "refresh_token": d["refresh_token"], "client_id": APP_ID,
            "device_id": d.get("device_id", ""), "state": _b64(secrets.token_bytes(16))}, timeout=20).json()
    except Exception as e:
        r = {"error": type(e).__name__}
    if not r.get("access_token"):
        d.update(error="продление не прошло: " + _err(r), refresh_failed=now)
        save(d)
        return None
    d.update(access_token=r["access_token"], refresh_token=r.get("refresh_token") or d["refresh_token"],
             expires_at=now + int(r.get("expires_in") or 3600), error="", refresh_failed=0)
    save(d)
    return d["access_token"]


def status():
    """Для страницы настройки: (ok, текст). Ключи не показываются."""
    d = load()
    if not d.get("access_token"):
        return None, "вход не выполнен — фото новостей во ВК не видны"
    when = time.strftime("%d.%m %H:%M", time.localtime(float(d.get("login_at", 0))))
    if d.get("error"):
        return False, f"вход {when} (VK id {d.get('user_id')}), {d['error']} — войдите ещё раз"
    return True, f"вход {when} (VK id {d.get('user_id')}), доступы: {d.get('scope') or '—'}; ключ продлевается сам"


def _api(method, tok, **p):
    p.update(access_token=tok, v="5.199")
    r = requests.post(VK_API + method, data=p, timeout=30).json()
    if "error" in r:
        raise RuntimeError(f"{method}: {r['error'].get('error_code')} {r['error'].get('error_msg')}")
    return r["response"]


def upload_wall_photo(tok, gid, image_bytes) -> str:
    """Фото на стену группы gid от имени админа -> 'photo-<gid>_<id>' для wall.post."""
    gid = abs(int(gid))
    srv = _api("photos.getWallUploadServer", tok, group_id=gid)
    up = requests.post(srv["upload_url"], files={"photo": ("photo.jpg", image_bytes, "image/jpeg")}, timeout=60).json()
    if not up.get("photo") or up.get("photo") == "[]":
        raise RuntimeError("ВК не принял файл фото")
    saved = _api("photos.saveWallPhoto", tok, group_id=gid, server=up["server"], photo=up["photo"], hash=up["hash"])
    ph = saved[0]
    return f"photo{ph['owner_id']}_{ph['id']}"

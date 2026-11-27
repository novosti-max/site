"""Крео-бот (27.11): вызывается из radar_bot.handle() для КАЖДОГО события MAX.

Только дописывает строки в файлы и сразу возвращается: сети нет, ожиданий нет, любые ошибки глушатся —
тревоги это не задерживает и не ломает.
  creo_inbox.jsonl — сообщения людей в личку боту (их читает сервис radar-creo и отвечает крео);
  creo_feed.jsonl  — посты всех каналов, где бот админ (последние FEED_KEEP), — из них крео берёт факты.
"""
import json
import os
import time

FEED_KEEP = 6000      # строк в ленте (≈ сутки-двое по всем каналам)
INBOX_KEEP = 300      # строк в очереди лички
_n = {"feed": 0, "inbox": 0}


def _append(path, row, keep, key):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _n[key] += 1
    if _n[key] % 300 == 1:  # при старте и раз в ~300 строк обрезаем файл
        lines = open(path, encoding="utf-8").readlines()
        if len(lines) > keep:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines[-keep:])
            os.replace(tmp, path)


def _images(body):
    out = []
    for a in body.get("attachments") or []:
        if a.get("type") == "image":
            u = (a.get("payload") or {}).get("url")
            if u:
                out.append(u)
    return out


def record(msg, chat_to_source, state_dir):
    try:
        rec = msg.get("recipient") or {}
        body = msg.get("body") or {}
        snd = msg.get("sender") or {}
        ct = rec.get("chat_type")
        if ct == "dialog":
            if snd.get("is_bot"):
                return
            row = {"t": msg.get("timestamp"), "rt": round(time.time(), 1), "mid": body.get("mid"),
                   "user_id": snd.get("user_id"), "name": snd.get("name") or snd.get("first_name") or "",
                   "chat_id": rec.get("chat_id"), "text": (body.get("text") or "")[:2000]}
            _append(os.path.join(state_dir, "creo_inbox.jsonl"), row, INBOX_KEEP, "inbox")
        elif ct == "channel":
            row = {"t": msg.get("timestamp"), "chat": rec.get("chat_id"),
                   "src": (chat_to_source or {}).get(rec.get("chat_id")) or "",
                   "mid": body.get("mid"), "text": (body.get("text") or "")[:3000],
                   "img": len(_images(body)), "fwd": ((msg.get("link") or {}).get("type") == "forward"),
                   "fwd_text": (((msg.get("link") or {}).get("message") or {}).get("text") or "")[:3000]}
            _append(os.path.join(state_dir, "creo_feed.jsonl"), row, FEED_KEEP, "feed")
    except Exception:
        pass

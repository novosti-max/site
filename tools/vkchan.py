#!/usr/bin/env python3
"""Проверка каналов ВК (26.11): может ли ключ сообщества Самары писать в канал группы.

Ничего не публикует: методы отправки вызываются БЕЗ текста. Ответ ВК показывает, что дальше:
3 — метода нет, 100 — метод есть и ключ принят (не хватает текста), 5/15/27/917 — ключу нельзя.
Канал Самары создан 26.11 (vk.ru/im/channels/-242693933). Ключи не печатает.
"""
import json, sys, time

import requests

API = "https://api.vk.ru/method/"
CH = 242693933            # канал «Радар Самара 63 | БПЛА»
GID = 237253756           # группа Самара


def call(method, tok, v, **p):
    p.update(access_token=tok, v=v)
    try:
        r = requests.post(API + method, data=p, timeout=15).json()
    except Exception as e:
        return f"сеть: {type(e).__name__}"
    if "error" in r:
        e = r["error"]
        d = f" descr «{e['descr']}»" if e.get("descr") else ""
        return f"ошибка {e.get('error_code')}: {e.get('error_msg')}{d}"
    return "ok " + json.dumps(r.get("response"), ensure_ascii=False)[:300]


def main(out, secrets="/etc/radar-bot/secrets.json"):
    tok = json.load(open(secrets, encoding="utf-8")).get("vk", {}).get("samara", "")
    L = [time.strftime("%Y-%m-%d %H:%M:%S") + " проверка канала ВК Самары (ничего не публикует)"]
    if not tok:
        L.append("нет ключа samara")
    for v in ("5.199", "5.289"):
        L.append(f"--- v={v}")
        L.append("channels.getById: " + call("channels.getById", tok, v, channel_id=CH))
        L.append("channels.getById(-): " + call("channels.getById", tok, v, channel_id=-CH))
        L.append("messages.getConversationsById: " + call("messages.getConversationsById", tok, v, peer_ids=-CH))
        L.append("messages.send без текста peer -CH: " + call("messages.send", tok, v, peer_id=-CH, random_id=0))
        L.append("messages.send без текста peer CH: " + call("messages.send", tok, v, peer_id=CH, random_id=0))
        for m in ("channels.send", "channels.sendMessage", "channels.post", "channels.createPost", "channels.messagesSend"):
            L.append(f"{m} без текста: " + call(m, tok, v, channel_id=CH))
        r = call("photos.getMessagesUploadServer", tok, v, peer_id=-CH)
        L.append("photos.getMessagesUploadServer: " + ("ok (адрес загрузки выдан)" if r.startswith("ok") else r))
    open(out + ".tmp", "w", encoding="utf-8").write("\n".join(L) + "\n")
    import os; os.replace(out + ".tmp", out)


if __name__ == "__main__":
    main(sys.argv[1])

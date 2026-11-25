#!/usr/bin/env python3
"""Диагностика ключей ВК (25.11): почему wall.post отвечает «User authorization failed (5)».

Ничего не публикует: wall.post вызывается БЕЗ текста и вложений — если ключ принят, ВК отвечает
ошибкой 100 «параметр не задан», если не принят — ошибкой 5. Так видно, на каком шаге отказ.
Проверяет оба адреса API (api.vk.ru и api.vk.com), по IPv4 и по IPv6 (другой адрес сервера), и внешний IP. Ключи не печатает.
Запуск: python3 tools/vkdiag.py --out rb-vkdiag.txt [--secrets /etc/radar-bot/secrets.json]
"""
import argparse, json, os, sys, time

import socket

import requests, urllib3.util.connection as u3c, yaml

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTS = ("https://api.vk.ru/method/", "https://api.vk.com/method/")
FAMILIES = (("IPv4", socket.AF_INET), ("IPv6", socket.AF_INET6))   # 25.11: пробуем другой IP сервера — IPv6, если он есть
SLUGS = ("russia", "samara")   # ключи этих групп Никита заменил 25.11


def call(host, method, token, **p):
    p.update(access_token=token, v="5.199")
    try:
        r = requests.post(host + method, data=p, timeout=15).json()
    except Exception as e:
        return f"сеть: {type(e).__name__}"
    if "error" in r:
        e = r["error"]
        extra = []
        if e.get("error_subcode"):
            extra.append(f"subcode {e['error_subcode']}")
        if e.get("error_text"):
            extra.append(f"text «{e['error_text']}»")
        if e.get("redirect_uri"):
            extra.append("есть redirect_uri (ВК просит подтверждение в браузере)")
        if e.get("captcha_sid"):
            extra.append("captcha")
        return f"ошибка {e.get('error_code')}: {e.get('error_msg')}" + (" | " + "; ".join(extra) if extra else "")
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--secrets", default="/etc/radar-bot/secrets.json")
    a = ap.parse_args()
    s = json.load(open(a.secrets, encoding="utf-8"))
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8"))
    groups = {g["slug"]: g for g in cfg["groups"]}
    lines = [time.strftime("%Y-%m-%d %H:%M:%S") + " диагностика ключей ВК (ничего не публикует)"]
    for fam_name, fam in FAMILIES:
        u3c.allowed_gai_family = lambda fam=fam: fam
        try:
            ip = requests.get("https://api64.ipify.org", timeout=10).text.strip()
        except Exception as e:
            ip = f"не узнать ({type(e).__name__})"
        lines.append(f"{fam_name}: внешний адрес сервера {ip}")
        for slug in SLUGS:
            g, tok = groups.get(slug), s.get("vk", {}).get(slug)
            if not g or not tok:
                lines.append(f"  {slug}: нет группы или ключа")
                continue
            for host in HOSTS:
                h = host.split("/")[2]
                read = call(host, "groups.getById", tok, group_id=g["gid"])
                perms = call(host, "groups.getTokenPermissions", tok)
                post = call(host, "wall.post", tok, owner_id=-int(g["gid"]), from_group=1)
                lines.append(f"  {slug} {h}: чтение {read}; права {perms}; wall.post без текста {post}")
    open(a.out + ".tmp", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    os.replace(a.out + ".tmp", a.out)


if __name__ == "__main__":
    sys.exit(main())

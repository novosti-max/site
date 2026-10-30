"""Разовая диагностика доступа MAX-бота к каналам (печатает только коды ответов и обрезанные ответы, без токена)."""
import json, requests
s = json.load(open("/etc/radar-bot/secrets.json"))
h = {"Authorization": s.get("max_token", "")}
B = "https://platform-api.max.ru"
for path in ["/chats", "/chats?count=50", "/chats/channel_radar_msk", "/chats/@channel_radar_msk",
             "/chats/max.ru%2Fchannel_radar_msk", "/chats/https%3A%2F%2Fmax.ru%2Fchannel_radar_msk", "/subscriptions"]:
    try:
        r = requests.get(B + path, headers=h, timeout=15)
        print(path, r.status_code, r.text[:300].replace("\n", " "))
    except Exception as e:
        print(path, "ERR", e)
try:
    r = requests.get(B + "/chats", headers={}, params={"access_token": s.get("max_token", "")}, timeout=15)
    print("/chats?access_token=…", r.status_code, r.text[:200].replace("\n", " "))
except Exception as e:
    print("qs ERR", e)

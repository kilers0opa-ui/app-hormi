import json, hormi_videos as hv
out = {}
for h in ("@LigaBBVAMX", "@ligamx", "@ChivasTV", "@Chivas", "@TigresOficial", "@ClubAmerica", "@Rayados", "@Xolos", "@PremierLiga", "@rpl"):
    d = hv.yt_api("channels", {"part": "snippet", "forHandle": h}, 1) or {}
    out[h] = [(it["id"], it["snippet"]["title"]) for it in d.get("items") or []]
d = hv.yt_api("search", {"part": "snippet", "q": "Chivas Tigres resumen", "type": "video", "order": "date", "maxResults": 50,
                          "publishedAfter": "2026-10-18T00:00:00Z", "regionCode": "MX"}, 100) or {}
out["busqueda"] = [(it["snippet"]["channelTitle"], it["snippet"]["channelId"], it["snippet"]["title"][:70]) for it in d.get("items") or []]
json.dump(out, open("diag_canales2.json", "w"), ensure_ascii=False, indent=1)

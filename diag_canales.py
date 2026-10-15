import json, traceback
from datetime import datetime, timezone, timedelta
import hormi_videos as hv
out = {"canales": {}, "err": []}
desde = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
qs = ["resumen Claro Sports Europa League", "ESPN MX resumen", "TNT Sports México resumen Champions",
      "FOX Sports MX resumen", "TUDN resumen", "Copenhague Braga", "FC Copenhagen Braga highlights",
      "Olympiacos highlights", "Feyenoord AZ samenvatting"]
for q in qs:
    try:
        d = hv.yt_api("search", {"part": "snippet", "q": q, "type": "video", "order": "date", "maxResults": 50,
                                 "regionCode": "MX", "publishedAfter": desde}, 100) or {}
        for it in d.get("items") or []:
            sn = it.get("snippet") or {}
            c = out["canales"].setdefault(sn.get("channelTitle"), {"id": sn.get("channelId"), "n": 0, "ej": []})
            c["n"] += 1
            if len(c["ej"]) < 2:
                c["ej"].append(sn.get("title"))
    except Exception:
        out["err"].append(traceback.format_exc())
json.dump(out, open("diag_canales.json", "w"), indent=1, ensure_ascii=False)

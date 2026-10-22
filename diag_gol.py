import json, hormi_alertas as ha
r = ha.api("/fixtures", {"id": 1636308}) or []
f = r[0] if r else {}
out = {"score": f.get("goals"), "status": (f.get("fixture") or {}).get("status"),
       "goles": [e for e in f.get("events") or [] if e.get("type") == "Goal"],
       "armando_stats": [p for t in f.get("players") or [] for p in t.get("players") or [] if p["player"]["id"] == 291713]}
json.dump(out, open("diag_gol.json", "w"), ensure_ascii=False, indent=1)

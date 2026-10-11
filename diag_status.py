"""TEMPORAL: consumo del día en API-Football (/status no gasta cupo)."""
import json, os, urllib.request
req = urllib.request.Request("https://v3.football.api-sports.io/status", headers={"x-apisports-key": os.environ["APIFOOTBALL_KEY"]})
d = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
json.dump(d.get("response"), open("diag_status.json", "w"), ensure_ascii=False, indent=1)

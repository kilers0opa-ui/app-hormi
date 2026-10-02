import json, os, urllib.request, urllib.parse, time
KEY = os.environ["APIFOOTBALL_KEY"]
def api(ep, **p):
    r = urllib.request.Request("https://v3.football.api-sports.io" + ep + "?" + urllib.parse.urlencode(p), headers={"x-apisports-key": KEY})
    time.sleep(0.4)
    return json.loads(urllib.request.urlopen(r, timeout=30).read())
cfg = json.load(open("hormi_config.json"))
ids = {p["player_id"]: p["key"] for p in cfg["players"] if p.get("player_id")}
out = {}
sq = api("/players/squads", team=16).get("response", [])
players = sq[0]["players"] if sq else []
out["squad_size"] = len(players)
out["ours_in_squad"] = {ids[p["id"]]: p for p in players if p["id"] in ids}
out["squad_names"] = [p["name"] for p in players]
# alineaciones del partido USA-Mexico (por si ya hay)
sch = json.load(open("hormi_horario.json"))
fid = None
for k, v in sch.items():
    if isinstance(v, dict):
        for t, e in v.items():
            if isinstance(e, dict) and e.get("home") == "USA":
                fid = e["fixture_id"]
out["fixture"] = fid
if fid:
    out["lineups"] = api("/fixtures/lineups", fixture=fid).get("response", [])
    out["fixture_info"] = [{"status": f["fixture"]["status"], "date": f["fixture"]["date"]} for f in api("/fixtures", id=fid).get("response", [])]
json.dump(out, open("diag/diag_convoc.json", "w"), ensure_ascii=False, indent=1)

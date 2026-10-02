import json, os, urllib.request, urllib.parse, time
KEY = os.environ["APIFOOTBALL_KEY"]
def api(ep, **p):
    r = urllib.request.Request("https://v3.football.api-sports.io" + ep + "?" + urllib.parse.urlencode(p), headers={"x-apisports-key": KEY})
    time.sleep(0.4)
    return json.loads(urllib.request.urlopen(r, timeout=30).read())
out = {}
fx = api("/fixtures", team=553, league=199, season=2026).get("response", [])
out["fixtures_cup"] = []
for f in fx:
    item = {"id": f["fixture"]["id"], "date": f["fixture"]["date"][:10], "status": f["fixture"]["status"]["short"], "round": f["league"]["round"],
            "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"], "goals": f["goals"], "armando": None}
    for t in api("/fixtures/players", fixture=f["fixture"]["id"]).get("response", []):
        for pl in t["players"]:
            if pl["player"]["id"] == 291713:
                s = pl["statistics"][0]
                item["armando"] = {"min": s["games"]["minutes"], "goals": s["goals"]["total"], "assists": s["goals"]["assists"], "sub": s["games"]["substitute"]}
    ev = api("/fixtures/events", fixture=f["fixture"]["id"]).get("response", [])
    item["events_armando"] = [{"min": e["time"]["elapsed"], "type": e["type"], "detail": e["detail"]} for e in ev if (e.get("player") or {}).get("id") == 291713 or (e.get("assist") or {}).get("id") == 291713]
    out["fixtures_cup"].append(item)
# temporadas 2025 de Armando en Olympiacos? (por si fue en otra temporada)
d = api("/players", id=291713, season=2025)
out["season2025_leagues"] = [x["league"]["name"] for e in d.get("response", []) for x in e["statistics"]]
json.dump(out, open("diag/diag_grecia.json", "w"), ensure_ascii=False, indent=1)

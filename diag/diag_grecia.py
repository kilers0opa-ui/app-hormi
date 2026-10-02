import json, os, urllib.request, urllib.parse, time
KEY = os.environ["APIFOOTBALL_KEY"]
def api(ep, **p):
    r = urllib.request.Request("https://v3.football.api-sports.io" + ep + "?" + urllib.parse.urlencode(p), headers={"x-apisports-key": KEY})
    time.sleep(0.4)
    return json.loads(urllib.request.urlopen(r, timeout=30).read())
out = {}
for s in (2024, 2025, 2026):
    d = api("/players", id=291713, season=s)
    out[f"players_{s}"] = [
        {"team": x["team"]["name"], "league": x["league"]["id"], "league_name": x["league"]["name"], "country": x["league"].get("country"),
         "played": x["games"]["appearences"], "goals": x["goals"]["total"], "assists": x["goals"]["assists"], "min": x["games"]["minutes"]}
        for e in d.get("response", []) for x in e["statistics"]]
    out[f"players_{s}_errors"] = d.get("errors")
for s in (2025, 2026):
    d = api("/leagues", team=553, season=s)
    out[f"leagues_olympiacos_{s}"] = [{"id": l["league"]["id"], "name": l["league"]["name"], "type": l["league"]["type"]} for l in d.get("response", [])]
d = api("/fixtures", team=553, last=15)
out["olympiacos_last15"] = [{"date": f["fixture"]["date"][:10], "league": f["league"]["id"], "name": f["league"]["name"], "season": f["league"]["season"], "round": f["league"].get("round"), "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"], "score": f["goals"]} for f in d.get("response", [])]
json.dump(out, open("diag/diag_grecia.json", "w"), ensure_ascii=False, indent=1)

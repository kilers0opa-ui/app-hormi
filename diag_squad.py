"""TEMPORAL: ¿la API publica la convocatoria de México? /players/squads?team=16 y /fixtures?team=16&next=5"""
import json, os, urllib.request, urllib.parse
key = os.environ["APIFOOTBALL_KEY"]
def get(ep, **p):
    req = urllib.request.Request("https://v3.football.api-sports.io" + ep + "?" + urllib.parse.urlencode(p), headers={"x-apisports-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()), r.headers.get("x-ratelimit-requests-remaining"), r.headers.get("x-ratelimit-requests-limit")
out = {}
sq, rem, lim = get("/players/squads", team=16)
out["limite_dia"] = lim; out["quedan_hoy"] = rem
r = (sq.get("response") or [{}])[0]
out["squad"] = [{"id": p["id"], "name": p["name"], "pos": p.get("position")} for p in r.get("players") or []]
fx, _, _ = get("/fixtures", team=16, next=6)
out["proximos"] = [{"id": f["fixture"]["id"], "date": f["fixture"]["date"], "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"], "league": f["league"]["name"], "logo": f["league"]["logo"]} for f in fx.get("response") or []]
json.dump(out, open("diag_squad.json", "w"), ensure_ascii=False, indent=1)

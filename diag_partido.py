"""Diagnóstico temporal: qué devuelve la API para el partido de la Selección (alineación, estado)."""
import json, os, urllib.request, urllib.parse
FID = 1629004
key = os.environ["APIFOOTBALL_KEY"]
def get(ep, **p):
    req = urllib.request.Request("https://v3.football.api-sports.io" + ep + "?" + urllib.parse.urlencode(p),
                                 headers={"x-apisports-key": key})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
out = {}
fx = get("/fixtures", id=FID)
r = (fx.get("response") or [None])[0]
out["errors"] = fx.get("errors")
if r:
    out["status"] = r["fixture"]["status"]
    out["score"] = r["goals"]
    out["lineups_in_fixture"] = [{"team": l["team"]["name"], "n_start": len(l.get("startXI") or []), "n_subs": len(l.get("substitutes") or [])} for l in (r.get("lineups") or [])]
    out["n_events"] = len(r.get("events") or [])
    out["periods"] = r["fixture"].get("periods")
    out["fixture_ts"] = r["fixture"].get("timestamp")
    out["events"] = [{"min": e["time"]["elapsed"], "extra": e["time"].get("extra"), "type": e["type"], "detail": e.get("detail"), "team": e["team"]["name"], "player": e["player"].get("name"), "assist": (e.get("assist") or {}).get("name")} for e in (r.get("events") or [])]
ln = get("/fixtures/lineups", fixture=FID)
out["lineups_endpoint"] = [{"team": l["team"]["name"], "formation": l.get("formation"),
    "start": [p["player"]["name"] for p in l.get("startXI") or []],
    "subs": [p["player"]["name"] for p in l.get("substitutes") or []]} for l in (ln.get("response") or [])]
out["lineups_errors"] = ln.get("errors")
json.dump(out, open("diag_partido.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False)[:1500])

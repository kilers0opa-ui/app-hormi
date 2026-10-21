import json, hormi_alertas as ha
out = {}
nxt = ha.api("/fixtures", {"team": 39, "next": 1}) or []
out["next"] = [{"id": f["fixture"]["id"], "date": f["fixture"]["date"], "m": f["teams"]["home"]["name"] + " vs " + f["teams"]["away"]["name"]} for f in nxt]
if nxt:
    out["inj_fixture"] = ha.api("/injuries", {"fixture": nxt[0]["fixture"]["id"]})
out["inj_team"] = [x for x in (ha.api("/injuries", {"team": 39, "season": 2026}) or [])][-15:]
out["inj_player"] = ha.api("/injuries", {"player": 2887, "season": 2026})
out["sidelined"] = ha.api("/sidelined", {"player": 2887})
for k, tid in (("olympiacos", 553), ("chivas", 2278)):
    out["n_team_" + k] = len(ha.api("/injuries", {"team": tid, "season": 2026}) or [])
json.dump(out, open("diag_dispo.json", "w"), ensure_ascii=False, indent=1, default=str)

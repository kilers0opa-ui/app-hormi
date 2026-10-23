#!/usr/bin/env python3
"""Diagnóstico temporal: compara player_stats.json (Ranking/hitos) contra los partidos uno por uno.
Escribe diag_datos.json. Se borra al terminar la revisión."""
import json
import hormi_alertas as ha

FIN = {"FT", "AET", "PEN"}
config = ha.load_config()
stats = ha.load_json(ha.STATS_FILE, {"players": {}})
out = {"players": {}}
cache_fp = {}


def fixture_players(fid, tid):
    k = (fid, tid)
    if k not in cache_fp:
        res = ha.api("/fixtures/players", {"fixture": fid, "team": tid}) or []
        filas = {}
        for t in res:
            for pl in t.get("players") or []:
                st = (pl.get("statistics") or [{}])[0]
                g = st.get("games") or {}
                filas[pl["player"]["id"]] = {"min": g.get("minutes") or 0, "sub": g.get("substitute"),
                                             "g": (st.get("goals") or {}).get("total") or 0,
                                             "a": (st.get("goals") or {}).get("assists") or 0}
        cache_fp[k] = filas
    return cache_fp[k]


def por_partidos(pid, tid, season):
    fxs = ha.api("/fixtures", {"team": tid, "season": season}) or []
    ligas = {}
    for fx in sorted(fxs, key=lambda f: f["fixture"]["timestamp"]):
        lg = fx["league"]
        if fx["fixture"]["status"]["short"] not in FIN or "riendl" in (lg.get("name") or ""):
            continue
        f = fixture_players(fx["fixture"]["id"], tid).get(pid)
        r = ligas.setdefault(f"{lg['id']} {lg['name']}", {"pj": 0, "g": 0, "a": 0, "min": 0, "sin_datos": 0, "partidos": []})
        if not cache_fp[(fx["fixture"]["id"], tid)]:
            r["sin_datos"] += 1
            continue
        if f and f["min"] > 0:
            r["pj"] += 1; r["g"] += f["g"]; r["a"] += f["a"]; r["min"] += f["min"]
            r["partidos"].append(f"{fx['fixture']['date'][:10]} {fx['teams']['home']['name']}-{fx['teams']['away']['name']} {f['min']}' {f['g']}g {f['a']}a")
    return ligas


for p in config["players"]:
    pid = p["player_id"]
    item = {"seasons_api": ha.api("/players/seasons", {"player": pid}), "raw": {}, "fixtures": {}}
    for s in sorted(item["seasons_api"] or [])[-6:]:
        res = ha.api("/players", {"id": pid, "season": s}) or []
        filas = []
        for r in res:
            for st in r.get("statistics") or []:
                g = st.get("games") or {}
                filas.append({"team": st["team"]["name"], "league": f"{st['league']['id']} {st['league']['name']}",
                              "apps": g.get("appearences"), "lineups": g.get("lineups"), "min": g.get("minutes"),
                              "sub_in": (st.get("substitutes") or {}).get("in"),
                              "g": (st.get("goals") or {}).get("total"), "a": (st.get("goals") or {}).get("assists")})
        item["raw"][str(s)] = filas
    club = [t for t in p["targets"] if t.get("kind") == "club" and t.get("team_id")]
    temporadas = [2026] + ([2025, 2024] if p["key"] in ("sandoval", "camberos", "campillo") else [])
    for t in club:
        for s in temporadas:
            item["fixtures"][f"{t['key']} {s}"] = por_partidos(pid, t["team_id"], s)
    item["guardado"] = (stats["players"].get(p["key"]) or {}).get("seasons")
    out["players"][p["key"]] = item
    ha.log(f"{p['key']}: listo")

ha.save_json(ha.Path("diag_datos.json"), out)

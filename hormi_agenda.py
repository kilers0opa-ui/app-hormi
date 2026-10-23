#!/usr/bin/env python3
"""
Agenda de la semana → agenda.json (sección "Agenda" de la app y el widget de agenda).

Todos los partidos de los equipos de tus jugadores (clubes y Selección) de los últimos 2 días y los próximos 7:
fecha, rivales, escudos, competencia, marcador si ya se jugó y qué jugadores tuyos juegan en cada uno.
La app marca "2º partido de la semana" (equipo con más de un partido en la semana), "Duelo de la Legión"
(tus jugadores en los dos equipos) y las bajas (disponibilidad.json).

Costo: 1 petición por equipo (/fixtures?team&season&from&to), ~14 por corrida.
"""
from datetime import datetime, timedelta

import hormi_alertas as ha

OUT = "agenda.json"
DIAS_ATRAS, DIAS_ADELANTE = 2, 7


def main():
    config = ha.load_config()
    archivados = set(config.get("archivados") or [])
    hoy = datetime.now(ha.LOCAL_TZ).date()
    desde, hasta = hoy - timedelta(days=DIAS_ATRAS), hoy + timedelta(days=DIAS_ADELANTE)
    prev = ha.load_json(ha.Path(OUT), {}) or {}
    prev_por_id = {p["fixture_id"]: p for p in prev.get("partidos") or []}

    equipos = {}   # team_id -> {season, jugadores: [(player_key, target_key)]}
    for p in config["players"]:
        if p["key"] in archivados:
            continue
        for t in p["targets"]:
            if not t.get("team_id"):
                continue
            e = equipos.setdefault(t["team_id"], {"season": t.get("season"), "jug": []})
            e["jug"].append((p["key"], t["key"]))

    partidos = {}
    fallas = 0
    for tid, e in equipos.items():
        res = ha.api("/fixtures", {"team": tid, "season": e["season"] or hoy.year,
                                   "from": desde.isoformat(), "to": hasta.isoformat(), "timezone": "America/Mexico_City"})
        if res is None:
            fallas += 1
            # sin datos de este equipo: se conservan sus partidos anteriores
            for fx in prev.get("partidos") or []:
                if tid in (fx.get("home_id"), fx.get("away_id")):
                    partidos.setdefault(fx["fixture_id"], fx)
            continue
        for fx in res:
            fid = fx["fixture"]["id"]
            st = fx["fixture"]["status"].get("short")
            if st in ("CANC", "ABD", "PST"):
                continue
            g = fx.get("goals") or {}
            h, a = fx["teams"]["home"], fx["teams"]["away"]
            item = partidos.setdefault(fid, {
                "fixture_id": fid,
                "kickoff": datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).isoformat(),
                "home": ha.nombre_es(h["name"]), "away": ha.nombre_es(a["name"]),
                "home_id": h["id"], "away_id": a["id"], "home_logo": h.get("logo"), "away_logo": a.get("logo"),
                "league": ha.liga_es(fx["league"]), "league_logo": fx["league"].get("logo"),
                "status": st, "home_goals": g.get("home"), "away_goals": g.get("away"),
                "penales": ha._penales(fx),
                "jugadores": [],
            })
            lado = "L" if h["id"] == tid else "V"
            for pk, tk in e["jug"]:
                if not any(j["key"] == pk for j in item["jugadores"]):
                    item["jugadores"].append({"key": pk, "team_key": tk, "lado": lado})

    if not partidos and prev_por_id and fallas:
        ha.log("agenda: la API falló; se conserva la anterior")
        return
    out = {"updated_at": datetime.now(ha.LOCAL_TZ).isoformat(), "desde": desde.isoformat(), "hasta": hasta.isoformat(),
           "partidos": sorted(partidos.values(), key=lambda x: x["kickoff"])}
    ha.save_json(ha.Path(OUT), out)
    ha.log(f"agenda.json: {len(out['partidos'])} partidos del {desde} al {hasta} ({len(equipos)} equipos)")


if __name__ == "__main__":
    main()

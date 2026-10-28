#!/usr/bin/env python3
"""
Datos para diseñar y calibrar la "Calificación Legión MX" (nuestra propia calificación por posición).

Junta, de los últimos partidos de los equipos de la app (clubes + Selección), las estadísticas de TODOS los
jugadores de cada partido (/fixtures/players) y la alineación con su lugar en la cancha (/fixtures?ids: "grid",
para distinguir central de lateral y medio de extremo). Escribe calif_datos.json (compacto). Lo que ya está
guardado no se vuelve a pedir.

Costo: 1 petición por equipo (últimos partidos) + 1 por partido nuevo (/fixtures/players) + 1 por cada 20
partidos nuevos (/fixtures?ids). La primera corrida ~200; después, unas cuantas por semana.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import hormi_alertas as ha

OUT = Path("calif_datos.json")
FIN = {"FT", "AET", "PEN"}


def plano(st):
    """Estadísticas de un jugador en un partido, aplanadas: {"shots_total": 3, "passes_accuracy": 84, ...}."""
    out = {}
    for grupo, vals in (st or {}).items():
        if isinstance(vals, dict):
            for k, v in vals.items():
                if v is not None and not isinstance(v, (dict, list)):
                    out[f"{grupo}_{k}"] = v
        elif vals is not None and not isinstance(vals, list):
            out[grupo] = vals   # p. ej. "offsides"
    return out


def main(ultimos):
    config = ha.load_config()
    datos = ha.load_json(OUT, {}) or {}
    partidos = datos.setdefault("partidos", {})
    equipos = {}
    for p in config["players"]:
        for t in p.get("targets") or []:
            if t.get("team_id"):
                equipos[t["team_id"]] = t.get("label")
    nuevos = {}
    for tid, label in equipos.items():
        res = ha.api("/fixtures", {"team": tid, "last": ultimos, "timezone": "America/Mexico_City"}) or []
        for fx in res:
            fid = str(fx["fixture"]["id"])
            if fx["fixture"]["status"].get("short") in FIN and fid not in partidos:
                nuevos[fid] = fx
    ha.log(f"calif: {len(equipos)} equipos · {len(nuevos)} partido(s) nuevo(s) · {len(partidos)} ya guardados")

    # Alineaciones (grid) de los partidos nuevos, de 20 en 20.
    ids = list(nuevos)
    grids = {}
    for i in range(0, len(ids), 20):
        for fx in ha.api("/fixtures", {"ids": "-".join(ids[i:i + 20])}) or []:
            g = {}
            for lu in fx.get("lineups") or []:
                for pl in lu.get("startXI") or []:
                    j = pl.get("player") or {}
                    if j.get("id"):
                        g[str(j["id"])] = {"grid": j.get("grid"), "pos": j.get("pos"), "formacion": lu.get("formation")}
            grids[str(fx["fixture"]["id"])] = g

    for fid, fx in nuevos.items():
        res = ha.api("/fixtures/players", {"fixture": fid})
        if not res:
            continue
        h, a = fx["teams"]["home"], fx["teams"]["away"]
        filas = []
        for t in res:
            tid = (t.get("team") or {}).get("id")
            for pl in t.get("players") or []:
                st = (pl.get("statistics") or [{}])[0]
                if not (st.get("games") or {}).get("minutes"):
                    continue
                pid = str(pl["player"]["id"])
                filas.append({"pid": pid, "nombre": pl["player"].get("name"), "team_id": tid,
                              **(grids.get(fid, {}).get(pid) or {}), **plano(st)})
        partidos[fid] = {
            "fecha": fx["fixture"].get("date"), "liga": (fx.get("league") or {}).get("name"),
            "home_id": h["id"], "away_id": a["id"], "home": h["name"], "away": a["name"],
            "goles_home": (fx.get("goals") or {}).get("home"), "goles_away": (fx.get("goals") or {}).get("away"),
            "jugadores": filas,
        }
    datos["updated_at"] = datetime.now(ha.LOCAL_TZ).isoformat()
    OUT.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ha.log(f"calif_datos.json: {len(partidos)} partidos · {sum(len(p['jugadores']) for p in partidos.values())} filas")


if __name__ == "__main__":
    from hormi_pausa import salir_si_pausa
    salir_si_pausa()
    ap = argparse.ArgumentParser()
    ap.add_argument("--ultimos", type=int, default=15)
    main(ap.parse_args().ultimos)

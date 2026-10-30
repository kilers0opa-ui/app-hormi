#!/usr/bin/env python3
"""Desglose de la Calificación Legión MX de TODOS los jugadores de un partido (para revisar la fórmula).
Uso: python hormi_calif_partido.py <fixture_id>  → escribe calif_partido.txt"""
import sys
from pathlib import Path

import hormi_alertas as ha
import hormi_calif_legion as CL

fid = sys.argv[1]
res = ha.api("/fixtures/players", {"fixture": fid}) or []
filas = []
for t in res:
    tid, equipo = t["team"]["id"], t["team"]["name"]
    f, c = CL._marcador(res, tid)
    for pl in t.get("players") or []:
        st = (pl.get("statistics") or [{}])[0]
        if CL._g(st, "games", "minutes") < CL.MIN_MINUTOS:
            continue
        rol = CL.rol_de_api((st.get("games") or {}).get("position"))
        ctx = {"goles_favor": f, "goles_contra": c}
        r = CL.calcular(st, rol, ctx)
        if r is None:
            continue
        d = CL.desglose(st, rol, ctx)
        filas.append((r, equipo, pl["player"]["name"], rol, st, d))
filas.sort(key=lambda x: -x[0])
out = []
for r, eq, nom, rol, st, d in filas[:8]:
    g = st.get("goals") or {}; sh = st.get("shots") or {}; pa = st.get("passes") or {}; du = st.get("duels") or {}
    dr = st.get("dribbles") or {}
    out.append(f"{r:4.1f}  {nom} ({eq}, {rol}, {st['games'].get('minutes')}') · G {g.get('total') or 0} A {g.get('assists') or 0} · "
               f"tiros {sh.get('on') or 0}/{sh.get('total') or 0} · pases {pa.get('total')} ({pa.get('accuracy')}) clave {pa.get('key') or 0} · "
               f"duelos {du.get('won') or 0}/{du.get('total') or 0} · regates {dr.get('success') or 0}/{dr.get('attempts') or 0} · api {st['games'].get('rating')}")
    out.append("      " + " · ".join(f"{k} {v:+.2f}" for k, v in d if abs(v) >= 0.05))
Path("calif_partido.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))

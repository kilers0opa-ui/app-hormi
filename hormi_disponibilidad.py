#!/usr/bin/env python3
"""
Disponibilidad: lesiones y suspensiones de los jugadores para su PRÓXIMO partido con su club → disponibilidad.json.

Por equipo: /fixtures?team&next=1 (el próximo partido) + /injuries?fixture (quién no va o está en duda).
≈ 2 peticiones por equipo (≈ 26 al día por corrida). La app pone la etiqueta en Inicio, Legión y la agenda.

Avisos (tipo "disponibilidad"), comparando con la corrida anterior:
  - 🚑 "Raúl Jiménez: baja por lesión" (rodilla · se pierde Wolves vs Everton)
  - 🟥 "César Montes: suspendido" (acumulación de amarillas · ...)
  - ❓ "X: en duda para el partido" (la API lo marca "Questionable")
  - ✅ "X ya está disponible" (deja de aparecer en la lista de su siguiente partido)
La primera corrida (sin disponibilidad.json) no avisa: solo arma el estado.
"""
import argparse
from datetime import datetime
from pathlib import Path

import hormi_alertas as ha

OUT = Path("disponibilidad.json")

# Motivos de la API (en inglés) → español. Lo que no esté aquí se muestra tal cual.
ZONAS = {
    "knee": "rodilla", "hamstring": "isquiotibiales", "ankle": "tobillo", "muscle": "muscular", "groin": "ingle",
    "calf": "pantorrilla", "thigh": "muslo", "foot": "pie", "back": "espalda", "hip": "cadera", "shoulder": "hombro",
    "achilles": "tendón de Aquiles", "adductor": "aductor", "head": "cabeza", "concussion": "conmoción",
    "ligament": "ligamento", "cruciate": "ligamento cruzado", "meniscus": "menisco", "toe": "dedo del pie",
    "hand": "mano", "wrist": "muñeca", "arm": "brazo", "rib": "costilla", "face": "cara", "nose": "nariz",
    "illness": "enfermedad", "virus": "enfermedad", "fever": "fiebre", "fatigue": "fatiga", "knock": "golpe",
    "strain": "distensión", "fracture": "fractura", "surgery": "cirugía", "bruise": "contusión",
}


def clasificar(tipo, motivo):
    """("lesion" | "suspension" | "duda" | None, texto en español)."""
    m = (motivo or "").lower()
    if any(w in m for w in ("suspend", "red card", "yellow card", "ban")):
        if "yellow" in m:
            return "suspension", "acumulación de amarillas"
        if "red" in m:
            return "suspension", "tarjeta roja"
        return "suspension", "suspendido"
    if any(w in m for w in ("coach", "inactive", "rest", "personal", "international duty", "loan")):
        return None, ""   # no es lesión ni castigo: no se avisa
    zona = next((v for k, v in ZONAS.items() if k in m), None)
    texto = zona or (motivo or "lesión")
    return ("duda" if (tipo or "").lower() == "questionable" else "lesion"), texto


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-avisos", action="store_true")
    args = ap.parse_args()
    config = ha.load_config()
    archivados = set(config.get("archivados") or [])
    prev = ha.load_json(OUT, None)
    primera = prev is None
    antes = (prev or {}).get("players") or {}
    out = {"updated_at": datetime.now(ha.LOCAL_TZ).isoformat(), "players": {}}
    por_equipo = {}
    avisos = []

    for p in config["players"]:
        if p["key"] in archivados:
            continue
        t = next((x for x in p["targets"] if x.get("kind") != "seleccion" and x.get("team_id")), None)
        if not t:
            continue
        tid = t["team_id"]
        if tid not in por_equipo:
            nxt = ha.api("/fixtures", {"team": tid, "next": 1})
            fx = nxt[0] if nxt else None
            lista = None if nxt is None else (ha.api("/injuries", {"fixture": fx["fixture"]["id"]}) if fx else [])
            por_equipo[tid] = (fx, lista)
        fx, lista = por_equipo[tid]
        if lista is None:   # error de la API: se conserva lo anterior de este jugador
            if p["key"] in antes:
                out["players"][p["key"]] = antes[p["key"]]
            continue
        if not fx:
            continue
        mio = next((x for x in lista if (x.get("player") or {}).get("id") == p.get("player_id")), None)
        if not mio:
            continue
        estado, texto = clasificar((mio.get("player") or {}).get("type"), (mio.get("player") or {}).get("reason"))
        if not estado:
            continue
        partido = f"{ha.nombre_es(fx['teams']['home']['name'])} vs {ha.nombre_es(fx['teams']['away']['name'])}"
        out["players"][p["key"]] = {
            "estado": estado, "motivo": texto, "motivo_api": (mio.get("player") or {}).get("reason"),
            "partido": partido, "fixture_id": fx["fixture"]["id"],
            "kickoff": datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).isoformat(),
            "team_key": t["key"],
        }

    # ── avisos
    if not primera:
        for key, d in out["players"].items():
            a = antes.get(key)
            if a and a.get("estado") == d["estado"]:
                continue   # ya se avisó
            p = next(x for x in config["players"] if x["key"] == key)
            nombre = ha.display_name(p)
            if d["estado"] == "lesion":
                avisos.append((key, d["team_key"], f"🚑 {nombre}: baja por lesión", f"{d['motivo'].capitalize()} · se pierde {d['partido']}"))
            elif d["estado"] == "suspension":
                avisos.append((key, d["team_key"], f"🟥 {nombre}: suspendido", f"{d['motivo'].capitalize()} · se pierde {d['partido']}"))
            else:
                avisos.append((key, d["team_key"], f"❓ {nombre}: en duda para el partido", f"{d['motivo'].capitalize()} · {d['partido']}"))
        for key, a in antes.items():
            if key not in out["players"] and key not in archivados and a.get("estado") in ("lesion", "suspension"):
                p = next((x for x in config["players"] if x["key"] == key), None)
                if p:
                    avisos.append((key, a.get("team_key"), f"✅ {ha.display_name(p)} ya está disponible",
                                   "Ya no aparece en la lista de bajas de su próximo partido"))

    ha.save_json(OUT, out)
    ha.log(f"disponibilidad.json: {len(out['players'])} con baja/duda · {len(avisos)} aviso(s)"
           + (" · primera corrida, sin avisos" if primera else ""))
    if avisos and not args.sin_avisos:
        fcm = ha.FcmSender(config.get("fcm_project_id"))
        for key, tkey, title, body in avisos:
            try:
                fcm.send("disponibilidad", title, body, key, tkey)
            except Exception as err:
                ha.log(f"⚠️  no se pudo mandar el aviso de disponibilidad ({key}): {err}")


if __name__ == "__main__":
    main()

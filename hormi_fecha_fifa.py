#!/usr/bin/env python3
"""
Modo Selección → fecha_fifa.json (pestaña "Fecha FIFA" en Selección Mexicana y tarjeta en Inicio).

Toma la convocatoria vigente (hormi_convocatoria.json → seleccion: desde/hasta/convocados) y arma, para cada
partido de México en esas fechas, los minutos, goles, asistencias y si fue titular cada jugador de la app.

  "activa": true desde 15 días antes del primer partido hasta 1 día después del último → la app muestra la
            tarjeta en Inicio. Fuera de esas fechas se conserva la última fecha (la pestaña la sigue mostrando).

Costo: 1 petición por corrida (/fixtures de México en esas fechas) solo mientras la fecha está activa, más 1 por
partido terminado la primera vez (/fixtures/players; después queda guardado) y 1 por partido en vivo.
"""
from datetime import date, datetime, timedelta

import hormi_alertas as ha

OUT = ha.Path("fecha_fifa.json")
MEXICO = 16
EN_VIVO = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}


def _fecha(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def main():
    config = ha.load_config()
    jugadores = {p["player_id"]: p["key"] for p in config["players"] if p.get("player_id")}
    conv = (ha.load_json(ha.Path("hormi_convocatoria.json"), {}) or {}).get("seleccion") or {}
    prev = ha.load_json(OUT, {}) or {}
    desde, hasta = _fecha(conv.get("desde")), _fecha(conv.get("hasta"))
    hoy = datetime.now(ha.LOCAL_TZ).date()
    if not desde or not hasta:
        ha.log("fecha FIFA: no hay convocatoria con fechas; se conserva lo anterior")
        return
    activa = desde - timedelta(days=15) <= hoy <= hasta + timedelta(days=1)
    misma = prev.get("desde") == conv.get("desde") and prev.get("hasta") == conv.get("hasta")
    previos = {p["fixture_id"]: p for p in (prev.get("partidos") or [])} if misma else {}
    terminados = all(p.get("status") in ha.FINISHED for p in previos.values()) and previos

    out = {"updated_at": datetime.now(ha.LOCAL_TZ).isoformat(), "activa": activa,
           "desde": conv.get("desde"), "hasta": conv.get("hasta"), "motivo": conv.get("motivo") or "",
           "convocados": conv.get("convocados") or [], "dudosos": conv.get("dudosos") or [],
           "partidos": list(previos.values())}

    if activa or not (misma and terminados):
        fxs = []
        for season in sorted({desde.year, hasta.year}):   # selecciones: temporada = año calendario
            res = ha.api("/fixtures", {"team": MEXICO, "season": season, "from": desde.isoformat(),
                                       "to": hasta.isoformat(), "timezone": "America/Mexico_City"})
            if res is None:
                fxs = None
                break
            fxs += res
        if fxs is None:
            ha.log("fecha FIFA: la API no respondió; se conserva lo anterior")
            if prev:
                prev["activa"] = activa
                ha.save_json(OUT, prev)
            return
        partidos = []
        for fx in sorted(fxs, key=lambda f: f["fixture"]["timestamp"]):
            fid = fx["fixture"]["id"]
            st = fx["fixture"]["status"].get("short")
            if st in ("CANC", "ABD", "PST"):
                continue
            h, a = fx["teams"]["home"], fx["teams"]["away"]
            local = h["id"] == MEXICO
            rival = a if local else h
            g = fx.get("goals") or {}
            item = {"fixture_id": fid, "kickoff": datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).isoformat(),
                    "rival": ha.nombre_es(rival["name"]), "rival_logo": rival.get("logo"), "local": local,
                    "competencia": ha.liga_es(fx["league"]), "status": st,
                    "goles_mx": g.get("home") if local else g.get("away"),
                    "goles_rival": g.get("away") if local else g.get("home"),
                    "penales": ha._penales(fx), "jugadores": {}}
            viejo = previos.get(fid)
            if viejo and viejo.get("status") in ha.FINISHED and viejo.get("jugadores") is not None and st in ha.FINISHED:
                item["jugadores"] = viejo["jugadores"]
            elif st in ha.FINISHED or st in EN_VIVO:
                res = ha.api("/fixtures/players", {"fixture": fid, "team": MEXICO})
                if res is None and viejo:
                    item["jugadores"] = viejo.get("jugadores") or {}
                for t in res or []:
                    for pl in t.get("players") or []:
                        key = jugadores.get((pl.get("player") or {}).get("id"))
                        if not key:
                            continue
                        s = (pl.get("statistics") or [{}])[0]
                        gm = s.get("games") or {}
                        gl = s.get("goals") or {}
                        try:   # Calificación Legión MX (la nuestra); si no se puede, la de la API.
                            import hormi_calif_legion as CL
                            nuestra = CL.calcular(s, CL.ROL_JUGADOR.get(key) or CL.rol_de_api(gm.get("position")),
                                                  {"goles_favor": item["goles_mx"], "goles_contra": item["goles_rival"]})
                        except Exception:
                            nuestra = None
                        item["jugadores"][key] = {
                            "min": gm.get("minutes") or 0, "titular": gm.get("substitute") is False,
                            "goles": gl.get("total") or 0, "asist": gl.get("assists") or 0,
                            "rating": nuestra or gm.get("rating"),
                        }
            partidos.append(item)
        out["partidos"] = partidos

    tot = {"goles": 0, "asist": 0, "min": 0}
    for p in out["partidos"]:
        for j in (p.get("jugadores") or {}).values():
            tot["goles"] += j.get("goles") or 0
            tot["asist"] += j.get("asist") or 0
            tot["min"] += j.get("min") or 0
    out["totales"] = tot
    if {k: v for k, v in prev.items() if k != "updated_at"} == {k: v for k, v in out.items() if k != "updated_at"}:
        ha.log("fecha FIFA: sin cambios")
        return
    ha.save_json(OUT, out)
    ha.log(f"fecha_fifa.json: {len(out['partidos'])} partido(s) · activa={activa} · {tot}")


if __name__ == "__main__":
    from hormi_pausa import salir_si_pausa
    salir_si_pausa()
    main()

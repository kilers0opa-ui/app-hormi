#!/usr/bin/env python3
"""
Previa del partido → previa.json (botón "Previa" en la tarjeta del próximo partido: aparece 24 h antes y se
quita 5 min antes del inicio; eso lo decide la app con el kickoff).

Para cada partido de tus jugadores que empieza en las próximas 30 h (sale de agenda.json):
  - tabla: lugar y puntos de los dos equipos (team_standings.json, sin gastar peticiones) si juegan la misma liga;
  - últimos 5 de cada equipo (G/E/P, el más reciente a la derecha);
  - historial entre ellos (últimos 5 enfrentamientos);
  - cómo llega cada jugador tuyo: rachas (rachas.json) y minutos en los últimos 5 de su equipo (rachas_cache.json).

Costo: 3 peticiones por partido (2 × últimos 5 + historial), una sola vez por partido (se guarda 12 h).
"""
from datetime import datetime, timedelta

import hormi_alertas as ha

OUT = ha.Path("previa.json")
FIN = {"FT", "AET", "PEN"}


def forma(fixtures, tid):
    """[{"r": "G"|"E"|"P", "rival", "marcador", "fid"}] del más viejo al más reciente."""
    out = []
    for fx in sorted(fixtures or [], key=lambda f: f["fixture"]["timestamp"]):
        if fx["fixture"]["status"].get("short") not in FIN:
            continue
        h, a = fx["teams"]["home"], fx["teams"]["away"]
        g = fx.get("goals") or {}
        local = h["id"] == tid
        mios, suyos = (g.get("home"), g.get("away")) if local else (g.get("away"), g.get("home"))
        if mios is None or suyos is None:
            continue
        r = "G" if mios > suyos else "E" if mios == suyos else "P"
        pen = ha._penales(fx)
        if r == "E" and pen:   # definido en penales
            a_, b_ = (int(x) for x in pen.split("-"))
            r = "G" if (a_ > b_) == local else "P"
        out.append({"r": r, "rival": ha.nombre_es((a if local else h)["name"]), "marcador": f"{mios}-{suyos}",
                    "fid": fx["fixture"]["id"], "local": local})
    return out[-5:]


def tabla(standings, home_id, away_id):
    for t in (standings.get("teams") or {}).values():
        for grupo in t.get("standings") or []:
            filas = {f.get("team_id"): f for f in grupo.get("table") or []}
            if home_id in filas and away_id in filas:
                fh, fa = filas[home_id], filas[away_id]
                return {"liga": grupo.get("league_name") or "",
                        "home": {"rank": fh.get("rank"), "pts": fh.get("points"), "pj": fh.get("played")},
                        "away": {"rank": fa.get("rank"), "pts": fa.get("points"), "pj": fa.get("played")}}
    return None


def main():
    config = ha.load_config()
    jugadores = {p["key"]: p for p in config["players"]}
    agenda = ha.load_json(ha.Path("agenda.json"), {}) or {}
    standings = ha.load_json(ha.Path("team_standings.json"), {}) or {}
    rachas = (ha.load_json(ha.Path("rachas.json"), {}) or {}).get("players") or {}
    cache_min = ha.load_json(ha.Path("rachas_cache.json"), {}) or {}
    prev = ha.load_json(OUT, {}) or {}
    ahora = datetime.now(ha.LOCAL_TZ)
    out = {"updated_at": ahora.isoformat(), "partidos": {}, "jugadores": {}}
    nuevas = 0
    for p in agenda.get("partidos") or []:
        try:
            ko = datetime.fromisoformat(p["kickoff"])
        except Exception:
            continue
        if not (ahora - timedelta(minutes=10) <= ko <= ahora + timedelta(hours=30)) or p.get("status") not in (None, "NS", "TBD"):
            continue
        fid = str(p["fixture_id"])
        viejo = (prev.get("partidos") or {}).get(fid)
        if viejo and viejo.get("v") == 2 and (ha._parse(viejo.get("hecho")) or ahora - timedelta(days=1)) > ahora - timedelta(hours=12):
            item = viejo
        else:
            hid, aid = p.get("home_id"), p.get("away_id")
            ult_h = ha.api("/fixtures", {"team": hid, "last": 8})
            ult_a = ha.api("/fixtures", {"team": aid, "last": 8})
            h2h = ha.api("/fixtures/headtohead", {"h2h": f"{hid}-{aid}", "last": 5})
            if ult_h is None or ult_a is None:
                if viejo:
                    out["partidos"][fid] = viejo
                continue
            hist = []
            for fx in sorted(h2h or [], key=lambda f: f["fixture"]["timestamp"], reverse=True):
                if fx["fixture"]["status"].get("short") not in FIN:
                    continue
                g = fx.get("goals") or {}
                hist.append({"fecha": datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).date().isoformat(),
                             "home": ha.nombre_es(fx["teams"]["home"]["name"]), "away": ha.nombre_es(fx["teams"]["away"]["name"]),
                             "home_id": fx["teams"]["home"]["id"], "marcador": f"{g.get('home')}-{g.get('away')}",
                             "ganador": (fx["teams"]["home"]["id"] if fx["teams"]["home"].get("winner")
                                         else fx["teams"]["away"]["id"] if fx["teams"]["away"].get("winner") else None)})
            item = {"fixture_id": p["fixture_id"], "kickoff": p["kickoff"], "home": p["home"], "away": p["away"],
                    "home_id": hid, "away_id": aid, "home_logo": p.get("home_logo"), "away_logo": p.get("away_logo"),
                    "league": p.get("league"), "tabla": tabla(standings, hid, aid),
                    "forma_home": forma(ult_h, hid), "forma_away": forma(ult_a, aid), "historial": hist[:5],
                    "hecho": ahora.isoformat(), "v": 2}
            nuevas += 1
        # cómo llega cada jugador (siempre con lo más reciente de rachas)
        item["jugadores"] = []
        for j in p.get("jugadores") or []:
            pl = jugadores.get(j["key"])
            if not pl:
                continue
            tid = p.get("home_id") if j.get("lado") == "L" else p.get("away_id")
            r = rachas.get(j["key"]) or {}
            rach = {x["id"]: x["actual"] for x in r.get("rachas") or []}
            forma_eq = item["forma_home"] if j.get("lado") == "L" else item["forma_away"]
            minutos = []
            for fm in forma_eq:
                fila = (cache_min.get(f"{fm['fid']}:{tid}") or {}).get(str(pl.get("player_id")))
                minutos.append({"rival": fm["rival"], "min": (fila or {}).get("min", 0) if f"{fm['fid']}:{tid}" in cache_min else None,
                                "goles": (fila or {}).get("goles", 0)})
            item["jugadores"].append({"key": j["key"], "lado": j.get("lado"),
                                      "rachas": {k: v for k, v in rach.items() if k in ("jugando", "titular", "gol", "ga", "cero") and v >= 2},
                                      "minutos": minutos})
            out["jugadores"][j["key"]] = p["fixture_id"]
        out["partidos"][fid] = item
    ha.save_json(OUT, out)
    ha.log(f"previa.json: {len(out['partidos'])} partido(s) en las próximas 30 h · {nuevas} nueva(s)")


if __name__ == "__main__":
    from hormi_pausa import salir_si_pausa
    salir_si_pausa()
    main()

#!/usr/bin/env python3
"""Último partido en el que cada jugador tuvo minutos (de su club o de la Selección, el más reciente).
Guarda players.<jugador>.ultimo_partido en player_stats.json, que es lo que muestra la app en
"Último partido". En vivo lo escribe hormi_alertas.registrar_ultimo() al terminar cada partido; este
script lo calcula con la API para los partidos anteriores y lo repara a diario (lo llama
hormi_historial.py, y también se puede correr solo).

Costo: 1 petición por objetivo (últimos partidos del equipo) y 1 por partido revisado hasta encontrar
uno con minutos. Si el último partido terminado de todos los objetivos ya es el guardado, no hace nada."""
import sys
from datetime import datetime

import hormi_alertas as ha
import hormi_historial as hh

FINISHED_SHORT = {"FT", "AET", "PEN"}


def _candidatos(player, target):
    """Últimos partidos terminados del equipo, el más reciente primero."""
    res = hh.api("/fixtures", {"team": target["team_id"], "last": 6}) or []
    fin = [f for f in res if f["fixture"]["status"]["short"] in FINISHED_SHORT]
    return sorted(fin, key=lambda f: f["fixture"]["timestamp"], reverse=True)


def _participacion(player, target, fx):
    """(minutos, goles, asistencias) del jugador en ese partido, o None si no jugó."""
    res = hh.api("/fixtures/players", {"fixture": fx["fixture"]["id"], "team": target["team_id"]}) or []
    for team in res:
        for p in team.get("players") or []:
            if p["player"]["id"] != player["player_id"]:
                continue
            st = (p.get("statistics") or [{}])[0]
            mins = (st.get("games") or {}).get("minutes") or 0
            if mins > 0:
                return (mins, (st.get("goals") or {}).get("total") or 0, (st.get("goals") or {}).get("assists") or 0)
    return None


def actualizar(config, solo=None):
    stats = hh.load_json(hh.STATS_FILE, {"updated_at": None, "players": {}})
    stats.setdefault("players", {})
    cambios = 0
    for player in config["players"]:
        if solo and player["key"] != solo:
            continue
        entry = stats["players"].setdefault(player["key"], {"seasons": {}, "applied_fixtures": {}})
        mejor = entry.get("ultimo_partido")
        for target in player["targets"]:
            cands = _candidatos(player, target)
            if not cands:
                continue
            if mejor and mejor.get("fixture_id") == cands[0]["fixture"]["id"]:
                continue   # ya es ese
            for fx in cands[:4]:
                ko = datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).isoformat()
                if mejor and ko <= (mejor.get("kickoff") or ""):
                    break   # los que siguen son más viejos que el guardado
                part = _participacion(player, target, fx)
                if not part:
                    continue
                g = fx.get("goals") or {}
                mejor = {
                    "fixture_id": fx["fixture"]["id"], "kickoff": ko,
                    "home": ha.nombre_es(fx["teams"]["home"]["name"]), "away": ha.nombre_es(fx["teams"]["away"]["name"]),
                    "home_logo": fx["teams"]["home"].get("logo"), "away_logo": fx["teams"]["away"].get("logo"),
                    "home_goals": g.get("home"), "away_goals": g.get("away"),
                    "league": ha.liga_es(fx["league"]["name"]),
                    "team_key": target["key"], "team_label": target["label"], "emoji": target["emoji"],
                    "crest_url": target.get("crest_url"),
                    "minutes": part[0], "goals": part[1], "assists": part[2],
                }
                break
        if mejor and mejor != entry.get("ultimo_partido"):
            entry["ultimo_partido"] = mejor
            cambios += 1
            hh.log(f"🕘 {player['name']}: último partido {mejor['home']} {mejor['home_goals']}-{mejor['away_goals']} {mejor['away']} ({mejor['minutes']} min)")
    if cambios:
        stats["updated_at"] = datetime.now(ha.LOCAL_TZ).isoformat()
        hh.save_json(hh.STATS_FILE, stats)
    return cambios


if __name__ == "__main__":
    cfg = hh.load_json(hh.CONFIG_FILE, None)
    if cfg is None:
        sys.exit("Falta hormi_config.json")
    n = actualizar(cfg, sys.argv[1] if len(sys.argv) > 1 else None)
    hh.log(f"✅ último partido: {n} jugador(es) actualizados")

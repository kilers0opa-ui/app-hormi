#!/usr/bin/env python3
"""
EuroGoalMX · Fase 4 — Tabla de posiciones (team_standings.json)
=================================================================
Para cada jugador con un club (los targets "kind": "club" de
hormi_config.json — no "seleccion"), descubre en qué competiciones juega
ESE equipo ahora mismo (/leagues?team=<team_id>, sin necesitar mantener a
mano una lista de league_id por equipo — igual de "autodescubierto" que ya
son team_id y player_id en las otras fases) y para cada una que sí tenga
tabla (una liga normal, o una copa continental con fase de liga como la
Europa League desde el formato nuevo) guarda el lugar del equipo: rank,
puntos, PJ, G/E/P, diferencia de gol, forma y en qué fase/grupo va.

Una copa de eliminación directa (Copa de Grecia, etc.) no tiene tabla —
API-Football simplemente no devuelve nada para esas, así que esas
competiciones quedan fuera solas, sin necesidad de saber de antemano
cuáles son copa y cuáles son liga.

Qué escribe: team_standings.json — un archivo aparte de player_stats.json
(las estadísticas del JUGADOR) porque esto es del EQUIPO, no depende de
que el jugador tenga player_id ni de que haya jugado minutos esa
temporada. Cada corrida reemplaza por completo lo que sí trajo
respuesta; lo que no responda (plan gratuito, o esa competición ya
terminó/no aplica) se deja tal cual estaba.

Uso:
  export APIFOOTBALL_KEY="tu_api_key"
  python hormi_tabla.py                  # todos los jugadores con club
  python hormi_tabla.py --jugador raul    # solo uno

Pensado para correr 1x/día desde GitHub Actions (ver
.github/workflows/tabla.yml), en un horario distinto al de Fase 3 para no
juntar de golpe las peticiones de ambas en el mismo minuto.

Requisitos: Python 3.9+, sin librerías externas. Variable APIFOOTBALL_KEY.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "https://v3.football.api-sports.io"
CONFIG_FILE = Path("hormi_config.json")
STANDINGS_FILE = Path("team_standings.json")

LOCAL_TZ = timezone(timedelta(hours=-6))
MIN_SECONDS_BETWEEN_REQUESTS = 0.3         # plan Pro: 300 peticiones/min (en el gratuito eran 10/min → 6.5 s)
CURRENT_YEAR = 2026

_last_request_at = 0.0


def log(msg):
    print(f"[{datetime.now(LOCAL_TZ):%H:%M:%S}] {msg}")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def api(endpoint, params):
    global _last_request_at
    api_key = os.environ.get("APIFOOTBALL_KEY")
    if not api_key:
        sys.exit("Falta la variable de entorno APIFOOTBALL_KEY.")
    url = f"{BASE_URL}{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
    for attempt in (1, 2):
        wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.time() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        try:
            _last_request_at = time.time()
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt == 1:
                log("     límite por minuto alcanzado, esperando 60 s...")
                time.sleep(60)
                continue
            log(f"     ⚠️  HTTP {err.code} en {endpoint}: {err}")
            return None
        except Exception as err:
            log(f"     ⚠️  fallo de red en {endpoint}: {err}")
            return None
    if data.get("errors"):
        log(f"     ⚠️  la API respondió con error: {data['errors']}")
        return None
    return data.get("response", [])


def current_leagues_for_team(team_id):
    """Ligas/copas en las que compite ESTE equipo ahora mismo (temporada
    CURRENT_YEAR marcada como 'current' por la propia API) — así no hace
    falta mantener a mano qué league_id le toca a cada equipo, ni
    enterarse a mano cuando un equipo cambia de división o de copa."""
    data = api("/leagues", {"team": team_id})
    if not data:
        return []
    leagues = []
    for entry in data:
        league = entry.get("league") or {}
        for season in entry.get("seasons") or []:
            if season.get("year") == CURRENT_YEAR and season.get("current"):
                leagues.append({"id": league.get("id"), "name": league.get("name")})
                break
    return leagues


def standings_row_for(league_id, season, team_id):
    """La fila de ESTE equipo en la tabla de esa liga/temporada — None si
    la competición no tiene tabla (copa de eliminación directa) o si el
    plan actual no cubre esa temporada."""
    data = api("/standings", {"league": league_id, "season": season})
    if not data:
        return None
    for entry in data:
        league = entry.get("league") or {}
        groups = league.get("standings") or []
        total_teams = sum(len(g) for g in groups)
        for group in groups:
            for row in group:
                if (row.get("team") or {}).get("id") != team_id:
                    continue
                all_stats = row.get("all") or {}
                goals = all_stats.get("goals") or {}
                return {
                    "season": season,
                    "group": row.get("group"),
                    "rank": row.get("rank"),
                    "points": row.get("points"),
                    "played": all_stats.get("played"),
                    "win": all_stats.get("win"),
                    "draw": all_stats.get("draw"),
                    "lose": all_stats.get("lose"),
                    "goals_for": goals.get("for"),
                    "goals_against": goals.get("against"),
                    "goals_diff": row.get("goalsDiff"),
                    "form": row.get("form"),
                    "status": row.get("status"),
                    "description": row.get("description"),
                    "total_teams": total_teams,
                }
    return None


def build_team_standings(target):
    team_id = target.get("team_id")
    if not team_id:
        return None
    leagues = current_leagues_for_team(team_id)
    if not leagues:
        log(f"    sin ligas 'current' para team_id {team_id} todavía (¿el plan no cubre {CURRENT_YEAR}?)")
        return None
    result = []
    # temporadas a intentar, de la más nueva a la más vieja que el plan
    # gratuito sí suele cubrir — así hoy mismo se ve algo (aunque sea de
    # 2024) y en cuanto se pague Pro empieza a traer la de verdad sin
    # tocar nada.
    seasons_to_try = [CURRENT_YEAR, CURRENT_YEAR - 1, CURRENT_YEAR - 2]
    for league in leagues:
        league_id = league["id"]
        row = None
        used_season = None
        for season in seasons_to_try:
            row = standings_row_for(league_id, season, team_id)
            if row is not None:
                used_season = season
                break
        if row is None:
            log(f"    {league['name']}: sin tabla (¿es de eliminación directa, o el plan aún no cubre ninguna temporada probada?)")
            continue
        row["league_id"] = league_id
        row["league_name"] = league["name"]
        log(f"    {league['name']} {used_season}: lugar {row['rank']}/{row['total_teams']} · {row['points']} pts")
        result.append(row)
    return result or None


def main():
    parser = argparse.ArgumentParser(description="Fase 4 · Tabla de posiciones")
    parser.add_argument("--jugador", help="Limita a un jugador por su 'key'")
    args = parser.parse_args()

    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit(f"Falta {CONFIG_FILE}.")

    players = config["players"]
    if args.jugador:
        players = [p for p in players if p["key"] == args.jugador]
        if not players:
            sys.exit(f"No hay jugador con key='{args.jugador}' en {CONFIG_FILE}.")

    standings = load_json(STANDINGS_FILE, {"updated_at": None, "teams": {}})
    standings.setdefault("teams", {})

    for player in players:
        club_targets = [t for t in player["targets"] if t.get("kind") == "club"]
        if not club_targets:
            continue
        target = club_targets[0]
        log(f"🏆 {player['name']} · {target['label']}")
        rows = build_team_standings(target)
        if rows is None:
            continue
        standings["teams"][target["key"]] = {
            "label": target["label"],
            "player_key": player["key"],
            "standings": rows,
        }

    standings["updated_at"] = datetime.now(LOCAL_TZ).isoformat()
    save_json(STANDINGS_FILE, standings)
    log(f"✅ listo · {STANDINGS_FILE} actualizado")


if __name__ == "__main__":
    main()

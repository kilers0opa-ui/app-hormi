#!/usr/bin/env python3
"""
EuroGoalMX · Fase 3 — Historial real por competición (player_stats.json)
=========================================================================
Arma el histórico real de cada jugador — goles, asistencias y minutos por
temporada y competición — llamando a /players?id=<player_id>&season=<año>
de API-Football una vez por cada año de "career_seasons" (hormi_config.json).

Ese endpoint ya trae, por temporada, el/los club(es) con los que jugó esa
temporada y sus estadísticas por cada competición en la que participó — así
se cubre el historial completo (Raúl con América/Atlético/Benfica/
Wolves/Fulham, Quiñones con Venados/Tigres/Lobos BUAP/Atlas/América/
Al-Qadsiah, etc.) sin tener que mantener a mano una lista de equipos
anteriores: basta con darle los años en career_seasons. También trae la URL
del escudo real de cada equipo/competición (team.logo / league.logo,
hospedados en media.api-sports.io — gratis en cualquier plan), así que la
app puede mostrar el escudo correcto de cualquier equipo, incluso uno que
nunca le dimos artwork a mano.

Qué escribe: player_stats.json — lo que lee la pantalla de Estadísticas de
la app (StatsScreen.kt vía StatsViewModel). Cada corrida REEMPLAZA por
completo los datos de las temporadas que sí trajeron respuesta de la API
(es la fuente de verdad); si una temporada no trae nada (plan gratuito no
la cubre todavía, préstamo raro, o la API tronó), se deja tal cual estaba
de la corrida anterior — nunca se borra lo bueno por un error puntual o por
un plan que aún no alcanza esos años.

hormi_alertas.py (el chequeo de cada 5 min) hace un ajuste "en vivo" aparte
en esta misma player_stats.json, sumando gol a gol apenas termina cada
partido, para no depender de que esta Fase 3 corra a cada rato. Ese ajuste
queda pisado (correctamente) la próxima vez que esta Fase 3 vuelva a traer
el total real y ya actualizado de la API — por diseño no hay doble conteo:
Fase 3 siempre reemplaza, nunca suma.

Uso:
  export APIFOOTBALL_KEY="tu_api_key"
  python hormi_historial.py                     # todos los jugadores
  python hormi_historial.py --jugador raul       # solo uno
  python hormi_historial.py --jugador raul --temporada 2019   # un solo año (pruebas)

Pensado para correr una vez al día desde GitHub Actions (ver
.github/workflows/historial.yml) — NO desde el chequeo de cada 5 min: cada
jugador gasta 1 petición por año en career_seasons (Raúl solo son ~16), así
que conviene una cadencia baja para no competir por cuota con el chequeo en
vivo (100 peticiones/día en el plan gratuito).

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
STATS_FILE = Path("player_stats.json")

LOCAL_TZ = timezone(timedelta(hours=-6))   # hora del centro de México
MIN_SECONDS_BETWEEN_REQUESTS = 0.3         # plan Pro: 300 peticiones/min (en el gratuito eran 10/min → 6.5 s)
_last_request_at = 0.0
last_api_error = None


def log(msg):
    print(f"[{datetime.now(LOCAL_TZ):%H:%M:%S}] {msg}")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def api(endpoint, params):
    """GET a API-Football. Sin caché a propósito (a diferencia de la Fase 1):
    aquí queremos el total real y al día cada vez que se corre esta fase."""
    global _last_request_at, last_api_error
    last_api_error = None
    api_key = os.environ.get("APIFOOTBALL_KEY")
    if not api_key:
        sys.exit("Falta la variable de entorno APIFOOTBALL_KEY.")
    url = f"{BASE_URL}{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
    data = None
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
    if data is None:
        return None
    if data.get("errors"):
        last_api_error = data["errors"]
        log(f"     ⚠️  la API respondió con error: {data['errors']}")
        return None
    return data.get("response", [])


def season_rows(player_id, season):
    """Una fila por competición (con su equipo) en la que jugó ese jugador
    esa temporada. None = "no sabemos" (la API no respondió / el plan no
    cubre esta temporada) — distinto de [] = "sabemos que no jugó nada"."""
    data = api("/players", {"id": player_id, "season": season})
    if data is None:
        return None
    rows = []
    for entry in data:
        for s in entry.get("statistics") or []:
            league = s.get("league") or {}
            team = s.get("team") or {}
            games = s.get("games") or {}
            goals = s.get("goals") or {}
            played = games.get("appearences") or 0
            g, a = goals.get("total") or 0, goals.get("assists") or 0
            if played == 0 and not g and not a:
                continue  # ni jugó ni anotó con ese equipo/competición esa temporada
            rows.append({
                "league_id": league.get("id"),
                "league_name": league.get("name") or "?",
                "league_logo": league.get("logo"),
                "team_id": team.get("id"),
                "team_name": team.get("name") or "?",
                "team_logo": team.get("logo"),
                "played": played,
                "goals": g,
                "assists": a,
                "minutes": games.get("minutes") or 0,
            })
    return rows


def build_player_seasons(player, only_season=None):
    if not player.get("player_id"):
        log(f"⚠️  {player['name']}: sin player_id todavía en {CONFIG_FILE} "
            "(hace falta correr Fase 1 con una temporada que el plan ya cubra) — se salta.")
        return {}
    seasons_cfg = list(player.get("career_seasons") or [])
    # Años nuevos se agregan solos: si la lista llega a 2026 y hoy es 2027,
    # se consulta también 2027 (y así cada año), sin tocar el config a mano.
    # Los torneos de selecciones usan año calendario, así que sin esto se
    # perderían en enero. Si ese año aún no tiene minutos, queda vacío.
    if seasons_cfg and not only_season:
        year_now = datetime.now(timezone(timedelta(hours=-6))).year
        seasons_cfg += range(max(seasons_cfg) + 1, year_now + 1)
    if only_season:
        seasons_cfg = [s for s in seasons_cfg if s == only_season]
    if not seasons_cfg:
        log(f"⚠️  {player['name']}: sin 'career_seasons' en {CONFIG_FILE} — se salta.")
        return {}

    result = {}
    for season in seasons_cfg:
        log(f"  · temporada {season}...")
        rows = season_rows(player["player_id"], season)
        if rows is None:
            continue  # no se toca lo que ya había guardado para esa temporada
        result[str(season)] = rows
        if rows:
            g = sum(r["goals"] for r in rows)
            a = sum(r["assists"] for r in rows)
            comps = ", ".join(f"{r['team_name']}/{r['league_name']}" for r in rows)
            log(f"    {len(rows)} competición(es) · {g} gol(es) · {a} asistencia(s) · {comps}")
        else:
            log("    sin minutos esa temporada (o el plan aún no la cubre)")
    return result


def main():
    parser = argparse.ArgumentParser(description="Fase 3 · Historial real por competición")
    parser.add_argument("--jugador", help="Limita a un jugador por su 'key' (armando, raul, quinones, mora)")
    parser.add_argument("--temporada", type=int, help="Limita a una sola temporada, para probar")
    args = parser.parse_args()

    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit(f"Falta {CONFIG_FILE}.")

    players = config["players"]
    if args.jugador:
        players = [p for p in players if p["key"] == args.jugador]
        if not players:
            sys.exit(f"No hay jugador con key='{args.jugador}' en {CONFIG_FILE}.")

    stats = load_json(STATS_FILE, {"updated_at": None, "players": {}})
    stats.setdefault("players", {})

    for player in players:
        log(f"⚽ {player['name']}")
        seasons_data = build_player_seasons(player, args.temporada)
        if not seasons_data:
            continue
        entry = stats["players"].setdefault(
            player["key"], {"seasons": {}, "applied_fixtures": {}})
        entry.setdefault("seasons", {}).update(seasons_data)

    # Poda jugadores que ya no están en hormi_config.json — igual que hace
    # el chequeo de cada 5 min con status.json, para que no se queden
    # pegados aquí para siempre si algún día se quita a alguien.
    valid_keys = {p["key"] for p in config["players"]}
    removed = [k for k in list(stats["players"]) if k not in valid_keys]
    for k in removed:
        del stats["players"][k]
    if removed:
        log(f"🧹 se quitaron de {STATS_FILE} jugadores que ya no están en "
            f"{CONFIG_FILE}: {', '.join(removed)}")

    stats["updated_at"] = datetime.now(LOCAL_TZ).isoformat()
    save_json(STATS_FILE, stats)
    log(f"✅ listo · {STATS_FILE} actualizado")


if __name__ == "__main__":
    main()

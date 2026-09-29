#!/usr/bin/env python3
"""
EuroGoalMX · Fase 1 — Validación de datos con API-Football (multi-jugador)
============================================================================
Comprueba que la API trae todo lo que la app necesita para seguir a varios
futbolistas mexicanos en Europa (o donde sea que jueguen) a la vez — cada
uno con sus propios clubes y su Selección. Los jugadores y sus equipos
salen de hormi_config.json (clave "players").

Por cada objetivo (jugador + equipo) revisa:
  1. Equipo (o Selección) y jugador
  2. Estadísticas de temporada por competición (goles, asistencias, minutos)
  3. Calendario de partidos (jugados y próximos)
  4. Alineación de un partido (¿titular o banca?)
  5. Eventos en vivo de un partido (goles, asistencias, cambios con minuto)

Uso:
  export APIFOOTBALL_KEY="tu_api_key"        # Windows: set APIFOOTBALL_KEY=...
  python hormiga_fase1_validar_api.py                  # valida TODOS los
                                                        # jugadores/objetivos
  python hormiga_fase1_validar_api.py --jugador raul   # valida solo un jugador
  python hormiga_fase1_validar_api.py --jugador raul --solo wolves
  python hormiga_fase1_validar_api.py --jugador raul --player-id 12345

Al terminar reescribe hormi_config.json con los team_id/player_id que
encontró, así que la siguiente fase ya no tiene que buscarlos.

Las respuestas se guardan en ./cache_api/ para que volver a correr el script
NO gaste cuota (el plan gratuito da 100 peticiones al día). Usa --no-cache
para forzar datos frescos.
"""

import argparse
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://v3.football.api-sports.io"
CACHE_DIR = Path("cache_api")
CONFIG_FILE = Path("hormi_config.json")

requests_used = 0
last_request_at = 0.0
last_api_error = None    # último {"errors": ...} de la API, para diagnosticar sin logs
MIN_SECONDS_BETWEEN_REQUESTS = 6.5   # plan gratuito: máx. 10 peticiones/min


# ---------------------------------------------------------------- utilidades
def norm(text):
    """Minúsculas y sin acentos, para comparar nombres."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def api(endpoint, params, use_cache=True):
    """GET a la API con caché en disco. Devuelve la lista 'response'."""
    global requests_used, last_request_at, last_api_error
    last_api_error = None
    key = endpoint.strip("/").replace("/", "_") + "_" + "_".join(
        f"{k}-{v}" for k, v in sorted(params.items()))
    cache_file = CACHE_DIR / f"{key}.json"

    if use_cache and cache_file.exists():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        api_key = os.environ.get("APIFOOTBALL_KEY")
        if not api_key:
            sys.exit("Falta la variable de entorno APIFOOTBALL_KEY.")
        url = f"{BASE_URL}{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
        remaining = None
        for attempt in (1, 2):
            wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.time() - last_request_at)
            if wait > 0:
                time.sleep(wait)
            try:
                last_request_at = time.time()
                with urllib.request.urlopen(req, timeout=30) as resp:
                    remaining = resp.headers.get("x-ratelimit-requests-remaining")
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as err:
                if err.code == 429 and attempt == 1:
                    print("     · límite por minuto alcanzado, esperando 60 s...")
                    time.sleep(60)
                    continue
                raise
        requests_used += 1
        if remaining is not None:
            print(f"     · peticiones restantes hoy: {remaining}")
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    errors = data.get("errors")
    if errors:  # la API devuelve {} o [] cuando no hay errores
        last_api_error = errors
        print(f"     ⚠️  La API respondió con error: {errors}")
        if cache_file.exists():
            cache_file.unlink()
        return None
    return data.get("response", [])


# ---------------------------------------------------------------- pasos
RESERVE_WORDS = {"w", "women", "femenino", "feminine", "b", "ii", "reserves", "youth", "academy", "juvenil"}


def is_reserve_or_women(name):
    """Equipos femeniles, filiales y juveniles ('Porto B', 'Betis W', 'Mexico U23'):
    la búsqueda por nombre los devuelve mezclados con el equipo principal, y el
    orden no es fiable — no deben ganarle al primer equipo."""
    tokens = norm(name).replace(".", " ").split()
    return any(t in RESERVE_WORDS or (t.startswith("u") and t[1:].isdigit()) for t in tokens)


def pick_team(teams, target):
    """Entre los resultados que coinciden en país y tipo (club/selección), elige
    el primer equipo: descarta femenil/filial/juvenil y prefiere el nombre
    idéntico a la búsqueda. Devuelve (elegido, candidatos)."""
    is_national = target["kind"] == "seleccion"
    candidates = [t["team"] for t in teams
                  if t["team"].get("country") == target.get("country")
                  and bool(t["team"].get("national")) == is_national]
    if not candidates:
        return None, []
    wanted = norm(target.get("search") or "")
    ranked = sorted(enumerate(candidates),
                    key=lambda ic: (is_reserve_or_women(ic[1]["name"]),
                                    norm(ic[1]["name"]) != wanted, ic[0]))
    return ranked[0][1], candidates


def find_team(target, args):
    print("\n1) Equipo")
    if target.get("team_id"):
        check(target, "Equipo (id conocido)", True, f"id {target['team_id']}")
        return target["team_id"]

    is_national = target["kind"] == "seleccion"
    teams = api("/teams", {"search": target["search"]}, args.cache) or []
    team, candidates = pick_team(teams, target)
    if team:
        others = [f"{c['name']} (id {c['id']})" for c in candidates if c["id"] != team["id"]]
        extra = f" · otros candidatos descartados: {', '.join(others)}" if others else ""
        check(target, "Selección encontrada" if is_national else "Equipo encontrado", True,
              f"{team['name']} (id {team['id']}){extra}")
        target["team_id"] = team["id"]
        if team.get("logo"):
            target["crest_url"] = team["logo"]
        return team["id"]
    names = ", ".join(f"{t['team']['name']} ({t['team'].get('country')})" for t in teams) or "ninguno"
    check(target, "Equipo/Selección encontrado", False,
          f"no coincidió con country='{target.get('country')}'. Resultados de la búsqueda: {names}")
    return None


def find_player(target, args, team_id, player):
    print("\n2) Jugador")
    if args.player_id:
        check(target, f"{player['name']} (id manual)", True, f"id {args.player_id}")
        player["player_id"] = args.player_id
        return args.player_id
    if player.get("player_id"):
        check(target, f"{player['name']} (id conocido)", True, f"id {player['player_id']}")
        return player["player_id"]

    search_term = player["player_search_term"]
    first, last = player["search_first_name"], player["search_last_name"]
    raw = api("/players", {"team": team_id, "season": target["season"],
                           "search": search_term}, args.cache)
    if raw is None and last_api_error:
        # El plan gratuito bloquea /players cuando season es una temporada
        # que no cubre (p.ej. 2026: "Free plans do not have access to this
        # season, try from 2022 to 2024"). /players/profiles NO pide season
        # — solo busca el perfil del jugador (sin stats de equipo/temporada)
        # — así que sirve de respaldo para encontrar su id aunque el plan
        # todavía no cubra la temporada actual.
        first_error = last_api_error
        profiles = api("/players/profiles", {"search": search_term}, args.cache) or []
        # /players/profiles busca en TODA la base de la API, sin acotar por
        # equipo — un nombre y apellido no bastan para identificar a la
        # persona correcta (p.ej. "Julián Quiñones" también es un jugador
        # colombiano; el filtro de nacionalidad evita ese falso positivo).
        # Todos los jugadores que sigue esta app son mexicanos, así que se
        # exige nationality == "Mexico"; si hay más de un candidato mexicano
        # con ese nombre, no se adivina — hay que resolverlo a mano con
        # --jugador <key> --player-id <id>.
        candidates = []
        for p in profiles:
            info = p["player"]
            full = norm(f"{info.get('firstname')} {info.get('lastname')} {info.get('name')}")
            if first in full and last in full and norm(info.get("nationality") or "") == "mexico":
                candidates.append(info)
        if len(candidates) == 1:
            info = candidates[0]
            check(target, f"{player['name']} encontrado (perfil, sin equipo/temporada todavía)", True,
                  f"{info['name']} · id {info['id']} · {info.get('nationality')}")
            player["player_id"] = info["id"]
            return info["id"]
        if len(candidates) > 1:
            ids = ", ".join(f"{c['name']} (id {c['id']})" for c in candidates)
            check(target, f"{player['name']} encontrado en la plantilla", False,
                  f"hay {len(candidates)} jugadores mexicanos con ese nombre — resuélvelo a mano: "
                  f"{ids}. Pasa --jugador {player['key']} --player-id <id>")
            return None
        # Nadie con ese nombre figura como mexicano en la API (la nacionalidad
        # registrada puede ser la de nacimiento: Argentina, EE. UU., España...).
        # No se adivina, pero se muestran los que coinciden por nombre para
        # poder elegir a mano con --player-id.
        by_name = []
        for p in profiles:
            info = p["player"]
            full = norm(f"{info.get('firstname')} {info.get('lastname')} {info.get('name')}")
            if first in full and last in full:
                birth = (info.get("birth") or {}).get("country")
                by_name.append(f"{info['name']} (id {info['id']}, nacionalidad {info.get('nationality')}"
                               f"{', nació en ' + birth if birth else ''}, {info.get('age')} años)")
        if by_name:
            check(target, f"{player['name']} encontrado en la plantilla", False,
                  f"ninguno figura con nacionalidad mexicana; coinciden por nombre: "
                  f"{'; '.join(by_name[:5])}. Si uno es él, pasa --jugador {player['key']} --player-id <id>")
            return None
        check(target, f"{player['name']} encontrado en la plantilla", False,
              f"error de API: {first_error} (tampoco se encontró por /players/profiles "
              "con nacionalidad mexicana)")
        return None
    players = raw or []
    for p in players:
        info = p["player"]
        full = norm(f"{info.get('firstname')} {info.get('lastname')} {info.get('name')}")
        if first in full and last in full:
            check(target, f"{player['name']} encontrado en la plantilla", True,
                  f"{info['name']} · id {info['id']} · "
                  f"{info.get('nationality')} · {info.get('age')} años")
            player["player_id"] = info["id"]
            return info["id"]
    names = ", ".join(p["player"]["name"] for p in players) or "ninguno"
    check(target, f"{player['name']} encontrado en la plantilla", False,
          f"resultados: {names}. Si sale con otro nombre, pasa --jugador {player['key']} --player-id <id>")
    return None


def season_stats(target, args, player_id):
    print(f"\n3) Estadísticas de temporada {target['season']}")
    data = api("/players", {"id": player_id, "season": target["season"]}, args.cache)
    if not data:
        check(target, "Stats de temporada disponibles", False,
              "sin datos (¿el plan gratuito no cubre esta temporada?)")
        return None
    stats = data[0]["statistics"]
    total_goals = total_assists = total_minutes = 0
    print(f"     {'Competición':<28}{'PJ':>4}{'Tit':>5}{'Min':>6}{'Gol':>5}{'Ast':>5}")
    for s in stats:
        g, games = s["goals"], s["games"]
        goals, assists = g.get("total") or 0, g.get("assists") or 0
        minutes = games.get("minutes") or 0
        total_goals += goals
        total_assists += assists
        total_minutes += minutes
        print(f"     {s['league']['name'][:27]:<28}"
              f"{games.get('appearences') or 0:>4}"
              f"{games.get('lineups') or 0:>5}{minutes:>6}"
              f"{goals:>5}{assists:>5}")
    check(target, "Stats de temporada disponibles", True,
          f"{total_goals} goles, {total_assists} asistencias, "
          f"{total_minutes} minutos en total")
    return stats


def fixtures(target, args, team_id):
    print("\n4) Calendario")
    data = api("/fixtures", {"team": team_id, "season": target["season"]}, args.cache)
    if not data:
        check(target, "Calendario disponible", False)
        return [], []
    data.sort(key=lambda f: f["fixture"]["timestamp"])
    done = [f for f in data if f["fixture"]["status"]["short"] in ("FT", "AET", "PEN")]
    upcoming = [f for f in data if f["fixture"]["status"]["short"] in ("NS", "TBD")]
    check(target, "Calendario disponible", True,
          f"{len(done)} jugados, {len(upcoming)} por jugar")
    for f in upcoming[:3]:
        fx = f["fixture"]
        print(f"     · Próximo: {fx['date'][:16].replace('T', ' ')} UTC — "
              f"{f['teams']['home']['name']} vs {f['teams']['away']['name']} "
              f"({f['league']['name']})")
    return done, upcoming


def pick_test_match(done):
    return done[-1] if done else None


def find_goal_match(args, done, team_id, player_id, buscar):
    print(f"\n   Buscando un partido con gol/asistencia suya (máx. {buscar} peticiones)...")
    scanned = 0
    for f in reversed(done):
        side = "home" if f["teams"]["home"]["id"] == team_id else "away"
        if not f["goals"][side]:
            continue
        if scanned >= buscar:
            break
        scanned += 1
        events = api("/fixtures/events", {"fixture": f["fixture"]["id"]}, args.cache) or []
        for e in events:
            if e["type"] == "Goal" and player_id in (
                    e["player"].get("id"), (e.get("assist") or {}).get("id")):
                return f
    print("   No se encontró en el rango revisado; se usa el último partido.")
    return None


def lineup_and_events(target, args, match, player_id, player):
    fx = match["fixture"]
    title = (f"{match['teams']['home']['name']} {match['goals']['home']}-"
             f"{match['goals']['away']} {match['teams']['away']['name']}")
    print(f"\n5) Partido de prueba: {title} ({match['league']['name']})")

    role = None
    lineups = api("/fixtures/lineups", {"fixture": fx["id"]}, args.cache) or []
    for team in lineups:
        for slot, label in (("startXI", "titular"), ("substitutes", "banca")):
            for p in team.get(slot) or []:
                if p["player"]["id"] == player_id:
                    role = label
    check(target, f"Alineación incluye a {player['name']}", role is not None,
          f"fue {role}" if role else "no aparece (no convocado o alineación no disponible)")

    events = api("/fixtures/events", {"fixture": fx["id"]}, args.cache) or []
    goals, assists, subs = [], [], []
    for e in events:
        minute = e["time"]["elapsed"]
        extra = e["time"].get("extra")
        minute_txt = f"{minute}+{extra}'" if extra else f"{minute}'"
        pid = e["player"].get("id")
        aid = (e.get("assist") or {}).get("id")
        if e["type"] == "Goal" and e["detail"] != "Missed Penalty":
            if pid == player_id:
                goals.append(f"{minute_txt} ({e['detail']})")
            if aid == player_id:
                assists.append(f"{minute_txt} a {e['player']['name']}")
        elif e["type"].lower() == "subst" and player_id in (pid, aid):
            accion = "entra" if role == "banca" else "sale"
            subs.append(f"{minute_txt} {accion}")

    check(target, "Evento de gol con minuto", bool(goals),
          ", ".join(goals) or "sin goles suyos en este partido")
    check(target, "Evento de asistencia", bool(assists),
          ", ".join(assists) or "sin asistencias suyas en este partido")
    check(target, "Evento de cambio con minuto", bool(subs),
          ", ".join(subs) or "jugó completo o no participó")


def check(target, name, ok, detail=""):
    target.setdefault("_checks", []).append((name, ok, detail))
    icon = "✅" if ok else "❌"
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------- por objetivo
def validate_target(target, args, player):
    print("\n" + "═" * 60)
    print(f"⚽ {player['name']} · {target['emoji']} {target['label']}")
    print("═" * 60)
    team_id = find_team(target, args)
    if not team_id:
        return
    player_id = args.player_id or find_player(target, args, team_id, player)
    if not player_id:
        return
    season_stats(target, args, player_id)
    done, _ = fixtures(target, args, team_id)
    match = None
    if args.buscar_gol:
        match = find_goal_match(args, done, team_id, player_id, args.buscar_gol)
    match = match or pick_test_match(done)
    if match:
        lineup_and_events(target, args, match, player_id, player)


# ---------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="Validación API · EuroGoalMX (multi-jugador)")
    parser.add_argument("--config", default=str(CONFIG_FILE))
    parser.add_argument("--jugador", help="Limita a un jugador por su 'key' (armando, raul, quinones)")
    parser.add_argument("--solo", help="Dentro de --jugador, limita a un objetivo por su 'key' (wolves, seleccion, ...)")
    parser.add_argument("--player-id", type=int,
                        help="Fuerza el id del jugador en vez de buscarlo (requiere --jugador)")
    parser.add_argument("--buscar-gol", type=int, default=0, metavar="N",
                        help="Revisa hasta N partidos para encontrar uno con gol/asistencia suya")
    parser.add_argument("--no-cache", dest="cache", action="store_false")
    args = parser.parse_args()

    if args.player_id and not args.jugador:
        sys.exit("--player-id necesita --jugador <key> (a qué jugador aplica el id).")
    if args.solo and not args.jugador:
        sys.exit("--solo necesita --jugador <key> (los objetivos se repiten entre jugadores, p.ej. 'seleccion').")

    config_path = Path(args.config)
    config = load_json(config_path, None)
    if config is None:
        sys.exit(f"No encontré {config_path}. Debe existir con la lista de 'players'.")

    players = config["players"]
    if args.jugador:
        players = [p for p in players if p["key"] == args.jugador]
        if not players:
            sys.exit(f"No hay ningún jugador con key='{args.jugador}' en {config_path}.")

    print("⚽ EuroGoalMX · Fase 1 — validando API-Football (multi-jugador)")
    for player in players:
        targets = player["targets"]
        if args.solo:
            targets = [t for t in targets if t["key"] == args.solo]
            if not targets:
                sys.exit(f"No hay ningún objetivo con key='{args.solo}' para {player['key']}.")
        for target in targets:
            validate_target(target, args, player)

    report(config, config_path)


def report(config, config_path):
    print("\n" + "─" * 60)
    all_checks = []
    for player in config["players"]:
        for t in player["targets"]:
            for c in t.pop("_checks", []):
                all_checks.append((f"{player['name']} · {t['label']}", *c))
    ok = sum(1 for _, _, good, _ in all_checks if good)
    print(f"Resultado global: {ok}/{len(all_checks)} comprobaciones OK · "
          f"{requests_used} peticiones gastadas en esta corrida")

    # Guarda el config actualizado (con los team_id/player_id detectados)
    save_json(config_path, config)
    print(f"Config actualizado en {config_path} (revisa los team_id/player_id detectados).")

    Path("resultado_fase1.json").write_text(json.dumps(
        {"players": [
            {
                "key": player["key"], "name": player["name"],
                "player_id": player.get("player_id"),
                "targets": [
                    {"key": t["key"], "label": t["label"], "team_id": t.get("team_id"),
                     "checks": [{"check": c, "ok": o, "detalle": d}
                                for label, c, o, d in all_checks
                                if label == f"{player['name']} · {t['label']}"]}
                    for t in player["targets"]
                ],
            }
            for player in config["players"]
        ]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Resumen guardado en resultado_fase1.json — mándamelo de vuelta.")


if __name__ == "__main__":
    main()

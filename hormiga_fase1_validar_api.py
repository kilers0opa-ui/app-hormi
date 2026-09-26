#!/usr/bin/env python3
"""
App Hormi · Fase 1 — Validación de datos con API-Football (multi-entidad)
==========================================================================
Comprueba que la API trae todo lo que la app necesita para seguir a
Armando "La Hormiga" González en varios equipos a la vez: sus clubes
(Chivas, Olympiacos) y la Selección Mexicana.

Por cada objetivo en hormi_config.json revisa:
  1. Equipo (o Selección) y jugador
  2. Estadísticas de temporada por competición (goles, asistencias, minutos)
  3. Calendario de partidos (jugados y próximos)
  4. Alineación de un partido (¿titular o banca?)
  5. Eventos en vivo de un partido (goles, asistencias, cambios con minuto)

Uso:
  export APIFOOTBALL_KEY="tu_api_key"        # Windows: set APIFOOTBALL_KEY=...
  python hormiga_fase1_validar_api.py                  # valida TODOS los
                                                        # objetivos de
                                                        # hormi_config.json
  python hormiga_fase1_validar_api.py --solo chivas    # valida solo uno
  python hormiga_fase1_validar_api.py --solo seleccion --no-cache

Al terminar reescribe hormi_config.json con los team_id que encontró
(por ejemplo el de la Selección Mexicana, que se detecta automáticamente),
así que la siguiente fase ya no tiene que buscarlos.

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
PLAYER_SEARCH = "Gonz"          # la API pide mínimo 4 caracteres
PLAYER_FIRST_NAME = "armando"

requests_used = 0
last_request_at = 0.0
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
    global requests_used, last_request_at
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
        print(f"     ⚠️  La API respondió con error: {errors}")
        if cache_file.exists():
            cache_file.unlink()
        return None
    return data.get("response", [])


# ---------------------------------------------------------------- pasos
def find_team(target, args):
    print("\n1) Equipo")
    if target.get("team_id"):
        check(target, "Equipo (id conocido)", True, f"id {target['team_id']}")
        return target["team_id"]

    is_national = target["kind"] == "seleccion"
    teams = api("/teams", {"search": target["search"]}, args.cache) or []
    for t in teams:
        team = t["team"]
        same_country = team.get("country") == target.get("country")
        if is_national and same_country and team.get("national"):
            check(target, "Selección encontrada", True,
                  f"{team['name']} (id {team['id']})")
            target["team_id"] = team["id"]
            return team["id"]
        if not is_national and same_country and not team.get("national"):
            check(target, "Equipo encontrado", True,
                  f"{team['name']} (id {team['id']})")
            target["team_id"] = team["id"]
            return team["id"]
    check(target, "Equipo/Selección encontrado", False, "no apareció en la búsqueda")
    return None


def find_player(target, args, team_id):
    print("\n2) Jugador")
    if args.player_id:
        check(target, "Hormiga (id manual)", True, f"id {args.player_id}")
        return args.player_id
    players = api("/players", {"team": team_id, "season": target["season"],
                               "search": PLAYER_SEARCH}, args.cache) or []
    for p in players:
        info = p["player"]
        full = norm(f"{info.get('firstname')} {info.get('lastname')} {info.get('name')}")
        if PLAYER_FIRST_NAME in full and "gonzalez" in full:
            check(target, "Hormiga encontrado en la plantilla", True,
                  f"{info['name']} · id {info['id']} · "
                  f"{info.get('nationality')} · {info.get('age')} años")
            return info["id"]
    names = ", ".join(p["player"]["name"] for p in players) or "ninguno"
    check(target, "Hormiga encontrado en la plantilla", False,
          f"resultados: {names}. Si sale con otro nombre, pasa --player-id")
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


def lineup_and_events(target, args, match, player_id):
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
    check(target, "Alineación incluye a la Hormiga", role is not None,
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
def validate_target(target, args):
    print("\n" + "═" * 60)
    print(f"🐜 {target['emoji']} {target['label']}")
    print("═" * 60)
    team_id = find_team(target, args)
    if not team_id:
        return
    player_id = args.player_id or find_player(target, args, team_id)
    if not player_id:
        return
    season_stats(target, args, player_id)
    done, _ = fixtures(target, args, team_id)
    match = None
    if args.buscar_gol:
        match = find_goal_match(args, done, team_id, player_id, args.buscar_gol)
    match = match or pick_test_match(done)
    if match:
        lineup_and_events(target, args, match, player_id)


# ---------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="Validación API · App Hormi (multi-entidad)")
    parser.add_argument("--config", default=str(CONFIG_FILE))
    parser.add_argument("--solo", help="Valida solo un objetivo por su 'key' (chivas, olympiacos, seleccion)")
    parser.add_argument("--player-id", type=int, help="Fuerza el id del jugador en vez de buscarlo")
    parser.add_argument("--buscar-gol", type=int, default=0, metavar="N",
                        help="Revisa hasta N partidos para encontrar uno con gol/asistencia suya")
    parser.add_argument("--no-cache", dest="cache", action="store_false")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_json(config_path, None)
    if config is None:
        sys.exit(f"No encontré {config_path}. Debe existir con la lista de 'targets'.")

    targets = config["targets"]
    if args.solo:
        targets = [t for t in targets if t["key"] == args.solo]
        if not targets:
            sys.exit(f"No hay ningún target con key='{args.solo}' en {config_path}.")

    print("🐜 App Hormi · Fase 1 — validando API-Football (multi-entidad)")
    for target in targets:
        validate_target(target, args)

    report(config, config_path)


def report(config, config_path):
    print("\n" + "─" * 60)
    all_checks = []
    for t in config["targets"]:
        for c in t.pop("_checks", []):
            all_checks.append((t["label"], *c))
    ok = sum(1 for _, _, good, _ in all_checks if good)
    print(f"Resultado global: {ok}/{len(all_checks)} comprobaciones OK · "
          f"{requests_used} peticiones gastadas en esta corrida")

    # Guarda el config actualizado (con los team_id detectados, p.ej. Selección)
    save_json(config_path, config)
    print(f"Config actualizado en {config_path} (revisa los team_id detectados).")

    Path("resultado_fase1.json").write_text(json.dumps(
        {"targets": [
            {"key": t["key"], "label": t["label"], "team_id": t.get("team_id"),
             "checks": [{"check": c, "ok": o, "detalle": d}
                        for label, c, o, d in all_checks if label == t["label"]]}
            for t in config["targets"]
        ]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Resumen guardado en resultado_fase1.json — mándamelo de vuelta.")


if __name__ == "__main__":
    main()

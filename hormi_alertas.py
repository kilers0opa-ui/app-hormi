#!/usr/bin/env python3
"""
EuroGoalMX · Fase 2 — Motor de alertas (multi-jugador, listo para la nube)
===========================================================================
Sigue a varios futbolistas mexicanos en Europa (o donde sea que jueguen) a
la vez — los jugadores y sus equipos salen de hormi_config.json (clave
"players") — y manda notificaciones push al celular (vía ntfy y/o FCM):

  ⭐ Titular / 🪑 banca / ❌ no convocado    (cuando sale la alineación,
                                              ~1h antes del partido — API-
                                              Football no publica la
                                              convocatoria oficial con más
                                              anticipación que eso)
  📅 Juega hoy
  ⚽ Arrancó el partido
  🔄 Entra de cambio / ↩️ sale de cambio      (con minuto)
  🔥 GOL / 🎯 asistencia / 🚫 gol anulado     (con minuto y marcador)
  🏁 Resumen final: minutos, goles, asistencias

Modos:
  python hormi_alertas.py prueba
      Manda una notificación de prueba a tu celular.

  python hormi_alertas.py repeticion --jugador raul --solo wolves --contra Everton
      Reproduce un partido YA JUGADO minuto a minuto, como si fuera en vivo.
      Usa la caché de la Fase 1 (cache_api/), así que no gasta peticiones.
      --jugador es obligatorio (armando, raul, quinones); --solo elige el
      objetivo de ese jugador (si no se da, usa el primero).

  python hormi_alertas.py chequeo
      UNA sola pasada: revisa TODOS los jugadores y objetivos de
      hormi_config.json, manda las alertas nuevas y actualiza status.json
      (lo que lee la app). Se puede limitar con --jugador y/o --solo. Este
      es el modo pensado para correr cada pocos minutos desde un cron en
      la nube (GitHub Actions) — no se queda corriendo, entra y sale.

Requisitos: Python 3.9+, sin librerías externas. Variable APIFOOTBALL_KEY
(no se necesita en modo repetición, que usa solo la caché).

Push real (opcional, además de ntfy):
  Variable de entorno FCM_SERVICE_ACCOUNT_JSON con el JSON de una cuenta de
  servicio de Firebase (requiere el paquete 'google-auth'), y la llave
  "fcm_project_id" en hormi_config.json. Sin eso, el motor sigue mandando
  las alertas por ntfy exactamente igual que antes. Cada push real lleva
  un campo "player_key" en los datos, así la app puede descartar en el
  celular las notificaciones de jugadores que no estén en Favoritos.
"""

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "https://v3.football.api-sports.io"
CACHE_DIR = Path("cache_api")
CONFIG_FILE = Path("hormi_config.json")
STATE_FILE = Path("hormi_estado.json")     # qué alertas ya se mandaron
STATUS_FILE = Path("status.json")          # lo que lee la app (dashboard)

LOCAL_TZ = timezone(timedelta(hours=-6))   # hora del centro de México

LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}
FINISHED = {"FT", "AET", "PEN"}
CANCELLED = {"PST", "CANC", "ABD", "AWD", "WO", "SUSP"}
NOT_STARTED = {"NS", "TBD"}

MIN_SECONDS_BETWEEN_REQUESTS = 6.5         # plan gratuito: 10 peticiones/min
_last_request_at = 0.0
last_api_error = None    # último {"errors": ...} de la API, para diagnosticar sin logs


# ═══════════════════════════════════════════════════════ utilidades
def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config():
    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit(f"Falta {CONFIG_FILE}. Corre primero hormiga_fase1_validar_api.py.")
    return config


def api(endpoint, params, use_cache=False):
    """GET a API-Football. Misma caché que la Fase 1 (mismos nombres)."""
    global _last_request_at, last_api_error
    last_api_error = None
    key = endpoint.strip("/").replace("/", "_") + "_" + "_".join(
        f"{k}-{v}" for k, v in sorted(params.items()))
    cache_file = CACHE_DIR / f"{key}.json"
    if use_cache and cache_file.exists():
        return load_json(cache_file, {}).get("response", [])

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
                log("límite por minuto alcanzado, esperando 60 s...")
                time.sleep(60)
                continue
            raise
    if data.get("errors"):
        last_api_error = data["errors"]
        log(f"⚠️  La API respondió con error: {data['errors']}")
        return None
    CACHE_DIR.mkdir(exist_ok=True)
    save_json(cache_file, data)
    return data.get("response", [])


def log(msg):
    print(f"[{datetime.now(LOCAL_TZ):%H:%M:%S}] {msg}")


def minute_txt(t):
    return f"{t['elapsed']}+{t['extra']}'" if t.get("extra") else f"{t['elapsed']}'"


# ═══════════════════════════════════════════════════════ notificaciones
class Notifier:
    """Envía push con ntfy (https://ntfy.sh). Sin cuenta, gratis."""

    def __init__(self, topic, console_only=False):
        self.topic = topic
        self.console_only = console_only

    def send(self, title, message, priority=3):
        log(f"📲 {title} — {message}")
        if self.console_only:
            return
        body = json.dumps({"topic": self.topic, "title": title,
                           "message": message, "priority": priority}).encode()
        req = urllib.request.Request(
            "https://ntfy.sh", data=body,
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as err:
            log(f"⚠️  No se pudo enviar a ntfy: {err}")


def get_topic(config):
    if not config.get("ntfy_topic"):
        config["ntfy_topic"] = f"hormi-{secrets.token_hex(6)}"
        save_json(CONFIG_FILE, config)
        print("\n🔔 Se creó tu canal privado de notificaciones:\n"
              f"      {config['ntfy_topic']}\n"
              "   1. Instala 'ntfy' desde Play Store.\n"
              "   2. Toca '+', escribe ese nombre exacto y suscríbete.\n"
              "   No lo compartas: cualquiera con el nombre ve tus alertas.\n")
    return config["ntfy_topic"]


# ═══════════════════════════════════════════════════════ push real (FCM)
class FcmSender:
    """Push real vía Firebase Cloud Messaging, al tema 'hormi_alerts' (lo que
    la app Android suscribe al arrancar). Manda mensajes SOLO de datos (sin
    'notification'), así la app arma ella misma la notificación — incluso
    con el teléfono bloqueado o la app cerrada — y puede mostrar la imagen
    grande de '¡GOOOOL!' cuando type == 'goal'. Cada mensaje incluye
    'player_key' para que la app descarte en el celular las notificaciones
    de jugadores que el usuario no tenga en Favoritos.

    Necesita dos cosas, ninguna es obligatoria: si faltan, la app sigue
    recibiendo alertas por ntfy como siempre, solo que sin push real.
      - hormi_config.json: "fcm_project_id" (el project_id de Firebase,
        no es secreto).
      - Variable de entorno FCM_SERVICE_ACCOUNT_JSON: el JSON completo de
        una clave de cuenta de servicio (Firebase Console → Configuración
        del proyecto → Cuentas de servicio → Generar nueva clave privada).
        ESO SÍ es secreto — va como GitHub Actions secret, nunca en el repo.
    """
    TOPIC = "hormi_alerts"
    SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

    def __init__(self, project_id):
        self.project_id = project_id
        self.enabled = False
        self._token = None

        sa_json = os.environ.get("FCM_SERVICE_ACCOUNT_JSON")
        if not project_id:
            log("ℹ️  FCM: falta 'fcm_project_id' en hormi_config.json — push real desactivado.")
            return
        if not sa_json:
            log("ℹ️  FCM: falta la variable FCM_SERVICE_ACCOUNT_JSON — push real desactivado.")
            return
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests
            info = json.loads(sa_json)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=[self.SCOPE])
            creds.refresh(google.auth.transport.requests.Request())
            self._token = creds.token
            self.enabled = True
        except Exception as err:
            log(f"⚠️  FCM: no se pudo autenticar con la cuenta de servicio — {err}")

    def send(self, tipo, title, body, player_key=None, team_key=None):
        """tipo: 'goal' (dispara la pantalla de ¡GOOOOL! en la app) o
        cualquier otro string para una notificación normal. player_key
        identifica de qué jugador es la alerta (p.ej. 'armando', 'raul',
        'quinones'), para que la app filtre por Favoritos. team_key es el
        objetivo (p.ej. 'olympiacos', 'seleccion', 'wolves'), para que la
        app elija la imagen de fondo correcta en notificaciones de gol."""
        if not self.enabled:
            return
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        payload = {
            "message": {
                "topic": self.TOPIC,
                "data": {"type": tipo, "title": title, "body": body,
                         "player_key": player_key or "", "team_key": team_key or ""},
            }
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=UTF-8",
                     "Authorization": f"Bearer {self._token}"})
        try:
            urllib.request.urlopen(req, timeout=15).close()
        except urllib.error.HTTPError as err:
            body_txt = ""
            try:
                body_txt = err.read().decode("utf-8")
            except Exception:
                pass
            log(f"⚠️  FCM: el envío falló ({err.code}) — {body_txt or err}")
        except Exception as err:
            log(f"⚠️  FCM: no se pudo enviar el push real — {err}")


# ═══════════════════════════════════════════════════════ detector
def analyze(data, player, target):
    """Recibe una 'foto' del partido (formato de /fixtures?id=...) para UN
    jugador y UN objetivo (club o selección) y devuelve TODAS las alertas
    que esa foto implica, más un resumen de estado. La deduplicación va
    aparte, así el detector no necesita recordar nada.

    El prefijo de cada llave de alerta incluye jugador+objetivo, así que
    nunca chocan aunque dos jugadores compartan el mismo partido (p.ej.
    ambos convocados a la Selección Mexicana en la misma fecha FIFA)."""
    player_id = player["player_id"]
    apodo = player.get("apodo") or player["name"]
    emoji, label, tkey = target["emoji"], target["label"], target["key"]
    prefix = f"{player['key']}:{tkey}"

    fx = data["fixture"]
    fid, status = fx["id"], fx["status"]["short"]
    home, away = data["teams"]["home"], data["teams"]["away"]
    league = data["league"]["name"]
    match = f"{home['name']} vs {away['name']}"

    def score():
        g = data["goals"]
        return f"{home['name']} {g['home'] or 0}-{g['away'] or 0} {away['name']}"

    alerts = []

    def add(key, title, message, priority=3):
        alerts.append((f"{prefix}:{fid}:{key}", title, message, priority))

    if status in CANCELLED:
        add("cancelado", f"⚠️ {emoji} {label} ({apodo}): partido suspendido/pospuesto",
            f"{match} · {fx['status']['long']}")
        return alerts, {"status": "cancelado", "match": match, "league": league,
                        "score": score(), "goals": 0, "assists": 0, "summary": None}

    # --- Alineación (API-Football la publica ~1h antes del partido; no hay
    # forma de detectar la convocatoria oficial con más anticipación)
    role = None
    for team in data.get("lineups") or []:
        for slot, lbl in (("startXI", "titular"), ("substitutes", "banca")):
            if any(p["player"]["id"] == player_id for p in team.get(slot) or []):
                role = lbl
    if data.get("lineups"):
        if role == "titular":
            add("alineacion", f"⭐ {emoji} ¡{apodo} titular con {label}!", f"{match} · {league}", 4)
        elif role == "banca":
            add("alineacion", f"🪑 {emoji} {apodo} arranca en la banca ({label})",
                f"{match} · {league}")
        else:
            add("alineacion", f"❌ {emoji} {apodo} no convocado ({label})",
                f"{match} · {league}", 2)

    if status in LIVE | FINISHED:
        add("inicio", f"⚽ {emoji} Arrancó: {label} ({apodo})", match, 2)

    entered = False
    goals = assists = 0
    in_minute = out_minute = None
    events = sorted(data.get("events") or [],
                    key=lambda e: (e["time"]["elapsed"], e["time"].get("extra") or 0))
    for e in events:
        t, etype, detail = e["time"], e["type"].lower(), e.get("detail") or ""
        pid = e["player"].get("id")
        aid = (e.get("assist") or {}).get("id")
        k = f"{etype}:{t['elapsed']}:{t.get('extra')}:{pid}:{aid}"
        m = minute_txt(t)

        if etype == "goal" and detail != "Missed Penalty":
            if pid == player_id and detail != "Own Goal":
                goals += 1
                tipo = " de penal" if detail == "Penalty" else ""
                add(k + ":gol", f"🔥 {emoji} ¡GOL DE {apodo.upper()}{tipo.upper()}! {m}",
                    f"{label} · {score()}", 5)
            elif aid == player_id:
                assists += 1
                add(k + ":ast", f"🎯 {emoji} ¡Asistencia de {apodo}! {m}",
                    f"{label} · Gol de {e['player']['name']} · {score()}", 4)
        elif etype == "var" and "goal" in detail.lower() and player_id in (pid, aid):
            add(k, f"🚫 {emoji} VAR anula jugada de gol de {apodo} {m}", f"{label} · {detail}")
        elif etype == "subst" and player_id in (pid, aid):
            if role == "banca" and not entered:
                entered, in_minute = True, t["elapsed"]
                add(k, f"🔄 {emoji} ¡Entra {apodo}! {m}", f"{label} · {score()}", 4)
            else:
                out_minute = t["elapsed"]
                add(k, f"↩️ {emoji} Sale {apodo} {m}", f"{label} · {score()}", 2)

    summary = None
    if status in FINISHED:
        if role == "titular":
            played = out_minute or 90
        elif entered:
            played = max(1, (out_minute or 90) - in_minute)
        else:
            played = 0
        detalle = (f"{played} min · {goals} gol(es) · {assists} asistencia(s)") \
            if played else "No tuvo minutos."
        add("final", f"🏁 {emoji} Final {label} ({apodo}): {score()}", detalle, 3)
        summary = {"minutes": played, "goals": goals, "assists": assists}

    current_status = "no_convocado" if data.get("lineups") and role is None else \
        role or ("en_cancha" if status in LIVE else
                 ("finalizado" if status in FINISHED else "sin_partido"))

    return alerts, {"status": current_status, "match": match, "league": league,
                     "score": score(), "goals": goals, "assists": assists,
                     "summary": summary}


def dispatch(alerts, sent, notifier, fcm=None, player_key=None, team_key=None):
    new = 0
    for key, title, message, priority in alerts:
        if key not in sent:
            notifier.send(title, message, priority)
            if fcm:
                tipo = "goal" if key.endswith(":gol") else "info"
                fcm.send(tipo, title, message, player_key, team_key)
            sent.add(key)
            new += 1
    return new


# ═══════════════════════════════════════════════════════ modo repetición
def find_cached_fixture(args, team_id):
    for f in CACHE_DIR.glob("fixtures_season-*.json"):
        for fx in load_json(f, {}).get("response", []):
            if fx["teams"]["home"]["id"] != team_id and fx["teams"]["away"]["id"] != team_id:
                continue
            if args.fixture_id and fx["fixture"]["id"] == args.fixture_id:
                return fx
            names = (fx["teams"]["home"]["name"] + fx["teams"]["away"]["name"]).lower()
            if args.contra and args.contra.lower() in names:
                return fx
    return None


def replay(args, notifier, config, fcm=None):
    if not args.jugador:
        sys.exit("Modo repeticion necesita --jugador <key> (armando, raul, quinones).")
    player = next((p for p in config["players"] if p["key"] == args.jugador), None)
    if not player:
        sys.exit(f"No hay jugador con key='{args.jugador}' en {CONFIG_FILE}.")
    target = next((t for t in player["targets"] if t["key"] == args.solo), None) \
        if args.solo else player["targets"][0]
    if not target:
        sys.exit(f"No hay objetivo con key='{args.solo}' para {player['key']} en {CONFIG_FILE}.")
    meta = find_cached_fixture(args, target["team_id"])
    if not meta:
        sys.exit("No encontré ese partido en cache_api/ para ese equipo. Corre "
                 "primero la Fase 1 (con --buscar-gol si quieres uno con gol), o "
                 "revisa --contra / --fixture-id.")
    fid = meta["fixture"]["id"]
    lineups = api("/fixtures/lineups", {"fixture": fid}, use_cache=True) or []
    events = api("/fixtures/events", {"fixture": fid}, use_cache=True) or []
    home_id = meta["teams"]["home"]["id"]
    last = max([e["time"]["elapsed"] for e in events] + [90])

    print(f"\n▶️  Repetición [{player['name']} · {target['label']}]: "
         f"{meta['teams']['home']['name']} vs {meta['teams']['away']['name']} "
         f"({meta['league']['name']}) · {args.velocidad}s por minuto\n")
    sent = set()
    for step in [-45] + list(range(0, last + 1)) + ["FT"]:
        snap = json.loads(json.dumps(meta))
        snap["lineups"] = lineups
        if step == "FT":
            snap["fixture"]["status"] = {"short": "FT", "long": "Match Finished"}
            snap["events"] = events
        elif step < 0:
            log("— 45 min antes: se publica la alineación")
            snap["fixture"]["status"] = {"short": "NS", "long": "Not Started"}
            snap["events"] = []
        else:
            if step % 15 == 0:
                log(f"— minuto {step}")
            snap["fixture"]["status"] = {"short": "1H" if step <= 45 else "2H", "long": "En juego"}
            snap["events"] = [e for e in events if e["time"]["elapsed"] <= step]
            g = {"home": 0, "away": 0}
            for e in snap["events"]:
                if e["type"] == "Goal" and e["detail"] != "Missed Penalty":
                    g["home" if e["team"]["id"] == home_id else "away"] += 1
            snap["goals"] = g
        alerts, _ = analyze(snap, player, target)
        dispatch(alerts, sent, notifier, fcm, player["key"], target["key"])
        time.sleep(args.velocidad)
    print("\n✅ Repetición terminada.")


# ═══════════════════════════════════════════════════════ modo chequeo (cron)
# El plan gratuito de API-Football da 100 peticiones/día. Si el cron llamara
# a la API en cada corrida, correr cada 5-10 min ya se come la cuota en un
# rato — y ahora hay 3 jugadores en vez de 1. Para evitarlo, "chequeo"
# separa dos cosas:
#
#   · HORARIO: cuándo es el próximo partido de cada objetivo. Se guarda en
#     hormi_horario.json y solo se refresca cada REFRESH_HORAS horas (o si
#     el partido guardado ya pasó hace rato) — 1-3 peticiones por objetivo,
#     unas pocas veces al día.
#   · VENTANA EN VIVO: solo cuando el reloj está cerca del kickoff guardado
#     (VENTANA_ANTES antes, VENTANA_DESPUES después) se hace la petición
#     "cara" (/fixtures?id=) que trae alineación y eventos en vivo.
#
# Así el workflow de GitHub Actions puede correr cada pocos minutos SIEMPRE
# (no cuesta nada si no hay nada que ver) y solo gasta cuota real durante
# los partidos.
SCHEDULE_FILE = Path("hormi_horario.json")
REFRESH_HORAS = 6                      # cada cuánto se revisa si hay partido nuevo
VENTANA_ANTES = timedelta(minutes=20)  # desde cuándo antes del kickoff se vigila en vivo
VENTANA_DESPUES = timedelta(hours=3)   # hasta cuándo después se sigue vigilando


def now_iso():
    return datetime.now(LOCAL_TZ).isoformat()


def _parse(iso):
    return datetime.fromisoformat(iso) if iso else None


def schedule_stale(entry):
    """True si hay que gastar peticiones para refrescar el horario de este objetivo."""
    if not entry:
        return True
    now = datetime.now(LOCAL_TZ)
    kickoff = _parse(entry.get("kickoff"))
    if kickoff and now > kickoff + VENTANA_DESPUES:
        return True  # ese partido ya se jugó, hay que buscar el siguiente
    refreshed = _parse(entry.get("refreshed_at"))
    if not refreshed or now - refreshed > timedelta(hours=REFRESH_HORAS):
        return True  # por si el calendario cambió (aplazamientos, etc.)
    return False


def in_live_window(entry):
    kickoff = _parse(entry.get("kickoff")) if entry else None
    if not kickoff:
        return False
    now = datetime.now(LOCAL_TZ)
    return kickoff - VENTANA_ANTES <= now <= kickoff + VENTANA_DESPUES


def refresh_schedule(target):
    """1-3 peticiones: hoy → próximo (si es pronto) → último jugado."""
    team_id, season = target["team_id"], target["season"]
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    todays = api("/fixtures", {"team": team_id, "season": season, "date": today,
                               "timezone": "America/Mexico_City"}) or []
    if todays:
        return todays[0], "hoy"

    upcoming = api("/fixtures", {"team": team_id, "next": 1}) or []
    if upcoming:
        kickoff = datetime.fromtimestamp(upcoming[0]["fixture"]["timestamp"], LOCAL_TZ)
        if kickoff - datetime.now(LOCAL_TZ) <= timedelta(hours=REFRESH_HORAS + 1):
            return upcoming[0], "próximo"
        # el próximo está lejos: igual lo guardamos para mostrar en la app,
        # pero no hace falta vigilarlo en vivo todavía
        return upcoming[0], "próximo (lejos)"

    last = api("/fixtures", {"team": team_id, "last": 1}) or []
    if last:
        return last[0], "último"
    if last_api_error:
        log(f"⚠️  {target['label']}: sin horario — error de API: {last_api_error}")
    return None, None


def check_once(player, target, config, notifier, sent_by_target, status_out, schedule, fcm=None):
    label, emoji, key = target["label"], target["emoji"], target["key"]
    if not target.get("team_id"):
        log(f"⚠️  {player['name']} · {label}: sin team_id en {CONFIG_FILE}, se salta (corre Fase 1).")
        return

    entry = schedule.get(key, {})
    if schedule_stale(entry):
        fx_meta, why = refresh_schedule(target)
        if not fx_meta:
            schedule[key] = {"refreshed_at": now_iso(), "last_error": last_api_error}
            status_out[key] = {
                "label": label, "emoji": emoji, "status": "sin_partido", "checked_at": now_iso(),
                "api_error": last_api_error}
            return
        entry = {
            "fixture_id": fx_meta["fixture"]["id"],
            "kickoff": datetime.fromtimestamp(fx_meta["fixture"]["timestamp"], LOCAL_TZ).isoformat(),
            "why": why,
            "refreshed_at": now_iso(),
        }
        schedule[key] = entry
    else:
        why = entry.get("why")

    if not in_live_window(entry):
        # Nada que vigilar todavía / ya pasó la ventana: no gasta petición,
        # deja el status.json como estaba (o marca "sin_partido" si no había nada).
        status_out.setdefault(key, {
            "label": label, "emoji": emoji, "status": "sin_partido", "checked_at": now_iso()})
        return

    snap = api("/fixtures", {"id": entry["fixture_id"]})
    if not snap:
        return
    snap = snap[0]

    sent = sent_by_target.setdefault(key, set())
    alerts, info = analyze(snap, player, target)
    n = dispatch(alerts, sent, notifier, fcm, player["key"], key)
    if n:
        log(f"{emoji} {player['name']} · {label}: {n} alerta(s) nueva(s)")

    status_out[key] = {
        "label": label, "emoji": emoji, "checked_at": now_iso(),
        "match_reference": why, **info, "kickoff": entry["kickoff"],
    }


def chequeo(args, notifier, config, fcm=None):
    state = load_json(STATE_FILE, {})
    sent_raw = state.get("sent", {})
    schedule_raw = load_json(SCHEDULE_FILE, {})
    status_raw = load_json(STATUS_FILE, {}).get("players", {})

    valid_player_keys = {p["key"] for p in config["players"]}
    # Poda jugadores que ya no están en hormi_config.json — si no, se
    # quedan pegados en status.json para siempre y la app los sigue
    # mostrando como si se siguieran vigilando.
    removed_players = [k for k in list(status_raw) if k not in valid_player_keys]
    for k in removed_players:
        del status_raw[k]
    sent_raw = {k: v for k, v in sent_raw.items() if k in valid_player_keys}
    schedule_raw = {k: v for k, v in schedule_raw.items() if k in valid_player_keys}
    if removed_players:
        log(f"🧹 Se quitaron de status.json jugadores que ya no están en "
           f"{CONFIG_FILE}: {', '.join(removed_players)}")

    players = config["players"]
    if args.jugador:
        players = [p for p in players if p["key"] == args.jugador]
        if not players:
            sys.exit(f"No hay jugador con key='{args.jugador}' en {CONFIG_FILE}.")

    status_out, sent_out, schedule_out = {}, {}, {}
    processed = set()

    for player in players:
        pkey = player["key"]
        valid_target_keys = {t["key"] for t in player["targets"]}
        targets = player["targets"]
        if args.solo:
            targets = [t for t in targets if t["key"] == args.solo]
            if not targets:
                if args.jugador:
                    sys.exit(f"No hay objetivo con key='{args.solo}' para {pkey}.")
                continue  # este jugador no tiene ese objetivo: se salta sin error

        sent_by_target = {k: set(v) for k, v in sent_raw.get(pkey, {}).items()
                          if k in valid_target_keys}
        status_for_player = {k: v for k, v in status_raw.get(pkey, {}).get("targets", {}).items()
                             if k in valid_target_keys}
        schedule_for_player = {k: v for k, v in schedule_raw.get(pkey, {}).items()
                               if k in valid_target_keys}

        removed_targets = [k for k in status_raw.get(pkey, {}).get("targets", {})
                          if k not in valid_target_keys]
        if removed_targets:
            log(f"🧹 {player['name']}: se quitaron objetivos que ya no están en "
               f"{CONFIG_FILE}: {', '.join(removed_targets)}")

        for target in targets:
            try:
                check_once(player, target, config, notifier, sent_by_target,
                          status_for_player, schedule_for_player, fcm)
            except Exception as err:
                log(f"⚠️  {player['name']} · {target['label']}: error en el chequeo — {err}")

        status_out[pkey] = {"name": player["name"], "player_id": player.get("player_id"),
                            "targets": status_for_player}
        sent_out[pkey] = {k: sorted(v)[-300:] for k, v in sent_by_target.items()}
        schedule_out[pkey] = schedule_for_player
        processed.add(pkey)

    # Jugadores que este corrida no tocó (filtrados por --jugador o sin
    # objetivos tras --solo): conserva su estado tal cual estaba.
    for pkey in valid_player_keys - processed:
        if pkey in status_raw:
            status_out[pkey] = status_raw[pkey]
        if pkey in sent_raw:
            sent_out[pkey] = sent_raw[pkey]
        if pkey in schedule_raw:
            schedule_out[pkey] = schedule_raw[pkey]

    save_json(STATE_FILE, {"sent": sent_out})
    save_json(SCHEDULE_FILE, schedule_out)
    save_json(STATUS_FILE, {
        "updated_at": now_iso(),
        "app_name": config.get("app_name", ""),
        "players": status_out})
    log(f"✅ chequeo listo · {STATUS_FILE}, {STATE_FILE} y {SCHEDULE_FILE} actualizados")


# ═══════════════════════════════════════════════════════ main
def main():
    parser = argparse.ArgumentParser(description="Motor de alertas · EuroGoalMX (multi-jugador)")
    parser.add_argument("modo", choices=["prueba", "repeticion", "chequeo"])
    parser.add_argument("--jugador", help="Limita a un jugador por su 'key' (armando, raul, quinones)")
    parser.add_argument("--solo", help="Limita a un objetivo por su 'key' (olympiacos, seleccion, wolves, al_qadsiah)")
    parser.add_argument("--contra", help="Rival del partido a repetir (ej. Juarez)")
    parser.add_argument("--fixture-id", type=int)
    parser.add_argument("--velocidad", type=float, default=0.5,
                        help="Segundos reales por minuto de partido (repetición)")
    parser.add_argument("--solo-consola", action="store_true", help="No manda push, solo imprime")
    args = parser.parse_args()

    config = load_config()
    topic = None if args.solo_consola else get_topic(config)
    notifier = Notifier(topic, console_only=args.solo_consola)
    fcm = None if args.solo_consola else FcmSender(config.get("fcm_project_id"))
    app_name = config.get("app_name", "EuroGoalMX")

    if args.modo == "prueba":
        notifier.send(f"⚽ {app_name} conectada",
                      "Si ves esto, las alertas llegarán a tu celular.", 4)
        if fcm and fcm.enabled:
            fcm.send("info", f"⚽ {app_name} conectada",
                     "El push real (FCM) también está funcionando.")
    elif args.modo == "repeticion":
        replay(args, notifier, config, fcm)
    else:
        chequeo(args, notifier, config, fcm)


if __name__ == "__main__":
    main()

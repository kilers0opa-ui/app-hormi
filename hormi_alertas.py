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

  python hormi_alertas.py vigilar
      Lo que usa el workflow: igual que "chequeo", pero si hay un partido en
      curso o por empezar se queda vivo y repite la pasada cada minuto
      (hasta que no quede ningún partido por vigilar). Evita el atraso del
      cron de GitHub. Con HORMI_GIT_PUSH=1 publica el estado a git en cada
      pasada donde haya algo nuevo.

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
import re
import secrets
import subprocess
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
STATS_FILE = Path("player_stats.json")     # historial por competición (Estadísticas)
PENDING_VIDEOS_FILE = Path("videos_pendientes.json")  # goles a los que hormi_videos.py --gol les buscará video

LOCAL_TZ = timezone(timedelta(hours=-6))   # hora del centro de México

LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}
FINISHED = {"FT", "AET", "PEN"}
CANCELLED = {"PST", "CANC", "ABD", "AWD", "WO", "SUSP"}
NOT_STARTED = {"NS", "TBD"}

MIN_SECONDS_BETWEEN_REQUESTS = 0.3         # plan Pro: 300 peticiones/min (en el gratuito eran 10/min → 6.5 s)
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


def display_name(player):
    """Nombre completo SIN el apodo entre comillas ('Armando "La Hormiga" González'
    → 'Armando González'). Las notificaciones usan el nombre del jugador; el apodo
    (El Chaquito, Morita...) queda solo para las de gol."""
    return re.sub(r'\s*"[^"]*"\s*', " ", player["name"]).strip()


def api(endpoint, params, use_cache=False):
    """GET a API-Football. Misma caché que la Fase 1 (mismos nombres)."""
    global _last_request_at, last_api_error
    last_api_error = None
    key = endpoint.strip("/").replace("/", "_") + "_" + "_".join(
        f"{k}-{v}" for k, v in sorted(params.items()))
    key = key.replace("/", "_")   # p.ej. timezone=America/Mexico_City: la "/" rompía el nombre del archivo
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
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        save_json(cache_file, data)
    except OSError as err:   # la caché es solo una comodidad: nunca debe tumbar una consulta que sí funcionó
        log(f"ℹ️  no se pudo guardar la caché ({err})")
    return data.get("response", [])


def log(msg):
    print(f"[{datetime.now(LOCAL_TZ):%H:%M:%S}] {msg}")


def minute_txt(t):
    return f"{t['elapsed']}+{t['extra']}'" if t.get("extra") else f"{t['elapsed']}'"


# ═══════════════════════════════════════════════════════ notificaciones
def ntfy_activo():
    """ntfy está APAGADO desde que la app recibe push real (FCM): hormi_config.json
    "ntfy_activo": false. Para volver a usarlo, ponerlo en true (o quitar la llave)."""
    try:
        return bool(json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("ntfy_activo", True))
    except Exception:
        return True


class Notifier:
    """Envía push con ntfy (https://ntfy.sh). Sin cuenta, gratis."""

    def __init__(self, topic, console_only=False):
        self.topic = topic
        self.console_only = console_only

    def send(self, title, message, priority=3):
        log(f"📲 {title} — {message}")
        if self.console_only or not ntfy_activo():
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
    TOKEN_MAX_AGE = 45 * 60   # el token de Google dura 60 min; en modo "vigilar" el proceso vive horas, hay que renovarlo

    def __init__(self, project_id):
        self.project_id = project_id
        self.enabled = False
        self._token = None
        self._token_at = 0.0
        self._creds = None
        self._request = None

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
            self._creds = service_account.Credentials.from_service_account_info(
                info, scopes=[self.SCOPE])
            self._request = google.auth.transport.requests.Request()
            self._refresh_token()
            self.enabled = True
        except Exception as err:
            log(f"⚠️  FCM: no se pudo autenticar con la cuenta de servicio — {err}")

    def _refresh_token(self):
        self._creds.refresh(self._request)
        self._token = self._creds.token
        self._token_at = time.time()

    def send(self, tipo, title, body, player_key=None, team_key=None, player_keys=None):
        """tipo: 'goal' (dispara la pantalla de ¡GOOOOL! en la app) o
        cualquier otro string para una notificación normal. La app usa el
        tipo para decidir sonido/vibración/silencio (pantalla de
        configuración) y a qué pantalla abre al tocarla (ver
        alert_type() y HormiFirebaseMessagingService.kt). player_keys: para
        avisos que son de varios jugadores a la vez (p.ej. "Juega mañana" de
        la Selección): la app lo muestra si AL MENOS UNO es Favorito. player_key
        identifica de qué jugador es la alerta (p.ej. 'armando', 'raul',
        'quinones'), para que la app filtre por Favoritos. team_key es el
        objetivo (p.ej. 'olympiacos', 'seleccion', 'wolves'), para que la
        app elija la imagen de fondo correcta en notificaciones de gol."""
        if not self.enabled:
            return
        if time.time() - self._token_at > self.TOKEN_MAX_AGE:
            try:
                self._refresh_token()
            except Exception as err:
                log(f"⚠️  FCM: no se pudo renovar el token — {err} (se intenta con el anterior)")
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        payload = {
            "message": {
                "topic": self.TOPIC,
                "data": {"type": tipo, "title": title, "body": body,
                         "player_key": player_key or "", "team_key": team_key or "",
                         "player_keys": ",".join(player_keys or [])},
                # Mensajes de solo-datos con prioridad normal los puede retrasar
                # el modo Doze del teléfono (minutos u horas). HIGH los entrega
                # al instante. TTL: un aviso de gol que no se pudo entregar en
                # 30 min ya no sirve — mejor descartarlo que llegar tarde.
                "android": {"priority": "HIGH", "ttl": "1800s"},
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
    apodo = player.get("apodo") or player["name"]   # solo para las alertas de GOL
    nombre = display_name(player)                    # para todas las demás
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
        add("cancelado", f"⚠️ {emoji} {label} ({nombre}): partido suspendido/pospuesto",
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
            add("alineacion", f"⭐ {emoji} ¡{nombre} titular con {label}!", f"{match} · {league}", 4)
        elif role == "banca":
            add("alineacion", f"🪑 {emoji} {nombre} arranca en la banca ({label})",
                f"{match} · {league}")
        else:
            add("alineacion", f"❌ {emoji} {nombre} no convocado ({label})",
                f"{match} · {league}", 2)

    if status in LIVE | FINISHED:
        add("inicio", f"⚽ {emoji} Arrancó: {label} ({nombre})", match, 2)

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
                add(k + ":ast", f"🎯 {emoji} ¡Asistencia de {nombre}! {m}",
                    f"{label} · Gol de {e['player']['name']} · {score()}", 4)
        elif etype == "var" and "goal" in detail.lower() and player_id in (pid, aid):
            add(k, f"🚫 {emoji} VAR anula jugada de gol de {nombre} {m}", f"{label} · {detail}")
        elif etype == "subst" and player_id in (pid, aid):
            if role == "banca" and not entered:
                entered, in_minute = True, t["elapsed"]
                add(k, f"🔄 {emoji} ¡Entra {nombre}! {m}", f"{label} · {score()}", 4)
            else:
                out_minute = t["elapsed"]
                add(k, f"↩️ {emoji} Sale {nombre} {m}", f"{label} · {score()}", 2)

    summary = None
    if status in FINISHED:
        if not data.get("lineups"):
            # Sin alineación no se pueden saber los minutos: no afirmar
            # "No tuvo minutos" (sería falso) ni sumar nada a las estadísticas.
            detalle = "Sin datos de minutos para este partido"
            if goals or assists:
                detalle += f" · {goals} gol(es) · {assists} asistencia(s)"
        else:
            if role == "titular":
                played = out_minute or 90
            elif entered:
                played = max(1, (out_minute or 90) - in_minute)
            else:
                played = 0
            detalle = (f"{played} min · {goals} gol(es) · {assists} asistencia(s)") \
                if played else "No tuvo minutos."
            summary = {"minutes": played, "goals": goals, "assists": assists}
        add("final", f"🏁 {emoji} Final {label} ({nombre}): {score()}", detalle, 3)

    current_status = "no_convocado" if data.get("lineups") and role is None else \
        role or ("en_cancha" if status in LIVE else
                 ("finalizado" if status in FINISHED else "sin_partido"))

    return alerts, {"status": current_status, "match": match, "league": league,
                     "score": score(), "goals": goals, "assists": assists,
                     "summary": summary}


def apply_live_delta(player, target, snap, info):
    """Cuando un partido de un objetivo TERMINA, suma sus minutos/goles/
    asistencias al histórico de player_stats.json — así Estadísticas no
    depende de que hormi_historial.py (Fase 3) vuelva a correr para verse
    al día; ese cálculo periódico simplemente reemplaza este ajuste con el
    total real de la API la próxima vez que corra, así que nunca hay doble
    conteo. Usa el fixture_id para no sumar el mismo partido dos veces —
    check_once() lo sigue viendo hasta VENTANA_DESPUES después del final."""
    summary = info.get("summary")
    # Sin minutos (no convocado, o en banca sin entrar) no cuenta como partido
    # jugado: antes sumaba +1 PJ con 0 minutos.
    if not summary or not summary.get("minutes"):
        return

    stats = load_json(STATS_FILE, {"updated_at": None, "players": {}})
    stats.setdefault("players", {})
    entry = stats["players"].setdefault(
        player["key"], {"seasons": {}, "applied_fixtures": {}})
    applied = entry.setdefault("applied_fixtures", {}).setdefault(target["key"], [])

    fixture_id = snap["fixture"]["id"]
    if fixture_id in applied:
        return
    applied.append(fixture_id)
    del applied[:-500]  # no crecer sin límite

    league = snap.get("league") or {}
    # La temporada sale del propio partido (la API la trae en league.season),
    # no del config: así la Selección y los clubes no dependen de que
    # "season" esté bien puesto a mano (los torneos de selecciones usan
    # año calendario, los clubes europeos el año en que empieza la temporada).
    season_key = str(league.get("season") or target["season"])
    rows = entry.setdefault("seasons", {}).setdefault(season_key, [])
    row = next((r for r in rows if r.get("league_id") == league.get("id")), None)
    if row is None:
        row = {"league_id": league.get("id"), "league_name": league.get("name") or target["label"],
               "team_id": target.get("team_id"), "team_name": target["label"],
               "played": 0, "goals": 0, "assists": 0, "minutes": 0}
        rows.append(row)
    row["played"] += 1
    row["goals"] += summary["goals"]
    row["assists"] += summary["assists"]
    row["minutes"] += summary["minutes"]

    stats["updated_at"] = now_iso()
    save_json(STATS_FILE, stats)
    log(f"📊 {player['name']} · {target['label']}: histórico actualizado "
        f"({league.get('name') or target['label']} {row['played']}PJ "
        f"{row['goals']}G {row['assists']}A)")


def register_goal_for_video(key, player_key, team_key):
    """Anota un gol recién alertado en videos_pendientes.json para que el
    workflow aparte video-gol.yml (hormi_videos.py --gol) le busque video a
    los ~15 y ~30 min.

    REGLA: esto NUNCA puede retrasar ni romper una alerta de gol. Por eso se
    llama SOLO después de que la alerta ya salió (ntfy + FCM), no hace red
    (solo escribe un archivo local pequeño) y se traga cualquier error. Este
    archivo lo escribe únicamente el motor de alertas; el lado de videos
    nunca lo modifica (así los dos workflows jamás chocan al hacer push)."""
    try:
        now = datetime.now(timezone.utc)
        data = load_json(PENDING_VIDEOS_FILE, {"goles": []})
        goles = data.setdefault("goles", [])
        if any(g.get("key") == key for g in goles):
            return
        cutoff = now - timedelta(hours=6)   # lo viejo ya no le sirve a nadie: se poda aquí
        goles[:] = [g for g in goles
                    if datetime.fromisoformat(g["goal_seen_at"]) >= cutoff]
        goles.append({
            "key": key,
            "player_key": player_key,
            "team_key": team_key,
            "goal_seen_at": now.isoformat(),
        })
        tmp = PENDING_VIDEOS_FILE.with_suffix(".tmp")
        save_json(tmp, data)
        os.replace(tmp, PENDING_VIDEOS_FILE)
    except Exception as err:
        log(f"ℹ️  No se pudo anotar el gol para buscarle video (la alerta ya salió): {err}")


def register_final_for_video(key, player_key, team_key, match_info):
    """Anota el FINAL de un partido en videos_pendientes.json ("finales") para
    que video-gol.yml (hormi_videos.py --eventos) busque el resumen del partido
    (a los ~30 y ~60 min) y refresque la noticia principal de cada jugador.

    Un solo evento por partido (fixture): si varios jugadores del roster
    comparten el mismo partido, se agregan a la lista "jugadores" del mismo
    evento. Mismas reglas que register_goal_for_video: solo después de que la
    alerta ya salió, sin red, y cualquier error se traga."""
    try:
        fid = (match_info or {}).get("fixture_id")
        if fid is None:
            return
        now = datetime.now(timezone.utc)
        data = load_json(PENDING_VIDEOS_FILE, {"goles": []})
        finales = data.setdefault("finales", [])
        cutoff = now - timedelta(hours=6)
        finales[:] = [f for f in finales
                      if datetime.fromisoformat(f["final_seen_at"]) >= cutoff]
        summary = match_info.get("summary") or {}
        jugador = {
            "player_key": player_key,
            "team_key": team_key,
            "minutes": summary.get("minutes", 0),
            "goals": summary.get("goals", 0),
            "assists": summary.get("assists", 0),
        }
        event = next((f for f in finales if f.get("fixture_id") == fid), None)
        if event is None:
            event = {
                "fixture_id": fid,
                "home": match_info.get("home"),
                "away": match_info.get("away"),
                "league": match_info.get("league"),
                "score": match_info.get("score"),
                "final_seen_at": now.isoformat(),
                "jugadores": [],
            }
            finales.append(event)
        if any(j.get("player_key") == player_key and j.get("team_key") == team_key
               for j in event["jugadores"]):
            return
        event["jugadores"].append(jugador)
        tmp = PENDING_VIDEOS_FILE.with_suffix(".tmp")
        save_json(tmp, data)
        os.replace(tmp, PENDING_VIDEOS_FILE)
    except Exception as err:
        log(f"ℹ️  No se pudo anotar el final para buscarle resumen (la alerta ya salió): {err}")


def alert_type(key):
    """Tipo de notificación a partir de la llave de la alerta. La app deja
    configurar sonido/vibración/silencio por tipo y abre Inicio o Videos
    según el tipo. 'goal' es el único que dispara la pantalla de ¡GOOOOL!.
    Tipos: goal, assist, lineup, start, sub, final, incident (VAR/suspendido),
    reminder (juega mañana), video, transfer."""
    if key.endswith(":gol"):
        return "goal"
    if key.endswith(":ast"):
        return "assist"
    if key.endswith(":alineacion"):
        return "lineup"
    if key.endswith(":inicio"):
        return "start"
    if key.endswith(":final"):
        return "final"
    if key.endswith(":cancelado") or ":var:" in key:
        return "incident"
    if ":subst:" in key:
        return "sub"
    return "info"


def dispatch(alerts, sent, notifier, fcm=None, player_key=None, team_key=None,
             register_video=True, match_info=None):
    new = 0
    for key, title, message, priority in alerts:
        if key not in sent:
            notifier.send(title, message, priority)
            if fcm:
                fcm.send(alert_type(key), title, message, player_key, team_key)
            sent.add(key)
            new += 1
            # Va al final, con la alerta ya enviada. No aplica en repeticiones
            # ni en modo solo-consola.
            if register_video and not getattr(notifier, "console_only", False):
                if key.endswith(":gol"):
                    register_goal_for_video(key, player_key, team_key)
                elif key.endswith(":final") and match_info:
                    register_final_for_video(key, player_key, team_key, match_info)
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
        dispatch(alerts, sent, notifier, fcm, player["key"], target["key"], register_video=False)  # repetición: no es un gol real
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
CONVOCATORIA_FILE = Path("hormi_convocatoria.json")   # convocatoria de la Selección (la escribe hormi_convocatoria.py)
REFRESH_HORAS = 6                      # cada cuánto se revisa si hay partido nuevo
VENTANA_ANTES = timedelta(minutes=75)  # desde cuándo antes del kickoff se vigila en vivo (la API publica la alineación ~60 min antes)
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
    ko = _parse(entry.get("kickoff"))
    if ko and not entry.get("done") and ko - VENTANA_ANTES - ARRANQUE_EXTRA <= now <= ko + VENTANA_DESPUES:
        # Partido en curso o por empezar: el horario NO se toca. Refrescarlo ahora
        # podría sustituir el partido por el siguiente (la búsqueda "próximo" ya no
        # devuelve el que está en juego) y se perderían las alertas a media partido.
        return False
    if entry.get("home") and "home_logo" not in entry:
        return True   # horario viejo sin escudos de los equipos: se completa una vez
    refreshed = _parse(entry.get("refreshed_at"))
    if not refreshed or now - refreshed > timedelta(hours=REFRESH_HORAS):
        return True  # por si el calendario cambió (aplazamientos, etc.)
    kickoff = _parse(entry.get("kickoff"))
    if kickoff and now > kickoff + VENTANA_DESPUES and refreshed < kickoff + VENTANA_DESPUES:
        # El partido guardado ya venció y todavía no se ha buscado el siguiente:
        # se busca UNA vez. (Antes se repetía en cada corrida hasta medianoche,
        # porque la búsqueda "partido de hoy" devolvía el mismo partido ya terminado.)
        return True
    return False


def in_live_window(entry):
    kickoff = _parse(entry.get("kickoff")) if entry else None
    if not kickoff or entry.get("done"):
        return False
    now = datetime.now(LOCAL_TZ)
    return kickoff - VENTANA_ANTES <= now <= kickoff + VENTANA_DESPUES


def refresh_schedule(target, known_season=None):
    """1-3 peticiones: hoy → próximo (si es pronto) → último jugado.

    known_season: la temporada que la propia API le asignó al último partido
    guardado de este objetivo (league.season). Se usa en lugar del "season"
    del config, así la búsqueda "hoy" se ajusta sola con los cambios de
    temporada/año y no hay que editar el config cada año."""
    team_id, season = target["team_id"], known_season or target["season"]
    now = datetime.now(LOCAL_TZ)
    today = now.strftime("%Y-%m-%d")
    # La API EXIGE "season" junto con team+date ("The Season field is required",
    # comprobado con el plan Pro el 2-oct-2026). Si por temporada del config
    # no aparece nada (la Selección juega torneos de año calendario), no pasa
    # nada grave: más abajo se cae a "próximo" (next=1), que no necesita season.
    todays = api("/fixtures", {"team": team_id, "season": season, "date": today,
                               "timezone": "America/Mexico_City"}) or []
    # Solo cuentan los partidos de hoy que siguen vigentes (aún no vence su
    # ventana de vigilancia); uno ya terminado y vencido no debe volver a
    # guardarse como "el partido de este objetivo".
    vigentes = sorted(
        (f for f in todays
         if datetime.fromtimestamp(f["fixture"]["timestamp"], LOCAL_TZ) + VENTANA_DESPUES >= now),
        key=lambda f: f["fixture"]["timestamp"])
    if vigentes:
        return vigentes[0], "hoy"

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


def _fixture_meta(fx):
    """Datos del partido que se guardan en el horario (para el aviso 'Juega mañana')."""
    return {"home": fx["teams"]["home"]["name"], "away": fx["teams"]["away"]["name"],
            "home_logo": fx["teams"]["home"].get("logo"), "away_logo": fx["teams"]["away"].get("logo"),
            "league": (fx.get("league") or {}).get("name"),
            "season": (fx.get("league") or {}).get("season"),
            "fx_status": fx["fixture"]["status"]["short"]}


# ═══════════════════════════════════════════════════════ aviso "Juega mañana"
# Un solo aviso por partido, en la noche (REMINDER_HORA, hora CDMX) del día
# anterior, con la hora del partido ya convertida a hora CDMX. Mucho mejor que
# "juega hoy" por la mañana: los partidos en Europa caen de madrugada (9 pm en
# Inglaterra = 6 am en CDMX) y el aviso llegaría tarde. NO dice nada de
# convocatoria: eso solo se sabe ~1 h antes (alineación), y las listas de las
# noticias suelen cambiar.
REMINDER_HORA = 21
_NEXT_CACHE = {}     # team_id -> próximo partido, válido durante UNA pasada


def _hora_cdmx(dt):
    h12 = dt.hour % 12 or 12
    return f"{h12}:{dt.minute:02d} {'a.m.' if dt.hour < 12 else 'p.m.'}"


def _entry_future(entry, now):
    ko = _parse(entry.get("kickoff")) if entry else None
    return bool(ko and ko > now and entry.get("home"))


def _advance_entry(target, entry, now):
    """El horario de este objetivo apunta a un partido ya pasado (o sin datos
    del rival): busca el próximo (1 petición, una vez al día por objetivo) y
    lo guarda en el horario. Devuelve el horario (nuevo o el mismo)."""
    today = now.strftime("%Y-%m-%d")
    if entry and entry.get("next_checked") == today:
        return entry
    tid = target["team_id"]
    if tid not in _NEXT_CACHE:
        _NEXT_CACHE[tid] = api("/fixtures", {"team": tid, "next": 1}) or []
    nxt = _NEXT_CACHE[tid]
    if not nxt:
        entry = dict(entry or {})
        entry["next_checked"] = today
        return entry
    fx = nxt[0]
    new = {
        "fixture_id": fx["fixture"]["id"],
        "kickoff": datetime.fromtimestamp(fx["fixture"]["timestamp"], LOCAL_TZ).isoformat(),
        "why": "próximo (lejos)", "refreshed_at": now_iso(), "next_checked": today,
        **_fixture_meta(fx),
    }
    if (entry and entry.get("done") and entry.get("fixture_id") == new["fixture_id"]
            and entry.get("kickoff") == new["kickoff"]):
        new["done"] = True
    return new


def send_reminders(config, notifier, fcm, schedule_out, reminders_sent):
    """Manda 'Juega mañana' (una vez por partido). reminders_sent es un set
    de ids 'fixture_id:fecha' que se va llenando. Devuelve cuántos mandó."""
    now = datetime.now(LOCAL_TZ)
    if now.hour < REMINDER_HORA:
        return 0
    tomorrow = (now + timedelta(days=1)).date()
    groups = {}
    for player in config["players"]:
        if not player.get("player_id"):
            continue
        pkey = player["key"]
        for target in player["targets"]:
            if not target.get("team_id"):
                continue
            sched = schedule_out.setdefault(pkey, {})
            entry = sched.get(target["key"]) or {}
            ko0 = _parse(entry.get("kickoff"))
            if (ko0 and ko0 <= now <= ko0 + VENTANA_DESPUES and not entry.get("done")):
                continue   # partido en curso o recién terminado: nunca se le cambia el horario
            try:
                if not _entry_future(entry, now):
                    entry = _advance_entry(target, entry, now)
                    sched[target["key"]] = entry
            except Exception as err:
                log(f"⚠️  {player['name']} · {target['label']}: no se pudo buscar el próximo partido — {err}")
                continue
            ko = _parse(entry.get("kickoff"))
            if not ko or not entry.get("home") or ko <= now or ko.date() != tomorrow:
                continue
            if entry.get("fx_status") in CANCELLED:
                continue
            rid = f"{entry['fixture_id']}:{ko.date().isoformat()}"
            if rid in reminders_sent:
                continue
            if pkey in _no_convocados(target, entry):
                continue   # no convocado a ese partido de la Selección: sin aviso
            g = groups.setdefault(rid, {"entry": entry, "target": target, "players": []})
            g["players"].append(player)

    sent_n = 0
    for rid, g in groups.items():
        e, target, players = g["entry"], g["target"], g["players"]
        ko = _parse(e["kickoff"])
        hora = "hora por confirmar" if e.get("fx_status") == "TBD" else f"{_hora_cdmx(ko)} (hora centro)"
        quien = display_name(players[0])
        if len(players) == 1:
            title = f"📅 Mañana juega {quien} ({target['label']})"
        else:
            title = f"📅 Mañana juega {target['label']}"
        body = f"{e['home']} vs {e['away']} · {hora}" + (f" · {e['league']}" if e.get("league") else "")
        notifier.send(title, body, 3)
        if fcm:
            fcm.send("reminder", title, body, None, target["key"],
                     player_keys=[p["key"] for p in players])
        if not getattr(notifier, "console_only", False):
            reminders_sent.add(rid)
        sent_n += 1
    return sent_n


_WARNED = set()      # avisos que ya se mostraron en este proceso (el modo vigilar repite pasadas cada minuto)
_SNAP_CACHE = {}     # fixture_id -> foto del partido, válida solo durante UNA pasada


def warn_once(key, msg):
    if key not in _WARNED:
        _WARNED.add(key)
        log(msg)


def _no_convocados(target, entry):
    """Llaves de jugadores que NO están en la lista de la Selección para ESTE
    partido. La lista la escribe hormi_convocatoria.py (lee la convocatoria
    oficial en Wikipedia) con la ventana de fechas de esa convocatoria
    ("desde"/"hasta"); solo vale para partidos dentro de esa ventana, así que
    se vence sola. Sin archivo, fuera de ventana o con cualquier problema =
    nadie excluido (ante la duda, se muestra)."""
    if target.get("kind") != "seleccion" or not entry:
        return set()
    try:
        sel = (load_json(CONVOCATORIA_FILE, {}) or {}).get("seleccion") or {}
        ko = _parse(entry.get("kickoff"))
        if not ko or not sel.get("desde") or not sel.get("hasta"):
            return set()
        dia = ko.astimezone(LOCAL_TZ).date().isoformat()
        if sel["desde"] <= dia <= sel["hasta"]:
            return set(sel.get("no_convocados") or [])
    except Exception:
        pass
    return set()


def _en_concentracion(player_key, entry):
    """True si el jugador está convocado a la Selección y el partido de su CLUB
    cae dentro de la ventana de esa convocatoria (fecha FIFA): ese día está con
    el Tri, no con el club, así que un amistoso/partido del club no es suyo
    (p. ej. Genoa vs Rapid mientras Vásquez está concentrado con México).
    Sin archivo, fuera de ventana o cualquier problema = False (ante la duda, se muestra)."""
    try:
        sel = (load_json(CONVOCATORIA_FILE, {}) or {}).get("seleccion") or {}
        ko = _parse(entry.get("kickoff")) if entry else None
        if not ko or not sel.get("desde") or not sel.get("hasta"):
            return False
        dia = ko.astimezone(LOCAL_TZ).date().isoformat()
        return player_key in (sel.get("convocados") or []) and sel["desde"] <= dia <= sel["hasta"]
    except Exception:
        return False


def _aparece_en_alineacion(snap, player_id):
    for team in snap.get("lineups") or []:
        for slot in ("startXI", "substitutes"):
            if any(p["player"]["id"] == player_id for p in team.get(slot) or []):
                return True
    return False


def _next_match_status(target, entry, player_key=None):
    """Estado para la app de un objetivo sin partido en vigilancia: el próximo
    partido si el horario lo conoce, o "sin_partido" a secas."""
    base = {"label": target["label"], "emoji": target["emoji"],
            "crest_url": target.get("crest_url"), "status": "sin_partido",
            "checked_at": now_iso()}
    ko = _parse(entry.get("kickoff")) if entry else None
    if player_key and player_key in _no_convocados(target, entry):
        return base   # no está convocado: no se le muestra ese partido como su próximo
    if ko and entry.get("home") and ko > datetime.now(LOCAL_TZ) and entry.get("fx_status") not in CANCELLED:
        base.update({"match_reference": "próximo",
                     "match": f"{entry['home']} vs {entry['away']}",
                     "home": entry["home"], "away": entry["away"],
                     "home_logo": entry.get("home_logo"), "away_logo": entry.get("away_logo"),
                     "league": entry.get("league"), "kickoff": entry["kickoff"]})
    return base


def check_once(player, target, config, notifier, sent_by_target, status_out, schedule, fcm=None):
    label, emoji, key = target["label"], target["emoji"], target["key"]
    crest_url = target.get("crest_url")
    if not target.get("team_id"):
        warn_once((player["key"], key, "team_id"),
                  f"⚠️  {player['name']} · {label}: sin team_id en {CONFIG_FILE}, se salta (corre Fase 1).")
        return
    if not player.get("player_id"):
        # Sin player_id el detector no puede reconocer al jugador en la
        # alineación y mandaría un "no convocado" FALSO en cada partido.
        warn_once((player["key"], key, "player_id"),
                  f"⚠️  {player['name']} · {label}: sin player_id en {CONFIG_FILE}, se salta (corre Fase 1).")
        return

    entry = schedule.get(key, {})
    if schedule_stale(entry):
        prev = entry
        fx_meta, why = refresh_schedule(target, prev.get("season"))
        if not fx_meta:
            schedule[key] = {"refreshed_at": now_iso(), "last_error": last_api_error}
            status_out[key] = {
                "label": label, "emoji": emoji, "crest_url": crest_url,
                "status": "sin_partido", "checked_at": now_iso(),
                "api_error": last_api_error}
            return
        entry = {
            "fixture_id": fx_meta["fixture"]["id"],
            "kickoff": datetime.fromtimestamp(fx_meta["fixture"]["timestamp"], LOCAL_TZ).isoformat(),
            "why": why,
            "refreshed_at": now_iso(),
            **_fixture_meta(fx_meta),
        }
        if (prev.get("done") and prev.get("fixture_id") == entry["fixture_id"]
                and prev.get("kickoff") == entry["kickoff"]):
            entry["done"] = True     # mismo partido (misma hora) que ya se cerró: no volver a vigilarlo
        schedule[key] = entry
    else:
        why = entry.get("why")

    if not in_live_window(entry):
        # Nada que vigilar todavía / ya pasó la ventana: no gasta petición.
        # En vez de dejar "Sin partido programado", la app muestra el PRÓXIMO
        # partido que ya conoce el horario (rival, liga, fecha/hora). Se
        # conserva el resultado del partido recién terminado durante 48 h
        # para que "Último partido" no desaparezca al instante.
        cur = status_out.get(key)
        ko = _parse((cur or {}).get("kickoff"))
        keep_last = bool(cur and cur.get("status") == "finalizado" and not cur.get("api_error")
                         and ko and datetime.now(LOCAL_TZ) - ko < timedelta(hours=48))
        # Un "finalizado" de club sin minutos (sin alineación) de un convocado al Tri
        # dentro de su ventana no es partido suyo: no se conserva como "Último partido".
        if (keep_last and target.get("kind") == "club" and _en_concentracion(player["key"], cur)
                and not ((cur.get("summary") or {}).get("minutes"))):
            keep_last = False
        if not keep_last:
            status_out[key] = _next_match_status(target, entry, player["key"])
        return

    # Varios jugadores comparten partido (p.ej. los 10 con la Selección): se
    # pide UNA sola vez por pasada y se reparte, en vez de una petición por jugador.
    fid = entry["fixture_id"]
    if fid not in _SNAP_CACHE:
        res = api("/fixtures", {"id": fid})
        _SNAP_CACHE[fid] = res[0] if res else None
    snap = _SNAP_CACHE[fid]
    if not snap:
        return

    # Convocado al Tri y su club juega en la ventana FIFA sin que aparezca en
    # la alineación: no es partido suyo (ni "En cancha" ni "Arrancó").
    if (target.get("kind") == "club" and _en_concentracion(player["key"], entry)
            and not _aparece_en_alineacion(snap, player["player_id"])):
        status_out[key] = _next_match_status(target, entry, player["key"])
        return

    sent = sent_by_target.setdefault(key, set())
    alerts, info = analyze(snap, player, target)
    match_info = {
        "fixture_id": snap["fixture"]["id"],
        "home": snap["teams"]["home"]["name"],
        "away": snap["teams"]["away"]["name"],
        "league": snap["league"]["name"],
        "score": info.get("score"),
        "summary": info.get("summary"),
    }
    n = dispatch(alerts, sent, notifier, fcm, player["key"], key, match_info=match_info)
    if n:
        log(f"{emoji} {player['name']} · {label}: {n} alerta(s) nueva(s)")
    try:
        apply_live_delta(player, target, snap, info)
    except Exception as err:
        log(f"⚠️  {player['name']} · {label}: no se pudo actualizar el histórico — {err}")

    status_out[key] = {
        "label": label, "emoji": emoji, "crest_url": crest_url, "checked_at": now_iso(),
        "match_reference": why, **info, "kickoff": entry["kickoff"],
        "home": snap["teams"]["home"]["name"], "away": snap["teams"]["away"]["name"],
        "home_logo": snap["teams"]["home"].get("logo"), "away_logo": snap["teams"]["away"].get("logo"),
    }

    # Partido cerrado (final o cancelado ya avisado): dejar de pedirlo a la API
    # el resto de la ventana. Antes se seguía pidiendo hasta kickoff + 3 h.
    st = snap["fixture"]["status"]["short"]
    closing_key = f"{player['key']}:{key}:{fid}:" + ("cancelado" if st in CANCELLED else "final")
    # "SUSP" (suspendido) puede reanudarse: ese no se da por cerrado.
    if (st in FINISHED or st in CANCELLED - {"SUSP"}) and closing_key in sent:
        entry["done"] = True


STATUS_HEARTBEAT = timedelta(minutes=60)   # la app muestra "Actualizado: <hora>" con este campo
_VOLATILE = {"updated_at", "checked_at"}


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in _VOLATILE}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def save_status(new):
    """Escribe status.json solo si cambió algo real (marcador, estado, alertas...)
    o si el "Actualizado" guardado ya tiene más de STATUS_HEARTBEAT. Antes se
    reescribía en cada corrida porque 'updated_at' siempre cambia → ~70 commits
    al día aunque no pasara nada."""
    old = load_json(STATUS_FILE, None)
    if old is not None and _strip_volatile(old) == _strip_volatile(new):
        old_ts = _parse(old.get("updated_at"))
        if old_ts and datetime.now(LOCAL_TZ) - old_ts < STATUS_HEARTBEAT:
            return False
    save_json(STATUS_FILE, new)
    return True


def chequeo(args, notifier, config, fcm=None):
    _SNAP_CACHE.clear()
    _NEXT_CACHE.clear()
    state = load_json(STATE_FILE, {})
    sent_raw = state.get("sent", {})
    reminders_sent = set(state.get("reminders", []))
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
                # También queda en status.json: los logs de Actions no se ven desde fuera,
                # y así un fallo repetido se diagnostica leyendo ese archivo.
                status_for_player[target["key"]] = {
                    "label": target["label"], "emoji": target["emoji"],
                    "crest_url": target.get("crest_url"), "status": "sin_partido",
                    "checked_at": now_iso(),
                    "api_error": {"error": f"{type(err).__name__}: {err}"[:300]}}

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

    # "Juega mañana": solo en pasadas completas (con --jugador/--solo el
    # horario de los demás no se toca).
    if not args.jugador and not args.solo:
        try:
            n = send_reminders(config, notifier, fcm, schedule_out, reminders_sent)
            if n:
                log(f"📅 {n} aviso(s) de 'Juega mañana'")
        except Exception as err:
            log(f"⚠️  no se pudieron mandar los avisos de 'Juega mañana' — {err}")
    # Los ids llevan la fecha del partido: se conservan los más recientes.
    reminders_keep = sorted(reminders_sent, key=lambda r: r.split(":", 1)[1])[-60:]
    save_json(STATE_FILE, {"sent": sent_out, "reminders": reminders_keep})
    save_json(SCHEDULE_FILE, schedule_out)
    save_status({
        "updated_at": now_iso(),
        "app_name": config.get("app_name", ""),
        "players": status_out})
    log(f"✅ chequeo listo · {STATUS_FILE}, {STATE_FILE} y {SCHEDULE_FILE} al día")


# ═══════════════════════════════════════════════════════ modo vigilar
# El cron de GitHub Actions dice "cada 5 min" pero en la práctica corre cada
# ~20-30 min (mediana medida: 22 min), así que una alerta de gol podía llegar
# 25 min tarde. "vigilar" lo resuelve sin depender de la puntualidad del cron:
# cuando hay un partido en curso o por empezar, UNA corrida se queda viva y
# hace una pasada de "chequeo" cada minuto (publicando el estado a git cuando
# hay algo nuevo). Si no hay partido cerca, hace una sola pasada y sale, igual
# que antes. El cron solo sirve de arranque (por eso el margen ARRANQUE_EXTRA)
# y de red de seguridad si la corrida larga muere.
ARRANQUE_EXTRA = timedelta(minutes=45)   # margen sobre VENTANA_ANTES: cubre el atraso del cron (se midió hasta ~29 min)
MAX_VIGILAR = timedelta(hours=5, minutes=40)   # el job de GitHub se corta a las 6 h
PASO_VIGILAR = 60                        # segundos entre pasadas
MIN_PAUSA_VIGILAR = 10
MAX_FALLOS_SEGUIDOS = 5

STATE_OWNED_FILES = (STATUS_FILE, STATE_FILE, SCHEDULE_FILE, PENDING_VIDEOS_FILE)


def active_soon(config):
    """¿Algún objetivo tiene un partido en curso o que empieza pronto?
    Mira hormi_horario.json (lo que ya se sabe), sin gastar peticiones."""
    now = datetime.now(LOCAL_TZ)
    schedule = load_json(SCHEDULE_FILE, {})
    for p in config["players"]:
        if not p.get("player_id"):
            continue
        for t in p["targets"]:
            e = (schedule.get(p["key"]) or {}).get(t["key"]) or {}
            ko = _parse(e.get("kickoff"))
            if not ko or e.get("done"):
                continue
            if ko - VENTANA_ANTES - ARRANQUE_EXTRA <= now <= ko + VENTANA_DESPUES:
                return True
    return False


def _git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True)


def publish_state():
    """Sube a git el estado del motor si cambió (solo con HORMI_GIT_PUSH=1, que
    pone el workflow). Pensado para el modo vigilar, donde el proceso vive
    horas: si la máquina muriera, lo ya avisado quedaría guardado y no se
    repetirían alertas. Nunca debe tumbar el motor: cualquier fallo solo se
    registra."""
    if os.environ.get("HORMI_GIT_PUSH") != "1":
        return
    try:
        files = [str(f) for f in (*STATE_OWNED_FILES, STATS_FILE) if f.exists()]
        _git("add", *files)
        if _git("diff", "--cached", "--quiet").returncode == 0:
            return
        _git("commit", "-m", "chore: actualiza estado del chequeo [skip ci]")
        for attempt in range(3):
            if _git("pull", "--rebase", "origin", "main").returncode == 0:
                if _git("push").returncode == 0:
                    return
                time.sleep(3 * (attempt + 1))   # alguien subió justo antes: se reintenta
                continue
            # El rebase chocó. El único archivo que también escribe otro workflow
            # (Fase 3, de madrugada) es player_stats.json: ahí gana la versión del
            # servidor, que Fase 3 recalcula completa. El resto es solo del motor
            # y se conserva tal cual.
            _git("rebase", "--abort")
            mine = _git("rev-parse", "HEAD").stdout.strip()
            owned = [str(f) for f in STATE_OWNED_FILES
                     if _git("cat-file", "-e", f"{mine}:{f}").returncode == 0]
            _git("fetch", "origin", "main")
            _git("reset", "--hard", "origin/main")
            if owned:
                _git("checkout", mine, "--", *owned)
                _git("add", *owned)
            if _git("diff", "--cached", "--quiet").returncode == 0:
                return
            _git("commit", "-m", "chore: actualiza estado del chequeo [skip ci]")
            if _git("push").returncode == 0:
                return
            time.sleep(3 * (attempt + 1))
        log("⚠️  No se pudo subir el estado a git tras 3 intentos (se reintenta en la siguiente pasada).")
    except Exception as err:
        log(f"⚠️  publish_state: {err}")


def vigilar(args, notifier, config, fcm=None):
    start = datetime.now(LOCAL_TZ)
    fallos = passes = 0
    while True:
        t0 = time.time()
        try:
            config = load_config()   # por si Fase 1/5 cambió algo desde la pasada anterior
            chequeo(args, notifier, config, fcm)
            fallos = 0
        except Exception as err:
            fallos += 1
            log(f"⚠️  pasada fallida ({fallos}/{MAX_FALLOS_SEGUIDOS}): {err}")
            if fallos >= MAX_FALLOS_SEGUIDOS:
                publish_state()
                sys.exit(1)
        publish_state()
        passes += 1
        if datetime.now(LOCAL_TZ) - start > MAX_VIGILAR:
            log("⏱️  tiempo máximo de vigilancia alcanzado; el cron reanuda.")
            break
        if not active_soon(config):
            break
        time.sleep(max(MIN_PAUSA_VIGILAR, PASO_VIGILAR - (time.time() - t0)))
    log(f"👋 vigilar terminó tras {passes} pasada(s)")


# ═══════════════════════════════════════════════════════ main
def main():
    parser = argparse.ArgumentParser(description="Motor de alertas · EuroGoalMX (multi-jugador)")
    parser.add_argument("modo", choices=["prueba", "repeticion", "chequeo", "vigilar"])
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
    elif args.modo == "vigilar":
        vigilar(args, notifier, config, fcm)
    else:
        chequeo(args, notifier, config, fcm)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
EuroGoalMX · Fase 5 — Deteccion automatica de cambio de equipo
================================================================
Para cada jugador con player_id conocido, revisa /transfers?player=<id> y
compara el fichaje mas reciente contra el team_id guardado en su target
"kind": "club" de hormi_config.json. Si el jugador fichó por un equipo
distinto, actualiza sola el config (team_id, label, search, country,
crest_url) y manda una notificacion (ntfy + FCM opcional) avisando el
cambio — igual que las alertas de gol de hormi_alertas.py.

Por que revisar TODOS LOS DIAS y no "solo en ventana de pases": la
peticion es barata (1 por jugador, ~5/dia de las 100 diarias del plan
gratuito), asi que el costo de preguntar y el costo de no encontrar nada
nuevo son iguales. Las ventanas de pases cambian de fecha por pais/liga
(Europa, Liga MX, Arabia Saudita...), asi que intentar adivinarlas seria
mas complicado y fragil que simplemente preguntar cada dia "¿tu ultimo
fichaje sigue siendo el mismo equipo que ya tengo guardado?".

Nota importante (confirmada probando contra la API real): la lista de
"transfers" que regresa el endpoint NO viene ordenada de mas reciente a
mas antigua — hay que ordenar por fecha uno mismo y tomar el ultimo.

crest_url: la URL oficial del escudo del equipo (media.api-sports.io),
publica y sin necesitar API key para verla. Se guarda en el config para
que la app pinte el escudo real de forma dinamica (ver TeamLogos.kt) en
vez de depender de que alguien suba un PNG a mano cada vez que un jugador
cambia de equipo. hormiga_fase1_validar_api.py hace lo mismo cuando
descubre un team_id por primera vez.

Uso:
  export APIFOOTBALL_KEY="tu_api_key"
  python hormi_transferencias.py                    # revisa cambios de equipo, todos los jugadores
  python hormi_transferencias.py --jugador raul      # solo uno
  python hormi_transferencias.py --backfill-crests   # solo rellena crest_url que falte (no revisa transferencias)

Pensado para correr 1x/dia desde GitHub Actions (ver
.github/workflows/transferencias.yml).

Requisitos: Python 3.9+, sin librerias externas. Variable APIFOOTBALL_KEY.
Notificaciones opcionales: mismas variables que hormi_alertas.py
(hormi_config.json/"ntfy_topic" + hormi_config.json/"fcm_project_id" +
variable de entorno FCM_SERVICE_ACCOUNT_JSON).
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

LOCAL_TZ = timezone(timedelta(hours=-6))
MIN_SECONDS_BETWEEN_REQUESTS = 6.5

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
                log("     limite por minuto alcanzado, esperando 60 s...")
                time.sleep(60)
                continue
            log(f"     ⚠️  HTTP {err.code} en {endpoint}: {err}")
            return None
        except Exception as err:
            log(f"     ⚠️  fallo de red en {endpoint}: {err}")
            return None
    if data is None or data.get("errors"):
        if data is not None:
            log(f"     ⚠️  la API respondió con error: {data['errors']}")
        return None
    return data.get("response", [])


def latest_transfer(player_id):
    """El fichaje mas reciente de este jugador, ordenando por fecha
    nosotros mismos (la API no garantiza el orden). None si no hay
    transferencias registradas o la peticion falla."""
    data = api("/transfers", {"player": player_id})
    if not data:
        return None
    entries = data[0].get("transfers") or []
    dated = [t for t in entries if t.get("date") and (t.get("teams") or {}).get("in", {}).get("id")]
    if not dated:
        return None
    dated.sort(key=lambda t: t["date"])
    return dated[-1]


def team_info(team_id):
    """{'logo':.., 'country':..} del equipo — {} si falla la peticion."""
    data = api("/teams", {"id": team_id})
    if not data:
        return {}
    team = (data[0] or {}).get("team") or {}
    return {"logo": team.get("logo"), "country": team.get("country")}


# ═══════════════════════════════════════════════════════ notificaciones
# (mismo mecanismo que hormi_alertas.py: ntfy siempre, FCM si esta
# configurado — se duplica aqui a proposito, cada fase de este proyecto es
# un script independiente que no depende de los demas para correr solo.)
class Notifier:
    def __init__(self, topic):
        self.topic = topic

    def send(self, title, message, priority=3):
        log(f"📲 {title} — {message}")
        if not self.topic:
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


class FcmSender:
    TOPIC = "hormi_alerts"
    SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

    def __init__(self, project_id):
        self.enabled = False
        self.project_id = project_id
        self._token = None
        sa_json = os.environ.get("FCM_SERVICE_ACCOUNT_JSON")
        if not project_id or not sa_json:
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
        if not self.enabled:
            return
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        payload = {"message": {"topic": self.TOPIC, "data": {
            "type": tipo, "title": title, "body": body,
            "player_key": player_key or "", "team_key": team_key or ""}}}
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=UTF-8",
                     "Authorization": f"Bearer {self._token}"})
        try:
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as err:
            log(f"⚠️  FCM: no se pudo enviar el push real — {err}")


# ═══════════════════════════════════════════════════════ logica principal
def check_player(player, notifier, fcm):
    """True si detecto y aplico un cambio de equipo para este jugador."""
    player_id = player.get("player_id")
    if not player_id:
        log(f"  sin player_id todavía, se omite.")
        return False

    club_targets = [t for t in player["targets"] if t.get("kind") == "club"]
    if not club_targets:
        return False
    target = club_targets[0]

    transfer = latest_transfer(player_id)
    if not transfer:
        log(f"  sin fichajes en la API (o falló la petición).")
        return False

    new_team = transfer["teams"]["in"]
    new_team_id = new_team.get("id")
    if not new_team_id or new_team_id == target.get("team_id"):
        log(f"  sigue en {target.get('label')} (sin cambios · último fichaje registrado {transfer.get('date')}).")
        return False

    old_label = target.get("label")
    info = team_info(new_team_id)
    new_label = new_team.get("name") or old_label or "equipo nuevo"

    log(f"  🚨 cambio de equipo: {old_label} → {new_label} (fichaje del {transfer.get('date')})")

    target["team_id"] = new_team_id
    target["label"] = new_label
    target["search"] = new_label
    if info.get("country"):
        target["country"] = info["country"]
    crest = info.get("logo") or new_team.get("logo")
    if crest:
        target["crest_url"] = crest

    apodo = player.get("apodo") or player["name"]
    title = f"🚨 ¡{apodo} cambió de equipo!"
    message = f"Ahora juega en {new_label}" + (f" (antes {old_label})" if old_label else "")
    notifier.send(title, message, 5)
    fcm.send("info", title, message, player["key"], target["key"])
    return True


def backfill_crests(config):
    """Rellena crest_url en cualquier target que ya tenga team_id pero
    todavía no tenga escudo guardado (uso único, para los equipos que se
    agregaron antes de que existiera este campo)."""
    changed = False
    for player in config["players"]:
        for target in player["targets"]:
            if target.get("crest_url") or not target.get("team_id"):
                continue
            info = team_info(target["team_id"])
            if info.get("logo"):
                target["crest_url"] = info["logo"]
                log(f"  {player['name']} · {target['label']}: crest_url = {info['logo']}")
                changed = True
            else:
                log(f"  {player['name']} · {target['label']}: no se pudo obtener el escudo.")
    return changed


def main():
    parser = argparse.ArgumentParser(description="Fase 5 · Detección de cambio de equipo")
    parser.add_argument("--jugador", help="Limita a un jugador por su 'key'")
    parser.add_argument("--backfill-crests", action="store_true",
                         help="Solo rellena crest_url faltante, sin revisar transferencias")
    args = parser.parse_args()

    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit(f"Falta {CONFIG_FILE}.")

    if args.backfill_crests:
        log("🖼️  Rellenando escudos faltantes...")
        if backfill_crests(config):
            save_json(CONFIG_FILE, config)
            log("✅ listo, hormi_config.json actualizado.")
        else:
            log("Nada que rellenar.")
        return

    players = config["players"]
    if args.jugador:
        players = [p for p in players if p["key"] == args.jugador]
        if not players:
            sys.exit(f"No hay jugador con key='{args.jugador}' en {CONFIG_FILE}.")

    notifier = Notifier(config.get("ntfy_topic"))
    fcm = FcmSender(config.get("fcm_project_id"))

    changed = False
    for player in players:
        log(f"👤 {player['name']}")
        if check_player(player, notifier, fcm):
            changed = True

    if changed:
        save_json(CONFIG_FILE, config)
        log("✅ hormi_config.json actualizado con el/los cambio(s) de equipo.")
    else:
        log("Sin cambios de equipo hoy.")


if __name__ == "__main__":
    main()

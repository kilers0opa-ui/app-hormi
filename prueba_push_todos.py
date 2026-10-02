#!/usr/bin/env python3
"""Prueba de push de TODOS los tipos y de TODOS los jugadores (solo FCM, sin ntfy).
Cada mensaje lleva el mismo formato que los reales (type, player_key, team_key) y el prefijo
"🧪 PRUEBA", para comprobar en el celular que (1) solo llegan los de jugadores en Favoritos,
(2) todos abren Inicio salvo los de video (que abren Videos y noticias).
Resultado en prueba_push.json (los logs de Actions no se pueden leer desde fuera)."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import hormi_alertas as ha

PAUSA = float(os.environ.get("PAUSA", "1.5"))
cfg = json.load(open("hormi_config.json", encoding="utf-8"))
fcm = ha.FcmSender(cfg.get("fcm_project_id"))
res = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fcm": fcm.enabled, "enviados": 0, "fallos": []}
URL = f"https://fcm.googleapis.com/v1/projects/{fcm.project_id}/messages:send"


def push(tipo, title, body, player_key="", team_key="", player_keys=()):
    payload = {"message": {"topic": fcm.TOPIC,
                           "data": {"type": tipo, "title": title, "body": body, "player_key": player_key,
                                    "team_key": team_key, "player_keys": ",".join(player_keys)},
                           "android": {"priority": "HIGH", "ttl": "900s"}}}
    req = urllib.request.Request(URL, data=json.dumps(payload).encode("utf-8"), headers={
        "Content-Type": "application/json; charset=UTF-8", "Authorization": f"Bearer {fcm._token}"})
    try:
        urllib.request.urlopen(req, timeout=20).close()
        res["enviados"] += 1
    except urllib.error.HTTPError as e:
        res["fallos"].append(f"{tipo}/{player_key}: {e.code} {e.read().decode('utf-8')[:200]}")
    time.sleep(PAUSA)


if not fcm.enabled:
    res["error"] = "FCM no habilitado"
else:
    P = "🧪 PRUEBA · "
    # Avisos generales (sin jugador): siempre deben llegar.
    push("info", P + "General (siempre llega)", "Sin jugador asociado: debe llegar aunque no tengas favoritos.")
    # Aviso "Juega mañana" de la Selección: llega si AL MENOS UNO de los convocados es favorito.
    conv = [p["key"] for p in cfg["players"] if p["key"] not in ("raul", "quinones", "huescas")]
    push("reminder", P + "📅 Mañana juega Selección Mexicana", "USA vs Mexico · 20:00 (hora centro) · Friendlies",
         "", "seleccion", conv)
    for p in cfg["players"]:
        k = p["key"]
        club = next(t for t in p["targets"] if t.get("kind") == "club")
        n = ha.display_name(p)
        ap = p.get("apodo") or p["name"]
        c, ck = club["label"], club["key"]
        e = club.get("emoji", "")
        push("goal", P + f"🔥 {e} ¡GOL DE {ap.upper()}! 67'", f"{c} · {c} 1-0 Rival", k, ck)
        push("assist", P + f"🎯 {e} ¡Asistencia de {n}! 52'", f"{c} · Gol de un compañero · {c} 1-0 Rival", k, ck)
        push("lineup", P + f"⭐ {e} ¡{n} titular con {c}!", f"{c} vs Rival · Liga", k, ck)
        push("start", P + f"⚽ {e} Arrancó: {c} ({n})", f"{c} vs Rival", k, ck)
        push("sub", P + f"🔄 {e} ¡Entra {n}! 60'", f"{c} · {c} 1-0 Rival", k, ck)
        push("final", P + f"🏁 {e} Final {c} ({n}): {c} 2-0 Rival", "90 min · 1 gol(es) · 0 asistencia(s)", k, ck)
        push("incident", P + f"🚫 {e} VAR anula jugada de gol de {n} 70'", f"{c} · Goal cancelled", k, ck)
        push("transfer", P + f"🔁 {n} cambia de equipo", "Aviso de transferencia (prueba)", k, ck)
        push("video", P + f"🎬 Ya está el video del gol de {ap}", "Video de prueba — míralo en la app", k, ck)
        push("video", P + f"🎬 Ya está disponible el resumen del partido de {p['name']}", "Resumen de prueba", k, ck)
        print(k, res["enviados"], flush=True)
json.dump(res, open("prueba_push.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False))

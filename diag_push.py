#!/usr/bin/env python3
"""Diagnóstico del push real (FCM): manda UN mensaje de prueba al tema que escucha la app y
guarda el resultado (código HTTP y respuesta de Google) en diag_push.json, porque los logs de
Actions no se pueden leer desde fuera. El mensaje no lleva player_key: la app lo muestra siempre."""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

import hormi_alertas as ha

OUT = "diag_push.json"
res = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
try:
    cfg = json.load(open("hormi_config.json", encoding="utf-8"))
    res["project_id"] = cfg.get("fcm_project_id")
    res["secret_presente"] = bool(os.environ.get("FCM_SERVICE_ACCOUNT_JSON"))
    s = ha.FcmSender(cfg.get("fcm_project_id"))
    res["fcm_habilitado"] = s.enabled
    if s.enabled:
        url = f"https://fcm.googleapis.com/v1/projects/{s.project_id}/messages:send"
        payload = {"message": {"topic": s.TOPIC,
                               "data": {"type": "info", "title": "🔔 Prueba de Legión MX",
                                        "body": "Si ves esto, el push real funciona.", "player_key": "",
                                        "team_key": "", "player_keys": ""},
                               "android": {"priority": "HIGH", "ttl": "600s"}}}
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={
            "Content-Type": "application/json; charset=UTF-8", "Authorization": f"Bearer {s._token}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                res["http"] = r.status
                res["respuesta"] = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            res["http"] = e.code
            res["respuesta"] = e.read().decode("utf-8")
except Exception as e:  # el diagnóstico nunca debe fallar en silencio
    res["error"] = repr(e)
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False))

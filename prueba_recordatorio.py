#!/usr/bin/env python3
"""TEMPORAL: vista previa de cómo se vería el 'Mañana juegan' unificado (2 variantes). Resultado en prueba_push.json."""
import json, time
from datetime import datetime, timezone
import hormi_alertas as ha
cfg = json.load(open("hormi_config.json", encoding="utf-8"))
fcm = ha.FcmSender(cfg.get("fcm_project_id"))
res = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fcm": fcm.enabled, "enviados": 0, "fallos": []}
keys = ["vasquez", "raul", "obed", "chavez", "gimenez", "mora"]
lineas = [
    "🦅 Johan Vásquez · Genoa vs Fiorentina · 7:00 a.m.",
    "🐺 Raúl Jiménez · Middlesbrough vs Wolves · 8:00 a.m.",
    "🔴 Obed Vargas · Alavés vs Atlético de Madrid · 8:15 a.m.",
    "🧀 Mateo Chávez · Feyenoord vs AZ Alkmaar · 10:45 a.m.",
    "🐉 Santiago Giménez · Marítimo vs FC Porto · 11:00 a.m.",
    "🐕 Gilberto Mora · FC Juárez vs Club Tijuana · 5:00 p.m.",
]
variantes = [
    ("🧪 C · 📅 Mañana juegan 6 de tus jugadores favoritos", "Da clic o entra a la app para ver los detalles de los partidos."),
    ("🧪 D · 📅 Mañana juegan 6 de tus jugadores favoritos", "Vásquez, Raúl, Obed, Chávez, Giménez y Mora\nDa clic o entra a la app para ver los detalles de los partidos."),
]
for title, body in variantes:
    try:
        fcm.send("reminder", title, body, None, None, player_keys=keys)
        res["enviados"] += 1
    except Exception as e:
        res["fallos"].append(str(e))
    time.sleep(3)
json.dump(res, open("prueba_push.json", "w"), ensure_ascii=False)
print(res)

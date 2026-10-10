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
pruebas = [
    ("🧪 ⚽ 🔴 Arrancó: Olympiacos (Armando)",
     "Olympiacos vs Panathinaikos · Superliga de Grecia\nSigue el marcador en tiempo real desde la app.",
     "armando", "olympiacos", None),
    ("🧪 ⚽ 🇲🇽 Arrancó: Selección Mexicana",
     "México vs Chile · Amistoso\nSigue el marcador en tiempo real desde la app.",
     None, "seleccion", ["armando", "vasquez", "gimenez", "obed", "fidalgo"]),
]
for title, body, pk, tk, pks in pruebas:
    try:
        fcm.send("start", title, body, pk, tk, player_keys=pks)
        res["enviados"] += 1
    except Exception as e:
        res["fallos"].append(str(e))
    time.sleep(3)
json.dump(res, open("prueba_push.json", "w"), ensure_ascii=False)
print(res)

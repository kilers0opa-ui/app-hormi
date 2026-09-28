#!/usr/bin/env python3
"""Script temporal de un solo uso: vuelca la respuesta CRUDA de /players
para un jugador conocido (Armando, id 291713, season 2024) a un archivo, así
se puede confirmar qué campos trae de verdad la API (p.ej. si "team"/"league"
incluyen una URL de "logo") sin adivinar por la documentación. Se borra
después de revisarlo."""
import json
import os
import urllib.parse
import urllib.request

api_key = os.environ["APIFOOTBALL_KEY"]
url = "https://v3.football.api-sports.io/players?" + urllib.parse.urlencode(
    {"id": 291713, "season": 2024}
)
req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
with urllib.request.urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read().decode("utf-8"))

with open("_debug_raw.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print("listo")

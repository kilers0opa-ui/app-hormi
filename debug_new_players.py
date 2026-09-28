#!/usr/bin/env python3
"""Debug temporal: investigar por que no se encontraron Porto/AZ Alkmaar
y los player_id de Gimenez/Chavez. Se borra despues de usarse."""
import json
import os
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.environ["APIFOOTBALL_KEY"]
_last = 0.0


def norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def api(endpoint, params):
    global _last
    url = f"{BASE_URL}{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    wait = 6.5 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    try:
        _last = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data
    except Exception as err:
        return {"exception": str(err), "trace": traceback.format_exc()}


out = {}

out["teams_porto"] = api("/teams", {"search": "Porto"})
out["teams_az"] = api("/teams", {"search": "AZ Alkmaar"})
out["teams_az2"] = api("/teams", {"search": "AZ"})
out["profiles_gimenez"] = api("/players/profiles", {"search": "Gimenez"})
out["profiles_chavez"] = api("/players/profiles", {"search": "Chavez"})

Path("debug_output.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("listo, ver debug_output.json")

#!/usr/bin/env python3
"""Script temporal de una sola vez: inspecciona la forma real de la
respuesta de /transfers para decidir el diseño de la Fase 5 (deteccion
automatica de cambio de equipo). Se borra despues de usarlo."""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = "https://v3.football.api-sports.io"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", type=int, required=True)
    args = parser.parse_args()

    api_key = os.environ.get("APIFOOTBALL_KEY")
    if not api_key:
        sys.exit("Falta APIFOOTBALL_KEY")

    url = f"{BASE_URL}/transfers?{urllib.parse.urlencode({'player': args.player})}"
    req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    with open("debug_transfers_output.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

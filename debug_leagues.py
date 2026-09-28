#!/usr/bin/env python3
"""Script temporal: inspecciona /leagues?team= para depurar por que un
equipo no aparece en team_standings.json. Se borra despues de usarlo."""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = "https://v3.football.api-sports.io"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", type=int, required=True)
    args = parser.parse_args()

    api_key = os.environ.get("APIFOOTBALL_KEY")
    if not api_key:
        sys.exit("Falta APIFOOTBALL_KEY")

    url = f"{BASE_URL}/leagues?{urllib.parse.urlencode({'team': args.team})}"
    req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    with open("debug_leagues_output.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

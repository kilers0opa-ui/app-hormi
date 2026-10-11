#!/usr/bin/env python3
"""TEMPORAL: push de prueba para grabar video (sin 🧪): gol Armando club y Selección, arranque Quiñones, titular Giménez.
Pasan por analyze() real, así salen idénticos a los de un partido."""
import json, time
from datetime import datetime, timezone
import hormi_alertas as ha
cfg = json.load(open("hormi_config.json", encoding="utf-8"))
fcm = ha.FcmSender(cfg.get("fcm_project_id"))
res = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fcm": fcm.enabled, "enviados": [], "fallos": []}
P = {p["key"]: p for p in cfg["players"]}
EQUIPO_API = {"olympiacos": "Olympiakos Piraeus", "seleccion": "Mexico", "al_qadsiah": "Al-Qadisiyah FC", "porto": "FC Porto"}
LIGA = {"olympiacos": "Super League 1", "seleccion": "Friendlies", "al_qadsiah": "Pro League", "porto": "Primeira Liga"}
def sim(pk, tk, rival, short, el, events, tipo, goals=(0, 0)):
    pl = P[pk]; tg = next(t for t in pl["targets"] if t["key"] == tk)
    sn = {"fixture": {"id": 999200, "status": {"short": short, "long": short, "elapsed": el, "extra": None}, "timestamp": 0},
          "teams": {"home": {"id": tg["team_id"], "name": EQUIPO_API[tk]}, "away": {"id": 9999, "name": rival}},
          "league": {"name": LIGA[tk], "country": "World"}, "goals": {"home": goals[0], "away": goals[1]}, "players": [],
          "lineups": [{"team": {"id": tg["team_id"]}, "startXI": [{"player": {"id": pl["player_id"]}}], "substitutes": [{"player": {"id": 1}}]}],
          "events": events}
    for k, t, b, pr in ha.analyze(sn, pl, tg)[0]:
        if ha.alert_type(k) == tipo:
            try:
                fcm.send(ha.fcm_tipo(tipo), t, b, pk, tk); res["enviados"].append(t)
            except Exception as e:
                res["fallos"].append(str(e))
            time.sleep(2)
            return
    res["fallos"].append(f"sin aviso {tipo} para {pk}/{tk}")
def gol(min_, pid, name, team):
    return {"time": {"elapsed": min_, "extra": None}, "type": "Goal", "detail": "Normal Goal", "team": {"name": team}, "player": {"id": pid, "name": name}, "assist": {}}
sim("armando", "olympiacos", "Panathinaikos", "2H", 67, [gol(67, P["armando"]["player_id"], "A. González", "Olympiakos Piraeus")], "goal", (2, 0))
sim("armando", "seleccion", "Brazil", "1H", 23, [gol(23, P["armando"]["player_id"], "A. González", "Mexico")], "goal", (1, 0))
sim("quinones", "al_qadsiah", "Al Hilal Saudi FC", "1H", 1, [], "start")
sim("gimenez", "porto", "Benfica", "NS", None, [], "lineup")
json.dump(res, open("prueba_push.json", "w"), ensure_ascii=False); print(res)

#!/usr/bin/env python3
"""TEMPORAL: prueba por el camino real de los avisos nuevos (medio tiempo, arrancó, mañana juega 1 y 3)."""
import json, time
from datetime import datetime, timedelta, timezone
import hormi_alertas as ha
cfg = json.load(open("hormi_config.json", encoding="utf-8"))
fcm = ha.FcmSender(cfg.get("fcm_project_id"))
res = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fcm": fcm.enabled, "enviados": 0, "fallos": []}
_send = fcm.send
def send_prueba(tipo, title, body, player_key=None, team_key=None, player_keys=None, extra=None):
    ex = dict(extra or {})
    if "grupo" in ex:
        g = json.loads(ex["grupo"]); g["title"] = "🧪 " + g["title"]
        for it in g["items"]: it["title"] = "🧪 " + it["title"]
        ex["grupo"] = json.dumps(g, ensure_ascii=False, separators=(",", ":"))
    try:
        _send(tipo, "🧪 " + title, body, player_key, team_key, player_keys, extra=ex); res["enviados"] += 1
    except Exception as e:
        res["fallos"].append(str(e))
    time.sleep(3)
fcm.send = send_prueba
class N:
    console_only = True
    def send(self, *a, **k): pass
P = {p["key"]: p for p in cfg["players"]}
def sim(pk, tk, short, el, events, tipo, goals=(0, 0)):
    pl = P[pk]; tg = next(t for t in pl["targets"] if t["key"] == tk)
    rival = {"id": 9999, "name": "Panathinaikos" if pk == "armando" and tk != "seleccion" else ("Chile" if tk == "seleccion" else "Al Hilal Saudi FC")}
    home = {"id": tg["team_id"], "name": "Olympiakos Piraeus" if tk == "olympiacos" else ("Mexico" if tk == "seleccion" else "Al-Qadisiyah FC")}
    sn = {"fixture": {"id": 999100, "status": {"short": short, "long": short, "elapsed": el, "extra": None}, "timestamp": 0},
          "teams": {"home": home, "away": rival}, "league": {"name": "Friendlies"}, "goals": {"home": goals[0], "away": goals[1]}, "players": [],
          "lineups": [{"team": {"id": tg["team_id"]}, "startXI": [{"player": {"id": pl["player_id"]}}], "substitutes": [{"player": {"id": 1}}]}],
          "events": events}
    for k, t, b, pr in ha.analyze(sn, pl, tg)[0]:
        if ha.alert_type(k) == tipo:
            fcm.send(tipo, t, b, pk, tk)
def gol(min_, pid, name, team):
    return {"time": {"elapsed": min_, "extra": None}, "type": "Goal", "detail": "Normal Goal", "team": {"name": team}, "player": {"id": pid, "name": name}, "assist": {}}
sim("armando", "olympiacos", "1H", 1, [], "start")
sim("armando", "seleccion", "1H", 23, [gol(23, P["armando"]["player_id"], "A. González", "Mexico")], "goal", (1, 0))
sim("quinones", "al_qadsiah", "HT", 45, [gol(12, P["quinones"]["player_id"], "J. Quiñones", "Al-Qadisiyah FC"), gol(40, 7, "S. Al-Dawsari", "Al Hilal Saudi FC")], "halftime", (1, 1))
json.dump(res, open("prueba_push.json", "w"), ensure_ascii=False); print(res)

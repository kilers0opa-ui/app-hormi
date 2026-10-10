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
arm = P["armando"]; tgt = next(t for t in arm["targets"] if t["key"] == "olympiacos")
def snap(short, elapsed, events):
    return {"fixture": {"id": 999002, "status": {"short": short, "long": short, "elapsed": elapsed, "extra": None}, "timestamp": 0},
            "teams": {"home": {"id": tgt["team_id"], "name": "Olympiakos Piraeus"}, "away": {"id": 617, "name": "Panathinaikos"}},
            "league": {"name": "Super League 1"}, "goals": {"home": 1, "away": 1}, "players": [],
            "lineups": [{"team": {"id": tgt["team_id"]}, "startXI": [{"player": {"id": arm["player_id"]}}], "substitutes": [{"player": {"id": 1}}]}],
            "events": events}
EV = [{"time": {"elapsed": 23, "extra": None}, "type": "Goal", "detail": "Normal Goal", "team": {"name": "Olympiakos Piraeus"}, "player": {"id": 77, "name": "A. El Kaabi"}, "assist": {}},
      {"time": {"elapsed": 45, "extra": 2}, "type": "Goal", "detail": "Penalty", "team": {"name": "Panathinaikos"}, "player": {"id": 9, "name": "F. Ioannidis"}, "assist": {}}]
for short, el, tipo in (("1H", 1, "start"), ("HT", 45, "halftime")):
    alerts, _ = ha.analyze(snap(short, el, EV if short == "HT" else []), arm, tgt)
    for k, t, b, pr in alerts:
        if ha.alert_type(k) == tipo:
            fcm.send(tipo, t, b, "armando", "olympiacos")
# Mañana juega: 1 jugador (Mora) y 3 jugadores
ha.REMINDER_HORA = 0
now = datetime.now(ha.LOCAL_TZ); man = now + timedelta(days=1)
def entry(fid, h, m, home, away, liga):
    return {"fixture_id": fid, "kickoff": man.replace(hour=h, minute=m, second=0, microsecond=0).isoformat(), "home": home, "away": away, "league": liga}
def cfg_de(keys):
    return {"players": [dict(P[k], targets=[t for t in P[k]["targets"] if t["kind"] == "club"][:1]) for k in keys]}
s1 = {"mora": {cfg_de(["mora"])["players"][0]["targets"][0]["key"]: entry(990001, 17, 0, "FC Juárez", "Club Tijuana", "Liga MX")}}
ha.send_reminders(cfg_de(["mora"]), N(), fcm, s1, set())
s3 = {}
for k, (fid, h, m, home, away, liga) in {"armando": (990002, 8, 15, "Olympiacos", "Panathinaikos", "Superliga"),
                                        "raul": (990003, 13, 0, "Middlesbrough", "Wolves", "Championship"),
                                        "vasquez": (990004, 7, 0, "Genoa", "Fiorentina", "Serie A")}.items():
    s3[k] = {cfg_de([k])["players"][0]["targets"][0]["key"]: entry(fid, h, m, home, away, liga)}
ha.send_reminders(cfg_de(["armando", "raul", "vasquez"]), N(), fcm, s3, set())
json.dump(res, open("prueba_push.json", "w"), ensure_ascii=False); print(res)

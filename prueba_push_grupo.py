#!/usr/bin/env python3
"""Prueba de los avisos UNIFICADOS de la Selección (alineación, arrancó, final) por el camino real:
analyze() con un partido simulado -> anotar_grupo() -> enviar_grupos() -> FCM con el JSON 'grupo'.
Todos los títulos llevan 🧪. Sirve para comprobar en el celular que, según tus Favoritos, llega el aviso
individual (1 favorito) o el unificado (varios). Resultado en prueba_push.json."""
import json, time
from datetime import datetime, timezone
import hormi_alertas as ha

cfg = json.load(open("hormi_config.json", encoding="utf-8"))
fcm = ha.FcmSender(cfg.get("fcm_project_id"))
res = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fcm": fcm.enabled, "grupos": 0, "fallos": []}

class Notif:   # sin ntfy
    console_only = True
    def send(self, *a, **k): pass

_send = fcm.send
def send_prueba(tipo, title, body, player_key=None, team_key=None, player_keys=None, extra=None):
    ex = dict(extra or {})
    if "grupo" in ex:
        g = json.loads(ex["grupo"]); g["title"] = "🧪 " + g["title"]
        for it in g["items"]: it["title"] = "🧪 " + it["title"]
        ex["grupo"] = json.dumps(g, ensure_ascii=False, separators=(",", ":"))
    try:
        _send(tipo, "🧪 " + title, body, player_key, team_key, player_keys, extra=ex)
        res["grupos"] += 1
    except Exception as e:
        res["fallos"].append(str(e))
    time.sleep(3)
fcm.send = send_prueba

ROLES = {"vasquez": "titular", "gimenez": "titular", "armando": "banca", "chavez": "banca",
         "obed": "banca", "fidalgo": "banca"}   # el resto: no convocado
players = cfg["players"]
pid = {p["key"]: p["player_id"] for p in players}
xi = [{"player": {"id": pid[k]}} for k, r in ROLES.items() if r == "titular"]
subs = [{"player": {"id": pid[k]}} for k, r in ROLES.items() if r == "banca"]

def snap(short, elapsed, events=()):
    return {"fixture": {"id": 999001, "status": {"short": short, "long": short, "elapsed": elapsed, "extra": None}},
            "teams": {"home": {"name": "USA", "id": 1}, "away": {"name": "Mexico", "id": 2}},
            "league": {"name": "Friendlies"}, "goals": {"home": 3, "away": 0},
            "lineups": [{"team": {"id": 2}, "startXI": xi, "substitutes": subs}], "events": list(events)}

def ev(m, out_k, in_k):
    return {"time": {"elapsed": m, "extra": None}, "type": "subst", "detail": "Substitution",
            "player": {"id": pid[out_k] if out_k else 1, "name": "x"}, "assist": {"id": pid[in_k]}, "team": {"name": "Mexico"}}

FINAL_EVENTS = [ev(46, None, "chavez"), ev(67, None, "fidalgo"), ev(80, None, "armando")]
tipos_ok = ("lineup", "start", "final")
sent = {p["key"]: set() for p in players}
if not fcm.enabled:
    res["error"] = "FCM no habilitado"
else:
    for fase, sn in (("alineación", snap("NS", None)), ("arrancó", snap("1H", 1)), ("final", snap("FT", 90, FINAL_EVENTS))):
        ha._GRUPOS.clear()
        for p in players:
            tg = next(t for t in p["targets"] if t["kind"] == "seleccion")
            alerts, info = ha.analyze(sn, p, tg)
            alerts = [a for a in alerts if ha.alert_type(a[0]) in tipos_ok]
            gg = []
            ha.dispatch(alerts, sent[p["key"]], Notif(), fcm, p["key"], tg["key"], register_video=False, grupo=gg)
            if gg:
                ha.anotar_grupo(p, tg, info, 999001, gg)
        ha.enviar_grupos(Notif(), fcm)
json.dump(res, open("prueba_push.json", "w"), ensure_ascii=False, indent=1)
print(res)

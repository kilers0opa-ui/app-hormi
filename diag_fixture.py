import json, hormi_alertas as ha
fx = (ha.api("/fixtures", {"id": 1551014}) or [{}])[0]
out = {"status": fx.get("fixture", {}).get("status"), "goals": fx.get("goals"),
       "lineups": [{"team": t["team"]["name"], "startXI": [p["player"]["name"] + f" ({p['player']['id']})" for p in t.get("startXI") or []],
                    "subs": [p["player"]["name"] + f" ({p['player']['id']})" for p in t.get("substitutes") or []]} for t in fx.get("lineups") or []],
       "events": [{"min": e["time"]["elapsed"], "type": e["type"], "detail": e["detail"], "team": e["team"]["name"],
                   "player": (e.get("player") or {}).get("name"), "pid": (e.get("player") or {}).get("id"),
                   "assist": (e.get("assist") or {}).get("name")} for e in fx.get("events") or []],
       "lineups_endpoint": [{"team": t["team"]["name"], "n": len(t.get("startXI") or [])} for t in (ha.api("/fixtures/lineups", {"fixture": 1551014}) or [])],
       "players_endpoint": [{"team": t["team"]["name"], "n": len(t.get("players") or [])} for t in (ha.api("/fixtures/players", {"fixture": 1551014}) or [])]}
ha.save_json(ha.Path("diag_fixture.json"), out)

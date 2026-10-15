import json, hormi_alertas as ha
r = ha.api("fixtures", {"id": 1552181})
d = (r.get("response") or [{}])[0]
json.dump({"goals": d.get("goals"), "events": [e for e in d.get("events") or [] if (e.get("type") or "").lower() in ("goal", "var")]},
          open("diag_eventos.json", "w"), indent=1, ensure_ascii=False)

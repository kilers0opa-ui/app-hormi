import json, traceback
out = {}
try:
    import hormi_alertas as ha
    r = ha.api("fixtures", {"id": 1552181})
    d = (r or [{}])[0]
    out = {"goals": d.get("goals"), "err": ha.last_api_error,
           "events": [e for e in d.get("events") or [] if (e.get("type") or "").lower() in ("goal", "var")]}
except Exception:
    out = {"tb": traceback.format_exc()}
json.dump(out, open("diag_eventos.json", "w"), indent=1, ensure_ascii=False)

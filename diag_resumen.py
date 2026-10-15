import json, traceback
from datetime import datetime, timezone, timedelta
import hormi_videos as hv
S = dict(hv.DEFAULTS) if hasattr(hv, "DEFAULTS") else {}
out = []
casos = [("Real Betis", "FC Porto", "2026-10-14T20:50:00+00:00"),
         ("AZ Alkmaar", "Hapoel Beer Sheva", "2026-10-15T18:30:00+00:00")]
for home, away, base in casos:
    try:
        since = datetime.fromisoformat(base)
        hp, ap = hv.team_prefixes(home), hv.team_prefixes(away)
        for q in (f"{home} {away} resumen goles", f"{home} {away} highlights", f"{home} vs {away}"):
            found = hv.search_videos(q, published_after=since, max_results=15) or []
            vis = {e["id"] for e in (hv.filter_visibles(found, "MX") or [])}
            out.append({"q": q, "res": [{
                "t": e.get("title"), "ch": e.get("channel"), "pub": e.get("published_at"),
                "es_resumen": hv.is_match_summary(e, hp, ap),
                "confiable": hv.resumen_confiable(e, hp, ap, S),
                "descartado": hv.resumen_descartado(e, S),
                "visible_mx": e["id"] in vis} for e in found]})
    except Exception:
        out.append({"tb": traceback.format_exc()})
json.dump(out, open("diag_resumen.json", "w"), indent=1, ensure_ascii=False)

"""TEMPORAL: segunda fuente de convocatoria: es.wikipedia y sitio FMF."""
import json, re, urllib.request, urllib.parse
UA = {"User-Agent": "LegionMX-Convocatoria/1.0 (https://github.com/kilers0opa-ui/app-hormi)"}
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "replace")
out = {}
try:
    api = "https://es.wikipedia.org/w/api.php?"
    secs = json.loads(get(api + urllib.parse.urlencode({"action": "parse", "page": "Selección de fútbol de México", "prop": "sections", "format": "json"})))["parse"]["sections"]
    out["es_secciones"] = [(s["index"], s["line"]) for s in secs]
    cand = [s for s in secs if re.search(r"convoca|plantel|jugadores", s["line"], re.I)]
    out["es_textos"] = {}
    for s in cand[:4]:
        wt = json.loads(get(api + urllib.parse.urlencode({"action": "parse", "page": "Selección de fútbol de México", "prop": "wikitext", "section": s["index"], "format": "json"})))["parse"]["wikitext"]["*"]
        out["es_textos"][s["line"]] = wt if "convoca" in s["line"].lower() else wt[:200]
except Exception as e:
    out["es_error"] = str(e)
json.dump(out, open("diag_squad.json", "w"), ensure_ascii=False, indent=1)

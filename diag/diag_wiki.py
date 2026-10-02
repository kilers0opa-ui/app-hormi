import json, re, urllib.request, urllib.parse
UA = {"User-Agent": "LegionMX-Convocatoria/1.0 (https://github.com/kilers0opa-ui/app-hormi)"}
def get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode())
out = {}
for lang, page in (("en", "Mexico_national_football_team"), ("es", "Selección_de_fútbol_de_México")):
    try:
        d = get(f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode({"action": "parse", "page": page, "prop": "sections", "format": "json"}))
        secs = d["parse"]["sections"]
        out[f"{lang}_sections"] = [(s["index"], s["line"]) for s in secs]
        for s in secs:
            if re.search(r"current squad|plantilla|convocatoria|latest squad|recent call", s["line"], re.I):
                w = get(f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode({"action": "parse", "page": page, "prop": "wikitext", "section": s["index"], "format": "json"}))
                out[f"{lang}_{s['index']}_{s['line']}"] = w["parse"]["wikitext"]["*"][:7000]
    except Exception as e:
        out[f"{lang}_error"] = str(e)
json.dump(out, open("diag/diag_wiki.json", "w"), ensure_ascii=False, indent=1)

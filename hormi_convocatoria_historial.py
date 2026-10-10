#!/usr/bin/env python3
"""Reconstruye convocatorias PASADAS de la Selección leyendo el historial de cambios de Wikipedia
(en español "Última convocatoria" y en inglés "Current squad"): toma una versión de la página cada pocos
días, lee la lista vigente en ese momento y se queda con la versión final de cada ventana de partidos.
Uso único (o cuando haga falta rellenar): escribe el resultado en hormi_convocatoria.json → historial.

  python hormi_convocatoria_historial.py --desde 2025-01-01 [--ver]   (--ver: solo imprime, no escribe)
"""
import argparse, json, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
import hormi_convocatoria as hc

DUMP = None
PAGINAS = {"es": (hc.API_ES, hc.PAGE_ES), "en": (hc.API, hc.PAGE.replace("_", " "))}


def revisiones(api, page, desde):
    out, cont = [], {}
    while True:
        p = {"action": "query", "prop": "revisions", "titles": page, "rvprop": "ids|timestamp", "rvlimit": 500,
             "rvend": desde + "T00:00:00Z", "rvdir": "older", **cont}
        d = hc._get(p, api)
        for pg in d["query"]["pages"].values():
            out += pg.get("revisions", [])
        if "continue" not in d:
            return out
        cont = d["continue"]


def contenido(api, revid):
    d = hc._get({"action": "query", "prop": "revisions", "revids": revid, "rvprop": "content", "rvslots": "main"}, api)
    pg = next(iter(d["query"]["pages"].values()))
    return pg["revisions"][0]["slots"]["main"]["*"]


def seccion(texto, idioma):
    pat = r"^(=+)\s*(?:Última convocatoria)\s*\1\s*$" if idioma == "es" else r"^(=+)\s*(?:Current|Latest) squad\s*\1\s*$"
    m = re.search(pat, texto, re.M | re.I)
    if not m:
        return None
    nivel = len(m.group(1))
    resto = texto[m.end():]
    fin = re.search(r"^={2,%d}[^=].*?={2,%d}\s*$" % (nivel, nivel), resto, re.M)
    return m.group(0) + "\n" + (resto[:fin.start()] if fin else resto)


MOTIVOS = [("copa mundial", "Mundial"), ("world cup", "Mundial"), ("copa de oro", "Copa Oro"), ("gold cup", "Copa Oro"),
           ("liga de naciones", "Liga de Naciones"), ("nations league", "Liga de Naciones"),
           ("amistoso", "Amistosos"), ("friendl", "Amistosos")]


def motivo(intro):
    t = intro.lower()
    vistos = []
    for k, v in MOTIVOS:
        if k in t and v not in vistos:
            vistos.append(v)
    anio = re.search(r"(20\d\d)", intro)
    return " y ".join(vistos) + (f" {anio.group(1)}" if vistos == ["Mundial"] and anio else "") if vistos else None


def leer(idioma, desde, paso_dias=4):
    api, page = PAGINAS[idioma]
    revs = revisiones(api, page, desde)
    revs.sort(key=lambda r: r["timestamp"])
    elegidas, ultimo = [], None
    for r in revs:   # una versión cada `paso_dias` (la última de cada tramo)
        t = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
        if ultimo is None or t - ultimo >= timedelta(days=paso_dias):
            elegidas.append(r); ultimo = t
        else:
            elegidas[-1] = r
    ventanas = {}
    for r in elegidas:
        sec = None
        try:
            sec = seccion(contenido(api, r["revid"]), idioma)
            if not sec:
                continue
            pp, pw, pd = ((hc.parse_players_es, hc.parse_window_es, hc.parse_detalle_es) if idioma == "es"
                          else (hc.parse_players, hc.parse_window, hc.parse_detalle_en))
            f = hc.leer_fuente(idioma, sec, pp, pw, pd, hc.info_es if idioma == "es" else None)
            f["motivo"] = f.get("motivo") or motivo(sec.split("{|")[0] if idioma == "es" else sec.split("{{nat fs g start}}")[0])
            f["rev"] = r["timestamp"]
            ventanas[(f["desde"], f["hasta"])] = f   # la versión más nueva de cada ventana gana
        except Exception as err:
            print(f"  {idioma} {r['timestamp']}: {err}")
            if DUMP is not None and idioma == "es" and sec and len(DUMP) < 40:
                DUMP.append({"rev": r["timestamp"], "err": str(err), "intro": sec[:700]})
        time.sleep(0.2)
    return ventanas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2025-01-01")
    ap.add_argument("--ver", action="store_true")
    args = ap.parse_args()
    config = json.loads(hc.CONFIG_FILE.read_text(encoding="utf-8"))
    global DUMP
    if args.ver:
        DUMP = []
    es, en = leer("es", args.desde), leer("en", args.desde)
    if DUMP is not None:
        json.dump(DUMP, open("historial_dump.json", "w"), ensure_ascii=False, indent=1)
    print(f"es: {len(es)} ventanas · en: {len(en)} ventanas")
    for (d, h), f in sorted(en.items()):
        print(f"  en {d} → {h} · {f.get('motivo')} · {f['n']} jug · rev {f['rev']}")
    hist = []
    for (d, h), f in sorted(es.items(), reverse=True):
        # la de inglés que se traslape con esta ventana confirma
        par = next((g for (d2, h2), g in en.items() if d2 <= h and h2 >= d), None)
        sel = hc.construir({"es": f, "en": par}, config)
        sel["motivo"] = f.get("motivo")
        sel["reconstruida"] = True
        hist.append(sel)
        print(f"{sel['desde']} → {sel['hasta']} · {sel.get('motivo')} · {len(sel['jugadores'])} jug · anunciada {sel.get('anunciada')} · "
              f"partidos {[p['rival'] for p in sel['partidos']]} · app: {sel['convocados']} dudosos {sel['dudosos']}")
    if args.ver:
        json.dump(hist, open("convocatorias_reconstruidas.json", "w"), ensure_ascii=False, indent=1)
        return 0
    data = json.loads(hc.OUT_FILE.read_text(encoding="utf-8"))
    actual = data.get("seleccion") or {}
    previas = [x for x in hist if x["desde"] < actual.get("desde", "9999")]
    data["historial"] = previas
    hc.OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"historial: {len(previas)} convocatorias")
    return 0


if __name__ == "__main__":
    sys.exit(main())

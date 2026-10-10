#!/usr/bin/env python3
"""
Convocatoria de la Selección Mexicana -> hormi_convocatoria.json

La API-Football no publica la lista de convocados con anticipación (solo la
alineación ~1 h antes del partido) y su "plantilla" de la Selección es la del
Mundial, no la de cada fecha FIFA. La lista oficial sí está, casi al instante
y con las fechas de los partidos, en la sección "Current squad" de la página
de Wikipedia de la Selección (se actualiza al anunciarse cada convocatoria).

Este script la lee, cruza los nombres con el roster de hormi_config.json y
escribe, para esa ventana de partidos, quién NO está convocado:

  {"seleccion": {"desde": "2026-09-25", "hasta": "2026-10-07",
                 "convocados": [...], "no_convocados": [...], ...}}

El motor (hormi_alertas.py) usa ese archivo para no mostrar el partido de la
Selección como "próximo" (ni mandar "Mañana juega") a quien no está en la
lista. La ventana se vence sola con las fechas de los partidos. Si Wikipedia
cambia de formato o no responde, NO se toca el archivo (nunca se esconde a
alguien por un fallo de lectura).

DOS FUENTES (oct-2026): una sola fuente no es confiable, así que también se lee la sección "Última
convocatoria" de Wikipedia en español (otros editores, con la cita al sitio oficial de la FMF) y se
comparan: "convocados" = los que están en las DOS listas de la misma ventana; "dudosos" = en solo una
(la app no muestra su partido de Selección hasta que salga la alineación); "no_convocados" = en ninguna.

Uso:  python hormi_convocatoria.py            (lee Wikipedia)
      python hormi_convocatoria.py --archivo wikitext.txt   (prueba local)
"""
import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CONFIG_FILE = Path("hormi_config.json")
OUT_FILE = Path("hormi_convocatoria.json")
PAGE = "Mexico_national_football_team"
API = "https://en.wikipedia.org/w/api.php"
API_ES = "https://es.wikipedia.org/w/api.php"
PAGE_ES = "Selección de fútbol de México"
MESES_ES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
     "agosto", "septiembre", "octubre", "noviembre", "diciembre"], 1)}
UA = {"User-Agent": "LegionMX-Convocatoria/1.0 (https://github.com/kilers0opa-ui/app-hormi)"}
MIN_JUGADORES = 18          # una convocatoria real trae ~23-30; menos = lectura rota
MESES = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split()


def plain_name(player):
    return re.sub(r'\s*"[^"]*"\s*', " ", player["name"]).strip()


def _get(params, api=API):
    url = api + "?" + urllib.parse.urlencode(dict(params, format="json"))
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_wikitext():
    secs = _get({"action": "parse", "page": PAGE, "prop": "sections"})["parse"]["sections"]
    for s in secs:
        if re.fullmatch(r"(Current|Latest) squad", s["line"].strip(), re.I):
            w = _get({"action": "parse", "page": PAGE, "prop": "wikitext", "section": s["index"]})
            return w["parse"]["wikitext"]["*"]
    raise RuntimeError("no encontré la sección 'Current squad'")


def parse_players(wikitext):
    """Nombres (como se muestran) de los jugadores de la lista."""
    nombres = []
    for m in re.finditer(r"\{\{nat fs g player\|[^\n]*?name=\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", wikitext):
        titulo, mostrado = m.group(1), m.group(2)
        nombres.append(mostrado or re.sub(r"\s*\(.*?\)\s*$", "", titulo))
    return nombres


def parse_window(wikitext):
    """(desde, hasta) = primer y último partido de la convocatoria."""
    cab = wikitext.split("{{nat fs g start}}")[0]
    m = re.search(r"\bon ([^.<]*?)\s*respectively", cab) or re.search(r"\bon ((?:[A-Z][a-z]+ )?\d{1,2}[^.<]*?,\s*20\d\d)", cab)
    if m:
        txt = m.group(1)
        y = re.search(r"(20\d\d)\s*$", txt)
        if y:
            year = int(y.group(1))
            body = txt[:y.start()]
            mes, fechas = None, []
            for tok in re.finditer(r"(January|February|March|April|May|June|July|August|September|October|November|December)|(?<!\d)(\d{1,2})(?!\d)", body):
                if tok.group(1):
                    mes = MESES[tok.group(1)]
                elif mes:
                    try:
                        fechas.append(date(year, mes, int(tok.group(2))))
                    except ValueError:
                        pass
            if fechas:
                return min(fechas), max(fechas)
    # Plan B: fecha de la convocatoria + 30 días
    r = re.search(r"date=(\d{1,2}) ([A-Z][a-z]+) (20\d\d)", cab)
    if r and r.group(2) in MESES:
        d0 = date(int(r.group(3)), MESES[r.group(2)], int(r.group(1)))
        return d0, d0 + timedelta(days=30)
    raise RuntimeError("no pude leer las fechas de la convocatoria")


def fetch_wikitext_es():
    secs = _get({"action": "parse", "page": PAGE_ES, "prop": "sections"}, API_ES)["parse"]["sections"]
    for s in secs:
        if re.fullmatch(r"[ÚU]ltima convocatoria", s["line"].strip(), re.I):
            w = _get({"action": "parse", "page": PAGE_ES, "prop": "wikitext", "section": s["index"]}, API_ES)
            return w["parse"]["wikitext"]["*"]
    raise RuntimeError("no encontré la sección 'Última convocatoria' (es)")


def parse_players_es(wikitext):
    """Nombres de la tabla de la convocatoria (es.wikipedia): renglón '|[[Título|Nombre]]' tras cada '!'."""
    nombres = []
    for m in re.finditer(r"^\|\s*\[\[([^\]|]+)(?:\|([^\]]+))?\]\]\s*$", wikitext, re.M):
        titulo, mostrado = m.group(1), m.group(2)
        nombres.append(mostrado or re.sub(r"\s*\(.*?\)\s*$", "", titulo))
    return nombres


def parse_window_es(wikitext):
    """(desde, hasta) de los partidos listados en la introducción: '(26 de septiembre)' ... 'de 2026'."""
    intro = wikitext.split("{|")[0]
    y = re.search(r"\b(20\d\d)\b", intro)
    if not y:
        raise RuntimeError("no pude leer el año de la convocatoria (es)")
    year = int(y.group(1))
    fechas = []
    for d, mes in re.findall(r"\((\d{1,2}) de (" + "|".join(MESES_ES) + r")\)", intro, re.I):
        try:
            fechas.append(date(year, MESES_ES[mes.lower()], int(d)))
        except ValueError:
            pass
    if not fechas:
        raise RuntimeError("no pude leer las fechas de la convocatoria (es)")
    return min(fechas), max(fechas)


def esta_en_lista(player, nombres_norm):
    """Nombre completo igual, o (apellido + inicial del nombre) para evitar
    falsos 'no convocado' por acentos o segundos nombres."""
    toks = norm(plain_name(player))
    if toks in nombres_norm:
        return True
    ap, nom = toks[-1], toks[0]
    return any(n[-1] == ap and n[0][0] == nom[0] for n in nombres_norm)


def leer_fuente(nombre, wikitext, parse_p, parse_w):
    nombres = parse_p(wikitext)
    if len(nombres) < MIN_JUGADORES:
        raise RuntimeError(f"{nombre}: solo leí {len(nombres)} jugadores; no me fío de la lectura")
    desde, hasta = parse_w(wikitext)
    return {"nombres": [norm(n) for n in nombres], "n": len(nombres), "desde": desde, "hasta": hasta}


def construir(fuentes, config):
    """fuentes: {"en": {...}|None, "es": {...}|None}. Ver docstring del módulo."""
    ok = {k: v for k, v in fuentes.items() if v}
    if not ok:
        raise RuntimeError("ninguna fuente se pudo leer")
    # La ventana más reciente manda; una fuente con una ventana vieja (aún no actualizada) no cuenta.
    reciente = max(ok.values(), key=lambda f: f["hasta"])
    vigentes = {k: f for k, f in ok.items() if f["hasta"] >= reciente["desde"] and f["desde"] <= reciente["hasta"]}
    desde = min(f["desde"] for f in vigentes.values())
    hasta = max(f["hasta"] for f in vigentes.values())
    conv, dud, no_conv = [], [], []
    for p in config["players"]:
        en = sum(1 for f in vigentes.values() if esta_en_lista(p, f["nombres"]))
        if en == 0:
            no_conv.append(p["key"])
        elif en == len(vigentes) and len(vigentes) >= 2:
            conv.append(p["key"])
        else:
            dud.append(p["key"])   # solo una fuente lo trae (o solo hay una fuente al día): sin confirmar
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(),
            "jugadores_en_lista": max(f["n"] for f in vigentes.values()),
            "convocados": conv, "dudosos": dud, "no_convocados": no_conv,
            "fuentes": sorted(vigentes),
            "fuente": f"https://en.wikipedia.org/wiki/{PAGE}#Current_squad · https://es.wikipedia.org/wiki/Selección_de_fútbol_de_México#Última_convocatoria"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archivo", help="wikitext local en inglés (pruebas)")
    ap.add_argument("--archivo-es", help="wikitext local en español (pruebas)")
    args = ap.parse_args()
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    fuentes = {}
    for k, archivo, fetch, pp, pw in (("en", args.archivo, fetch_wikitext, parse_players, parse_window),
                                      ("es", args.archivo_es, fetch_wikitext_es, parse_players_es, parse_window_es)):
        try:
            wt = Path(archivo).read_text(encoding="utf-8") if archivo else fetch()
            fuentes[k] = leer_fuente(k, wt, pp, pw)
        except Exception as err:
            print(f"⚠️  fuente {k}: {err}")
            fuentes[k] = None
    try:
        sel = construir(fuentes, config)
    except Exception as err:
        print(f"⚠️  convocatoria sin cambios: {err}")
        return 0
    prev = {}
    try:
        prev = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("seleccion") or {}
    except Exception:
        pass
    keys = ("desde", "hasta", "convocados", "dudosos", "no_convocados")
    if all(prev.get(k) == sel[k] for k in keys):
        print("Sin cambios en la convocatoria.")
        return 0
    sel["actualizado"] = datetime.now(timezone(timedelta(hours=-6))).isoformat(timespec="seconds")
    OUT_FILE.write_text(json.dumps({"seleccion": sel}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Convocatoria {sel['desde']} → {sel['hasta']} ({', '.join(sel['fuentes'])}): convocados = {sel['convocados']} · "
          f"dudosos = {sel['dudosos']} · no convocados = {sel['no_convocados']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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


def _get(params):
    url = API + "?" + urllib.parse.urlencode(dict(params, format="json"))
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


def esta_en_lista(player, nombres_norm):
    """Nombre completo igual, o (apellido + inicial del nombre) para evitar
    falsos 'no convocado' por acentos o segundos nombres."""
    toks = norm(plain_name(player))
    if toks in nombres_norm:
        return True
    ap, nom = toks[-1], toks[0]
    return any(n[-1] == ap and n[0][0] == nom[0] for n in nombres_norm)


def construir(wikitext, config):
    nombres = parse_players(wikitext)
    if len(nombres) < MIN_JUGADORES:
        raise RuntimeError(f"solo leí {len(nombres)} jugadores; no me fío de la lectura")
    nn = [norm(n) for n in nombres]
    desde, hasta = parse_window(wikitext)
    conv, no_conv = [], []
    for p in config["players"]:
        (conv if esta_en_lista(p, nn) else no_conv).append(p["key"])
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(),
            "jugadores_en_lista": len(nombres), "convocados": conv, "no_convocados": no_conv,
            "fuente": f"https://en.wikipedia.org/wiki/{PAGE}#Current_squad"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archivo", help="wikitext local (pruebas)")
    args = ap.parse_args()
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    try:
        wt = Path(args.archivo).read_text(encoding="utf-8") if args.archivo else fetch_wikitext()
        sel = construir(wt, config)
    except Exception as err:
        print(f"⚠️  convocatoria sin cambios: {err}")
        return 0
    prev = {}
    try:
        prev = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("seleccion") or {}
    except Exception:
        pass
    keys = ("desde", "hasta", "convocados", "no_convocados")
    if all(prev.get(k) == sel[k] for k in keys):
        print("Sin cambios en la convocatoria.")
        return 0
    sel["actualizado"] = datetime.now(timezone(timedelta(hours=-6))).isoformat(timespec="seconds")
    OUT_FILE.write_text(json.dumps({"seleccion": sel}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Convocatoria {sel['desde']} → {sel['hasta']}: no convocados = {sel['no_convocados']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

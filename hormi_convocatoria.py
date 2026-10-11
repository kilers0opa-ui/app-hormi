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


POS_ES = {"por": "Portero", "def": "Defensa", "med": "Mediocampista", "del": "Delantero"}
POS_EN = {"GK": "Portero", "DF": "Defensa", "MF": "Mediocampista", "FW": "Delantero"}


def _link_texto(txt):
    """'[[Título (algo)|Mostrado]]' → 'Mostrado'; '[[Título]]' → 'Título' sin paréntesis."""
    m = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", txt or "")
    if not m:
        return None
    return (m.group(2) or re.sub(r"\s*\(.*?\)\s*$", "", m.group(1))).strip()


def parse_detalle_es(wikitext):
    """[{nombre, pos, club}] de la tabla de es.wikipedia: renglón del nombre y, abajo, posición y club."""
    lineas = wikitext.split("\n")
    out = []
    for i, ln in enumerate(lineas):
        m = re.match(r"^\|\s*(\[\[[^\]]+\]\])\s*$", ln)
        if not m:
            continue
        sig = lineas[i + 1] if i + 1 < len(lineas) else ""
        pm = re.search(r"\{\{(por|def|med|del)\}\}", sig)
        clubs = re.findall(r"\[\[[^\]]+\]\]", sig)
        out.append({"nombre": _link_texto(m.group(1)), "pos": POS_ES.get(pm.group(1)) if pm else None,
                    "club": _link_texto(clubs[-1]) if clubs else None})
    return out


def parse_players_es(wikitext):
    return [d["nombre"] for d in parse_detalle_es(wikitext)]


def parse_detalle_en(wikitext):
    out = []
    for m in re.finditer(r"\{\{nat fs g player\|([^\n]*?)\}\}\s*$", wikitext, re.M):
        campos = m.group(1)
        nombre = _link_texto((re.search(r"name=(\[\[[^\]]+\]\])", campos) or [None, ""])[1])
        pos = (re.search(r"pos=(\w+)", campos) or [None, ""])[1]
        club = _link_texto((re.search(r"club=(\[\[[^\]]+\]\])", campos) or [None, ""])[1])
        if nombre:
            out.append({"nombre": nombre, "pos": POS_EN.get(pos), "club": club})
    return out


def info_es(wikitext):
    """Fecha de anuncio (de la cita), motivo y partidos [{rival, fecha}] de la introducción."""
    bruto = wikitext.split("{|")[0]
    f = re.search(r"fecha=(\d{1,2} de \w+ de 20\d\d)", bruto)
    intro = _intro_es(wikitext)
    partidos = [{"rival": r.strip(), "fecha": d.strip()} for r, d in
                re.findall(r"\{\{sel\|([^}|]+)[^}]*\}\}\s*\(([^)]+)\)", intro)]
    if not partidos:
        # Formato viejo: "... los días 16 y 21 de enero de 2025 contra [[Inter]] y [[River Plate]]."
        c = re.search(r"\bcontra\s+(.*)$", intro, re.S)
        rivales = []
        if c:
            for t in re.findall(r"\{\{sel\|([^}|]+)[^}]*\}\}|\[\[([^\]]+)\]\]", c.group(1)):
                nombre = t[0] or _link_texto("[[" + t[1] + "]]")
                if nombre and re.search(r"copa|liga|mundial|cup|torneo", nombre, re.I):
                    continue   # es el torneo, no un rival
                if nombre and nombre not in rivales:
                    rivales.append(nombre.strip())
        fechas = [f"{d} de {list(MESES_ES)[m - 1]}" for _, m, d in _fechas_es(intro)]
        if rivales and len(fechas) == len(rivales):
            partidos = [{"rival": r, "fecha": fe} for r, fe in zip(rivales, fechas)]
        elif len(rivales) == 1 and fechas:
            partidos = [{"rival": rivales[0], "fecha": " y ".join(fechas)}]
        elif rivales:
            partidos = [{"rival": r, "fecha": ""} for r in rivales]
    return {"anunciada": f.group(1) if f else None, "partidos": partidos, "motivo": motivo_es(intro)}


def motivo_es(intro):
    """Para qué fue la convocatoria: Mundial 2026, Copa Oro, Liga de Naciones, Amistosos..."""
    t = intro.lower()
    for clave, nombre in (("copa mundial", "Mundial"), ("copa de oro", "Copa Oro"), ("liga de naciones", "Liga de Naciones"),
                          ("copa américa", "Copa América"), ("amistoso", "Amistosos")):
        if clave in t:
            y = re.search(r"(20\d\d)", intro)
            return f"{nombre} {y.group(1)}" if nombre in ("Mundial", "Copa Oro", "Copa América") and y else nombre
    return None


def _intro_es(wikitext):
    """Texto de la introducción de la convocatoria (el <small> de arriba), sin las citas <ref>."""
    intro = wikitext.split("{|")[0]
    m = re.search(r"<small>(.*?)</small>", intro, re.S)
    texto = m.group(1) if m else intro
    return re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", "", texto, flags=re.S)


_MES_RE = "|".join(MESES_ES)


def _fechas_es(texto, year_default=None):
    """Todas las fechas del texto: '26 de septiembre', 'los días 16 y 21 de enero de 2025', '5, 9 y 12 de junio'."""
    fechas = []
    for m in re.finditer(r"((?:\d{1,2}(?:\s*,\s*|\s+y\s+|\s+al\s+))*\d{1,2})\s+de\s+(" + _MES_RE + r")(?:\s+(?:de|del)\s+(20\d\d))?", texto, re.I):
        nums = [int(n) for n in re.findall(r"\d{1,2}", m.group(1))]
        mes = MESES_ES[m.group(2).lower()]
        y = int(m.group(3)) if m.group(3) else year_default
        for n in nums:
            fechas.append((y, mes, n))
    return fechas


def parse_window_es(wikitext):
    """(desde, hasta) de los partidos listados en la introducción."""
    intro = _intro_es(wikitext)
    bruto = wikitext.split("{|")[0]
    cita = re.search(r"fecha=(\d{1,2}) de (" + _MES_RE + r") de (20\d\d)", bruto, re.I)
    years = re.findall(r"\b(20\d\d)\b", intro) or ([cita.group(3)] if cita else [])
    if not years:
        raise RuntimeError("no pude leer el año de la convocatoria (es)")
    y0 = int(cita.group(3)) if cita else int(years[-1])   # el año del anuncio ("2024-25" en el texto confunde)
    d_cita = date(int(cita.group(3)), MESES_ES[cita.group(2).lower()], int(cita.group(1))) if cita else None
    fechas = []
    for y, mes, d in _fechas_es(intro, None):
        try:
            f = date(y or y0, mes, d)
            if not y and d_cita and f < d_cita - timedelta(days=7):
                f = date(y0 + 1, mes, d)   # partidos de enero anunciados en diciembre
            fechas.append(f)
        except ValueError:
            pass
    if fechas and d_cita and re.search(r"copa mundial|copa de oro|copa américa", intro, re.I):
        # Lista para un torneo (con amistosos de preparación): la ventana cubre el torneo completo.
        return min(fechas), max(max(fechas), d_cita + timedelta(days=45))
    if not fechas and cita:
        # Torneo sin fechas en el texto (p. ej. "Lista final ... para la Copa del Mundo 2026"): desde el anuncio
        # y 45 días (lo que dura la fase de grupos + eliminatorias de un torneo así).
        d0 = date(int(cita.group(3)), MESES_ES[cita.group(2).lower()], int(cita.group(1)))
        return d0, d0 + timedelta(days=45)
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
    if any(n[-1] == ap and n[0][0] == nom[0] for n in nombres_norm):
        return True
    # Con dos apellidos en una fuente y uno en la otra ("Víctor Guzmán Olmedo" / "Víctor Guzmán"):
    # mismo nombre de pila y algún apellido en común.
    return any(n[0] == nom and set(n[1:]) & set(toks[1:]) for n in nombres_norm)


def leer_fuente(nombre, wikitext, parse_p, parse_w, parse_d=None, info=None):
    nombres = parse_p(wikitext)
    if len(nombres) < MIN_JUGADORES:
        raise RuntimeError(f"{nombre}: solo leí {len(nombres)} jugadores; no me fío de la lectura")
    desde, hasta = parse_w(wikitext)
    return {"nombres": [norm(n) for n in nombres], "n": len(nombres), "desde": desde, "hasta": hasta,
            "detalle": parse_d(wikitext) if parse_d else [], **(info(wikitext) if info else {})}


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
    # Lista completa para la app (nombre, posición, club): de es.wikipedia si está al día; si no, de la otra.
    vista = vigentes.get("es") or next(iter(vigentes.values()))
    otras = [f for k, f in vigentes.items() if f is not vista]
    roster = {p["key"]: p for p in config["players"]}
    jugadores = []
    for d in vista.get("detalle") or []:
        nn = norm(d["nombre"])
        key = next((k for k, p in roster.items() if esta_en_lista(p, [nn])), None)
        confirmado = bool(otras) and all(esta_en_lista({"name": d["nombre"]}, f["nombres"]) for f in otras)
        jugadores.append({**d, "key": key, "confirmado": confirmado})
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
            "anunciada": vista.get("anunciada"), "partidos": vista.get("partidos") or [],
            "motivo": vista.get("motivo"),
            "jugadores": jugadores,
            "fuente": f"https://en.wikipedia.org/wiki/{PAGE}#Current_squad · https://es.wikipedia.org/wiki/Selección_de_fútbol_de_México#Última_convocatoria"}


def avisar(prev, sel, nueva, config):
    """Push "📋 Nueva convocatoria del Tri" (o "Cambio en la convocatoria") cuando sale una lista nueva o cambia
    quién de la app va. La app arma el texto con TUS favoritos (grupo): con uno solo, su aviso individual."""
    van = sel.get("convocados", []) + sel.get("dudosos", [])
    antes = (prev.get("convocados") or []) + (prev.get("dudosos") or []) if not nueva else []
    if not nueva and set(van) == set(antes):
        return
    import hormi_alertas as ha
    fcm = ha.FcmSender(config.get("fcm_project_id"))
    if not fcm.enabled:
        print("FCM no habilitado: sin aviso de convocatoria")
        return
    nombre = {p["key"]: ha.display_name(p) for p in config["players"]}
    rivales = ", ".join(p["rival"] for p in sel.get("partidos") or [])
    motivo = sel.get("motivo")
    if motivo and not motivo.startswith("Amistoso"):
        cola = f" · {motivo}"   # torneo: no se sabe contra quién se termina jugando
    else:
        cola = f" · vs {rivales}" if rivales else ""
    items = []
    for p in config["players"]:
        k = p["key"]
        if k in van:
            t = f"📋 {nombre[k]}, convocado a la Selección" + (" (por confirmar)" if k in sel.get("dudosos", []) else "")
        else:
            t = f"📋 {nombre[k]} no fue convocado a la Selección"
        items.append({"key": k, "name": nombre[k], "status": "convocado" if k in van else "no_convocado",
                      "summary": None, "title": t, "body": "Entra para ver la lista completa" + cola})
    titulo = "📋 Nueva convocatoria de la Selección" if nueva else "📋 Cambio en la convocatoria de la Selección"
    cuerpo = ("Van: " + ", ".join(nombre[k] for k in van)) if van else "Ninguno de los jugadores de la app"
    g = {"emoji": "📋", "label": "Selección Mexicana", "match": None, "league": None, "score": None}
    fcm.send("convocatoria", titulo, cuerpo, None, "seleccion", [p["key"] for p in config["players"]],
             extra={"grupo": ha._payload_grupo(g, items, titulo, cuerpo)})
    print(f"📲 aviso: {titulo} — {cuerpo}")


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
            fuentes[k] = leer_fuente(k, wt, pp, pw, parse_detalle_es if k == "es" else parse_detalle_en,
                                     info_es if k == "es" else None)
        except Exception as err:
            print(f"⚠️  fuente {k}: {err}")
            fuentes[k] = None
    try:
        sel = construir(fuentes, config)
    except Exception as err:
        print(f"⚠️  convocatoria sin cambios: {err}")
        return 0
    prev, historial = {}, []
    try:
        anterior = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        prev, historial = anterior.get("seleccion") or {}, anterior.get("historial") or []
    except Exception:
        pass
    keys = ("desde", "hasta", "convocados", "dudosos", "no_convocados", "jugadores", "motivo", "partidos", "anunciada")
    if all(prev.get(k) == sel.get(k) for k in keys):
        print("Sin cambios en la convocatoria.")
        return 0
    nueva = bool(prev) and (prev.get("desde"), prev.get("hasta")) != (sel["desde"], sel["hasta"])
    if nueva and prev.get("desde") < sel["desde"]:
        historial = [prev] + [h for h in historial if h.get("desde") != prev.get("desde")]   # la anterior pasa al historial
    # Se guardan las convocatorias del año de la más reciente y del año anterior: cuando sale la primera de un
    # año nuevo, se borran las de hace dos años (la app las agrupa por año).
    anio = int(sel["desde"][:4])
    historial = [h for h in historial if int((h.get("desde") or "0")[:4]) >= anio - 1]
    sel["actualizado"] = datetime.now(timezone(timedelta(hours=-6))).isoformat(timespec="seconds")
    OUT_FILE.write_text(json.dumps({"seleccion": sel, "historial": historial}, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    try:
        avisar(prev, sel, nueva or not prev, config)
    except Exception as err:
        print(f"⚠️  no se pudo mandar el aviso de convocatoria: {err}")
    print(f"Convocatoria {sel['desde']} → {sel['hasta']} ({', '.join(sel['fuentes'])}): convocados = {sel['convocados']} · "
          f"dudosos = {sel['dudosos']} · no convocados = {sel['no_convocados']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

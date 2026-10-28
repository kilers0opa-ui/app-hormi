#!/usr/bin/env python3
"""
Imágenes para COMPARTIR las estadísticas de un jugador (botón "Compartir" en Estadísticas), formato historia 9:16:

  tarjetas/<jugador>_temporada.jpg  "Portada de revista": carta inclinada con su foto, temporada actual con su club
                                     (partidos, goles, asistencias, minutos), goles por competición y cómo va el club
                                     en sus tablas (liga y torneo continental).
  tarjetas/<jugador>_carrera.jpg    "Carta + editorial": toda su carrera (clubes + Selección mayor), goles por equipo
                                     y clubes vs. Selección.

compartir.json: {jugador: {"temporada": {url, titulo}, "carrera": {url, titulo}}}. La app lo lee y ofrece las dos.
Solo regenera la imagen de un jugador si cambiaron sus datos (huella en compartir.json). No gasta API: usa
player_stats.json, team_standings.json, hormi_config.json y las fotos de fondos_fuente/<jugador>/.
"""
import hashlib
import io
import json
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import hormi_alertas as ha
from hormi_rachas import JUVENIL_SEL_RE, SELECCION_IDS

OUT_JSON = Path("compartir.json")
TARJETAS = Path("tarjetas")
FOTOS = Path("fondos_fuente")
RAW = "https://raw.githubusercontent.com/kilers0opa-ui/app-hormi/main/"
PB = "fondos_assets/Poppins-Bold.ttf"
PM = "fondos_assets/Poppins-Medium.ttf"
VERSION = 1   # sube si cambia el diseño: se regeneran todas

W, H = 1080, 1920
GOLD = (243, 210, 122)
GOLD2 = (184, 134, 43)
CREMA = (243, 238, 228)
TINTA = (20, 22, 28)
VERDE = (15, 81, 50)
GRIS = (85, 85, 85)
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def F(path, size):
    return ImageFont.truetype(path, size)


def fit(d, txt, path, maxw, start, minimo=28):
    s = start
    while s > minimo and d.textlength(txt, font=F(path, s)) > maxw:
        s -= 2
    return F(path, s)


_logos = {}


def logo(url, size):
    if not url:
        return None
    if url not in _logos:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            _logos[url] = Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=10).read())).convert("RGBA")
        except Exception:
            _logos[url] = None
    im = _logos[url]
    if im is None:
        return None
    im = im.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    return im


def foto(key, cual):
    for c in (cual, "club", "seleccion"):
        p = FOTOS / key / f"{c}.jpg"
        if p.exists():
            return Image.open(p).convert("RGB")
    return Image.new("RGB", (720, 1284), (40, 46, 58))


def cubrir(im, w, h, foco_y=.16):
    """Recorta y escala la foto para llenar w×h (como background-size: cover), con el foco vertical arriba."""
    r = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * r + .5), int(im.height * r + .5)), Image.LANCZOS)
    x0 = (im.width - w) // 2
    y0 = int(max(0, min(im.height - h, (im.height - h) * foco_y)))
    return im.crop((x0, y0, x0 + w, y0 + h))


def redondear(im, radio):
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=radio, fill=255)
    out = im.convert("RGBA")
    out.putalpha(m)
    return out


def marco_dorado(w, h, radio, grosor=8):
    """Marco con degradado dorado (carta de colección)."""
    g = Image.new("RGBA", (w, h))
    gd = ImageDraw.Draw(g)
    for i in range(h):
        t = i / h
        c = tuple(int(GOLD[k] * (1 - t) + GOLD2[k] * t) for k in range(3))
        gd.line([(0, i), (w, i)], fill=c + (255,))
    m = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(m)
    md.rounded_rectangle([0, 0, w - 1, h - 1], radius=radio, fill=255)
    md.rounded_rectangle([grosor, grosor, w - 1 - grosor, h - 1 - grosor], radius=radio - grosor, fill=0)
    g.putalpha(m)
    return g


def degradado_abajo(im, desde=.4, color=(13, 16, 25), alfa=240):
    w, h = im.size
    capa = Image.new("RGBA", (w, h), color + (0,))
    a = Image.new("L", (w, h), 0)
    ad = ImageDraw.Draw(a)
    y0 = int(h * desde)
    for y in range(y0, h):
        ad.line([(0, y), (w, y)], fill=int(alfa * (y - y0) / max(1, h - y0)))
    capa.putalpha(a)
    out = im.convert("RGBA")
    out.alpha_composite(capa)
    return out


def guardar(img, nombre):
    TARJETAS.mkdir(exist_ok=True)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=88, optimize=True)
    data = buf.getvalue()
    (TARJETAS / nombre).write_bytes(data)
    return f"{RAW}tarjetas/{nombre}?v={hashlib.sha1(data).hexdigest()[:8]}"


def fecha_hoy():
    hoy = datetime.now(ha.LOCAL_TZ)
    return f"Datos al {hoy.day} {MESES[hoy.month - 1]} {hoy.year}"


def apodo(p):
    return (p.get("apodo") or "").upper() or p["name"].split()[-1].upper()


def nombre_simple(p):
    """'Armando "La Hormiga" González' → 'Armando González'."""
    import re
    return re.sub(r'\s*"[^"]*"\s*', " ", p["name"]).strip()


def es_juvenil(r):
    return bool(JUVENIL_SEL_RE.match((r.get("team_name") or "").strip()))


def es_seleccion(r):
    return r.get("team_id") in SELECCION_IDS or (r.get("team_name") or "").lower() in ("mexico", "méxico")


# ───────────────────────────────────────────────────────── datos
def datos_temporada(p, stats, standings):
    club = next((t for t in p.get("targets") or [] if t.get("kind") == "club" or t.get("key") != "seleccion"), None)
    if not club:
        return None
    season = club.get("season")
    filas = [r for r in (stats.get("seasons") or {}).get(str(season), [])
             if r.get("team_id") == club.get("team_id") and not es_juvenil(r)]
    tot = {k: sum(r.get(k) or 0 for r in filas) for k in ("played", "goals", "assists", "minutes")}
    comps = sorted(({"nombre": ha.liga_es(r.get("league_name")), "pj": r.get("played") or 0, "g": r.get("goals") or 0}
                    for r in filas if (r.get("played") or 0) > 0), key=lambda c: (-c["g"], -c["pj"]))
    tablas = []
    for s in ((standings.get("teams") or {}).get(club["key"]) or {}).get("standings") or []:
        if s.get("rank"):
            tablas.append({"liga": ha.liga_es(s.get("league_name")), "rank": s["rank"], "pts": s.get("points"),
                           "de": s.get("total_teams") or len(s.get("table") or [])})
    yy = int(season) % 100
    return {"club": club.get("label") or "", "club_key": club["key"], "crest": club.get("crest_url"),
            "temporada": f"{yy:02d}/{(yy + 1) % 100:02d}", "tot": tot, "comps": comps[:3], "tablas": tablas[:2]}


# Nombres como los muestra la app (la API los trae en inglés o sin acento).
EQUIPOS = {"Club America": "América", "Guadalajara Chivas": "Chivas", "Olympiakos Piraeus": "Olympiacos"}


def datos_carrera(p, stats):
    etiquetas = {t.get("team_id"): t.get("label") for t in p.get("targets") or [] if t.get("team_id")}
    club = {"played": 0, "goals": 0, "assists": 0, "minutes": 0}
    sel = dict(club)
    equipos = {}
    for filas in (stats.get("seasons") or {}).values():
        for r in filas:
            if es_juvenil(r):
                continue
            tgt = sel if es_seleccion(r) else club
            for k in tgt:
                tgt[k] += r.get(k) or 0
            nom = ("Selección Mexicana" if es_seleccion(r)
                   else etiquetas.get(r.get("team_id")) or EQUIPOS.get(r.get("team_name"))
                   or ha.nombre_es(r.get("team_name") or ""))
            e = equipos.setdefault(nom, {"nombre": nom, "pj": 0, "g": 0})
            e["pj"] += r.get("played") or 0
            e["g"] += r.get("goals") or 0
    tot = {k: club[k] + sel[k] for k in club}
    eq = sorted((e for e in equipos.values() if e["pj"] > 0), key=lambda e: (-e["g"], -e["pj"]))
    return {"tot": tot, "club": club, "sel": sel, "equipos": eq[:3]}


# ───────────────────────────────────────────────────────── piezas comunes
def numeros(d, x0, x1, y, tot, color, sub, linea):
    d.line([(x0, y), (x1, y)], fill=linea, width=6)
    vals = [(str(tot["played"]), "PARTIDOS"), (str(tot["goals"]), "GOLES"), (str(tot["assists"]), "ASIST."),
            (f"{tot['minutes']:,}'".replace(",", ","), "MINUTOS")]
    fb = F(PB, 96)
    anchos = [max(d.textlength(v, font=fb), d.textlength(l, font=F(PM, 27))) for v, l in vals]
    hueco = (x1 - x0 - sum(anchos)) / (len(vals) - 1)
    x = x0
    for (v, l), w in zip(vals, anchos):
        d.text((x, y + 28), v, font=fb, fill=color)
        d.text((x, y + 150), l, font=F(PM, 27), fill=sub, spacing=0)
        x += w + hueco
    d.line([(x0, y + 205), (x1, y + 205)], fill=linea, width=6)
    return y + 205


def barras(d, x0, x1, y, titulo, filas, color_t, texto, fondo, barra, etiqueta):
    d.text((x0, y), titulo, font=F(PB, 29), fill=color_t)
    y += 62
    maxg = max([f["g"] for f in filas] + [1])
    for f in filas:
        nf = fit(d, f["nombre"], PM, 340, 38)
        d.text((x0, y + 22), f["nombre"], font=nf, fill=texto, anchor="lm")
        et = etiqueta(f)
        fe = F(PB, 34)
        we = d.textlength(et, font=fe)
        d.text((x1, y + 22), et, font=fe, fill=texto, anchor="rm")
        bx0, bx1 = x0 + 360, x1 - we - 24
        d.rounded_rectangle([bx0, y + 12, bx1, y + 34], radius=11, fill=fondo)
        w = max(22, int((bx1 - bx0) * f["g"] / maxg)) if f["g"] else 10
        d.rounded_rectangle([bx0, y + 12, bx0 + w, y + 34], radius=11, fill=barra)
        y += 66
    return y


def cajas(d, x0, x1, y, items, fondo, borde, num, sub):
    gap = 24
    w = (x1 - x0 - gap * (len(items) - 1)) / max(1, len(items))
    for i, (grande, chico) in enumerate(items):
        bx = x0 + i * (w + gap)
        d.rounded_rectangle([bx, y, bx + w, y + 168], radius=28, fill=fondo, outline=borde, width=3)
        d.text((bx + 30, y + 22), grande, font=F(PB, 66), fill=num)
        d.text((bx + 30, y + 118), chico, font=fit(d, chico, PM, w - 50, 30, 20), fill=sub)
    return y + 168


# ───────────────────────────────────────────────────────── temporada: "portada de revista"
def imagen_temporada(p, t):
    B = cubrir(foto(p["key"], "club"), W, H, .3).filter(ImageFilter.GaussianBlur(40))
    B = Image.blend(B, Image.new("RGB", (W, H), (8, 10, 14)), .62).convert("RGBA")

    # carta inclinada
    cw, ch = 510, 750
    carta = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ph = redondear(cubrir(foto(p["key"], "club"), cw - 16, ch - 16, .16), 30)
    ph = degradado_abajo(ph, .72, (13, 16, 25), 235)
    carta.alpha_composite(ph, (8, 8))
    carta.alpha_composite(marco_dorado(cw, ch, 38, 8))
    cd = ImageDraw.Draw(carta)
    ap = apodo(p)
    cd.text((cw / 2, ch - 70), ap, font=fit(cd, ap, PB, cw - 80, 54), fill=GOLD, anchor="mm")
    sombra = Image.new("RGBA", (cw + 120, ch + 120), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle([60, 70, cw + 60, ch + 70], radius=40, fill=(0, 0, 0, 170))
    sombra = sombra.filter(ImageFilter.GaussianBlur(24)).rotate(5, expand=True, resample=Image.BICUBIC)
    B.alpha_composite(sombra, (12, 30))
    carta = carta.rotate(5, expand=True, resample=Image.BICUBIC)
    B.alpha_composite(carta, (54, 70))
    x = 630
    cr = logo(t.get("crest"), 120)
    if cr:
        B.alpha_composite(cr, (x, 490))
    # Ya sin capas: se dibuja sobre RGB para que los rellenos semitransparentes se mezclen bien.
    B = B.convert("RGB")
    d = ImageDraw.Draw(B, "RGBA")

    # título a la derecha
    d.text((x, 130), f"TEMPORADA {t['temporada']}", font=F(PB, 31), fill=GOLD)
    partes = nombre_simple(p).split()
    l1, l2 = partes[0], " ".join(partes[1:]) or ""
    for i, l in enumerate((l1, l2)):
        if l:
            d.text((x, 195 + i * 100), l, font=fit(d, l, PB, W - x - 50, 92, 50), fill=CREMA)
    d.text((x, 420), t["club"], font=fit(d, t["club"], PM, W - x - 50, 36), fill=(201, 207, 219))

    # cuerpo
    x0, x1 = 64, W - 64
    y = numeros(d, x0, x1, 930, t["tot"], CREMA, (185, 192, 207), CREMA)
    if t["comps"]:
        y = barras(d, x0, x1, y + 50, "GOLES POR COMPETICIÓN", t["comps"], GOLD, CREMA, (255, 255, 255, 34), GOLD,
                   lambda f: f"{f['g']} · {f['pj']} PJ")
    if t["tablas"]:
        d.text((x0, y + 30), f"{t['club'].upper()} HOY", font=F(PB, 29), fill=GOLD)
        items = [(f"{tb['rank']}°", f"{tb['liga']} · " + (f"{tb['pts']} pts" if i == 0 else f"de {tb['de']}"))
                 for i, tb in enumerate(t["tablas"])]
        cajas(d, x0, x1, y + 92, items, (255, 255, 255, 20), (255, 255, 255, 50), CREMA, (185, 192, 207))
    d.text((x0, H - 70), "LEGIÓN MX", font=F(PB, 34), fill=GOLD, anchor="lm")
    d.text((x1, H - 70), fecha_hoy(), font=F(PM, 28), fill=(170, 178, 195), anchor="rm")
    return B


# ───────────────────────────────────────────────────────── carrera: "carta + editorial"
def imagen_carrera(p, c):
    B = Image.new("RGBA", (W, H), CREMA + (255,))
    ImageDraw.Draw(B).rectangle([0, 0, W, 905], fill=(13, 16, 25))
    cw, ch = W - 108, 820
    ph = redondear(cubrir(foto(p["key"], "seleccion"), cw - 16, ch - 16, .16), 40)
    ph = degradado_abajo(ph, .45, (13, 16, 25), 240)
    B.alpha_composite(ph, (62, 56))
    B.alpha_composite(marco_dorado(cw, ch, 48, 8), (54, 48))
    cr = logo("https://media.api-sports.io/football/teams/16.png", 110)
    if cr:
        B.alpha_composite(cr, (W - 94 - cr.width, 80))
    B = B.convert("RGB")
    d = ImageDraw.Draw(B, "RGBA")
    abajo = 48 + ch
    # etiqueta y escudo
    tf = F(PB, 30)
    tw = d.textlength("CARRERA", font=tf)
    d.rounded_rectangle([94, 88, 94 + tw + 56, 150], radius=16, fill=(13, 16, 25, 200))
    d.text((94 + 28, 119), "CARRERA", font=tf, fill=GOLD, anchor="lm")
    d.text((98, abajo - 225), nombre_simple(p).upper(), font=fit(d, nombre_simple(p).upper(), PB, cw - 120, 34), fill=GOLD)
    ap = apodo(p)
    d.text((94, abajo - 185), ap, font=fit(d, ap, PB, cw - 100, 128, 60), fill=(255, 255, 255))

    x0, x1 = 66, W - 66
    y = numeros(d, x0, x1, 955, c["tot"], TINTA, GRIS, TINTA)
    if c["equipos"]:
        y = barras(d, x0, x1, y + 40, "GOLES POR EQUIPO", c["equipos"], VERDE, TINTA, (221, 213, 196), VERDE,
                   lambda f: f"{f['g']} · {f['pj']} PJ")
    d.text((x0, y + 26), "EN CLUBES · EN SELECCIÓN", font=F(PB, 29), fill=VERDE)
    gl = lambda n: "gol" if n == 1 else "goles"
    items = [(str(c["club"]["goals"]), f"{gl(c['club']['goals'])} en {c['club']['played']} partidos"),
             (str(c["sel"]["goals"]), f"{gl(c['sel']['goals'])} en {c['sel']['played']} partidos")]
    cajas(d, x0, x1, y + 86, items, (255, 255, 255), (226, 218, 201), TINTA, GRIS)
    d.text((x0, H - 70), "LEGIÓN MX", font=F(PB, 34), fill=VERDE, anchor="lm")
    d.text((x1, H - 70), fecha_hoy(), font=F(PM, 28), fill=(119, 119, 119), anchor="rm")
    return B


def huella(*objs):
    return hashlib.sha1(json.dumps([VERSION, *objs], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def main(solo=None, forzar=False):
    config = ha.load_config()
    stats = (ha.load_json(Path("player_stats.json"), {}) or {}).get("players") or {}
    standings = ha.load_json(Path("team_standings.json"), {}) or {}
    prev = ha.load_json(OUT_JSON, {}) or {}
    out = {"updated_at": prev.get("updated_at"), "players": dict(prev.get("players") or {})}
    cambio = False
    for p in config["players"]:
        if solo and p["key"] != solo:
            continue
        s = stats.get(p["key"])
        if not s:
            continue
        ent = dict(out["players"].get(p["key"]) or {})
        t = datos_temporada(p, s, standings)
        if t and t["tot"]["played"] > 0:
            h = huella(t, (FOTOS / p["key"] / "club.jpg").stat().st_mtime if (FOTOS / p["key"] / "club.jpg").exists() else 0)
            if forzar or (ent.get("temporada") or {}).get("huella") != h:
                ent["temporada"] = {"url": guardar(imagen_temporada(p, t), f"{p['key']}_temporada.jpg"),
                                    "titulo": f"Temporada {t['temporada']}", "huella": h}
                ha.log(f"🖼️  {p['key']}: temporada {t['temporada']} · {t['tot']}")
                cambio = True
        c = datos_carrera(p, s)
        if c["tot"]["played"] > 0:
            h = huella(c)
            if forzar or (ent.get("carrera") or {}).get("huella") != h:
                ent["carrera"] = {"url": guardar(imagen_carrera(p, c), f"{p['key']}_carrera.jpg"),
                                  "titulo": "Carrera", "huella": h}
                ha.log(f"🖼️  {p['key']}: carrera · {c['tot']}")
                cambio = True
        out["players"][p["key"]] = ent
    if cambio:
        out["updated_at"] = datetime.now(ha.LOCAL_TZ).isoformat()
        ha.save_json(OUT_JSON, out)
        ha.log("compartir.json actualizado")
    else:
        ha.log("compartir: sin cambios")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--jugador")
    ap.add_argument("--forzar", action="store_true")
    a = ap.parse_args()
    main(a.jugador, a.forzar)

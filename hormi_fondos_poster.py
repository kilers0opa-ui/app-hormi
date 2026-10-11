#!/usr/bin/env python3
"""
Fondos estilo póster, hechos solos a partir de las fotos de cada jugador.

Fuente: fondos_fuente/<jugador>/club.jpg y seleccion.jpg (las mismas fotos de celebración que usa la app;
ver fondos_fuente/LEEME.md). Por jugador salen 2 fondos 1080x2340:
  - Club → estilo 1 "apellido gigante": el jugador recortado (sin fondo) delante de su apellido enorme,
    con los colores y el escudo de su club.
  - Selección → estilo 2 "duotono": la foto completa en los colores de la Selección con el jugador a color
    encima, nombre grande abajo y escudo arriba.

Recortar al jugador usa rembg (modelo isnet-general-use, ~180 MB que se baja la primera vez). Es lento, así que
cada póster se guarda en fondos/ y solo se rehace si cambia la foto, el escudo, el nombre o POSTER_V
(caché en fondos/_posters_cache.json). Si un jugador cambia de club, su foto de club es del equipo anterior:
ese póster se omite hasta que haya foto nueva (meta.json → club_team_id).
"""
import hashlib
import io
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import hormi_fondos as F

POSTER_V = "1"
FUENTE_DIR = os.path.join(F.HERE, "fondos_fuente")
CACHE = os.path.join(F.OUT_DIR, "_posters_cache.json")
W, H = 1080, 2340
FB = os.path.join(F.ASSETS, "Poppins-Bold.ttf")
FM = os.path.join(F.ASSETS, "Poppins-Medium.ttf")

_session = None


def _cutout(img):
    """Jugador sin fondo (RGBA). Se queda con la figura más grande y quita la neblina del borde."""
    global _session
    import cv2
    from rembg import new_session, remove
    if _session is None:
        _session = new_session("isnet-general-use")
    cut = remove(img, session=_session)
    a = np.array(cut.split()[3])
    a[a < 60] = 0
    n, lab, st, _ = cv2.connectedComponentsWithStats((a > 0).astype("uint8"), 8)
    if n > 1:
        big = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        a[lab != big] = 0
    cut.putalpha(Image.fromarray(a))
    return cut


def _font(path, size):
    return ImageFont.truetype(path, size)


def _fit(txt, path, maxw, start):
    s = start
    while s > 60:
        f = _font(path, s)
        if f.getbbox(txt)[2] <= maxw:
            return f
        s -= 8
    return _font(path, s)


def _grad(c1, c2):
    a = np.linspace(0, 1, H)[:, None, None]
    img = (np.array(c1) * (1 - a) + np.array(c2) * a) * np.ones((H, W, 3))
    return Image.fromarray(img.astype("uint8"))


def _fade(h, top_alpha=0, bottom_alpha=240, from_top=False):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for i in range(h):
        t = i / h
        alpha = int(top_alpha + (bottom_alpha - top_alpha) * t)
        y = (h - 1 - i) if from_top else (H - h + i)
        d.line([(0, y), (W, y)], fill=(6, 8, 12, alpha))
    return ov


def _grain(img, amt=7):
    n = np.random.default_rng(7).normal(0, amt, (H, W, 1))
    return Image.fromarray(np.clip(np.array(img).astype(float) + n, 0, 255).astype("uint8"))


def _texto_claro(rgb):
    return F.luminance(rgb) > 0.35


def estilo_apellido(cut, nombre, apellido, c1, c2, crest, etiqueta):
    """Estilo 1 (club): apellido gigante detrás del jugador recortado."""
    c1, c2 = F.hex_rgb(c1), F.hex_rgb(c2)
    bg = _grad(tuple(int(x * 0.55) for x in c1), (10, 12, 18)).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for i in range(-H, W, 140):
        od.line([(i, H), (i + H, 0)], fill=c2 + (18,), width=40)
    bg = Image.alpha_composite(bg, ov)
    color_ap = c2 if _texto_claro(c2) else (255, 255, 255)
    f = _fit(apellido.upper(), FB, W - 40, 520)
    t = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(t)
    x = (W - f.getbbox(apellido.upper())[2]) // 2
    td.text((x, 330), apellido.upper(), font=f, fill=color_ap + (235,))
    td.text((x, 330 + f.size * 0.95), apellido.upper(), font=f, fill=(0, 0, 0, 0),
            stroke_width=4, stroke_fill=color_ap + (110,))
    bg = Image.alpha_composite(bg, t)
    p = cut.crop(cut.getbbox())
    r = int(H * 0.62) / p.height
    p = p.resize((int(p.width * r), int(p.height * r)), Image.LANCZOS)
    if p.width > W * 1.25:
        r = W * 1.25 / p.width
        p = p.resize((int(p.width * r), int(p.height * r)), Image.LANCZOS)
    px, py = (W - p.width) // 2, H - p.height - 330
    glow = Image.new("RGBA", p.size, c1 + (255,))
    glow.putalpha(p.split()[3])
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    halo.paste(glow, (px, py), glow)
    bg = Image.alpha_composite(bg, halo)
    bg.alpha_composite(p, (px, py))
    bg = Image.alpha_composite(bg, _fade(520))
    d = ImageDraw.Draw(bg)
    d.text((W // 2, H - 330), nombre.upper(), font=_font(FM, 58), fill=(235, 235, 235), anchor="mm")
    d.text((W // 2, H - 245), apellido.upper(), font=_fit(apellido.upper(), FB, W - 160, 110),
           fill=(255, 255, 255), anchor="mm")
    d.text((W // 2, H - 150), etiqueta.upper(), font=_font(FM, 38),
           fill=c2 if _texto_claro(c2) else (220, 220, 220), anchor="mm")
    if crest is not None:
        c = crest.copy()
        c.thumbnail((170, 170), Image.LANCZOS)
        bg.alpha_composite(c, ((W - c.width) // 2, 200))
    return _grain(bg.convert("RGB"))


def estilo_duotono(photo, cut, nombre, apellido, c1, c2, crest, etiqueta):
    """Estilo 2 (Selección): foto en duotono con los colores del equipo y el jugador a color encima."""
    c1, c2 = F.hex_rgb(c1), F.hex_rgb(c2)
    ph = ImageOps.fit(photo, (W, H), Image.LANCZOS, centering=(0.5, 0.3))
    g = ImageOps.autocontrast(ImageOps.grayscale(ph))
    duo = ImageOps.colorize(g, black=tuple(int(x * 0.25) for x in c1), white=c1).convert("RGBA")
    duo = duo.filter(ImageFilter.GaussianBlur(6))
    duo.alpha_composite(ImageOps.fit(cut, (W, H), Image.LANCZOS, centering=(0.5, 0.3)))
    duo = Image.alpha_composite(duo, _fade(700, 0, 235))
    duo = Image.alpha_composite(duo, _fade(300, 0, 150, from_top=True))
    d = ImageDraw.Draw(duo)
    d.text((80, H - 470), nombre.upper(), font=_font(FM, 64), fill=(230, 230, 230))
    d.text((72, H - 410), apellido.upper(), font=_fit(apellido.upper(), FB, W - 120, 230), fill=(255, 255, 255))
    d.rectangle([80, H - 150, 240, H - 140], fill=c2 if F.luminance(c2) > 0.15 else (255, 255, 255))
    d.text((80, H - 120), etiqueta.upper(), font=_font(FM, 40), fill=(220, 220, 220))
    if crest is not None:
        c = crest.copy()
        c.thumbnail((150, 150), Image.LANCZOS)
        duo.alpha_composite(c, (W - 80 - c.width, 120))
    return duo.convert("RGB")


def _partes(player):
    n = F.plain_name(player).split()
    return n[0], " ".join(n[1:]) or n[0]


def posters(config, only_player=None):
    """{jugador: [items para "generados"]}. Reusa los ya hechos (caché) y solo recorta lo nuevo."""
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        cache = {}
    sucio = False
    out = {}
    for p in config["players"]:
        if only_player and p["key"] != only_player:
            continue
        carpeta = os.path.join(FUENTE_DIR, p["key"])
        if not os.path.isdir(carpeta):
            continue
        try:
            meta = json.load(open(os.path.join(carpeta, "meta.json"), encoding="utf-8"))
        except Exception:
            meta = {}
        nombre, apellido = _partes(p)
        items = []
        for t in p["targets"]:
            es_sel = t.get("kind") == "seleccion"
            src = os.path.join(carpeta, "seleccion.jpg" if es_sel else "club.jpg")
            if not os.path.exists(src):
                continue
            if not es_sel and meta.get("club_team_id") and meta["club_team_id"] != t.get("team_id"):
                print(f"  póster {p['key']}/{t['key']}: la foto es del club anterior, se omite")
                continue
            c1, c2 = F.PALETAS.get(t["key"], F.PALETA_DEFAULT)
            with open(src, "rb") as fh:
                firma = hashlib.sha1(fh.read() + json.dumps(
                    [POSTER_V, t.get("crest_url"), c1, c2, nombre, apellido, t.get("label")]).encode()).hexdigest()
            fname = f"poster_{p['key']}_{'seleccion' if es_sel else 'club'}.jpg"
            destino = os.path.join(F.OUT_DIR, fname)
            if cache.get(fname) != firma or not os.path.exists(destino):
                print(f"  póster {p['key']}/{t['key']} ({'duotono' if es_sel else 'apellido gigante'})")
                try:
                    foto = Image.open(src).convert("RGB")
                    cut = _cutout(foto)
                    crest = F._fetch_crest(t.get("crest_url"))
                    if es_sel:
                        img = estilo_duotono(foto, cut, nombre, apellido, c1, c2, crest, t.get("label") or "Selección Mexicana")
                    else:
                        img = estilo_apellido(cut, nombre, apellido, c1, c2, crest, t.get("label") or "")
                except Exception as e:
                    print(f"  (póster {p['key']}/{t['key']} falló: {e})")
                    if not os.path.exists(destino):
                        continue
                else:
                    os.makedirs(F.OUT_DIR, exist_ok=True)
                    img.save(destino, "JPEG", quality=90, optimize=True)
                    cache[fname] = firma
                    sucio = True
            with open(destino, "rb") as fh:
                ver = hashlib.sha1(fh.read()).hexdigest()[:8]
            items.append({
                "id": f"poster_{'seleccion' if es_sel else 'club'}",
                "title": f"Póster · {t.get('label') or t['key']}",
                "team_key": t["key"],
                "url": f"{F.RAW_BASE}{fname}?v={ver}",
            })
        if items:
            out[p["key"]] = items
    if sucio:
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=1, sort_keys=True)
    return out

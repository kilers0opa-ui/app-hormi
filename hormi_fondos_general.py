#!/usr/bin/env python3
"""
Fondos "generales" de Legión MX: el catálogo que NO depende de un jugador.

Se guardan en fondos.json -> "general": {"categorias": [...]}, cada categoría con
su título y sus fondos (mismo formato de item que los de jugadores):

  - Escudos      un escudo grande por club que sigue la app, con sus colores.
  - Patrones     el escudo repetido en diagonal, muy suave (queda bien con iconos).
  - Selección    tricolor (verde, blanco y rojo) en varios estilos.
  - Legión MX    la marca de la app: cancha, noche de estadio, minimalista.
  - Estadios     fotos reales con licencia libre (Wikimedia Commons), recortadas
                 a formato celular, con el crédito del autor.

Lo generado se hace aquí mismo; las fotos solo si Commons responde (si falla, se
conservan las anteriores). Se invoca desde hormi_fondos.py (--solo general).
"""
import hashlib
import io
import os
import re
import time

import hormi_fondos as F
from hormi_fondos import (H, W, OUT_DIR, RAW_BASE, PALETAS, PALETA_DEFAULT, hex_rgb,
                          luminance, mix, norm, _font, _fit_font, _fetch_crest,
                          _commons, _strip_html, http_get, LICENCIAS_OK)

MAX_POR_ESTADIO = 2


# --------------------------------------------------------------- utilidades
def _gradiente(top, bottom, curva=1.0):
    """Degradado vertical W x H (rápido: una columna escalada)."""
    from PIL import Image
    col = Image.new("RGB", (1, H))
    px = col.load()
    for y in range(H):
        t = (y / (H - 1)) ** curva
        px[0, y] = mix(top, bottom, t)
    return col.resize((W, H), Image.BILINEAR).convert("RGBA")


def _escudo_escalado(crest, lado):
    from PIL import Image
    k = lado / max(crest.size)
    return crest.resize((max(1, int(crest.width * k)), max(1, int(crest.height * k))), Image.LANCZOS)


def _pegar_con_sombra(img, c, centro, blur=14, desp=(10, 16), alfa=0.5):
    from PIL import Image, ImageFilter
    cx, cy = centro
    sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
    s = Image.new("RGBA", c.size, (0, 0, 0, 120))
    s.putalpha(c.getchannel("A").point(lambda a: int(a * alfa)))
    sombra.paste(s, (cx - c.width // 2 + desp[0], cy - c.height // 2 + desp[1]), s)
    img = Image.alpha_composite(img, sombra.filter(ImageFilter.GaussianBlur(blur)))
    img.alpha_composite(c, (cx - c.width // 2, cy - c.height // 2))
    return img


def _marca(img, texto, claro, y=None):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    col = (20, 20, 20) if claro else (255, 255, 255)
    d.text((W // 2, y if y is not None else H - 150), texto, font=_font("Poppins-Medium.ttf", 36),
           fill=col + (150,), anchor="mm")


def _guardar(img, nombre):
    os.makedirs(OUT_DIR, exist_ok=True)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    data = buf.getvalue()
    with open(os.path.join(OUT_DIR, nombre), "wb") as f:
        f.write(data)
    return f"{RAW_BASE}{nombre}?v={hashlib.sha1(data).hexdigest()[:8]}"


def _item(id_, titulo, nombre_archivo, img):
    return {"id": id_, "title": titulo, "url": _guardar(img, nombre_archivo),
            "width": W, "height": H}


def _paleta(key):
    prim, sec = PALETAS.get(key, PALETA_DEFAULT)
    return hex_rgb(prim), hex_rgb(sec)


# --------------------------------------------------------------- escudos
def fondo_escudo(label, key, crest):
    from PIL import Image, ImageDraw, ImageFilter
    prim, sec = _paleta(key)
    top = mix(prim, (0, 0, 0), 0.7)
    img = _gradiente(top, prim, 1.2)
    claro = luminance(mix(top, prim, 0.6)) > 0.55
    texto = (20, 20, 20) if claro else (255, 255, 255)
    # Aro de color secundario muy tenue detrás del escudo.
    cx, cy = W // 2, int(H * 0.42)
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse([cx - 460, cy - 460, cx + 460, cy + 460], fill=sec + (46,))
    img = Image.alpha_composite(img, halo.filter(ImageFilter.GaussianBlur(60)))
    d = ImageDraw.Draw(img)
    for r, a in ((470, 70), (400, 40)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=texto + (a,), width=4)
    if crest is not None:
        img = _pegar_con_sombra(img, _escudo_escalado(crest, 640), (cx, cy), blur=18, desp=(12, 20))
    else:
        d.text((cx, cy), label[:1].upper(), font=_font("Poppins-Bold.ttf", 420), fill=texto + (230,), anchor="mm")
    d = ImageDraw.Draw(img)
    nombre = label.upper()
    f = _fit_font(d, nombre, "Poppins-Bold.ttf", W - 140, 130, 50)
    d.text((W // 2, int(H * 0.72)), nombre, font=f, fill=texto + (245,), anchor="mm")
    d.rectangle([W // 2 - 70, int(H * 0.72) + 95, W // 2 + 70, int(H * 0.72) + 101], fill=sec + (255,))
    _marca(img, "LEGIÓN MX", claro)
    return img


def fondo_patron(label, key, crest):
    from PIL import Image
    prim, sec = _paleta(key)
    top = mix(prim, (0, 0, 0), 0.55)
    img = _gradiente(top, mix(prim, (0, 0, 0), 0.15), 1.0)
    claro = luminance(mix(top, prim, 0.6)) > 0.55
    if crest is not None:
        lado, paso = 170, 320
        base = _escudo_escalado(crest, lado)
        # Se aplana el escudo a un solo color suave: se ve como "estampado",
        # no como un montón de escudos compitiendo.
        alfa = base.getchannel("A").point(lambda a: int(a * 0.30))
        capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        tinta = Image.new("RGBA", base.size, ((20, 20, 20) if claro else (255, 255, 255)) + (255,))
        tinta.putalpha(alfa)
        fila = 0
        for y in range(-paso, H + paso, paso * 3 // 4):
            desp = (paso // 2) if fila % 2 else 0
            for x in range(-paso + desp, W + paso, paso):
                capa.paste(tinta, (x, y), tinta)  # las fichas no se traslapan; paste admite bordes
            fila += 1
        img = Image.alpha_composite(img, capa)
    _marca(img, "LEGIÓN MX", claro)
    return img


# --------------------------------------------------------------- selección
VERDE, BLANCO, ROJO = (0, 104, 71), (245, 245, 240), (206, 17, 38)


def fondo_tricolor(crest):
    from PIL import Image
    img = Image.new("RGBA", (W, H))
    ancho = W // 3
    for i, c in enumerate((VERDE, BLANCO, ROJO)):
        img.paste(Image.new("RGBA", (ancho + 1, H), c + (255,)), (i * ancho, 0))
    # Viñeta oscura abajo para que el escudo y el texto respiren.
    sombra = _gradiente((0, 0, 0), (0, 0, 0))
    sombra.putalpha(Image.linear_gradient("L").resize((W, H)).point(lambda v: int(v * 0.55)))
    img = Image.alpha_composite(img, sombra)
    cx, cy = W // 2, int(H * 0.44)
    if crest is not None:
        img = _pegar_con_sombra(img, _escudo_escalado(crest, 560), (cx, cy), blur=20, desp=(10, 18), alfa=0.6)
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.text((W // 2, int(H * 0.74)), "MÉXICO", font=_fit_font(d, "MÉXICO", "Poppins-Bold.ttf", W - 140, 220, 80),
           fill=(255, 255, 255, 255), anchor="mm")
    _marca(img, "LEGIÓN MX", False)
    return img


def fondo_seleccion_noche(crest):
    from PIL import Image, ImageDraw, ImageFilter
    img = _gradiente((4, 20, 14), VERDE, 1.4)
    luz = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(luz).ellipse([-200, int(H * 0.05), W + 200, int(H * 0.75)], fill=VERDE + (110,))
    img = Image.alpha_composite(img, luz.filter(ImageFilter.GaussianBlur(120)))
    d = ImageDraw.Draw(img)
    # Franja tricolor fina arriba y abajo.
    alto = 22
    for i, c in enumerate((VERDE, BLANCO, ROJO)):
        for y0 in (0, H - alto):
            d.rectangle([i * (W // 3), y0, (i + 1) * (W // 3), y0 + alto], fill=c + (255,))
    cx, cy = W // 2, int(H * 0.42)
    if crest is not None:
        img = _pegar_con_sombra(img, _escudo_escalado(crest, 600), (cx, cy), blur=22, desp=(12, 22), alfa=0.6)
    d = ImageDraw.Draw(img)
    f = _fit_font(d, "SELECCIÓN MEXICANA", "Poppins-Bold.ttf", W - 140, 100, 44)
    d.text((W // 2, int(H * 0.72)), "SELECCIÓN MEXICANA", font=f, fill=(255, 255, 255, 245), anchor="mm")
    f2 = _font("Poppins-Medium.ttf", 52)
    d.text((W // 2, int(H * 0.72) + 100), "EL TRI", font=f2, fill=ROJO + (255,), anchor="mm")
    _marca(img, "LEGIÓN MX", False, H - 120)
    return img


def fondo_viva_mexico():
    from PIL import Image, ImageDraw
    img = _gradiente((10, 10, 10), (30, 8, 12), 1.0)
    d = ImageDraw.Draw(img)
    ancho = W // 3
    for i, c in enumerate((VERDE, BLANCO, ROJO)):
        d.rectangle([i * ancho, int(H * 0.60), (i + 1) * ancho + 1, int(H * 0.60) + 36], fill=c + (255,))
    f = _fit_font(d, "¡VIVA", "Poppins-Bold.ttf", W - 120, 330, 100)
    d.text((W // 2, int(H * 0.30)), "¡VIVA", font=f, fill=(255, 255, 255, 255), anchor="mm")
    f = _fit_font(d, "MÉXICO!", "Poppins-Bold.ttf", W - 120, 330, 100)
    d.text((W // 2, int(H * 0.30) + 300), "MÉXICO!", font=f, fill=(255, 255, 255, 255), anchor="mm")
    f2 = _font("Poppins-Medium.ttf", 54)
    d.text((W // 2, int(H * 0.60) + 130), "SIEMPRE CON EL TRI", font=f2, fill=(255, 255, 255, 190), anchor="mm")
    _marca(img, "LEGIÓN MX", False)
    return img


# --------------------------------------------------------------- marca Legión MX
def _lineas_cancha(d, color, ancho=6):
    """Dibuja una cancha vertical que llena W x H."""
    mx, my = 90, 150
    x0, y0, x1, y1 = mx, my, W - mx, H - my
    d.rectangle([x0, y0, x1, y1], outline=color, width=ancho)
    d.line([x0, H // 2, x1, H // 2], fill=color, width=ancho)
    r = 170
    d.ellipse([W // 2 - r, H // 2 - r, W // 2 + r, H // 2 + r], outline=color, width=ancho)
    d.ellipse([W // 2 - 12, H // 2 - 12, W // 2 + 12, H // 2 + 12], fill=color)
    for arriba in (True, False):
        gx0, gx1 = W // 2 - 300, W // 2 + 300
        if arriba:
            d.rectangle([gx0, y0, gx1, y0 + 330], outline=color, width=ancho)
            d.rectangle([W // 2 - 140, y0, W // 2 + 140, y0 + 130], outline=color, width=ancho)
        else:
            d.rectangle([gx0, y1 - 330, gx1, y1], outline=color, width=ancho)
            d.rectangle([W // 2 - 140, y1 - 130, W // 2 + 140, y1], outline=color, width=ancho)


def fondo_cancha():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (22, 94, 62, 255))
    d = ImageDraw.Draw(img)
    franja = H // 12
    for i in range(12):
        if i % 2 == 0:
            d.rectangle([0, i * franja, W, (i + 1) * franja], fill=(26, 104, 68, 255))
    _lineas_cancha(d, (255, 255, 255, 215))
    _marca(img, "LEGIÓN MX", False, H - 70)
    return img


def fondo_noche_estadio():
    from PIL import Image, ImageDraw, ImageFilter
    img = _gradiente((3, 8, 14), (10, 52, 38), 1.5)
    luces = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(luces)
    d.polygon([(120, 0), (330, 0), (W // 2 + 120, int(H * 0.82)), (W // 2 - 380, int(H * 0.82))], fill=(255, 255, 255, 42))
    d.polygon([(W - 330, 0), (W - 120, 0), (W // 2 + 380, int(H * 0.82)), (W // 2 - 120, int(H * 0.82))], fill=(255, 255, 255, 42))
    img = Image.alpha_composite(img, luces.filter(ImageFilter.GaussianBlur(30)))
    d = ImageDraw.Draw(img)
    for x in (225, W - 225):
        d.ellipse([x - 70, -40, x + 70, 100], fill=(255, 255, 255, 235))
    # Césped al fondo con líneas en perspectiva simple.
    d.rectangle([0, int(H * 0.78), W, H], fill=(14, 82, 52, 255))
    d.line([0, int(H * 0.78), W, int(H * 0.78)], fill=(255, 255, 255, 120), width=5)
    f = _fit_font(d, "LEGIÓN MX", "Poppins-Bold.ttf", W - 140, 170, 70)
    d.text((W // 2, int(H * 0.42)), "LEGIÓN MX", font=f, fill=(255, 255, 255, 255), anchor="mm")
    f2 = _font("Poppins-Medium.ttf", 48)
    d.text((W // 2, int(H * 0.42) + 120), "LOS SEGUIMOS DONDE JUEGUEN", font=f2, fill=(255, 255, 255, 190), anchor="mm")
    return img


def fondo_minimal():
    from PIL import Image, ImageDraw
    img = _gradiente((8, 24, 19), (16, 56, 44), 1.0)
    d = ImageDraw.Draw(img)
    d.ellipse([W // 2 - 300, int(H * 0.42) - 300, W // 2 + 300, int(H * 0.42) + 300], outline=(255, 255, 255, 60), width=5)
    f = _fit_font(d, "LEGIÓN", "Poppins-Bold.ttf", W - 160, 190, 80)
    d.text((W // 2, int(H * 0.42) - 20), "LEGIÓN", font=f, fill=(255, 255, 255, 255), anchor="mm")
    f = _fit_font(d, "MX", "Poppins-Bold.ttf", W - 160, 190, 80)
    d.text((W // 2, int(H * 0.42) + 150), "MX", font=f, fill=(0, 200, 130, 255), anchor="mm")
    return img


# --------------------------------------------------------------- generación
def generar_general(config):
    """Devuelve las categorías generadas (sin fotos)."""
    clubes, seleccion = {}, None
    for p in config["players"]:
        for t in p["targets"]:
            if t["key"] == "seleccion":
                seleccion = seleccion or t
            else:
                clubes.setdefault(t["key"], t)
    escudos, patrones = [], []
    for key, t in sorted(clubes.items(), key=lambda kv: (kv[1].get("label") or kv[0]).lower()):
        label = t.get("label") or key
        print(f"General: escudo/patrón {key}")
        crest = _fetch_crest(t.get("crest_url"))
        escudos.append(_item(f"esc_{key}", label, f"general_escudo_{key}.jpg", fondo_escudo(label, key, crest)))
        patrones.append(_item(f"pat_{key}", label, f"general_patron_{key}.jpg", fondo_patron(label, key, crest)))
    print("General: selección y marca")
    sel_crest = _fetch_crest((seleccion or {}).get("crest_url"))
    sel = [
        _item("sel_tricolor", "Tricolor", "general_sel_tricolor.jpg", fondo_tricolor(sel_crest)),
        _item("sel_noche", "El Tri de noche", "general_sel_noche.jpg", fondo_seleccion_noche(sel_crest)),
        _item("sel_viva", "¡Viva México!", "general_sel_viva.jpg", fondo_viva_mexico()),
    ]
    marca = [
        _item("marca_cancha", "Cancha", "general_marca_cancha.jpg", fondo_cancha()),
        _item("marca_noche", "Noche de estadio", "general_marca_noche.jpg", fondo_noche_estadio()),
        _item("marca_minimal", "Minimalista", "general_marca_minimal.jpg", fondo_minimal()),
    ]
    return [
        {"id": "seleccion", "title": "Selección Mexicana", "items": sel},
        {"id": "legion", "title": "Legión MX", "items": marca},
        {"id": "escudos", "title": "Escudos", "items": escudos},
        {"id": "patrones", "title": "Patrones", "items": patrones},
    ]


# --------------------------------------------------------------- fotos de estadios
ESTADIOS = [
    # (consulta en Commons, palabras que deben aparecer, título para la app)
    ("Estadio Azteca", ("azteca", "banorte"), "Estadio Azteca"),
    ("Estadio Akron", ("akron",), "Estadio Akron"),
    ("Estadio BBVA Monterrey", ("bbva",), "Estadio BBVA"),
    ("Estadio Olímpico Universitario", ("olimpico universitario", "ciudad universitaria"), "Estadio Olímpico Universitario"),
    ("Estadio Jalisco", ("jalisco",), "Estadio Jalisco"),
    ("Estadio Caliente Tijuana", ("caliente", "tijuana"), "Estadio Caliente"),
]

# Estadios de los clubes que siguen los jugadores (aparecen en la sección de cada
# jugador, en "contexto"): (consulta, palabras clave, título, team_key del config).
ESTADIOS_CLUB = [
    ("Karaiskakis Stadium Piraeus", ("karaiskakis",), "Estadio Karaiskakis", "olympiacos"),
    ("Molineux Stadium Wolverhampton", ("molineux",), "Molineux", "wolves"),
    ("Luigi Ferraris stadium Genoa", ("ferraris",), "Stadio Luigi Ferraris", "genoa"),
    ("Estádio do Dragão Porto", ("dragao", "dragon"), "Estádio do Dragão", "porto"),
    ("AFAS Stadion Alkmaar", ("afas", "alkmaar"), "AFAS Stadion", "az_alkmaar"),
    ("Estadio Metropolitano Madrid Atlético", ("metropolitano",), "Estadio Metropolitano", "atletico_madrid"),
    ("Estadio Benito Villamarín Sevilla", ("villamarin",), "Benito Villamarín", "betis"),
    ("Parken Stadium Copenhagen", ("parken",), "Parken", "copenhague"),
    ("Estadio Caliente Tijuana", ("caliente", "tijuana"), "Estadio Caliente", "tijuana"),
    ("Al-Qadsiah Stadium Khobar", ("qadsiah", "qadisiyah", "khobar"), "Estadio Al-Qadsiah", "al_qadsiah"),
]
MIN_LADO = 1600  # alto mínimo de la foto original para que el recorte vertical se vea nítido


def _recortar_vertical(img):
    """Recorte centrado a la proporción del celular y escala a W x H."""
    from PIL import Image
    objetivo = W / H
    if img.width / img.height > objetivo:
        nuevo_w = int(img.height * objetivo)
        x0 = (img.width - nuevo_w) // 2
        img = img.crop((x0, 0, x0 + nuevo_w, img.height))
    else:
        nuevo_h = int(img.width / objetivo)
        y0 = (img.height - nuevo_h) // 2
        img = img.crop((0, y0, img.width, y0 + nuevo_h))
    return img.resize((W, H), Image.LANCZOS)


def buscar_estadios(lista=None, max_por=MAX_POR_ESTADIO):
    """Fotos libres de estadios, recortadas a formato celular. Lanza si Commons falla.
    Cada elemento de `lista` es (consulta, claves, título[, team_key])."""
    from PIL import Image
    items = []
    vistos = set()
    for entrada in (lista or ESTADIOS):
        consulta, claves, titulo = entrada[:3]
        team_key = entrada[3] if len(entrada) > 3 else None
        data = _commons({
            "generator": "search", "gsrnamespace": "6", "gsrsearch": consulta + " filetype:bitmap",
            "gsrlimit": "30", "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "2600",
            "iiextmetadatafilter": "Artist|LicenseShortName|ImageDescription|Categories|ObjectName",
        })
        cand = []
        for page in (data.get("query") or {}).get("pages", []):
            info = (page.get("imageinfo") or [None])[0]
            if not info or info.get("mime") != "image/jpeg" or page["pageid"] in vistos:
                continue
            meta = info.get("extmetadata") or {}
            licencia = _strip_html((meta.get("LicenseShortName") or {}).get("value"))
            if not LICENCIAS_OK.match(licencia):
                continue
            pajar = norm(" ".join([
                page.get("title", ""),
                _strip_html((meta.get("ImageDescription") or {}).get("value")),
                _strip_html((meta.get("ObjectName") or {}).get("value")),
            ]))
            titulo_n = norm(page.get("title", ""))
            if not any(c in titulo_n for c in claves):
                continue
            if not re.search(r"\b(estadio|stadium|stadion|stadio|estadi|parken|molineux|dragao|metropolitano|villamarin|azteca|akron|bbva|jalisco|caliente|ferraris|karaiskakis)\b", pajar):
                continue
            # Fuera mapas, planos, escudos, logos, gente/aficionados, metro, ríos…
            if re.search(r"\b(map|mapa|logo|plano|diagram|scheme|crest|escudo|badge|interior de|ticket|boleto|station|metro|estacion|fans?|supporters?|aficion|celebration|crowd|river|bisagno|riva|gigi|player|jugador|jersey|shirt|train|bus|tram|poster|painting|statue|monument|night market|ai[- ]generated)\b", pajar):
                continue
            w, h = info.get("width") or 0, info.get("height") or 0
            if h < MIN_LADO or w < 1200:
                continue
            cand.append((w * h, page, info, licencia))
        cand.sort(key=lambda c: c[0], reverse=True)
        tomadas = 0
        for _, page, info, licencia in cand:
            if tomadas >= max_por:
                break
            try:
                img = Image.open(io.BytesIO(http_get(info.get("thumburl") or info["url"], timeout=60))).convert("RGB")
                img = _recortar_vertical(img)
            except Exception as e:
                print(f"  (no se pudo procesar {page.get('title')}: {e})")
                continue
            meta = info.get("extmetadata") or {}
            artista = _strip_html((meta.get("Artist") or {}).get("value")) or "Autor desconocido"
            if len(artista) > 80:
                artista = artista[:77] + "…"
            idf = f"commons_{page['pageid']}"
            url = _guardar(img, f"foto_general_{idf}.jpg")
            vistos.add(page["pageid"])
            items.append({
                "id": idf, "title": titulo, "url": url, "width": W, "height": H,
                "credit": f"Foto: {artista} · {licencia} · Wikimedia Commons",
                "source_url": info.get("descriptionurl"),
                **({"team_key": team_key} if team_key else {}),
            })
            tomadas += 1
            time.sleep(1)
        print(f"  {titulo}: {tomadas} foto(s)")
        time.sleep(1)
    return items


# --------------------------------------------------------------- fotos de la selección
def buscar_seleccion(maximo=6):
    """Fotos libres de partidos/jugadores de la Selección Mexicana (Commons).
    Son fotos del equipo en general, no de un jugador en particular."""
    from PIL import Image
    cand = {}
    for consulta in ("Mexico national football team 2018 FIFA World Cup", "Mexico World Cup 2022 Qatar football",
                     "Selección Mexicana Copa Oro", "Mexico Copa America football match", "Mexico national football team friendly"):
        data = _commons({
            "generator": "search", "gsrnamespace": "6", "gsrsearch": consulta + " filetype:bitmap",
            "gsrlimit": "50", "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "2200",
            "iiextmetadatafilter": "Artist|LicenseShortName|ImageDescription|Categories|ObjectName",
        })
        for page in (data.get("query") or {}).get("pages", []):
            cand[page["pageid"]] = page
        time.sleep(1)
    buenas = []
    for page in cand.values():
        info = (page.get("imageinfo") or [None])[0]
        if not info or info.get("mime") != "image/jpeg":
            continue
        meta = info.get("extmetadata") or {}
        licencia = _strip_html((meta.get("LicenseShortName") or {}).get("value"))
        if not LICENCIAS_OK.match(licencia):
            continue
        pajar = norm(" ".join([
            page.get("title", ""),
            _strip_html((meta.get("ImageDescription") or {}).get("value")),
            _strip_html((meta.get("Categories") or {}).get("value")).replace("|", " "),
        ]))
        if not re.search(r"\b(mexico|mexican|mexicana|mexicano)\b", pajar):
            continue
        if not re.search(r"\b(mex|mexico|mexican|mexicana|mexicano)\b", norm(page.get("title", "")).replace("_", " ")):
            continue  # el título debe nombrar a México (descarta p. ej. "Arabia Saudita vs México")
        if not re.search(r"\b(national|nacional|seleccion|world cup|mundial|copa|gold cup|friendly|amistoso|concacaf|match|partido|qualif)\b", pajar):
            continue
        if re.search(r"\b(logo|map|mapa|crest|escudo|badge|women|womens|femenil|feminine|femenina|u-?1\d|u-?2\d|sub-?\d+|ticket|blind|ibsa|paralymp|jersey|shirt|kit|camiseta|playera|stamp|beach|futsal|amputee|cerebral|deaf|1[0-9]{3}|198\d|197\d|199\d|200[0-9]|201[0-5]|ai[- ]generated)\b", pajar):
            continue
        w, h = info.get("width") or 0, info.get("height") or 0
        if h < 1500 or w < 1000:
            continue
        buenas.append((1 if h >= w else 0, w * h, page, info, licencia))
    buenas.sort(key=lambda b: (b[0], b[1]), reverse=True)
    items = []
    for _, _, page, info, licencia in buenas:
        if len(items) >= maximo:
            break
        try:
            img = Image.open(io.BytesIO(http_get(info.get("thumburl") or info["url"], timeout=60))).convert("RGB")
            img = _recortar_vertical(img)
        except Exception as e:
            print(f"  (no se pudo procesar {page.get('title')}: {e})")
            continue
        meta = info.get("extmetadata") or {}
        artista = _strip_html((meta.get("Artist") or {}).get("value")) or "Autor desconocido"
        if len(artista) > 80:
            artista = artista[:77] + "…"
        idf = f"commons_{page['pageid']}"
        url = _guardar(img, f"foto_general_{idf}.jpg")
        items.append({
            "id": idf, "title": "Selección Mexicana", "url": url, "width": W, "height": H,
            "credit": f"Foto: {artista} · {licencia} · Wikimedia Commons",
            "source_url": info.get("descriptionurl"), "team_key": "seleccion",
        })
        time.sleep(1)
    print(f"  Selección: {len(items)} foto(s)")
    return items

#!/usr/bin/env python3
"""
Fondos de pantalla (wallpapers) de Legión MX.

Produce fondos.json (lo lee la pantalla "Fondos" de la app) con, por jugador:
  - "generados": un wallpaper por equipo que sigue (club y selección), hecho
    aquí mismo con los colores del equipo, su escudo y el nombre del jugador
    (se guardan en fondos/*.jpg, 1080x2340).
  - "fotos": hasta 4 fotos reales con licencia libre (CC BY, CC BY-SA, CC0 o
    dominio público) buscadas en Wikimedia Commons, con el crédito del autor
    y la licencia — hay que darlo, y la app lo muestra.

Las fotos propias de celebración de gol viven dentro de la app (res/drawable),
no pasan por aquí.

Uso:
  python hormi_fondos.py                 # todo, todos los jugadores
  python hormi_fondos.py --solo generar  # solo wallpapers hechos aquí
  python hormi_fondos.py --solo fotos    # solo búsqueda en Commons
  python hormi_fondos.py --jugador mora

Si Commons no responde, conserva las fotos que ya estaban en fondos.json (nunca
deja a un jugador sin fotos por un fallo de red).
"""
import argparse
import hashlib
import html
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "hormi_config.json")
OUT_JSON = os.path.join(HERE, "fondos.json")
OUT_DIR = os.path.join(HERE, "fondos")
ASSETS = os.path.join(HERE, "fondos_assets")
RAW_BASE = "https://raw.githubusercontent.com/kilers0opa-ui/app-hormi/main/fondos/"
UA = "LegionMX-Fondos/1.0 (https://github.com/kilers0opa-ui/app-hormi)"

W, H = 1080, 2340
MAX_FOTOS = 4

# (color principal, color secundario) por target. El texto se elige solo
# (blanco u oscuro) según qué tan claro quede el fondo.
PALETAS = {
    "seleccion": ("#006847", "#CE1126"),
    "olympiacos": ("#C8102E", "#FFFFFF"),
    "wolves": ("#FDB913", "#231F20"),
    "al_qadsiah": ("#FFD100", "#0B3C8C"),
    "tijuana": ("#C8102E", "#111111"),
    "genoa": ("#9B1B30", "#0A2A5E"),
    "porto": ("#003A8F", "#FFFFFF"),
    "az_alkmaar": ("#E2001A", "#FFFFFF"),
    "atletico_madrid": ("#CB3524", "#272E61"),
    "betis": ("#00954C", "#FFFFFF"),
    "copenhague": ("#0A2A6B", "#FFFFFF"),
}
PALETA_DEFAULT = ("#1F6F5C", "#0B1F1A")


# ---------------------------------------------------------------- utilidades
def plain_name(player):
    """Nombre sin el apodo entre comillas."""
    return re.sub(r'\s*"[^"]*"\s*', " ", player["name"]).strip()


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminance(rgb):
    r, g, b = [c / 255 for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def load_previous():
    try:
        with open(OUT_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# -------------------------------------------------------------- generación
def _font(name, size):
    from PIL import ImageFont
    candidatos = [
        os.path.join(ASSETS, name),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for c in candidatos:
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_font(draw, text, name, max_w, start, minimum=40):
    size = start
    while size > minimum:
        f = _font(name, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return _font(name, minimum)


def _fetch_crest(url):
    from PIL import Image
    if not url:
        return None
    try:
        data = http_get(url, timeout=20)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:  # sin escudo el fondo sigue saliendo bien
        print(f"  (sin escudo: {e})")
        return None


def make_wallpaper(player, target, crest):
    """Devuelve una imagen PIL RGB de W x H."""
    from PIL import Image, ImageDraw, ImageFilter

    prim, sec = (hex_rgb(c) for c in PALETAS.get(target["key"], PALETA_DEFAULT))
    # Fondo: degradado vertical, oscuro arriba -> color del equipo abajo.
    base = Image.new("RGB", (W, H))
    top = mix(prim, (0, 0, 0), 0.62)
    px = base.load()
    for y in range(H):
        t = (y / (H - 1)) ** 1.15
        row = mix(top, prim, t)
        for x in range(W):
            px[x, y] = row
    img = base.convert("RGBA")

    # Franjas diagonales anchas con el color secundario, muy suaves.
    franjas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(franjas)
    for i, off in enumerate((-300, 380, 1060)):
        ancho = 210 if i == 1 else 120
        d.polygon([(off, H), (off + ancho, H), (off + ancho + 1300, 0), (off + 1300, 0)],
                  fill=sec + (30,))
    img = Image.alpha_composite(img, franjas)

    # Apellido gigante de fondo, girado y casi transparente.
    d = ImageDraw.Draw(img)
    partes = plain_name(player).split()
    apellido = (partes[-1] if partes else player["key"]).upper()
    claro = luminance(mix(top, prim, 0.6)) > 0.55
    texto = (20, 20, 20) if claro else (255, 255, 255)
    f_ghost = _fit_font(d, apellido, "Poppins-Bold.ttf", H - 300, 420, 120)
    ghost = Image.new("RGBA", (int(d.textlength(apellido, font=f_ghost)) + 40, 560), (0, 0, 0, 0))
    ImageDraw.Draw(ghost).text((20, 20), apellido, font=f_ghost, fill=texto + (22,))
    ghost = ghost.rotate(90, expand=True)
    img.alpha_composite(ghost, (W - ghost.width + 15, (H - ghost.height) // 2))

    # Halo + escudo al centro.
    cx, cy = W // 2, int(H * 0.40)
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse([cx - 380, cy - 380, cx + 380, cy + 380], fill=texto + (34,))
    halo = halo.filter(ImageFilter.GaussianBlur(40))
    img = Image.alpha_composite(img, halo)
    d = ImageDraw.Draw(img)
    d.ellipse([cx - 330, cy - 330, cx + 330, cy + 330], outline=texto + (110,), width=6)
    if crest is not None:
        lado = 470
        c = crest.copy()
        # Los escudos de la API son chicos (~150 px): se agrandan a `lado`.
        k = lado / max(c.size)
        c = c.resize((max(1, int(c.width * k)), max(1, int(c.height * k))), Image.LANCZOS)
        sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
        s = Image.new("RGBA", c.size, (0, 0, 0, 120))
        s.putalpha(c.getchannel("A").point(lambda a: int(a * 0.5)))
        sombra.paste(s, (cx - c.width // 2 + 10, cy - c.height // 2 + 16), s)
        img = Image.alpha_composite(img, sombra.filter(ImageFilter.GaussianBlur(14)))
        img.alpha_composite(c, (cx - c.width // 2, cy - c.height // 2))
    else:
        d = ImageDraw.Draw(img)
        inicial = (target.get("label") or "?")[:1].upper()
        f = _font("Poppins-Bold.ttf", 320)
        d.text((cx, cy), inicial, font=f, fill=texto + (230,), anchor="mm")

    # Nombre del jugador.
    d = ImageDraw.Draw(img)
    y0 = int(H * 0.66)
    nombre = " ".join(partes[:-1]).upper() if len(partes) > 1 else ""
    if nombre:
        f1 = _fit_font(d, nombre, "Poppins-Medium.ttf", W - 160, 78, 36)
        d.text((W // 2, y0), nombre, font=f1, fill=texto + (230,), anchor="mm")
    f2 = _fit_font(d, apellido, "Poppins-Bold.ttf", W - 120, 190, 70)
    d.text((W // 2, y0 + 130), apellido, font=f2, fill=texto + (255,), anchor="mm")

    # Etiqueta del equipo (con numero opcional del config) y marca.
    etiqueta = (target.get("label") or "").upper()
    numero = player.get("numero")
    if numero:
        etiqueta = f"#{numero}  ·  {etiqueta}"
    f3 = _fit_font(d, etiqueta, "Poppins-Medium.ttf", W - 200, 52, 28)
    d.text((W // 2, y0 + 290), etiqueta, font=f3, fill=texto + (200,), anchor="mm")
    d.rectangle([W // 2 - 70, y0 + 345, W // 2 + 70, y0 + 351], fill=sec + (255,) if luminance(sec) > 0.2 or not claro else texto + (200,))
    f4 = _font("Poppins-Medium.ttf", 36)
    d.text((W // 2, H - 150), "LEGIÓN MX", font=f4, fill=texto + (150,), anchor="mm")
    return img.convert("RGB")


def generar(config, only_player=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    resultado = {}
    for p in config["players"]:
        if only_player and p["key"] != only_player:
            continue
        items = []
        for t in p["targets"]:
            print(f"Generando {p['key']} / {t['key']}")
            crest = _fetch_crest(t.get("crest_url"))
            img = make_wallpaper(p, t, crest)
            fname = f"{p['key']}_{t['key']}.jpg"
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90, optimize=True)
            data = buf.getvalue()
            with open(os.path.join(OUT_DIR, fname), "wb") as f:
                f.write(data)
            version = hashlib.sha1(data).hexdigest()[:8]
            items.append({
                "id": f"gen_{t['key']}",
                "title": t.get("label") or t["key"],
                "team_key": t["key"],
                "url": f"{RAW_BASE}{fname}?v={version}",
            })
        resultado[p["key"]] = items
    return resultado


# ------------------------------------------------------------ Wikimedia Commons
CONTEXTO_FUTBOL = r"\b(football|footballer|futbol|futbolista|soccer|fifa|concacaf|liga mx|national team|seleccion|world cup|copa mundial|uefa)\b"
LICENCIAS_OK = re.compile(r"^(cc[ -]?by|cc[ -]?0|cc0|public domain|pd\b|attribution)", re.I)


def _strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _commons(params):
    params = dict(params, action="query", format="json", formatversion="2")
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    return json.loads(http_get(url, timeout=30).decode("utf-8"))


def buscar_fotos(player):
    """Fotos libres del jugador en Commons. Lanza excepción si Commons falla."""
    nombre = plain_name(player)
    partes = norm(nombre).split()
    primero, apellido = partes[0], partes[-1]
    clubes = [norm(t.get("label") or "") for t in player.get("targets", []) if t["key"] != "seleccion"]
    clubes = [c for c in clubes if len(c) >= 4]
    candidatos = {}
    for consulta in (f'"{nombre}" footballer', f'"{nombre}"'):
        data = _commons({
            "generator": "search", "gsrnamespace": "6", "gsrsearch": consulta,
            "gsrlimit": "40", "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "1200",
            "iiextmetadatafilter": "Artist|LicenseShortName|ImageDescription|Categories|ObjectName|DateTimeOriginal",
        })
        for page in (data.get("query") or {}).get("pages", []):
            candidatos[page["pageid"]] = page
        time.sleep(1)

    fotos = []
    for page in candidatos.values():
        info = (page.get("imageinfo") or [None])[0]
        if not info or info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        meta = info.get("extmetadata") or {}
        licencia = _strip_html((meta.get("LicenseShortName") or {}).get("value"))
        if not LICENCIAS_OK.match(licencia):
            continue
        # Que de verdad sea este jugador: nombre y apellido en título,
        # descripción o categorías (evita homónimos y fotos de otra gente).
        pajar = norm(" ".join([
            page.get("title", ""),
            _strip_html((meta.get("ImageDescription") or {}).get("value")),
            _strip_html((meta.get("ObjectName") or {}).get("value")),
            _strip_html((meta.get("Categories") or {}).get("value")).replace("|", " "),
        ]))
        if primero not in pajar or apellido not in pajar:
            continue
        # Y que sea de fútbol: evita políticos, músicos y otros homónimos
        # (p. ej. otro "Armando González"). Debe aparecer el deporte o el
        # equipo (club/selección) del jugador.
        # Y que el contexto sea el suyo: selección mexicana o su club actual
        # (descarta homónimos de otros países, p. ej. un "Julián Quiñones" de
        # una selección juvenil colombiana). Prefiero pocas fotos seguras.
        if not (re.search(CONTEXTO_FUTBOL, pajar) or any(c in pajar for c in clubes)):
            continue
        if not (re.search(r"\bmexic", pajar) or any(c in pajar for c in clubes)):
            continue
        if re.search(r"\b(politic|diputad|senador|alcalde|gobernador|candidat|cantante|actor|actriz)", pajar):
            continue
        w, h = info.get("width") or 0, info.get("height") or 0
        if w < 800 or h < 800:
            continue
        artista = _strip_html((meta.get("Artist") or {}).get("value")) or "Autor desconocido"
        if len(artista) > 80:
            artista = artista[:77] + "…"
        fotos.append({
            "score": (1 if h >= w else 0, min(w, h)),
            "src": info.get("thumburl") or info.get("url"),
            "item": {
                "id": f"commons_{page['pageid']}",
                "title": nombre,
                "url": None,
                "credit": f"Foto: {artista} · {licencia} · Wikimedia Commons",
                "source_url": info.get("descriptionurl"),
                "width": info.get("thumbwidth") or w,
                "height": info.get("thumbheight") or h,
            },
        })
    fotos.sort(key=lambda f: f["score"], reverse=True)
    items = []
    for f in fotos:
        if len(items) >= MAX_FOTOS:
            break
        # La foto se guarda en el repo (1080 px de ancho): así la app la baja
        # de GitHub como las demás y no depende de Wikimedia.
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(http_get(f["src"], timeout=40))).convert("RGB")
            if img.width > 1080:
                img = img.resize((1080, round(img.height * 1080 / img.width)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=88, optimize=True)
        except Exception as e:
            print(f"  (no se pudo bajar {f['src']}: {e})")
            continue
        data = buf.getvalue()
        fname = f"foto_{f['item']['id']}.jpg"
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, fname), "wb") as fh:
            fh.write(data)
        f["item"]["url"] = f"{RAW_BASE}{fname}?v={hashlib.sha1(data).hexdigest()[:8]}"
        f["item"]["width"], f["item"]["height"] = img.size
        items.append(f["item"])
        time.sleep(1)
    return items


# ------------------------------------------------------------------ principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo", choices=["generar", "fotos", "general"], default=None)
    ap.add_argument("--jugador", default=None)
    args = ap.parse_args()

    config = load_config()
    previo = load_previous()
    players_prev = previo.get("players", {})
    salida = {k: dict(v) for k, v in players_prev.items()}

    if args.solo in (None, "generar"):
        for key, items in generar(config, args.jugador).items():
            salida.setdefault(key, {})["generados"] = items

    if args.solo in (None, "fotos"):
        for p in config["players"]:
            if args.jugador and p["key"] != args.jugador:
                continue
            print(f"Buscando fotos de {plain_name(p)}")
            try:
                fotos = buscar_fotos(p)
                print(f"  {len(fotos)} foto(s) libres")
                salida.setdefault(p["key"], {})["fotos"] = fotos
            except Exception as e:
                print(f"  Commons falló ({e}); se conservan las anteriores")
                salida.setdefault(p["key"], {}).setdefault("fotos", [])
            time.sleep(1)

    general = previo.get("general") or {"categorias": []}
    if args.solo in (None, "general") and not args.jugador:
        import hormi_fondos_general as G
        cats = G.generar_general(config)
        print("Buscando fotos de estadios")
        try:
            estadios = G.buscar_estadios()
        except Exception as e:
            print(f"  Commons falló ({e}); se conservan las anteriores")
            estadios = next((c["items"] for c in general.get("categorias", []) if c["id"] == "estadios"), [])
        if estadios:
            cats.append({"id": "estadios", "title": "Estadios", "items": estadios})
        general = {"categorias": cats}

    for key in list(salida):
        salida[key].setdefault("generados", [])
        salida[key].setdefault("fotos", [])
    nuevo = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "players": salida, "general": general}
    # No reescribir (y no hacer commit vacío) si nada cambió salvo la hora.
    if previo.get("players") == salida and previo.get("general") == general:
        print("Sin cambios.")
        return 0
    usadas = {os.path.basename(i["url"].split("?")[0]) for v in salida.values()
              for i in v["generados"] + v["fotos"] if i.get("url")}
    usadas |= {os.path.basename(i["url"].split("?")[0]) for c in general.get("categorias", [])
               for i in c["items"] if i.get("url")}
    for fn in os.listdir(OUT_DIR) if os.path.isdir(OUT_DIR) else []:
        if fn.startswith(("foto_", "general_")) and fn not in usadas:
            os.remove(os.path.join(OUT_DIR, fn))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(nuevo, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("fondos.json actualizado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Tarjetas para compartir (imágenes que la app comparte por WhatsApp/redes):

  - tarjeta_gol(...)     → tarjetas/<jugador>_gol.jpg (1080×1920). La genera el motor al detectar un gol de tu
    jugador; la app la ofrece SOLO 10 min (reloj real). Si anota otra vez, se reemplaza (marcador actual,
    DOBLETE / TRIPLETE) y el reloj vuelve a empezar. Sin calificación ni minutos: imagen, frase, minuto,
    competencia y marcador.
  - tarjeta_partido(...) → tarjetas/<jugador>_partido.jpg (1080×1080). Al terminar un partido con minutos (y otra
    vez cuando llega la calificación). La app la ofrece en Estadísticas hasta 24 h después del partido.

Las ilustraciones de gol son las mismas de la app (tarjetas_assets/<jugador>_<equipo>.jpg); CARAS dice dónde
queda la cara (fracción del ancho) para recorrer la imagen y que el texto no la tape.
"""
import hashlib
import io
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path("tarjetas_assets")
OUT = Path("tarjetas")
RAW = "https://raw.githubusercontent.com/kilers0opa-ui/app-hormi/main/"
FB = str(ASSETS / "DejaVuSans-Bold.ttf")
FR = str(ASSETS / "DejaVuSans.ttf")

GOLD = (255, 201, 74)
WHITE = (255, 255, 255)
MUTED = (160, 170, 184)
GREEN = (61, 220, 132)

# Dónde está la cara en cada ilustración (fracción del ancho). Detectado con OpenCV y revisado a ojo.
CARAS = {
    "_generico": .45, "armando_olympiacos": .51, "armando_seleccion": .30, "camberos_chivas": .42,
    "camberos_seleccion": .30, "campillo_chivas": .54, "campillo_seleccion": .36, "chavez_az_alkmaar": .53,
    "chavez_seleccion": .50, "fidalgo_betis": .45, "fidalgo_seleccion": .39, "fimbres_monterrey": .41,
    "fimbres_seleccion": .48, "gimenez_porto": .47, "gimenez_seleccion": .47, "huescas_copenhague": .50,
    "huescas_seleccion": .48, "luis_chavez_dinamo": .51, "luis_chavez_seleccion": .30, "montes_lokomotiv": .30,
    "montes_seleccion": .47, "mora_seleccion": .45, "mora_tijuana": .40, "obed_atletico_madrid": .55,
    "obed_seleccion": .53, "quinones_al_qadsiah": .54, "quinones_seleccion": .42, "raul_seleccion": .50,
    "raul_wolves": .48, "sandoval_chivas": .62, "sandoval_seleccion": .40, "vasquez_genoa": .44,
    "vasquez_seleccion": .52,
}

# Color del club detrás del texto (rayas blancas encima).
COLORES = {
    "seleccion": (0, 104, 71), "olympiacos": (178, 14, 40), "wolves": (214, 130, 0), "al_qadsiah": (170, 20, 30),
    "tijuana": (170, 15, 30), "genoa": (150, 20, 40), "porto": (0, 70, 160), "az_alkmaar": (190, 10, 30),
    "atletico_madrid": (190, 20, 40), "betis": (0, 120, 60), "copenhague": (20, 60, 140), "lokomotiv": (150, 20, 30),
    "dinamo": (0, 60, 150), "monterrey": (12, 32, 84), "chivas": (190, 15, 35),
}

# Frase de gol: (renglón blanco, renglón dorado). Se puede cambiar en hormi_config.json → jugador.frase_gol.
FRASES = {
    "armando": ("HORMI", "GOL"), "quinones": ("GOL DE", "LA PANTERA"), "chavez": ("GOL DEL", "TILONCITO"),
    "mora": ("GOL DE", "MORITA"), "raul": ("GOL DE", "RAÚL"), "fidalgo": ("GOL DEL", "MAGUITO"),
    "montes": ("GOL DEL", "CACHORRO"), "gimenez": ("GOL DEL", "CHAQUITO"), "luis_chavez": ("GOL DE", "CHÁVEZ"),
}

COMP_CORTA = {"Liga Profesional Saudí": "LIGA SAUDÍ", "Liga Premier de Rusia": "LIGA RUSA",
              "Superliga de Grecia": "SUPERLIGA", "La Liga": "LALIGA"}


def _f(path, size):
    return ImageFont.truetype(path, size)


def frase(player):
    cfg = player.get("frase_gol")
    if isinstance(cfg, (list, tuple)) and len(cfg) == 2:
        return tuple(str(x).upper() for x in cfg)
    if player["key"] in FRASES:
        return FRASES[player["key"]]
    apellido = (player.get("search_last_name") or player.get("name", "").split()[-1]).upper()
    return ("GOL DE", apellido)


def _foto(player_key, target_key):
    p = ASSETS / f"{player_key}_{target_key}.jpg"
    if not p.exists():
        p, cara = ASSETS / "_generico.jpg", CARAS["_generico"]
    else:
        cara = CARAS.get(f"{player_key}_{target_key}", .5)
    return Image.open(p).convert("RGB"), cara


_logos = {}


def _logo(url, size, texto="?"):
    """Escudo de la API (con caché en memoria); si no baja, un círculo con iniciales."""
    im = None
    if url:
        if url not in _logos:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                _logos[url] = Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=10).read())).convert("RGBA")
            except Exception:
                _logos[url] = None
        im = _logos[url]
    if im is not None:
        im = im.copy()
        im.thumbnail((size, size), Image.LANCZOS)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
        return out
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    d.ellipse([0, 0, size - 1, size - 1], fill=(40, 46, 58, 255))
    ini = "".join(w[0] for w in texto.replace("FC ", "").split()[:2]).upper() or "?"
    d.text((size / 2, size / 2), ini, font=_f(FB, int(size * .38)), fill=WHITE, anchor="mm")
    return out


def _paste(base, im, xy):
    base.paste(im, (int(xy[0]), int(xy[1])), im)


def _pill(d, x, y, txt, font, fg, bg, pad=(22, 10)):
    w = d.textlength(txt, font=font)
    h = font.size
    d.rounded_rectangle([x, y, x + w + 2 * pad[0], y + h + 2 * pad[1]], radius=(h + 2 * pad[1]) // 2, fill=bg)
    d.text((x + pad[0] + w / 2, y + pad[1] + h / 2), txt, font=font, fill=fg, anchor="mm")
    return x + w + 2 * pad[0]


def _fit(d, txt, maxw, start):
    s = start
    while s > 40 and d.textlength(txt, font=_f(FB, s)) > maxw:
        s -= 4
    return _f(FB, s)


def _guardar(img, nombre, size=None):
    OUT.mkdir(exist_ok=True)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86, optimize=True)
    data = buf.getvalue()
    (OUT / nombre).write_bytes(data)
    return f"{RAW}tarjetas/{nombre}?v={hashlib.sha1(data).hexdigest()[:8]}"


def comp_corta(league):
    return COMP_CORTA.get(league or "", (league or "").upper())


# ───────────────────────────────────────────────────────────── tarjeta de gol (9:16)
def tarjeta_gol(player, target, home, away, home_logo, away_logo, hg, ag, minuto, competencia, goles_partido):
    W, H = 1080, 1920
    color = COLORES.get(target["key"], (150, 20, 40))
    bg, cara = _foto(player["key"], target["key"])
    D = Image.new("RGB", (W, H), color)
    d = ImageDraw.Draw(D, "RGBA")
    for i in range(-H, W, 150):
        d.polygon([(i, 0), (i + 70, 0), (i + 70 + H * .35, H), (i + H * .35, H)], fill=(255, 255, 255, 45))
    sc = H / bg.height
    ph = bg.resize((int(bg.width * sc), H), Image.LANCZOS)
    ox = int(min(max(W * .74 - cara * ph.width, 0), W * .45))   # la cara queda a ~74 % del ancho
    m = Image.new("L", ph.size, 255)
    md = ImageDraw.Draw(m)
    for x in range(260):
        md.line([(x, 0), (x, H)], fill=int(255 * x / 260))
    D.paste(ph.crop((0, 0, W - ox, H)), (ox, 0), m.crop((0, 0, W - ox, H)))
    tint = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tint)
    for x in range(int(W * .6)):
        td.line([(x, 0), (x, H)], fill=color + (int(150 * (1 - x / (W * .6))),))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dark)
    for y in range(int(H * .62), H):
        dd.line([(0, y), (W, y)], fill=(10, 4, 8, int(235 * ((y - H * .62) / (H * .38)))))
    D = D.convert("RGBA")
    D.alpha_composite(tint)
    D.alpha_composite(dark)
    D = D.convert("RGB")
    d = ImageDraw.Draw(D, "RGBA")
    l1, l2 = frase(player)
    f1, f2 = _fit(d, l1, 560, 150), _fit(d, l2, 560, 150)
    d.text((60, 90), l1, font=f1, fill=WHITE, anchor="lt")
    y2 = 90 + f1.size + 10
    d.text((60, y2), l2, font=f2, fill=GOLD, anchor="lt")
    y3 = y2 + f2.size + 40
    _pill(d, 62, y3, f"{minuto}  ·  {comp_corta(competencia)}", _f(FB, 32), WHITE, (0, 0, 0, 150))
    extra = {2: "DOBLETE", 3: "TRIPLETE"}.get(goles_partido) or (f"{goles_partido} GOLES" if goles_partido > 3 else None)
    if extra:
        _pill(d, 62, y3 + 80, extra, _f(FB, 32), (14, 17, 22), GOLD)
    y = 1530
    _paste(D, _logo(home_logo, 140, home), (60, y))
    d.text((230, y + 70), f"{hg} - {ag}", font=_f(FB, 120), fill=WHITE, anchor="lm")
    _paste(D, _logo(away_logo, 140, away), (640, y))
    d.text((60, y + 200), f"{home} vs {away}", font=_fit(d, f"{home} vs {away}", 960, 36), fill=WHITE, anchor="lm")
    d.text((W - 60, H - 60), "LEGIÓN MX", font=_f(FB, 30), fill=(255, 255, 255, 210), anchor="rm")
    return _guardar(D, f"{player['key']}_gol.jpg")


# ───────────────────────────────────────────────────────────── tarjeta del partido (1:1)
def _circulo(img, sz, ring=None, rw=9):
    img = img.resize((sz, sz), Image.LANCZOS)
    m = Image.new("L", (sz, sz), 0)
    ImageDraw.Draw(m).ellipse([0, 0, sz - 1, sz - 1], fill=255)
    out = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    out.paste(img, (0, 0), m)
    if ring:
        ImageDraw.Draw(out).ellipse([rw // 2, rw // 2, sz - 1 - rw // 2, sz - 1 - rw // 2], outline=ring, width=rw)
    return out


MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]


def tarjeta_partido(player, u, apodo, posicion=None):
    """u = ultimo_partido de player_stats.json (home/away, goles, logos, eventos, stats...)."""
    W = H = 1080
    B = Image.new("RGB", (W, H), (14, 17, 22))
    d = ImageDraw.Draw(B, "RGBA")
    for i in range(0, W + H, 60):
        d.line([(i, 0), (i - H, H)], fill=(255, 255, 255, 10), width=18)
    d.rounded_rectangle([50, 50, W - 50, H - 50], radius=48, fill=(24, 29, 37))
    try:
        ko = datetime.fromisoformat(u["kickoff"])
        fecha = f"{ko.day} {MESES[ko.month - 1]}"
    except Exception:
        fecha = ""
    cab = " · ".join(x for x in (comp_corta(u.get("league")), fecha) if x)
    d.text((W / 2, 120), cab, font=_fit(d, cab, 900, 30), fill=MUTED, anchor="mm")
    _paste(B, _logo(u.get("home_logo"), 170, u.get("home") or ""), (150, 190))
    _paste(B, _logo(u.get("away_logo"), 170, u.get("away") or ""), (W - 320, 190))
    marcador = f"{u.get('home_goals')} - {u.get('away_goals')}"
    d.text((W / 2, 275), marcador, font=_f(FB, 140), fill=WHITE, anchor="mm")
    if u.get("penales"):
        d.text((W / 2, 370), f"Penales {u['penales']}", font=_f(FB, 28), fill=GOLD, anchor="mm")
    for x, nombre in ((235, u.get("home") or ""), (W - 235, u.get("away") or "")):
        d.text((x, 395), nombre, font=_fit(d, nombre, 300, 32), fill=WHITE, anchor="mm")
    d.line([(110, 450), (W - 110, 450)], fill=(60, 68, 80), width=2)
    # Foto: el ícono del jugador (el mismo de la app, ya centrado en la cara); si no hay, un recorte de su ilustración.
    av = ASSETS / f"avatar_{player['key']}.jpg"
    if av.exists():
        B_av = _circulo(Image.open(av).convert("RGB"), 210, GOLD)
    else:
        bg, cara = _foto(player["key"], u.get("team_key") or "")
        lado = int(bg.width * .55)
        cx = int(cara * bg.width)
        x0 = max(0, min(bg.width - lado, cx - lado // 2))
        B_av = _circulo(bg.crop((x0, int(bg.height * .02), x0 + lado, int(bg.height * .02) + lado)), 210, GOLD)
    _paste(B, B_av, (110, 500))
    d.text((350, 540), apodo, font=_fit(d, apodo, 620, 62), fill=WHITE, anchor="lm")
    sub = player.get("name", "").replace(f'"{apodo}" ', "")
    if posicion:
        sub += f" · {posicion}"
    d.text((352, 610), sub, font=_fit(d, sub, 620, 32), fill=MUTED, anchor="lm")
    st = u.get("stats") or {}
    if st.get("rating"):
        txt = f"★ {st['rating']:.1f}  " + ("Mejor nota de su equipo" if st.get("mejor_equipo") else "Calificación")
        _pill(d, 352, 650, txt, _f(FB, 28), (14, 17, 22), GOLD)
    cajas = [(f"{u.get('minutes', 0)}'", "Minutos", False), (str(u.get("goals", 0)), "Goles", (u.get("goals") or 0) > 0),
             (str(u.get("assists", 0)), "Asist.", (u.get("assists") or 0) > 0)]
    if st.get("tiros") is not None:
        cajas.append((f"{st.get('tiros_puerta', 0)}/{st['tiros']}", "Tiros a puerta", False))
    elif st.get("pases_pct") is not None:
        cajas.append((f"{st['pases_pct']}%", "Pases", False))
    else:
        cajas.append(("—", "Tiros", False))
    for i, (v, l, dorado) in enumerate(cajas[:4]):
        x0 = 110 + i * 220
        d.rounded_rectangle([x0, 770, x0 + 200, 920], radius=24, fill=(34, 41, 52))
        d.text((x0 + 100, 830), v, font=_f(FB, 56), fill=GOLD if dorado else WHITE, anchor="mm")
        d.text((x0 + 100, 888), l, font=_f(FR, 24), fill=MUTED, anchor="mm")
    hechos = []
    for e in u.get("eventos") or []:
        if not e.get("mio"):
            continue
        t = e.get("tipo")
        if t in ("gol", "pen"):
            hechos.append(f"Gol al {e.get('min')}" + (" (pen.)" if t == "pen" else ""))
        elif t == "entra":
            hechos.append(f"Entró al {e.get('min')}")
        elif t == "sale":
            hechos.append(f"Salió al {e.get('min')}")
        elif t == "amarilla":
            hechos.append(f"Amarilla {e.get('min')}")
        elif t == "roja":
            hechos.append(f"Roja {e.get('min')}")
    linea = "  ·  ".join(hechos[:4])
    if linea:
        d.text((W / 2, 968), linea, font=_fit(d, linea, 900, 30), fill=WHITE, anchor="mm")
    d.text((W / 2, 1035), "LEGIÓN MX", font=_f(FB, 30), fill=(110, 120, 135), anchor="mm")
    return _guardar(B, f"{player['key']}_partido.jpg")

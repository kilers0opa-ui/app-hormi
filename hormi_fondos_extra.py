#!/usr/bin/env python3
"""
Fuentes extra de fondos de pantalla de Legión MX (se usan desde hormi_fondos.py):

  - Flickr (API oficial): solo fotos con licencia Creative Commons que permite el
    uso aquí: CC BY (4), CC BY-SA (5), CC0 (9) y Dominio público (10). Se descartan
    a propósito las NC (1,2,3) y ND (6). Necesita la variable FLICKR_API_KEY (secreto
    de GitHub); sin ella se omite sin error.
  - "Mis fondos": imágenes que el dueño de la app sube a mano a mis_fondos/<jugador>/
    (o mis_fondos/general/) y se agregan solas al catálogo.
"""
import hashlib
import io
import json
import os
import re
import time
import urllib.parse

import hormi_fondos as F
from hormi_fondos import OUT_DIR, RAW_BASE, norm, plain_name, http_get, CONTEXTO_FUTBOL

FLICKR_LICENCIAS = {"4": "CC BY 2.0", "5": "CC BY-SA 2.0", "9": "CC0", "10": "Dominio público"}
MIS_FONDOS_DIR = os.path.join(F.HERE, "mis_fondos")
_EXT = (".jpg", ".jpeg", ".png", ".webp")


# ------------------------------------------------------------------ Flickr
def _flickr(texto, extras_pp=60):
    key = os.environ.get("FLICKR_API_KEY", "").strip()
    if not key:
        return None
    params = {
        "method": "flickr.photos.search", "api_key": key, "format": "json", "nojsoncallback": "1",
        "text": texto, "license": ",".join(FLICKR_LICENCIAS), "media": "photos", "content_type": "1",
        "safe_search": "1", "sort": "relevance", "per_page": str(extras_pp),
        "extras": "license,owner_name,tags,description,url_k,url_h,url_l,url_o,o_dims",
    }
    url = "https://api.flickr.com/services/rest/?" + urllib.parse.urlencode(params)
    data = json.loads(http_get(url, timeout=30).decode("utf-8"))
    if data.get("stat") != "ok":
        raise RuntimeError(data.get("message") or "Flickr respondió error")
    return (data.get("photos") or {}).get("photo", [])


def _mejor_url(p):
    """(url, ancho, alto) de la versión más grande razonable (≥1400 px de lado largo)."""
    for suf in ("k", "h"):
        if p.get(f"url_{suf}"):
            return p[f"url_{suf}"], int(p.get(f"width_{suf}") or 0), int(p.get(f"height_{suf}") or 0)
    if p.get("url_o") and p.get("width_o"):
        w, h = int(p["width_o"]), int(p["height_o"])
        if max(w, h) <= 4000:
            return p["url_o"], w, h
    return None, 0, 0


def _texto(p):
    d = p.get("description")
    d = d.get("_content", "") if isinstance(d, dict) else (d or "")
    return norm(" ".join([p.get("title") or "", re.sub(r"<[^>]+>", " ", d), p.get("tags") or ""]))


def _guardar_flickr(p, url, idf, prefijo, recortar):
    from PIL import Image
    img = Image.open(io.BytesIO(http_get(url, timeout=60))).convert("RGB")
    if recortar:
        import hormi_fondos_general as G
        img = G._recortar_vertical(img)
    elif img.width > 1080:
        img = img.resize((1080, round(img.height * 1080 / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    data = buf.getvalue()
    fname = f"{prefijo}{idf}.jpg"
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, fname), "wb") as fh:
        fh.write(data)
    return f"{RAW_BASE}{fname}?v={hashlib.sha1(data).hexdigest()[:8]}", img.size


def _credito(p):
    lic = FLICKR_LICENCIAS.get(str(p.get("license")), "CC")
    return f"Foto: {p.get('ownername') or 'Autor desconocido'} · {lic} · Flickr"


def flickr_jugador(player, ya, maximo):
    """Fotos Flickr del jugador. `ya` = ids ya incluidos; `maximo` = cuántas puede sumar."""
    if maximo <= 0:
        return []
    nombre = plain_name(player)
    partes = norm(nombre).split()
    primero, apellido = partes[0], partes[-1]
    clubes = [norm(t.get("label") or "") for t in player.get("targets", []) if t["key"] != "seleccion"]
    clubes = [c for c in clubes if len(c) >= 4]
    fotos = _flickr(f'"{nombre}"')
    if fotos is None:
        print("  (Flickr: sin FLICKR_API_KEY, se omite)")
        return []
    items = []
    for p in sorted(fotos, key=lambda x: 0):  # ya viene por relevancia
        if len(items) >= maximo:
            break
        idf = f"flickr_{p['id']}"
        if idf in ya or str(p.get("license")) not in FLICKR_LICENCIAS:
            continue
        t = _texto(p)
        if primero not in t or apellido not in t:
            continue
        if not (re.search(CONTEXTO_FUTBOL, t) or any(c in t for c in clubes)):
            continue
        if not (re.search(r"\bmexic", t) or any(c in t for c in clubes)):
            continue
        if re.search(r"\b(colombia|colombian|ukrain|politic|diputad|senador|alcalde|cantante|actor|actriz|ai[- ]generated)", t):
            continue
        url, w, h = _mejor_url(p)
        if not url or min(w, h) < 800:
            continue
        try:
            nueva, (iw, ih) = _guardar_flickr(p, url, p["id"], "foto_flickr_", recortar=False)
        except Exception as e:
            print(f"  (Flickr: no se pudo bajar {p['id']}: {e})")
            continue
        items.append({
            "id": idf, "title": nombre, "url": nueva, "width": iw, "height": ih,
            "credit": _credito(p),
            "source_url": f"https://www.flickr.com/photos/{p.get('owner')}/{p['id']}",
        })
        time.sleep(1)
    return items


def flickr_seleccion(ya, maximo=4):
    """Fotos Flickr de la Selección Mexicana (partidos, afición), recortadas a vertical."""
    out = []
    for consulta in ("Mexico national football team", "Selección Mexicana fútbol"):
        fotos = _flickr(consulta)
        if fotos is None:
            return []
        for p in fotos:
            if len(out) >= maximo:
                return out
            idf = f"flickr_{p['id']}"
            if idf in ya or any(o["id"] == idf for o in out) or str(p.get("license")) not in FLICKR_LICENCIAS:
                continue
            t = _texto(p)
            if not re.search(r"\b(mexico|mexican|mexicana|mexicano)\b", t):
                continue
            if not re.search(r"\b(national team|seleccion|world cup|mundial|copa|gold cup|friendly|amistoso|concacaf|match|partido)\b", t):
                continue
            if re.search(r"\b(logo|map|mapa|women|womens|femenil|femenina|u-?\d\d|sub-?\d+|jersey|shirt|kit|camiseta|playera|blind|beach|futsal|ai[- ]generated)\b", t):
                continue
            url, w, h = _mejor_url(p)
            if not url or h < 1400 or w < 1000:
                continue
            try:
                nueva, _ = _guardar_flickr(p, url, p["id"], "foto_general_flickr_", recortar=True)
            except Exception as e:
                print(f"  (Flickr: no se pudo bajar {p['id']}: {e})")
                continue
            out.append({
                "id": idf, "title": "Selección Mexicana", "url": nueva, "width": F.W, "height": F.H,
                "credit": _credito(p), "team_key": "seleccion",
                "source_url": f"https://www.flickr.com/photos/{p.get('owner')}/{p['id']}",
            })
            time.sleep(1)
    return out


# ------------------------------------------------------------------ Mis fondos
def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", norm(s)).strip("-") or "fondo"


def mis_fondos(keys_jugadores):
    """Lee mis_fondos/<clave>/*.jpg|png|webp. Devuelve {clave: [items]} (clave 'general'
    incluida). Cada imagen se copia a fondos/ (lado largo ≤ 2340 px) para que la app la
    baje igual que las demás."""
    from PIL import Image
    res = {}
    if not os.path.isdir(MIS_FONDOS_DIR):
        return res
    for key in sorted(os.listdir(MIS_FONDOS_DIR)):
        carpeta = os.path.join(MIS_FONDOS_DIR, key)
        if not os.path.isdir(carpeta) or (key != "general" and key not in keys_jugadores):
            continue
        items = []
        n_foto = 0
        for fn in sorted(os.listdir(carpeta)):
            if not fn.lower().endswith(_EXT):
                continue
            stem = os.path.splitext(fn)[0]
            n_foto += 1
            # Nombres de cámara/descarga (hash largo o solo números) no sirven de título.
            if re.fullmatch(r"[0-9a-fA-F]{16,}|\d+|img[-_ ]?\d+|image\d*", stem, re.I):
                titulo = f"Mi foto {n_foto}"
            else:
                titulo = stem.replace("_", " ").replace("-", " ").strip().title()
            try:
                img = Image.open(os.path.join(carpeta, fn)).convert("RGB")
                if max(img.size) > 2340:
                    k = 2340 / max(img.size)
                    img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=90, optimize=True)
            except Exception as e:
                print(f"  (mis_fondos/{key}/{fn}: no se pudo leer: {e})")
                continue
            data = buf.getvalue()
            nombre = f"mio_{key}_{_slug(stem)}.jpg"
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(os.path.join(OUT_DIR, nombre), "wb") as f:
                f.write(data)
            items.append({
                "id": f"mio_{_slug(stem)}", "title": titulo,
                "url": f"{RAW_BASE}{nombre}?v={hashlib.sha1(data).hexdigest()[:8]}",
                "width": img.width, "height": img.height,
            })
        if items:
            res[key] = items
    return res

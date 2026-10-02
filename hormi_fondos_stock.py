#!/usr/bin/env python3
"""
Fondos genéricos (México en general, cancha, estadios de noche) desde
bancos de fotos gratuitos con API:

  - Pexels  (PEXELS_API_KEY)   licencia Pexels: uso libre, sin crédito obligatorio.
  - Pixabay (PIXABAY_API_KEY)  licencia Pixabay: uso libre, sin crédito obligatorio.

Las imágenes se bajan al repo (Pixabay NO permite enlazarlas directo de forma permanente),
y la app muestra el crédito aunque no sea obligatorio. Son fotos genéricas: no sirven
para jugadores concretos. Sin llaves, se omite sin error y se conservan las categorías previas.
"""
import hashlib
import io
import json
import os
import time
import urllib.parse
import urllib.request

import hormi_fondos as F
from hormi_fondos import OUT_DIR, RAW_BASE, UA

# (id de categoría, título en la app, [(consulta en inglés/español, team_key o None)], máximo de fotos)
# team_key: si la consulta es del estadio de un club que sigue la app, la foto también sale
# en la sección de sus jugadores (campo "contexto").
CATEGORIAS = [
    ("stock_mexico", "México", [
        ("mexico flag", None), ("Mexico City skyline", None), ("Angel de la Independencia", None),
        ("Zocalo Mexico City", None), ("Guadalajara Mexico", None), ("Monterrey Mexico city", None),
        ("Chichen Itza", None), ("mexican fans football", None),
    ], 12),
    ("stock_cancha", "Cancha y balón", [("soccer ball on grass", None), ("football pitch aerial", None), ("soccer field lines", None)], 6),
    ("stock_noche", "Estadios de noche", [("football stadium night lights", None), ("stadium floodlights soccer", None)], 6),
]
POR_CONSULTA = 2


def _get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _pexels(consulta, key):  # noqa: E302
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
        {"query": consulta, "orientation": "portrait", "size": "large", "per_page": "30"})
    data = json.loads(_get(url, {"Authorization": key}))
    for p in data.get("photos", []):
        if (p.get("height") or 0) < 2000:
            continue
        # imgix de Pexels: recorta a 1080x2340 y comprime.
        src = (p["src"]["original"] + "?auto=compress&cs=tinysrgb&fit=crop&w=1080&h=2340")
        yield {"id": f"pexels_{p['id']}", "src": src, "credit": f"Foto: {p.get('photographer') or 'Autor'} · Pexels",
               "source_url": p.get("url")}


def _pixabay(consulta, key):
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode(
        {"key": key, "q": consulta, "image_type": "photo", "orientation": "vertical",
         "min_height": "1800", "safesearch": "true", "per_page": "30", "order": "popular"})
    data = json.loads(_get(url))
    for p in data.get("hits", []):
        if not p.get("largeImageURL"):
            continue
        yield {"id": f"pixabay_{p['id']}", "src": p["largeImageURL"],
               "credit": f"Foto: {p.get('user') or 'Autor'} · Pixabay", "source_url": p.get("pageURL")}


def stock_categorias(previas):
    """Devuelve la lista de categorías 'stock_*'. Si no hay ninguna llave o todo falla,
    devuelve las `previas` (para no borrar lo que ya estaba)."""
    from PIL import Image
    kp, kx = os.environ.get("PEXELS_API_KEY", "").strip(), os.environ.get("PIXABAY_API_KEY", "").strip()
    if not kp and not kx:
        print("  (stock: sin PEXELS_API_KEY ni PIXABAY_API_KEY, se omite)")
        return previas
    nuevas = []
    excluidas = F.load_excluidas()
    try:
        with open(os.path.join(F.HERE, "fondos_stock_aprobadas.json"), encoding="utf-8") as f:
            aprobadas = set(json.load(f).get("ids", []))
    except (OSError, ValueError):
        aprobadas = set()
    if not aprobadas:
        print("  (stock: no hay fotos aprobadas en fondos_stock_aprobadas.json, se omite)")
        return previas
    for cid, titulo, consultas, maximo in CATEGORIAS:
        items, vistos = [], set()
        for consulta, team_key in consultas:
            for fuente, key in (("pexels", kp), ("pixabay", kx)):
                if not key or len(items) >= maximo:
                    continue
                try:
                    candidatos = list(_pexels(consulta, key) if fuente == "pexels" else _pixabay(consulta, key))
                except Exception as e:
                    print(f"  ({fuente} '{consulta}' falló: {e})")
                    continue
                tomadas = 0
                for c in candidatos:
                    if len(items) >= maximo or tomadas >= 12 or c["id"] in vistos or c["id"] in excluidas or c["id"] not in aprobadas:
                        continue
                    try:
                        img = Image.open(io.BytesIO(_get(c["src"], timeout=60))).convert("RGB")
                        if img.height < img.width * 1.3 or img.height < 1200:
                            continue
                        if img.width > 1080:
                            img = img.resize((1080, round(img.height * 1080 / img.width)), Image.LANCZOS)
                        buf = io.BytesIO()
                        img.save(buf, "JPEG", quality=88, optimize=True)
                    except Exception as e:
                        print(f"  (no se pudo bajar {c['id']}: {e})")
                        continue
                    data = buf.getvalue()
                    fname = f"foto_general_stock_{c['id']}.jpg"
                    os.makedirs(OUT_DIR, exist_ok=True)
                    with open(os.path.join(OUT_DIR, fname), "wb") as f:
                        f.write(data)
                    vistos.add(c["id"])
                    item = {"id": c["id"], "title": (consulta if cid.startswith("stock_estadios") else titulo),
                            "url": f"{RAW_BASE}{fname}?v={hashlib.sha1(data).hexdigest()[:8]}",
                            "width": img.width, "height": img.height, "credit": c["credit"],
                            "source_url": c["source_url"], "query": consulta}
                    if team_key:
                        item["team_key"] = team_key
                    items.append(item)
                    tomadas += 1
                    time.sleep(0.5)
        print(f"  {titulo}: {len(items)} foto(s)")
        if items:
            nuevas.append({"id": cid, "title": titulo, "items": items})
    return nuevas or previas

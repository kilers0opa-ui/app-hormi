#!/usr/bin/env python3
"""
Legión MX · Fase 6 — Videos y noticias
=======================================
Dos archivos, dos escritores (así los workflows jamás chocan al hacer push):

  videos.json      (corrida normal, videos.yml, 2 veces al día)
                   Noticias por jugador: titular, medio, fecha y enlace (SIN el
                   texto de la nota) desde el RSS de Google Noticias. Solo
                   titulares que nombran al jugador (apellido + nombre o apodo),
                   sin repetidos, pocas (max_noticias, 2 por defecto). "videos"
                   queda vacío: la búsqueda abierta de YouTube se RETIRÓ (traía
                   videos de aficionados). La app abre las noticias en un Custom Tab.

  videos_gol.json  (modo --eventos, video-gol.yml, cada 5 min solo si hay algo)
                   Los videos llegan únicamente por EVENTOS que anota el motor
                   de alertas (hormi_alertas.py) en videos_pendientes.json:
                     · goles      → video del gol
                     · finales    → resumen del partido + noticia principal
                   Todos se reproducen DENTRO de la app con el reproductor
                   oficial de YouTube (IFrame): la búsqueda filtra
                   videoEmbeddable=true.

Cuota de YouTube Data API (10,000 unidades/día gratis): search.list = 100.
La corrida normal ya NO gasta cuota. Los eventos comparten un tope de 90
búsquedas/día (= 9,000 unidades de las 10,000 gratis); de esas, los
resúmenes pueden usar máximo 35 para no quitarle cuota a los goles.

Modo eventos (--eventos, alias --gol)
-------------------------------------
GOL  · búsqueda con el NOMBRE COMPLETO ("Armando González"); título/descripción
       deben contener nombre Y apellido; solo videos publicados desde el gol
       (menos un margen); 2 intentos (~15 y ~30 min después), luego se omite;
       al aparecer manda la notificación "Ya está el video del gol de X".
FINAL· un evento por partido (aunque jueguen varios del roster).
       - Noticia principal de cada jugador: se refresca a los +30 y +60 min
         (RSS, gratis) y queda en partidos[fixture].noticias[jugador].
       - Resumen del partido: 2 intentos, +30 y +60 min; el título debe decir
         "resumen/highlights/goles…" y nombrar a los DOS equipos (tolera
         variantes tipo Olympiacos/Olympiakos; ver "equipos" en el archivo de
         fuentes); solo si algún jugador del roster tuvo minutos.
       - Al aparecer el resumen manda "Ya está disponible el resumen del partido de {jugador}",
         una por cada jugador del roster que tuvo minutos (resumen_notificar; False lo apaga).
Si un intento falla o no hay nada, se omite: nunca se sigue gastando cuota.

Uso:
  export YOUTUBE_API_KEY="tu_api_key"     # Google Cloud → YouTube Data API v3
  python hormi_videos.py                          # noticias de todos los jugadores
  python hormi_videos.py --jugador armando         # solo uno
  python hormi_videos.py --eventos                 # atiende goles y finales pendientes
  python hormi_videos.py --eventos --sin-notificar # igual, sin mandar push

Pensado para correr desde GitHub Actions (ver .github/workflows/).

Requisitos: Python 3.9+, sin librerías externas. YOUTUBE_API_KEY solo se usa
en --eventos (las noticias no necesitan llave).
"""

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

CONFIG_FILE = Path("hormi_config.json")
SOURCES_FILE = Path("hormi_videos_fuentes.json")
VIDEOS_FILE = Path("videos.json")
PENDING_FILE = Path("videos_pendientes.json")   # lo escribe hormi_alertas.py
GOLES_FILE = Path("videos_gol.json")             # lo escribe SOLO el modo --gol

YT_BASE = "https://www.googleapis.com/youtube/v3"
NEWS_RSS = "https://news.google.com/rss/search"

LOCAL_TZ = timezone(timedelta(hours=-6))

DEFAULTS = {
    "max_noticias": 3,       # tope de noticias guardadas por jugador (pocas y buenas, no un feed)
    "dias_retencion": 30,    # lo guardado más viejo que esto se descarta. Las políticas de la API de YouTube exigen borrar o refrescar sus datos a los 30 días: NO subir de 30
    # --- modo --eventos: goles
    "gol_intentos_min": [15, 30],     # minutos después del gol en que se busca (2 intentos, ni uno más)
    "gol_margen_min": 10,             # el clip puede haberse publicado hasta N min antes de que se detectara el gol
    "gol_min_entre_intentos": 10,     # si el worker se atrasa, separa los intentos al menos N min
    "gol_max_busquedas_dia": 90,      # tope de búsquedas de YouTube al día (goles + resúmenes): 90 x 100 = 9,000 de las 10,000 unidades gratis (deja ~1,000 para videos.list)
    "gol_caducidad_horas": 3,         # un gol/partido pendiente más viejo que esto se omite
    "gol_retencion_horas": 48,
    "gol_reverificar_min": 30,        # cada cuánto se vuelve a revisar que el video de un gol siga disponible
    "gol_reverificar_horas": 24,      # ... durante cuántas horas después del gol (1 unidad por cada 50 videos)
    # Videos quitados a mano (id de YouTube): nunca se usan y, si ya estaban puestos, se quitan y se busca otro.
    "videos_descartados": [],        # cuánto se conserva un gol/partido en videos_gol.json
    # --- modo --eventos: final de partido (resumen + noticia principal)
    "resumen_intentos_min": [30, 60, 120, 180, 300],   # minutos después del final en que se busca el resumen (5 intentos; los canales oficiales a veces lo suben 2-3 h después) y se refresca la noticia
    "resumen_caducidad_horas": 6,         # un resumen pendiente más viejo que esto se omite (los goles usan gol_caducidad_horas)
    "resumen_margen_min": 10,             # el resumen puede haberse publicado hasta N min antes de que se detectara el final
    "resumen_max_busquedas_dia": 50,      # de las 90 búsquedas del día, máximo 35 pueden ser de resúmenes: los goles siempre conservan cuota
    "resumen_solo_con_minutos": True,     # solo se busca resumen si algún jugador del roster tuvo minutos
    # Calidad del resumen: solo se acepta el de un canal confiable (estos nombres, o el canal oficial de alguno de
    # los dos equipos). Los videos de canales raros (voz de IA, "reacciones", narraciones de relleno) se descartan.
    # Se puede ajustar en hormi_videos_fuentes.json → config.
    "canales_confiables": ["tudn", "espn", "fox sports", "foxsports", "claro sports", "clarosports", "tv azteca",
                           "azteca deportes", "azteca 7", "televisa", "tyc sports", "dsports", "directv sports",
                           "sky sports", "dazn", "tnt sports", "cbs sports", "canal 13", "tvn", "chilevision",
                           "sport tv", "movistar", "fifa", "conmebol", "concacaf", "uefa", "mediotiempo",
                           "record", "marca", "superdeporte", "futbol total",
                           # Estados Unidos (alternativas cuando el de México está bloqueado allá)
                           "telemundo", "univision", "bein sports", "nbc sports", "paramount", "golazo"],
    "canales_bloqueados": ["spectaglobeworld"],
    "titulos_descartados": ["gameplay", "efootball", "fifa 2", "fc 26", "fc26", "simulacion", "simulation",
                            "reaccion", "reacciona", " en vivo ", " en directo ", " live ", "prediccion", " previa ",
                            "analisis", " ia ", "inteligencia artificial", "narracion", "narrado", "de infarto"],
    "resumen_notificar": True,
    # Canales que se revisan DIRECTO (sus últimas subidas, 1 unidad cada uno) en cada pasada mientras haya un resumen
    # pendiente. El buscador de YouTube tarda horas en indexar videos nuevos: ESPN MX subió el de AZ-Hapoel 47 min
    # después del final y la búsqueda no lo vio sino hasta 5 h después. Mirando sus subidas se ve al instante.
    "canales_resumen_ids": {
        "ESPN MX": "UC4vmPjKs8tJM0HMd9JmgZ-g",
        "Claro Sports": "UCqfCJBfrFSO4tZM1LNZTBFQ",
        "TNT Sports México": "UCPZOI6QrL73FQXoZEqgvOFA",
        "FOX Sports MX": "UCUf4RLsG_TsjinvtL2jT-VA",
        "TUDN México": "UCTIyEyDNHPrwVFPhpi5dm0A",
        "ESPN Deportes": "UC08mnbiC4FykqpHqbEWgFcg",
        "LIGA BBVA MX": "UCq8BPLXtFeiSFOvmJrknWGg",
        "Chivas": "UCbtodmCOE5CDdVvVvUzwQfQ",
    },
    "canales_resumen_desde_min": 20,   # desde cuántos minutos después del final se revisan esos canales
    # País donde tiene que poderse ver el video (goles y resúmenes): se descartan los bloqueados ahí por
    # el canal (p. ej. Fox Deportes bloquea muchos clips en México), los no incrustables y los +18.
    "region_visible": "MX",
    # Países extra (afición fuera de México): si el video elegido está bloqueado en alguno, se busca una alternativa
    # que SÍ se vea ahí y la app la muestra a quien esté en ese país. Las alternativas pueden ser de canales no
    # oficiales, pero con filtro estricto: título detallado (gol: nombre + "gol" + rival; resumen: los dos equipos +
    # "resumen/highlights"), duración razonable y sin títulos basura. Máximo 1 búsqueda extra por gol/partido.
    "regiones_extra": ["US"],
    "alt_gol_duracion_seg": [10, 360],
    "alt_resumen_duracion_seg": [60, 1500],
}

QUOTA_SEARCH = 100
QUOTA_LIST = 1

_quota_used = 0
_youtube_disabled = False   # se activa si la cuota se agota, para no seguir pegándole a la API


def log(msg):
    print(f"[{datetime.now(LOCAL_TZ):%H:%M:%S}] {msg}")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize(text):
    """Minúsculas y sin acentos, para comparar 'González' con 'gonzalez'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "LegionMX-backend/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------- YouTube --

def yt_api(endpoint, params, cost):
    """Llama a la YouTube Data API. Devuelve el JSON o None si falla.
    Si la cuota diaria se agotó, desactiva YouTube para el resto de la corrida."""
    global _quota_used, _youtube_disabled
    if _youtube_disabled:
        return None
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        log("     ⚠️  falta YOUTUBE_API_KEY — se omiten los videos")
        _youtube_disabled = True
        return None
    url = f"{YT_BASE}/{endpoint}?{urllib.parse.urlencode({**params, 'key': api_key})}"
    try:
        data = json.loads(http_get(url).decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = ""
        try:
            body = err.read().decode("utf-8", "replace")
        except Exception:
            pass
        if err.code == 403 and "quota" in body.lower():
            log("     ⚠️  cuota diaria de YouTube agotada — se omiten los videos restantes")
            _youtube_disabled = True
        else:
            log(f"     ⚠️  HTTP {err.code} en YouTube {endpoint}: {body[:200]}")
        return None
    except Exception as err:
        log(f"     ⚠️  fallo de red en YouTube {endpoint}: {err}")
        return None
    _quota_used += cost
    return data


def thumb_of(snippet):
    thumbs = snippet.get("thumbnails") or {}
    for size in ("high", "medium", "default"):
        if size in thumbs and thumbs[size].get("url"):
            return thumbs[size]["url"]
    return None


def video_entry(video_id, snippet, embeddable):
    return {
        "id": video_id,
        "title": snippet.get("title"),
        "channel": snippet.get("channelTitle"),
        "published_at": snippet.get("publishedAt"),
        "thumbnail": thumb_of(snippet),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "embeddable": embeddable,   # True = se puede reproducir dentro de la app; None = sin verificar
    }


def name_tokens(full):
    return [t for t in re.split(r"\s+", normalize(full)) if t]


def mentions_player(entry, tokens):
    """El clip debe nombrar al jugador con nombre Y apellido (todas las
    palabras de su nombre completo) en título o descripción. La búsqueda de
    YouTube es difusa, y un apellido solo ('Mora', 'González') deja pasar
    videos de otras personas."""
    haystack = normalize(f"{entry.get('title') or ''} {entry.get('_description') or ''}")
    return all(re.search(rf"\b{re.escape(t)}\b", haystack) for t in tokens)


def search_videos(query, days=None, published_after=None, max_results=25, region="MX"):
    """published_after (datetime UTC) tiene prioridad sobre days. region: país para el que YouTube filtra (solo
    devuelve lo que se puede ver ahí)."""
    if published_after is None:
        published_after = datetime.now(timezone.utc) - timedelta(days=days)
    data = yt_api("search", {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": "date",
        "maxResults": max_results,
        "videoEmbeddable": "true",   # la clave: solo clips que sí se pueden reproducir dentro de la app
        "regionCode": region,
        "relevanceLanguage": "es",
        "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, QUOTA_SEARCH)
    if not data:
        return None
    found = []
    for item in data.get("items") or []:
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        entry = video_entry(video_id, snippet, True)
        entry["_description"] = snippet.get("description")
        found.append(entry)
    return found


def channel_uploads(channel_id, max_items=15):
    """Últimos videos subidos por un canal. La lista de 'uploads' de cualquier
    canal es la playlist cuyo id es el del canal cambiando el prefijo UC → UU
    (cuesta 1 unidad, contra 100 de una búsqueda)."""
    if not (channel_id or "").startswith("UC"):
        log(f"     ⚠️  channel_id inválido: {channel_id!r} (debe empezar con 'UC')")
        return []
    data = yt_api("playlistItems", {
        "part": "snippet",
        "playlistId": "UU" + channel_id[2:],
        "maxResults": max_items,
    }, QUOTA_LIST)
    if not data:
        return []
    found = []
    for item in data.get("items") or []:
        snippet = item.get("snippet") or {}
        video_id = (snippet.get("resourceId") or {}).get("videoId")
        if not video_id:
            continue
        entry = video_entry(video_id, snippet, None)
        entry["_description"] = snippet.get("description")
        found.append(entry)
    return found


def _iso_dur(d):
    """'PT1M35S' → 95 segundos (None si no se puede leer)."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m or not d:
        return None
    dd, h, mi, se = (int(x or 0) for x in m.groups())
    return ((dd * 24 + h) * 60 + mi) * 60 + se


def _bloqueado(rr, region):
    return region in (rr.get("blocked") or []) or ("allowed" in rr and region not in (rr.get("allowed") or []))


def filter_visibles(entries, region, extra=()):
    """Quita los videos que NO se pueden ver en `region` (bloqueo por país del canal), los que no se
    pueden incrustar y los de restricción de edad. Una llamada a videos.list (1 unidad por hasta 50 ids).
    Si la consulta falla, no se descarta nada (mejor un video posible que ninguno).
    A cada video que queda le anota "bloqueado_en" (de los países de `extra` donde NO se ve) y "_dur" (segundos)."""
    if not entries or not region:
        return entries
    data = yt_api("videos", {
        "part": "contentDetails,status",
        "id": ",".join(e["id"] for e in entries[:50]),
    }, QUOTA_LIST)
    if not data:
        return entries
    info = {it.get("id"): it for it in data.get("items") or []}
    ok = []
    for e in entries:
        it = info.get(e["id"])
        if it is None:
            log(f"     🚫 {e.get('title')!r}: ya no existe o es privado")
            continue
        rr = (it.get("contentDetails") or {}).get("regionRestriction") or {}
        bloqueado = _bloqueado(rr, region)
        stt = it.get("status") or {}
        no_embed = stt.get("embeddable") is False
        # Reclamo de derechos (UEFA, LaLiga...), borrado o ya no público: YouTube lo bloquea en todos lados.
        rechazado = stt.get("uploadStatus") in ("rejected", "deleted", "failed") or stt.get("privacyStatus") not in (None, "public")
        edad = ((it.get("contentDetails") or {}).get("contentRating") or {}).get("ytRating") == "ytAgeRestricted"
        if bloqueado or no_embed or edad or rechazado:
            motivo = ("bloqueado en " + region if bloqueado else "no se puede incrustar" if no_embed
                      else "restricción de edad" if edad else f"bloqueado ({stt.get('rejectionReason') or stt.get('uploadStatus') or stt.get('privacyStatus')})")
            log(f"     🚫 {e.get('title')!r} ({e.get('channel')}): {motivo}")
            continue
        e["bloqueado_en"] = [r for r in extra if r != region and _bloqueado(rr, r)]
        e["_dur"] = _iso_dur((it.get("contentDetails") or {}).get("duration"))
        ok.append(e)
    return ok


# ------------------------------------------------- Alternativas por país --

def _dur_ok(e, rango):
    d = e.get("_dur")
    return d is None or rango[0] <= d <= rango[1]


def alt_gol_ok(e, tokens, rival_pref, settings, oficial):
    """Alternativa del video del gol para otro país. De canal oficial: basta con los filtros normales. De otro
    canal: el TÍTULO debe traer el nombre completo, decir gol y (si se conoce) nombrar al rival, p. ej.
    "Gol de Armando González vs AEK"; duración de clip y sin títulos basura."""
    title = normalize(e.get("title") or "")
    if resumen_descartado(e, settings) or NO_GOL_RE.search(title):
        return False
    if oficial:
        return True
    return (all(re.search(rf"\b{re.escape(t)}\b", title) for t in tokens)
            and bool(GOAL_RE.search(title))
            and (not rival_pref or mentions_team(title, rival_pref))
            and _dur_ok(e, settings["alt_gol_duracion_seg"]))


def alt_resumen_ok(e, home_pref, away_pref, settings):
    """Alternativa del resumen para otro país. De canal confiable u oficial de un equipo: filtros normales. De otro
    canal: el TÍTULO debe nombrar a los dos equipos y decir resumen/highlights ("Resumen Olympiacos vs AEK"),
    con duración de resumen y sin títulos basura."""
    if resumen_descartado(e, settings):
        return False
    if resumen_confiable(e, home_pref, away_pref, settings):
        return True
    title = normalize(e.get("title") or "")
    return (mentions_team(title, home_pref) and mentions_team(title, away_pref)
            and any(w in title for w in RESUMEN_WORDS)
            and _dur_ok(e, settings["alt_resumen_duracion_seg"]))


def poner_alternativas(st, pool, es_valido, query, since, settings, cuota, usados=()):
    """Si el video elegido (st["video"]) está bloqueado en algún país de regiones_extra, busca una alternativa
    visible ahí: primero entre los mismos candidatos (sin gastar cuota) y, si no hay, con UNA búsqueda en ese
    país (solo una vez por gol/partido y si hay cuota). Guarda st["alternativos"][país]. Devuelve True si cambió."""
    v = st.get("video") or {}
    alts = st.setdefault("alternativos", {})
    cambio = False
    for r in v.get("bloqueado_en") or []:
        if r in alts:
            continue
        evitar = {v.get("id")} | set(usados) | set(st.get("descartados") or []) | set(settings.get("videos_descartados") or [])
        cands = [e for e in pool if e["id"] not in evitar and r not in (e.get("bloqueado_en") or []) and es_valido(e)]
        if not cands and not st.get(f"alt_buscado_{r}") and not _youtube_disabled \
                and cuota.get("busquedas", 0) < settings["gol_max_busquedas_dia"]:
            st[f"alt_buscado_{r}"] = True
            cambio = True
            log(f"     🌎 video bloqueado en {r}: búsqueda de alternativa · {query!r}")
            found = search_videos(query, published_after=since, max_results=50, region=r)
            if found is not None:
                cuota["busquedas"] = cuota.get("busquedas", 0) + 1
                since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
                found = [e for e in found if e["id"] not in evitar and (e.get("published_at") or "") >= since_iso]
                cands = [e for e in filter_visibles(found, r) if es_valido(e)]
        if cands:
            alt = strip_private([sorted(cands, key=lambda e: e.get("published_at") or "")[0]])[0]
            alt.pop("bloqueado_en", None)
            alt["embeddable"] = True
            alts[r] = alt
            cambio = True
            log(f"     🌎 alternativa para {r}: {alt['title']!r} ({alt['channel']})")
        else:
            log(f"     🌎 sin alternativa visible en {r} todavía")
    if not alts:
        st.pop("alternativos", None)
    return cambio


def fill_embeddable(entries):
    """Los videos que vienen de canales no dicen si se pueden incrustar:
    una llamada a videos.list (1 unidad por hasta 50 ids) lo resuelve."""
    pending = [e for e in entries if e.get("embeddable") is None]
    if not pending:
        return
    data = yt_api("videos", {
        "part": "status",
        "id": ",".join(e["id"] for e in pending[:50]),
    }, QUOTA_LIST)
    if not data:
        return
    status = {i["id"]: (i.get("status") or {}).get("embeddable") for i in data.get("items") or []}
    for e in pending:
        if e["id"] in status:
            e["embeddable"] = bool(status[e["id"]])


# ------------------------------------------------------------------- News --

# Ediciones de Google Noticias. "mx" y "es" en español; la "local" es la del país del club (se traduce).
EDICIONES = {
    "mx": {"hl": "es-419", "gl": "MX", "ceid": "MX:es-419", "pais": "México", "idioma": "es"},
    "es": {"hl": "es", "gl": "ES", "ceid": "ES:es", "pais": "España", "idioma": "es"},
}
# Prensa local por club (clave del equipo en hormi_config.json). Clubes de México o España no llevan edición local
# (ya la cubren "mx" / "es"). Un club nuevo sin entrada aquí simplemente no tiene noticia local.
EDICION_LOCAL = {
    "olympiacos": {"hl": "el", "gl": "GR", "ceid": "GR:el", "pais": "Grecia", "idioma": "el"},
    "az_alkmaar": {"hl": "nl", "gl": "NL", "ceid": "NL:nl", "pais": "Países Bajos", "idioma": "nl"},
    "porto": {"hl": "pt-PT", "gl": "PT", "ceid": "PT:pt-150", "pais": "Portugal", "idioma": "pt"},
    "copenhague": {"hl": "da", "gl": "DK", "ceid": "DK:da", "pais": "Dinamarca", "idioma": "da"},
    "lokomotiv": {"hl": "ru", "gl": "RU", "ceid": "RU:ru", "pais": "Rusia", "idioma": "ru"},
    "dinamo": {"hl": "ru", "gl": "RU", "ceid": "RU:ru", "pais": "Rusia", "idioma": "ru"},
    "wolves": {"hl": "en-GB", "gl": "GB", "ceid": "GB:en", "pais": "Inglaterra", "idioma": "en"},
    "genoa": {"hl": "it", "gl": "IT", "ceid": "IT:it", "pais": "Italia", "idioma": "it"},
    "al_qadsiah": {"hl": "ar", "gl": "SA", "ceid": "SA:ar", "pais": "Arabia Saudita", "idioma": "ar"},
}
TRADUCCIONES_FILE = Path("traducciones_cache.json")
AZURE_URL = "https://api.cognitive.microsofttranslator.com/translate"


def traducir(textos, origen):
    """Titulares al español con Azure Translator (plan gratis: 2 millones de caracteres al mes). Guarda cada
    traducción en traducciones_cache.json para no volver a pagar el mismo titular. Sin clave → None."""
    key = os.environ.get("AZURE_TRANSLATOR_KEY")
    if not key or not textos:
        return None
    cache = load_json(TRADUCCIONES_FILE, {})
    faltan = [t for t in dict.fromkeys(textos) if t not in cache]
    if faltan:
        headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json; charset=UTF-8"}
        region = os.environ.get("AZURE_TRANSLATOR_REGION")
        if region:
            headers["Ocp-Apim-Subscription-Region"] = region
        url = f"{AZURE_URL}?api-version=3.0&to=es&from={urllib.parse.quote(origen)}"
        body = json.dumps([{"Text": t} for t in faltan[:25]]).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                res = json.loads(resp.read().decode("utf-8"))
            for t, r in zip(faltan[:25], res):
                cache[t] = ((r.get("translations") or [{}])[0]).get("text") or t
            log(f"     🌐 {len(faltan[:25])} titular(es) traducidos ({sum(len(t) for t in faltan[:25])} caracteres)")
        except Exception as err:
            log(f"     ⚠️  no se pudo traducir con Azure: {err}")
        if len(cache) > 3000:   # tope: se quedan las más recientes
            cache = dict(list(cache.items())[-2000:])
        save_json(TRADUCCIONES_FILE, cache)
    return {t: cache[t] for t in textos if t in cache}


def fetch_news(query, limit=15, edicion=None):
    """Titulares desde el RSS de Google Noticias. Solo se guarda título, medio,
    fecha y enlace — nunca el cuerpo de la nota."""
    ed = edicion or EDICIONES["mx"]
    params = {"q": query, "hl": ed["hl"], "gl": ed["gl"], "ceid": ed["ceid"]}
    url = f"{NEWS_RSS}?{urllib.parse.urlencode(params)}"
    try:
        root = ET.fromstring(http_get(url))
    except Exception as err:
        log(f"     ⚠️  fallo leyendo noticias ({query!r}): {err}")
        return None
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title or not link:
            continue
        source_node = node.find("source")
        source = (source_node.text or "").strip() if source_node is not None else None
        # Google Noticias termina el título con " - Nombre del medio": se quita para no repetirlo
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].rstrip()
        published = None
        pub = node.findtext("pubDate")
        if pub:
            try:
                published = parsedate_to_datetime(pub).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                published = None
        items.append({"title": title, "source": source, "url": link, "published_at": published})
        if len(items) >= limit:
            break
    return items


# ------------------------------------------------------------------ Merge --

def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def merge(old, new, key, cap, retention_days):
    """Nuevo + viejo, sin duplicados (gana la versión nueva), más reciente
    primero, sin lo más viejo que retention_days, con tope."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    by_key = {}
    for entry in list(old or []) + list(new or []):   # el nuevo va después: sobreescribe al viejo
        by_key[entry[key]] = {**by_key.get(entry[key], {}), **entry}
    kept = []
    for entry in by_key.values():
        ts = parse_ts(entry.get("published_at"))
        if ts is not None and ts < cutoff:
            continue
        kept.append(entry)
    kept.sort(key=lambda e: e.get("published_at") or "", reverse=True)
    return kept[:cap]


# --------------------------------------------------------- Curar noticias --

ALIAS_STOPWORDS = {"el", "la", "los", "las", "de", "del"}


def title_mentions_player(title, player):
    """El TITULAR debe nombrar al jugador: apellido + (nombre de pila o apodo).
    Google Noticias devuelve notas donde el jugador apenas aparece en el
    cuerpo, y esas no son 'sus' noticias. Apodo que sea igual al nombre o al
    apellido no cuenta como apodo (así 'Mora' solo no basta)."""
    text = normalize(title)

    def has(tok):
        return re.search(rf"\b{re.escape(tok)}\b", text) is not None

    first = normalize(player.get("search_first_name") or "")
    last = normalize(player.get("search_last_name") or "")
    if not last or not has(last):
        return False
    if first and has(first):
        return True
    alias = [t for t in name_tokens(player.get("apodo") or "")
             if t not in (first, last) and t not in ALIAS_STOPWORDS and len(t) >= 4]
    return bool(alias) and all(has(t) for t in alias)


def mentions_surname(title, player):
    """Para la prensa local ya traducida: basta el apellido (la traducción a veces cambia el nombre de pila)."""
    last = normalize(player.get("search_last_name") or "")
    return bool(last) and re.search(rf"\b{re.escape(last)}\b", normalize(title)) is not None


def curate_news(news, player, cap):
    """Solo titulares que nombran al jugador, sin títulos repetidos (el mismo
    titular llega a veces con dos enlaces), más reciente primero, con tope."""
    ordered = sorted(news, key=lambda n: n.get("published_at") or "", reverse=True)
    seen, out = set(), []
    for n in ordered:
        if not title_mentions_player(n.get("title") or "", player):
            continue
        key = re.sub(r"[^a-z0-9 ]+", "", normalize(n.get("title") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out[:cap]


# ------------------------------------------------------------------- Main --

def player_names(player):
    first = (player.get("search_first_name") or "").strip()
    last = (player.get("search_last_name") or "").strip()
    return first.title(), last.title()


def full_name(player):
    """Nombre completo con acentos y sin el apodo entre comillas:
    'Armando "La Hormiga" González' -> 'Armando González'."""
    name = re.sub(r'\s*"[^"]*"\s*', " ", player.get("name") or "")
    name = " ".join(name.split())
    if name:
        return name
    first, last = player_names(player)
    return f"{first} {last}".strip()


def club_label(player):
    for t in player.get("targets", []):
        if t.get("kind") == "club":
            return t.get("label")
    return None


def news_query(player, overrides):
    full = full_name(player)
    club = club_label(player)
    return overrides.get("query_noticias") or " ".join(x for x in (f'"{full}"', club) if x)


def club_key(player):
    for t in player.get("targets", []):
        if t.get("kind") == "club":
            return t.get("key")
    return None


def build_for_player(player, overrides):
    """Solo noticias, de 3 ediciones: México, España y la prensa local del club (traducida). Los videos ya NO
    se buscan de forma abierta (traía videos de aficionados): llegan únicamente por eventos (process_events)."""
    query = news_query(player, overrides)
    out = {}
    for ed_key, ed in EDICIONES.items():
        log(f"   📰 noticias {ed['pais']} · {query!r}")
        items = fetch_news(query, edicion=ed)
        if items is not None:
            out[ed_key] = [dict(n, edicion=ed_key) for n in items]
    local = overrides.get("edicion_local") or EDICION_LOCAL.get(club_key(player) or "")
    if local and os.environ.get("AZURE_TRANSLATOR_KEY"):
        # Sin comillas: la prensa local suele escribir el nombre en su alfabeto/idioma y Google empata mejor así.
        q_local = overrides.get("query_local") or " ".join(x for x in (full_name(player), club_label(player)) if x)
        log(f"   📰 noticias {local['pais']} (se traducen) · {q_local!r}")
        items = fetch_news(q_local, edicion=local) or []
        items = [n for n in items[:10]]
        tr = traducir([n["title"] for n in items], local["idioma"]) or {}
        out["local"] = [dict(n, title=tr[n["title"]], title_original=n["title"], edicion="local",
                             pais=local["pais"], traducida=True) for n in items if n["title"] in tr]
    elif local:
        log("     (sin AZURE_TRANSLATOR_KEY: se omite la prensa local)")
    return {"noticias_ed": out}


RELATO_RE = re.compile(r"(disparo por|tiro atajado|se estrella en el poste|anota para|^cerca\b|^gol\b.*anota|tarjeta amarilla para|sustitucion|cambio en )")


def curate_por_edicion(news, player, cap):
    """Una noticia por edición (México, España, prensa local), la más reciente que de verdad hable del
    jugador; si una edición no trae nada, se completa con otra de las demás. Sin titulares repetidos."""
    def valida(n):
        t = n.get("title") or ""
        # Fuera: "titulares" que solo son el nombre, y los automáticos de sitios de minuto a minuto.
        if len(re.findall(r"\w+", t)) < 5 or RELATO_RE.search(normalize(t)):
            return False
        if n.get("edicion") == "local":
            return mentions_surname(n.get("title") or "", player) or title_mentions_player(n.get("title_original") or "", player)
        return title_mentions_player(n.get("title") or "", player)
    ordered = sorted([n for n in news if valida(n)], key=lambda n: n.get("published_at") or "", reverse=True)
    seen, elegidas = set(), []
    def clave(n):
        return re.sub(r"[^a-z0-9 ]+", "", normalize(n.get("title") or ""))
    for ed in ("mx", "es", "local"):
        n = next((x for x in ordered if (x.get("edicion") or "mx") == ed and clave(x) not in seen), None)
        if n:
            seen.add(clave(n))
            elegidas.append(n)
    for n in ordered:
        if len(elegidas) >= cap:
            break
        if clave(n) not in seen:
            seen.add(clave(n))
            elegidas.append(n)
    return elegidas[:cap]


def strip_private(entries):
    return [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]

# ------------------------------------------------------------- Modo gol --

GOAL_WORDS = ("gol", "goal", "golazo", "anota", "marca")
# Palabra completa (no "goleador"/"goleo"): el título tiene que hablar de UN gol.
GOAL_RE = re.compile(r"\b(gol|goles|golazo|golaco|golao|goal|goals|anota|anoto|marca|marco|scores?|scored)\b")
# Títulos de listas/noticias que mencionan al jugador pero no son el gol (p. ej. "Quiñones, 28 del Balón de Oro").
NO_GOL_RE = re.compile(r"balon de oro|ballon d|ranking|top \d|tabla de goleo|goleador|pichichi|record|fichaje|rumor|entrevista|lesion")


def es_clip_del_gol(entry, rival_pref=None):
    """El título debe decir que es un gol (o nombrar al rival del partido) y no ser una lista/noticia."""
    title = normalize(entry.get("title") or "")
    if NO_GOL_RE.search(title):
        return False
    return bool(GOAL_RE.search(title)) or bool(rival_pref and mentions_team(title, rival_pref))


def rival_del_gol(key, player_key):
    """Prefijos del rival del partido del gol (llave 'jugador:equipo:fixture:goal:...'), desde player_stats.json."""
    try:
        fid = int(key.split(":")[2])
        stats = json.loads(Path("player_stats.json").read_text(encoding="utf-8"))
        u = ((stats.get("players") or {}).get(player_key) or {}).get("ultimo_partido") or {}
        if u.get("fixture_id") != fid:
            return None
        nombres = {u.get("home") or "", u.get("away") or ""}
        club = normalize(u.get("team_label") or "")
        rival = [n for n in nombres if n and not mentions_team(normalize(n), team_prefixes(club))] if club else []
        return team_prefixes(rival[0], siglas=True) if len(rival) == 1 else None
    except Exception:
        return None


def sin_video_en(video, st):
    """Países donde NO hay forma de ver este video (bloqueado y sin alternativa): la app de quien esté ahí no
    muestra el aviso "Ya está el video..." (las versiones viejas de la app lo ignoran y lo muestran)."""
    alts = (st or {}).get("alternativos") or {}
    return ",".join(r for r in (video.get("bloqueado_en") or []) if r not in alts)


def send_video_push(config, player, video, team_key, st=None):
    """Segunda notificación: 'Ya está el video del gol de X'. Va por ntfy y por
    FCM (como tipo 'info', así la app NO vuelve a abrir la pantalla de
    ¡GOOOOL!). Cualquier fallo aquí se registra y se ignora."""
    try:
        import hormi_alertas as ha
    except Exception as err:
        log(f"     ⚠️  no se pudo cargar hormi_alertas para notificar: {err}")
        return
    apodo = player.get("apodo") or player["name"]
    title = f"🎬 Ya está el video del gol de {apodo}"
    parts = [video.get("title"), video.get("channel")]
    message = " · ".join(x for x in parts if x) + " — míralo en la app"
    try:
        if config.get("ntfy_topic"):
            ha.Notifier(config["ntfy_topic"]).send(title, message, 3)
        fcm = ha.FcmSender(config.get("fcm_project_id"))
        fcm.send("video", title, message, player["key"], team_key, extra={"sin_video_en": sin_video_en(video, st)})
    except Exception as err:
        log(f"     ⚠️  fallo enviando la notificación del video: {err}")


def _due(n, base, last_iso, offsets, settings, now, caducidad_h=None):
    """None = todavía no toca · 'normal' | 'final' = toca ·
    'agotado' | 'caducado' = ya no se hace más. n = intentos hechos; base =
    momento del evento; offsets = minutos después del evento de cada intento."""
    if n >= len(offsets):
        return "agotado"
    if base is None:
        return "agotado"
    if now - base > timedelta(hours=caducidad_h or settings["gol_caducidad_horas"]):
        return "caducado"
    if now < base + timedelta(minutes=offsets[n]):
        return None
    last = parse_ts(last_iso)
    if last and now - last < timedelta(minutes=settings["gol_min_entre_intentos"]):
        return None
    # Si el worker llegó tarde y ya pasó también la hora del último intento,
    # se hace UNA sola pasada y esa es la definitiva (nunca más de las previstas).
    late = n == 0 and now >= base + timedelta(minutes=offsets[-1])
    return "final" if (late or n == len(offsets) - 1) else "normal"


def next_attempt(st, settings, now):
    """Para un gol: ver _due."""
    return _due(st["intentos"], parse_ts(st.get("goal_seen_at")), st.get("ultimo_intento"),
                settings["gol_intentos_min"], settings, now)


def pick_goal_video(candidates, settings=None, team_pref=None):
    """El clip más probable: primero los de canales oficiales (televisoras/ligas de canales_confiables o el
    canal del propio equipo) — los de fans a veces no dejan reproducir dentro de la app aunque YouTube diga que
    sí —; luego los que dicen 'gol' en el título; y entre ellos, el que se publicó antes."""
    def oficial(e):
        ch = normalize(e.get("channel") or "") + " "
        return (any(t in ch for t in (settings or {}).get("canales_confiables") or [])
                or bool(team_pref and canal_del_equipo(ch, team_pref)))
    def score(e):
        title = normalize(e.get("title") or "")
        has_word = any(w in title for w in GOAL_WORDS)
        return (not oficial(e), not has_word, e.get("published_at") or "")
    return sorted(candidates, key=score)[0]


# ------------------------------------------------------- Final de partido --

TEAM_STOP = {"fc", "cf", "sc", "ac", "as", "cd", "ud", "club", "de", "del", "la", "el", "the", "and"}
TEAM_GENERIC = {"real", "united", "city", "sporting", "deportivo", "athletic"}
RESUMEN_WORDS = ("resumen", "highlights", "goles", "goals", "compacto", "melhores", "resume", "extended",
                 "samenvatting", "hoogtepunten")


def team_prefixes(name, aliases=(), siglas=False):
    """Prefijos (5 letras) de las palabras distintivas de un equipo, para
    aguantar variantes: 'Olympiakos Piraeus' / 'Olympiacos' -> 'olymp'.
    siglas=True agrega también las palabras de 3 letras ('AEK Athens' -> 'aek', 'PSV Eindhoven' -> 'psv'),
    que es como suelen escribirlas en los títulos."""
    def words(text):
        return [w for w in re.findall(r"[a-z0-9]+", normalize(text)) if w not in TEAM_STOP]
    base = [w for w in words(name) if len(w) >= 4]
    strong = [w for w in base if w not in TEAM_GENERIC] or base or [w for w in words(name) if len(w) >= 2]
    extra = [w for a in aliases for w in words(a) if len(w) >= 4]
    cortas = [w for t in (name, *aliases) for w in words(t) if len(w) == 3 and w.isalpha()] if siglas else []
    return {w[:5] for w in strong + extra + cortas}


def mentions_team(text_norm, prefixes):
    words = re.findall(r"[a-z0-9]+", text_norm)
    return any(w.startswith(p) for p in prefixes for w in words)


def is_match_summary(entry, home_pref, away_pref):
    """El clip debe nombrar a los DOS equipos y decir que es un resumen."""
    text = normalize(f"{entry.get('title') or ''} {entry.get('_description') or ''}")
    title = normalize(entry.get("title") or "")
    return (mentions_team(text, home_pref) and mentions_team(text, away_pref)
            and any(w in title for w in RESUMEN_WORDS))


CANAL_GENERICO = {"fc", "cf", "sc", "ac", "club", "clube", "oficial", "official", "tv", "channel", "canal", "youtube", "de", "the",
                  "balompie", "futbol", "football", "calcio", "real", "united", "city", "deportivo", "athletic"}


def canal_del_equipo(ch_norm, prefixes):
    """El canal ES el del equipo ("Real Betis Balompié", "Chivas", "Olympiacos FC"), no uno que solo lo
    contiene ("Barcelonabloodman", "Chivas EMILIANO Aficionados"): todas sus palabras (quitando FC, TV,
    oficial...) deben ser palabras del equipo, y debe haber al menos una."""
    words = [w for w in re.findall(r"[a-z0-9]+", ch_norm) if w not in CANAL_GENERICO and len(w) > 2]
    if not words:
        return False
    return all(any(w.startswith(p) and len(w) <= len(p) + 6 for p in prefixes) for w in words)


def resumen_confiable(entry, home_pref, away_pref, settings):
    """True si el canal es de los confiables o el oficial de alguno de los dos equipos."""
    ch = normalize(entry.get("channel") or "") + " "
    return (any(t in ch for t in settings.get("canales_confiables") or [])
            or canal_del_equipo(ch, home_pref) or canal_del_equipo(ch, away_pref))


MARCADOR_RE = re.compile(r"\b\d{1,2}\s*[-–]\s*\d{1,2}\b")


def resumen_de_canal_oficial(entry, home_pref, away_pref):
    """En las subidas de los canales oficiales (liga, club, TUDN...), un video con los dos equipos y el
    marcador en el título es el resumen aunque no diga "resumen" (la Liga MX titula "CHIVAS 1-1 TIGRES | ...")."""
    title = normalize(entry.get("title") or "")
    return mentions_team(title, home_pref) and mentions_team(title, away_pref) and bool(MARCADOR_RE.search(title))


def resumen_descartado(entry, settings):
    """Canal bloqueado o título que delata que NO es el resumen oficial (voz de IA, reacción, partida de videojuego...)."""
    ch = normalize(entry.get("channel") or "")
    title = " " + normalize(entry.get("title") or "") + " "
    return (any(b in ch for b in settings.get("canales_bloqueados") or [])
            or any(w in title for w in settings.get("titulos_descartados") or []))


def send_summary_push(config, players, video, st):
    """Notificación 'Ya está disponible el resumen del partido de {jugador}'.
    Una por cada jugador del roster que tuvo minutos en ese partido (cada una
    llega por el canal de ese jugador, así la app respeta lo que el usuario
    tenga activado). Solo se llama cuando el resumen SÍ se encontró. Va por
    ntfy y por FCM como tipo 'info'. Cualquier fallo se registra y se ignora."""
    try:
        import hormi_alertas as ha
    except Exception as err:
        log(f"     ⚠️  no se pudo cargar hormi_alertas para notificar: {err}")
        return
    detalle = " · ".join(x for x in (st.get("score"), video.get("title"), video.get("channel")) if x)
    message = f"{detalle} — míralo en la app"
    try:
        notifier = ha.Notifier(config["ntfy_topic"]) if config.get("ntfy_topic") else None
        fcm = ha.FcmSender(config.get("fcm_project_id"))
    except Exception as err:
        log(f"     ⚠️  fallo preparando la notificación del resumen: {err}")
        return
    seleccion = []   # jugadores de la Selección: UN solo aviso por partido (no uno por jugador)
    for pk, j in st["jugadores"].items():
        player = players.get(pk)
        if player is None or (j.get("minutes") or 0) <= 0:
            continue
        title = f"🎬 Ya está disponible el resumen del partido de {full_name(player)}"
        if j.get("team_key") == "seleccion":
            seleccion.append({"key": pk, "name": ha.display_name(player), "status": "jugo",
                              "summary": {"minutes": j.get("minutes")}, "title": title, "body": message})
            continue
        try:
            if notifier:
                notifier.send(title, message, 3)
            fcm.send("video", title, message, pk, j.get("team_key"), extra={"sin_video_en": sin_video_en(video, st)})
        except Exception as err:
            log(f"     ⚠️  fallo enviando la notificación del resumen ({pk}): {err}")
    if seleccion:
        # La app arma el texto según tus favoritos: con uno solo, su aviso individual; con varios, este.
        try:
            tg = next(t for pk in (x["key"] for x in seleccion) for t in players[pk]["targets"] if t["key"] == "seleccion")
            g = {"emoji": tg["emoji"], "label": tg["label"], "match": f"{st.get('home')} vs {st.get('away')}",
                 "league": st.get("league"), "score": st.get("score")}
            title_all = f"🎬 Ya está disponible el resumen del partido de la {tg['label']}"
            if notifier:
                notifier.send(title_all, message, 3)
            fcm.send("video", title_all, message, None, "seleccion", [x["key"] for x in seleccion],
                     extra={"grupo": ha._payload_grupo(g, seleccion, title_all, message),
                            "sin_video_en": sin_video_en(video, st)})
        except Exception as err:
            log(f"     ⚠️  fallo enviando la notificación unificada del resumen: {err}")


_SUBIDAS = None


def _subidas_canales(settings):
    """Últimas subidas de los canales de canales_resumen_ids (una sola vez por corrida, 1 unidad por canal)."""
    global _SUBIDAS
    if _SUBIDAS is None:
        _SUBIDAS = []
        for nombre, cid in (settings.get("canales_resumen_ids") or {}).items():
            try:
                _SUBIDAS += channel_uploads(cid, max_items=50)
            except Exception as err:
                log(f"     ⚠️  no se pudieron leer las subidas de {nombre}: {err}")
        log(f"📺 {len(_SUBIDAS)} subidas recientes de {len(settings.get('canales_resumen_ids') or {})} canales confiables")
    return _SUBIDAS


def _finales_step(out, config, settings, sources, notify, now):
    """Final de partido: (1) refresca la noticia principal de cada jugador
    (RSS, gratis) a los +30 y +60 min; (2) busca el resumen en YouTube a los
    +30, +60, +120, +180 y +300 min (resumen_intentos_min). Devuelve True si cambió algo."""
    partidos = out.setdefault("partidos", {})
    cuota = out["cuota"]
    pending = (load_json(PENDING_FILE, {}).get("finales")) or []
    players = {p["key"]: p for p in config["players"]}
    per_player = sources.get("jugadores") or {}
    aliases = sources.get("equipos") or {}
    offsets = settings["resumen_intentos_min"]
    changed = False

    # 1) partidos nuevos (y jugadores nuevos de un partido que ya conocíamos)
    for ev in pending:
        fid = ev.get("fixture_id")
        js = {j["player_key"]: j for j in ev.get("jugadores") or [] if j.get("player_key") in players}
        if fid is None or not js:
            continue
        st = partidos.get(str(fid))
        if st is None:
            st = partidos[str(fid)] = {
                "fixture_id": fid, "home": ev.get("home"), "away": ev.get("away"),
                "league": ev.get("league"), "score": ev.get("score"),
                "final_seen_at": ev.get("final_seen_at"), "jugadores": {},
                "status": "pendiente", "intentos": 0, "fallos": 0,
                "ultimo_intento": None, "video": None,
                "noticias_pasadas": 0, "ultima_noticia": None, "noticias": {},
            }
            changed = True
        for pk, j in js.items():
            if pk not in st["jugadores"]:
                st["jugadores"][pk] = {k: j.get(k) for k in ("team_key", "minutes", "goals", "assists")}
                changed = True

    # 2) poda
    cutoff = now - timedelta(hours=settings["gol_retencion_horas"])
    for k in [k for k, st in partidos.items()
              if (parse_ts(st.get("final_seen_at")) or now) < cutoff]:
        del partidos[k]
        changed = True

    for fid, st in partidos.items():
        base = parse_ts(st.get("final_seen_at"))
        match = f"{st.get('home')} vs {st.get('away')}"

        # 3) noticia principal de cada jugador (no gasta cuota)
        if st["noticias_pasadas"] < len(offsets):
            when = _due(st["noticias_pasadas"], base, st.get("ultima_noticia"), offsets, settings, now)
            if when in ("agotado", "caducado"):
                st["noticias_pasadas"] = len(offsets)
                changed = True
            elif when is not None:
                log(f"📰 final {match} · noticias de {len(st['jugadores'])} jugador(es)")
                for pk in st["jugadores"]:
                    player = players.get(pk)
                    if player is None:
                        continue
                    fresh = fetch_news(news_query(player, per_player.get(pk) or {}))
                    if fresh is not None:
                        st["noticias"][pk] = [dict(n, edicion="mx") for n in curate_news(fresh, player, settings["max_noticias"])]
                st["noticias_pasadas"] = len(offsets) if when == "final" else st["noticias_pasadas"] + 1
                st["ultima_noticia"] = now.isoformat()
                changed = True

        # 4a) resumen en las subidas de los canales confiables (barato y sin el retraso del buscador)
        if (st["status"] == "pendiente" and base is not None
                and now >= base + timedelta(minutes=settings.get("canales_resumen_desde_min", 20))
                and now - base <= timedelta(hours=settings.get("resumen_caducidad_horas") or settings["gol_caducidad_horas"])
                and any((j.get("minutes") or 0) > 0 for j in st["jugadores"].values())):
            home_pref = team_prefixes(st.get("home") or "", aliases.get(st.get("home")) or [])
            away_pref = team_prefixes(st.get("away") or "", aliases.get(st.get("away")) or [])
            since_iso = (base - timedelta(minutes=settings["resumen_margen_min"])).strftime("%Y-%m-%dT%H:%M:%SZ")
            used = {s2["video"]["id"] for k2, s2 in partidos.items() if k2 != fid and s2.get("video")}
            cands = [e for e in _subidas_canales(settings)
                     if (is_match_summary(e, home_pref, away_pref) or resumen_de_canal_oficial(e, home_pref, away_pref))
                     and (e.get("published_at") or "") >= since_iso
                     and e["id"] not in used and not resumen_descartado(e, settings)]
            vis = filter_visibles(cands, settings.get("region_visible"), settings.get("regiones_extra") or ()) if cands else []
            if vis:
                best = sorted(vis, key=lambda e: e.get("published_at") or "")[0]
                video = strip_private([best])[0]
                video["embeddable"] = True   # filter_visibles ya lo verificó
                st["status"], st["video"] = "resuelto", video
                st.pop("motivo", None)
                changed = True
                log(f"✅ resumen {match} en subidas de {video['channel']}: {video['title']!r}")
                base_since = base - timedelta(minutes=settings["resumen_margen_min"])
                hp_s = team_prefixes(st.get("home") or "", aliases.get(st.get("home")) or [], siglas=True)
                ap_s = team_prefixes(st.get("away") or "", aliases.get(st.get("away")) or [], siglas=True)
                poner_alternativas(st, vis, lambda e, hp=hp_s, ap=ap_s: alt_resumen_ok(e, hp, ap, settings),
                                   f"{st.get('home')} vs {st.get('away')}", base_since, settings, cuota, used)
                if notify and settings.get("resumen_notificar") and not st.get("silencioso"):
                    send_summary_push(config, players, video, st)
                continue

        # 4b) resumen del partido (búsqueda en YouTube, respaldo)
        if st["status"] != "pendiente":
            continue
        when = _due(st["intentos"], base, st.get("ultimo_intento"), offsets, settings, now,
                    settings.get("resumen_caducidad_horas"))
        if when is None:
            continue
        if when in ("agotado", "caducado"):
            st["status"], st["motivo"] = "omitido", when
            changed = True
            continue
        if settings["resumen_solo_con_minutos"] and not any(
                (j.get("minutes") or 0) > 0 for j in st["jugadores"].values()):
            st["status"], st["motivo"] = "omitido", "ningún jugador tuvo minutos"
            log(f"   ⏭️  {match}: nadie del roster jugó — no se busca resumen")
            changed = True
            continue
        if (cuota["busquedas"] >= settings["gol_max_busquedas_dia"]
                or cuota.get("busquedas_resumen", 0) >= settings["resumen_max_busquedas_dia"]):
            st["status"], st["motivo"] = "omitido", "tope diario de búsquedas"
            log(f"   ⛔ tope diario de búsquedas alcanzado — se omite el resumen de {match}")
            changed = True
            continue

        home_pref = team_prefixes(st.get("home") or "", aliases.get(st.get("home")) or [])
        away_pref = team_prefixes(st.get("away") or "", aliases.get(st.get("away")) or [])
        since = base - timedelta(minutes=settings["resumen_margen_min"])
        # Cada intento usa una búsqueda distinta (los videos de relleno suelen salir primero con "order=date")
        # Búsquedas cortas: con muchas palabras YouTube devuelve casi nada (AZ-Hapoel "resumen goles" dio 1 resultado
        # y "AZ Alkmaar vs Hapoel Beer Sheva" sí trajo el de Claro Sports). El filtro de resumen lo hacemos nosotros.
        h, a = st.get("home"), st.get("away")
        consultas = [f"{h} vs {a}", f"{h} {a} resumen", f"{h} vs {a}", f"{h} {a} highlights", f"{h} vs {a}"]
        query = consultas[min(st["intentos"], len(consultas) - 1)]
        log(f"🔎 resumen {match} · intento {st['intentos'] + 1} · {query!r} · desde {since:%H:%M} UTC")
        found = search_videos(query, published_after=since, max_results=50)   # 50 cuesta lo mismo que 15 (100 u.); con 15 el oficial quedaba tapado
        changed = True
        if found is None:
            st["fallos"] += 1
            if _youtube_disabled or st["fallos"] >= 3:
                st["status"], st["motivo"] = "omitido", "sin acceso a YouTube"
            continue
        cuota["busquedas"] += 1
        cuota["busquedas_resumen"] = cuota.get("busquedas_resumen", 0) + 1
        st["intentos"] += 1
        st["ultimo_intento"] = now.isoformat()

        used = {s2["video"]["id"] for k2, s2 in partidos.items() if k2 != fid and s2.get("video")}
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        candidates = [e for e in found
                      if is_match_summary(e, home_pref, away_pref)
                      and (e.get("published_at") or "") >= since_iso
                      and e["id"] not in used
                      and not resumen_descartado(e, settings)]
        # Solo canales confiables (el primero en publicar). Si hay válidos pero de canales desconocidos,
        # no se acepta ninguno: se espera al siguiente intento (los oficiales tardan más en subirlo).
        visibles = filter_visibles(candidates, settings.get("region_visible"), settings.get("regiones_extra") or ())
        confiables = [e for e in visibles if resumen_confiable(e, home_pref, away_pref, settings)]
        log(f"     {len(found)} resultados · {len(candidates)} válidos · {len(confiables)} de canal confiable")
        if confiables:
            best = sorted(confiables, key=lambda e: e.get("published_at") or "")[0]
            video = strip_private([best])[0]
            st["status"], st["video"] = "resuelto", video
            log(f"     ✅ {video['title']!r} ({video['channel']})")
            hp_s = team_prefixes(h or "", aliases.get(h) or [], siglas=True)
            ap_s = team_prefixes(a or "", aliases.get(a) or [], siglas=True)
            poner_alternativas(st, visibles, lambda e, hp=hp_s, ap=ap_s: alt_resumen_ok(e, hp, ap, settings),
                               f"{h} vs {a}", since, settings, cuota, used)
            if notify and settings.get("resumen_notificar") and not st.get("silencioso"):
                send_summary_push(config, players, video, st)
        elif when == "final":
            st["status"], st["motivo"] = "omitido", "sin resumen tras los intentos"
            log("     ⏭️  sin resumen tras el último intento — se omite")
    return changed


# ----------------------------------------------------------- Modo eventos --

def _reverificar_goles(state, settings, now):
    manual = set(settings.get("videos_descartados") or [])
    revisar = []
    for key, st in state.items():
        v = st.get("video")
        if st.get("status") != "resuelto" or not v:
            continue
        goal_at = parse_ts(st.get("goal_seen_at")) or now
        last = parse_ts(st.get("verificado"))
        if v["id"] in manual:
            revisar.append((key, st, True))
        elif now - goal_at <= timedelta(hours=settings["gol_reverificar_horas"]) and (
                not last or now - last >= timedelta(minutes=settings["gol_reverificar_min"])):
            revisar.append((key, st, False))
    if not revisar:
        return False
    a_checar = [dict(st["video"]) for _, st, forzado in revisar if not forzado]
    vis = filter_visibles(a_checar, settings.get("region_visible"), settings.get("regiones_extra") or ()) if a_checar else []
    vivos = {e["id"] for e in vis}
    if a_checar and not vivos and _youtube_disabled:
        return False   # sin acceso a YouTube: no se quita nada a ciegas
    # Si el canal cambió en qué países se ve, se actualiza (la app decide con eso si muestra la alternativa).
    bloq = {e["id"]: e.get("bloqueado_en") or [] for e in vis}
    for _, st, _f in revisar:
        if st["video"]["id"] in bloq:
            st["video"]["bloqueado_en"] = bloq[st["video"]["id"]]
    # Las alternativas por país también se revisan (una llamada por país); la que se cayó se quita.
    for r in settings.get("regiones_extra") or ():
        alts = [(st, st["alternativos"][r]) for _, st, _f in revisar if r in (st.get("alternativos") or {})]
        if alts and not _youtube_disabled:
            ok = {e["id"] for e in filter_visibles([dict(a) for _, a in alts], r)}
            for st, a in alts:
                if a["id"] not in ok and not _youtube_disabled:
                    log(f"🧹 alternativa {r} ya no disponible: {a.get('title')!r} — se quita")
                    st["alternativos"].pop(r, None)
    for key, st, forzado in revisar:
        v = st["video"]
        st["verificado"] = now.isoformat()
        if not forzado and v["id"] in vivos:
            continue
        log(f"🧹 video del gol {key} ya no disponible: {v.get('title')!r} ({v.get('channel')}) — se quita y se busca otro")
        st.setdefault("descartados", []).append(v["id"])
        st["video"] = None
        st["status"] = "pendiente"
        st.pop("alternativos", None)   # eran alternativas del video que se cayó
        for r in settings.get("regiones_extra") or ():
            st.pop(f"alt_buscado_{r}", None)
        # Una búsqueda más (la última): next_attempt la da por "final".
        st["intentos"] = max(0, len(settings["gol_intentos_min"]) - 1)
        st["ultimo_intento"] = None
        st["silencioso"] = True   # el reemplazo no vuelve a mandar notificación
    return True


def _goals_step(out, config, settings, sources, notify, now):
    state = out.setdefault("goles", {})
    cuota = out["cuota"]
    pending = (load_json(PENDING_FILE, {"goles": []}).get("goles")) or []
    players = {p["key"]: p for p in config["players"]}
    per_player = sources.get("jugadores") or {}
    changed = False

    # 1) goles nuevos que anotó el motor de alertas
    for g in pending:
        key = g.get("key")
        if key and key not in state and g.get("player_key") in players:
            state[key] = {
                "player_key": g["player_key"], "team_key": g.get("team_key"),
                "goal_seen_at": g.get("goal_seen_at"), "status": "pendiente",
                "intentos": 0, "fallos": 0, "ultimo_intento": None, "video": None,
            }
            changed = True

    # 2) poda de goles viejos
    cutoff = now - timedelta(hours=settings["gol_retencion_horas"])
    for key in [k for k, st in state.items()
                if (parse_ts(st.get("goal_seen_at")) or now) < cutoff]:
        del state[key]
        changed = True

    # 2.5) ¿siguen disponibles los videos ya puestos? Un video puede caerse DESPUÉS de publicarse (reclamo de
    #      derechos de la UEFA/liga, el canal lo borra o lo hace privado). Si se cayó: se quita, se anota como
    #      descartado y se hace una búsqueda más (si el gol todavía está dentro de su caducidad).
    changed = _reverificar_goles(state, settings, now) or changed

    # 3) un intento por gol pendiente al que ya le toca
    for key, st in state.items():
        if st["status"] != "pendiente":
            continue
        player = players.get(st["player_key"])
        if player is None:
            continue
        when = next_attempt(st, settings, now)
        if when is None:
            continue
        if when in ("agotado", "caducado"):
            st["status"], st["motivo"] = "omitido", when
            changed = True
            continue
        if cuota["busquedas"] >= settings["gol_max_busquedas_dia"]:
            st["status"], st["motivo"] = "omitido", "tope diario de búsquedas"
            log(f"   ⛔ tope diario de búsquedas alcanzado — se omite {key}")
            changed = True
            continue

        full = full_name(player)
        tokens = name_tokens(full)
        goal_at = parse_ts(st["goal_seen_at"])
        since = goal_at - timedelta(minutes=settings["gol_margen_min"])
        query = (per_player.get(player["key"]) or {}).get("query_video_gol") or f'"{full}" gol'
        log(f"🔎 gol de {full} · intento {st['intentos'] + 1} · {query!r} · desde {since:%H:%M} UTC")
        found = search_videos(query, published_after=since, max_results=50)   # 50 cuesta lo mismo que 15 (100 u.); con 15 el oficial quedaba tapado
        changed = True
        if found is None:
            st["fallos"] += 1
            if _youtube_disabled or st["fallos"] >= 3:
                st["status"], st["motivo"] = "omitido", "sin acceso a YouTube"
            continue
        cuota["busquedas"] += 1
        st["intentos"] += 1
        st["ultimo_intento"] = now.isoformat()

        used = {s2["video"]["id"] for k2, s2 in state.items()
                if k2 != key and s2["player_key"] == st["player_key"]
                and s2.get("video")}
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        candidates = [e for e in found
                      if mentions_player(e, tokens)
                      and (e.get("published_at") or "") >= since_iso
                      and e["id"] not in used
                      and e["id"] not in set(st.get("descartados") or []) | set(settings.get("videos_descartados") or [])]
        rival_pref = rival_del_gol(key, st["player_key"])
        sin_gol = [e for e in candidates if not es_clip_del_gol(e, rival_pref)]
        for e in sin_gol:
            log(f"     ✗ no es el gol: {e.get('title')!r}")
        candidates = [e for e in candidates if es_clip_del_gol(e, rival_pref)]
        candidates = filter_visibles(candidates, settings.get("region_visible"), settings.get("regiones_extra") or ())
        log(f"     {len(found)} resultados · {len(candidates)} válidos y visibles en {settings.get('region_visible')}")
        if candidates:
            tgt = next((t for t in player.get("targets") or [] if t.get("key") == st.get("team_key")), {})
            club_pref = team_prefixes(tgt.get("label") or "")
            video = strip_private([pick_goal_video(candidates, settings, club_pref)])[0]
            st["status"], st["video"] = "resuelto", video
            log(f"     ✅ {video['title']!r} ({video['channel']})")
            def _oficial(e, cp=club_pref):
                ch = normalize(e.get("channel") or "") + " "
                return any(t in ch for t in settings.get("canales_confiables") or []) or bool(cp and canal_del_equipo(ch, cp))
            poner_alternativas(st, candidates,
                               lambda e, tk=tokens, rp=rival_pref: mentions_player(e, tk) and alt_gol_ok(e, tk, rp, settings, _oficial(e)),
                               f"{full} gol", since, settings, cuota, used)
            if notify and not st.get("silencioso"):
                send_video_push(config, player, video, st.get("team_key"), st)
        elif when == "final":
            st["status"], st["motivo"] = "omitido", "sin video tras los intentos"
            log("     ⏭️  sin video tras el último intento — se omite")
    return changed


def process_events(config, settings, sources, notify=True):
    """Modo --eventos (alias --gol): atiende lo que anotó el motor de alertas
    en videos_pendientes.json — goles (video del gol) y finales de partido
    (resumen + noticia principal) — y lo escribe en videos_gol.json. Los goles
    van primero. Ambos comparten el tope diario de búsquedas."""
    out = load_json(GOLES_FILE, {"updated_at": None, "cuota": {}, "goles": {}, "partidos": {}})
    cuota = out.setdefault("cuota", {})
    now = datetime.now(timezone.utc)
    today = f"{datetime.now(LOCAL_TZ):%Y-%m-%d}"
    if cuota.get("fecha") != today:
        cuota["fecha"], cuota["busquedas"], cuota["busquedas_resumen"] = today, 0, 0

    changed = _goals_step(out, config, settings, sources, notify, now)
    try:
        changed = _finales_step(out, config, settings, sources, notify, now) or changed
    except Exception as err:   # un fallo en finales jamás debe perder lo ya resuelto de los goles
        log(f"⚠️  error atendiendo finales de partido: {err}")

    if changed:
        out["updated_at"] = datetime.now(LOCAL_TZ).isoformat()
        save_json(GOLES_FILE, out)
        log(f"✅ {GOLES_FILE} actualizado · cuota de YouTube gastada: {_quota_used} unidades")
    else:
        log("Nada que atender.")


def main():
    parser = argparse.ArgumentParser(description="Fase 6 · Videos y noticias")
    parser.add_argument("--jugador", help="Limita a un jugador por su 'key'")
    parser.add_argument("--eventos", "--gol", dest="eventos", action="store_true",
                        help="Atiende goles y finales de partido pendientes (videos_pendientes.json) y escribe videos_gol.json")
    parser.add_argument("--sin-notificar", action="store_true", help="Con --eventos: no manda notificaciones")
    args = parser.parse_args()

    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit(f"Falta {CONFIG_FILE}.")
    sources = load_json(SOURCES_FILE, {})
    settings = {**DEFAULTS, **(sources.get("config") or {})}
    per_player = sources.get("jugadores") or {}

    if args.eventos:
        process_events(config, settings, sources, notify=not args.sin_notificar)
        return

    players = config["players"]
    if args.jugador:
        players = [p for p in players if p["key"] == args.jugador]
        if not players:
            sys.exit(f"No hay jugador con key='{args.jugador}' en {CONFIG_FILE}.")

    output = load_json(VIDEOS_FILE, {"updated_at": None, "players": {}})
    output.setdefault("players", {})

    for player in players:
        log(f"📺 {player['name']}")
        fresh = build_for_player(player, per_player.get(player["key"]) or {})
        entry = output["players"].setdefault(player["key"], {"videos": [], "noticias": []})
        entry["videos"] = []   # la búsqueda abierta se retiró: los videos llegan por eventos (videos_gol.json)
        merged_news = entry.get("noticias") or []
        for items in (fresh.get("noticias_ed") or {}).values():
            merged_news = merge(merged_news, items, "url", 10_000, settings["dias_retencion"])
        # se cura SIEMPRE (también lo ya guardado), así una corrida limpia lo que quedó de antes
        entry["noticias"] = curate_por_edicion(merged_news, player, settings["max_noticias"])
        log(f"     → {len(entry['noticias'])} noticias guardadas")

    output["updated_at"] = datetime.now(LOCAL_TZ).isoformat()
    save_json(VIDEOS_FILE, output)
    log(f"✅ listo · {VIDEOS_FILE} actualizado")


if __name__ == "__main__":
    main()

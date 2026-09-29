#!/usr/bin/env python3
"""
Legión MX · Fase 6 — Videos y noticias (videos.json)
=====================================================
Para cada jugador del roster arma dos listas que la app muestra en su
sección "Videos y noticias":

  * videos   — clips reproducibles DENTRO de la app con el reproductor
               oficial de YouTube (IFrame API). Se buscan con la YouTube
               Data API v3 filtrando videoEmbeddable=true, así ningún clip
               llega a la app con el embed desactivado por su dueño.
               Opcionalmente también se leen los "uploads" de canales
               oficiales (hormi_videos_fuentes.json → "canales").
  * noticias — titular, medio, fecha y enlace (SIN el texto de la nota)
               desde el RSS de Google Noticias. La app abre el enlace en
               un Custom Tab.

Qué escribe: videos.json — aparte de player_stats.json y
team_standings.json. Cada corrida MEZCLA lo nuevo con lo que ya había
(sin duplicados, más reciente primero, tope por lista, descartando lo
muy viejo): si una fuente falla o se agota la cuota, lo anterior se queda.

Cuota de YouTube Data API (10,000 unidades/día gratis):
  search.list = 100 · playlistItems.list = 1 · videos.list = 1
  Con 10 jugadores y 2 corridas/día: ~2,000 unidades. El script cuenta las
  unidades que gasta y las imprime al final.

Uso:
  export YOUTUBE_API_KEY="tu_api_key"     # Google Cloud → YouTube Data API v3
  python hormi_videos.py                          # todos los jugadores
  python hormi_videos.py --jugador armando         # solo uno
  python hormi_videos.py --solo noticias           # sin gastar cuota de YouTube
  python hormi_videos.py --solo videos

Pensado para correr desde GitHub Actions (ver .github/workflows/videos.yml).

Requisitos: Python 3.9+, sin librerías externas. Variable YOUTUBE_API_KEY
(solo para videos; las noticias no necesitan llave).
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

YT_BASE = "https://www.googleapis.com/youtube/v3"
NEWS_RSS = "https://news.google.com/rss/search"

LOCAL_TZ = timezone(timedelta(hours=-6))

DEFAULTS = {
    "max_videos": 10,        # tope de videos guardados por jugador
    "max_noticias": 10,      # tope de noticias guardadas por jugador
    "dias_busqueda": 30,     # la búsqueda de YouTube solo mira lo publicado en los últimos N días
    "dias_retencion": 60,    # lo guardado más viejo que esto se descarta
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


def mentions_player(entry, last_name):
    """El clip debe nombrar al jugador (apellido) en título o descripción — la
    búsqueda de YouTube es difusa y a veces devuelve videos que no tienen que ver."""
    haystack = normalize(f"{entry.get('title') or ''} {entry.get('_description') or ''}")
    return normalize(last_name) in haystack


def search_videos(query, days):
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = yt_api("search", {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": "date",
        "maxResults": 25,
        "videoEmbeddable": "true",   # la clave: solo clips que sí se pueden reproducir dentro de la app
        "regionCode": "MX",
        "relevanceLanguage": "es",
        "publishedAfter": published_after,
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

def fetch_news(query, limit=15):
    """Titulares desde el RSS de Google Noticias. Solo se guarda título, medio,
    fecha y enlace — nunca el cuerpo de la nota."""
    params = {"q": query, "hl": "es-419", "gl": "MX", "ceid": "MX:es-419"}
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
        by_key[entry[key]] = entry
    kept = []
    for entry in by_key.values():
        ts = parse_ts(entry.get("published_at"))
        if ts is not None and ts < cutoff:
            continue
        kept.append(entry)
    kept.sort(key=lambda e: e.get("published_at") or "", reverse=True)
    return kept[:cap]


# ------------------------------------------------------------------- Main --

def player_names(player):
    first = (player.get("search_first_name") or "").strip()
    last = (player.get("search_last_name") or "").strip()
    return first.title(), last.title()


def club_label(player):
    for t in player.get("targets", []):
        if t.get("kind") == "club":
            return t.get("label")
    return None


def build_for_player(player, overrides, settings, do_videos, do_news):
    first, last = player_names(player)
    club = club_label(player)
    full = f"{first} {last}".strip()
    result = {}

    if do_videos:
        query = overrides.get("query_video") or " ".join(x for x in (full, club) if x)
        log(f"   🎬 videos · búsqueda: {query!r}")
        found = search_videos(query, settings["dias_busqueda"])
        if found is None:
            result["videos"] = None
        else:
            found = [e for e in found if mentions_player(e, last)]
            for canal in overrides.get("canales") or []:
                log(f"   🎬 videos · canal {canal.get('label') or canal.get('channel_id')}")
                uploads = [e for e in channel_uploads(canal.get("channel_id")) if mentions_player(e, last)]
                fill_embeddable(uploads)
                # de canales solo entran los que sí se pueden incrustar
                found += [e for e in uploads if e.get("embeddable") is not False]
            result["videos"] = found

    if do_news:
        query = overrides.get("query_noticias") or " ".join(x for x in (f'"{full}"', club) if x)
        log(f"   📰 noticias · {query!r}")
        result["noticias"] = fetch_news(query)

    return result


def strip_private(entries):
    return [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]


def main():
    parser = argparse.ArgumentParser(description="Fase 6 · Videos y noticias")
    parser.add_argument("--jugador", help="Limita a un jugador por su 'key'")
    parser.add_argument("--solo", choices=["videos", "noticias"], help="Corre solo una de las dos fuentes")
    args = parser.parse_args()

    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit(f"Falta {CONFIG_FILE}.")
    sources = load_json(SOURCES_FILE, {})
    settings = {**DEFAULTS, **(sources.get("config") or {})}
    per_player = sources.get("jugadores") or {}

    players = config["players"]
    if args.jugador:
        players = [p for p in players if p["key"] == args.jugador]
        if not players:
            sys.exit(f"No hay jugador con key='{args.jugador}' en {CONFIG_FILE}.")

    do_videos = args.solo in (None, "videos")
    do_news = args.solo in (None, "noticias")

    output = load_json(VIDEOS_FILE, {"updated_at": None, "players": {}})
    output.setdefault("players", {})

    for player in players:
        log(f"📺 {player['name']}")
        fresh = build_for_player(player, per_player.get(player["key"]) or {}, settings, do_videos, do_news)
        entry = output["players"].setdefault(player["key"], {"videos": [], "noticias": []})
        if fresh.get("videos") is not None:
            entry["videos"] = strip_private(
                merge(entry.get("videos"), strip_private(fresh["videos"]), "id",
                      settings["max_videos"], settings["dias_retencion"]))
            log(f"     → {len(entry['videos'])} videos guardados")
        if fresh.get("noticias") is not None:
            entry["noticias"] = merge(entry.get("noticias"), fresh["noticias"], "url",
                                      settings["max_noticias"], settings["dias_retencion"])
            log(f"     → {len(entry['noticias'])} noticias guardadas")

    output["updated_at"] = datetime.now(LOCAL_TZ).isoformat()
    save_json(VIDEOS_FILE, output)
    log(f"✅ listo · {VIDEOS_FILE} actualizado · cuota de YouTube gastada: {_quota_used} unidades")


if __name__ == "__main__":
    main()

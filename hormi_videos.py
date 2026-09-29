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

Modo gol (--gol): el video del gol recién metido
------------------------------------------------
El motor de alertas (hormi_alertas.py) anota cada gol que alerta en
videos_pendientes.json. Con --gol, este script busca el clip de ESE gol:

  * Búsqueda con el NOMBRE COMPLETO del jugador ("Armando González"), y el
    título/descripción del resultado debe contener nombre Y apellido.
  * Filtrada por fecha: solo videos publicados desde el momento del gol
    (menos un pequeño margen por el retraso de detección) — así el clip es
    del gol de hoy, no de uno viejo.
  * Solo 2 intentos por gol: ~15 y ~30 min después. Si en el segundo no hay
    video, se omite (no se sigue gastando cuota).
  * Cuando aparece, manda una segunda notificación: "Ya está el video del
    gol de La Hormiga".
  * Tope diario de búsquedas de gol (gol_max_busquedas_dia) para que una
    tarde con muchos goles no se coma la cuota.

Escribe videos_gol.json (un solo escritor: este modo). videos.json lo
escribe únicamente la corrida normal; así los dos nunca chocan.

Uso:
  export YOUTUBE_API_KEY="tu_api_key"     # Google Cloud → YouTube Data API v3
  python hormi_videos.py                          # todos los jugadores
  python hormi_videos.py --jugador armando         # solo uno
  python hormi_videos.py --solo noticias           # sin gastar cuota de YouTube
  python hormi_videos.py --solo videos
  python hormi_videos.py --gol                     # atiende los goles pendientes
  python hormi_videos.py --gol --sin-notificar     # igual, sin mandar push

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
PENDING_FILE = Path("videos_pendientes.json")   # lo escribe hormi_alertas.py
GOLES_FILE = Path("videos_gol.json")             # lo escribe SOLO el modo --gol

YT_BASE = "https://www.googleapis.com/youtube/v3"
NEWS_RSS = "https://news.google.com/rss/search"

LOCAL_TZ = timezone(timedelta(hours=-6))

DEFAULTS = {
    "max_videos": 10,        # tope de videos guardados por jugador
    "max_noticias": 10,      # tope de noticias guardadas por jugador
    "dias_busqueda": 30,     # la búsqueda de YouTube solo mira lo publicado en los últimos N días
    "dias_retencion": 60,    # lo guardado más viejo que esto se descarta
    # --- modo --gol
    "gol_intentos_min": [15, 30],     # minutos después del gol en que se busca (2 intentos, ni uno más)
    "gol_margen_min": 10,             # el clip puede haberse publicado hasta N min antes de que se detectara el gol
    "gol_min_entre_intentos": 10,     # si el worker se atrasa, separa los intentos al menos N min
    "gol_max_busquedas_dia": 40,      # tope de búsquedas de gol al día (40 x 100 = 4,000 unidades)
    "gol_caducidad_horas": 3,         # un gol pendiente más viejo que esto se omite
    "gol_retencion_horas": 48,        # cuánto se conserva un gol en videos_gol.json
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


def search_videos(query, days=None, published_after=None, max_results=25):
    """published_after (datetime UTC) tiene prioridad sobre days."""
    if published_after is None:
        published_after = datetime.now(timezone.utc) - timedelta(days=days)
    data = yt_api("search", {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": "date",
        "maxResults": max_results,
        "videoEmbeddable": "true",   # la clave: solo clips que sí se pueden reproducir dentro de la app
        "regionCode": "MX",
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
        by_key[entry[key]] = {**by_key.get(entry[key], {}), **entry}
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


def build_for_player(player, overrides, settings, do_videos, do_news):
    club = club_label(player)
    full = full_name(player)
    tokens = name_tokens(full)
    result = {}

    if do_videos:
        query = overrides.get("query_video") or " ".join(x for x in (f'"{full}"', club) if x)
        log(f"   🎬 videos · búsqueda: {query!r}")
        found = search_videos(query, settings["dias_busqueda"])
        if found is None:
            result["videos"] = None
        else:
            found = [e for e in found if mentions_player(e, tokens)]
            for canal in overrides.get("canales") or []:
                log(f"   🎬 videos · canal {canal.get('label') or canal.get('channel_id')}")
                uploads = [e for e in channel_uploads(canal.get("channel_id")) if mentions_player(e, tokens)]
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

# ------------------------------------------------------------- Modo gol --

GOAL_WORDS = ("gol", "goal", "golazo", "anota", "marca")


def send_video_push(config, player, video, team_key):
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
        fcm.send("info", title, message, player["key"], team_key)
    except Exception as err:
        log(f"     ⚠️  fallo enviando la notificación del video: {err}")


def next_attempt(st, settings, now):
    """None = todavía no toca · 'normal' | 'final' = toca buscar ·
    'agotado' | 'caducado' = ya no se busca más."""
    offsets = settings["gol_intentos_min"]
    n = st["intentos"]
    if n >= len(offsets):
        return "agotado"
    goal_at = parse_ts(st.get("goal_seen_at"))
    if goal_at is None:
        return "agotado"
    if now - goal_at > timedelta(hours=settings["gol_caducidad_horas"]):
        return "caducado"
    if now < goal_at + timedelta(minutes=offsets[n]):
        return None
    last = parse_ts(st.get("ultimo_intento"))
    if last and now - last < timedelta(minutes=settings["gol_min_entre_intentos"]):
        return None
    # Si el worker llegó tarde y ya pasó también la hora del último intento,
    # se hace UNA sola búsqueda y esa es la definitiva (nunca más de 2).
    late = n == 0 and now >= goal_at + timedelta(minutes=offsets[-1])
    return "final" if (late or n == len(offsets) - 1) else "normal"


def pick_goal_video(candidates):
    """El clip más probable: primero los que dicen 'gol' en el título; entre
    ellos, el que se publicó antes (el primero en salir tras el gol)."""
    def score(e):
        title = normalize(e.get("title") or "")
        has_word = any(w in title for w in GOAL_WORDS)
        return (not has_word, e.get("published_at") or "")
    return sorted(candidates, key=score)[0]


def process_goals(config, settings, sources, notify=True):
    out = load_json(GOLES_FILE, {"updated_at": None, "cuota": {}, "goles": {}})
    state = out.setdefault("goles", {})
    cuota = out.setdefault("cuota", {})
    pending = (load_json(PENDING_FILE, {"goles": []}).get("goles")) or []
    players = {p["key"]: p for p in config["players"]}
    per_player = sources.get("jugadores") or {}
    now = datetime.now(timezone.utc)
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

    # 3) un intento por gol pendiente al que ya le toca
    today = f"{datetime.now(LOCAL_TZ):%Y-%m-%d}"
    if cuota.get("fecha") != today:
        cuota["fecha"], cuota["busquedas"] = today, 0
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
            log(f"   ⛔ tope diario de búsquedas de gol alcanzado — se omite {key}")
            changed = True
            continue

        full = full_name(player)
        tokens = name_tokens(full)
        goal_at = parse_ts(st["goal_seen_at"])
        since = goal_at - timedelta(minutes=settings["gol_margen_min"])
        query = (per_player.get(player["key"]) or {}).get("query_video_gol") or f'"{full}" gol'
        log(f"🔎 gol de {full} · intento {st['intentos'] + 1} · {query!r} · desde {since:%H:%M} UTC")
        found = search_videos(query, published_after=since, max_results=15)
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
                      and e["id"] not in used]
        log(f"     {len(found)} resultados · {len(candidates)} válidos")
        if candidates:
            video = strip_private([pick_goal_video(candidates)])[0]
            st["status"], st["video"] = "resuelto", video
            log(f"     ✅ {video['title']!r} ({video['channel']})")
            if notify:
                send_video_push(config, player, video, st.get("team_key"))
        elif when == "final":
            st["status"], st["motivo"] = "omitido", "sin video tras los intentos"
            log("     ⏭️  sin video tras el último intento — se omite")

    if changed:
        out["updated_at"] = datetime.now(LOCAL_TZ).isoformat()
        save_json(GOLES_FILE, out)
        log(f"✅ {GOLES_FILE} actualizado · cuota de YouTube gastada: {_quota_used} unidades")
    else:
        log("Nada que atender.")



def main():
    parser = argparse.ArgumentParser(description="Fase 6 · Videos y noticias")
    parser.add_argument("--jugador", help="Limita a un jugador por su 'key'")
    parser.add_argument("--solo", choices=["videos", "noticias"], help="Corre solo una de las dos fuentes")
    parser.add_argument("--gol", action="store_true",
                        help="Atiende los goles pendientes (videos_pendientes.json) y escribe videos_gol.json")
    parser.add_argument("--sin-notificar", action="store_true", help="Con --gol: no manda la notificación")
    args = parser.parse_args()

    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit(f"Falta {CONFIG_FILE}.")
    sources = load_json(SOURCES_FILE, {})
    settings = {**DEFAULTS, **(sources.get("config") or {})}
    per_player = sources.get("jugadores") or {}

    if args.gol:
        process_goals(config, settings, sources, notify=not args.sin_notificar)
        return

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

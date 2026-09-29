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
La corrida normal ya NO gasta cuota. Los eventos comparten un tope de 50
búsquedas/día (= 5,000 unidades, la mitad de la cuota gratis); de esas, los
resúmenes pueden usar máximo 20 para no quitarle cuota a los goles.

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
       - "resumen_notificar" (apagado) manda "Ya está el resumen".
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
    "max_noticias": 2,       # tope de noticias guardadas por jugador (pocas y buenas, no un feed)
    "dias_retencion": 30,    # lo guardado más viejo que esto se descarta. Las políticas de la API de YouTube exigen borrar o refrescar sus datos a los 30 días: NO subir de 30
    # --- modo --eventos: goles
    "gol_intentos_min": [15, 30],     # minutos después del gol en que se busca (2 intentos, ni uno más)
    "gol_margen_min": 10,             # el clip puede haberse publicado hasta N min antes de que se detectara el gol
    "gol_min_entre_intentos": 10,     # si el worker se atrasa, separa los intentos al menos N min
    "gol_max_busquedas_dia": 50,      # tope de búsquedas de YouTube al día (goles + resúmenes): 50 x 100 = 5,000 unidades, la mitad de la cuota gratis
    "gol_caducidad_horas": 3,         # un gol/partido pendiente más viejo que esto se omite
    "gol_retencion_horas": 48,        # cuánto se conserva un gol/partido en videos_gol.json
    # --- modo --eventos: final de partido (resumen + noticia principal)
    "resumen_intentos_min": [30, 60],     # minutos después del final en que se busca el resumen (2 intentos) y se refresca la noticia
    "resumen_margen_min": 10,             # el resumen puede haberse publicado hasta N min antes de que se detectara el final
    "resumen_max_busquedas_dia": 20,      # de las 50 búsquedas del día, máximo 20 pueden ser de resúmenes: los goles siempre conservan cuota
    "resumen_solo_con_minutos": True,     # solo se busca resumen si algún jugador del roster tuvo minutos
    "resumen_notificar": False,           # True = manda "Ya está el resumen" (apagado por defecto para no saturar de notificaciones)
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


def build_for_player(player, overrides):
    """Solo noticias. Los videos ya NO se buscan de forma abierta (traía videos
    de aficionados): llegan únicamente por eventos, ver process_events."""
    query = news_query(player, overrides)
    log(f"   📰 noticias · {query!r}")
    return {"noticias": fetch_news(query)}


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


def _due(n, base, last_iso, offsets, settings, now):
    """None = todavía no toca · 'normal' | 'final' = toca ·
    'agotado' | 'caducado' = ya no se hace más. n = intentos hechos; base =
    momento del evento; offsets = minutos después del evento de cada intento."""
    if n >= len(offsets):
        return "agotado"
    if base is None:
        return "agotado"
    if now - base > timedelta(hours=settings["gol_caducidad_horas"]):
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


def pick_goal_video(candidates):
    """El clip más probable: primero los que dicen 'gol' en el título; entre
    ellos, el que se publicó antes (el primero en salir tras el gol)."""
    def score(e):
        title = normalize(e.get("title") or "")
        has_word = any(w in title for w in GOAL_WORDS)
        return (not has_word, e.get("published_at") or "")
    return sorted(candidates, key=score)[0]


# ------------------------------------------------------- Final de partido --

TEAM_STOP = {"fc", "cf", "sc", "ac", "as", "cd", "ud", "club", "de", "del", "la", "el", "the", "and"}
TEAM_GENERIC = {"real", "united", "city", "sporting", "deportivo", "athletic"}
RESUMEN_WORDS = ("resumen", "highlights", "goles", "goals", "compacto", "melhores", "resume", "extended")


def team_prefixes(name, aliases=()):
    """Prefijos (5 letras) de las palabras distintivas de un equipo, para
    aguantar variantes: 'Olympiakos Piraeus' / 'Olympiacos' -> 'olymp'."""
    def words(text):
        return [w for w in re.findall(r"[a-z0-9]+", normalize(text)) if w not in TEAM_STOP]
    base = [w for w in words(name) if len(w) >= 4]
    strong = [w for w in base if w not in TEAM_GENERIC] or base or [w for w in words(name) if len(w) >= 2]
    extra = [w for a in aliases for w in words(a) if len(w) >= 4]
    return {w[:5] for w in strong + extra}


def mentions_team(text_norm, prefixes):
    words = re.findall(r"[a-z0-9]+", text_norm)
    return any(w.startswith(p) for p in prefixes for w in words)


def is_match_summary(entry, home_pref, away_pref):
    """El clip debe nombrar a los DOS equipos y decir que es un resumen."""
    text = normalize(f"{entry.get('title') or ''} {entry.get('_description') or ''}")
    title = normalize(entry.get("title") or "")
    return (mentions_team(text, home_pref) and mentions_team(text, away_pref)
            and any(w in title for w in RESUMEN_WORDS))


def send_summary_push(config, players, video, st):
    """Opcional (resumen_notificar): 'Ya está el resumen'. Tipo 'info'."""
    try:
        import hormi_alertas as ha
        title = f"🎬 Ya está el resumen: {st.get('home')} vs {st.get('away')}"
        message = " · ".join(x for x in (video.get("title"), video.get("channel")) if x) + " — míralo en la app"
        if config.get("ntfy_topic"):
            ha.Notifier(config["ntfy_topic"]).send(title, message, 3)
        fcm = ha.FcmSender(config.get("fcm_project_id"))
        for pk, j in st["jugadores"].items():
            if pk in players and (j.get("minutes") or 0) > 0:
                fcm.send("info", title, message, pk, j.get("team_key"))
    except Exception as err:
        log(f"     ⚠️  fallo enviando la notificación del resumen: {err}")


def _finales_step(out, config, settings, sources, notify, now):
    """Final de partido: (1) refresca la noticia principal de cada jugador
    (RSS, gratis) a los +30 y +60 min; (2) busca el resumen en YouTube a los
    +30 y +60 min (2 intentos, ni uno más). Devuelve True si cambió algo."""
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
                        st["noticias"][pk] = curate_news(fresh, player, settings["max_noticias"])
                st["noticias_pasadas"] = len(offsets) if when == "final" else st["noticias_pasadas"] + 1
                st["ultima_noticia"] = now.isoformat()
                changed = True

        # 4) resumen del partido (YouTube)
        if st["status"] != "pendiente":
            continue
        when = _due(st["intentos"], base, st.get("ultimo_intento"), offsets, settings, now)
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
        query = f"{st.get('home')} {st.get('away')} resumen goles"
        log(f"🔎 resumen {match} · intento {st['intentos'] + 1} · {query!r} · desde {since:%H:%M} UTC")
        found = search_videos(query, published_after=since, max_results=15)
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
                      and e["id"] not in used]
        log(f"     {len(found)} resultados · {len(candidates)} válidos")
        if candidates:
            best = sorted(candidates, key=lambda e: e.get("published_at") or "")[0]
            video = strip_private([best])[0]
            st["status"], st["video"] = "resuelto", video
            log(f"     ✅ {video['title']!r} ({video['channel']})")
            if notify and settings.get("resumen_notificar"):
                send_summary_push(config, players, video, st)
        elif when == "final":
            st["status"], st["motivo"] = "omitido", "sin resumen tras los intentos"
            log("     ⏭️  sin resumen tras el último intento — se omite")
    return changed


# ----------------------------------------------------------- Modo eventos --

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
        if fresh.get("noticias") is not None:
            merged_news = merge(merged_news, fresh["noticias"], "url", 10_000, settings["dias_retencion"])
        # se cura SIEMPRE (también lo ya guardado), así una corrida limpia lo que quedó de antes
        entry["noticias"] = curate_news(merged_news, player, settings["max_noticias"])
        log(f"     → {len(entry['noticias'])} noticias guardadas")

    output["updated_at"] = datetime.now(LOCAL_TZ).isoformat()
    save_json(VIDEOS_FILE, output)
    log(f"✅ listo · {VIDEOS_FILE} actualizado")


if __name__ == "__main__":
    main()

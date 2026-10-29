#!/usr/bin/env python3
"""
Rachas e hitos de cada jugador → rachas.json (lo lee la app en Estadísticas).

Rachas (con su club, últimos partidos oficiales):
  - gol: partidos seguidos anotando
  - ga: partidos seguidos con gol o asistencia
  - titular: partidos seguidos de titular
  - jugando: partidos seguidos con minutos
  Se cuentan del partido más reciente hacia atrás. "puntos" = los últimos 8 (el primero es el más reciente).

Hitos (toda su carrera, de player_stats.json):
  - goles y partidos con clubes, goles y partidos con la Selección.
  Muestra cuánto le falta para la siguiente meta (1, 5, 10, 15, 20, 25, 30, 40, 50, ...).

Avisos (tipo "racha"):
  - Racha de gol o de gol/asistencia que llega a 3 o más (y cada vez que crece).
  - Racha de titular o jugando al llegar a 10, 20, 30...
  - Hito cumplido: "🏅 Armando González llegó a 40 goles con clubes".
  La primera corrida (sin rachas.json previo) no avisa nada: solo arma el estado.

Peticiones: 1 por equipo (/fixtures?team&last=N) + 1 por partido terminado nuevo (/fixtures/players,
se guarda en rachas_cache.json y no se vuelve a pedir).
"""
import argparse
import re
import json
from datetime import datetime
from pathlib import Path

import hormi_alertas as ha

OUT = Path("rachas.json")
CACHE = Path("rachas_cache.json")
ULTIMOS = 20          # partidos del club que se revisan
PUNTOS = 8            # puntos que se muestran en la app
TERMINADOS = {"FT", "AET", "PEN"}
SELECCION_IDS = {16}
METAS_GOLES = [1, 5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 175, 200, 250, 300, 400, 500]
METAS_PARTIDOS = [1, 10, 25, 50, 75, 100, 150, 200, 250, 300, 350, 400, 500, 600, 700, 800]
RACHAS = [
    ("gol", "Partidos seguidos con gol"),
    ("ga", "Partidos seguidos con gol o asistencia"),
    ("titular", "Partidos seguidos de titular"),
    ("jugando", "Partidos seguidos jugando"),
]
# Defensas: en vez de "con gol", partidos seguidos sin recibir gol jugando al menos 60' (portería en cero).
RACHA_DEFENSA = ("cero", "Partidos seguidos sin recibir gol")
MIN_CERO = 60
EN_RACHA = {"gol": 2, "ga": 2, "cero": 2, "titular": 5, "jugando": 5}   # desde cuánto se pinta "EN RACHA" en la app


def log(msg):
    ha.log(msg)


def club_target(player):
    for t in player.get("targets") or []:
        if t.get("kind") != "seleccion" and t.get("team_id"):
            return t
    return None


def stats_partido(fid, team_id, cache):
    """{player_id: {min, titular, goles, asist}} de ese equipo en ese partido (con caché)."""
    k = f"{fid}:{team_id}"
    if k in cache:
        return cache[k]
    res = ha.api("/fixtures/players", {"fixture": fid, "team": team_id})
    if res is None:
        return None   # error de la API: se reintenta en la siguiente corrida
    out = {}
    for t in res:
        for p in t.get("players") or []:
            pid = (p.get("player") or {}).get("id")
            st = (p.get("statistics") or [{}])[0]
            g = st.get("games") or {}
            gl = st.get("goals") or {}
            out[str(pid)] = {"min": g.get("minutes") or 0, "titular": g.get("substitute") is False,
                             "goles": gl.get("total") or 0, "asist": gl.get("assists") or 0}
    cache[k] = out
    return out


def titulares(fids, team_id, cache):
    """Ids de los 11 titulares de ese equipo en cada partido, de la alineación (startXI). El campo
    'substitute' de /fixtures/players NO es confiable (marcó titular a Armando cuando entró de cambio en
    Marsella). /fixtures?ids=… trae hasta 20 partidos completos (con alineaciones) en 1 petición."""
    faltan = [f for f in fids if f"xi:{f}:{team_id}" not in cache]
    for i in range(0, len(faltan), 20):
        res = ha.api("/fixtures", {"ids": "-".join(str(f) for f in faltan[i:i + 20])})
        for fx in res or []:
            fid = (fx.get("fixture") or {}).get("id")
            for lu in fx.get("lineups") or []:
                if (lu.get("team") or {}).get("id") == team_id and lu.get("startXI"):
                    cache[f"xi:{fid}:{team_id}"] = [str((x.get("player") or {}).get("id")) for x in lu["startXI"]]
    return {f: cache.get(f"xi:{f}:{team_id}") for f in fids}


def racha(partidos, cond):
    n = 0
    for p in partidos:
        if not cond(p):
            break
        n += 1
    return n


def conds():
    return {
        "gol": lambda p: p["goles"] > 0,
        "cero": lambda p: p["min"] >= MIN_CERO and p.get("recibidos") == 0,
        "ga": lambda p: p["goles"] + p["asist"] > 0,
        "titular": lambda p: p["titular"] and p["min"] > 0,
        "jugando": lambda p: p["min"] > 0,
    }


# Selecciones juveniles (Mexico U20, Colombia U21, United States U20…): no son clubes ni la Selección mayor.
JUVENIL_SEL_RE = re.compile(r"^(mexico|méxico|colombia|united states|usa|spain|españa|argentina|brazil|portugal|"
                            r"netherlands|italy|germany|france|england)\s+u\d{2}$", re.I)


_TORNEOS_JUVENILES = ("pan american games", "olympics", "cac games", "maurice revello", "toulon", "u20", "u23", "u17")


def cuenta_en_carrera(r):
    """Misma regla que la app (Estadísticas → General) y la imagen de "Carrera": no cuentan los partidos de
    exhibición (MLS All-Star vs Liga MX All-Stars) ni los de selecciones juveniles (México Sub-20, Sub-23, Juegos
    Panamericanos que la API trae sin equipo...)."""
    tn = (r.get("team_name") or "").strip()
    liga = (r.get("league_name") or "").lower()
    if "all-star" in liga or "all star" in liga or "all-stars" in tn.lower():
        return False
    if JUVENIL_SEL_RE.match(tn):
        return False
    if (r.get("team_id") is None or tn in ("", "?")) and any(t in liga for t in _TORNEOS_JUVENILES):
        return False
    return True


def hitos(seasons):
    tot = {"goles_club": 0, "partidos_club": 0, "goles_sel": 0, "partidos_sel": 0}
    for rows in (seasons or {}).values():
        for r in rows:
            if not cuenta_en_carrera(r):
                continue
            sel = r.get("team_id") in SELECCION_IDS or (r.get("team_name") or "").lower() in ("mexico", "méxico")
            tot["goles_sel" if sel else "goles_club"] += r.get("goals") or 0
            tot["partidos_sel" if sel else "partidos_club"] += r.get("played") or 0
    tot["goles_total"] = tot["goles_club"] + tot["goles_sel"]
    defs = [("goles_total", "Goles en su carrera", METAS_GOLES, "gol", "goles"),
            ("goles_club", "Goles con clubes", METAS_GOLES, "gol", "goles"),
            ("partidos_club", "Partidos con clubes", METAS_PARTIDOS, "partido", "partidos"),
            ("goles_sel", "Goles con la Selección", METAS_GOLES, "gol", "goles"),
            ("partidos_sel", "Partidos con la Selección", METAS_PARTIDOS, "partido", "partidos")]
    out = []
    for hid, titulo, metas, uno, varios in defs:
        v = tot[hid]
        if hid.endswith("_sel") and tot["partidos_sel"] == 0:
            continue
        meta = next((m for m in metas if m > v), None)
        previo = max([m for m in metas if m <= v], default=0)
        falta = (meta - v) if meta else 0
        out.append({"id": hid, "titulo": titulo, "valor": v, "meta": meta, "previo": previo,
                    "texto": (f"A {falta} {uno if falta == 1 else varios} de {meta}" if meta else "")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-avisos", action="store_true", help="no manda push (pruebas)")
    args = ap.parse_args()

    config = ha.load_json(ha.CONFIG_FILE, {})
    archivados = set(config.get("archivados") or [])
    stats = ha.load_json(Path("player_stats.json"), {}).get("players") or {}
    prev = ha.load_json(OUT, None)
    primera = prev is None
    prev_players = (prev or {}).get("players") or {}
    cache = ha.load_json(CACHE, {})
    por_equipo = {}
    avisos = []
    out = {"updated_at": datetime.now(ha.LOCAL_TZ).isoformat(), "players": {}}

    for player in config.get("players") or []:
        key = player["key"]
        if key in archivados:
            continue
        nombre = ha.display_name(player)
        pid = str(player.get("player_id"))
        prev_e = prev_players.get(key) or {}
        # Hitos ya avisados ("partidos_club:200"): el conteo a veces baja y sube (199 → 200 → 199 → 200) y no
        # debe volver a avisar. Incluye los que ya estaban logrados en la corrida anterior.
        avisados = set(prev_e.get("avisados") or []) | {f"{h['id']}:{h['previo']}" for h in prev_e.get("hitos") or [] if h.get("previo", 0) > 0}
        logros = []
        for l in reversed(prev_e.get("logros") or []):   # sin repetidos: se queda la fecha en que lo logró primero
            if all(l.get("texto") != x.get("texto") for x in logros):
                logros.insert(0, l)
        hs = hitos((stats.get(key) or {}).get("seasons"))
        # Si los datos se corrigen hacia abajo (p. ej. 200 → 196 al quitar la Sub-23), se quitan los logros y las
        # marcas de "ya avisado" que ya no se cumplen (por más de 2): así el aviso llega cuando de verdad llegue.
        valor_h = {h["id"]: h["valor"] for h in hs}
        def _vigente(l):
            m = re.match(r"^(\d+) (goles|partidos) con (clubes|la Selección)", l.get("texto") or "")
            if not m:
                return True
            hid = f"{m.group(2)}_{'club' if m.group(3) == 'clubes' else 'sel'}"
            return valor_h.get(hid, 0) >= int(m.group(1)) - 2   # bajar 1-2 es vaivén de la API, no corrección
        logros = [l for l in logros if _vigente(l)]
        avisados = {a for a in avisados if not (a.split(":")[0] in valor_h and a.split(":")[-1].isdigit()
                                                and valor_h[a.split(":")[0]] < int(a.split(":")[-1]) - 2)}
        entry = {"rachas": [], "hitos": hs, "logros": logros,
                 "avisados": sorted(avisados)}
        t = club_target(player)
        if t:
            tid = t["team_id"]
            if tid not in por_equipo:
                fx = ha.api("/fixtures", {"team": tid, "last": ULTIMOS}) or []
                por_equipo[tid] = sorted([f for f in fx if (f.get("fixture") or {}).get("status", {}).get("short") in TERMINADOS],
                                         key=lambda f: f["fixture"]["timestamp"], reverse=True)
            partidos = []
            xi = titulares([f["fixture"]["id"] for f in por_equipo[tid]], tid, cache)
            for f in por_equipo[tid]:
                sp = stats_partido(f["fixture"]["id"], tid, cache)
                if sp is None:
                    break   # sin datos de ese partido: no inventamos rachas, se completa en la siguiente corrida
                fila = dict(sp.get(pid) or {"min": 0, "titular": False, "goles": 0, "asist": 0})
                once = xi.get(f["fixture"]["id"])
                if once is not None:
                    fila["titular"] = pid in once   # la alineación manda; si no hay alineación se queda lo de la API
                local = ((f.get("teams") or {}).get("home") or {}).get("id") == tid
                gl = f.get("goals") or {}
                fila["recibidos"] = gl.get("away" if local else "home")
                partidos.append(fila)
            entry["club"] = t.get("label")
            entry["partidos_revisados"] = len(partidos)
            c = conds()
            lista = RACHAS if player.get("posicion") != "defensa" else [RACHA_DEFENSA] + RACHAS[1:]
            for rid, titulo in lista:
                actual = racha(partidos, c[rid])
                entry["rachas"].append({"id": rid, "titulo": titulo, "actual": actual,
                                        "en_racha": actual >= EN_RACHA[rid],
                                        "puntos": [c[rid](p) for p in partidos[:PUNTOS]]})

        # ── avisos (comparando con la corrida anterior)
        if not primera:
            antes_r = {r["id"]: r["actual"] for r in (prev_players.get(key) or {}).get("rachas") or []}
            ahora_r = {r["id"]: r["actual"] for r in entry["rachas"]}
            if ahora_r.get("gol", 0) >= 3 and ahora_r["gol"] > antes_r.get("gol", 0):
                avisos.append((key, t, f"🔥 {nombre} lleva {ahora_r['gol']} partidos seguidos anotando",
                               f"Racha goleadora con {t.get('label') if t else 'su club'}", "gol"))
            elif ahora_r.get("cero", 0) >= 3 and ahora_r["cero"] > antes_r.get("cero", ahora_r["cero"]):
                avisos.append((key, t, f"🧱 {nombre}: {ahora_r['cero']} partidos seguidos sin recibir gol",
                               f"Muralla con {t.get('label') if t else 'su club'}", "cero"))
            elif ahora_r.get("ga", 0) >= 3 and ahora_r["ga"] > antes_r.get("ga", 0):
                avisos.append((key, t, f"🔥 {nombre}: {ahora_r['ga']} partidos seguidos con gol o asistencia",
                               f"En racha con {t.get('label') if t else 'su club'}", "ga"))
            for rid, txt in (("titular", "de titular"), ("jugando", "jugando")):
                n = ahora_r.get(rid, 0)
                if n >= 10 and n % 10 == 0 and n > antes_r.get(rid, 0):
                    avisos.append((key, t, f"💪 {nombre} lleva {n} partidos seguidos {txt}", t.get("label") if t else "", rid))
            antes_h = {h["id"]: h["valor"] for h in (prev_players.get(key) or {}).get("hitos") or []}
            for h in entry["hitos"]:
                marca = f"{h['id']}:{h['previo']}"
                antes_v = antes_h.get(h["id"], h["valor"])
                if h["previo"] > 0 and marca not in avisados and antes_v < h["previo"] <= h["valor"]:
                    avisados.add(marca)
                    entry["avisados"] = sorted(avisados)
                    # Un salto grande no es un partido nuevo, es una corrección de datos (p. ej. la API empezó a
                    # contar bien los partidos de la Liga MX: 12 → 45). Se marca como visto, sin push ni logro.
                    if h["valor"] - antes_v > (4 if h["id"].startswith("goles") else 2):
                        ha.log(f"  {key}: {h['id']} {antes_v} → {h['valor']} es corrección de datos, sin aviso")
                        continue
                    unidad = "goles" if h["id"].startswith("goles") else "partidos"
                    donde = ("en su carrera" if h["id"].endswith("_total")
                             else "con la Selección" if h["id"].endswith("_sel") else "con clubes")
                    txt = f"{h['previo']} {unidad} {donde}" if h["previo"] > 1 else f"su primer {'gol' if unidad == 'goles' else 'partido'} {donde}"
                    avisos.append((key, t, f"🏅 {nombre} llegó a {txt}", "Nuevo hito en su carrera", h["id"]))
                    entry["logros"].insert(0, {"texto": txt[0].upper() + txt[1:], "fecha": datetime.now(ha.LOCAL_TZ).date().isoformat()})
            entry["logros"] = entry["logros"][:5]
        out["players"][key] = entry

    ha.save_json(OUT, out)
    ha.save_json(CACHE, cache)
    log(f"rachas.json: {len(out['players'])} jugadores · {len(cache)} partidos en caché · {len(avisos)} aviso(s)"
        + (" · primera corrida, sin avisos" if primera else ""))

    if avisos and not args.sin_avisos:
        fcm = ha.FcmSender(config.get("fcm_project_id"))
        for key, t, title, body, *foco in avisos:
            try:
                extra = {"tab": "hitos" if title.startswith("🏅") else "rachas", "player": key}   # la app abre esa pestaña y ese jugador
                if foco:
                    extra["item"] = foco[0]   # id del hito para resaltarlo
                fcm.send("racha", title, body, key, (t or {}).get("key"), extra=extra)
            except Exception as err:
                log(f"⚠️  no se pudo mandar el aviso de racha ({key}): {err}")


if __name__ == "__main__":
    from hormi_pausa import salir_si_pausa
    salir_si_pausa()
    main()

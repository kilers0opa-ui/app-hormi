#!/usr/bin/env python3
"""
Revisión de partidos recientes: la API a veces corrige un partido horas o días después (un gol que pasa a
ser autogol del rival, un gol que se le da a otro, una asistencia que se quita). El motor solo vigila hasta
3 h después del final, así que esto vuelve a leer el "Último partido" de cada jugador si fue en los últimos
3 días y actualiza su tarjeta (goles, asistencias, Detalles, alineación, marcador).

Si un gol que YA SE AVISÓ deja de ser del jugador, manda una corrección (una sola vez):
  ✏️ Corrección: el gol de Armando González (78') quedó como autogol de Mannsverk

Costo: 1 petición por cada 20 partidos (/fixtures?ids=...) → normalmente 1 al día. Corre en historial.yml.
"""
import argparse
from pathlib import Path
from datetime import datetime, timedelta

import hormi_alertas as ha

STATS_FILE = ha.STATS_FILE
ESTADO_FILE = ha.STATE_FILE
REV_FILE = Path("revision.json")   # correcciones ya avisadas (para no repetirlas)
DIAS = 3


def conteo(fx, pid):
    """(goles, asistencias) del jugador según los eventos (sin autogoles, penales fallados, anulados ni tanda)."""
    tanda = ha._tanda(fx)
    anulados = ha._goles_anulados(fx)
    g = a = 0
    for i, e in enumerate(fx.get("events") or []):
        if (e.get("type") or "").lower() != "goal" or i in anulados or id(e) in tanda:
            continue
        det = e.get("detail") or ""
        if det == "Missed Penalty":
            continue
        if (e.get("player") or {}).get("id") == pid and det != "Own Goal":
            g += 1
        elif (e.get("assist") or {}).get("id") == pid and det != "Own Goal":
            a += 1
    return g, a


def _es_gol(ev):
    return ev.get("tipo") in ("gol", "pen")


def correcciones(nombre, viejos, nuevos):
    """Textos de corrección para los goles del jugador que ya no son suyos (comparando 'Detalles')."""
    out = []
    mios_nuevos = {e.get("min") for e in nuevos if e.get("mio") and _es_gol(e)}
    for e in viejos:
        if not (e.get("mio") and _es_gol(e)) or e.get("min") in mios_nuevos:
            continue
        m = e.get("min")
        ahora = next((x for x in nuevos if x.get("min") == m and x.get("tipo") in ("gol", "pen", "autogol", "anulado")), None)
        if ahora and ahora.get("tipo") == "autogol":
            txt = f"el gol de {nombre} ({m}) quedó como autogol de {ahora.get('nombre') or 'un rival'}"
        elif ahora and ahora.get("tipo") in ("gol", "pen"):
            txt = f"el gol de {nombre} ({m}) se lo dieron a {ahora.get('nombre') or 'otro jugador'}"
        else:
            txt = f"el gol de {nombre} ({m}) ya no cuenta"
        out.append((m, txt))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-avisos", action="store_true")
    args = ap.parse_args()
    config = ha.load_config()
    archivados = set(config.get("archivados") or [])
    stats = ha.load_json(STATS_FILE, {"players": {}})
    estado = ha.load_json(ESTADO_FILE, {})
    def _llaves(v):
        if isinstance(v, dict):
            return [k for x in v.values() for k in _llaves(x)]
        return list(v) if isinstance(v, list) else []
    enviados = set(_llaves(estado.get("sent") or {}))   # {jugador: {equipo: [llaves]}}
    rev = ha.load_json(REV_FILE, {"avisadas": []})
    avisadas = set(rev.get("avisadas") or [])
    limite = datetime.now(ha.LOCAL_TZ) - timedelta(days=DIAS)

    por_fixture = {}
    for p in config["players"]:
        if p["key"] in archivados:
            continue
        u = (stats.get("players", {}).get(p["key"]) or {}).get("ultimo_partido") or {}
        try:
            reciente = u.get("fixture_id") and datetime.fromisoformat(u["kickoff"]) >= limite
        except Exception:
            reciente = False
        if reciente:
            por_fixture.setdefault(u["fixture_id"], []).append(p)
    if not por_fixture:
        ha.log("revisión: ningún partido en los últimos 3 días")
        return

    ids = list(por_fixture)
    datos = {}
    for i in range(0, len(ids), 20):
        res = ha.api("/fixtures", {"ids": "-".join(str(x) for x in ids[i:i + 20])}) or []
        for fx in res:
            datos[fx["fixture"]["id"]] = fx
    ha.log(f"revisión: {len(datos)}/{len(ids)} partido(s) leídos")

    cambios, avisos = 0, []
    for fid, jugadores in por_fixture.items():
        fx = datos.get(fid)
        if not fx or (fx["fixture"]["status"] or {}).get("short") not in ha.FINISHED:
            continue
        g = fx.get("goals") or {}
        for p in jugadores:
            pid = p.get("player_id")
            u = stats["players"][p["key"]]["ultimo_partido"]
            goles, asist = conteo(fx, pid)
            nuevos = ha._eventos(fx, pid)
            viejos = u.get("eventos") or []
            nuevo = dict(u, eventos=nuevos, eventos_v=ha.EVENTOS_V, alineacion=ha._alineacion(fx, pid) or u.get("alineacion"),
                         home_goals=g.get("home"), away_goals=g.get("away"), penales=ha._penales(fx),
                         goals=goles, assists=asist)
            if nuevo == u:
                continue
            nombre = ha.display_name(p)
            if (u.get("goals"), u.get("assists")) != (goles, asist):
                ha.log(f"✏️  {nombre} · {u.get('home')} vs {u.get('away')}: goles {u.get('goals')}→{goles}, "
                       f"asistencias {u.get('assists')}→{asist}")
            stats["players"][p["key"]]["ultimo_partido"] = nuevo
            cambios += 1
            # Corrección solo si ese gol se había avisado (el motor guarda "jugador:equipo:fixture:goal:...:gol").
            avisado = any(k.startswith(f"{p['key']}:{u.get('team_key')}:{fid}:goal:") and k.endswith(":gol") for k in enviados)
            if avisado:
                for m, txt in correcciones(nombre, viejos, nuevos):
                    marca = f"{p['key']}:{fid}:{m}"
                    if marca in avisadas:
                        continue
                    avisadas.add(marca)
                    body = f"{u.get('home')} {g.get('home')}-{g.get('away')} {u.get('away')} · {u.get('league') or ''}".strip(" ·")
                    avisos.append((p["key"], u.get("team_key"), f"✏️ Corrección: {txt}", body))

    # Tarjeta del partido (para compartir, 24 h): se crea si falta y se rehace si la API corrigió algo.
    for p in config["players"]:
        u = (stats.get("players", {}).get(p["key"]) or {}).get("ultimo_partido") or {}
        try:
            reciente = u.get("fixture_id") and datetime.fromisoformat(u["kickoff"]) >= datetime.now(ha.LOCAL_TZ) - timedelta(hours=30)
        except Exception:
            reciente = False
        # Una sola vez: los partidos guardados antes de la Calificación Legión MX se recalculan con la nuestra.
        migrar = bool(u.get("stats")) and "rating_api" not in u["stats"] and bool(u.get("fixture_id"))
        if (not reciente and not migrar) or p["key"] in archivados:
            continue
        antes_t = dict(u.get("tarjeta") or {})
        # Calificación DEFINITIVA: la fuente publica una provisional al final y la recalcula en las horas siguientes.
        # Desde 3 h después del final (~5 h después del inicio) se lee UNA vez más y queda fija para siempre
        # (Inicio, Estadísticas y la tarjeta). 1 petición por partido.
        try:
            ya_asento = datetime.now(ha.LOCAL_TZ) >= datetime.fromisoformat(u["kickoff"]) + timedelta(hours=5)
        except Exception:
            ya_asento = False
        if migrar or (u.get("stats") and not u["stats"].get("calif_fija") and (ya_asento or u.get("stats_v") != 2)):
            tid = next((t.get("team_id") for t in p.get("targets") or [] if t.get("key") == u.get("team_key")), None)
            res = ha.api("/fixtures/players", {"fixture": u["fixture_id"]})
            st = ha.stats_jugador(res, p.get("player_id"), tid, p["key"]) if res else None
            if st:
                if ya_asento or u["stats"].get("calif_fija"):
                    st["calif_fija"] = True
                antes = (u.get("stats") or {}).get("rating")
                u["stats"], u["stats_v"] = st, 2
                cambios += 1
                ha.log(f"📊 {p['key']}: estadísticas del partido{' (calificación fija)' if ya_asento else ''} · "
                       f"calif. {antes} → {st.get('rating')}")
            elif migrar:
                u["stats"]["rating_api"] = None   # liga sin números por jugador: no se vuelve a intentar
                cambios += 1
        if not reciente:
            continue
        try:
            ha.tarjeta_partido_para(p, u, dict(u))
        except Exception as err:
            ha.log(f"⚠️  tarjeta del partido de {p['key']}: {err}")
            continue
        if u.get("tarjeta") != antes_t:
            cambios += 1
    if cambios:
        stats["updated_at"] = ha.now_iso()
        ha.save_json(STATS_FILE, stats)
    rev["avisadas"] = sorted(avisadas)
    ha.save_json(REV_FILE, rev)
    ha.log(f"revisión: {cambios} tarjeta(s) actualizada(s) · {len(avisos)} corrección(es)")
    if avisos and not args.sin_avisos:
        fcm = ha.FcmSender(config.get("fcm_project_id"))
        for key, tkey, title, body in avisos:
            try:
                fcm.send("incident", title, body, key, tkey)
            except Exception as err:
                ha.log(f"⚠️  no se pudo mandar la corrección ({key}): {err}")


if __name__ == "__main__":
    from hormi_pausa import salir_si_pausa
    salir_si_pausa()
    main()

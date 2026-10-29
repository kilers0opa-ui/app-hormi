#!/usr/bin/env python3
"""
Auditor de Legión MX: revisa SOLO lo que la app muestra de cada jugador después de sus partidos y lo corrige en
silencio (nunca manda notificaciones a los usuarios). Lo que no puede corregir, y el resumen de lo que corrigió,
llega por correo a soporte (legionmx.soporte@gmail.com).

Lo lanza el motor ~15 min después de que termina cada partido (y corre solo cada 3 h como respaldo).

Qué revisa, por cada partido terminado de los últimos 3 días (agenda.json) y cada jugador de ese partido:
  1. ¿Jugó? (estadísticas por jugador → alineación → eventos suyos: gol, asistencia, cambio, tarjeta).
  2. Si jugó y es su partido más reciente → debe ser su "Último partido" (si no: se rehace).
  3. Debe estar sumado a su temporada (si no: se rehace su historial).
  4. Debe tener calificación. Sin estadísticas por jugador en la fuente → calificación PARCIAL con lo que sí hay
     (minutos, goles, asistencias, tarjetas, resultado). Si después la fuente las publica (≤ 48 h), se recalcula 1 vez.
  5. La calificación debe cuadrar con el marcador (victoria/derrota/portería en cero). Si se calculó con el marcador
     equivocado, se recalcula.
  6. Si metió gol: que se haya buscado el video (si no: se busca en silencio).
Además: Agenda sin partidos "atorados" (horario o "en vivo" de un partido ya terminado), bajas/dudas viejas de
un jugador que ya volvió a jugar, e hitos/rachas al día.

Costo: 1 petición por partido terminado nuevo (/fixtures?id trae alineación, eventos y estadísticas). Un partido ya
revisado y en orden no se vuelve a pedir.
"""
import argparse
import json
import os
import smtplib
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import hormi_alertas as ha
import hormi_calif_legion as CL

OUT = Path("auditoria.json")
CORREO = "legionmx.soporte@gmail.com"
DIAS = 3
FIN = {"FT", "AET", "PEN"}
CERRADOS = FIN | {"CANC", "AWD", "WO"}
HORA_RESUMEN = 21   # resumen diario (hora del centro de México)

hechos = []      # lo que se corrigió en esta corrida: (clave única, texto)
problemas = []   # lo que no se pudo corregir: (clave única, texto)


def hecho(clave, txt):
    ha.log(f"🛠️  {txt}")
    hechos.append((clave, txt))


def problema(clave, txt):
    ha.log(f"❗ {txt}")
    problemas.append((clave, txt))


def ahora():
    return datetime.now(ha.LOCAL_TZ)


def correr(*cmd):
    """Corre otro script del repo (en silencio). True si terminó bien."""
    r = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, timeout=900)
    if r.returncode:
        ha.log(f"⚠️  {' '.join(cmd)} falló: {(r.stderr or r.stdout)[-400:]}")
    return r.returncode == 0


# ───────────────────────────── participación y calificación ─────────────────────────────

def participacion(fx, pid, tid):
    """{"minutes", "goals", "assists", "yellow", "red", "fuente"} o None si no jugó."""
    for t in fx.get("players") or []:
        if (t.get("team") or {}).get("id") != tid:
            continue
        for pl in t.get("players") or []:
            if (pl.get("player") or {}).get("id") == pid:
                st = (pl.get("statistics") or [{}])[0]
                m = (st.get("games") or {}).get("minutes") or 0
                if not m:
                    return None
                g = st.get("goals") or {}
                c = st.get("cards") or {}
                return {"minutes": m, "goals": g.get("total") or 0, "assists": g.get("assists") or 0,
                        "yellow": c.get("yellow") or 0, "red": c.get("red") or 0, "fuente": "estadisticas"}
    titular = banca = False
    for lu in fx.get("lineups") or []:
        if any((x.get("player") or {}).get("id") == pid for x in lu.get("startXI") or []):
            titular = True
        if any((x.get("player") or {}).get("id") == pid for x in lu.get("substitutes") or []):
            banca = True
    total = 120 if fx["fixture"]["status"]["short"] in ("AET", "PEN") else 90
    goles = asis = am = roja = 0
    entra = sale = None
    visto = False
    for e in fx.get("events") or []:
        mi = (e.get("time") or {}).get("elapsed") or 0
        pl, asst = (e.get("player") or {}).get("id"), (e.get("assist") or {}).get("id")
        tipo, det = e.get("type"), e.get("detail") or ""
        if tipo == "Goal" and not ha._es_tanda(e):
            if pl == pid and "Missed" not in det and "Own" not in det:
                goles += 1; visto = True
            if asst == pid:
                asis += 1; visto = True
        elif tipo == "subst":
            if pl == pid:
                sale = mi; visto = True
            if asst == pid:
                entra = mi; visto = True
        elif tipo == "Card" and pl == pid:
            visto = True
            if "Yellow" in det:
                am += 1
            else:
                roja = mi or 1
    if not (titular or visto):
        return None
    if banca and not titular and entra is None and not goles and not asis:
        return None
    ini = entra if entra is not None else 0
    fin = sale if sale is not None else total
    if roja:
        fin = min(fin, roja)
    m = max(1, fin - ini)
    return {"minutes": m, "goals": goles, "assists": asis, "yellow": am, "red": 1 if roja else 0,
            "fuente": "alineacion" if (titular or banca) else "eventos"}


def marcador(fx, tid):
    g = fx.get("goals") or {}
    h = (fx["teams"]["home"] or {}).get("id") == tid
    return (g.get("home") if h else g.get("away")) or 0, (g.get("away") if h else g.get("home")) or 0


def calif_parcial(part, rol, favor, contra):
    s = {"games": {"minutes": part["minutes"]}, "goals": {"total": part["goals"], "assists": part["assists"]},
         "cards": {"yellow": part["yellow"], "red": part["red"]}}
    ctx = {"goles_favor": favor, "goles_contra": contra}
    r = CL.calcular(s, rol, ctx)
    if r is None:
        return None
    return {"rating": r, "calif_fija": True, "parcial": True, "rating_api": None, "pesos": CL.explicar(s, rol, ctx)}


def ctx_viejo(fx, tid):
    """El marcador con el que se calculaba ANTES de corregir _marcador (solo goles recibidos por los porteros):
    si da distinto al real, esa calificación salió con el resultado equivocado."""
    favor = contra = 0
    for t in fx.get("players") or []:
        propio = (t.get("team") or {}).get("id") == tid
        for pl in t.get("players") or []:
            st = (pl.get("statistics") or [{}])[0]
            if ((st.get("games") or {}).get("position") or "") == "G":
                rec = CL._g(st, "goals", "conceded")
                if propio:
                    contra += rec
                else:
                    favor += rec
    return favor, contra


# ───────────────────────────── revisión por partido ─────────────────────────────

def revisar_partido(config, stats, aud, p_ag, fx_cache):
    fid = p_ag["fixture_id"]
    jugadores = {p["key"]: p for p in config["players"]}
    for j in p_ag.get("jugadores") or []:
        key, tkey = j["key"], j["team_key"]
        p = jugadores.get(key)
        if not p:
            continue
        clave = f"{key}:{fid}"
        estado = aud["partidos"].get(clave) or {}
        if estado.get("ok") and not estado.get("parcial"):
            continue
        t = next((x for x in p["targets"] if x["key"] == tkey), None)
        if not t or not t.get("team_id"):
            continue
        tid = t["team_id"]
        if fid not in fx_cache:
            res = ha.api("/fixtures", {"id": fid})
            fx_cache[fid] = res[0] if res else None
        fx = fx_cache[fid]
        if not fx:
            continue
        if fx["fixture"]["status"]["short"] not in FIN:
            continue
        nombre = ha.display_name(p)
        partido = f"{ha.nombre_es(fx['teams']['home']['name'])} {fx['goals']['home']}-{fx['goals']['away']} " \
                  f"{ha.nombre_es(fx['teams']['away']['name'])}"
        part = participacion(fx, p.get("player_id"), tid)
        if not part:
            # Solo se da por revisado si la fuente ya publicó alineación o estadísticas; si no, se vuelve a ver
            # en la siguiente corrida (hasta 3 días): a veces las publica horas después.
            if fx.get("lineups") or fx.get("players"):
                aud["partidos"][clave] = {"ok": True, "jugo": False, "revisado": ahora().isoformat()}
            continue
        entry = stats["players"].setdefault(key, {"seasons": {}, "applied_fixtures": {}})
        ko = datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).isoformat()

        # 2. Último partido
        u = entry.get("ultimo_partido") or {}
        if (u.get("kickoff") or "") < ko:
            import hormi_ultimo
            ha.save_json(ha.STATS_FILE, stats)
            hormi_ultimo.actualizar(config, solo=key)
            stats.update(ha.load_json(ha.STATS_FILE, {}))
            entry = stats["players"][key]
            u = entry.get("ultimo_partido") or {}
            if u.get("fixture_id") == fid:
                hecho(f"ultimo:{clave}", f"{nombre}: {partido} no aparecía como su último partido — agregado")
            elif (u.get("kickoff") or "") < ko:
                problema(f"ultimo:{clave}", f"{nombre}: jugó {partido} ({part['minutes']}') pero no se pudo poner "
                                            f"como su último partido")

        # 3. Temporada
        aplicados = set((entry.get("applied_fixtures") or {}).get(tkey) or [])
        if fid not in aplicados and not estado.get("historial"):
            ha.save_json(ha.STATS_FILE, stats)
            if correr("hormi_historial.py", "--jugador", key):
                stats.update(ha.load_json(ha.STATS_FILE, {}))
                entry = stats["players"][key]
                estado["historial"] = True
                # Queda anotado como contado (el motor no lo vuelve a sumar si lo ve después).
                entry.setdefault("applied_fixtures", {}).setdefault(tkey, []).append(fid)
                hecho(f"temporada:{clave}", f"{nombre}: {partido} no estaba confirmado en su temporada — "
                                            f"temporada recalculada con la fuente")
            else:
                problema(f"temporada:{clave}", f"{nombre}: no se pudo rehacer su historial tras {partido}")
            u = entry.get("ultimo_partido") or {}

        # 4 y 5. Calificación (solo la del último partido es la que muestra la app)
        if u.get("fixture_id") == fid:
            st = u.get("stats") or {}
            favor, contra = marcador(fx, tid)
            rol = CL.ROL_JUGADOR.get(key) or "MC"
            completa = None
            if fx.get("players"):
                completa = ha.stats_jugador(fx["players"], p.get("player_id"), tid, key)
            if not st.get("rating"):
                nueva = completa or calif_parcial(part, rol, favor, contra)
                if nueva:
                    u["stats"], u["stats_v"] = nueva, 2
                    u.pop("sin_stats", None)
                    u.pop("calif_reintento", None)
                    hecho(f"calif:{clave}", f"{nombre}: {partido} no tenía calificación — "
                                            f"{nueva['rating']}{' (parcial: la fuente no trae sus estadísticas)' if nueva.get('parcial') else ''}")
                else:
                    problema(f"calif:{clave}", f"{nombre}: {partido} sin calificación y no se pudo calcular")
            elif st.get("parcial") and completa:
                u["stats"] = completa
                hecho(f"calif_completa:{clave}", f"{nombre}: {partido} — ya hay estadísticas completas, calificación "
                                                 f"{st['rating']} → {completa['rating']}")
            elif completa and not st.get("manual") and not st.get("parcial") and fx.get("players") \
                    and not estado.get("marcador_ok"):
                if ctx_viejo(fx, tid) != (favor, contra) and completa["rating"] != st.get("rating"):
                    u["stats"] = completa
                    hecho(f"calif_marcador:{clave}", f"{nombre}: {partido} — su calificación se había calculado con el "
                                                     f"marcador equivocado: {st.get('rating')} → {completa['rating']}")
                estado["marcador_ok"] = True
            # Tarjeta para compartir (24 h)
            try:
                if datetime.fromisoformat(u["kickoff"]) >= ahora() - timedelta(hours=30):
                    ha.tarjeta_partido_para(p, u, dict(u))
            except Exception as err:
                ha.log(f"⚠️  tarjeta de {key}: {err}")
            estado["parcial"] = bool((u.get("stats") or {}).get("parcial")) and \
                ahora() - datetime.fromisoformat(ko) < timedelta(hours=48)

        # 6. Video de sus goles
        if part["goals"] and ahora() - datetime.fromisoformat(ko) < timedelta(hours=24) and not estado.get("video"):
            vg = (ha.load_json(Path("videos_gol.json"), {}) or {}).get("goles") or {}
            if not any(k.startswith(f"{key}:{tkey}:{fid}:") for k in vg):
                ha.save_json(ha.STATS_FILE, stats)
                if correr("hormi_videos.py", "--recuperar", f"{key}:{fid}"):
                    hecho(f"video:{clave}", f"{nombre}: no se había buscado el video de su gol en {partido} — buscado")
                stats.update(ha.load_json(ha.STATS_FILE, {}))
            estado["video"] = True

        estado.update(ok=True, jugo=True, revisado=ahora().isoformat())
        aud["partidos"][clave] = estado
        ha.save_json(ha.STATS_FILE, stats)


# ───────────────────────────── agenda, bajas, hitos ─────────────────────────────

def revisar_agenda(ag):
    """Partidos que ya debieron terminar pero siguen con horario / "en vivo" → se piden y se corrigen."""
    lim = ahora() - timedelta(hours=2, minutes=30)
    atorados = [p for p in ag.get("partidos") or []
                if p.get("status") not in CERRADOS and datetime.fromisoformat(p["kickoff"]) < lim]
    if not atorados:
        return False
    cambio = False
    ids = [str(p["fixture_id"]) for p in atorados]
    nuevos = {}
    for i in range(0, len(ids), 20):
        for fx in ha.api("/fixtures", {"ids": "-".join(ids[i:i + 20])}) or []:
            nuevos[fx["fixture"]["id"]] = fx
    for p in atorados:
        fx = nuevos.get(p["fixture_id"])
        if not fx:
            continue
        st = fx["fixture"]["status"]["short"]
        g = fx.get("goals") or {}
        if st != p.get("status") or g.get("home") != p.get("home_goals") or g.get("away") != p.get("away_goals"):
            hecho(f"agenda:{p['fixture_id']}:{st}",
                  f"Agenda: {p['home']} vs {p['away']} seguía como '{p.get('status')}' — ahora {st} "
                  f"{g.get('home')}-{g.get('away')}")
            p.update(status=st, home_goals=g.get("home"), away_goals=g.get("away"), penales=ha._penales(fx),
                     kickoff=datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).isoformat())
            cambio = True
        elif st in ("NS", "TBD") and datetime.fromisoformat(p["kickoff"]) < ahora() - timedelta(hours=6):
            nombres = ", ".join(j["key"] for j in p.get("jugadores") or [])
            problema(f"agenda_ns:{p['fixture_id']}",
                     f"Agenda: {p['home']} vs {p['away']} ({p['kickoff'][:16].replace('T', ' ')}, {nombres}) — la fuente "
                     f"lo sigue marcando 'sin empezar' horas después. ¿Se jugó, se pospuso o cambió de fecha?")
        # PST/SUSP/ABD: pospuesto de verdad; la agenda lo muestra así hasta que la fuente publique la nueva fecha.
    return cambio


def revisar_bajas(config, stats):
    disp = ha.load_json(Path("disponibilidad.json"), {}) or {}
    viejas = []
    for key, d in (disp.get("players") or {}).items():
        if d.get("manual"):
            continue
        u = ((stats.get("players") or {}).get(key) or {}).get("ultimo_partido") or {}
        # La baja era para un partido que ya pasó, o ya volvió a jugar después de que se puso.
        if (d.get("kickoff") or "9") < (ahora() - timedelta(hours=6)).isoformat() or \
                ((u.get("minutes") or 0) > 0 and (u.get("kickoff") or "") >= (d.get("kickoff") or "9")):
            viejas.append(key)
    if not viejas:
        return
    correr("hormi_disponibilidad.py", "--sin-avisos")
    disp2 = (ha.load_json(Path("disponibilidad.json"), {}) or {}).get("players") or {}
    nombres = {p["key"]: ha.display_name(p) for p in config["players"]}
    for key in viejas:
        d0 = (disp.get("players") or {}).get(key) or {}
        if key in disp2 and disp2[key].get("fixture_id") == d0.get("fixture_id"):
            problema(f"baja:{key}:{d0.get('fixture_id')}", f"{nombres.get(key, key)}: sigue marcado '{d0.get('estado')}' "
                                                           f"para {d0.get('partido')} aunque ese partido ya pasó")
        else:
            hecho(f"baja:{key}:{d0.get('fixture_id')}", f"{nombres.get(key, key)}: tenía '{d0.get('estado')}' viejo "
                                                        f"({d0.get('partido')}) — corregido")


def revisar_hitos(stats):
    r = ha.load_json(Path("rachas.json"), {}) or {}
    ult = max(((v.get("ultimo_partido") or {}).get("kickoff") or "" for v in (stats.get("players") or {}).values()),
              default="")
    if not ult:
        return
    fin_ult = (datetime.fromisoformat(ult) + timedelta(hours=2, minutes=15)).isoformat()
    if (r.get("updated_at") or "") < fin_ult and ahora().isoformat() > (datetime.fromisoformat(fin_ult) + timedelta(minutes=10)).isoformat():
        if correr("hormi_rachas.py", "--sin-avisos"):
            hecho(f"hitos:{ult}", "Hitos y rachas no se habían actualizado tras el último partido — actualizados")


# ───────────────────────────── correo ─────────────────────────────

def enviar_correo(asunto, cuerpo):
    usuario, clave = os.environ.get("SOPORTE_SMTP_USER"), os.environ.get("SOPORTE_SMTP_PASS")
    if usuario and clave:
        try:
            msg = MIMEText(cuerpo, "plain", "utf-8")
            msg["Subject"], msg["From"], msg["To"] = asunto, f"Auditor Legión MX <{usuario}>", CORREO
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
                s.login(usuario, clave)
                s.send_message(msg)
            ha.log(f"📧 correo enviado: {asunto}")
            return True
        except Exception as err:
            ha.log(f"⚠️  no se pudo mandar el correo: {err}")
    # Respaldo mientras no esté configurado el correo: un aviso (issue) en GitHub, que también llega por correo.
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if token and repo:
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues", method="POST",
                                         data=json.dumps({"title": asunto, "body": cuerpo, "labels": ["auditor"]}).encode(),
                                         headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
            urllib.request.urlopen(req, timeout=15).close()
            ha.log(f"📝 aviso en GitHub: {asunto}")
            return True
        except Exception as err:
            ha.log(f"⚠️  tampoco se pudo crear el aviso en GitHub: {err}")
    return False


def reportar(aud, prueba=False):
    hoy = ahora().date().isoformat()
    vistos = set(aud.get("avisados") or [])
    nuevos_prob = [(k, t) for k, t in problemas if k not in vistos]
    for k, t in hechos:
        if k not in vistos:
            aud.setdefault("bitacora", []).append({"fecha": ahora().isoformat(), "txt": t})
            vistos.add(k)
    # Lo que NO se pudo corregir: correo en ese momento.
    if nuevos_prob:
        cuerpo = "El auditor encontró esto y NO lo pudo corregir solo:\n\n" + "\n".join(f"• {t}" for _, t in nuevos_prob)
        cuerpo += "\n\nCorrección sugerida: avísale a Claude con este correo."
        if enviar_correo(f"⚠️ Legión MX: {len(nuevos_prob)} problema(s) por revisar", cuerpo):
            vistos.update(k for k, _ in nuevos_prob)
    # Resumen diario de lo que se corrigió solo (si hubo algo).
    pend = [b for b in aud.get("bitacora") or [] if not b.get("enviado")]
    if pend and (prueba or (ahora().hour >= HORA_RESUMEN and aud.get("resumen_dia") != hoy)
                 or any(b["fecha"][:10] < hoy for b in pend)):
        cuerpo = "Esto corrigió el auditor solo, sin mandar notificaciones a los usuarios:\n\n" + \
                 "\n".join(f"• {b['fecha'][5:16].replace('T', ' ')} · {b['txt']}" for b in pend)
        if enviar_correo(f"✅ Legión MX: {len(pend)} corrección(es) automática(s)", cuerpo):
            for b in pend:
                b["enviado"] = True
            aud["resumen_dia"] = hoy
    aud["avisados"] = sorted(vistos)[-500:]
    aud["bitacora"] = [b for b in aud.get("bitacora") or [] if not b.get("enviado") or b["fecha"][:10] >= hoy][-200:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--correo-prueba", action="store_true", help="solo manda un correo de prueba")
    args = ap.parse_args()
    if args.correo_prueba:
        ok = enviar_correo("🧪 Legión MX: prueba del auditor", "Si te llegó este correo, el auditor ya puede avisarte.")
        sys.exit(0 if ok else 1)
    config = ha.load_config()
    archivados = set(config.get("archivados") or [])
    aud = ha.load_json(OUT, {}) or {}
    aud.setdefault("partidos", {})
    stats = ha.load_json(ha.STATS_FILE, {"players": {}})
    ag = ha.load_json(Path("agenda.json"), {}) or {}

    try:
        if revisar_agenda(ag):
            ag["updated_at"] = ahora().isoformat()
            ha.save_json(Path("agenda.json"), ag)
    except Exception as err:
        problema("err:agenda", f"Error revisando la agenda: {err}")

    lim = ahora() - timedelta(days=DIAS)
    fx_cache = {}
    for p_ag in ag.get("partidos") or []:
        ko = datetime.fromisoformat(p_ag["kickoff"])
        if p_ag.get("status") not in FIN or ko < lim:
            continue
        p_ag = dict(p_ag, jugadores=[j for j in p_ag.get("jugadores") or [] if j["key"] not in archivados])
        try:
            revisar_partido(config, stats, aud, p_ag, fx_cache)
        except Exception as err:
            problema(f"err:{p_ag['fixture_id']}", f"Error revisando {p_ag['home']} vs {p_ag['away']}: {err}")

    for fn, nombre in ((lambda: revisar_bajas(config, stats), "bajas"), (lambda: revisar_hitos(stats), "hitos")):
        try:
            fn()
        except Exception as err:
            problema(f"err:{nombre}", f"Error revisando {nombre}: {err}")

    # Lo revisado hace más de una semana ya no se guarda.
    corte = (ahora() - timedelta(days=7)).isoformat()
    aud["partidos"] = {k: v for k, v in aud["partidos"].items() if (v.get("revisado") or "9") >= corte}
    reportar(aud)
    aud["updated_at"] = ahora().isoformat()
    ha.save_json(OUT, aud)
    ha.log(f"auditor: {len(hechos)} corrección(es) · {len(problemas)} problema(s)")


if __name__ == "__main__":
    from hormi_pausa import salir_si_pausa
    salir_si_pausa()
    main()

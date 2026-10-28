#!/usr/bin/env python3
"""
Resumen semanal de la Legión ("Jornada de la Legión"): corre cada lunes por la mañana con la semana de lunes a
domingo que acaba de terminar.

Por cada partido terminado de los equipos que sigue la app (clubes y Selección) se leen los números de cada
jugador en /fixtures/players (minutos, goles, asistencias y calificación). Si la liga no los publica (p. ej.
Copa de Rusia) se calculan con los eventos y la alineación del partido (sin calificación).

Produce:
  - semana.json: totales (goles, asistencias, partidos, minutos), MVP, goleadores y la URL de la imagen.
  - semana/semana_<lunes>.jpg: imagen 1080x1350 lista para compartir (WhatsApp / historias).
  - Un aviso "📊 Jornada de la Legión" (tipo "semana").

MVP: mejor calificación promedio (con al menos 60 minutos en la semana); si empatan, más goles + asistencias.
Costo: ~1 petición por equipo + 1 por partido (≈30-40 a la semana).

Uso:
  python hormi_semana.py                      # semana pasada, con aviso
  python hormi_semana.py --hasta 2026-10-12   # la semana que termina ese domingo
  python hormi_semana.py --sin-aviso
"""
import argparse
import hashlib
import json
import os
from datetime import date, datetime, timedelta

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import hormi_alertas as ha
import hormi_historial as hh

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "semana.json")
OUT_DIR = os.path.join(HERE, "semana")
RAW_BASE = "https://raw.githubusercontent.com/kilers0opa-ui/app-hormi/main/"
FB = os.path.join(HERE, "fondos_assets", "Poppins-Bold.ttf")
FM = os.path.join(HERE, "fondos_assets", "Poppins-Medium.ttf")
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
W, H = 1080, 1350
DORADO = (242, 169, 59)


def rango(domingo=None):
    """(lunes, domingo) de la semana a resumir: la que terminó el domingo pasado (o la de --hasta)."""
    if domingo is None:
        hoy = datetime.now(ha.LOCAL_TZ).date()
        domingo = hoy - timedelta(days=hoy.weekday() + 1)
    return domingo - timedelta(days=6), domingo


def titulo_rango(ini, fin):
    if ini.month == fin.month:
        return f"{ini.day} – {fin.day} {MESES[fin.month - 1]} {fin.year}"
    return f"{ini.day} {MESES[ini.month - 1]} – {fin.day} {MESES[fin.month - 1]} {fin.year}"


def _fecha(fx):
    return datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).date()


AUSENCIAS = {}   # jugador -> motivo si su club jugó y él no tuvo minutos: "banca" | "no_convocado"


def recolectar(config, ini, fin):
    """{jugador: [apariciones]} de la semana. De paso anota en AUSENCIAS por qué no jugó el que no jugó."""
    por_equipo, stats_partido = {}, {}
    out = {}
    AUSENCIAS.clear()
    for p in config["players"]:
        pid = p.get("player_id")
        for t in p["targets"]:
            tid = t.get("team_id")
            if not tid or not pid:
                continue
            if tid not in por_equipo:
                res = ha.api("/fixtures", {"team": tid, "last": 6}) or []
                por_equipo[tid] = [fx for fx in res if ini <= _fecha(fx) <= fin
                                   and fx["fixture"]["status"]["short"] in ha.FINISHED]
            for fx in por_equipo[tid]:
                fid = fx["fixture"]["id"]
                if fid not in stats_partido:
                    stats_partido[fid] = ha.api("/fixtures/players", {"fixture": fid}) or []
                res = stats_partido[fid]
                linea = next((pl["statistics"][0] for team in res for pl in team.get("players") or []
                              if pl["player"]["id"] == pid), None)
                if res:
                    if not linea or not (linea.get("games") or {}).get("minutes"):
                        if t.get("kind") != "seleccion":
                            # en la lista del partido sin minutos = banca; fuera de la lista = no convocado
                            motivo = "banca" if linea else "no_convocado"
                            if AUSENCIAS.get(p["key"]) != "banca":
                                AUSENCIAS[p["key"]] = motivo
                        continue   # no jugó
                    g = (linea.get("goals") or {}).get("total") or 0
                    a = (linea.get("goals") or {}).get("assists") or 0
                    mins = linea["games"]["minutes"]
                    # Calificación Legión MX (la nuestra); si no se puede calcular, la de la API.
                    try:
                        import hormi_calif_legion as CL
                        rating = CL.para_pid(res, pid, CL.ROL_JUGADOR.get(p["key"]) or CL.rol_de_api(
                            (linea.get("games") or {}).get("position")))
                    except Exception:
                        rating = None
                    rating = rating or ha._num((linea.get("games") or {}).get("rating"))
                else:
                    inv = hh._fixture_involvement(pid, fx)   # liga sin números por jugador: eventos + alineación
                    if not inv:
                        continue
                    g, a, mins, rating = inv["goals"], inv["assists"], inv["minutes"], None
                home, away = fx["teams"]["home"], fx["teams"]["away"]
                out.setdefault(p["key"], []).append({
                    "fixture_id": fid, "fecha": _fecha(fx).isoformat(), "equipo": t["label"],
                    "seleccion": t.get("kind") == "seleccion", "min": mins, "goles": g, "asist": a,
                    "rating": rating, "partido": f"{ha.nombre_es(home['name'])} {fx['goals']['home']}-"
                                                 f"{fx['goals']['away']} {ha.nombre_es(away['name'])}"})
    return out


def resumir(config, apariciones):
    nombres = {p["key"]: ha.display_name(p) for p in config["players"]}
    jug = []
    for k, aps in apariciones.items():
        ratings = [x["rating"] for x in aps if x["rating"]]
        equipos = []
        for x in aps:
            if x["goles"] and x["equipo"] not in equipos:
                equipos.append(x["equipo"])
        jug.append({"key": k, "name": nombres.get(k, k), "partidos": len(aps), "min": sum(x["min"] for x in aps),
                    "goles": sum(x["goles"] for x in aps), "asist": sum(x["asist"] for x in aps),
                    "rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
                    "equipos_gol": equipos or [aps[0]["equipo"]]})
    if not jug:
        return None
    # Todos los jugadores (no archivados), para ver por qué el MVP fue ese: los que jugaron con su promedio
    # (o N/A si la liga no publica calificación) y los que no, con el motivo.
    archivados = set(config.get("archivados") or [])
    jugaron = {j["key"]: j for j in jug}
    todos = []
    for p in config["players"]:
        if p["key"] in archivados:
            continue
        if p["key"] in jugaron:
            j = jugaron[p["key"]]
            todos.append({"key": j["key"], "name": j["name"], "estado": "jugo", "rating": j["rating"],
                          "partidos": j["partidos"], "min": j["min"], "goles": j["goles"], "asist": j["asist"]})
        else:
            todos.append({"key": p["key"], "name": nombres.get(p["key"], p["key"]),
                          "estado": AUSENCIAS.get(p["key"], "sin_partido"), "rating": None,
                          "partidos": 0, "min": 0, "goles": 0, "asist": 0})
    orden_estado = {"jugo": 0, "banca": 1, "no_convocado": 2, "sin_partido": 3}
    todos.sort(key=lambda j: (orden_estado.get(j["estado"], 9), -(j["rating"] or 0), -j["min"], j["name"]))
    cand = [j for j in jug if j["min"] >= 60] or jug
    mvp = max(cand, key=lambda j: (j["rating"] or 0, j["goles"] + j["asist"], j["min"]))
    goleadores = sorted([j for j in jug if j["goles"]], key=lambda j: (-j["goles"], -j["asist"], j["name"]))
    return {
        "totales": {"goles": sum(j["goles"] for j in jug), "asistencias": sum(j["asist"] for j in jug),
                    "partidos": sum(j["partidos"] for j in jug), "minutos": sum(j["min"] for j in jug),
                    "jugaron": len(jug)},
        "mvp": {k: mvp[k] for k in ("key", "name", "partidos", "min", "goles", "asist", "rating")},
        "goleadores": [{"key": j["key"], "name": j["name"], "goles": j["goles"], "equipo": " / ".join(j["equipos_gol"])}
                       for j in goleadores],
        "jugadores": sorted(jug, key=lambda j: (-(j["goles"] + j["asist"]), -j["min"])),
        "todos": todos,
        "criterio_mvp": "Mejor calificación promedio entre los que jugaron 60' o más; si empatan, más goles + asistencias.",
    }


# ─────────────────────────────────────────────────────────── imagen
def _font(path, size):
    return ImageFont.truetype(path, size)


def _estrella(d, cx, cy, r, fill):
    import math
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    d.polygon(pts, fill=fill)


def _miles(n):
    return f"{n:,}"


def _avatar(key, size):
    """Foto redonda del jugador (tarjetas_assets/avatar_<key>.jpg) con borde dorado opcional."""
    for path in (os.path.join(HERE, "tarjetas_assets", f"avatar_{key}.jpg"),
                 os.path.join(HERE, "fondos_fuente", key, "avatar.jpg")):
        if os.path.exists(path):
            av = Image.open(path).convert("RGB")
            lado = min(av.size)
            av = av.crop(((av.width - lado) // 2, 0, (av.width - lado) // 2 + lado, lado)).resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size * 4, size * 4), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
            return av, mask.resize((size, size), Image.LANCZOS)
    return None, None


def _club(config, key):
    p = next((p for p in config["players"] if p["key"] == key), None) or {}
    t = next((t for t in p.get("targets") or [] if t.get("kind") == "club"), None) or {}
    return ha.nombre_es(t.get("label") or "") if t else ""


def _plural(n, uno, varios):
    return f"{n} {uno if n == 1 else varios}"


def imagen(res, ini, fin, config=None):
    """Imagen para compartir (1080x1350): MVP con SUS números y por qué lo es; luego los totales de TODA la Legión,
    con etiquetas claras (para que no parezca que el MVP jugó 16 partidos); y los 5 mejor calificados."""
    config = config or ha.load_config()
    archivados = set(config.get("archivados") or [])
    total_jug = len([p for p in config["players"] if p["key"] not in archivados])
    BLANCO, GRIS, GRIS2 = (255, 255, 255), (207, 214, 222), (154, 164, 174)
    a = np.linspace(0, 1, H)[:, None, None]
    top, mid, bot = np.array((15, 61, 38)), np.array((16, 24, 32)), np.array((58, 15, 21))
    arr = np.where(a < 0.45, top * (1 - a / 0.45) + mid * (a / 0.45), mid * (1 - (a - 0.45) / 0.55) + bot * ((a - 0.45) / 0.55))
    img = Image.fromarray((arr * np.ones((H, W, 3))).astype("uint8")).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for i in range(-H, W, 120):
        od.line([(i, H), (i + H, 0)], fill=(255, 255, 255, 7), width=40)
    img = Image.alpha_composite(img, ov)
    x0 = 64
    t, m = res["totales"], res["mvp"]
    jug = [j for j in res.get("todos") or [] if j.get("estado") == "jugo"]
    con_nota = [j for j in jug if j.get("rating")]
    prom = (sum(j["rating"] for j in con_nota) / len(con_nota)) if con_nota else None

    # Capa translúcida: tarjeta del MVP y recuadros de totales
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(capa)
    y_mvp, h_mvp = 218, 254
    cd.rounded_rectangle([x0, y_mvp, W - x0, y_mvp + h_mvp], 28, fill=(242, 169, 59, 26), outline=(242, 169, 59, 235), width=3)
    y_tot = y_mvp + h_mvp + 74
    bw, bh, gap = (W - 2 * x0 - 3 * 16) // 4, 152, 16
    for i in range(4):
        x = x0 + i * (bw + gap)
        cd.rounded_rectangle([x, y_tot, x + bw, y_tot + bh], 22, fill=(255, 255, 255, 16), outline=(255, 255, 255, 22), width=1)
    img = Image.alpha_composite(img, capa)
    d = ImageDraw.Draw(img)

    # Encabezado
    d.text((x0, 56), "JORNADA DE LA LEGIÓN", font=_font(FB, 24), fill=DORADO)
    d.text((x0, 88), titulo_rango(ini, fin).upper(), font=_font(FB, 70), fill=BLANCO)

    # MVP
    av, mask = _avatar(m["key"], 150)
    if av:
        d.ellipse([x0 + 26, y_mvp + 48, x0 + 26 + 156, y_mvp + 48 + 156], fill=DORADO)
        img.paste(av, (x0 + 29, y_mvp + 51), mask)
    tx = x0 + 214
    d.text((tx, y_mvp + 30), "MVP DE LA SEMANA", font=_font(FB, 22), fill=DORADO)
    d.text((tx, y_mvp + 58), m["name"], font=_font(FB, 46 if len(m["name"]) < 20 else 38), fill=BLANCO)
    club = _club(config, m["key"])
    if club:
        d.text((tx, y_mvp + 116), club, font=_font(FM, 22), fill=GRIS)
    x = tx
    yv = y_mvp + 150
    for v, lbl in ((str(m["partidos"]), " PJ"), (f"{_miles(m['min'])}'", ""), (str(m["goles"]), " gol" if m["goles"] == 1 else " goles"),
                   (str(m["asist"]), " asist.")):
        d.text((x, yv), v, font=_font(FB, 36), fill=BLANCO)
        x += d.textlength(v, font=_font(FB, 36))
        if lbl:
            d.text((x, yv + 10), lbl, font=_font(FM, 24), fill=GRIS)
            x += d.textlength(lbl, font=_font(FM, 24))
        x += 24
    if m.get("rating"):
        _estrella(d, x + 16, yv + 22, 17, DORADO)
        d.text((x + 40, yv), f"{m['rating']:.1f}", font=_font(FB, 36), fill=DORADO)
    por_que = "Mejor calificación de la semana" + (f"  ·  promedio de la Legión: {prom:.1f}" if prom else "")
    d.text((tx, y_mvp + 206), por_que, font=_font(FM, 19), fill=GRIS2)

    # Totales de toda la Legión
    d.text((x0, y_tot - 44), "TODA LA LEGIÓN ESTA SEMANA", font=_font(FB, 21), fill=GRIS)
    cajas = [(str(t.get("jugaron", len(jug))), "JUGARON", f"de {total_jug} mexicanos"), (str(t["goles"]), "GOLES", ""),
             (str(t["asistencias"]), "ASISTENCIAS", ""), (_miles(t["minutos"]) + "'", "MINUTOS", "entre todos")]
    for i, (v, lbl, sub) in enumerate(cajas):
        cx = x0 + i * (bw + gap) + bw // 2
        d.text((cx, y_tot + 52), v, font=_font(FB, 58), fill=BLANCO, anchor="mm")
        d.text((cx, y_tot + 104), lbl, font=_font(FM, 21), fill=GRIS, anchor="mm")
        if sub:
            d.text((cx, y_tot + 132), sub, font=_font(FM, 16), fill=GRIS2, anchor="mm")

    # Los 5 mejor calificados (sin calificación: por goles+asistencias y minutos)
    y = y_tot + bh + 46
    d.text((x0, y), "LOS MEJORES CALIFICADOS" if con_nota else "LOS QUE MÁS JUGARON", font=_font(FB, 21), fill=GRIS)
    y += 38
    orden = sorted(jug, key=lambda j: (-(j.get("rating") or 0), -(j["goles"] + j["asist"]), -j["min"]))[:5]
    fila_h = (H - 110 - y) // max(5, len(orden))
    fila_h = min(fila_h, 112)
    for n, j in enumerate(orden):
        yy = y + n * fila_h
        d.line([(x0, yy), (W - x0, yy)], fill=(62, 58, 66), width=1)
        d.text((x0 + 4, yy + fila_h // 2), str(n + 1), font=_font(FB, 30), fill=DORADO if n == 0 else GRIS2, anchor="lm")
        av2, mask2 = _avatar(j["key"], 62)
        if av2:
            img.paste(av2, (x0 + 46, yy + (fila_h - 62) // 2), mask2)
        nx = x0 + 128
        d.text((nx, yy + 16), j["name"], font=_font(FB, 30), fill=BLANCO)
        partes = [p for p in (_club(config, j["key"]), f"{j['partidos']} PJ", f"{j['min']}'",
                              _plural(j["goles"], "gol", "goles") if j["goles"] else "",
                              _plural(j["asist"], "asist.", "asist.") if j["asist"] else "") if p]
        d.text((nx, yy + 54), "  ·  ".join(partes), font=_font(FM, 20), fill=GRIS2)
        if j.get("rating"):
            ancho = W - x0 - 110 - nx
            frac = max(0.06, min(1.0, (j["rating"] - 5.5) / 3))
            d.rounded_rectangle([nx, yy + 86, nx + ancho, yy + 93], 4, fill=(58, 56, 64))
            d.rounded_rectangle([nx, yy + 86, nx + int(ancho * frac), yy + 93], 4, fill=DORADO)
            d.text((W - x0, yy + fila_h // 2), f"{j['rating']:.1f}", font=_font(FB, 34), fill=DORADO, anchor="rm")

    d.text((x0, H - 74), "Legión MX", font=_font(FB, 26), fill=(225, 230, 236))
    d.text((W - x0, H - 74), "Mexicanos por el mundo", font=_font(FM, 24), fill=GRIS2, anchor="ra")
    return img.convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hasta", help="domingo de la semana a resumir (YYYY-MM-DD)")
    ap.add_argument("--sin-aviso", action="store_true")
    args = ap.parse_args()
    config = ha.load_config()
    ini, fin = rango(date.fromisoformat(args.hasta) if args.hasta else None)
    print(f"Semana {ini} → {fin}")
    res = resumir(config, recolectar(config, ini, fin))
    if not res:
        print("Ningún jugador jugó esta semana: no se publica resumen.")
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    nombre = f"semana_{ini.isoformat()}.jpg"
    img = imagen(res, ini, fin, config)
    img.save(os.path.join(OUT_DIR, nombre), "JPEG", quality=90, optimize=True)
    with open(os.path.join(OUT_DIR, nombre), "rb") as fh:
        ver = hashlib.sha1(fh.read()).hexdigest()[:8]
    # Solo se guardan las últimas 8 imágenes.
    for fn in sorted(f for f in os.listdir(OUT_DIR) if f.startswith("semana_"))[:-8]:
        os.remove(os.path.join(OUT_DIR, fn))
    salida = {"desde": ini.isoformat(), "hasta": fin.isoformat(), "titulo": titulo_rango(ini, fin),
              "generado": datetime.now(ha.LOCAL_TZ).isoformat(timespec="seconds"),
              "imagen": f"{RAW_BASE}semana/{nombre}?v={ver}", **res}
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(salida, fh, ensure_ascii=False, indent=1)
    t, m = res["totales"], res["mvp"]
    print(json.dumps({"totales": t, "mvp": m, "goleadores": res["goleadores"]}, ensure_ascii=False))
    if not args.sin_aviso:
        fcm = ha.FcmSender(config.get("fcm_project_id"))
        title = f"📊 Jornada de la Legión: {t['goles']} gol" + ("es" if t["goles"] != 1 else "") + " en la semana"
        body = (f"MVP: {m['name']}" + (f" (⭐ {m['rating']})" if m.get("rating") else "") +
                f" · {t.get('jugaron', 0)} jugaron · {_miles(t['minutos'])} minutos")
        print(f"📲 {title} — {body}")
        # Con avisos_hora_local: solo a las apps nuevas (tema hormi_local_v1), que lo muestran a las 9:00 de SU hora
        # (si allá ya pasaron las 9, al recibirlo). Sin el flag: a todas, al momento (el workflow espera a las 9:00 CDMX).
        if config.get("avisos_hora_local"):
            fcm.send("semana", title, body, None, None, None, extra={"mostrar_local": "09:00"}, topic=ha.LOCAL_TOPIC)
        else:
            fcm.send("semana", title, body, None, None, None)
    return 0


if __name__ == "__main__":
    from hormi_pausa import salir_si_pausa
    salir_si_pausa()
    raise SystemExit(main())

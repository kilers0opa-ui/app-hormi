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
                    rating = ha._num((linea.get("games") or {}).get("rating"))
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


def imagen(res, ini, fin):
    a = np.linspace(0, 1, H)[:, None, None]
    top, mid, bot = np.array((10, 61, 42)), np.array((11, 15, 20)), np.array((58, 13, 20))
    arr = np.where(a < 0.55, top * (1 - a / 0.55) + mid * (a / 0.55), mid * (1 - (a - 0.55) / 0.45) + bot * ((a - 0.55) / 0.45))
    img = Image.fromarray((arr * np.ones((H, W, 3))).astype("uint8")).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for i in range(-H, W, 150):
        od.line([(i, H), (i + H, 0)], fill=(255, 255, 255, 8), width=46)
    img = Image.alpha_composite(img, ov)
    d = ImageDraw.Draw(img)
    x0 = 72
    d.text((x0, 70), "JORNADA DE LA LEGIÓN", font=_font(FB, 30), fill=DORADO)
    rango_txt = titulo_rango(ini, fin).upper()
    d.text((x0, 112), rango_txt, font=_font(FB, 74), fill=(255, 255, 255))
    # 4 recuadros
    t = res["totales"]
    cajas = [(str(t["goles"]), "GOLES"), (str(t["asistencias"]), "ASISTENCIAS"),
             (str(t.get("jugaron", t["partidos"])), "JUGARON"), (_miles(t["minutos"]) + "'", "MINUTOS")]
    bw, bh, gap, y0 = (W - 2 * x0 - 24) // 2, 150, 24, 230
    # Lo translúcido se pinta en una capa aparte (ImageDraw no mezcla transparencias sobre la imagen).
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(capa)
    for i in range(4):
        x, y = x0 + (i % 2) * (bw + gap), y0 + (i // 2) * (bh + gap)
        cd.rounded_rectangle([x, y, x + bw, y + bh], 22, fill=(255, 255, 255, 20))
    ym = y0 + 2 * (bh + gap) + 6
    cd.rounded_rectangle([x0, ym, W - x0, ym + 170], 26, fill=(242, 169, 59, 38), outline=(242, 169, 59, 150), width=3)
    img = Image.alpha_composite(img, capa)
    d = ImageDraw.Draw(img)
    for i, (v, lbl) in enumerate(cajas):
        x, y = x0 + (i % 2) * (bw + gap), y0 + (i // 2) * (bh + gap)
        d.text((x + bw // 2, y + 62), v, font=_font(FB, 64), fill=(255, 255, 255), anchor="mm")
        d.text((x + bw // 2, y + 118), lbl, font=_font(FM, 24), fill=(207, 214, 222), anchor="mm")
    # MVP
    m = res["mvp"]
    y = ym
    av_path = os.path.join(HERE, "fondos_fuente", m["key"], "avatar.jpg")
    if os.path.exists(av_path):
        av = Image.open(av_path).convert("RGB").resize((124, 124), Image.LANCZOS)
        mask = Image.new("L", (124, 124), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, 123, 123], fill=255)
        img.paste(av, (x0 + 24, y + 23), mask)
    tx = x0 + 172
    d.text((tx, y + 26), "MVP DE LA SEMANA", font=_font(FB, 24), fill=DORADO)
    d.text((tx, y + 56), m["name"], font=_font(FB, 44), fill=(255, 255, 255))
    linea = [f"{m['partidos']} PJ", f"{_miles(m['min'])}'", f"{m['goles']} gol" + ("es" if m["goles"] != 1 else ""),
             f"{m['asist']} asist."]
    txt = "  ·  ".join(linea) + ("  ·  " if m.get("rating") else "")
    d.text((tx, y + 116), txt, font=_font(FM, 26), fill=(225, 230, 236))
    if m.get("rating"):
        sx = tx + d.textlength(txt, font=_font(FM, 26))
        _estrella(d, sx + 13, y + 133, 13, DORADO)
        d.text((sx + 32, y + 116), str(m["rating"]), font=_font(FB, 26), fill=DORADO)
    # Goleadores
    y += 210
    gol = res["goleadores"]
    if gol:
        d.text((x0, y), "GOLEADORES", font=_font(FB, 24), fill=(170, 179, 189))
        y += 44
        for gj in gol[:6]:
            veces = f" ×{gj['goles']}" if gj["goles"] > 1 else ""
            d.ellipse([x0 + 6, y + 14, x0 + 26, y + 34], fill=DORADO)
            d.text((x0 + 48, y), gj["name"] + veces, font=_font(FB, 32), fill=(255, 255, 255))
            w = d.textlength(gj["name"] + veces, font=_font(FB, 32))
            d.text((x0 + 48 + w, y + 4), f"  ·  {gj['equipo']}", font=_font(FM, 27), fill=(170, 179, 189))
            y += 52
        if len(gol) > 6:
            d.text((x0 + 48, y), f"y {len(gol) - 6} más", font=_font(FM, 26), fill=(170, 179, 189))
        y += 40
    else:
        d.text((x0, y), "Sin goles esta semana", font=_font(FM, 30), fill=(170, 179, 189))
        y += 84
    asis = sorted([j for j in res["jugadores"] if j["asist"]], key=lambda j: (-j["asist"], j["name"]))
    if asis and y < H - 190:
        d.text((x0, y), "ASISTENCIAS", font=_font(FB, 24), fill=(170, 179, 189))
        y += 44
        for aj in asis:
            if y > H - 140:
                break
            veces = f" ×{aj['asist']}" if aj["asist"] > 1 else ""
            d.ellipse([x0 + 6, y + 14, x0 + 26, y + 34], outline=DORADO, width=4)
            d.text((x0 + 48, y), aj["name"] + veces, font=_font(FB, 32), fill=(255, 255, 255))
            y += 52
    d.text((x0, H - 78), "Legión MX", font=_font(FB, 28), fill=(225, 230, 236))
    d.text((W - x0, H - 78), "Mexicanos por el mundo", font=_font(FM, 26), fill=(170, 179, 189), anchor="ra")
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
    img = imagen(res, ini, fin)
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
        # La app lo muestra a las 9:00 de la hora LOCAL del celular (si allá ya pasaron las 9, al recibirlo).
        fcm.send("semana", title, body, None, None, None, extra={"mostrar_local": "09:00"})
    return 0


if __name__ == "__main__":
    from hormi_pausa import salir_si_pausa
    salir_si_pausa()
    raise SystemExit(main())

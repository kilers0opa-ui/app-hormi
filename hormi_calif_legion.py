#!/usr/bin/env python3
"""
Calificación Legión MX: nuestra propia calificación del jugador en un partido (escala 3.0–10.0), con una fórmula
DISTINTA por posición:

  POR  portero          atajadas, goles recibidos, portería en cero, penales atajados, salida con el balón
  DFC  defensa central  duelos, entradas, intercepciones, bloqueos, que no lo regateen, pases, goles recibidos
  LAT  lateral          lo defensivo del central (con menos peso) + pases clave, regates, ida al ataque
  MC   medio central    pases (precisión y volumen), pases clave, recuperaciones, duelos
  MCO  medio ofensivo   pases clave, tiros, regates, goles/asistencias
  EXT  extremo          regates, pases clave, tiros, goles/asistencias
  DEL  delantero        goles, tiros a puerta, pases clave, duelos ganados

Base 6.0 (partido "normal"); cada acción suma o resta según la posición. Lo que se acumula (entradas, pases
clave...) cuenta tal cual: quien juega 20 min tiene menos acciones y su nota queda cerca de 6. Con menos de 10
minutos no se califica. El resultado del equipo mueve poco (±0.3). Todos los pesos están en PESOS y la escala se
ajusta con datos reales (hormi_calif_datos.py → calif_datos.json).

calcular(stats_api, rol, contexto) → float | None
  stats_api: estadísticas del jugador tal como vienen en /fixtures/players (o aplanadas "grupo_campo").
  contexto: {"goles_favor": n, "goles_contra": n} del partido.
"""

ROLES = ("POR", "DFC", "LAT", "MC", "MCO", "EXT", "DEL")

# Posición de cada jugador de la app (la API solo dice G/D/M/F; esto distingue central de lateral, etc.).
ROL_JUGADOR = {
    "armando": "DEL", "raul": "DEL", "gimenez": "DEL", "quinones": "EXT", "camberos": "EXT",
    "mora": "MCO", "fimbres": "MCO", "sandoval": "MCO", "obed": "MC", "fidalgo": "MC", "luis_chavez": "MC",
    "vasquez": "DFC", "montes": "DFC", "campillo": "DFC", "chavez": "LAT", "huescas": "LAT",
}

#               POR    DFC    LAT    MC     MCO    EXT    DEL
_T = {
    "gol":        (2.5,   1.6,   1.5,   1.4,   1.35,  1.35,  1.35),
    "asistencia": (1.2,   1.0,   0.95,  0.9,   0.9,   0.9,   0.85),
    "pase_clave": (0.10,  0.12,  0.16,  0.17,  0.18,  0.17,  0.14),
    "tiro_puerta": (0.0,  0.10,  0.10,  0.12,  0.13,  0.13,  0.15),
    "tiro_fuera": (0.0,   0.00,  0.00,  0.01,  0.02,  0.02,  0.02),
    "regate_ok":  (0.0,   0.06,  0.09,  0.08,  0.10,  0.10,  0.08),
    "regate_mal": (0.0,  -0.05, -0.03, -0.03, -0.02, -0.02, -0.02),
    "duelo_gan":  (0.03,  0.07,  0.06,  0.05,  0.04,  0.04,  0.05),
    "duelo_per":  (-0.03, -0.07, -0.05, -0.04, -0.03, -0.03, -0.03),
    "entrada":    (0.0,   0.10,  0.09,  0.09,  0.07,  0.06,  0.05),
    "intercep":   (0.05,  0.12,  0.10,  0.10,  0.07,  0.06,  0.05),
    "bloqueo":    (0.05,  0.12,  0.09,  0.07,  0.05,  0.04,  0.03),
    "lo_regatean": (0.0, -0.10, -0.08, -0.06, -0.04, -0.03, -0.02),
    "falta_rec":  (0.0,   0.03,  0.04,  0.04,  0.05,  0.05,  0.05),
    "falta_com":  (-0.05, -0.05, -0.05, -0.05, -0.04, -0.04, -0.04),
    "fuera_lugar": (0.0,  0.0,   -0.03, -0.03, -0.04, -0.04, -0.05),
    "amarilla":   (-0.3, -0.3,  -0.3,  -0.3,  -0.3,  -0.3,  -0.3),
    "roja":       (-1.5, -1.5,  -1.5,  -1.5,  -1.5,  -1.5,  -1.5),
    "pen_gan":    (0.0,   0.4,   0.4,   0.4,   0.4,   0.4,   0.4),
    "pen_com":    (-0.7, -0.7,  -0.7,  -0.7,  -0.6,  -0.6,  -0.6),
    "pen_fall":   (0.0,  -0.7,  -0.7,  -0.7,  -0.8,  -0.8,  -0.8),
    "atajada":    (0.25,  0.0,   0.0,   0.0,   0.0,   0.0,   0.0),
    "pen_ataj":   (1.2,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0),
    "gol_recib":  (-0.35, -0.15, -0.10, -0.05, 0.0,   0.0,   0.0),
    "cero":       (0.6,   0.4,   0.3,   0.1,   0.0,   0.0,   0.0),   # portería en cero (60+ min)
    # Pases: (precisión - referencia) × peso × volumen (hasta 1 con 40 pases); y un poco por volumen.
    "pase_ref":   (70,    86,    80,    85,    80,    76,    70),
    "pase_peso":  (1.0,   2.5,   2.0,   3.5,   2.5,   2.0,   1.0),
    "pase_vol":   (0.002, 0.004, 0.004, 0.008, 0.006, 0.004, 0.003),
}
PESOS = {r: {k: v[i] for k, v in _T.items()} for i, r in enumerate(ROLES)}

BASE = 6.0
RESULTADO = 0.3      # ganar +0.3 / perder -0.3 (proporcional a los minutos)
MIN_MINUTOS = 10
# Calibración por posición: cada posición se lleva a la misma escala (promedio 6.8, dispersión 0.72), así un
# central no queda "castigado" frente a un delantero solo porque su fórmula suma distinto. Se calcula con
# actuaciones reales (calif_datos.json) y se guarda en calif_calibracion.json: python hormi_calif_legion.py --calibrar
CALIBRACION_FILE = "calif_calibracion.json"
MEDIA, DISPERSION = 6.8, 0.72


def rol_de_api(pos, grid=None, formacion=None):
    """Rol aproximado para jugadores que no son de la app: G/D/M/F y, si hay, su lugar en la cancha (grid
    "fila:columna") para separar laterales de centrales y extremos de medios."""
    pos = (pos or "").upper()[:1]
    try:
        fila, col = (int(x) for x in str(grid).split(":"))
        ancho = int(str(formacion).split("-")[fila - 2]) if formacion and fila >= 2 else None
    except Exception:
        fila = col = ancho = None
    orilla = bool(ancho and ancho >= 4 and col in (1, ancho))
    if pos == "G":
        return "POR"
    if pos == "D":
        return "LAT" if orilla else "DFC"
    if pos == "M":
        return "EXT" if orilla else "MC"
    if pos == "F":
        return "EXT" if (ancho and ancho >= 3 and col in (1, ancho)) else "DEL"
    return "MC"


def _g(s, *ruta):
    """Lee s["grupo"]["campo"] o s["grupo_campo"] (aplanado); None/"" → 0."""
    plana = "_".join(ruta)
    if plana in s and not isinstance(s[plana], dict):
        v = s[plana]
    else:
        v = s
        for r in ruta:
            v = (v or {}).get(r) if isinstance(v, dict) else None
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def desglose(s, rol, ctx=None):
    """Lista de (concepto, puntos) — para explicar la nota y para calibrar."""
    ctx = ctx or {}
    w = PESOS[rol]
    minutos = _g(s, "games", "minutes")
    out = []

    def add(nombre, cant, peso):
        if cant and peso:
            out.append((nombre, cant * peso))
    add("goles", _g(s, "goals", "total"), w["gol"])
    add("asistencias", _g(s, "goals", "assists"), w["asistencia"])
    add("pases clave", _g(s, "passes", "key"), w["pase_clave"])
    tiros, puerta = _g(s, "shots", "total"), _g(s, "shots", "on")
    add("tiros a puerta", puerta, w["tiro_puerta"])
    add("tiros fuera", max(0, tiros - puerta), w["tiro_fuera"])
    rg_i, rg_ok = _g(s, "dribbles", "attempts"), _g(s, "dribbles", "success")
    add("regates", rg_ok, w["regate_ok"])
    add("regates perdidos", max(0, rg_i - rg_ok), w["regate_mal"])
    du, du_g = _g(s, "duels", "total"), _g(s, "duels", "won")
    add("duelos ganados", du_g, w["duelo_gan"])
    add("duelos perdidos", max(0, du - du_g), w["duelo_per"])
    add("entradas", _g(s, "tackles", "total"), w["entrada"])
    add("intercepciones", _g(s, "tackles", "interceptions"), w["intercep"])
    add("bloqueos", _g(s, "tackles", "blocks"), w["bloqueo"])
    add("lo regatearon", _g(s, "dribbles", "past"), w["lo_regatean"])
    add("faltas recibidas", _g(s, "fouls", "drawn"), w["falta_rec"])
    add("faltas cometidas", _g(s, "fouls", "committed"), w["falta_com"])
    add("fueras de lugar", _g(s, "offsides"), w["fuera_lugar"])
    add("amarilla", _g(s, "cards", "yellow"), w["amarilla"])
    add("roja", _g(s, "cards", "red"), w["roja"])
    add("penal ganado", _g(s, "penalty", "won"), w["pen_gan"])
    add("penal cometido", _g(s, "penalty", "commited"), w["pen_com"])
    add("penal fallado", _g(s, "penalty", "missed"), w["pen_fall"])
    add("atajadas", _g(s, "goals", "saves"), w["atajada"])
    add("penal atajado", _g(s, "penalty", "saved"), w["pen_ataj"])
    # Pases: precisión contra lo normal en su posición, pesa más con más pases.
    p_tot = _g(s, "passes", "total")
    acc = _g(s, "passes", "accuracy")
    if p_tot:
        pct = acc / p_tot * 100 if acc <= p_tot else acc
        vol = min(1.0, p_tot / 40)
        out.append(("precisión de pases", (pct - w["pase_ref"]) / 100 * w["pase_peso"] * vol))
        add("volumen de pases", p_tot, w["pase_vol"])
    # Marcador (proporcional a los minutos jugados).
    f, c = ctx.get("goles_favor"), ctx.get("goles_contra")
    if f is not None and c is not None and minutos:
        frac = min(1.0, minutos / 90)
        if f != c:
            out.append(("resultado", (RESULTADO if f > c else -RESULTADO) * frac))
        if rol == "POR":
            add("goles recibidos", _g(s, "goals", "conceded") or c * frac, w["gol_recib"])
        else:
            add("goles recibidos", c * frac, w["gol_recib"])
        if c == 0 and minutos >= 60:
            out.append(("portería en cero", w["cero"]))
    return out


def calcular(s, rol, ctx=None):
    if _g(s, "games", "minutes") < MIN_MINUTOS or rol not in PESOS:
        return None
    bruta = BASE + sum(p for _, p in desglose(s, rol, ctx))
    m, sd = _calibracion().get(rol, (BASE, DISPERSION))
    return round(max(3.0, min(10.0, MEDIA + (bruta - m) * DISPERSION / (sd or DISPERSION))), 1)


def para_jugador(res, pid, team_id, rol):
    """Desde la respuesta completa de /fixtures/players: la calificación de un jugador. El marcador se saca de los
    goles recibidos por cada portero (el del rival = goles a favor)."""
    favor = contra = 0
    mio = None
    for t in res or []:
        propio = (t.get("team") or {}).get("id") == team_id
        for pl in t.get("players") or []:
            st = (pl.get("statistics") or [{}])[0]
            if ((st.get("games") or {}).get("position") or "") == "G":
                rec = _g(st, "goals", "conceded")
                if propio:
                    contra += rec
                else:
                    favor += rec
            if (pl.get("player") or {}).get("id") == pid:
                mio = st
    if mio is None:
        return None
    return calcular(mio, rol, {"goles_favor": favor, "goles_contra": contra})


_CAL = None


def _calibracion():
    global _CAL
    if _CAL is None:
        import json
        from pathlib import Path
        try:
            _CAL = {k: tuple(v) for k, v in json.loads(Path(__file__).with_name(CALIBRACION_FILE).read_text()).get("roles", {}).items()}
        except Exception:
            _CAL = {}
    return _CAL


def _contexto(p, j):
    h = j["team_id"] == p["home_id"]
    return {"goles_favor": p["goles_home"] if h else p["goles_away"], "goles_contra": p["goles_away"] if h else p["goles_home"]}


def calibrar(datos_path="calif_datos.json"):
    """(media, desviación) de la nota sin calibrar por posición, con todas las actuaciones de calif_datos.json.
    El medio ofensivo (MCO) no se distingue en la API: se mide aplicando su fórmula a los medios (MC)."""
    import json
    import statistics as S
    from datetime import datetime
    from pathlib import Path
    d = json.loads(Path(datos_path).read_text(encoding="utf-8"))["partidos"]
    brutas = {r: [] for r in ROLES}
    for p in d.values():
        for j in p["jugadores"]:
            if _g(j, "games", "minutes") < MIN_MINUTOS:
                continue
            rol = rol_de_api(j.get("games_position"), j.get("grid"), j.get("formacion"))
            ctx = _contexto(p, j)
            brutas[rol].append(BASE + sum(x for _, x in desglose(j, rol, ctx)))
            if rol == "MC":
                brutas["MCO"].append(BASE + sum(x for _, x in desglose(j, "MCO", ctx)))
    out = {"actualizado": datetime.now().isoformat(timespec="seconds"),
           "actuaciones": sum(len(v) for v in brutas.values()),
           "roles": {r: [round(S.mean(v), 3), round(S.pstdev(v), 3)] for r, v in brutas.items() if len(v) > 30}}
    Path(__file__).with_name(CALIBRACION_FILE).write_text(json.dumps(out, indent=1), encoding="utf-8")
    global _CAL
    _CAL = None
    return out


if __name__ == "__main__":
    import sys
    if "--calibrar" in sys.argv:
        print(calibrar())

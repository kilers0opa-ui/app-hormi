#!/usr/bin/env python3
"""
Disponibilidad: lesiones y suspensiones de los jugadores para su PRÓXIMO partido con su club → disponibilidad.json.

Por equipo: /fixtures?team&next=1 (el próximo partido) + /injuries?team&season (bajas del partido más reciente que
ya tenga lista; la del próximo partido la publica la API hasta ~1 día antes).
≈ 2 peticiones por equipo (≈ 26 al día por corrida). La app pone la etiqueta en Inicio, Legión y la agenda.

Avisos (tipo "disponibilidad"), comparando con la corrida anterior:
  - 🚑 "Raúl Jiménez: baja por lesión" (rodilla · se pierde Wolves vs Everton)
  - 🟥 "César Montes: suspendido" (acumulación de amarillas · ...)
  - ⚠️ "X: en duda para el partido" (la API lo marca "Questionable")
  - ✅ "X ya está disponible" (deja de aparecer en la lista de su siguiente partido)
La primera corrida (sin disponibilidad.json) no avisa: solo arma el estado.
"""
import argparse
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hormi_alertas as ha

OUT = Path("disponibilidad.json")

# Motivos de la API (en inglés) → español. Lo que no esté aquí se muestra tal cual.
ZONAS = {
    "knee": "rodilla", "hamstring": "isquiotibiales", "ankle": "tobillo", "muscle": "muscular", "groin": "ingle",
    "calf": "pantorrilla", "thigh": "muslo", "foot": "pie", "back": "espalda", "hip": "cadera", "shoulder": "hombro",
    "achilles": "tendón de Aquiles", "adductor": "aductor", "head": "cabeza", "concussion": "conmoción",
    "ligament": "ligamento", "cruciate": "ligamento cruzado", "meniscus": "menisco", "toe": "dedo del pie",
    "hand": "mano", "wrist": "muñeca", "arm": "brazo", "rib": "costilla", "face": "cara", "nose": "nariz",
    "illness": "enfermedad", "virus": "enfermedad", "fever": "fiebre", "fatigue": "fatiga", "knock": "golpe",
    "strain": "distensión", "fracture": "fractura", "surgery": "cirugía", "bruise": "contusión",
}


def clasificar(tipo, motivo):
    """("lesion" | "suspension" | "duda" | None, texto en español)."""
    m = (motivo or "").lower()
    if any(w in m for w in ("suspend", "red card", "yellow card", "ban")):
        if "yellow" in m:
            return "suspension", "acumulación de amarillas"
        if "red" in m:
            return "suspension", "tarjeta roja"
        return "suspension", "suspendido"
    if any(w in m for w in ("coach", "inactive", "rest", "personal", "international duty", "loan")):
        return None, ""   # no es lesión ni castigo: no se avisa
    zona = next((v for k, v in ZONAS.items() if k in m), None)
    texto = zona or (motivo or "lesión")
    return ("duda" if (tipo or "").lower() == "questionable" else "lesion"), texto


LIGA_MX = 262
# Noticias (Liga MX), en orden: LESIÓN clara → "Lesionado · se pierde X vs Y"; SUSPENSIÓN clara → "Suspendido";
# cualquier otra ausencia (es baja, no viajó, se pierde, fuera de la lista...) → solo "En duda", sin el porqué
# (las notas a veces dicen tonterías: no se muestra el titular ni el motivo).
LESION_RE = re.compile(r"\b((por|sufre|sufri[oó]|con|una|nueva|grave) lesi[oó]n|lesionad[oa]|desgarro|esguince|fractura|"
                       r"rotura|operad[oa]|cirug[ií]a|parte m[eé]dico)\b", re.I)
SUSP_RE = re.compile(r"\b(suspendid[oa] (por|para)|suspensi[oó]n de \d|castigad[oa] (con|por))\b", re.I)
DUDA_RE = re.compile(r"\b(no juega|no jugar[aá]|no ha jugado|se pierde|fuera por|no viaj[oó]|no viajar[aá]|no viaja|fuera de la (convocatoria|lista)|"
                     r"no fue convocado|no estar[aá]|descartad[oa]|en duda|duda para|"
                     r"(es|ser[aá]|ser[ií]a|causa|causar[aá]|sensible|otra|nueva|confirma) baja|"
                     r"baja (por|para|ante|contra|en el|del cl[aá]sico))\b", re.I)
CLAVE_BAJA = re.compile("|".join(r.pattern for r in (LESION_RE, SUSP_RE, DUDA_RE)), re.I)
DESCARTE = re.compile(r"\b(regres[ao]|vuelve|volver[aá]|recuper[ao]|recuperad[oa]|alta|ya entrena|listo para|"
                      r"reaparece|de vuelta|descart[ao] lesi[oó]n|sin lesi[oó]n|baja california|partido suspendido)\b", re.I)


def _norm(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


AMARILLAS_CACHE = ha.Path("amarillas_cache.json")   # {fixture:team: {player_id: amarillas que cuentan}}
RONDA_REGULAR = re.compile(r"^(Apertura|Clausura) - (\d+)$")
_fixtures_mx = {}


def suspension_amarillas(p, t, fx_next, cache):
    """Liga MX: cada 5 amarillas en la fase regular (17 jornadas) = 1 partido de suspensión. En Play-In y Liguilla
    el conteo se reinicia (no se cuentan). Si su amarilla número 5, 10, 15... fue en el ÚLTIMO partido de liga de su
    equipo, se pierde el siguiente. Las amarillas de una expulsión por doble amarilla no cuentan (eso es roja).
    Costo: 1 petición por equipo por corrida + 1 por partido nuevo (se guarda en amarillas_cache.json)."""
    tid, pid = t["team_id"], p.get("player_id")
    season = (fx_next.get("league") or {}).get("season")
    torneo = ((fx_next.get("league") or {}).get("round") or "").split(" - ")[0]   # Apertura / Clausura
    if not season or torneo not in ("Apertura", "Clausura"):
        return None
    if tid not in _fixtures_mx:
        _fixtures_mx[tid] = ha.api("/fixtures", {"team": tid, "league": LIGA_MX, "season": season}) or []
    jugados = sorted([f for f in _fixtures_mx[tid] if f["fixture"]["status"].get("short") in ("FT", "AET", "PEN")],
                     key=lambda f: f["fixture"]["timestamp"])
    if not jugados:
        return None
    regulares = [f for f in jugados if (m := RONDA_REGULAR.match((f["league"].get("round") or "")))
                 and m.group(1) == torneo]
    total = 0
    cruce = None
    for f in regulares:
        k = f"{f['fixture']['id']}:{tid}"
        if k not in cache:
            res = ha.api("/fixtures/players", {"fixture": f["fixture"]["id"], "team": tid})
            if res is None:
                return None   # sin datos completos no se afirma nada
            fila = {}
            for team in res:
                for pl in team.get("players") or []:
                    c = ((pl.get("statistics") or [{}])[0].get("cards") or {})
                    y, r = c.get("yellow") or 0, c.get("red") or 0
                    if y and not r:   # doble amarilla = roja: esas no acumulan
                        fila[str(pl["player"]["id"])] = y
            cache[k] = fila
        antes = total
        total += cache[k].get(str(pid), 0)
        if total // 5 > antes // 5:
            cruce = f
    # Solo si la amarilla que completó el ciclo fue en su último partido de liga.
    if cruce and cruce["fixture"]["id"] == jugados[-1]["fixture"]["id"]:
        return f"acumulación de amarillas ({total})"
    return None


def noticia_baja(p):
    """La nota más reciente (≤ 48 h) que diga que el jugador está lesionado/suspendido. None si no hay."""
    import hormi_videos as hv
    nombre = (p.get("name") or "").replace(f'"{p.get("apodo")}" ', "")
    apellido = _norm(p.get("search_last_name") or nombre.split()[-1])
    q = (f'"{nombre}" (lesión OR lesionado OR baja OR desgarro OR "parte médico" OR operado OR suspendido OR "no viajó" '
         f'OR "no juega" OR "se pierde" OR duda) when:2d')
    notas = hv.fetch_news(q, limit=15) or []
    limite = datetime.now(timezone.utc) - timedelta(hours=48)
    buenas = []
    for n in notas:
        t = n.get("title") or ""
        try:
            pub = datetime.fromisoformat((n.get("published_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if pub < limite or apellido not in _norm(t) or not CLAVE_BAJA.search(t) or DESCARTE.search(t):
            continue
        buenas.append((pub, n))
    return max(buenas, key=lambda x: x[0])[1] if buenas else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-avisos", action="store_true")
    args = ap.parse_args()
    config = ha.load_config()
    archivados = set(config.get("archivados") or [])
    prev = ha.load_json(OUT, None)
    primera = prev is None
    antes = (prev or {}).get("players") or {}
    out = {"updated_at": datetime.now(ha.LOCAL_TZ).isoformat(), "players": {}}
    por_equipo = {}
    avisos = []

    for p in config["players"]:
        if p["key"] in archivados:
            continue
        t = next((x for x in p["targets"] if x.get("kind") != "seleccion" and x.get("team_id")), None)
        if not t:
            continue
        tid = t["team_id"]
        if tid not in por_equipo:
            nxt = ha.api("/fixtures", {"team": tid, "next": 1})
            fx = nxt[0] if nxt else None
            # La lista de bajas de un partido la publica la API hasta ~1 día antes: se usa la del partido MÁS
            # RECIENTE del equipo que ya la tenga (de las últimas 3 semanas). Si ya salió la del próximo, es esa.
            todas = None if nxt is None else ha.api("/injuries", {"team": tid, "season": t.get("season") or 2026})
            lista = None
            if todas is not None:
                limite = datetime.now(ha.LOCAL_TZ).timestamp() - 21 * 86400
                fechas = [x["fixture"]["timestamp"] for x in todas
                          if (x.get("fixture") or {}).get("timestamp") and x["fixture"]["timestamp"] >= limite]
                ultima = max(fechas) if fechas else None
                lista = [x for x in todas if ultima and (x.get("fixture") or {}).get("timestamp") == ultima]
            por_equipo[tid] = (fx, lista)
        fx, lista = por_equipo[tid]
        if lista is None:   # error de la API: se conserva lo anterior de este jugador
            if p["key"] in antes:
                out["players"][p["key"]] = antes[p["key"]]
            continue
        if not fx:
            continue
        mio = next((x for x in lista if (x.get("player") or {}).get("id") == p.get("player_id")), None)
        if not mio:
            continue
        estado, texto = clasificar((mio.get("player") or {}).get("type"), (mio.get("player") or {}).get("reason"))
        if not estado:
            continue
        partido = f"{ha.nombre_es(fx['teams']['home']['name'])} vs {ha.nombre_es(fx['teams']['away']['name'])}"
        out["players"][p["key"]] = {
            "estado": estado, "motivo": texto, "motivo_api": (mio.get("player") or {}).get("reason"),
            "partido": partido, "fixture_id": fx["fixture"]["id"],
            "kickoff": datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).isoformat(),
            "team_key": t["key"],
        }

    # ── Liga MX: la API no trae lesiones de la Liga MX. Se usan (1) la roja directa de su último partido y
    #    (2) noticias de las últimas 48 h con reglas estrictas, como "Posible baja" (con el enlace a la nota).
    stats = ha.load_json(ha.Path("player_stats.json"), {}) or {}
    cache_am = ha.load_json(AMARILLAS_CACHE, {}) or {}
    for p in config["players"]:
        if p["key"] in archivados or p["key"] in out["players"]:
            continue
        t = next((x for x in p["targets"] if x.get("kind") != "seleccion" and x.get("team_id")), None)
        fx = (por_equipo.get(t["team_id"]) or (None, None))[0] if t else None
        if not fx or (fx.get("league") or {}).get("id") != LIGA_MX:
            continue
        base = {"partido": f"{ha.nombre_es(fx['teams']['home']['name'])} vs {ha.nombre_es(fx['teams']['away']['name'])}",
                "fixture_id": fx["fixture"]["id"], "team_key": t["key"],
                "kickoff": datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).isoformat()}
        u = ((stats.get("players") or {}).get(p["key"]) or {}).get("ultimo_partido") or {}
        roja = next((e for e in u.get("eventos") or [] if e.get("mio") and e.get("tipo") == "roja"), None)
        if roja and u.get("team_key") == t["key"] and (u.get("kickoff") or "") < base["kickoff"] and \
                datetime.fromisoformat(base["kickoff"]) - datetime.fromisoformat(u["kickoff"]) < timedelta(days=12):
            out["players"][p["key"]] = {**base, "estado": "suspension", "motivo": f"tarjeta roja ({roja.get('min')})"}
            continue
        try:
            am = suspension_amarillas(p, t, fx, cache_am)
        except Exception as err:
            ha.log(f"⚠️  amarillas de {p['key']}: {err}")
            am = None
        if am:
            out["players"][p["key"]] = {**base, "estado": "suspension", "motivo": am}
            continue
        nota = noticia_baja(p)
        if nota:
            t = nota["title"]
            estado = "lesion" if LESION_RE.search(t) else "suspension" if SUSP_RE.search(t) else "duda"
            # Sin motivo: la app solo dice "Lesionado · se pierde X vs Y" / "Suspendido" / "En duda".
            out["players"][p["key"]] = {**base, "estado": estado, "motivo": "", "fuente_noticia": t, "url": nota["url"]}

    ha.save_json(AMARILLAS_CACHE, cache_am)

    # ── Bajas a mano (bajas_manual.json): mandan sobre la API y las noticias mientras no pase su fecha "hasta".
    #    estado: lesion | suspension | duda | disponible ("disponible" quita una baja equivocada).
    #    Sin "hasta": vale hasta que el jugador vuelve a una alineación (titular o banca) o juega un partido
    #    posterior a cuando se puso; entonces se quita sola (y llega "✅ ya está disponible").
    try:
        manual = ha.load_json(ha.Path("bajas_manual.json"), {}) or {}
    except Exception as err:   # un error de captura (coma de más/de menos) no debe tumbar toda la revisión
        ha.log(f"⚠️  bajas_manual.json tiene un error y se ignora ({err})")
        manual = {}
        if not args.sin_avisos:   # que se note: si no, parece que "no pasa nada"
            try:
                ha.FcmSender(config.get("fcm_project_id")).send(
                    "info", "⚠️ bajas_manual.json tiene un error",
                    "No se pudo leer (revisa comas y llaves). Las bajas a mano no se aplicaron.")
            except Exception:
                pass
    hoy = datetime.now(ha.LOCAL_TZ).date().isoformat()
    status = (ha.load_json(ha.Path("status.json"), {}) or {}).get("players") or {}
    cerradas = dict((prev or {}).get("manual_cerradas") or {})   # bajas a mano ya quitadas solas (no se reactivan)
    nombres = {x["key"] for x in config["players"]}
    notas = {}   # key -> texto que se escribe en "_estado" de su línea (para que se vea si está activa o no)
    hoy_txt = datetime.now(ha.LOCAL_TZ).strftime("%d/%m %H:%M")
    for key, m in manual.items():
        if key.startswith("_") or not isinstance(m, dict):
            continue
        if key not in nombres:
            notas[key] = "❌ no reconocida: la clave del jugador no existe (revisa la lista de claves)"
            continue
        estado = (m.get("estado") or "").lower()
        if estado not in ("lesion", "suspension", "duda", "disponible"):
            notas[key] = "❌ no reconocida: estado debe ser lesion, suspension, duda o disponible"
            continue
        if (m.get("hasta") or "9999") < hoy:
            notas[key] = f"⏹️ desactivada: ya pasó su fecha ({m.get('hasta')})"
            continue
        firma = json.dumps({k: v for k, v in m.items() if not k.startswith("_")}, sort_keys=True, ensure_ascii=False)
        if cerradas.get(key) == firma:
            notas[key] = "⏹️ desactivada: volvió a jugar. Para activarla otra vez cambia el motivo o pon fecha"
            continue   # ya volvió a jugar; si la vuelves a necesitar, cambia algo de la línea (fecha o motivo)
        if estado != "disponible" and not m.get("hasta"):
            a_ = antes.get(key) or {}
            desde = a_.get("desde") if a_.get("manual") else None
            desde = desde or datetime.now(ha.LOCAL_TZ).isoformat()
            u = ((stats.get("players") or {}).get(key) or {}).get("ultimo_partido") or {}
            jugo = (u.get("minutes") or 0) > 0 and (u.get("kickoff") or "") > desde
            en_lista = any(t.get("status") in ("titular", "banca", "en_cancha") and (t.get("kickoff") or "") > desde
                           for t in ((status.get(key) or {}).get("targets") or {}).values())
            if jugo or en_lista:
                ha.log(f"{key}: volvió a una alineación — la baja puesta a mano (sin fecha) se quita sola")
                out["players"].pop(key, None)
                cerradas[key] = firma
                notas[key] = f"⏹️ desactivada: volvió a la alineación ({hoy_txt}). Para activarla otra vez cambia el motivo o pon fecha"
                continue
        if estado == "disponible":
            out["players"].pop(key, None)
            notas[key] = "✅ activa: se muestra como disponible" + (f" hasta el {m['hasta']}" if m.get("hasta") else "")
            continue
        p = next((x for x in config["players"] if x["key"] == key), None)
        t = next((x for x in (p or {}).get("targets", []) if x.get("kind") != "seleccion" and x.get("team_id")), None)
        fx = (por_equipo.get(t["team_id"]) or (None, None))[0] if t else None
        if not fx:
            notas[key] = "⏸️ en espera: su club no tiene próximo partido programado"
            continue
        partido = f"{ha.nombre_es(fx['teams']['home']['name'])} vs {ha.nombre_es(fx['teams']['away']['name'])}"
        out["players"][key] = {
            "estado": estado, "motivo": m.get("motivo") or "", "manual": True, "hasta": m.get("hasta"),
            "desde": ((antes.get(key) or {}).get("desde") if (antes.get(key) or {}).get("manual") else None)
                     or datetime.now(ha.LOCAL_TZ).isoformat(),
            "partido": partido,
            "fixture_id": fx["fixture"]["id"], "team_key": t["key"],
            "kickoff": datetime.fromtimestamp(fx["fixture"]["timestamp"], ha.LOCAL_TZ).isoformat(),
        }
        notas[key] = (f"✅ activa: se pierde {partido}"
                      + (f" · hasta el {m['hasta']}" if m.get("hasta") else " · se quita sola cuando vuelva a una alineación"))

    # Escribe el estado en el mismo archivo (campo "_estado" de cada línea) solo si cambió algo.
    cambio = False
    for key, nota in notas.items():
        if manual[key].get("_estado") != nota:
            manual[key]["_estado"] = nota
            cambio = True
    if cambio:
        ha.Path("bajas_manual.json").write_text(json.dumps(manual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out["manual_cerradas"] = {k: v for k, v in cerradas.items() if k in manual}

    # ── avisos
    if not primera:
        for key, d in out["players"].items():
            a = antes.get(key)
            if a and a.get("estado") == d["estado"]:
                continue   # ya se avisó
            p = next(x for x in config["players"] if x["key"] == key)
            nombre = ha.display_name(p)
            if d["estado"] == "lesion":
                avisos.append((key, d["team_key"], f"🚑 {nombre}: lesionado", (f"{d['motivo'].capitalize()} · " if d.get("motivo") else "") + f"se pierde {d['partido']}"))
            elif d["estado"] == "suspension":
                avisos.append((key, d["team_key"], f"🟥 {nombre}: suspendido", (f"{d['motivo'].capitalize()} · " if d.get("motivo") else "") + f"se pierde {d['partido']}"))
            else:
                avisos.append((key, d["team_key"], f"⚠️ {nombre}: en duda", d["partido"]))
        for key, a in antes.items():
            if key not in out["players"] and key not in archivados and a.get("estado") in ("lesion", "suspension"):
                p = next((x for x in config["players"] if x["key"] == key), None)
                # Si ya salió en una alineación (titular o banca) después de la baja, el aviso de alineación ya
                # dijo que regresó: "ya está disponible" después del partido no tiene sentido.
                # Referencia: cuándo se puso la baja (a mano) o el partido que se iba a perder (de la API).
                desde_b = a.get("desde") or a.get("kickoff") or ""
                u = ((stats.get("players") or {}).get(key) or {}).get("ultimo_partido") or {}
                ya_volvio = bool(desde_b) and (((u.get("minutes") or 0) > 0 and (u.get("kickoff") or "") >= desde_b) or any(
                    t.get("status") in ("titular", "banca", "en_cancha") and (t.get("kickoff") or "") >= desde_b
                    for t in ((status.get(key) or {}).get("targets") or {}).values()))
                if p and ya_volvio:
                    ha.log(f"{key}: ya volvió a una alineación — sin aviso de 'ya está disponible'")
                    continue
                if p:
                    avisos.append((key, a.get("team_key"), f"✅ {ha.display_name(p)} ya está disponible",
                                   "Ya no aparece en la lista de bajas de su próximo partido"))

    ha.save_json(OUT, out)
    ha.log(f"disponibilidad.json: {len(out['players'])} con baja/duda · {len(avisos)} aviso(s)"
           + (" · primera corrida, sin avisos" if primera else ""))
    if avisos and not args.sin_avisos:
        fcm = ha.FcmSender(config.get("fcm_project_id"))
        for key, tkey, title, body in avisos:
            try:
                fcm.send("disponibilidad", title, body, key, tkey)
            except Exception as err:
                ha.log(f"⚠️  no se pudo mandar el aviso de disponibilidad ({key}): {err}")


if __name__ == "__main__":
    from hormi_pausa import salir_si_pausa
    salir_si_pausa()
    main()

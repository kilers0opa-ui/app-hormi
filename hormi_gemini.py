#!/usr/bin/env python3
"""
Gemini (con búsqueda en Google) como RESPALDO de la fuente de datos. Solo se usa cuando la API no tiene la
información; nunca para avisos en vivo (goles, cambios).

  - alineacion(): 10 min antes del partido, si la API no publicó alineación → ¿titular, banca o no convocado?
  - partido_jugador(): después del partido, si la API no trae sus estadísticas → minutos, goles, tiros, etc.
  - mvp(): si alguna página nombra al jugador "jugador del partido" / MVP.

Reglas (para que no invente):
  - Siempre con la fecha dd/mm/aaaa del partido y los dos equipos.
  - Solo vale si la respuesta trae fuentes (páginas que Google encontró) y el modelo dice de dónde lo sacó.
  - Quien lo usa compara con lo que ya sabemos (marcador, goles) y lo descarta si no cuadra.

Necesita GEMINI_API_KEY (Google AI Studio, nivel gratuito). Sin llave, todo devuelve None.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

MODELOS = [m.strip() for m in (os.environ.get("GEMINI_MODELS") or "gemini-3.8-flash,gemini-flash-latest,gemini-flash-lite-latest").split(",") if m.strip()]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"


def _log(msg):
    print(f"[gemini] {msg}", flush=True)


def disponible():
    return bool(os.environ.get("GEMINI_API_KEY"))


def preguntar(prompt, timeout=60, buscar=True):
    """(dict de la respuesta JSON, [fuentes]) o (None, []) si falla. Fuentes = páginas que usó la búsqueda."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None, []
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        **({"tools": [{"google_search": {}}]} if buscar else {}),
        "generationConfig": {"temperature": 0},
    }).encode()
    for modelo in MODELOS:
        try:
            req = urllib.request.Request(URL.format(m=modelo), data=body, method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as err:
            _log(f"{modelo}: HTTP {err.code} {err.read()[:600]!r}")
            continue
        except Exception as err:
            _log(f"{modelo}: {err}")
            continue
        cand = (data.get("candidates") or [{}])[0]
        texto = "".join(p.get("text") or "" for p in ((cand.get("content") or {}).get("parts") or []))
        gm = cand.get("groundingMetadata") or {}
        if os.environ.get("GEMINI_DEBUG"):
            _log(f"top: {list(data.keys())} · parts: {[list(p.keys()) for p in ((cand.get('content') or {}).get('parts') or [])]}")
            _log(f"modelo {modelo} · uso: {data.get('usageMetadata')} · claves: {list(cand.keys())} · metadata: {json.dumps(gm, ensure_ascii=False)[:1500]}")
        chunks = gm.get("groundingChunks") or []
        fuentes = [{"titulo": (c.get("web") or {}).get("title"), "url": (c.get("web") or {}).get("uri")}
                   for c in chunks if c.get("web")]
        m = re.search(r"\{.*\}", texto, re.S)
        if not m:
            _log(f"{modelo}: respuesta sin JSON: {texto[:200]!r}")
            return None, fuentes
        try:
            return json.loads(m.group(0)), fuentes
        except json.JSONDecodeError:
            _log(f"{modelo}: JSON inválido: {texto[:200]!r}")
            return None, fuentes
    return None, []


REGLAS = (
    "Usa SIEMPRE la búsqueda de Google antes de responder (no respondas de memoria). Responde SOLO con datos que encuentres en páginas publicadas sobre ESTE partido exacto "
    "(misma fecha y mismos equipos); si no lo encuentras, dilo — no supongas ni uses otros partidos. "
    "Responde ÚNICAMENTE con un objeto JSON válido, sin texto antes ni después."
)


def alineacion(jugador, equipo, local, visita, fecha, liga):
    """{"estado": "titular"|"banca"|"no_convocado", "motivo": ..., "sitio": ...} o None si no hay alineación
    OFICIAL publicada o no es confiable."""
    prompt = f"""{REGLAS}

Partido: {local} vs {visita}, {liga}, fecha {fecha} (dd/mm/aaaa).
Pregunta: ¿ya se publicó la alineación OFICIAL (confirmada por el club, la liga o la transmisión, NO "posibles
alineaciones" ni "alineaciones probables") de {equipo} para este partido? ¿{jugador} es titular, está en la banca
de suplentes o no fue convocado? Si no fue convocado, ¿se publicó el motivo (lesión, suspensión, decisión técnica)?

JSON:
{{"alineacion_oficial_publicada": true/false,
  "banca_publicada": true/false,
  "estado": "titular" | "banca" | "no_convocado" | "desconocido",
  "motivo": "lesion" | "suspension" | "tecnica" | "otro" | null,
  "sitio": "nombre del sitio de donde lo tomaste",
  "cita": "frase corta de esa página que lo confirma"}}"""
    r, fuentes = preguntar(prompt)
    _log(f"alineación {jugador} ({local} vs {visita} {fecha}): {r} · {len(fuentes)} fuente(s)")
    if not r or not fuentes or not r.get("alineacion_oficial_publicada") or not r.get("sitio"):
        return None
    est = r.get("estado")
    if est not in ("titular", "banca", "no_convocado"):
        return None
    if est == "no_convocado" and not r.get("banca_publicada"):
        return None   # sin la banca completa no se puede afirmar que no fue convocado
    return {"estado": est, "motivo": r.get("motivo"), "sitio": r.get("sitio"), "cita": r.get("cita"),
            "fuentes": fuentes[:5]}


def partido_jugador(jugador, equipo, local, visita, fecha, liga):
    """Estadísticas del jugador en el partido (formato de API-Football para la fórmula) + marcador y MVP, o None."""
    prompt = f"""{REGLAS}

Partido: {local} vs {visita}, {liga}, fecha {fecha} (dd/mm/aaaa).
Pregunta: ¿{jugador} ({equipo}) jugó este partido? Si jugó: minutos, goles, asistencias, tiros totales, tiros a
puerta, tarjetas amarillas y rojas, pases totales, pases precisos, pases clave, duelos y duelos ganados, regates
intentados y exitosos, entradas, intercepciones, faltas cometidas y recibidas, fueras de lugar, atajadas (si es
portero). Usa null para lo que NO encuentres publicado (no pongas 0 si no lo sabes). Indica también el marcador
final y si alguna página lo nombra "jugador del partido" / MVP.

JSON:
{{"partido_encontrado": true/false, "marcador_local": n, "marcador_visita": n, "jugo": true/false,
  "titular": true/false/null, "minutos": n, "goles": n, "asistencias": n, "tiros": n, "tiros_puerta": n,
  "amarillas": n, "rojas": n, "pases": n, "pases_precisos": n, "pases_clave": n, "duelos": n, "duelos_ganados": n,
  "regates": n, "regates_exitosos": n, "entradas": n, "intercepciones": n, "faltas_cometidas": n,
  "faltas_recibidas": n, "fueras_de_lugar": n, "atajadas": n,
  "mvp": true/false, "mvp_sitio": "sitio que lo nombra MVP o null",
  "sitio": "sitio principal de donde tomaste las estadísticas"}}"""
    r, fuentes = preguntar(prompt)
    _log(f"partido {jugador} ({local} vs {visita} {fecha}): {r} · {len(fuentes)} fuente(s)")
    if not r or not fuentes or not r.get("partido_encontrado") or not r.get("sitio"):
        return None
    r["fuentes"] = fuentes[:5]
    return r


def mvp(jugador, equipo, local, visita, fecha, liga):
    """{"sitio": ...} si una página nombra al jugador MVP / jugador del partido; None si no."""
    prompt = f"""{REGLAS}

Partido: {local} vs {visita}, {liga}, fecha {fecha} (dd/mm/aaaa).
Pregunta: ¿la liga, el club, Sofascore, FotMob u otro medio nombró a {jugador} ({equipo}) "jugador del partido",
"MVP" u "hombre del partido" en ESTE partido? Solo di que sí si una página lo dice explícitamente.

JSON:
{{"partido_encontrado": true/false, "mvp": true/false, "sitio": "sitio que lo nombra o null",
  "cita": "frase corta de esa página o null"}}"""
    r, fuentes = preguntar(prompt)
    _log(f"mvp {jugador} ({local} vs {visita} {fecha}): {r} · {len(fuentes)} fuente(s)")
    if r and fuentes and r.get("partido_encontrado") and r.get("mvp") and r.get("sitio"):
        return {"sitio": r["sitio"], "cita": r.get("cita"), "fuentes": fuentes[:5]}
    return None


def a_formato_api(r):
    """Respuesta de partido_jugador → dict de estadísticas como /fixtures/players (lo que lee la fórmula)."""
    n = lambda k: r.get(k) if isinstance(r.get(k), (int, float)) else None
    return {
        "games": {"minutes": n("minutos")},
        "goals": {"total": n("goles") or 0, "assists": n("asistencias") or 0, "saves": n("atajadas")},
        "shots": {"total": n("tiros"), "on": n("tiros_puerta")},
        "passes": {"total": n("pases"), "accuracy": n("pases_precisos"), "key": n("pases_clave")},
        "duels": {"total": n("duelos"), "won": n("duelos_ganados")},
        "dribbles": {"attempts": n("regates"), "success": n("regates_exitosos")},
        "tackles": {"total": n("entradas"), "interceptions": n("intercepciones")},
        "fouls": {"committed": n("faltas_cometidas"), "drawn": n("faltas_recibidas")},
        "cards": {"yellow": n("amarillas") or 0, "red": n("rojas") or 0},
        "offsides": n("fueras_de_lugar"),
    }


if __name__ == "__main__":
    # Prueba: python hormi_gemini.py partido "Rodrigo Huescas" "FC Copenhague" "Aarhus Fremad" "FC Copenhague" 29/10/2026 "Copa de Dinamarca"
    modo, *a = sys.argv[1:]
    if modo == "ping":   # sin búsqueda: ¿la llave y el modelo funcionan?
        print(preguntar('Responde solo con {"ok": true}', buscar=False))
        sys.exit(0)
    fn = {"partido": partido_jugador, "alineacion": alineacion, "mvp": mvp}[modo]
    print(json.dumps(fn(*a), ensure_ascii=False, indent=2))

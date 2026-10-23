"""Pausa de la API (p. ej. mientras no se renueva la suscripción de API-Football).

hormi_config.json → "api_pausa": {"desde": "2026-11-02T00:00:00-06:00", "hasta": null}
  - entre "desde" y "hasta" (hasta = null → hasta que se quite) NINGÚN script llama a la API:
    cada llamada responde "sin datos" y todo se queda como estaba (la app muestra lo último que tenía,
    no se mandan avisos ni se borra nada). Sin "api_pausa" (o con "desde" vacío) todo funciona normal.
Con la suscripción vencida la API pasa al plan gratis (100 peticiones al día y sin la temporada actual):
sin la pausa, el motor gastaría esas 100 en minutos y el resto del día solo recibiría errores.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

_avisado = False


def api_en_pausa():
    global _avisado
    try:
        p = json.loads(Path("hormi_config.json").read_text(encoding="utf-8")).get("api_pausa") or {}
        if not p.get("desde"):
            return False
        ahora = datetime.now(timezone.utc)
        desde = datetime.fromisoformat(p["desde"])
        hasta = datetime.fromisoformat(p["hasta"]) if p.get("hasta") else None
        pausa = desde <= ahora and (hasta is None or ahora < hasta)
    except Exception:
        return False
    if pausa and not _avisado:
        _avisado = True
        print("⏸️  API en pausa (hormi_config.json → api_pausa): no se consulta nada, se conserva lo anterior")
    return pausa


def salir_si_pausa():
    """Al inicio de cada script que vive de la API: en pausa no hace nada (y no cambia ningún archivo)."""
    if api_en_pausa():
        raise SystemExit(0)

# App Hormi — motor de alertas

Sigue a Armando "La Hormiga" González (Chivas, Olympiacos y Selección
Mexicana) usando API-Football, y manda notificaciones push por
[ntfy](https://ntfy.sh). La app Android lee `status.json` de este repo.

## Configurar

1. **Secreto de la API**: Settings → Secrets and variables → Actions →
   New repository secret → nombre `APIFOOTBALL_KEY`, valor tu API key de
   [API-Football](https://www.api-football.com/).
2. **Permisos de escritura del workflow**: ya vienen declarados en el YAML
   (`permissions: contents: write`), no hay que tocar nada más — pero si el
   push del estado falla, revisa Settings → Actions → General → Workflow
   permissions → "Read and write permissions".
3. **hormi_config.json**: trae los `team_id` de Chivas y Olympiacos ya
   validados. El de la Selección Mexicana se detecta solo la primera vez
   que corras la Fase 1 (busca por país + selección nacional) — si sale
   null, corre localmente:
   ```
   export APIFOOTBALL_KEY=tu_key
   python hormiga_fase1_validar_api.py --solo seleccion
   ```
   y sube el `hormi_config.json` actualizado.

## Correr localmente

```bash
export APIFOOTBALL_KEY=tu_key

# Validar que la API trae todo lo necesario (todos los targets)
python hormiga_fase1_validar_api.py

# Probar que las notificaciones push llegan
python hormi_alertas.py prueba

# Reproducir un partido ya jugado, minuto a minuto (usa la caché, no gasta cuota)
python hormi_alertas.py repeticion --solo chivas --contra Toluca
```

## En la nube

`.github/workflows/motor-alertas.yml` corre `python hormi_alertas.py chequeo`
cada 5 minutos. Ese modo es "barato": si no hay partido cerca para ningún
target, no gasta ninguna petición de la API — solo vigila de verdad (llamadas
en vivo) desde 20 min antes del kickoff hasta 3h después. El horario de cada
target se cachea en `hormi_horario.json` y se refresca cada 6h.

Con el plan gratuito de API-Football (100 peticiones/día) esto alcanza
sobrado. Si contratas el plan Pro puedes bajar `REFRESH_HORAS`,
`VENTANA_ANTES`/`VENTANA_DESPUES` en `hormi_alertas.py` para vigilar más
seguido.

Cada corrida actualiza y sube (`git push`) tres archivos: `status.json`
(lo que lee la app), `hormi_estado.json` (qué alertas ya se mandaron, para
no repetirlas) y `hormi_horario.json` (caché de horarios).

## Limitación conocida: convocatoria

API-Football publica la alineación (titular/banca) ~1h antes del partido,
no la convocatoria oficial con más anticipación. La alerta de "titular /
banca / no convocado" sale cuando esa alineación se publica, no cuando el
técnico anuncia la lista de convocados.

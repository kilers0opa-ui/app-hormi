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

Cada corrida actualiza y sube (`git push`) cuatro archivos: `status.json`
(lo que lee la app), `hormi_estado.json` (qué alertas ya se mandaron, para
no repetirlas), `hormi_horario.json` (caché de horarios) y `player_stats.json`
(ajuste "en vivo" del histórico — ver la sección de abajo).

## Historial real por competición (Fase 3 · `player_stats.json`)

La pantalla de Estadísticas de la app no usa datos de ejemplo: lee
`player_stats.json`, que se arma y se mantiene al día con dos piezas que
nunca se pisan entre sí:

1. **`hormi_historial.py`** (Fase 3) — corre una vez al día
   (`.github/workflows/historial.yml`, 03:00 hora de México) y llama a
   `/players?id=<player_id>&season=<año>` de API-Football una vez por cada
   año en `career_seasons` de cada jugador (`hormi_config.json`). Ese
   endpoint ya trae, por temporada, el/los club(es) con los que jugó y sus
   estadísticas por cada competición — así se cubre el historial completo
   (transferencias, préstamos, selección, etc.) sin mantener a mano una
   lista de equipos anteriores. Cada corrida **reemplaza por completo** los
   datos de las temporadas que sí trajeron respuesta; si una temporada no
   trae nada (el plan aún no la cubre, o la API falló un momento), se deja
   tal cual estaba — nunca se borra lo bueno por un error puntual.
2. **`apply_live_delta()`** dentro de `hormi_alertas.py` — cuando un
   partido de un jugador **termina**, durante el chequeo de cada 5 min, le
   suma minutos/goles/asistencias a la competición correspondiente en
   `player_stats.json` al instante, usando el `fixture_id` para no contar
   el mismo partido dos veces. Así Estadísticas no depende de que la Fase 3
   vuelva a correr para verse al día. Ese ajuste queda pisado (correctamente)
   la próxima vez que la Fase 3 traiga el total real y actualizado de la
   API — por diseño nunca hay doble conteo: la Fase 3 siempre reemplaza,
   nunca suma.

Correr manualmente:

```bash
export APIFOOTBALL_KEY=tu_key
python hormi_historial.py                     # todos los jugadores
python hormi_historial.py --jugador raul       # solo uno
python hormi_historial.py --jugador raul --temporada 2019   # un solo año, para pruebas
```

**Nota sobre el plan de API-Football**: con el plan gratuito, muchas
temporadas (sobre todo 2025/2026 en adelante) no traen datos todavía — esas
filas de `player_stats.json` simplemente no se llenan hasta contratar el
plan Pro. En cuanto se pague el plan Pro, sin tocar nada más de código, la
siguiente corrida de `historial.yml` y el próximo chequeo de `motor-alertas.yml`
empiezan a traer y actualizar esas temporadas automáticamente — tanto el
histórico como el "en vivo" quedan correctos sin ninguna migración manual.

## Limitación conocida: convocatoria

API-Football publica la alineación (titular/banca) ~1h antes del partido,
no la convocatoria oficial con más anticipación. La alerta de "titular /
banca / no convocado" sale cuando esa alineación se publica, no cuando el
técnico anuncia la lista de convocados.

# Fotos fuente de los pósters

Una carpeta por jugador (misma clave que hormi_config.json) con:

- `club.jpg` → póster estilo "apellido gigante" con los colores y el escudo de su club.
- `seleccion.jpg` → póster estilo "duotono" con los colores de la Selección.
- `meta.json` → `{"club_team_id": <id del club de la foto>}`. Si el jugador cambia de club, el póster de club
  se deja de generar hasta que subas una foto nueva (y actualices este id).

Son las mismas fotos de celebración de gol que usa la app. Al subir o cambiar una foto aquí, el workflow
"Fondos de pantalla" genera solo el póster nuevo (los demás se reusan de la caché).
Ideal: foto vertical, el jugador completo o de medio cuerpo, buena resolución.

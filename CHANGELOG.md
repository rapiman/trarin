# Cambios

Formato: por fecha, de lo más nuevo a lo más antiguo. Versión del paquete: 0.1.0
(todavía sin versiones publicadas).

## 2026-10-22

### Grupos (Pro Tools)
- `trarin grupos sesion.ptx reglas.json -o salida.ptx`: crea grupos de pistas
  según patrones sobre los nombres (una pista puede ir en varios grupos), más un
  grupo con todas las pistas MIDI. Mezcla y edición, o solo mezcla. Verificado en
  Pro Tools 12: los bytes del grupo son los mismos que los de un grupo creado en
  Pro Tools, y la sesión abre y guarda bien.

### Audio
- `trarin bpm`: calcula el tempo de un audio (pulso, primer pulso, acento y
  tempo por tramos).
- `trarin convertir-audio`: cambia la frecuencia de un WAV (soxr), con opción de
  un archivo mono por canal. Extra opcional `audio` (numpy y soxr).
- Lector de archivos MIDI estándar (`trarin/midi_smf.py`).

### Armador de shows
- Canciones con otra frecuencia de muestreo (`"frecuencia"` del show): el audio
  se convierte y se recalculan posiciones, fundidos, clip gain y automatización.
- `fin_compas` corta la canción: no se copia lo posterior (clips recortados con
  un fundido corto de salida).
- Tempo y compás que no empiezan en el tick 0 (rige el primero desde el inicio).
- `cambio_tempo` por canción y modo `"fin"`.
- `pistas_extra`: pistas desde archivos WAV o MIDI; canciones sin sesión.
- `buses_show`: buses y auxiliares propios del show, con la salida de cada pista
  según su nombre.

## 2026-10-21

### Armador de shows (Pro Tools)

- `trarin show plan` y `trarin show armar`: junta varias canciones en una sesión
  continua. Separación en compases o segundos (puede ser negativa), cambio de tempo
  configurable (por defecto una blanca antes de la canción siguiente; con separación
  negativa, en la mitad de la zona encimada), marcadores por canción.
- Match de pistas entre canciones (mismo nombre, tipo y canales, o nombre parecido;
  o el indicado en `match`). Las pistas nuevas llevan clips, fundidos, clip gain,
  plugins, ruteo (entrada, salida y envíos), automatización corrida al inicio de la
  canción, color y salida MIDI.
- En las pistas que coinciden, la automatización de cada canción reemplaza a la de la
  pista desde el comienzo de esa canción.
- Buses: se crean los buses estéreo o mono que falten; opción `"buses": "separar"`
  para que cada canción use buses y auxiliares propios.
- Auxiliares y pistas de instrumento se copian de la canción cuando la base no tiene
  una plantilla. Opción `"copiar_solo"` (por defecto el solo no se copia).
- `trarin compatibilidad`: consulta previa (frecuencias, tempos, propuesta de match,
  ruteo y plugins que faltan). Solo lee.

### Pro Tools: escritura

- Pistas: crear y quitar pistas de audio (mono y estéreo), MIDI y auxiliares; crear
  pistas de instrumento copiando las de otra sesión; renombrar.
- Clips: agregar y quitar clips de audio (mono y estéreo, con importación de archivos y
  regiones nuevas) y MIDI; editar notas; fundidos de entrada, salida y cruzados; clip
  gain fijo y en curva en clips nuevos.
- Mezcla: volumen, paneo, mute y solo; color; copia de inserts entre sesiones; copia de
  ruteo con traducción de rutas por nombre; crear buses estéreo y mono.
- Automatización: copiar, correr y juntar listas; editar puntos de volumen, mute y
  paneo desde el modelo común.
- Línea de tiempo: marcadores (agregar, borrar, editar con comentario), selecciones
  nuevas, tempo y compases.
- Reconstrucción de la sesión tras cambiar bytes (tamaños de bloques e índice).

### Otros

- Servidor MCP por stdio (`python -m trarin mcp`, extra `mcp`): leer sesiones,
  compatibilidad, planear y armar shows, escribir cambios. Probado solo fuera del
  equipo de trabajo.
- Modelo común `trarin/1` para Pro Tools, Ableton y FL Studio.
- Ableton (.als) y FL Studio (.flp): lectura completa y escritura de cambios sobre una
  sesión del mismo programa (sin crear pistas ni clips).
- Plantillas vacías de Pro Tools (44,1, 48 y 96 kHz y una con pistas modelo) y de FL
  Studio, en `_trarin_pruebas\plantillas` (aún fuera del repositorio).

## 2026-10-20

- Lector de sesiones de Pro Tools 12: tempo, compases, marcadores, audio, regiones,
  pistas, MIDI, mezcla (volumen, paneo, mute, solo, envíos, plugins y su estado),
  automatización, entradas y salidas, playlists alternativas, grupos y color.
- Salida JSON por defecto (YAML opcional). Licencia LGPL 2.1 o posterior.

# Cambios

Formato: por fecha, de lo más nuevo a lo más antiguo. Versión del paquete: 0.1.0
(todavía sin versiones publicadas).

## 2026-10-22

### Fichas de sonido (fase 2, primera parte)
- `trarin/fichas.py`: cada fuente (un archivo o los L/R de un clip estéreo) se
  analiza una vez en bloques de 0,1 s y la serie queda en `trarin/fichas/`
  junto a la sesión; con ella se arma la ficha de un archivo, un clip, una
  pista o una pista en una canción sin volver a leer el audio.
- Ficha: tiempo (dónde suena, silencios), nivel (pico, RMS, LUFS integrado,
  rango, momentos más fuertes), problemas (saturación, picos sobre 0 dBFS por
  el clip gain, corriente continua), estéreo, color (cinco bandas, centroide,
  planitud) y una línea en palabras. Tiempos en m:ss y compás|tiempo.
- LUFS con la ponderación K de BS.1770 aplicada sobre el espectro de cada
  bloque (coeficientes de la norma a 48 kHz): un seno de 997 Hz a -20 dBFS da
  -23,0 LUFS en mono y -20,0 en estéreo.
- MCP: `fichas_calcular` (trabajo, a pedido) y `ficha`; `resumen_sesion` trae
  una línea de sonido por pista cuando hay fichas.
- Se calculan varias fuentes a la vez (hasta 4 procesos) y la lectura de WAV
  de 24 bits es el doble de rápida: el show (124 fuentes) en unos 6 min la
  primera vez; después solo lo que cambió.
- Umbrales de «tonal» y «ruidoso» calibrados con el show (pifilkas y sintes
  tonales, kaskawillas y cascabeles ruidosos; lo grave no se califica).
- En el show ya avisa de las pistas consolidadas que pasan 0 dBFS por el clip
  gain (Kultrun C, Kultrun C 3, Kultrun estéreo).

### Servidor MCP completo (fase 1)
- 23 herramientas: vistas acotadas (`resumen_sesion`, `detalle_pista`,
  `rutas_sesion`, `archivos_audio`, `comparar_sesiones`), notas del proyecto
  (`notas`, `anotar`), consolidar y grupos con su plan, bpm, conversión de audio
  y paneo de Live, además de las de antes.
- Trabajos largos en un proceso aparte (`trarin/trabajos.py`): id, avance por
  líneas nuevas, espera con tope de 40 s, matar y lista de recientes.
- Al escribir: se relee el resultado, se compara con el original y se anota en
  `trarin/notas.md` junto a la sesión (`trarin/notas.py`, `trarin/vistas.py`).
- `consolidar` busca los sets de Live por su cuenta con `live_carpeta` y
  `live_buscar` en las reglas (antes lo hacía un script aparte). Consolidar el
  show por MCP da la misma sesión que la v2 (mismo modelo leído; solo cambian
  los identificadores aleatorios de las pistas nuevas).

### Más rápido en sesiones grandes
- Leer una sesión: el mapa de tempo se prepara una vez (`MapaTempo`, búsqueda
  binaria) en vez de recorrerse en cada conversión, y el descifrado se hace de
  una vez. Leer el show pasó de 8,7 s a 1,1 s, con los mismos datos en las 150
  sesiones de prueba (y en 221.000 conversiones comparadas una a una).
- El árbol de bloques de los mismos bytes se arma una sola vez (memoria de los
  últimos 4).
- `ruteo.en_lote`: varias operaciones independientes con un solo
  `reconstruir`. Lo usan `copiar_ruteo`, `copiar_color`, `copiar_inserts` y
  `escribir_automatizacion` (argumento `lote`); lo que no se puede agrupar
  (crear buses, agregar plugins a la sesión) se hace después, de a una. 88
  operaciones sobre el show: de 27 s a 0,5 s, mismos bytes en 62 sesiones.
- Agregar varias pistas MIDI o varios auxiliares en una pasada (8 MIDI: de
  6,4 s a 1 s), mismos bytes que de a una en 453 casos. El modelo común agrega
  juntas las pistas seguidas del mismo tipo y escribe la automatización de
  todas las pistas de una vez.
- Consolidar el show: de 6,7 min a menos de 2 min (casi todo es medir el audio).

### Quitar y agregar varias pistas de una vez
- `quitar_pistas` con varias pistas hace una sola pasada sobre el archivo (antes,
  una por pista): 55 pistas del show en 0,7 s en vez de 37 s. Los mismos bytes
  que quitándolas de a una, comprobado en 275 casos (el show y sesiones de
  prueba con audio, MIDI y auxiliares mezclados, en distinto orden).
- `agregar_pistas` con varias pistas de audio (mono o estéreo) arma todo con un
  solo `reconstruir` y escribe el índice una vez: 20 pistas en 4 s en vez de
  24 s. Mismos bytes que de a una en 163 casos. Si el lote no se puede armar de
  una vez (plantilla que no sirve, o varias estéreo en una sesión sin ninguna
  estéreo) se agregan de a una. El modelo común agrega juntas las pistas de
  audio seguidas.
- `buscar` usa un índice por tipo de bloque que se arma una vez por árbol, y
  recorrer el árbol ya no anida generadores.

### Consolidar pistas (Pro Tools)
- `trarin consolidar sesion.ptx reglas.json -o salida.ptx`: sobre la misma
  sesión, las pistas del mismo instrumento de distintas canciones pasan a unas
  pocas pistas (mono por lado L/C/R según el paneo de Live o de Pro Tools,
  estéreo aparte, una pista más cuando dos suenan juntas), con el clip gain
  llevado a un RMS común, la automatización de volumen y mute por canción, los
  plugins y la salida a un bus por categoría; las pistas MIDI se juntan en
  carriles que no se pisan; crea auxiliares, cambia salidas, borra pistas y
  arma los grupos. Las pistas silenciadas no se tocan.
- Lectura del árbol de bloques unas 13 veces más rápida (salta directo al
  siguiente 0x5A); mismo resultado en las 211 sesiones de prueba.
- `reconstruir` ya no recorre todos los bloques por cada reemplazo (búsqueda
  binaria y corrimientos acumulados): quitar una pista de un show de 160
  pistas pasó de varios minutos a segundos; mismos bytes que antes en 151
  operaciones de prueba.

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

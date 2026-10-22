# trarin

Herramienta para armar shows a partir de sesiones de DAW. Lee cada sesión y la
vuelca a un archivo de texto (JSON) que cualquier persona o IA puede leer y
editar. Sobre esa representación se calcula dónde debe caer cada canción en el
show, sin desfases de audio, MIDI ni mapa de tempo.

*Trarin* (mapudungun): amarrar, atar.

## Estado

Versión 0.1.

- **Pro Tools** `.ptx` (lectura): probado con 12.5 (Windows), 12.8, 2019 y 2026 (Mac).
- **Ableton Live** `.als` (lectura y escritura): probado con Live 12.
- **FL Studio** `.flp` (lectura y escritura): probado con FL 12, 20 y 21.

Detalle por DAW y decisiones abiertas en [`docs/arquitectura.md`](docs/arquitectura.md).
La tabla siguiente es de Pro Tools.

| Dato de la sesión                   | Estado    |
|-------------------------------------|-----------|
| Producto, versión, frecuencia       | listo     |
| Mapa de tempo (incluidas curvas)    | listo     |
| Compases (cambios de métrica)       | listo     |
| Marcadores y selecciones, con comentario | listo |
| Archivos de audio y regiones        | listo     |
| Pistas de audio (canales, colocaciones, mute de región) | listo |
| Fundidos (entrada, salida, cruzados, pendiente y curva) | listo |
| Clip gain (fijo y curvas)           | listo     |
| Timecode: cadencia, inicio y duración de sesión | listo |
| MIDI (regiones, notas, pistas)      | listo     |
| Volumen, paneo, mute, solo          | listo     |
| Envíos (destino y nivel)            | listo     |
| Plugins (cuáles hay en cada canal)  | listo     |
| Automatización (volumen, mute, paneo) | listo   |
| Entrada y salida de cada canal      | listo     |
| Estado de plugins: controles genéricos, preset, valores FabFilter | listo |
| Estado propio de otros plugins      | solo tamaño |
| Colores de pista, grupos, playlists alternativas | listo |
| Significado de cada parámetro en el estado propio de un plugin | no está en el archivo |

Más adelante: escritor de sesiones, servidor MCP y soporte para Ableton.

## Uso

```
pip install -e .
python -m trarin leer "Mi canción.ptx" --resumen
python -m trarin leer "Mi canción.ptx" -o "Mi canción.json"
python -m trarin leer "Mi canción.ptx" --yaml     # requiere pip install pyyaml
python -m trarin leer "Mi tema.als" -o tema.json  # también .flp
python -m trarin escribir tema.json --plantilla "Mi tema.als" -o "Mi tema v2.als"
python -m trarin compatibilidad "Canción 1.ptx" "Canción 2.ptx"   # qué tan compatibles son antes de juntarlas
python -m trarin show plan show.json      # cómo quedarían las canciones juntas (solo lee)
python -m trarin show armar show.json     # escribe la sesión del show
```

Opciones del show (además de `canciones`, `separacion`, `cambio_tempo`, `base` y
`match`): `"buses": "juntar"` (por defecto: el bus «Aux 1» de cada canción va al
«Aux 1» del show) o `"separar"` (cada canción usa buses y auxiliares propios,
«Aux 1 (canción)»), y `"copiar_solo": true` para copiar el solo de las pistas
(por defecto no, para que una canción no silencie a las demás).

### Servidor MCP

```
pip install -e ".[mcp]"
python -m trarin mcp          # servidor MCP por stdio
```

Herramientas: `leer_sesion`, `compatibilidad`, `planear_show`, `armar_show` y
`escribir_sesion`. Las que escriben piden un archivo de salida que no exista.
Para un cliente MCP (por ejemplo Claude Desktop):

```json
{"mcpServers": {"trarin": {"command": "python", "args": ["-m", "trarin", "mcp"],
                           "cwd": "D:\\Proyectos\\Personal\\trarin"}}}
```

`escribir` aplica el JSON editado sobre una copia del archivo original y nunca
sobrescribe la plantilla.

trarin nunca modifica la sesión original.

## Representación (`formato: trarin/0`)

- Toda posición musical se guarda en **ticks**: 960.000 por negra, el mismo
  valor que usa Pro Tools internamente. Es el dato exacto.
- Cada evento agrega, como ayuda de lectura, `posicion` (`compás|tiempo|ticks`,
  como la muestra Pro Tools), `segundos` y `muestra` (según la frecuencia de la
  sesión), calculados con el mapa de tempo.
- `tempo[].pulso_ticks` indica la figura que lleva el BPM: 960000 = negra,
  480000 = corchea.
- Antes del primer evento de tempo rige el BPM de ese primer evento.

## Formato Pro Tools 12 (lo descifrado hasta ahora)

La especificación completa, con el grado de confianza de cada dato y las
diferencias con Pro Tools 2024, está en [`docs/formato_ptx.md`](docs/formato_ptx.md).

Ver `trarin/protools/tiempo.py`. Resumen:

- Bloque `0x2028` "Tempo": eventos `Const` + `TMS`; desde el inicio de `TMS`:
  `+19` posición (int64, cero = 10^12), `+29` BPM (double), `+37` pulso (u32).
- Bloque `0x2029` "Meter": cantidad (u32) y eventos de 36 bytes:
  posición (int64), compás (u32), numerador (u32), denominador (u32).
- Bloque `0x2077` (dentro de `0x271A` "Markers"): número (u16), largo (u32),
  nombre UTF-8, posición (40 bits).
- Tempo, compases y marcadores aparecen dos veces con el mismo contenido.

- Lista de pistas: `0x2519`/`0x251A` (nombre de la playlist activa y tipo).
- Playlists de audio: lista `0x1015`/`0x1014` (nombre y canales) y mapa `0x1054`;
  una playlist cuyo nombre no está en la lista de pistas es una alternativa
  guardada.
  cada canal es una entrada `0x1052` y cada colocación un `0x1050` con hijo
  `0x104F` (índice de región en `+4`, muestra de inicio en `+9`, 5 bytes).
  Si el último byte del `0x1050` vale 1, la entrada es un fade o crossfade.
- MIDI: notas en `0x2000` (bloques `MdNLB`, eventos de 35 bytes), regiones en
  `0x2634` (ventana sobre un bloque de notas) y pistas en `0x1058`/`0x1057`.

- Grupos: `0x202B`/`0x202A`: nombre, tipo, identificador, cantidad e índices
  de pistas.
- Mezcla: bloque `0x2624`, un hijo por canal (ver `trarin/protools/mezcla.py`).
  Volumen en décimas de dB, paneo de -100 a 100. Descifrado con sesiones de
  prueba hechas en Pro Tools, cada una con un solo cambio respecto de la
  anterior: volumen, paneo, mute, solo, plugin, envío y automatización.

## Preguntas abiertas

- En algunas sesiones el compás que declara un cambio de métrica no coincide
  con el cálculo desde el compás 1 (p. ej. compás 101 en la negra 248 con
  5/8 desde el inicio, donde se esperaría 250).
- Mapas de tempo cuyo primer evento no está en el cero.

## Licencia

GNU LGPL 2.1 o posterior (ver `LICENSE`).
`trarin/protools/descifrado.py` es un port de
[libptformat](https://github.com/zamaudio/ptformat) (Damien Zammit), que tiene
la misma licencia.

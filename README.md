# trarin

Lee sesiones de DAW y las vuelca a texto (JSON) que cualquier persona o IA puede
leer y editar; sobre esa representación arma shows (varias canciones en una sola
sesión, sin desfases de audio, MIDI ni mapa de tempo), revisa, compara, mide el
sonido y escribe los cambios de vuelta en una sesión nueva. Se usa por línea de
comandos o como servidor MCP, para trabajar una sesión conversando con una IA.

*Trarin* (mapudungun): amarrar, atar.

> Proyecto independiente. No está afiliado a Avid (Pro Tools), Ableton ni
> Image-Line (FL Studio), ni cuenta con su respaldo. Los formatos se descifraron
> mirando archivos propios; pueden fallar con versiones o casos no vistos.
> **Trabajar siempre con respaldo de las sesiones.**

## Estado

Versión 0.1 (alfa).

- **Pro Tools** `.ptx` (lectura y escritura): probado con 12.5 (Windows), 12.8,
  2019 y 2026 (Mac). La escritura está verificada abriendo los resultados en
  Pro Tools 12.
- **Ableton Live** `.als` (lectura y escritura): probado con Live 12.
- **FL Studio** `.flp` (lectura y escritura): probado con FL 12, 20 y 21.

Detalle por programa y decisiones abiertas en [`docs/arquitectura.md`](docs/arquitectura.md);
lo descifrado del formato de Pro Tools, con el grado de confianza de cada dato, en
[`docs/formato_ptx.md`](docs/formato_ptx.md). Lo que viene, en [`ROADMAP.md`](ROADMAP.md);
lo hecho, en [`CHANGELOG.md`](CHANGELOG.md).

Qué se lee de una sesión de Pro Tools:

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
| Grupos de clips (contenido y fundidos) | listo  |
| Timecode: cadencia, inicio y duración de sesión | listo |
| MIDI (regiones, notas, pistas, puerto y canal) | listo |
| Pistas de instrumento               | listo     |
| Volumen, paneo, mute, solo          | listo     |
| Envíos (destino y nivel)            | listo     |
| Plugins (cuáles hay en cada canal)  | listo     |
| Automatización (volumen, mute, paneo) | listo   |
| Entrada y salida de cada canal      | listo     |
| Estado de plugins: controles genéricos, preset, valores FabFilter | listo |
| Estado propio de otros plugins      | solo tamaño |
| Colores de pista, grupos, playlists alternativas | listo |
| Significado de cada parámetro en el estado propio de un plugin | no está en el archivo |

## Instalación

Requiere Python 3.10 o posterior. El núcleo no tiene dependencias; lo demás es
opcional:

```
git clone https://github.com/rapiman/trarin
cd trarin
pip install -e .              # leer y escribir sesiones
pip install -e ".[audio]"     # numpy y soxr: fichas, escuchar, bpm, golpes, nivelar, conversión de frecuencia
pip install -e ".[mp3]"       # lameenc: escuchar en MP3
pip install -e ".[mcp]"       # servidor MCP
pip install -e ".[yaml]"      # shows descritos en YAML
pip install -e ".[todo]"      # todo lo anterior
```

## Uso por línea de comandos

```
trarin leer "Mi canción.ptx" --resumen
trarin leer "Mi canción.ptx" -o "Mi canción.json"       # también .als y .flp; --yaml con pyyaml
trarin escribir tema.json --plantilla "Mi tema.als" -o "Mi tema v2.als"
trarin revisar show.ptx                                  # lo que Pro Tools mostraría roto
trarin compatibilidad "Canción 1.ptx" "Canción 2.ptx"   # antes de juntarlas
trarin show plan show.json                               # cómo quedarían las canciones juntas (solo lee)
trarin show armar show.json                              # escribe la sesión del show
trarin grupos show.ptx grupos.json -o show_grupos.ptx    # grupos de pistas por nombre
trarin consolidar show.ptx reglas.json -o show_v2.ptx    # junta pistas del mismo instrumento (--plan: solo muestra)
trarin bpm bombo.wav                                     # tempo de un audio
trarin convertir-audio voz.wav voz48 --frecuencia 48000 --mono   # voz48.L.wav y voz48.R.wav
```

`trarin --help` lista todas las órdenes (`midi`, `ordenar`, `cruces`, `golpes`,
`luces`, `onda`, `nivelar`, `editar`, `origen`, `respaldos`, `tempo`, `hoja`,
`parecidos`, `ficha-midi`, `buscar`, `uniones`, `pizarrita`, `instalar`) y
`trarin <orden> --help`, sus opciones. Sin instalar el paquete, `python -m trarin`
hace lo mismo.

### Describir un show

El show se describe en un JSON (`canciones`, `separacion`, `cambio_tempo`, `base`,
`match`); el formato completo está al comienzo de `trarin/show.py`. Opciones:

* `"buses": "juntar"` (por defecto: el bus «Aux 1» de cada canción va al «Aux 1»
  del show) o `"separar"` (cada canción usa buses y auxiliares propios,
  «Aux 1 (canción)»), y `"copiar_solo": true` para copiar el solo de las pistas
  (por defecto no, para que una canción no silencie a las demás).
* `"frecuencia": 48000`: el show va a esa frecuencia; el audio de las canciones
  con otra se convierte (a «Audio Files» junto al show) y las posiciones se
  recalculan.
* En una canción, `"fin_compas": 65` la corta al inicio de ese compás (clips,
  notas, automatización, marcadores, tempo y compases posteriores no se copian;
  `"cortar": false` solo la usa para ubicar la siguiente).
* En una canción, `"cambio_tempo"` propio; con `{"modo": "fin"}` el tempo de esa
  canción rige desde que termina la anterior (si la anterior acaba muy lenta).
* En una canción, `"pistas_extra"`: pistas armadas desde archivos WAV (con su
  posición en segundos) o desde un .mid (con el tempo con que se leen sus negras
  y el tramo); ver `_con_extras` en `show.py`. `"sesion"` también puede ser un
  modelo común (una canción sin sesión de Pro Tools, armada solo con
  `pistas_extra`).
* `"buses_show"`: buses y auxiliares del show. Cada pista de audio va al bus
  cuyo patrón calza con su nombre (o a la salida principal), sin envíos, y los
  auxiliares de las canciones no se copian; cada bus tiene un auxiliar estéreo
  que lo recibe. Ver el ejemplo en `docs/arquitectura.md`.

## Servidor MCP

```
pip install -e ".[mcp]"
trarin instalar            # agrega «trarin» a la configuración de Claude Desktop y revisa dependencias
trarin instalar --revisar  # solo revisa, no escribe
```

`trarin instalar` escribe la entrada en `claude_desktop_config.json` apuntando al
Python con que se corrió. A mano, dentro de `mcpServers`:

```json
"trarin": {"command": "/ruta/a/python", "args": ["-m", "trarin", "mcp"],
           "env": {"PYTHONIOENCODING": "utf-8"}}
```

Cualquier otro cliente MCP sirve igual: el servidor es `trarin mcp`, por stdio.

Herramientas (53), pensadas para que la IA sepa lo necesario sin leer la sesión
entera:

| Grupo | Herramientas |
|---|---|
| Mirar (solo leen) | `resumen_sesion`, `detalle_pista`, `rutas_sesion`, `archivos_audio`, `leer_sesion`, `comparar_sesiones`, `comparar_con_original`, `uniones`, `revisar`, `revisar_midi`, `paneo_live` |
| Notas del proyecto | `notas`, `anotar` |
| Shows | `compatibilidad`, `planear_show`, `armar_show`, `hoja_show`, `luces` |
| Cambiar una sesión | `plan_consolidar`, `consolidar`, `plan_grupos`, `crear_grupos`, `plantillas`, `ordenar_pistas`, `editar_clips`, `cruces`, `marcar_secciones`, `reparar_midi`, `salidas_midi`, `golpes_a_midi`, `nivelar`, `proponer_tempo`, `escribir_sesion` |
| Una sola sesión con respaldos | `reemplazar_sesion`, `respaldos_sesion`, `deshacer` |
| Sonido | `fichas_calcular`, `ficha`, `ficha_midi`, `parecidos`, `buscar_sonidos`, `mapa_cancion`, `que_suena`, `revisar_niveles`, `onda`, `escuchar`, `exportar_pizarrita`, `medir_bpm`, `convertir_audio` |
| Trabajos | `trabajo_esperar`, `trabajo_estado`, `trabajo_matar`, `trabajos_recientes` |

- Lo que tarda (armar, consolidar, fichas, escuchar, bpm, convertir audio) corre
  como **trabajo** en un proceso aparte: la herramienta devuelve un id y
  `trabajo_esperar` entrega el avance hasta 40 s por llamada.
- **Fichas de sonido**: se calculan una vez por sesión, a pedido, y quedan en
  `trarin/fichas/` junto a ella. Por pista, canción o archivo: dónde suena de
  verdad, silencios, pico, RMS, LUFS (BS.1770), saturación, clics, cortes, mono o
  estéreo, bandas, nota más presente, golpes y pulso, forma de la curva de nivel
  y una línea en palabras. Son aproximadas: con el clip gain, sin fader, plugins
  ni fundidos.
- **Notas del proyecto**: `trarin/notas.md` junto a la sesión, con decisiones, lo
  hecho y lo pendiente, para retomar en otra conversación.
- **Plantillas de reglas** (categorías, buses, grupos) con nombre, para reutilizar
  entre shows: van en `~/.trarin/plantillas/`.

## Qué toca trarin en el disco

- **Las herramientas que escriben una sesión piden un archivo de salida que no
  exista**; la sesión de origen no se modifica. Después de escribir, vuelven a
  leer lo escrito, lo revisan, lo comparan con el origen y agregan una línea en
  las notas.
- **`reemplazar_sesion` y `deshacer` (y `trarin respaldos`) sí mueven la sesión
  de trabajo**: es su función. La sesión vigente pasa a `Session File Backups`
  (`<nombre>.trarin.<fecha>_<n>.ptx`) y la nueva queda en su lugar; `deshacer`
  vuelve atrás y tampoco borra nada.
- Junto a cada sesión se crea una carpeta `trarin/` (notas, fichas, escuchas,
  ondas, hoja del show).
- En la carpeta del usuario: `~/.trarin/cache` (modelos ya leídos),
  `~/.trarin/plantillas` y los trabajos (`%LOCALAPPDATA%\trarin\trabajos` en
  Windows, `~/.trarin/trabajos` en los demás).
- El servidor MCP lee y escribe en cualquier ruta que le indique el cliente, con
  los permisos de quien lo corre.

Variables de entorno: `TRARIN_TRABAJOS`, `TRARIN_CACHE` y `TRARIN_PLANTILLAS`
cambian esas carpetas; `TRARIN_SIN_CACHE=1` apaga la caché. La caché se guarda
con `pickle`: no apuntar `TRARIN_CACHE` a una carpeta donde otras personas puedan
escribir.

## Representación (`formato: trarin/0`)

- Toda posición musical se guarda en **ticks**: 960.000 por negra, el mismo
  valor que usa Pro Tools internamente. Es el dato exacto.
- Cada evento agrega, como ayuda de lectura, `posicion` (`compás|tiempo|ticks`,
  como la muestra Pro Tools), `segundos` y `muestra` (según la frecuencia de la
  sesión), calculados con el mapa de tempo.
- `tempo[].pulso_ticks` indica la figura que lleva el BPM: 960000 = negra,
  480000 = corchea.
- Antes del primer evento de tempo rige el BPM de ese primer evento.

El modelo común a los tres programas (`trarin leer … --comun`, `formato: trarin/1`)
está en [`docs/modelo_comun.md`](docs/modelo_comun.md).

## Pruebas

```
python -m unittest discover -s pruebas
```

Sin dependencias; corren sobre sesiones chicas guardadas por Pro Tools
(`pruebas/sesiones/`), varias de ellas «oráculos»: lo que escribe trarin se
compara con lo que guardó Pro Tools al hacer lo mismo a mano.

## Preguntas abiertas

- En algunas sesiones el compás que declara un cambio de métrica no coincide
  con el cálculo desde el compás 1 (p. ej. compás 101 en la negra 248 con
  5/8 desde el inicio, donde se esperaría 250).
- Mapas de tempo cuyo primer evento no está en el cero.

## Licencia

Copyright (C) 2026 Juan Pablo Rapiman Risco

trarin es software libre: puede redistribuirse y modificarse bajo los términos de la
[GNU Lesser General Public License](LICENSE) publicada por la Free Software Foundation,
versión 2.1 o (a elección) cualquier versión posterior (`LGPL-2.1-or-later`).

Se distribuye con la esperanza de que sea útil, pero SIN NINGUNA GARANTÍA.

`trarin/protools/descifrado.py` es un port de
[libptformat](https://github.com/zamaudio/ptformat) (Damien Zammit), que tiene
la misma licencia.

Pro Tools, Ableton Live y FL Studio son marcas de sus respectivos dueños.

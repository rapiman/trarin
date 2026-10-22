# Formato de sesión Pro Tools (.ptx) — lo que trarin sabe

Este documento junta todo lo decodificado sobre el formato `.ptx`. Las pruebas se hicieron sobre
un corpus de 51 sesiones de Pro Tools HD 12.5 en Windows (48 y 96 kHz), sobre experimentos
controlados en Pro Tools 12.5 (una acción por sesión guardada, comparadas byte a byte) y sobre
tres sesiones de Mac incluidas en ptformat: Pro Tools HD 12.8.3, Pro Tools 2019.6 y
Pro Tools Ultimate 2026.4. Cada afirmación lleva una marca de confianza:

- **[verificado]**: comprobado en todo el corpus o contra lo que muestra Pro Tools.
- **[corpus]**: consistente en el corpus, pero sin experimento que lo confirme.
- **[externo]**: tomado de otra fuente y todavía no comprobado en PT12.

Fuentes externas usadas:

- [ptformat](https://github.com/zamaudio/ptformat) (LGPL-2.1+): descifrado, bloques, regiones y MIDI básico.
- [pt_api](https://github.com/sebedard-creator/pt_api) (LGPL-2.1+): especificación en francés
  (`pt_format_specs.md`) validada sobre Pro Tools Ultimate 2024.3. Incluye **escritura** de `.ptx`.
- [PTX2DAW](https://github.com/raggedrec/PTX2DAW.project) (MIT): conversor a DAWproject; usa ptformat.
- [Library of Congress, fdd000639](https://www.loc.gov/preservation/digital/formats/fdd/fdd000639.shtml): historia de extensiones y firma.

Las diferencias entre PT12 y PT2024 son reales: varios campos cambian de lugar (ver §13).

## 1. Envoltura y cifrado

| Offset | Tamaño | Contenido |
|---|---:|---|
| `0x00` | 1 | `0x03` |
| `0x01` | 16 | `"0010111100101011"` en ASCII |
| `0x11` | 1 | Orden de bytes: `0` little endian (único visto) |
| `0x12` | 1 | Modo XOR: `0x01` (PT 5–9) o `0x05` (PT10 y posteriores) |
| `0x13` | 1 | Valor del que se obtiene el delta |

Desde `0x14` todo va con XOR [verificado]. En modo `0x05` el delta es `-i mod 256`, donde `i` cumple
`(i*11) & 0xFF == valor`. La clave del byte en el offset `o` es `((o >> 12) & 0xFF) * delta & 0xFF`,
así que la clave cambia cada 4096 bytes. En modo `0x01` se usa `53` en vez de `11` y `o & 0xFF`.
La transformación es simétrica: la misma operación cifra y descifra.

## 2. Bloques

```
0x5A | u16 tipo_bloque | u32 tamaño | u16 tipo_contenido | carga…
```

- `tamaño` cuenta desde `tipo_contenido` (incluido) hasta el final. El bloque ocupa `7 + tamaño` bytes.
- Los hijos son bloques `0x5A` que caen dentro de la carga del padre. En trarin, `Bloque.inicio`
  apunta a `tipo_contenido`, por lo que la carga empieza en `inicio + 2`.
- Muchos bloques aparecen dos veces seguidas. Se usa la primera copia.
- Algunos tipos tienen carga fija y nunca contienen hijos, aunque su contenido tenga un `0x5A` que
  parezca una cabecera [externo]: `0x0002 0x1028 0x104F 0x204D 0x260A 0x262F 0x2637`.
  En el mapa `0x1054`, los primeros 4 bytes son un contador y nunca un bloque.
- Muchas listas (`0x2624`, `0x1004`, `0x262A`…) llevan un u32 cantidad justo tras el tipo de
  contenido. Con 90 elementos (`0x5A`) esa cantidad parece la cabecera de un hijo que se traga a los
  verdaderos: el lector la descarta si en los 4 bytes siguientes empieza un bloque válido
  (encontrado al juntar 90 canales en un show).

### 2.1 Tabla de punteros (necesaria para escribir)

- El primer bloque, en `0x14`, es especial: `5A 01 00 04 00 00 00` + **u32 offset absoluto del
  bloque final `0x0002`** (11 bytes, sin `tipo_contenido`) [verificado en las 51 sesiones].
- El último bloque es `0x0002` y termina justo al final del archivo [verificado]. Es un **índice**
  de posiciones absolutas de bloques; decodificado por completo (leerlo y reescribirlo da el mismo
  archivo en las 95 sesiones del corpus) [verificado]:

  ```
  u16 tipo_contenido | u32 n entradas
  entrada = u32 profundidad | profundidad × paso | u32 m | m × grupo
  paso    = u16 tipo | u32 posición del padre (FFFFFFFF = raíz) | u8 marca | u32 número
  grupo   = u8 1 | u16 bytes (= 4n) | u16 n | n × u32 posición | 6 bytes en cero
  ```

  Las listas (marca 1, número 0) de `0x1015`, `0x1054`, `0x2519` y `0x2624` tienen un grupo por
  bloque: el primero es la lista y después uno por hijo, en orden. `(0x2519, marca 0) + 0x251A`
  guarda los `0x251A` de la primera mitad; `(0x2519, 1, k) + 0x251A`, uno por pista de la segunda
  mitad; `(0x2519, 1, último) + 0x2716` lleva en `número` la cantidad de pistas; y
  `(0x2624, 1, k)(0x261C|0x261E)(0x261B)(0x2627)`, uno por canal. El índice no apunta a `0x1050`,
  `0x2077`, `0x1003`, `0x2629` ni a los trozos de `0x2000`.
- Consecuencia: **cualquier cambio de tamaño de un bloque obliga a recalcular esos offsets**.
  `reconstruccion.py` corrige tamaños, el puntero de `0x14` y las posiciones del índice; para
  agregar bloques nuevos al índice está `indice.py`.

## 3. Datos de la sesión

| Bloque | Contenido |
|---|---|
| `0x0003` | Producto, versión y plataforma: u32 largo en +3, nombre en +7 (`"Pro Tools HD"`), versión en `+3+largo+8`; luego `"12.5.0"`, `"Release"`, `"Pro Tools Session File"`, `"Windows"` [verificado] |
| `0x1028` | Frecuencia de muestreo, u32 en carga +2 [verificado]; luego la ruta a `Last Used.pio` |
| `0x204D` | Timecode [verificado por experimento]. Byte 0: código de cadencia (`0x02` 25, `0x05` 29,97 drop, `0x07` 30, `0x09` 23,976; resto según la tabla de pt_api). u32 en +9: inicio de la sesión en cuadros a la cadencia nominal (86 400 = 01:00:00:00 a 24). La cadena final `HH:MM:SS:FF` es la duración de la sesión (Session Length). En 2026 el bloque mide 69 bytes y repite los datos, pero el byte 0 y +9 valen igual |
| `0x2067` | Cadenas `Info #1..#5`, ruta de la sesión, identificador y nombre del archivo |
| `0x204B` | Nombre interno de la sesión |
| `0x2715` | `sessionMetadataBase64` |
| `0x2435` | Metadatos de título y compositor |
| `0x0F3D`/`0x0F3C` | Volúmenes de disco conocidos |
| `0x2024`, `0x2530` | Formatos de contador (compases, min:seg, timecode, pies+cuadros, muestras): valores de grilla y nudge [corpus] |
| `0x2511` | Snaps; `0x2508` `"Default"` |
| `0x2055` | Ajustes del click (notas 60, velocidades 100/127, duración, `120.0` double) [corpus] |
| `0x2025` | Desconocido; varía entre sesiones (por ejemplo 122 880 000 ticks = 128 negras) |

## 4. Tiempo musical

- Ticks: 960 000 por negra. Las posiciones musicales llevan un desplazamiento de `10**12`.
- **Tempo**: patrón `Const 01 00 2E 00 00 00 TMS`. Desde `TMS`: tick int64 en +19, bpm double en
  +29, pulso u32 en +37 [verificado con la sesión de tempo variable].
- **Tempo, estructura completa** (bloque `0x2028`, dos copias): tras `28 20 'Tempo' 02 00`, u32 largo
  (4 + 61 × eventos), u32 cantidad, 4 bytes, eventos de 61 bytes (`Const` … `TMS`: tick en +30, BPM
  en +40, figura del pulso en +49, banderas en +21 y +39 que valen 1 en el primer evento) y 13 bytes
  finales [verificado: agregar un tempo con trarin da los mismos bytes que con Pro Tools].
- **Compases** (bloque `0x2029`, dos copias): tras `29 20 'Meter' 02 00`, u32 largo del resto,
  u32 cantidad y eventos de 36 bytes: int64 tick (+10¹²), u32 número de compás, u32 numerador,
  u32 denominador y 16 bytes fijos; luego una tabla de 16 bytes por evento (u32 compás mostrado,
  u32 1, 8 bytes en cero) y 8 bytes finales. Sin eventos, Pro Tools usa 4/4 [verificado: trarin
  reescribe la lista y el resultado es idéntico byte a byte al que guarda Pro Tools].
- **Marcadores / ubicaciones de memoria** [verificado por experimento]: bloques `0x2077` hijos de
  la primera lista `0x2030` que los contiene (la lista aparece dos veces). Carga: u16 número;
  byte +3 banderas (bit 1 = selección; en el corpus aparecen 1 y 9 para marcadores); u32 largo en
  +6 y nombre desde +10. Con E = fin del nombre: inicio en E y fin en E+8, cada uno 5 bytes de
  tick (+10¹²) seguidos de `00 00 40`; comentario = u32 largo en E+80 y texto en E+84. El lector
  anterior de trarin buscaba un patrón de bytes que fallaba cuando el bloque medía más de 255
  bytes: perdía los marcadores de 16 de las 51 sesiones. pt_api describe además las reglas de
  marcadores múltiples de PT2024 (`0x2519 → 0x251B → 0x251C`) [externo].
  Escritura [verificado en Pro Tools 12]: nombre, inicio y fin se cambian en las dos copias del bloque;
  un nombre de otro largo cambia el tamaño y se reconstruye la sesión. El comentario está dos veces
  dentro del bloque: u32 largo + texto en E+80 y 4 bytes después del último hijo (en sesiones de otras
  versiones E+80 no es el comentario; ahí trarin no lo escribe).
  Agregar y borrar [verificado en Pro Tools 12]: cada lista `0x2030` lleva u32 cantidad en +2, los
  `0x2077` y 4 bytes en cero al final. Un marcador nuevo se copia de otro marcador común de la sesión
  (o de uno de Pro Tools 12.5 si no hay), con número = mayor + 1 y un UUID nuevo en los últimos 16
  bytes, y se inserta antes de esos 4 bytes en ambas listas. El índice `0x0002` no apunta a los
  marcadores, así que solo se corrigen tamaños y posiciones.

## 5. Archivos y regiones de audio

- **Archivos**: `0x1004` con hijos `0x103A` (nombres en +11; se descartan `.grp`, `Audio Files` y
  `Fade Files`). Largos en `0x1003`/`0x1001`, u64 en +8 [verificado].
- **Regiones**: lista `0x262A` (u32 cantidad) con hijos `0x2629`. El índice de una región es su
  posición en la lista. Cada `0x2629` contiene un `0x2628`: u32 largo + nombre, luego los
  "tres puntos" [verificado]:
  - byte `j`: banderas; bytes `j+1..j+3`: anchos en nibbles del desplazamiento en el archivo, del
    largo y del inicio original; los valores siguen desde `j+5`.
  - El archivo de la región es el u32 que sigue al final del `0x2628`.
  - **Clip gain**: int32 con el índice en el diccionario `0x2637` (`-1` = sin clip gain), 27 bytes
    después de la última marca `FF FF 04 00 04 00` del `0x2628` [verificado en 45 833 bloques de
    PT 12.5, 12.8, 2019 y 2026]. En PT 12 coincide con los últimos 4 bytes; en 2026 el bloque
    sigue con `00 00` y un UUID de 16 bytes.
- **Archivos nuevos** [verificado importando en Pro Tools 12]: cada archivo suma una cadena de
  entradas al `0x103A` (carpeta, archivo, volumen, carpetas hasta la raíz) y un `0x1003`. Su u32 en
  +2 es **el número de la entrada del archivo en el `0x103A`** (se cumple en 3 472 de 3 478 archivos
  del corpus). En el `0x1001`: u32 frecuencia de muestreo en +2, u8 bits en +7, u64 largo en +8 y un
  identificador de 8 bytes tras `2A 00 00 00`. En el `0x2106` (metadatos BWF): FILETIME en +31,
  u32 posición del audio dentro del WAV en +41, y tras el texto `Pro Tools` y 33 ceros: `01`, un
  byte con (largo − 1) en el nibble alto, 3 bytes, la referencia de tiempo BWF (largo bytes) y otro
  FILETIME. Los WAV que crea Pro Tools llevan su identificador en el chunk `umid` (tras
  `00000000 2A000000`) y el FILETIME en `minf`; la sesión debe usar esos mismos valores, o Pro Tools
  no la abre ("Assertion in FF_AudioFile.cpp").
- **Región estéreo** [verificado importando un WAV estéreo en Pro Tools 12]: Pro Tools lo convierte
  en dos archivos mono (`.L` y `.R`) y crea dos regiones mono (`nombre.L` y `nombre.R`), una
  colocación en el `0x1052` de cada canal de la pista. No hay otro bloque que las una.
- pt_api documenta además un segmento de 48 bytes con UUID por región, un enlace de 104 bytes con
  el índice de archivo en +96 y un perfil de "sync point" (`00 30 22 44 08`) [externo].

### 5.1 Diccionario de clip gain `0x2637` [verificado]

```
u32 cantidad
registro × cantidad:
  01 46 01 00 | u32 tamaño (el registro mide 8 + tamaño) | 00 00 |
  u32 nodos (+10) | u16 04 00 (+14) | u16 segmentos (+16) | u32 desconocido (+18) |
  nodos × [u32 posición en muestras, float32 dB]  (desde +22)
```

Un solo nodo es una ganancia fija. Hay curvas de 2 nodos en el corpus (pt_api solo acepta 1, 3 o 4).
En PT12 el campo +18 no es la duración de la región; sigue sin identificar.

Una región nueva con clip gain suma un registro al final de `0x2637` (con +18 en 0, como varios del
corpus) y lleva su número en lugar de `-1` [verificado en Pro Tools 12: los clips copiados a un show
guardan de nuevo con la misma ganancia fija o curva].

**Archivos y regiones nuevos** [verificado importando un archivo en Pro Tools 12 y repitiéndolo con
trarin]: la lista de rutas `0x103A` lleva u32 cantidad y entradas `tipo u8 | u32 índice del padre |
u32 largo + nombre | 4 bytes` (tipo 0 volumen con su serie y padre FFFFFFFF, 1 carpeta, 2 archivo con
su tipo `EVAW`/`FFIA`); cada archivo nuevo suma su cadena de carpetas hasta el volumen. El `0x1004`
lleva la cantidad de archivos y un `0x1003` por archivo (u32 número; `0x1001` con el largo u64 en +8
y los bits en +7; `0x1033`; `0x2106` con metadatos BWF y un identificador de 16 bytes). Cada región
es un `0x2629` con su `0x2628` (nombre, anchos en nibbles, desplazamiento y largo, `FF FF 04 00 04 00`
y el índice de clip gain) seguido del índice de archivo. Las regiones nuevas de trarin son idénticas
byte a byte a las de Pro Tools. El índice `0x0002` no apunta a estos bloques.

## 6. Pistas, playlists y colocaciones

- **Lista de pistas**: `0x2519` con hijos `0x251A`: u16 tipo en +2 (0 audio, 1 MIDI, 2 auxiliar;
  `0x0B` carpeta según pt_api) y nombre en +4. La lista viene dos veces [verificado].
- **Playlists de audio**: `0x1015` con hijos `0x1014`: nombre, luego una lista de números de canal.
  Cada número indexa una entrada `0x1052` del mapa `0x1054`. Las playlists cuyo nombre no es el de
  una pista activa son alternativas guardadas [verificado].
- **Colocación** `0x1050 → 0x104F` (offsets desde la carga del `0x104F`) [verificado]:

| Offset | Tamaño | Contenido |
|---|---:|---|
| +0 | 1 | Mute de la región: 1 = silenciada |
| +2 | 4 | Índice: región en `0x262A`, o fundido en `0x2630` |
| +7 | 5 (u64) | Muestra del ancla |
| +15 | 1 | Casi siempre 3 en regiones y 1 en fundidos, pero no es confiable: en 183 regiones vale 1 y el índice no sería válido como fundido. pt_api lo usa como tipo en PT2024 |

  El **primer byte después del `0x104F`** distingue el espacio de nombres: `1` = el índice apunta
  a un fundido, `0` = a una región [verificado]. En PT 12.5 ese byte cierra el `0x1050`; en
  12.8, 2019 y 2026 le sigue otro byte (`01`).

  Agregar o quitar colocaciones [verificado en Pro Tools 12]: el `0x1052` de cada canal lleva el
  nombre, u32 cantidad de colocaciones y los `0x1050`; una colocación nueva es una copia de otra con
  índice de región y muestra nuevos. El índice `0x0002` no apunta a los `0x1050`.
  Las colocaciones van ordenadas por posición dentro de la playlist (572 de 586 `0x1052` y 135 de 136
  `0x1057` del corpus); una colocación nueva se inserta antes de la primera con posición mayor. Si
  queda fuera de orden, Pro Tools abre la sesión pero al volver a guardarla pierde colocaciones
  [verificado con MIDI: un clip agregado al final en el tick 0 hizo desaparecer el último].
  **Escritura** [verificado en Pro Tools 12]: mover una región cambia la muestra del ancla en cada canal
  y la de sus fundidos de entrada y salida; silenciarla cambia el byte de mute. Ambos son de tamaño
  fijo, así que no hace falta reconstruir la sesión.

- **Renombrar una pista** [verificado renombrando en Pro Tools 12]: el nombre (u32 largo + texto)
  está en los bytes propios de `0x2519`, en los dos `0x251A`, en la playlist activa `0x1014` y en
  las entradas `0x1052` de sus canales (audio), en `0x1057` (MIDI), y en el nombre del canal
  `0x102D → 0x2619` solo para auxiliares y master (en audio ese nombre es el de creación).

- **Crear una pista de audio mono** [verificado en Pro Tools 12, comparando con una pista creada en
  Pro Tools]. Se copia una pista de audio mono existente con una sola playlist, y se cambia:
  - en los bytes propios de `0x2519`: u32 cantidad de pistas (justo antes de la primera entrada; la
    cabecera no tiene largo fijo) y una entrada nueva al final:
    `u16 tipo | nombre | u16 estéreo | uuid de 16 bytes | u16 número | u8 0`;
  - entre las dos mitades de `0x251A` hay otra vez un u32 con la cantidad de pistas;
  - un `0x251A` nuevo al final de cada mitad, con el nombre, el número y el uuid de la pista. Su
    identificador de 8 bytes (los que siguen a `2A 00 00 00`) es propio de cada pista y lo comparte
    con el nombre del canal `0x102D → 0x2619`; repetido, Pro Tools no abre la sesión;
  - una playlist `0x1014` y una entrada vacía `0x1052` (nombre + 4 ceros + `01 00`). Pro Tools
    guarda primero las playlists en uso (las que se llaman como su pista) y después las
    alternativas; la nueva va al final de las primeras, y se corren los números de playlist de los
    canales y los números de `0x1052` de las playlists que quedan después. La playlist también
    tiene un identificador de 8 bytes propio;
  - un canal `0x261C` copiado del de la plantilla: identificadores de parámetros nuevos (u16 n y n
    u32 consecutivos tras los hijos del `0x261B`, mayores que todos los de la sesión), uuids de 16
    bytes nuevos tras `FF 7F`, uuid de la pista, nombre, y tras el `0x261B`
    `u32 número de canal de audio | u16 cantidad de playlists | u32 playlist | FF FF`. El u32 final
    de su `0x2015` es correlativo entre los canales;
  - el grupo `<ALL>` `0x202A` (u32 cantidad + u16 por pista), `0x202C` y una tira `0x2589` en
    `0x258A` (u32 cantidad justo antes de las tiras; u16 número de pista en +2);
  - en el índice: las posiciones nuevas en las listas, una entrada `(0x2519, 1, n+1) + 0x251A` antes
    de la de `0x2716` (cuyo número pasa a n+1) y una entrada para el canal.
- **Crear grupos de pistas** [verificado en Pro Tools 12, comparado byte a byte con grupos creados
  en Pro Tools]: `0x202B` lleva u32 cantidad y un `0x202A` por grupo (el primero `<ALL>`), y luego
  otra vez la cantidad y la misma lista. `0x202A`: u32 largo y nombre, u8 tipo (1 mezcla, 2 mezcla y
  edición), u32 número (`<ALL>` FFFFFFFF; los demás 1, 2, 3…, letras a, b, c…), u8 edición y u8
  mezcla activos (0101 / 0001; Pro Tools los cambia al usar los grupos), u32 cantidad de pistas y
  un u16 por pista (su lugar en `0x2519`), `FEFF 1180 0100 0000 0000 FFFF` y un `0x258E` fijo de 61
  bytes. El índice no apunta a los `0x202A`. Una pista puede estar en varios grupos.
- **Crear una pista MIDI** [comparado con una pista MIDI creada en Pro Tools 12]: entrada en `0x2519`
  y `0x251A` en cada mitad con tipo 1; una playlist `0x1057` vacía al final de `0x1058` (nombre,
  u32 cantidad de colocaciones en 0 y sus hijos sin `0x1056`); un canal `0x2620` (sin `0x261B` ni
  identificadores de parámetros: nombre e identificador de 8 bytes en `0x102D → 0x2619`, UUID de
  la pista y el número correlativo del `0x2015`); grupo `<ALL>` y una tira `0x2589`. `0x202C` no
  cambia. En el índice: posiciones en las listas de `0x1058`, `0x2519` y `0x2624`, la entrada
  `(0x2519, 1, n+1) + 0x251A` y el número de la de `0x2716` (el `0x2620` no tiene `0x2627`).
  `0x2004` es la lista de salidas MIDI (u32 cantidad; cada `0x2005` lleva dispositivo y canal):
  cada canal MIDI apunta a la suya con un u32 (1 = la primera) en +14 de su `0x2036`, y el
  `0x2620` lleva tras su `0x102D` `u32 n | u32 1 | u32 n | u8 1`, con n = número del canal entre
  los `0x2620`/`0x2621` (desde 0). Si la pista nueva comparte la salida o el número con otra,
  Pro Tools abre la sesión pero se cae al cerrarla [verificado]; trarin le da una salida propia
  (copia de la de la plantilla, al final de la lista) y el número siguiente. Si la sesión no tiene
  pistas MIDI, trarin usa los bloques de la pista creada en Pro Tools 12 (`plantillas_midi.py`).
- **Crear una pista de audio estéreo** [verificado en Pro Tools 12, comparando con una pista estéreo
  creada en Pro Tools]: igual que la mono, con estas diferencias:
  - u16 estéreo = 1 en la entrada de `0x2519` y tras el nombre en los dos `0x251A`;
  - la playlist `0x1014` lleva el byte tras el nombre en 1, dos números de `0x1052` (dos entradas
    vacías nuevas en `0x1054`, y en el índice) y su u16 final en 1;
  - en el canal, tras el nombre `0x102D` del `0x261B`: `u8 estéreo | u8 canal de entrada` pasa de
    `00 01` o `00 02` (L/R) a `01 00`; la entrada (u32 largo + texto, 9 bytes después de los
    identificadores del canal) es la estéreo, y la identifican los 8 bytes que siguen a
    `00000000 2A000000`; el fader `0x1029` lleva paneo −100 en +15 y +100 en +43, y lo mismo 86
    bytes después (+101 y +129); el segundo `0x260C` del `0x260D`, vacío en mono, lleva una copia
    de la lista de paneo del primero, con valor −100 en la primera y +100 en la segunda (int16 en
    +28 del `0x260A`).
- **Crear una pista auxiliar** [verificado en Pro Tools 12, comparando con un auxiliar estéreo y uno
  mono creados al final en Pro Tools]: entrada de tipo 2 en `0x2519` (u16 estéreo según los canales)
  y un `0x251A` en cada mitad; un canal `0x261E` al final de `0x2624` (con `0x261B` y `0x200D`, sin
  datos de playlist tras el `0x261B`); grupo `<ALL>`, una tira `0x2589` y en el índice las listas
  de `0x2519` y `0x2624`, la entrada `(0x2519, 1, n+1) + 0x251A`, el número de `0x2716` y la entrada
  del `0x2627` del canal (con el tipo `0x261E` en su camino). No tiene playlists `0x1014`/`0x1052`, y
  `0x202C` no cambia. trarin copia un auxiliar de la sesión con la misma cantidad de canales:
  nombre (también en `0x102D → 0x2619`), uuid, identificador de 8 bytes, identificadores de
  parámetros, uuids tras `FF 7F` y el número siguiente del `0x2015`; conserva su entrada y su salida
  (Pro Tools deja sin entrada al auxiliar que crea). Si la sesión no tiene un auxiliar del mismo
  ancho, da error.
- **Quitar una pista de audio** [verificado en Pro Tools 12: comparado con una pista borrada en Pro
  Tools sobre la misma sesión guardada de nuevo sin cambios, y abriendo, guardando de nuevo y
  cerrando las sesiones de trarin]. Es lo inverso de crearla:
  - en `0x2519`: la cantidad (antes de la primera entrada y entre las mitades), la entrada y sus dos
    `0x251A`. Las pistas siguientes bajan su número (u16 de la entrada y u32 a 22 bytes del final
    del `0x251A`); una pista con número 0 no tiene número y no corre a las demás. Los
    identificadores de 8 bytes pueden contener `5A`, que el lector toma por un hijo: trarin
    reescribe el `0x251A` entero;
  - las playlists del canal (u32 tras su `0x261B`, índices de `0x1015`) y sus entradas `0x1052`;
    las playlists y canales que quedan corren sus índices, y los canales de audio siguientes bajan
    su número de canal de audio;
  - el canal de `0x2624`; los `0x2015` mayores que el del canal bajan en 1;
  - la pista sale de cada grupo `0x202A` y las siguientes bajan (si es la única de un grupo, trarin
    da error); `0x202C` como al crear; su tira `0x2589` y el u16 de las siguientes;
  - en el índice, los grupos de los bloques quitados; las entradas `(0x2519, 1, k) + 0x251A` y
    `(0x2624, 1, k) + … + 0x2627` siguientes bajan su número, y la de `0x2716` lleva la cantidad.
  Las regiones y archivos de la pista quedan en la lista de clips. Pro Tools también borra las
  definiciones de fundidos `0x2630` que dejan de usarse; trarin las deja, y Pro Tools abre la
  sesión igual.
- **Quitar una pista auxiliar** [verificado en Pro Tools 12; quitar el auxiliar creado por Pro Tools
  deja la misma estructura que antes de crearlo]: como una pista de audio, sin playlists y sin
  cambiar `0x202C`. Las pistas que envían a su bus siguen enviando al bus.
- **Quitar una pista MIDI** [verificado del mismo modo en Pro Tools 12]: lo mismo en `0x2519`,
  el canal (`0x2620`), los `0x2015`, los grupos `0x202A`, las tiras y el índice (lista de
  `0x1058` en vez de `0x1015`/`0x1054`); `0x202C` no cambia. Además:
  - su playlist `0x1057` sale de `0x1058` (u32 cantidad);
  - su salida `0x2005` sale de `0x2004` si ningún otro canal la usa, y los u32 en +14 de los
    `0x2036` de los demás canales MIDI e instrumento que apuntan más allá bajan en 1;
  - los canales `0x2620`/`0x2621` siguientes bajan su número n: en `0x2620`,
    `u32 n | u32 1 | u32 n | u8 1` tras el `0x102D`; en `0x2621` (instrumento),
    `u32 n | u16 1 | u32 n | u8 1` justo antes de su `0x2010`.
  Sus regiones MIDI quedan en la lista de clips.

## 7. Fundidos `0x2630 → 0x262F` [verificado en 16 124 geometrías]

Lista con u32 cantidad. Cada geometría:

| Offset | Contenido |
|---|---|
| +5 | Anchos en nibbles: **bajo = ancho del pre-roll, alto = ancho de la duración** (0, 1, 2 o 3 bytes) |
| +8 | Pre-roll en muestras (la parte del fundido antes del ancla) |
| siguiente | Duración total en muestras |
| m | Modo: `1` cruzado, `2` salida, `3` entrada |
| m+1 | Pendiente: `1` Equal Power, `2` Equal Gain [verificado por experimento] |
| curvas | `[tipo u8, preset int16]`: tipo `0` Standard, `1` S-Curve, `2` preset; el preset se cuenta desde la curva lineal (el segundo de los siete quedó como −2) |

Ubicación de las curvas desde el byte de modo: entrada m+6 [verificado]; salida m+12 [corpus];
cruce: curva de salida m+12 [verificado], curva de entrada m+19 [sin aislar: en el experimento
quedó S-Curve aunque se eligió Standard, posiblemente por el vínculo entre ambas].

En todo el corpus el modelo es consistente: una entrada tiene pre-roll 0, una salida tiene pre-roll
igual a la duración, y un cruce tiene un pre-roll intermedio, casi siempre la mitad. El fundido
empieza en `ancla − pre_roll` [verificado: el cruce del experimento empieza en 1|4|480, como
muestra Pro Tools].

**Escritura** [verificado en Pro Tools 12 con una entrada, un cruce y una salida en clips nuevos,
guardando de nuevo y releyendo]: la geometría tiene formato fijo en todo el corpus (16 726 de
16 792 se reproducen byte a byte): `2F 26`, 5 ceros, anchos, `00 00`, pre-roll y duración con el
mínimo de bytes, y la cola de cada modo: entrada = modo, pendiente, 4 ceros, curva, 3 ceros
(en 66 geometrías esos 3 bytes repiten la curva); salida = modo, pendiente, 10 ceros, curva;
cruce = modo, pendiente, 10 ceros, curva de salida, 4 ceros, curva de entrada. Una geometría
igual a una existente se reutiliza; si no, va al final de `0x2630` (el índice solo apunta a la
lista). Cada fundido se coloca como un `0x1050` en el `0x1052` de cada canal: índice de la
geometría en +4 del `0x104F`, ancla (inicio + pre-roll) en +9, el byte que sigue al `0x104F` en
1, y el byte +17 del `0x104F` en 1 (en el corpus, 1 en 18 828 de 18 872 fundidos y 3 en 22 715
de 23 121 regiones). Hay que respetarlo en las dos: un fundido con 3, o una región con 1 junto a
un fundido de salida que termina en ella, y Pro Tools descarta ese fundido al guardar. En la misma posición, el
fundido va antes que la región. El pre-roll de un cruce es la distancia hasta el inicio del clip
que empieza dentro de él.

## 8. MIDI

- Pistas MIDI: `0x1058/0x1057` (nombre), `0x1056/0x104F`: índice en +4 y tick en +9 (5 bytes, menos `10**12`).
- Regiones MIDI: `0x2634/0x2633/0x2628`: ventana `[desplazamiento, desplazamiento+largo)` sobre el
  bloque de notas cuyo índice es el u32 que sigue al `0x2628`.
- Notas: bloque `MdNLB`: cantidad en +11 y eventos de 35 bytes desde +15: posición (5 bytes),
  nota (+8), largo (+9, 5 bytes), velocidad (+17) [verificado].
- Bloque `0x2000`: u32 cantidad de chunks `MdChun` y los chunks (cada chunk `Md…` = etiqueta, u16
  versión, u32 largo y contenido). `MdChun` → `MdNLB` v3: u32 n, n eventos de 35 bytes (posición,
  `00 00 40`, nota, largo, `00 00 40`, velocidad, 17 bytes), copia de la interpretación original
  (u32 n, u32 11, n × 11 bytes), u32 n y n + 6 bytes que siempre son `01 00 01 00` y ceros (en los 890 chunks del corpus; no son marcas por nota: si no se respetan, Pro Tools da "size of header doesn't match" al leer la lista de chunks); siguen `MdCDS` (controladores) y
  `MdES`; el chunk termina en `2A 00 00 00` + identificador de 8 bytes + `01`. Al editar notas, Pro
  Tools agrega un chunk nuevo y la región pasa a usarlo (u32 tras el `0x2628`); trarin hace lo mismo
  [verificado editando, borrando y agregando notas].
- **Clips MIDI nuevos** [verificado en Pro Tools 12 dibujando una nota y duplicando el clip]: una
  región nueva suma un chunk al `0x2000` y un `0x2633` al final de `0x2634` (u32 cantidad en +2),
  con su `0x2628` (nombre; desplazamiento CERO en 5 bytes y largo en ticks con ancho variable) y
  el u32 del chunk. Cada aparición es un `0x1056` en el `0x1057` de la pista (u32 cantidad tras el
  nombre; si la pista no tiene clips, justo después de la cantidad): duplicar en Pro Tools solo
  agrega otro `0x1056` que apunta a la misma región. También cambian `0x2056` (herramienta de
  edición elegida) y `0x206a`, que no hace falta escribir.
  Quitar un clip MIDI es borrar su `0x1056` y bajar la cantidad; la región queda en la lista de
  clips, como al borrar en la línea de tiempo [verificado en Pro Tools 12, guardando de nuevo y
  releyendo].
- Controladores (en estudio): dentro de `MdChun`, varios `MdCDS`, cada uno con `Mdss` (5 bytes
  que identifican el carril) y `MdCD` (u32 cantidad, eventos de 12 bytes: tick de 5 bytes,
  `00 00 40`, int32 valor). Carriles vistos: `02 07` (volumen, 0–127), `02 0A` (paneo), `02 06`,
  `02 64`, `02 65` (RPN), `01 94 18 03 0A` (valores −1385..496, probablemente pitch bend) [corpus].

## 9. Mezcla

- Canales: hijos de `0x2624` (`0x261C` audio, `0x261E` auxiliar, `0x261D` master). El nombre sale
  de la lista de pistas; `0x102D → 0x2619` guarda el nombre con que se creó.
- Solo: el byte al final del bloque de nombre. Color: int16 en +25 del `0x2015` propio (`-2` = sin color).
- Entrada: primera cadena propia del `0x261B`.
- Faders `0x260D`: el primero es el principal (volumen int32/10 en +3, mute en +7, paneo en +15 y +43,
  salida = última cadena del `0x260E`); los siguientes son envíos.
- Escritura de volumen, mute y paneo [verificado en Pro Tools 12]: además del valor en `0x1029`, Pro Tools
  repite el valor fijo como único punto de la lista de automatización del parámetro; trarin actualiza
  ese punto. Si la lista tiene más de un punto, el parámetro está automatizado: Pro Tools sigue la
  curva e ignora el valor fijo, así que trarin desplaza toda la curva lo mismo que cambió el valor
  (volumen hasta +12 dB, sin mover los puntos en silencio de −144 dB; paneo entre −100 y 100).
- Automatización `0x260A`: u32 cantidad en +12, ancho u16 en +16, puntos desde +24
  (u32 muestra + valor con signo). Orden directo: volumen (déci-dB), mute, desconocido; dentro de
  `0x260C`: paneo izquierdo y derecho [verificado]. pt_api describe el mismo bloque con marca
  `01 46 01 00` y nodos `[u32 muestra, int16]`.
- Inserts: `0x1038`, con códigos de fabricante y producto en `dat[18:26]` cruzados con la lista de
  plugins de la sesión (`0x1018/0x1017`). El estado de cada plugin viaja en un chunk AAX:
  `'Controls State'` (pares `d_`/`l_` con id terminado en cero y rellenado a par, double/int32
  big endian), `'Preset Name'`, o bloques `FFBS` (u32 cantidad en +8 y float32 desde +12).
- **Lugares de insert** [verificado en Pro Tools 12 agregando un plugin y copiando inserts entre
  sesiones]: cada `0x261B` tiene un `0x2627` con 11 lugares: vacío = `0x2625`; ocupado =
  `0x2616 → 0x2615 → (0x2626, u16 plugin, 0x2613 → (0x1038, 0x260E)), 0x2614`.
  - el u16 que sigue al `0x2626` del `0x2615` es la posición del plugin en `0x1018` (sin contar los
    `0x1017` vacíos, de categoría FF). La lista va ordenada por nombre (114 de 121 sesiones) y Pro
    Tools pone el plugin nuevo en su lugar y renumera ese u16 en todos los inserts;
  - el u16 en +2 del `0x2613` apunta a un registro `0x2064` de `0x2027` (archivo de ajustes
    «Plug-In Settings» del plugin, `…Fact.tfx` con sus códigos; FFFF = ninguno);
  - cada `0x1017`: u8 categoría (3 terceros, 4 Avid, FF vacío), nombre, códigos de fabricante y
    producto de 4 letras al revés, identificador del paquete AAX;
  - dos instancias del mismo plugin no tienen identificadores propios y el índice no cambia: copiar
    los inserts de una pista es copiar su `0x2627` (entre canales con la misma cantidad de canales),
    agregar los plugins y registros que falten y traducir los dos u16. Con el número de plugin sin
    traducir, Pro Tools carga el plugin de esa posición y avisa «AAE error -20007».

## 10. Entradas, salidas y buses

`0x1021` (rutas: nombre, canales, ids, UID, nombre corto), `0x2602/0x2601/0x2600` (salidas con
puertos físicos), `0x2033` (nombres de salida), `0x230A/0x230B` (`Custom 1..48`).
pt_api describe la reasignación de salidas en `0x2603 → 0x2602` y `0x260E` [externo].

Lo que usa `protools/ruteo.py` [verificado comparando un envío agregado en Pro Tools 12 y en el
corpus de 145 sesiones]:

- Rutas: `0x1022` (u32 cantidad) → `0x1021`: `2110`, u8 clase (00 entrada, 02 bus, 03 insert),
  u8, u32 largo y nombre, …, `2A000000` e identificador de 8 bytes. Salidas y buses: `0x2603` →
  `0x2602`: `0226`, u8 clase, u8, u32 largo y nombre, …, `2A000000` e identificador (las mitades
  de un par llevan después el del par). Un bus nuevo agrega 3 entradas en cada lista (par, .L, .R);
  el índice no cambia.
- Entrada del canal: en los bytes del `0x261B` tras sus hijos, después de la lista de
  identificadores del canal (u16 n, n u32) y 9 bytes: u32 largo y nombre, u32 0, `2A000000` (o 0),
  identificador de la ruta (o 0) y 7 bytes (`00 02 …` si es un bus); luego `FFFFFFFF`. Sin entrada:
  largo 0 y ceros.
- Faders: el primer `0x260D` del `0x261B` es el del canal; le siguen u16 cantidad de envíos y, por
  envío, u16 lugar (0 = A) y su `0x260D`. Cada fader: `0x1029` (valores), `0x260E` (destino: u16
  clase 0x13 salida, 0x43 bus, FFFF ninguno; `01 01`, u32 0, `2A000000` e identificador; ceros;
  u32 largo, nombre y `01`) y listas de automatización `0x260A` (el paneo, dentro de `0x260C`).
- `0x260A`: `0A26 01 4601 00`, u32 largo desde +12, `0000`, u32 cantidad de puntos (+12), u16 ancho
  del valor (+16; 2 en todo el corpus), u32 cantidad − 1 (+18), `0000`; desde +24 los puntos (u32
  muestra y valor) y 2 bytes al final. Las muestras son de la línea de tiempo de la sesión. El
  primer punto va en la muestra 0: una lista que empieza después Pro Tools la descarta al abrir y
  deja solo su primer valor [verificado en Pro Tools 12 con un show: corrida y con el primer valor
  en 0, la automatización sobrevive a guardar de nuevo].
- Si un identificador del canal termina en `0x5A`, el árbol de bloques ve un hijo falso tras el
  último fader: la lista de identificadores se busca desde el último `0x260D`.
- Copiar el ruteo de un canal a otro es traducir los identificadores por clase y nombre de la ruta
  y reemplazar la entrada y el tramo de faders; el índice no cambia.
- Número global de una ruta: en las entradas (clase 00), su lugar en `0x1022`; en salidas y buses,
  su lugar en `0x2603` más una diferencia fija (las mitades de un par llevan en b ese número del
  par; en las mitades de entradas e inserts, b es el lugar del par en `0x1022`). Tras el `0x102D` de
  cada `0x261B` van u8 (1 = entrada de dos canales), u16 número global de la ruta de entrada
  (FFFF = ninguna) y u8 1 [963 de 963 canales del corpus; Pro Tools lo recalcula al guardar].
- Bus nuevo (Pro Tools 12, v5_plugin → v6_envio): par y mitades (`Aux 1`, `Aux 1.L`, `Aux 1.R`) tras
  el último bus de `0x1022` y al final de `0x2603`; a y b del par son sus canales (los siguientes
  al mayor de `0x2603`); b de las mitades, el número global del par; en las mitades de los
  inserts, que quedan después, b sube en 3. `ruteo.crear_bus` reproduce esas listas byte a byte
  (salvo los identificadores nuevos).
- Pista de instrumento nueva (Pro Tools 12, v9_antes_inst → v10_instrumento): como una MIDI
  (entrada de tipo 7 en `0x2519`, `0x251A` en cada mitad, `0x1057` vacía en `0x1058`, grupo
  `<ALL>`, tira) pero con un canal `0x2621` (`0x102D`, `0x2627`, `0x260D`, `0x2010`, `0x206F`,
  `0x2611`) cuyo `0x2627` va al índice (`2624/n → 2621 → 2627`). No agrega salida a `0x2004`. A
  2010.pos − 11: u32 n, u16 1, u32 n, u8 1 (n entre los canales MIDI e instrumento). El canal hace
  de `0x261B`: lista de identificadores tras el fader, sin entrada de audio.
- Color de pista: i16 en el byte 25 de los bytes propios del `0x2015` del canal (−1 = sin color).
- Bus mono (Pro Tools 12, v13_antes_mono → v14_bus_mono): una sola entrada en cada lista (bandera
  0, 1 canal, u16 a y FFFF donde el par lleva a, b y FFFF; en `0x1021` el nombre corto termina en
  00); en las mitades de los inserts b sube en 1. `ruteo.crear_bus(..., canales=1)` reproduce las
  listas byte a byte salvo el identificador.
- Selección nueva: además de inicio y fin, lleva una entrada `0x2506` por pista tras `FEFF` y u32
  cantidad (Pro Tools la ajusta al guardar si no coincide).
- Salida MIDI: u32 en +14 del `0x2036` del canal MIDI (`0x2611`), número desde 1 de una entrada
  `0x2005` de `0x2004` (0 = ninguna). Cada canal tiene la suya: si dos la comparten, Pro Tools se
  cae al cerrar la sesión.

## 11. Otros bloques identificados, sin decodificar

`0x260F/0x260B` (nombres de parámetros automatizables de plugins), `0x203E`, `0x2006/0x2086/0x208C`
(dispositivos MIDI), `0x2423..0x2428` (grupos de clips, documentados por pt_api), `0x2523/0x2526`,
`0x242B/0xFE01/0xFE03` bajo `0x104F`, `0x8000` bajo `0x2629`, `0x1040/0x1041` (grooves de Beat
Detective), `0x2620/0x2621` (canales MIDI e instrumento).

## 12. Cobertura

Entre 84 % y 87 % de los bytes de cada sesión del corpus quedan interpretados. Entre 3 % y 7 %
sigue sin significado conocido; el resto son contenedores y copias.

## 13. Diferencias PT12 frente a PT2024 (pt_api)

| Dato | Lo que lee trarin | pt_api (PT2024) |
|---|---|---|
| Fundido o región en una colocación | primer byte después del `0x104F` | byte +15 del `0x104F` |
| Índice de clip gain en `0x2628` | 27 bytes después de `FF FF 04 00 04 00` | `largo − 6` o `largo − 22` |
| Cadencia de timecode `0x204D` | byte 0 (también en 2026) | u32 en +40 |
| Curvas de clip gain | también 2 nodos | 1, 3 y 4 nodos |

Versiones probadas: 12.5 (Windows, 51 sesiones), 12.8.3, 2019.6 y 2026.4 (Mac, una sesión cada
una). Las sesiones `.ptf` de PT 8 se leen solo en parte y las `.pts` de PT 5 no se leen: usan
otros tipos de bloque y quedan fuera del alcance actual.

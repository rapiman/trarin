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

### 2.1 Tabla de punteros (necesaria para escribir)

- El primer bloque, en `0x14`, es especial: `5A 01 00 04 00 00 00` + **u32 offset absoluto del
  bloque final `0x0002`** (11 bytes, sin `tipo_contenido`) [verificado en las 51 sesiones].
- El último bloque es `0x0002` y termina justo al final del archivo [verificado]. Según pt_api
  contiene series de registros de 15 bytes `00 00 00 01 04 00 01 00 | u32 offset | 00 00 00`,
  cada serie precedida por su cantidad en u16 big endian, y apuntan a bloques concretos [externo].
- Consecuencia: **cualquier cambio de tamaño de un bloque obliga a recalcular esos offsets**. pt_api
  documenta el procedimiento de guardado; trarin todavía solo lee.

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
  **Escritura** [verificado en Pro Tools 12]: mover una región cambia la muestra del ancla en cada canal
  y la de sus fundidos de entrada y salida; silenciarla cambia el byte de mute. Ambos son de tamaño
  fijo, así que no hace falta reconstruir la sesión.

- **Renombrar una pista** [verificado renombrando en Pro Tools 12]: el nombre (u32 largo + texto)
  está en los bytes propios de `0x2519`, en los dos `0x251A`, en la playlist activa `0x1014` y en
  las entradas `0x1052` de sus canales (audio), en `0x1057` (MIDI), y en el nombre del canal
  `0x102D → 0x2619` solo para auxiliares y master (en audio ese nombre es el de creación).

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

## 8. MIDI

- Pistas MIDI: `0x1058/0x1057` (nombre), `0x1056/0x104F`: índice en +4 y tick en +9 (5 bytes, menos `10**12`).
- Regiones MIDI: `0x2634/0x2633/0x2628`: ventana `[desplazamiento, desplazamiento+largo)` sobre el
  bloque de notas cuyo índice es el u32 que sigue al `0x2628`.
- Notas: bloque `MdNLB`: cantidad en +11 y eventos de 35 bytes desde +15: posición (5 bytes),
  nota (+8), largo (+9, 5 bytes), velocidad (+17) [verificado].
- Bloque `0x2000`: u32 cantidad de chunks `MdChun` y los chunks (cada chunk `Md…` = etiqueta, u16
  versión, u32 largo y contenido). `MdChun` → `MdNLB` v3: u32 n, n eventos de 35 bytes (posición,
  `00 00 40`, nota, largo, `00 00 40`, velocidad, 17 bytes), copia de la interpretación original
  (u32 n, u32 11, n × 11 bytes), u32 n, un byte por nota y 6 bytes; siguen `MdCDS` (controladores) y
  `MdES`; el chunk termina en `2A 00 00 00` + identificador de 8 bytes + `01`. Al editar notas, Pro
  Tools agrega un chunk nuevo y la región pasa a usarlo (u32 tras el `0x2628`); trarin hace lo mismo
  [verificado editando, borrando y agregando notas].
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

## 10. Entradas, salidas y buses

`0x1021` (rutas: nombre, canales, ids, UID, nombre corto), `0x2602/0x2601/0x2600` (salidas con
puertos físicos), `0x2033` (nombres de salida), `0x230A/0x230B` (`Custom 1..48`).
pt_api describe la reasignación de salidas en `0x2603 → 0x2602` y `0x260E` [externo].

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

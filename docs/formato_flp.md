# Formato de proyecto de FL Studio (.flp) — lo que usa trarin

Implementación propia, sin dependencias, escrita a partir de la descripción pública del formato y
verificada con proyectos de FL 12.0, 20.7 y 20.99/21 (proyectos propios y demos de FL Studio 21).
No se copió código de PyFLP ni de DawVert (ambos GPL-3.0).

## Contenedor

| Parte | Contenido |
|---|---|
| `FLhd` | u32 tamaño (6), i16 formato (0 = proyecto), u16 cantidad de canales, u16 PPQ (96 en todos los probados) |
| `FLdt` | u32 tamaño y la secuencia de eventos |

Cada evento: 1 byte de id; 0–63 llevan 1 byte, 64–127 u16, 128–191 u32 y 192–255 un largo varint
(7 bits por byte) más los datos. Los textos son UTF-16LE con terminador desde FL 11.5 (antes ASCII);
la versión (199) siempre es ASCII. Los eventos 231, 239 y 241 son texto aunque caen en el rango de datos.

Los eventos son secuenciales: canal (64), patrón (65), arreglo (99) e insert del mezclador (236)
abren el contexto al que pertenecen los siguientes. Leer y volver a escribir la lista de eventos
sin cambios da un archivo **idéntico byte a byte** (verificado en los 7 proyectos).

## Eventos usados

| Id | Dato |
|---|---|
| 199 | versión (`"20.7.2.1852"`) |
| 156 | tempo, u32 = BPM × 1000 |
| 17 / 18 | compás global (en un proyecto FL 12 el 18 vale 3: se informa como no reconocido) |
| 194 | título |
| 64, 21, 22, 196, 203, 201 | canal: índice, tipo (0 sampler, 2 generador, 3 capas, 4 instrumento, 5 automatización), insert de destino, archivo de audio, nombre, plugin interno |
| 65, 193, 224 | patrón: número, nombre, notas |
| 99, 241, 233, 238, 239 | arreglo: número, nombre, items de la playlist, pista, nombre de pista (FL anterior a 20: playlist única sin arreglo) |
| 148, 205, 33, 34 | marcador de tiempo: posición (byte alto = tipo, 8 = cambio de compás), nombre, numerador, denominador; dentro de un patrón son marcas del patrón |
| 236, 204, 98 | insert del mezclador, nombre, slot de efecto (los eventos de plugin 201/203 anteriores al 98 son de ese slot) |
| 225 | parámetros del mezclador: registros de 12 bytes; [4] id (192 volumen, 193 paneo), [6:8] u16 = insert << 6 \| slot, [8:12] int32 valor (volumen 0..16 000, ver curva del fader) |

**Nota (24 bytes):** posición u32, banderas u16, canal u16, largo u32, tecla u16, grupo u16, afinación
fina, (byte), release, canal MIDI, paneo (64 = centro), velocidad, mod X, mod Y. Posiciones en PPQ.

**Item de playlist (32 bytes; 60 desde FL 20.99):** posición u32, base u16 (20 480), índice u16 (mayor
que la base = patrón índice − base; si no, canal: clip de audio o automatización), largo u32, pista u16
invertida (500 − pista desde FL 12.9; 199 − pista antes), y desplazamientos de inicio y fin.

## Curva del fader del mezclador

Medida en FL Studio 21 escribiendo valores en dB en los faders y leyendo el valor guardado:

ganancia = (e^(3x) − 1) / (e^2,4 − 1), con x = valor / 16 000

| dB | +3 | 0 | −3 | −6 | −12 | −24 | −48 |
|---|---|---|---|---|---|---|---|
| valor | 14 500 | 12 800 | 11 153 | 9 575 | 6 705 | 2 611 | 208 |

0 dB es x = 0,8; el máximo (x = 1) da +5,6 dB y x = 0 es silencio. La fórmula reproduce las
mediciones con error menor a 0,03 dB. trarin informa `volumen_db` por insert y lo acepta al escribir
(verificado: −6, −12 y −24 dB escritos por trarin se ven así en FL Studio 21).

## Escritura (`trarin.flstudio.escribir`)

Se reemplazan solo los eventos cuyo contenido cambió: tempo, compás, nombre y notas de patrones,
items de la playlist (cada item conserva sus bytes extra originales), nombre e insert de canales,
volumen y paneo de inserts. Verificado: una demo de FL Studio 21 con tempo, nombre y notas de un
patrón, un item movido y volumen del master cambiados cargó en FL Studio 21.

## Pendiente

Automatización (canales de tipo 5 con puntos en el evento 234), cambios de tempo (son automatización),
crear canales o patrones nuevos, clips de audio de la playlist (canales), estado de plugins (eventos
212/213, opacos), ruteo entre inserts (235).

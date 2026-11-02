# Cambios

Formato: por fecha, de lo más nuevo a lo más antiguo. Versión del paquete: 0.1.0
(todavía sin versiones publicadas).

## 2026-11-02

- **Comparar una canción del show con su original** (`trarin/origen.py`):
  clips del original hallados en el show (y en qué pista), faltantes, muteados
  en el show, en otro lugar, agregados en el show, pistas del original no
  llevadas y faders distintos. Los clips se reconocen por archivo (sin los
  sufijos `_48k_xxxxxx` y `.L`/`.R`), pedazo y largo; la alineación es el
  corrimiento más repetido entre coincidencias (alinear por el primer audio
  fallaba cuando esa pista no se llevó). Con Eluwün: 7 de 177 clips llevados,
  faltan el bajo, el mic y las kaskawillas (los cortes decididos). CLI `trarin
  origen show.ptx cancion original.ptx`; MCP `comparar_con_original`.
- **Una sola sesión con respaldos** (`trarin/respaldos.py`): `reemplazar`
  respalda la sesión de trabajo en Session File Backups
  (`<nombre>.trarin.<fecha>_<n>.ptx`) y pone en su lugar la sesión nueva que
  escribió otra herramienta; `deshacer` vuelve al último respaldo (o al
  indicado) dejando lo que había como `<nombre>.deshecho.<fecha>.ptx`;
  `listar` muestra los respaldos con su nota. Todo anota en las notas. Es la
  forma de trabajo que se usó a mano en el show, ahora en una llamada. CLI
  `trarin respaldos sesion.ptx [--reemplazar nueva.ptx --motivo …] [--deshacer
  [cual]]`; MCP `reemplazar_sesion`, `respaldos_sesion`, `deshacer`.
- `escuchar`: el master va como una pista más (fader, automatización de
  volumen y mute), no solo su volumen fijo. Probado inyectando −20 dB de
  automatización a mitad de un tramo: la segunda mitad sale 20 dB más baja.
  `mezclar` acepta un `fichas.Sesion` ya armado además de la ruta.
- **`escuchar` por pista y en crudo**: `crudo=True` deja solo clip gain y
  fundidos (sin fader, paneo, mute, solo, auxiliares, master ni plugins; una
  pista mono va igual a los dos lados): «la pista tal cual está grabada».
  `por_pista=True` (`escucha.por_pista`) escribe un archivo por pista de audio
  con sonido en el tramo, en una carpeta del tramo («dame las pistas sin
  efectos»); cada una con su fader y paneo, o crudas. El nombre del archivo
  lleva las pistas pedidas y «crudo» («Aliwen c12-20 [Bombo] crudo.wav»).
  Probado: el bombo de Aliwen en crudo da 3 dB más que con su paneo al centro.

## 2026-11-01

- **`escuchar` con auxiliares y MP3**: se aplica el fader (automatización y
  mute) de los auxiliares por los que pasa cada pista (pista → bus → retorno →
  bus…, hasta 4 niveles; el paneo del aux no), y se avisa cuáles y qué plugins
  de ellos no suenan; `auxiliares=False` lo apaga. En el show, Katrümongen
  pasa de +4,4 a +1,3 dBFS de pico por los faders de Aux Kultrum (−5,3) y Aux
  Percuciones (−11,9). `formato="mp3"` escribe MP3 con lameenc (extra
  `trarin[mp3]`, sin ffmpeg; 16 compases: 711 KB contra 5,7 MB del WAV);
  `mp3_kbps` (192 por omisión).
- **Caché del modelo común** (`trarin/cache.py`): `leer_comun` guarda el modelo
  en `~/.trarin/cache` (o `TRARIN_CACHE`) con llave ruta + fecha + tamaño del
  archivo; leer el show pasa de 0,45 s a 0,04 s, y cada herramienta MCP lo
  lee de nuevo en cada llamada. Cada lectura devuelve un objeto nuevo, idéntico
  al leído sin caché (verificado). Se poda sola a 200 archivos;
  `TRARIN_SIN_CACHE=1` la desactiva; `cache.vaciar()` la borra.
- **Tempo propuesto desde la percusión**: `trarin/tempo_propuesto.py`
  (`proponer`, `escribir`, `texto`). Mide con el detector de bpm el clip más
  largo de una pista de percusión (la indicada o la de kultrun/bombo con más
  audio en la ventana) y devuelve bpm, confianza, acento, tempo por tramos,
  dónde cae el primer pulso en la sesión, a cuántos ms del pulso cae la negra
  más cercana de la grilla actual y si la grilla actual es el mismo pulso
  contado distinto (en el show, 95 bpm = 126,67 × 3/4; en Eluwün, 33,75 = 45 ×
  3/4). Propone: en una sesión suelta, un tempo único con el tick 0 en un
  pulso (ancla); en un show o con 'cancion', un cambio de tempo en el pulso
  más cercano de la grilla actual. Con salida, escribe la copia por el modelo
  común y la revisa (probado: la sesión escrita lee el tempo y el ancla
  propuestos). CLI `trarin tempo sesion.ptx [--pista …] [--cancion …] [-o …]`;
  MCP `proponer_tempo`.
- `comun.aplicar`: conserva `inicio_segundos` del modelo al escribir el tempo
  (antes lo descartaba y el ancla quedaba en 0).
- **Hoja del show (fase 5)**: `trarin/hoja.py` (`hoja`, `markdown`, `escribir`).
  Por canción: orden, inicio, duración y compases, tempo con sus cambios (las
  rampas de muchos eventos resumidas en «de X a Y entre t1 y t2», partidas
  donde cambian de sentido), compás, marcas de sección y dónde entra cada
  pista de audio y MIDI (compás de la canción y minuto del show). Sale de los
  clips; no necesita fichas. Escribe `trarin/hoja_del_show.md` junto a la
  sesión. CLI `trarin hoja sesion.ptx [-o hoja.md] [--titulo …] [--json]`; MCP
  `hoja_show`. Generada para el show RAPIZUAM.
- **Comparar sonidos (fase 3)**: `trarin/parecido.py`. `parecidos`: las pistas
  que más se parecen a una pista de la sesión o a un WAV, por un vector de
  rasgos de la ficha (bandas, brillo, planitud, ataque, golpes por segundo,
  caída, cresta, cuánto es tonal, nota, mono/estéreo; sin el nivel), cada rasgo
  en una escala donde 1 es una diferencia que se nota, y cada resultado con en
  qué difiere. `buscar`: pistas cuya ficha cumple una descripción en palabras
  (agudo, grave, brillante, opaco, tonal, ruidoso, percusivo, sostenido,
  regular, rápido, lento, mono, estéreo, satura, fuerte, suave, crece, se
  apaga, constante, intermitente, «nota la»…); todos los criterios a la vez,
  y devuelve las palabras que no entendió. Las dos por sesión, por canción o
  cada pista en cada canción. Probado con el show: los kultrunes salen juntos,
  las pifilkas juntas. CLI `trarin parecidos` y `trarin buscar`; MCP
  `parecidos` y `buscar_sonidos`.
- **Revisión (lint) de una sesión**: `trarin/revision.py` (`revisar`, `texto`).
  Errores (lo que Pro Tools mostraría roto): regiones de audio con largo cero,
  fuera de su archivo o sin archivo; bloque `0x2628` inconsistente (el inicio
  original no se repite como lo escribe Pro Tools); listas `0x262A` y `0x1004`
  cuya cantidad no coincide con sus bloques; mapa de tempo cuyo largo o cantidad
  de eventos no cuadra; clips MIDI con las notas fuera de su ventana; ancla de
  la línea de tiempo distinta de 0 en un show; archivos de audio que faltan.
  Avisos: regiones con desplazamiento sin inicio original (la forma que
  escribía trarin hasta hoy), clips que cruzan un marcador de canción, huecos de
  más de 10 s. Verificado: acusa las sesiones rotas conocidas (ancla en 13 s,
  94 clips MIDI vacíos) y da limpio en el show actual y en una sesión original
  de Pro Tools. `tras_escribir` lo corre después de cada escritura y lo deja en
  `verificacion.revision` (y en la línea de las notas si hay errores). CLI
  `trarin revisar sesion.ptx [--json]` (código 1 con errores); MCP `revisar`.
- Documentado en `formato_ptx.md`: tras el largo, el `0x2628` repite dos veces
  el inicio original con el ancho del 4.º byte (2, 3, 4 u 8) y luego 8 bytes FF.
- **Uniones entre canciones**: `trarin/uniones.py` (`uniones`, `por_cancion`,
  `comparar_por_cancion`, `texto`). Por cada par de canciones seguidas: dónde
  termina el último audio con sonido (y en qué pistas), el marcador de la
  siguiente, dónde empieza su primer audio, el hueco entre ambos, lo mismo para
  el MIDI, y tempo y compás a cada lado; los huecos sin ningún clip y los clips
  que cruzan un marcador. Por canción: corrimiento del primer audio, de la
  primera nota MIDI (y la distancia entre ambos), fin del audio y primera
  automatización respecto del marcador; y la comparación de eso entre dos
  versiones (detecta un MIDI desfasado del audio, como el del ancla). Una
  canción va desde 3 s antes de su marcador hasta 3 s antes del siguiente. CLI
  `trarin uniones sesion.ptx [--comparar otra.ptx] [--json]`; MCP `uniones` y
  `comparar_sesiones(..., por_cancion=True)`. Viene de las sondas con las que se
  revisó el show RAPIZUAM.
- **Regiones que no empiezan en el inicio del archivo (corregido)**: el `0x2628`
  de una región nueva ahora se escribe como Pro Tools (desplazamiento, largo, dos
  veces el desplazamiento, 8 bytes FF, anchos en el 4.º byte y 8 en el 5.º). Sin
  los dos campos repetidos, Pro Tools dejaba con largo cero los clips que no
  empiezan en la muestra 0 del archivo (rayas negras, sin sonido).
- Árbol de bloques: el mapa de tempo `0x2028` se lee como lista plana (sin hijos).
  Una rampa con muchos eventos contiene bytes 0x5A que parecían cabeceras y
  rompían el árbol.

## 2026-10-27

- **Salida MIDI (puerto y canal)**: `protools/salidas_midi.py` (`leer_salidas`,
  `cambiar_salidas`). El canal se guarda desde 0 tras el nombre del puerto en el
  0x2006 y el 0x2086 de la entrada 0x2005 de cada pista. Verificado contra lo que
  muestra Pro Tools en el show (MOTU 1–3, Motu 3, UR242 4). MCP `salidas_midi`.
- `golpes`: separar los redobles (`separar_redobles`, rachas de golpes a menos
  de 120 ms), un canal de una pista estéreo (`senal(..., canal)`) y rearme tras
  cada golpe. En las pifilkas de Katrümongen los golpes del audio coinciden uno a
  uno con su MIDI (327/327, 240/240, 568/568).
- **MIDI desde los golpes de una percusión**: `golpes.py` (`planear`, `escribir`,
  `escribir_mid`, `texto`). Para kultrun o bombo: una nota por golpe, con la
  fuerza en la velocidad y el largo de lo que suena; cuantizada a la grilla
  (1/16 por omisión, con la fase corrida a los golpes: en el show la grilla de
  Katrümongen está 36 ms corrida respecto de la de la sesión); el plan avisa si
  muchos golpes caen lejos de la grilla. Va a una pista MIDI nueva o existente
  de la sesión (copia nueva) o a un `.mid`. CLI `trarin golpes sesion.ptx pista
  [--cancion …] [-o salida.ptx] [--mid archivo.mid]`; MCP `golpes_a_midi`.
  Probado con el kultrun de Katrümongen: las 576 notas del MIDI Bombo (alineado
  a mano) tienen un golpe detectado a menos de 50 ms.
- `fichas` (y con eso `escuchar` y los tramos por canción): una canción termina
  en el siguiente marcador de nombre único; antes terminaba en el primer
  «Inicio» del show y quedaba de pocos segundos.
- **Crossfades en clips pegados**: `protools/cruces.py` (`planear`, `escribir`,
  `verificar`, `texto`). Para una pista, varias o todas las de audio: uniones de
  clips pegados o con hasta 1 ms de hueco o superposición (el hueco se cierra
  corriendo el clip de después); cruce de 20 ms centrado en la unión, que se
  corre a un lado si el otro no tiene audio sobrante; Equal Power (preset de
  Pro Tools) o lineal (Equal Gain estándar, el de Pro Tools por omisión). No
  toca uniones con un fundido; cruza también clips muteados; en estéreo con
  cortes distintos por canal, cada canal lleva sus cruces. Los cruces se agregan
  como colocaciones de fundido sin borrar ni rehacer clips, así que un clip con
  un cruce en el otro extremo ya no es problema. CLI `trarin cruces sesion.ptx
  [--pistas A,B] [--largo-ms 20] [--tolerancia-ms 1] [--lineal] [-o salida.ptx]`;
  MCP `cruces` (plan por omisión).
- `escritura`: un fundido nuevo puede ir en un solo canal de la pista (`canal`).
- **Reordenar pistas**: `protools/pistas_orden.py` con `reordenar` (permuta lista
  de pistas, canales, playlists de audio y MIDI, mapa de colocaciones, grupos,
  tiras e índice, sin cambiar tamaños), `orden_por_grupos` (master y auxiliares,
  audio por grupo y primer clip, MIDI por grupo) y `verificar_orden`. CLI
  `trarin ordenar sesion.ptx (--lista nombres.txt | --grupos "A,B") [-o salida.ptx]`;
  MCP `ordenar_pistas` (plan por omisión). Verificado en Pro Tools 12 con dos
  pistas de audio y con el show RAPIZUAM (v8): cada pista conserva contenido,
  color, salida y volumen.
- **Ancla de la línea de tiempo (corregido)**: el primer evento de tempo de
  Pro Tools guarda en qué muestra cae su tick (int64 en TMS+11). trarin la
  ignoraba y suponía el tick 0 en la muestra 0. `leer_tempo` la lee y el primer
  evento del mapa lleva `inicio_segundos` (dónde cae el tick 0); `MapaTempo`,
  `segundos` y `tick_de_segundos` la usan; el modelo común la conserva; al
  escribir el tempo se pone la del modelo (0 en un show) en vez de la de la
  plantilla. El show RAPIZUAM la tenía en 13,09 s (copiada de Katrümongen, donde
  vale para otro tick): MIDI, marcadores y compases caían 13 s después que el
  audio. Verificado con los contadores de Pro Tools 12 (antes y después).
- Armador de shows: el mapa de cada canción lleva su ancla (Katrümongen: tick 0
  en −2,067 s), así su MIDI queda junto a su audio.
- **Clips MIDI vacíos en Pro Tools (corregido)**: un clip MIDI nuevo en una sesión
  cuyo primer chunk de notas no lleva CERO (`10**12`) quedaba con las notas fuera
  de la ventana de su región; Pro Tools lo mostraba vacío y no lo tocaba, aunque
  trarin leía las notas. Así quedaron 94 clips del show RAPIZUAM. Ahora las notas
  de un clip nuevo usan la misma convención que su región. Verificado en Pro
  Tools 12: en una sesión vacía con una nota y en el show.
- `protools/midi_revision.py`: `revisar_midi` (cuenta los clips que Pro Tools
  muestra vacíos y cuántos se pueden reparar) y `reparar_desplazamientos`
  (corrige las regiones sin mover notas ni clips; mismo tamaño). CLI
  `trarin midi sesion.ptx [-o salida.ptx]`; MCP `revisar_midi` y `reparar_midi`.
- Documentado en `docs/formato_ptx.md`: la convención CERO de las notas, lo que
  Pro Tools cambia al reordenar pistas y el corrimiento al importar un `.mid`
  en un show con muchos cambios de tempo.
- `grupos.cambiar_grupos`: agrega o quita pistas de grupos que ya existen
  (cambia las dos copias de la lista 0x202B).
- Borrar un clip de una pista estéreo cuyos canales tienen clips distintos en
  el mismo lugar (por ejemplo dos clips mono soltados en una pista estéreo):
  antes fallaba; ahora se borra lo que hay en ese lugar en cada canal.

## 2026-10-26

- `consolidar`: una categoría MIDI con una sola pista de origen también pasa a
  «MIDI <categoría>» (antes quedaba con su nombre y podía chocar con una pista
  nueva de audio del mismo nombre, como «Bombo» al cambiar el orden del show).

## 2026-10-22

### Escuchar sin abrir Pro Tools (fase 4)
- `trarin/escucha.py`: mezcla rápida de un tramo (canción, compases o
  segundos) en un WAV estéreo de 16 bits en `trarin/escuchas/` junto a la
  sesión, siempre un archivo nuevo. Suma clip gain (fijo o curva), fundidos,
  paneo (ley de potencia constante) y su automatización, fader y su
  automatización, mute, solo y el fader del master; sin plugins, buses ni
  auxiliares. Se arma por tramos de 20 s (sirve para un show entero); si la
  suma pasa 0 dBFS se baja el total lo justo y se avisa. Filtro de pistas y
  frecuencia de salida opcional (más liviano).
- MCP: `escuchar` (trabajo).

### Fase 2 cerrada
- `revisar_niveles`: picos sobre 0 dBFS con clip gain, fader, su automatización
  y mute (y si la causa es el clip gain, el fader o ambos), y la sonoridad de
  cada canción con la mezcla.
- `marcar_secciones`: escribe en una sesión nueva los marcadores de sección de
  una canción («Aliwen B», «Aliwen C»…), en los compases pedidos o en los que
  propone `mapa_cancion`; verifica y anota.

### Armador de shows en lotes
- Color y solo, plugins, ruteo, corrimiento de la automatización y
  automatización de las canciones que caen en pistas existentes van cada uno
  en un lote (un solo reconstruir por fase); el ruteo se separa de su
  corrimiento para poder agruparlo. Mismos bytes que antes en los 10 shows de
  prueba y en el de RAPIZUAM; el de RAPIZUAM pasó de 132 s a 53 s.

### Fichas de sonido (fase 2, segunda parte)
- Articulación: golpes detectados con envolvente de 10 ms, golpes por segundo,
  regularidad y pulso aproximado; percusivo, mixto o sostenido según cuánto cae
  el sonido 150 ms después de cada golpe (calibrado con el show); en una sesión,
  qué porcentaje de los golpes cae en la grilla de semicorcheas del tempo.
- Altura: la nota que más suena en los momentos tonales (Do…Si con octava,
  La4 = 440 Hz); no se informa en sonidos ruidosos.
- Forma: curva de nivel en 20 tramos, su palabra (constante, crescendo,
  decrescendo, crece al final, se apaga al final, intermitente) y los cambios
  de carácter (sube o baja de nivel, más brillante u opaco).
- Problemas: posibles clics, cortes a cero (si son muchos, se avisa que puede
  ser una puerta o un efecto), bordes de clip sin fundido, continua fija o
  variable (medida bloque a bloque con ventana para que lo grave no se cuele; la
  variable es típica del soplo o de mover el instrumento cerca del micrófono) e
  infrasonido bajo 20 Hz. En el show: las pifilkas no tienen continua;
  ballenas_acordes tiene continua fija de 0,10 y Kaskawilla_Audio2, variable.
- Estéreo: cuántos dB pierde (o gana) al sumarse en mono.
- `mapa_cancion`: la canción por tramos de compases, qué entra y sale,
  sonoridad de la suma y posibles marcadores de sección. `que_suena`: lo que
  suena en un compás de la sesión o de una canción.
- Las series cambian de versión: hay que volver a calcular las fichas.

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

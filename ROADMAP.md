# Hoja de ruta

Estado al 2026-11-01. Lo marcado ✅ está hecho y verificado; ⏳ está en curso o a medio
camino; lo demás está pendiente. Las fases van en orden: cada una se apoya en la anterior.

## Fase 1 — Servidor MCP completo ✅

- ✅ 53 herramientas: vistas acotadas (resumen por pista o canción, detalle, rutas, archivos
  de audio, comparar versiones, también por canción, uniones entre canciones, comparar con
  la sesión original), revisión (lint), notas y respaldos (reemplazar, deshacer), consolidar
  y grupos con su plan y plantillas con nombre, bpm y tempo propuesto, conversión de audio,
  paneo de Live, shows, su hoja y sus luces, fichas (audio y MIDI), parecidos y búsqueda de
  sonidos, onda dibujada, escuchar y exportar a Pizarrita, niveles y nivelar, secciones,
  editar clips por compás, MIDI (revisar, reparar, salidas, golpes), orden, cruces y
  escribir.
- ✅ Trabajos largos al estilo witral (id, avance por líneas nuevas, espera con tope de 40 s,
  matar, recientes).
- ✅ Al escribir: relee, compara con el original y anota en `trarin/notas.md` junto a la
  sesión.
- ✅ Instalado en Claude Desktop del equipo y probado con el show (consolidar por MCP da la
  misma sesión que la v2).

## Fase 2 — Ficha de sonido

- ✅ Primera parte: tiempo, nivel (con LUFS), problemas (saturación, sobre 0 dBFS, corriente
  continua), estéreo, color y la línea en palabras; series guardadas por fuente, fichas por
  archivo, pista y canción; `fichas_calcular`, `ficha` y la línea de sonido en
  `resumen_sesion`.
- ✅ Revisión de nivel básica: avisa picos sobre 0 dBFS por el clip gain.
- ✅ Segunda parte: articulación, forma y cambios, clics, cortes, bordes sin fundido,
  continua fija o variable, infrasonido, nota más presente, pérdida en mono, mapa de la
  canción y qué suena en un compás.
- ✅ Revisión de nivel con fader, automatización y mute (`revisar_niveles`) y marcadores de
  sección escritos en la sesión (`marcar_secciones`).
- **Timbre de vientos andinos y metales** (a la larga): asimetría de la onda (picos
  positivos y negativos distintos, armónicos pares; pifilka, antara, trompeta, clarinete),
  sonido rajado o áspero (modulación de 20 a 150 Hz, riqueza armónica), redoble o trémolo
  (pulsación periódica de 4 a 15 Hz), batimientos (0,5 a 4 Hz) y multifonía o bitonal
  (dos o más fundamentales no armónicas, como en la tarka). Necesita una envolvente de
  1 ms y grabaciones de referencia para calibrar. Mientras tanto, el servidor sabe que no
  lo mide y no lo inventa.

Objetivo: que la IA pueda conversar sobre el sonido con alguien que apenas sabe describirlo
(«la pifilka que suena aguda al final») sin abrir los archivos.

- Por archivo, por clip y por pista dentro de cada canción:
  - **Tiempo**: duración, dónde empieza y termina el sonido de verdad, silencios largos,
    cuánto del tiempo suena.
  - **Nivel**: pico, RMS, sonoridad (LUFS), dinámica, momentos más fuertes.
  - **Problemas**: saturación (dónde y cuánta), corriente continua, clics y cortes.
  - **Estéreo**: estéreo real o mono duplicado, hacia qué lado carga, ancho, fase.
  - **Color**: sub, graves, medios, agudos; brillante u opaco; tonal o ruidoso; nota o
    frecuencia dominante.
  - **Articulación**: golpes por segundo, regularidad, tempo, ataque percusivo o sostenido,
    cola.
  - **Forma**: la curva de nivel en unos 20 tramos y su palabra (constante, crescendo,
    decrescendo, intermitente…); dónde cambia el carácter.
  - **En palabras**: una línea («percusivo, grave, regular a 126 bpm, entra en el compás 5,
    crece al final, sin saturación, mono real»), con los tiempos también en compás.
- Se guarda en `trarin/fichas/` junto a la sesión. Se calcula una vez al principio (como
  trabajo largo); después solo se actualiza lo que cambió. Se recalcula entera si pasó mucho
  tiempo o si se pide. Es información de referencia, no exacta.
- `resumen_sesion` muestra la ficha de cada pista cuando existe.
- **Revisión de nivel**: con el clip gain y el fader, avisar picos sobre 0 dBFS (por ejemplo
  tras normalizar Wenu Mapu con +26 dB).
- **Mapa de la canción**: marcadores, tempo y fichas juntos: «qué suena en el compás 40 de
  Aliwen», «dónde entra el bombo»; proponer marcadores de sección.

## Fase 3 — Comparar sonidos ✅

- ✅ Parecido entre fichas (`parecidos`): «busca en el show lo que se parezca a este golpe»,
  con una pista o un WAV de referencia, y en qué difiere cada resultado.
- ✅ Búsquedas por descripción (`buscar_sonidos`): «qué pistas son agudas y percusivas en
  Aliwen», «nota la», «que saturen».
- Pendiente: afinar los pesos de los rasgos con casos reales (hoy calibrados a ojo con el
  show RAPIZUAM) y comparar tramos (un golpe suelto, no la pista entera).

## Fase 4 — Escuchar sin abrir Pro Tools

- ✅ `escuchar`: mezcla rápida de un tramo a WAV de 16 bits o MP3 (lameenc), con el fader de
  los auxiliares por los que pasa cada pista y el master (sin plugins); una pista sola o
  todas por separado, con mezcla o en crudo.
- ✅ Puente con Pizarrita (`exportar_pizarrita`): montaje de Edit Pro + mezcla MP3 + WAV.
  Pendiente del lado de Pizarrita: importar el montaje en una idea (subir archivos y pegarlo).

Una mezcla rápida de un tramo (clips, clip gain, fundidos, paneo, fader y su automatización,
mute; sin plugins) en un archivo liviano para escuchar desde cualquier lado.

Lo aprendido en Pizarrita Edit Pro y PizarriSound (`/srv/pizarrita/wt-3d`, ARQUITECTURA §19
y §24), que se reutiliza:

- **La onda se calcula una vez y se guarda** (`picos.php`): pares mínimo/máximo por
  centésima, en una caché cuya llave lleva la fecha del archivo. Lo mismo sirve para las
  fichas de la fase 2 y para dibujar la onda de una pista.
- **El montaje es una lista de datos y nunca toca los archivos**: pistas con clips
  `{f, a, b, en}` (pedazo `a–b` del archivo `f` en el segundo `en`), volumen, fundidos, paneo.
  Es casi el mismo modelo común de trarin: una canción o el show se pueden pasar a ese
  montaje.
- **Se arma en una sola pasada** (en Pizarrita, ffmpeg con `atrim`, `volume`, `adelay` y la
  mezcla sobre un silencio del largo total). En trarin, con numpy y soxr (ya instalados) o
  ffmpeg si está.
- **El resultado es un archivo nuevo**, como la «versión nueva» de Edit Pro.
- Posible puente: exportar una canción del show como montaje de Edit Pro para escucharla y
  comentarla en Pizarrita.

## Fase 5 — Lo que sale de lo anterior

- ✅ **Plantillas de reglas** con nombre (`plantillas`; viene «rapizuam»), usables como
  `reglas="plantilla:nombre[+extra.json]"` en consolidar y grupos.
- ✅ **Luces por MIDI** (`luces`): las tres pistas del show según la convención. Pendiente:
  verlas en Pro Tools.
- ✅ **Hoja del show** para los músicos (`hoja_show`): orden, inicio, duración, tempo, compás,
  secciones y dónde entra cada pista, en Markdown junto a la sesión.

## Para llevar a Pizarrita

- La ficha de sonido al subir un audio (la IA de Pizarrita sabría qué es sin escucharlo).
- Comparar versiones de un montaje y verificar después de escribir.
- Lo aprendido de rendimiento: nunca recorrer todo por cada cambio (ver abajo).

## Armador de shows

- ✅ Plan, armado, match, ruteo, automatización, plugins, buses, instrumentos.
- ✅ Frecuencias distintas (conversión con soxr), corte por compás, pistas desde
  archivos WAV y MIDI, buses propios del show.
- ✅ Grupos por nombre (también MIDI) y consolidar pistas por instrumento.
- ✅ **Show RAPIZUAM** (`_trarin_pruebas\show_rapizuam`): hecho y tocado; salió bien.
  Archivo de trabajo `base_show.ptx`, respaldos en Session File Backups, notas en
  `trarin/notas.md`.
- ⏳ **Prueba con las cinco canciones de I3000** (Bailando en el paradero, Guerra,
  Sin Tierra, Sombra en el Desierto y Viaje a Cunco), con separación negativa y
  cambios de tempo.
- Investigar por qué un show grande tarda varios minutos en cerrarse en Pro Tools, aunque
  abre y guarda bien (experimento barato: medir con y sin `WaveCache.wfm`, con la mitad de
  las pistas y sin fundidos).
- **Borrar o rehacer un clip que tiene un fundido cruzado** (por `aplicar`): sigue sin
  poderse. Para agregar crossfades ya no hace falta (`cruces` no toca los clips).

## Red de seguridad

Los tres últimos errores graves (ancla de la línea de tiempo, convención CERO de las notas,
regiones con desplazamiento) se descubrieron abriendo Pro Tools. Pendiente:

- ✅ **Pruebas de regresión** (`python -m unittest discover -s pruebas`, 10 pruebas, 1 s):
  descifrar → cifrar mismos bytes; aplicar sin cambios deja el modelo igual; tempo con ancla,
  renombrar y mezcla sobreviven la ida y vuelta; la revisión da limpio en lo guardado por Pro
  Tools y acusa un ancla distinta de 0 en un show; una nota MIDI escrita por trarin se lee
  igual que la que dibujó Pro Tools (oráculo `PRUEBA_una_nota`); quitar en lote = de a una;
  reordenar; cambiar canal MIDI. Sesiones chicas en `pruebas/sesiones/`. Pendiente: oráculos
  con audio (clip a mitad de archivo, clip gain, cruces) guardados desde Pro Tools.
- ✅ **`trarin revisar`, lint de una sesión**, corre solo después de cada escritura:
  regiones con largo cero, fuera de su archivo o con el `0x2628` inconsistente; cantidad de
  hijos de `0x262A` y `0x1004` contra su campo u32; eventos de tempo contra el largo del
  bloque `0x2028`; notas fuera de la ventana de su región; ancla distinta de 0 en un show;
  archivos que no existen; avisos por regiones con la forma antigua, clips que cruzan un
  marcador y huecos largos. Pendiente: sumar reglas a medida que aparezcan errores nuevos.

## Rendimiento

- ✅ Árbol de bloques: salta al siguiente 0x5A (13 veces más rápido).
- ✅ `reconstruir`: búsqueda binaria y corrimientos acumulados (quitar una pista: de minutos
  a segundos).
- ✅ Quitar varias pistas en una pasada (55 pistas: de 37 s a 0,7 s) y agregar varias de
  audio en una pasada (20 pistas: de 24 s a 4 s), con los mismos bytes que de a una.
- ✅ Agregar varias MIDI y auxiliares en una pasada; ruteo, color, plugins y automatización
  en lote; lectura con el mapa de tempo preparado y descifrado de una vez (leer el show: de
  8,7 s a 1,1 s; consolidar: de 6,7 min a menos de 2).
- ✅ Armador de shows en lotes: el show RAPIZUAM de 132 s a 53 s, mismos bytes.
- ✅ Caché del modelo común en disco (`~/.trarin/cache`, llave ruta + fecha + tamaño): leer el
  show de 0,45 s a 0,04 s en las llamadas repetidas del MCP.

## Conversión entre programas

Objetivo: convertir en todas las direcciones entre Pro Tools, Ableton y FL Studio,
todo salvo los plugins (no se pueden traducir).

1. Plantillas vacías en el repositorio. Hechas en el equipo: Pro Tools (44,1, 48 y
   96 kHz y una sesión con una pista de cada tipo) y FL Studio. Ableton: falta, porque
   Live no está autorizado y no deja guardar; alternativa: `DefaultLiveSet.als` de
   las pruebas.
2. Hacia Pro Tools (desde Ableton y FL Studio): crear pistas de audio desde la sesión
   de pistas modelo, convertir los archivos de audio a WAV mono a la frecuencia de la
   sesión.
3. Hacia Ableton: crear pistas, clips, envíos y automatización en el XML.
4. Hacia FL Studio: crear canales, patrones y elementos de la playlist.

Preguntas abiertas antes de construir:

- Plantilla de Ableton: `DefaultLiveSet.als` o autorizar Live.
- Audio hacia Pro Tools: crear copias convertidas en `Audio Files`; frecuencia de la
  sesión según la mayoría de los archivos o indicada.
- Canales de FL Studio con notas: pista MIDI o audio colocado en cada nota.
- Pistas MIDI con instrumento: pista de instrumento vacía o pista MIDI.
- Clips de la vista Session de Ableton: ignorar o llevar.

## Modelo común

- Pendiente: crear o quitar pistas de instrumento desde el modelo común (el armador
  de shows ya las copia).
- Pendiente: automatización de pistas MIDI y de parámetros de plugins.

## Audio

- ✅ Detector de bpm (`trarin bpm`) y conversor de frecuencia (`trarin convertir-audio`).
- ✅ Medición de RMS por tramos con sonido (en `consolidar`).
- ✅ Proponer el tempo de una sesión con el detector (`proponer_tempo`): grilla alineada al
  primer pulso por el ancla; en un show, cambio de tempo en el pulso más cercano. Pendiente:
  abrir en Pro Tools una sesión escrita con el tempo propuesto y confirmar que la grilla cae
  en los golpes.

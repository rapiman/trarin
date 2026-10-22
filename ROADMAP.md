# Hoja de ruta

Estado al 2026-10-22. Lo marcado ✅ está hecho y verificado; ⏳ está en curso o a medio
camino; lo demás está pendiente. Las fases van en orden: cada una se apoya en la anterior.

## Fase 1 — Servidor MCP completo ✅

- ✅ 23 herramientas: vistas acotadas (resumen por pista o canción, detalle, rutas, archivos
  de audio, comparar versiones), notas del proyecto, consolidar y grupos con su plan, bpm,
  conversión de audio, paneo de Live, shows y escribir.
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
- Segunda parte: articulación, forma (curva y su palabra), clics y cortes, nota dominante y
  el mapa de la canción; revisión de nivel con el fader.

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

## Fase 3 — Comparar sonidos

- Parecido entre fichas: «busca en el show lo que se parezca a este golpe».
- Búsquedas por descripción: «qué pistas son agudas y suenan en Aliwen».

## Fase 4 — Escuchar sin abrir Pro Tools

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

- **Plantillas de reglas** con nombre (categorías, buses y patrones de RAPIZUAM) para el
  próximo show.
- **Hoja del show** para los músicos: orden, tempo, compás y duración de cada canción y
  dónde entra cada cosa.

## Para llevar a Pizarrita

- La ficha de sonido al subir un audio (la IA de Pizarrita sabría qué es sin escucharlo).
- Comparar versiones de un montaje y verificar después de escribir.
- Lo aprendido de rendimiento: nunca recorrer todo por cada cambio (ver abajo).

## Armador de shows

- ✅ Plan, armado, match, ruteo, automatización, plugins, buses, instrumentos.
- ✅ Frecuencias distintas (conversión con soxr), corte por compás, pistas desde
  archivos WAV y MIDI, buses propios del show.
- ✅ Grupos por nombre (también MIDI) y consolidar pistas por instrumento.
- ⏳ **Show RAPIZUAM** (`_trarin_pruebas\show_rapizuam`): `rapizuam_show_v2.ptx`
  consolidado; falta abrirlo y escucharlo en Pro Tools, identificar «track 5 N-Audio» y
  «mic», agregar las grabaciones de Live que quedaron en el Mac («Dime si ves mas alla
  Project») y decidir los cortes de eluwün.
- ⏳ **Prueba con las cinco canciones de I3000** (Bailando en el paradero, Guerra,
  Sin Tierra, Sombra en el Desierto y Viaje a Cunco), con separación negativa y
  cambios de tempo.
- Investigar por qué un show grande tarda varios minutos en cerrarse en Pro Tools, aunque
  abre y guarda bien.

## Rendimiento

- ✅ Árbol de bloques: salta al siguiente 0x5A (13 veces más rápido).
- ✅ `reconstruir`: búsqueda binaria y corrimientos acumulados (quitar una pista: de minutos
  a segundos).
- ✅ Quitar varias pistas en una pasada (55 pistas: de 37 s a 0,7 s) y agregar varias de
  audio en una pasada (20 pistas: de 24 s a 4 s), con los mismos bytes que de a una.
- ✅ Agregar varias MIDI y auxiliares en una pasada; ruteo, color, plugins y automatización
  en lote; lectura con el mapa de tempo preparado y descifrado de una vez (leer el show: de
  8,7 s a 1,1 s; consolidar: de 6,7 min a menos de 2).
- Pendiente: el armador de shows (`show.py`) todavía copia ruteo, plugins y automatización
  pista por pista; pasarlo a lotes.

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
- Pendiente: usar el detector para proponer el tempo de una sesión (grilla de Pro
  Tools alineada al primer pulso).

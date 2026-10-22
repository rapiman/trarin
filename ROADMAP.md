# Hoja de ruta

Estado al 2026-10-22 (noche). Lo marcado ✅ está hecho y verificado en Pro Tools 12.

## Armador de shows

- ✅ Plan, armado, match, ruteo, automatización, plugins, buses, instrumentos.
- ⏳ **Prueba con las cinco canciones de I3000** (Bailando en el paradero, Guerra,
  Sin Tierra, Sombra en el Desierto y Viaje a Cunco), con separación negativa y
  cambios de tempo. Es lo siguiente pendiente del armador.
- ✅ Frecuencias distintas (conversión con soxr), corte por compás, pistas desde
  archivos WAV y MIDI, buses propios del show.
- ⏳ **Show RAPIZUAM** (`_trarin_pruebas\show_rapizuam`): armado; falta revisarlo en
  Pro Tools, agregar las grabaciones de Live que quedaron en el Mac («Dime si ves
  mas alla Project») y decidir los cortes de eluwün.
- Investigar por qué un show grande (Sombra + Sombra) tarda varios minutos en cerrarse
  en Pro Tools, aunque abre y guarda bien.

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

## Servidor MCP

- ✅ Servidor por stdio con leer, compatibilidad, planear, armar y escribir.
- Pendiente: instalarlo y probarlo en el equipo (no en el servidor). Por ahora no.

## Modelo común

- Pendiente: crear o quitar pistas de instrumento desde el modelo común (el armador
  de shows ya las copia).
- Pendiente: automatización de pistas MIDI (no hay ejemplos en las sesiones de prueba)
  y de parámetros de plugins.

## Audio

- ✅ Detector de bpm (`trarin bpm`) y conversor de frecuencia (`trarin convertir-audio`).
- Pendiente: usar el detector para proponer el tempo de una sesión (grilla de Pro
  Tools alineada al primer pulso).

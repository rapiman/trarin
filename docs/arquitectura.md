# Arquitectura de trarin para varios DAW

## Estado

| DAW | Lectura | Escritura |
|---|---|---|
| Pro Tools `.ptx` (12, 2019, 2026) | amplia (ver `formato_ptx.md`) | desde el modelo común: nombre y mezcla de pistas, posición y mute de regiones, agregar y quitar clips de audio (incluidos archivos y regiones nuevos), posición y notas de clips MIDI, clips MIDI nuevos y borrados, marcadores (cambiar, agregar, borrar), tempo, compases, pistas nuevas (audio mono o estéreo, MIDI, auxiliares), quitar pistas de audio, MIDI y auxiliares |
| Ableton Live `.als` (12; sets de 11 reguardados) | tempo, compás, marcadores, pistas, clips del Arrangement y de Session, notas, audio y warp, mezcla, envíos, dispositivos, automatización de mezcla | tempo, compás, marcadores, pistas (nombre, volumen, paneo, mute), clips (posición, nombre, notas) |
| FL Studio `.flp` (12, 20, 21) | tempo, compás, canales, patrones y notas, arreglos y playlist, marcadores, inserts con efectos, volumen y paneo | tempo, compás, patrones (nombre, notas), playlist, canales (nombre, insert), volumen y paneo de inserts |

Uso:

```
python -m trarin leer SESION.(ptx|als|flp) -o sesion.json
# editar sesion.json (a mano o con una IA)
python -m trarin escribir sesion.json --plantilla SESION.(als|flp) -o SESION_nueva.(als|flp)
python -m trarin compatibilidad CANCION1.ptx CANCION2.ptx ...   # consulta antes de armar un show
python -m trarin show plan SHOW.json                             # cómo quedarían las canciones juntas
python -m trarin show armar SHOW.json                            # escribe la sesión del show
```

## Show (juntar sesiones)

Decisiones tomadas para el armador de shows (en desarrollo):

- Antes de juntar, una **consulta de compatibilidad** (`compatibilidad.py`, solo lee): frecuencia
  de muestreo (distinta = bloqueo), bits de los archivos, tempo y compás de cada canción, duración,
  pistas que coinciden entre sesiones (primero por nombre exacto; las que quedan solas, por nombre
  parecido, una por sesión; nunca dos pistas de la misma sesión) con sus diferencias de tipo,
  canales, mezcla, inserts y ruteo, las pistas propias de cada sesión, el ruteo (entradas, salidas y
  envíos, y en qué sesiones está cada uno) y los plugins que no están en todas. Con eso se decide el
  **match**: qué pista de cada canción va a qué pista del show (por defecto, el que propone la
  consulta; se puede corregir a mano).
- Base: una sesión base indicada, o una copia de la primera canción.
- Separación entre canciones configurable, también negativa (canciones encimadas). Con separación
  positiva, la primera mitad lleva el tempo de la canción que termina y la segunda el de la que
  empieza, con el cambio de tempo una blanca antes del inicio (configurable); con separación negativa
  el tempo cambia en la mitad de la zona encimada.
- Mezcla de pistas juntadas: por ahora gana la de la primera canción; después, automatización.
- Plugins: se copian los inserts de cada pista de la canción a su pista del show
  (`protools/inserts.py`, con el estado de cada plugin); la consulta avisa qué plugins faltan.
- Plan de tiempo (`show.py`, `trarin show plan`): inicio de cada canción, mapa de tempo, compases y
  marcadores del show, calculados por tiempo real (el audio y el MIDI suenan igual que en su sesión;
  donde el tempo del show coincide con el de la canción, las posiciones en ticks se trasladan
  exactas).
- Armado (`trarin show armar`): copia la base (la primera canción, o `base`) con el tempo, los
  compases y los marcadores del plan; cada pista de las demás canciones va a la pista del show que
  indique `match` (`{canción: {pista: pista del show o null}}`) o, por defecto, a la del mismo nombre,
  tipo y canales (o nombre parecido, si hay una sola); si no hay, se crea una pista nueva (audio,
  MIDI o auxiliar) con la mezcla y los plugins de la pista de la canción. Los clips de audio apuntan
  a los archivos originales de cada canción (carpeta «Audio Files»), con sus fundidos; los clips MIDI
  llevan sus notas. Las sesiones originales no se tocan. Hay que correrlo en el computador donde
  están los archivos de audio (Pro Tools necesita sus identificadores). Verificado en Pro Tools 12
  juntando dos canciones (35 pistas): abre, guarda de nuevo sin perder nada y cierra. Aún no:
  pistas de instrumento, envíos y ruteo de las pistas nuevas (usan el de la plantilla), y la
  automatización de las canciones que no son la base.
- Los fundidos de los clips son obligatorios.

## Principios

1. **Un JSON por DAW con convenciones comunes** (`formato: trarin/0`): posiciones musicales en ticks
   (960 000 por negra), posiciones de audio en muestras, y como ayuda `posicion` (compás|tiempo|ticks)
   y `segundos`. Al escribir, la fuente de verdad es siempre `tick` (o `muestra`), nunca `posicion`.
2. **Escribir editando el original, nunca generando desde cero.** Los tres formatos tienen mucho
   estado no documentado; se cambia solo lo que el JSON cambió y el resto se conserva. Sin cambios,
   el resultado es idéntico (FL) o equivalente (Ableton) al original. La plantilla nunca se sobrescribe.
3. **Lo que no se modela se conserva opaco** (estado de plugins, dispositivos nativos, vistas).
4. **Sin dependencias**: solo la biblioteca estándar de Python.

## Fuentes y licencias

| Proyecto | Licencia | Uso en trarin |
|---|---|---|
| ptformat | LGPL-2.1+ | base del descifrado de `.ptx` (portado) |
| pt_api | LGPL-2.1+ | referencia de formato PT2024 y de la tabla de punteros |
| ableton-als | MIT | referencia de formato y reglas de escritura de `.als` |
| DAWproject (Bitwig) | MIT | posible formato puente hacia Bitwig, Studio One, Cubase y Nuendo |
| PyFLP | GPL-3.0 | no se usa (licencia incompatible, sin mantenimiento, no funciona en Python ≥ 3.11) |
| DawVert | GPL-3.0 | no se usa; su repositorio pide no ser analizado por agentes de IA |

## Decisiones

- Modelo común `trarin/1` para editar los tres DAW con el mismo JSON (ver `modelo_comun.md`).
  Pasar sesiones de un DAW a otro queda para después (requiere crear pistas y clips).
- FL Studio: cada canal es una pista del modelo común.
- Solo Pro Tools, Ableton y FL Studio; DAWproject descartado.

## Pendiente

- Escritura de Pro Tools: crear selecciones, puntos de automatización, crear y quitar pistas de instrumento, auxiliares en sesiones sin uno que sirva de plantilla.
- Servidor MCP.

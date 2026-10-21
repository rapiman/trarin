# Arquitectura de trarin para varios DAW

## Estado

| DAW | Lectura | Escritura |
|---|---|---|
| Pro Tools `.ptx` (12, 2019, 2026) | amplia (ver `formato_ptx.md`) | volumen, paneo y mute de pistas; posición y mute de regiones de audio (desde el modelo común) |
| Ableton Live `.als` (12; sets de 11 reguardados) | tempo, compás, marcadores, pistas, clips del Arrangement y de Session, notas, audio y warp, mezcla, envíos, dispositivos, automatización de mezcla | tempo, compás, marcadores, pistas (nombre, volumen, paneo, mute), clips (posición, nombre, notas) |
| FL Studio `.flp` (12, 20, 21) | tempo, compás, canales, patrones y notas, arreglos y playlist, marcadores, inserts con efectos, volumen y paneo | tempo, compás, patrones (nombre, notas), playlist, canales (nombre, insert), volumen y paneo de inserts |

Uso:

```
python -m trarin leer SESION.(ptx|als|flp) -o sesion.json
# editar sesion.json (a mano o con una IA)
python -m trarin escribir sesion.json --plantilla SESION.(als|flp) -o SESION_nueva.(als|flp)
```

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

- Escritura de Pro Tools: marcadores, tempo, nombres, clips MIDI (la reconstrucción con cambio de tamaño ya está verificada).
- Servidor MCP.

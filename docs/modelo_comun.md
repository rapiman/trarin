# Modelo común `trarin/1`

Un mismo JSON para Pro Tools (`.ptx`), Ableton Live (`.als`) y FL Studio (`.flp`), pensado para que
una persona o una IA edite cualquiera de los tres con las mismas reglas.

```
python -m trarin leer SESION --comun -o comun.json
# editar comun.json
python -m trarin escribir comun.json --plantilla SESION -o SESION_nueva
```

`escribir` edita una copia del original: solo cambia lo que el JSON cambió. Sin cambios, el resultado
es idéntico (FL Studio) o equivalente (Ableton) al original.

## Estructura

```
{
  "formato": "trarin/1",
  "origen": {...},                       # DAW, versión, archivo
  "unidades": {"ticks_por_negra": 960000, "frecuencia_muestreo": 48000},
  "base_tiempo": "ticks" | "muestras",   # qué campo manda al escribir posiciones de clips
  "tempo":     [{"tick", "bpm"}],
  "compases":  [{"tick", "numerador", "denominador"}],
  "marcadores":[{"id", "nombre", "tick", "fin_tick"?, "comentario"?}],
  "archivos":  [{"id", "ruta", "muestras"?}],
  "pistas": [{
      "id", "nombre", "tipo": "audio|midi|retorno|grupo|master", "grupo"?, "color"?,
      "mezcla": {"volumen_db", "paneo", "mute", "solo", "entrada", "salida", "envios": [{"destino", "volumen_db"}]},
      "efectos": [{"nombre", ...}],
      "automatizacion": {...},
      "clips": [
        {"id", "tipo": "audio", "nombre", "tick", "largo_ticks", "muestra"?, "largo_muestras"?,
         "archivo", "desde_muestra"?, "ganancia_db"?, "mute"?, "fundidos"?},
        {"id", "tipo": "midi", "nombre", "tick", "largo_ticks",
         "notas": [{"tick", "largo_ticks", "nota", "velocidad"}]}
      ]
  }],
  "nativo": {...}                        # lo propio de cada DAW; no se edita
}
```

## Reglas

- Ticks: 960 000 por negra. Las notas de un clip están en ticks relativos al inicio del clip.
- `posicion` (compás|tiempo|ticks) y `segundos` son solo de lectura.
- Volumen en dB. Paneo de −1 (izquierda) a 1 (derecha); en pistas estéreo de Pro Tools, `[izq, der]`.
- Los `id` identifican cada pista y clip en el archivo original: no se cambian.
- Por ahora no se pueden agregar ni quitar pistas o clips (se informa un error claro).

## Particularidades por DAW

| DAW | Notas |
|---|---|
| Pro Tools | `base_tiempo` = muestras: el audio se ubica por `muestra`. Pistas estéreo: un clip por región del primer canal, con `canales: 2`. Escritura desde el modelo común: nombre de pistas (el `id` sigue siendo el nombre original); volumen, paneo y mute de pistas (si el volumen o el paneo tienen automatización, se desplaza toda la curva lo que cambió el valor; el mute automatizado da error); posición (`muestra`, o `tick` si solo cambió ese) y mute de regiones de audio, con sus fundidos (un fundido cruzado se mueve si las dos regiones se mueven lo mismo); posición de clips MIDI; nombre, posición, fin y comentario de marcadores, y marcadores nuevos (sin `id`, con `nombre` y `tick`) o borrados (los que faltan); mapa de tempo (cambiar, agregar o quitar cambios; el primero en el tick 0); compases (lista completa, cada cambio al inicio de un compás). Crear selecciones, agregar o quitar pistas o clips, notas MIDI o puntos de automatización aún da error. |
| Ableton | Clips del Arrangement; los de Session van en `nativo.clips_session`. |
| FL Studio | Cada **canal** es una pista. Cada aparición de un patrón en la playlist genera un clip en las pistas de los canales que suenan en él (`id` = `arreglo:item:canal`, `patron` = número). Los clips de una misma aparición se mueven juntos. Las notas de un patrón se comparten: editar las de un clip cambia el patrón en todas sus apariciones. Los inserts del mezclador son pistas propias: `id` = `insert:N`, tipo `master` (insert 0) o `retorno`, con `volumen_db` y `paneo` editables (sin clips). Cada canal indica su insert en `mezcla.insert` (editable) y, solo de lectura, en `mezcla.salida`. El volumen propio de cada canal aún no se modela. |

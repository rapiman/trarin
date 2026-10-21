# trarin

Herramienta para armar shows a partir de sesiones de DAW. Lee cada sesión y la
vuelca a un archivo de texto (JSON) que cualquier persona o IA puede leer y
editar. Sobre esa representación se calcula dónde debe caer cada canción en el
show, sin desfases de audio, MIDI ni mapa de tempo.

*Trarin* (mapudungun): amarrar, atar.

## Estado

Versión 0.1. Solo lectura. Por ahora solo Pro Tools 10 a 12 (`.ptx`, Windows).

| Dato de la sesión                   | Estado    |
|-------------------------------------|-----------|
| Producto, versión, frecuencia       | listo     |
| Mapa de tempo (incluidas curvas)    | listo     |
| Compases (cambios de métrica)       | listo     |
| Marcadores                          | listo     |
| Pistas, regiones, archivos de audio | pendiente |
| MIDI                                | pendiente |
| Plugins, envíos, automatización     | no previsto aún |

Más adelante: escritor de sesiones, servidor MCP y soporte para Ableton.

## Uso

```
pip install -e .
python -m trarin leer "Mi canción.ptx" --resumen
python -m trarin leer "Mi canción.ptx" -o "Mi canción.json"
python -m trarin leer "Mi canción.ptx" --yaml     # requiere pip install pyyaml
```

trarin nunca modifica la sesión original: la lee completa en memoria.

## Representación (`formato: trarin/0`)

- Toda posición musical se guarda en **ticks**: 960.000 por negra, el mismo
  valor que usa Pro Tools internamente. Es el dato exacto.
- Cada evento agrega, como ayuda de lectura, `posicion` (`compás|tiempo|ticks`,
  como la muestra Pro Tools), `segundos` y `muestra` (según la frecuencia de la
  sesión), calculados con el mapa de tempo.
- `tempo[].pulso_ticks` indica la figura que lleva el BPM: 960000 = negra,
  480000 = corchea.
- Antes del primer evento de tempo rige el BPM de ese primer evento.

## Formato Pro Tools 12 (lo descifrado hasta ahora)

Ver `trarin/protools/tiempo.py`. Resumen:

- Bloque `0x2028` "Tempo": eventos `Const` + `TMS`; desde el inicio de `TMS`:
  `+19` posición (int64, cero = 10^12), `+29` BPM (double), `+37` pulso (u32).
- Bloque `0x2029` "Meter": cantidad (u32) y eventos de 36 bytes:
  posición (int64), compás (u32), numerador (u32), denominador (u32).
- Bloque `0x2077` (dentro de `0x271A` "Markers"): número (u16), largo (u32),
  nombre UTF-8, posición (40 bits).
- Tempo, compases y marcadores aparecen dos veces con el mismo contenido.

## Preguntas abiertas

- En algunas sesiones el compás que declara un cambio de métrica no coincide
  con el cálculo desde el compás 1 (p. ej. compás 101 en la negra 248 con
  5/8 desde el inicio, donde se esperaría 250).
- Mapas de tempo cuyo primer evento no está en el cero.

## Licencia

GNU LGPL 2.1 o posterior (ver `LICENSE`).
`trarin/protools/descifrado.py` es un port de
[libptformat](https://github.com/zamaudio/ptformat) (Damien Zammit), que tiene
la misma licencia.

"""Reconstrucción de una sesión .ptx tras modificar bytes.

Cambiar el tamaño de cualquier dato desplaza los bloques que siguen, y la
sesión guarda posiciones absolutas en dos lugares que hay que corregir:

1. El primer bloque (en 0x14) termina con un u32: posición del bloque final 0x0002.
2. El bloque final 0x0002 es un índice con dos clases de posiciones:
   - registros "00 00 00 01 | u16 bytes | u16 n | n × u32 posición" (bytes = 4n);
   - pares "u16 tipo de contenido | u32 posición" donde el bloque apuntado tiene
     un hijo de ese tipo (posición del padre; FF FF FF FF = sin padre).
   Verificado en las 51 sesiones del corpus: estas dos reglas explican todas
   las posiciones del índice.

Además, cada bloque que contiene un cambio ajusta su campo de tamaño.
El orden es: descifrar → reemplazar rangos → reconstruir → cifrar (el XOR es
simétrico y depende de la posición, por eso se aplica al final).
"""

import re
import struct

from .bloques import arbol
from .descifrado import descifrar


class ErrorReconstruccion(Exception):
    pass


def _todos(bloques):
    for b in bloques:
        yield b
        yield from _todos(b.hijos)


def _posiciones_indice(d, final, bloques_por_pos):
    """Offsets (absolutos) de cada u32 del índice 0x0002 que es una posición."""
    p0 = final.inicio + 2
    pl = d[p0:final.fin]
    out = set()
    for m in re.finditer(rb"\x00\x00\x00\x01", pl):
        k = m.start()
        if k + 8 > len(pl):
            continue
        nb, n = struct.unpack_from("<HH", pl, k + 4)
        if n and nb == 4 * n and k + 8 + nb <= len(pl):
            for j in range(n):
                out.add(p0 + k + 8 + 4 * j)
    for k in range(2, len(pl) - 3):
        if p0 + k in out:
            continue
        v = struct.unpack_from("<I", pl, k)[0]
        b = bloques_por_pos.get(v)
        if b is None or v == 0xFFFF:
            continue
        tipo = struct.unpack_from("<H", pl, k - 2)[0]
        if any(h.tipo_contenido == tipo for h in b.hijos):
            out.add(p0 + k)
    return sorted(out)


def reconstruir(d, reemplazos):
    """d: sesión descifrada. reemplazos: [(inicio, fin, bytes_nuevos)] sobre d,
    sin solaparse y cada uno dentro de la carga de un bloque hoja o de los
    bytes propios de un bloque. Devuelve la sesión descifrada reconstruida."""
    bloques = arbol(d)
    todos = list(_todos(bloques))
    por_pos = {b.pos: b for b in todos}
    final = bloques[-1]
    if final.tipo_contenido != 0x0002 or final.fin != len(d):
        raise ErrorReconstruccion("la sesión no termina en el índice 0x0002")
    if struct.unpack_from("<I", d, 0x14 + 7)[0] != final.pos:
        raise ErrorReconstruccion("el puntero inicial no apunta al índice 0x0002")

    reemplazos = sorted(reemplazos)
    for (a1, f1, _), (a2, f2, _) in zip(reemplazos, reemplazos[1:]):
        if a2 < f1:
            raise ErrorReconstruccion("reemplazos solapados")
    for ini, fin, _ in reemplazos:
        if ini < 0x14 + 11 or fin > final.pos:
            raise ErrorReconstruccion("reemplazo fuera de la zona de bloques")
        for b in todos:  # no se puede cortar una cabecera de bloque
            if ini < b.pos + 9 and fin > b.pos and not (fin <= b.pos):
                raise ErrorReconstruccion(f"el reemplazo {ini}-{fin} toca la cabecera del bloque en {b.pos}")

    def nueva(pos):
        """Posición nueva de un byte que estaba en 'pos' (fuera de los rangos reemplazados)."""
        delta = 0
        for ini, fin, nuevo in reemplazos:
            if fin <= pos:
                delta += len(nuevo) - (fin - ini)
        return pos + delta

    indice = _posiciones_indice(d, final, por_pos)

    # 1) armar los bytes nuevos
    salida = bytearray()
    previo = 0
    for ini, fin, nuevo in reemplazos:
        salida += d[previo:ini]
        salida += nuevo
        previo = fin
    salida += d[previo:]

    # 2) tamaños de los bloques que contienen algún reemplazo
    for b in todos:
        delta = sum(len(n) - (f - i) for i, f, n in reemplazos if b.inicio <= i and f <= b.fin)
        if delta:
            struct.pack_into("<I", salida, nueva(b.pos) + 3, b.tamano + delta)

    # 3) puntero inicial e índice
    struct.pack_into("<I", salida, 0x14 + 7, nueva(final.pos))
    for k in indice:
        v = struct.unpack_from("<I", d, k)[0]
        struct.pack_into("<I", salida, nueva(k), nueva(v))
    return bytes(salida)


def cifrar(d):
    """El XOR de Pro Tools es simétrico: cifrar es lo mismo que descifrar."""
    return descifrar(d)

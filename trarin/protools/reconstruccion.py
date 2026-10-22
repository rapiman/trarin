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


def _padres(bloques, padre=None, out=None):
    out = {} if out is None else out
    for b in bloques:
        out[b.pos] = padre
        _padres(b.hijos, b, out)
    return out


def reconstruir(d, reemplazos):
    """d: sesión descifrada. reemplazos: [(inicio, fin, bytes_nuevos)] sobre d,
    sin solaparse y cada uno dentro de la carga de un bloque hoja o de los
    bytes propios de un bloque. Devuelve la sesión descifrada reconstruida.

    Un reemplazo puede llevar un cuarto elemento: la posición del bloque que lo
    contiene. Así se insertan o quitan bloques hijos completos (un bloque nuevo
    al final de una lista, justo donde termina el último hijo, o un hijo
    entero que se elimina): solo ese bloque y sus ancestros cambian de tamaño.
    Un hijo entero también se puede reemplazar por otro: el índice puede seguir
    apuntando a su comienzo."""
    from bisect import bisect_left, bisect_right
    bloques = arbol(d)
    todos = list(_todos(bloques))                   # en orden de posición
    posiciones = [b.pos for b in todos]
    padres = _padres(bloques)
    por_pos = {b.pos: b for b in todos}
    final = bloques[-1]
    if final.tipo_contenido != 0x0002 or final.fin != len(d):
        raise ErrorReconstruccion("la sesión no termina en el índice 0x0002")
    if struct.unpack_from("<I", d, 0x14 + 7)[0] != final.pos:
        raise ErrorReconstruccion("el puntero inicial no apunta al índice 0x0002")

    contenedores = {}
    normalizados = []
    for r in reemplazos:
        ini, fin, nuevo = r[:3]
        if len(r) > 3:
            contenedores[(ini, fin)] = r[3]
        normalizados.append((ini, fin, nuevo))
    reemplazos = sorted(normalizados, key=lambda r: (r[0], r[1]))
    for (a1, f1, _), (a2, f2, _) in zip(reemplazos, reemplazos[1:]):
        if a2 < f1:
            raise ErrorReconstruccion("reemplazos solapados")
    for ini, fin, _ in reemplazos:
        if ini < 0x14 + 11 or fin > final.pos:
            raise ErrorReconstruccion("reemplazo fuera de la zona de bloques")
        c = contenedores.get((ini, fin))
        # no se puede cortar una cabecera de bloque: los bloques con pos en (ini − 9, fin)
        for k in range(bisect_right(posiciones, ini - 9), bisect_left(posiciones, fin)):
            b = todos[k]
            quitado_entero = (c is not None and ini <= b.pos and b.fin <= fin
                              and any(a.pos == c for a in _ancestros(b, padres)))
            if not quitado_entero:
                raise ErrorReconstruccion(f"el reemplazo {ini}-{fin} toca la cabecera del bloque en {b.pos}")
        if c is not None and not (por_pos[c].inicio <= ini and fin <= por_pos[c].fin):
            raise ErrorReconstruccion(f"el reemplazo {ini}-{fin} no está dentro del bloque {c}")

    # corrimiento acumulado: los reemplazos no se solapan, así que sus fines van en orden
    fines = [f for _, f, _ in reemplazos]
    acumulado = [0]
    for ini, fin, nuevo in reemplazos:
        acumulado.append(acumulado[-1] + len(nuevo) - (fin - ini))

    def nueva(pos):
        """Posición nueva de un byte que estaba en 'pos' (fuera de los rangos reemplazados)."""
        return pos + acumulado[bisect_right(fines, pos)]

    indice = _posiciones_indice(d, final, por_pos)

    # 1) armar los bytes nuevos
    salida = bytearray()
    previo = 0
    for ini, fin, nuevo in reemplazos:
        salida += d[previo:ini]
        salida += nuevo
        previo = fin
    salida += d[previo:]

    # 2) tamaños de los bloques que contienen algún reemplazo: los que envuelven
    # el rango (una cadena desde la raíz) o, con contenedor, él y sus ancestros
    pos_hijos = {}

    def envolventes(i, f):
        out, hijos = [], bloques
        while hijos:
            ps = pos_hijos.get(id(hijos))
            if ps is None:
                ps = pos_hijos[id(hijos)] = [h.pos for h in hijos]
            k = bisect_right(ps, i) - 1
            # el último que empieza antes de i; con un rango vacío justo al final
            # de un hermano, ese hermano (el anterior) también lo envuelve
            b = next((hijos[j] for j in (k, k - 1) if j >= 0
                      and hijos[j].inicio <= i and f <= hijos[j].fin), None)
            if b is None:
                break
            out.append(b)
            hijos = b.hijos
        return out

    deltas = {}
    for i, f, n in reemplazos:
        c = contenedores.get((i, f))
        cadena = envolventes(i, f) if c is None else [por_pos[c]] + list(_ancestros(por_pos[c], padres))
        for b in cadena:
            deltas[b.pos] = deltas.get(b.pos, 0) + len(n) - (f - i)
    eliminados = sorted((i, f) for i, f, n in reemplazos if (i, f) in contenedores)
    inicios_e = [i for i, _ in eliminados]

    def eliminado(v, quitado_entero):
        """(i, f) del rango quitado que contiene la posición v, o None."""
        k = bisect_right(inicios_e, v) - 1
        if k >= 0 and v < eliminados[k][1]:
            return eliminados[k]
        return None

    for pos, delta in deltas.items():
        b = por_pos[pos]
        e = eliminado(b.pos, True)
        if e is not None and b.fin <= e[1] and b.pos != contenedores[e]:
            continue                                    # bloque quitado entero
        if delta:
            struct.pack_into("<I", salida, nueva(b.pos) + 3, b.tamano + delta)

    # 3) puntero inicial e índice
    nuevos = {(i, f): n for i, f, n in reemplazos}   # un bloque reemplazado por otro sigue en su lugar
    struct.pack_into("<I", salida, 0x14 + 7, nueva(final.pos))
    for k in indice:
        v = struct.unpack_from("<I", d, k)[0]
        e = eliminado(v, False)
        if e is not None and not (v == e[0] and nuevos[e]):
            raise ErrorReconstruccion(f"el índice apunta a un bloque quitado ({v})")
        struct.pack_into("<I", salida, nueva(k), nueva(v))
    return bytes(salida)


def _ancestros(b, padres):
    a = padres.get(b.pos)
    while a is not None:
        yield a
        a = padres.get(a.pos)


def cifrar(d):
    """El XOR de Pro Tools es simétrico: cifrar es lo mismo que descifrar."""
    return descifrar(d)

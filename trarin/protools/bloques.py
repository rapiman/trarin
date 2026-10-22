"""Árbol de bloques de una sesión Pro Tools ya descifrada.

Cada bloque empieza con el byte 0x5A, seguido de: tipo de bloque (u16),
tamaño (u32) y tipo de contenido (u16), todo little-endian en las sesiones
de Windows. Los bloques se anidan dentro del tamaño de su padre.
"""

from dataclasses import dataclass, field
import struct

ZMARK = 0x5A


@dataclass
class Bloque:
    pos: int            # posición del byte 0x5A
    tipo_contenido: int
    tamano: int
    hijos: list = field(default_factory=list)

    @property
    def inicio(self) -> int:
        """Inicio del contenido (donde está el tipo de contenido)."""
        return self.pos + 7

    @property
    def fin(self) -> int:
        return self.inicio + self.tamano

    def recorrer(self):
        """El bloque y todos sus descendientes, en orden de posición (sin
        generadores anidados: con miles de niveles de yield era lo más lento)."""
        pila = [self]
        while pila:
            b = pila.pop()
            yield b
            if b.hijos:
                pila.extend(reversed(b.hijos))


class Arbol(list):
    """Bloques de primer nivel. Guarda un índice por tipo de contenido que se
    arma la primera vez que se busca en el árbol entero."""
    _por_tipo = None

    def por_tipo(self):
        if self._por_tipo is None:
            indice = {}
            for raiz in self:
                for b in raiz.recorrer():
                    indice.setdefault(b.tipo_contenido, []).append(b)
            self._por_tipo = indice
        return self._por_tipo


def _parsear(d: bytes, pos: int, maximo: int):
    if pos + 9 > len(d) or d[pos] != ZMARK:
        return None
    tipo_bloque, tamano, contenido = struct.unpack_from("<HIH", d, pos + 1)
    inicio = pos + 7
    if tamano + inicio > maximo or tipo_bloque & 0xFF00:
        return None
    b = Bloque(pos, contenido, tamano)
    i = 1
    tope = min(pos + tamano, maximo)
    while i < tamano and pos + i < maximo:
        if d[pos + i] != ZMARK:             # solo puede empezar un bloque en un 0x5A
            j = d.find(b"\x5a", pos + i, tope)
            if j < 0:
                break
            i = j - pos
        h = _parsear(d, pos + i, inicio + tamano)
        if h and i == 9 and d[pos + 10:pos + 13] == b"\0\0\0":
            # muchas listas llevan un u32 cantidad tras el tipo de contenido: con
            # 90 elementos (0x5A) esa cantidad parece un bloque; si en +4 empieza
            # un bloque válido, la cantidad no es un hijo
            real = _parsear(d, pos + 13, inicio + tamano)
            if real is not None:
                h = None
                i = 13
                continue
        if h:
            b.hijos.append(h)
            i += h.tamano + 7
        else:
            i += 1
    return b


def arbol(d: bytes, desde: int = 0x1F) -> list:
    """Bloques de primer nivel, con sus hijos."""
    salida, i = Arbol(), desde
    while i < len(d) - 8:
        if d[i] != ZMARK:
            j = d.find(b"\x5a", i, len(d) - 8)
            if j < 0:
                break
            i = j
        b = _parsear(d, i, len(d))
        if b:
            salida.append(b)
            i += b.tamano + 7
        else:
            i += 1
    return salida


def buscar(bloques: list, tipo_contenido: int) -> list:
    """Todos los bloques (a cualquier profundidad) con ese tipo de contenido."""
    if isinstance(bloques, Arbol):
        return list(bloques.por_tipo().get(tipo_contenido, ()))
    return [b for raiz in bloques for b in raiz.recorrer() if b.tipo_contenido == tipo_contenido]

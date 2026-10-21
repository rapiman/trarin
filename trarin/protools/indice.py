"""Índice final de la sesión (bloque 0x0002).

Lista de entradas que ubican bloques por su camino de tipos:

  u16 tipo de contenido (0x0002) | u32 cantidad de entradas | entradas
  entrada = u32 profundidad | profundidad × paso | u32 cantidad de grupos | grupos
  paso    = u16 tipo | u32 posición del padre (FFFFFFFF = raíz) | u8 marca | u32 número
  grupo   = u8 1 | u16 bytes (4n) | u16 n | n × u32 posición | 6 bytes (casi siempre 0)

Cada entrada dice "los bloques de tal tipo bajo tal camino están en estas
posiciones"; Pro Tools lo usa para encontrarlos sin recorrer la sesión.
Verificado: leer y volver a escribir el índice da los mismos bytes en todo el
corpus.
"""

import struct
from dataclasses import dataclass, field


class ErrorIndice(Exception):
    pass


@dataclass
class Paso:
    tipo: int
    padre: int
    marca: int
    numero: int


@dataclass
class Grupo:
    posiciones: list
    uno: int = 1
    cola: bytes = bytes(6)


@dataclass
class Entrada:
    camino: list
    grupos: list = field(default_factory=list)


def leer_indice(datos):
    """datos: contenido del bloque 0x0002 (desde su tipo de contenido)."""
    if datos[:2] != b"\x02\x00":
        raise ErrorIndice("no es un índice 0x0002")
    n = struct.unpack_from("<I", datos, 2)[0]
    p, entradas = 6, []
    for _ in range(n):
        prof = struct.unpack_from("<I", datos, p)[0]
        p += 4
        camino = []
        for _ in range(prof):
            tipo, padre, marca, numero = struct.unpack_from("<HIBI", datos, p)
            camino.append(Paso(tipo, padre, marca, numero))
            p += 11
        m = struct.unpack_from("<I", datos, p)[0]
        p += 4
        grupos = []
        for _ in range(m):
            uno, nb, k = struct.unpack_from("<BHH", datos, p)
            if nb != 4 * k:
                raise ErrorIndice(f"grupo con tamaño inesperado en {p}")
            pos = list(struct.unpack_from(f"<{k}I", datos, p + 5))
            cola = datos[p + 5 + nb:p + 11 + nb]
            grupos.append(Grupo(pos, uno, cola))
            p += 11 + nb
        entradas.append(Entrada(camino, grupos))
    return entradas, datos[p:]


def escribir_indice(entradas, resto=b""):
    out = bytearray(b"\x02\x00" + struct.pack("<I", len(entradas)))
    for e in entradas:
        out += struct.pack("<I", len(e.camino))
        for s in e.camino:
            out += struct.pack("<HIBI", s.tipo, s.padre, s.marca, s.numero)
        out += struct.pack("<I", len(e.grupos))
        for g in e.grupos:
            out += struct.pack("<BHH", g.uno, 4 * len(g.posiciones), len(g.posiciones))
            out += struct.pack(f"<{len(g.posiciones)}I", *g.posiciones) + g.cola
    return bytes(out + resto)

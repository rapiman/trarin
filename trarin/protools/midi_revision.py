"""Revisión y reparación de regiones MIDI de una sesión .ptx.

Una región MIDI (0x2633 → 0x2628) guarda un desplazamiento dentro de su chunk
de notas (MdChun → MdNLB) y un largo: Pro Tools muestra y toca solo las notas
del chunk cuya posición cae en [desplazamiento, desplazamiento + largo). Las
posiciones pueden llevar CERO (10**12) o no, según cómo nació la sesión, y la
región y su chunk tienen que usar la misma convención.

El armador de shows (y antes de 2026-10-27, cualquier clip MIDI nuevo en una
sesión cuyo primer chunk no lleva CERO) dejaba regiones con desplazamiento
CERO y notas sin CERO: en Pro Tools el clip aparece vacío aunque las notas
están en el archivo. trarin las leía bien porque quita el CERO al leer.

reparar_desplazamientos baja el desplazamiento de esas regiones a la convención
de sus notas. Es un cambio del mismo tamaño (el campo conserva su ancho): no
corre ningún bloque. Verificado en Pro Tools 12 con el show RAPIZUAM (94
regiones: las notas aparecen en su lugar)."""

import struct

from .bloques import arbol
from .descifrado import leer_descifrado
from .escritura import _chunks_notas as _chunks, _md
from .pistas import _cadena, _entero, _tres_puntos
from .pistas_nuevas import _primero
from .tiempo import CERO


def _posiciones(d, chunk):
    ini, _ = chunk
    _, _, _, c0 = _md(d, ini)
    tag, _, _, n0 = _md(d, c0)
    if bytes(tag) != b"MdNLB":
        return []
    n = struct.unpack_from("<I", d, n0)[0]
    return [_entero(d, n0 + 4 + 35 * k, 5) for k in range(n)]


def _regiones(d):
    b = arbol(d)
    notas = _primero(b, 0x2000)
    lista = _primero(b, 0x2634) or _primero(b, 0x2002)
    if not notas or not lista:
        return []
    chunks = _chunks(d, notas)
    out = []
    for c in lista.hijos:
        if c.tipo_contenido not in (0x2633, 0x2001):
            continue
        g = next((x for x in c.hijos if x.tipo_contenido in (0x2628, 0x1007)), None)
        if not g:
            continue
        nombre, j = _cadena(d, g.inicio + 2)
        _, desde, largo = _tres_puntos(d, j)
        fuente = struct.unpack_from("<I", d, g.fin)[0]
        pos = _posiciones(d, chunks[fuente]) if fuente < len(chunks) else []
        dentro = sum(1 for t in pos if desde <= t < desde + largo)
        out.append({"nombre": nombre, "j": j, "desde": desde, "largo": largo, "notas": len(pos), "dentro": dentro,
                    "posiciones": pos})
    return out


def _corregible(r):
    """Desplazamiento que deja dentro las notas de una región vacía por la convención CERO, o None."""
    if not r["notas"] or r["dentro"]:
        return None
    pos = r["posiciones"]
    for nuevo in (r["desde"] - CERO, r["desde"] + CERO):
        if 0 <= nuevo < 1 << 40 and any(nuevo <= t < nuevo + r["largo"] for t in pos):
            return nuevo
    return None


def revisar_midi(ruta_o_datos):
    """Cuenta las regiones MIDI cuyas notas quedan fuera de su ventana (clips que
    Pro Tools muestra vacíos). Solo lee."""
    d = leer_descifrado(ruta_o_datos) if isinstance(ruta_o_datos, str) else ruta_o_datos
    regs = _regiones(d)
    vacias = [r for r in regs if r["notas"] and not r["dentro"]]
    return {"regiones": len(regs), "con_notas": sum(1 for r in regs if r["notas"]),
            "vacias_en_pro_tools": len(vacias),
            "reparables": sum(1 for r in vacias if _corregible(r) is not None),
            "ejemplos": sorted({r["nombre"] for r in vacias})[:20]}


def reparar_desplazamientos(d):
    """d: sesión descifrada. Devuelve (sesión descifrada reparada, cantidad de
    regiones corregidas). No cambia tamaños: no hace falta reconstruir."""
    out = bytearray(d)
    n = 0
    for r in _regiones(d):
        nuevo = _corregible(r)
        if nuevo is None:
            continue
        j = r["j"]
        ancho = (out[j + 1] & 0xF0) >> 4
        if nuevo >= 1 << (8 * ancho):
            continue
        out[j + 5:j + 5 + ancho] = nuevo.to_bytes(ancho, "little")
        n += 1
    return bytes(out), n

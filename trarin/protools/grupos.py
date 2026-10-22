"""Grupos de pistas de Pro Tools (crear).

Comparado con grupos creados en Pro Tools 12 sobre la misma sesión (g1_base →
g2_un_grupo → g3_dos_grupos → g4_mezcla):

* los grupos están en 0x202B: u32 cantidad y un 0x202A por grupo (el primero
  es <ALL>), y después otra vez la cantidad y la misma lista;
* 0x202A: u32 largo y nombre, u8 tipo (1 mezcla, 2 mezcla y edición), u32
  número del grupo (<ALL> lleva FFFFFFFF; los demás 1, 2, 3… que Pro Tools
  muestra como a, b, c…), u8 edición y u8 mezcla (0101 en mezcla y edición,
  0001 solo mezcla; <ALL>: 0000), u32 cantidad de pistas y un
  u16 por pista (su lugar en la lista de pistas 0x2519, desde 0), FEFF 1180
  0100 0000 0000 FFFF (<ALL>: 1080) y un 0x258E fijo;
* una pista puede estar en varios grupos;
* el índice 0x0002 no apunta a los 0x202A (sí al 0x202B, que reconstruir corre).

Al crear grupos Pro Tools también cambia el estado de selección de las pistas
(0x2015) y de la ventana (0x2067); trarin no los toca.
"""

import struct

from .bloques import arbol
from .pistas_nuevas import ErrorPista, _lista_2519, _primero
from .reconstruccion import reconstruir

TIPOS = {"mezcla": (1, b"\x00\x01"), "ambos": (2, b"\x01\x01")}   # tipo y (edición, mezcla)
_COLA = bytes.fromhex("feff1180010000000000ffff")
_258E = bytes.fromhex("5a02003d0000008e2501010000010000000100") + bytes(49)       # 7 + 61 bytes


def _bloque_grupo(nombre, tipo, numero, miembros):
    nb = nombre.encode("utf-8")
    t, banderas = tipo
    cuerpo = (b"\x2a\x20" + struct.pack("<I", len(nb)) + nb + bytes([t]) + struct.pack("<I", numero)
              + banderas + struct.pack("<I", len(miembros)) + b"".join(struct.pack("<H", m) for m in miembros)
              + _COLA + _258E)
    return b"\x5a\x05\x00" + struct.pack("<I", len(cuerpo)) + cuerpo


def agregar_grupos(d, grupos):
    """d con grupos nuevos. grupos: [{"nombre", "pistas": [nombres], "tipo":
    "ambos" (mezcla y edición, por defecto) o "mezcla"}]. Las pistas que no
    existen se ignoran; un grupo sin pistas no se crea."""
    b = arbol(d)
    raiz = _primero(b, 0x202B)
    if raiz is None:
        raise ErrorPista("la sesión no tiene lista de grupos (0x202B)")
    hijos = [h for h in raiz.hijos if h.tipo_contenido == 0x202A]
    n = struct.unpack_from("<I", d, raiz.inicio + 2)[0]
    if len(hijos) != 2 * n or struct.unpack_from("<I", d, hijos[n - 1].fin)[0] != n:
        raise ErrorPista("lista de grupos (0x202B) no reconocida")
    nombres = {}
    for i, e in enumerate(_lista_2519(d, _primero(b, 0x2519))[0]):
        nombres.setdefault(e[3], i)
    existentes = set()
    numero = 0
    for h in hijos[:n]:
        p = h.inicio + 2
        largo = struct.unpack_from("<I", d, p)[0]
        existentes.add(d[p + 4:p + 4 + largo].decode("utf-8", "replace"))
        num = struct.unpack_from("<I", d, p + 5 + largo)[0]
        if num != 0xFFFFFFFF:
            numero = max(numero, num)
    nuevos = b""
    creados = 0
    for g in grupos:
        if g["nombre"] in existentes:
            raise ErrorPista(f"ya existe un grupo llamado {g['nombre']!r}")
        miembros = sorted({nombres[x] for x in g["pistas"] if x in nombres})
        if not miembros:
            continue
        tipo = TIPOS.get(g.get("tipo", "ambos"))
        if tipo is None:
            raise ErrorPista(f"tipo de grupo desconocido: {g.get('tipo')!r}")
        numero += 1
        creados += 1
        existentes.add(g["nombre"])
        nuevos += _bloque_grupo(g["nombre"], tipo, numero, miembros)
    if not creados:
        return d
    total = struct.pack("<I", n + creados)
    reemplazos = [(raiz.inicio + 2, raiz.inicio + 6, total),
                  (hijos[n - 1].fin, hijos[n - 1].fin + 4, nuevos + total, raiz.pos),
                  (hijos[-1].fin, hijos[-1].fin, nuevos, raiz.pos)]
    return reconstruir(d, reemplazos)


def grupos_por_nombre(pistas, reglas, grupo_midi=None):
    """Grupos a partir de los nombres de las pistas. pistas: [(nombre, tipo)]
    (tipo como en el modelo común: audio, midi, retorno, 0x0007…); reglas:
    [{"nombre", "patron" (expresión regular sobre el nombre en minúsculas y sin
    tildes), "tipo"}]. Una pista va a cada grupo cuyo patrón calce; con
    grupo_midi, además todas las pistas MIDI e instrumento van a ese grupo."""
    import re
    import unicodedata

    def normal(t):
        return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")

    out = []
    for r in reglas:
        miembros = [n for n, t in pistas if t in ("audio", "midi", "0x0007") and re.search(r["patron"], normal(n))]
        if miembros:
            out.append({"nombre": r["nombre"], "pistas": miembros, "tipo": r.get("tipo", "ambos")})
    if grupo_midi:
        midi = [n for n, t in pistas if t in ("midi", "0x0007")]
        if midi:
            out.append({"nombre": grupo_midi, "pistas": midi, "tipo": "ambos"})
    return out

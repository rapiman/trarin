"""Salida MIDI (puerto y canal) de las pistas MIDI de una sesión .ptx.

Cada canal MIDI (0x2620/0x2621) apunta con un 0x2036 (u32 en +14, desde 1; 0 =
ninguna) a una entrada 0x2005 de la lista 0x2004. Una entrada con salida lleva
el nombre del puerto tres veces: en un 0x2006 (seguido del canal, u8), en un
0x2086 (seguido de u8 0 y del canal, u8, y dos ceros) y en un 0x208C. El canal se
guarda desde 0 [verificado con el show RAPIZUAM: MOTU canal 1, 2 y 3 = 0, 1, 2;
«Motu» canal 5 = 4]. Una entrada vacía (sin salida) es solo el 0x2005 con ceros."""
import struct

from .bloques import arbol, buscar
from .pistas import _cadena, leer_lista_pistas
from .pistas_nuevas import ErrorPista, _primero
from .reconstruccion import reconstruir


def _entradas(d, b):
    sal = _primero(b, 0x2004)
    return sal, [h for h in (sal.hijos if sal else []) if h.tipo_contenido == 0x2005]


def _refs(d, b):
    """{nombre de pista MIDI: (índice 0x2005 desde 1, posición del u32)}."""
    nombres = [p["nombre"] for p in leer_lista_pistas(d, b)]
    raiz = _primero(b, 0x2624)
    out = {}
    for i, c in enumerate(raiz.hijos):
        if c.tipo_contenido in (0x2620, 0x2621):
            r = buscar([c], 0x2036)
            if len(r) == 1:
                out[nombres[i]] = (struct.unpack_from("<I", d, r[0].inicio + 14)[0], r[0].inicio + 14)
    return out


def _leer(d, h):
    """(puerto, canal desde 1) de una entrada 0x2005, o None si no tiene salida."""
    e = next((x for x in h.hijos if x.tipo_contenido == 0x2006), None)
    if e is None:
        return None
    n = struct.unpack_from("<I", d, e.inicio + 4)[0]
    nombre = bytes(d[e.inicio + 8:e.inicio + 8 + n]).decode("latin-1")
    return nombre, d[e.inicio + 8 + n] + 1


def leer_salidas(d):
    """{pista MIDI: {"puerto", "canal"} o None}."""
    b = arbol(d)
    _, l = _entradas(d, b)
    out = {}
    for pista, (k, _) in _refs(d, b).items():
        out[pista] = _leer(d, l[k - 1]) if 1 <= k <= len(l) else None
        if out[pista]:
            out[pista] = {"puerto": out[pista][0], "canal": out[pista][1]}
    return out


def cambiar_salidas(d, cambios):
    """cambios: {pista MIDI: {"canal": 1..16, "puerto"?: nombre}}. El puerto por
    omisión es el que ya tiene la pista. Una pista sin salida (entrada vacía)
    toma la entrada de otra pista con ese puerto como plantilla. La entrada no
    puede estar compartida con otra pista."""
    b = arbol(d)
    sal, l = _entradas(d, b)
    refs = _refs(d, b)
    usos = {}
    for k, _ in refs.values():
        usos[k] = usos.get(k, 0) + 1
    reemplazos = []
    for pista, cambio in cambios.items():
        if pista not in refs:
            raise ErrorPista(f"{pista!r} no es una pista MIDI de la sesión")
        canal = int(cambio["canal"])
        if not 1 <= canal <= 16:
            raise ErrorPista(f"{pista}: canal {canal} fuera de 1–16")
        k, _ = refs[pista]
        if not 1 <= k <= len(l):
            raise ErrorPista(f"{pista}: la pista no tiene entrada de salida MIDI (falta soporte para crearla)")
        if usos[k] > 1:
            raise ErrorPista(f"{pista}: su salida MIDI está compartida con otra pista")
        actual = _leer(d, l[k - 1])
        puerto = cambio.get("puerto") or (actual[0] if actual else None)
        if not puerto:
            raise ErrorPista(f"{pista}: no tiene salida; indicar 'puerto'")
        plantilla = next((h for h in l if (_leer(d, h) or (None,))[0] == puerto), None)
        if plantilla is None:
            raise ErrorPista(f"{pista}: ninguna pista de la sesión usa el puerto {puerto!r} (hace falta una de plantilla)")
        nuevo = bytearray(d[plantilla.pos:plantilla.fin])
        base = plantilla.pos
        for x in buscar([plantilla], 0x2006):
            n = struct.unpack_from("<I", d, x.inicio + 4)[0]
            nuevo[x.inicio + 8 + n - base] = canal - 1
        for x in buscar([plantilla], 0x2086):
            n = struct.unpack_from("<I", d, x.inicio + 3)[0]
            nuevo[x.inicio + 7 + n + 1 - base] = canal - 1
        h = l[k - 1]
        reemplazos.append((h.pos, h.fin, bytes(nuevo), sal.pos))
    return reconstruir(d, reemplazos) if reemplazos else d

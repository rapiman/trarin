"""Lectura de una sesión Pro Tools a la representación de trarin."""

from pathlib import Path
import struct

from .bloques import arbol, buscar
from .descifrado import leer_descifrado
from . import tiempo

FORMATO = "trarin/0"


def _producto_version(d, bloques):
    """Nombre del producto ("Pro Tools HD") y versión mayor (12)."""
    if not bloques:
        return None, None
    b = bloques[0]
    if b.tipo_contenido == 0x0003:
        largo = struct.unpack_from("<I", d, b.inicio + 3)[0]
        nombre = d[b.inicio + 7:b.inicio + 7 + largo].decode("utf-8", "replace")
        version = struct.unpack_from("<I", d, b.inicio + 3 + largo + 8)[0]
        return nombre, version
    if b.tipo_contenido == 0x2067:
        return None, 2 + struct.unpack_from("<I", d, b.inicio + 20)[0]
    return None, None


def _frecuencia(d, bloques):
    b = buscar(bloques, 0x1028)
    return struct.unpack_from("<I", d, b[0].inicio + 4)[0] if b else None


def leer(ruta) -> dict:
    """Lee una sesión .ptx y devuelve un dict listo para volcar a YAML/JSON.

    Cada posición musical lleva el tick (dato exacto) y, como ayuda de lectura,
    la posición en compás|tiempo|ticks y en segundos.
    """
    ruta = Path(ruta)
    d = leer_descifrado(ruta)
    bloques = arbol(d)
    tempo = tiempo.leer_tempo(d, bloques)
    compases = tiempo.leer_compases(d, bloques)
    marcadores = tiempo.leer_marcadores(d, bloques)
    fs = _frecuencia(d, bloques)
    producto, version = _producto_version(d, bloques)

    def ubicar(ev):
        s = tiempo.segundos(ev["tick"], tempo)
        ev["posicion"] = tiempo.compas_tiempo(ev["tick"], compases)
        ev["segundos"] = round(s, 6)
        if fs:
            ev["muestra"] = round(s * fs)
        return ev

    return {
        "formato": FORMATO,
        "origen": {
            "daw": "protools",
            "producto": producto,
            "version": version,
            "archivo": ruta.name,
            "frecuencia_muestreo": fs,
        },
        "unidades": {"ticks_por_negra": tiempo.TICKS_NEGRA},
        "tempo": [ubicar(e) for e in tempo],
        "compases": [ubicar(e) for e in compases],
        "marcadores": [ubicar(e) for e in marcadores],
        "pendiente": ["pistas", "regiones", "archivos de audio", "MIDI"],
    }

"""Lectura de una sesión Pro Tools a la representación de trarin."""

from pathlib import Path
import struct

from .bloques import arbol, buscar
from .descifrado import leer_descifrado
from . import tiempo
from . import pistas as P
from .mezcla import leer_mezcla

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

    archivos = P.leer_archivos_audio(d, bloques)
    regiones = P.leer_regiones_audio(d, bloques)
    pistas_audio = P.leer_pistas_audio(d, bloques, regiones)
    regiones_midi = P.leer_regiones_midi(d, bloques)
    pistas_midi = P.leer_pistas_midi(d, bloques, regiones_midi)
    mezcla = leer_mezcla(d, bloques, [p["nombre"] for p in pistas_audio])

    for p in pistas_audio:
        for c in p["canales"]:
            for r in c["regiones"]:
                if fs:
                    s = r["muestra"] / fs
                    r["segundos"] = round(s, 6)
                    r["posicion"] = tiempo.compas_tiempo(tiempo.tick_de_segundos(s, tempo), compases)
    for p in pistas_midi:
        for r in p["regiones"]:
            ubicar(r)

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
        "audio": {"archivos": archivos, "regiones": regiones},
        "pistas_audio": pistas_audio,
        "midi": {"regiones": regiones_midi},
        "pistas_midi": pistas_midi,
        "mezcla": mezcla,
        "pendiente": ["puntos de automatización", "ajustes internos de plugins", "ruteo de entradas y salidas", "colores, grupos, playlists alternativas"],
    }

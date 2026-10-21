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


# Código de cadencia de timecode (tabla de pt_api; en el corpus PT12 solo se
# ha visto 0x07). En PT12 el código está en el primer byte del bloque 0x204D.
_CADENCIAS = {0x01: "24", 0x02: "25", 0x03: "29.97", 0x05: "29.97 drop", 0x07: "30",
              0x08: "30 drop", 0x09: "23.976", 0x0A: "50", 0x0B: "59.94", 0x0C: "59.94 drop",
              0x0D: "60", 0x0E: "60 drop", 0x0F: "47.952", 0x10: "48", 0x11: "100",
              0x12: "119.88", 0x13: "119.88 drop", 0x14: "120", 0x15: "120 drop"}


def _cuadros(d, bloques):
    b = next(iter(buscar(bloques, 0x204D)), None)
    if not b:
        return None
    return _CADENCIAS.get(d[b.inicio + 2], f"0x{d[b.inicio + 2]:02x}")


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
    regiones = P.leer_regiones_audio(d, bloques, P.leer_clip_gain(d, bloques))
    fundidos = P.leer_fundidos(d, bloques)
    lista_pistas = P.leer_lista_pistas(d, bloques)
    nombres = [p["nombre"] for p in lista_pistas]
    pistas_audio, playlists_inactivas = P.leer_pistas_audio(d, bloques, regiones, set(nombres) or None, fundidos)
    grupos = P.leer_grupos(d, bloques)
    for g in grupos:
        g["pistas"] = [nombres[i] if i < len(nombres) else i for i in g["pistas"]]
    regiones_midi = P.leer_regiones_midi(d, bloques)
    pistas_midi = P.leer_pistas_midi(d, bloques, regiones_midi)
    mezcla = leer_mezcla(d, bloques, nombres)

    for p in pistas_audio + playlists_inactivas:
        for c in p["canales"]:
            for r in c["regiones"] + c.get("fundidos", []):
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
            "cuadros_por_segundo": _cuadros(d, bloques),
        },
        "unidades": {"ticks_por_negra": tiempo.TICKS_NEGRA},
        "tempo": [ubicar(e) for e in tempo],
        "compases": [ubicar(e) for e in compases],
        "marcadores": [ubicar(e) for e in marcadores],
        "audio": {"archivos": archivos, "regiones": regiones},
        "pistas": lista_pistas,
        "pistas_audio": pistas_audio,
        "playlists_inactivas": playlists_inactivas,
        "grupos": grupos,
        "midi": {"regiones": regiones_midi},
        "pistas_midi": pistas_midi,
        "mezcla": mezcla,
        "pendiente": ["significado de parámetros en el estado propio de cada plugin", "colores de regiones", "byte +15 de cada colocación (valores 1, 2, 3)", "playlists MIDI alternativas"],
    }

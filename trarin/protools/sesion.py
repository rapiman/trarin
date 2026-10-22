"""Lectura de una sesión Pro Tools a la representación de trarin."""

from pathlib import Path
import re
import struct

from .bloques import arbol, buscar
from .descifrado import leer_descifrado
from . import tiempo
from . import pistas as P
from .mezcla import leer_mezcla

FORMATO = "trarin/0"


def _producto_version(d, bloques):
    """Producto ("Pro Tools HD"), versión mayor (12), versión completa
    ("12.5.0") y plataforma ("Windows"/"MacOS"), del bloque 0x0003."""
    b = next(iter(buscar(bloques, 0x0003)), None)
    if b is None:
        if bloques and bloques[0].tipo_contenido == 0x2067:
            return None, 2 + struct.unpack_from("<I", d, bloques[0].inicio + 20)[0], None, None
        return None, None, None, None
    largo = struct.unpack_from("<I", d, b.inicio + 3)[0]
    nombre = d[b.inicio + 7:b.inicio + 7 + largo].decode("utf-8", "replace")
    version = struct.unpack_from("<I", d, b.inicio + 3 + largo + 8)[0]
    textos = re.findall(rb"[\x20-\x7e]{4,}", d[b.inicio + 7 + largo:b.fin])
    completa = next((t.decode() for t in textos if re.fullmatch(rb"\d+\.\d+[\w.]*", t)), None)
    plataforma = next((t.decode() for t in textos if t in (b"Windows", b"MacOS")), None)
    return nombre, version, completa, plataforma


def _frecuencia(d, bloques):
    b = buscar(bloques, 0x1028)
    return struct.unpack_from("<I", d, b[0].inicio + 4)[0] if b else None


# Código de cadencia de timecode. Coincide con la tabla de pt_api; en PT12 se
# verificaron 0x02 (25), 0x05 (29.97 drop), 0x07 (30) y 0x09 (23.976).
_CADENCIAS = {0x01: "24", 0x02: "25", 0x03: "29.97", 0x05: "29.97 drop", 0x07: "30",
              0x08: "30 drop", 0x09: "23.976", 0x0A: "50", 0x0B: "59.94", 0x0C: "59.94 drop",
              0x0D: "60", 0x0E: "60 drop", 0x0F: "47.952", 0x10: "48", 0x11: "100",
              0x12: "119.88", 0x13: "119.88 drop", 0x14: "120", 0x15: "120 drop"}


def _timecode(d, bloques):
    """Bloque 0x204D (PT12): byte 0 = código de cadencia, u32 en +9 = inicio
    de la sesión en cuadros (a la cadencia nominal), y al final la duración de
    la sesión como texto HH:MM:SS:FF."""
    b = next(iter(buscar(bloques, 0x204D)), None)
    if not b:
        return None
    p = b.inicio + 2
    codigo = d[p]
    out = {"cuadros_por_segundo": _CADENCIAS.get(codigo, f"0x{codigo:02x}")}
    if b.fin - p >= 13:
        cuadros = struct.unpack_from("<I", d, p + 9)[0]
        nominal = _NOMINAL.get(codigo)
        out["inicio_cuadros"] = cuadros
        if nominal:
            s_, f = divmod(cuadros, nominal)
            out["inicio"] = f"{s_ // 3600:02d}:{s_ // 60 % 60:02d}:{s_ % 60:02d}:{f:02d}"
    texto = d[p:b.fin].rsplit(b"\x00", 1)[-1]
    if len(texto) == 11 and texto[2:3] == b":":
        out["duracion"] = texto.decode("ascii")
    return out


_NOMINAL = {0x01: 24, 0x02: 25, 0x03: 30, 0x05: 30, 0x07: 30, 0x08: 30, 0x09: 24, 0x0A: 50,
            0x0B: 60, 0x0C: 60, 0x0D: 60, 0x0E: 60, 0x0F: 48, 0x10: 48, 0x11: 100,
            0x12: 120, 0x13: 120, 0x14: 120, 0x15: 120}


def _fin(ev, tempo, compases):
    if "fin_tick" in ev:
        ev["fin_posicion"] = tiempo.compas_tiempo(ev["fin_tick"], compases)
        ev["fin_segundos"] = round(tiempo.segundos(ev["fin_tick"], tempo), 6)
    return ev


def leer(ruta) -> dict:
    """Lee una sesión .ptx y devuelve un dict listo para volcar a YAML/JSON.

    Cada posición musical lleva el tick (dato exacto) y, como ayuda de lectura,
    la posición en compás|tiempo|ticks y en segundos.
    """
    ruta = Path(ruta)
    d = leer_descifrado(ruta)
    bloques = arbol(d)
    tempo = tiempo.leer_tempo(d, bloques)
    mapa = tiempo.MapaTempo(tempo)              # para las miles de conversiones de abajo
    compases = tiempo.leer_compases(d, bloques)
    marcadores = tiempo.leer_marcadores(d, bloques)
    fs = _frecuencia(d, bloques)
    producto, version, version_completa, plataforma = _producto_version(d, bloques)

    def ubicar(ev):
        s = tiempo.segundos(ev["tick"], mapa)
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
                    r["posicion"] = tiempo.compas_tiempo(tiempo.tick_de_segundos(s, mapa), compases)
    for p in pistas_midi:
        for r in p["regiones"]:
            ubicar(r)

    return {
        "formato": FORMATO,
        "origen": {
            "daw": "protools",
            "producto": producto,
            "version": version,
            "version_completa": version_completa,
            "plataforma": plataforma,
            "archivo": ruta.name,
            "frecuencia_muestreo": fs,
        },
        "timecode": _timecode(d, bloques),
        "unidades": {"ticks_por_negra": tiempo.TICKS_NEGRA},
        "tempo": [ubicar(e) for e in tempo],
        "compases": [ubicar(e) for e in compases],
        "marcadores": [_fin(ubicar(e), mapa, compases) for e in marcadores],
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

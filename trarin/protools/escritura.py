"""Escritura de sesiones .ptx: cambios sobre una copia de la plantilla.

Todo lo que se escribe aquí tiene tamaño fijo, así que se edita en el lugar
sin mover bloques (los cambios de tamaño pasan por reconstruccion.py):

* Colocación de una región (0x1050 → 0x104F): desde el inicio del bloque
  (tipo de contenido incluido), +2 mute (byte) y +9 muestra del ancla (5 bytes). En pistas estéreo se cambian todos los canales, y los
  fundidos de entrada y salida de la región se mueven con ella.
* Fader principal de un canal (0x2624 → canal → 0x261B → 0x260D → 0x1029):
  +3 volumen (int32, décimas de dB), +7 mute, +15 y +43 paneo (int32,
  −100..100). Pro Tools repite el valor fijo como único punto de la lista de
  automatización del parámetro (0x260A: volumen y mute directos, paneo dentro
  de 0x260C); ese punto se actualiza también. Si la lista tiene más de un
  punto, el parámetro está automatizado y no se toca.
"""

from pathlib import Path
import struct

from .bloques import arbol, buscar
from .descifrado import leer_descifrado
from .reconstruccion import cifrar
from . import pistas as P


class ErrorEscritura(Exception):
    pass


def _primero(bloques, tipo):
    b = buscar(bloques, tipo)
    return b[0] if b else None


# ------------------------------------------------------------------ regiones

def _colocaciones(d, bloques, nombre_playlist):
    """Por canal de la playlist: (regiones, fundidos) con la posición del 0x104F,
    en el mismo orden que leer_pistas_audio."""
    regiones = P.leer_regiones_audio(d, bloques)
    fundidos = P.leer_fundidos(d, bloques)
    lista = _primero(bloques, 0x1015)
    ids = None
    for c in (lista.hijos if lista else []):
        if c.tipo_contenido != 0x1014:
            continue
        nombre, j = P._cadena(d, c.inicio + 2)
        if nombre == nombre_playlist:
            j += 1
            n = struct.unpack_from("<I", d, j)[0]
            ids = list(struct.unpack_from(f"<{n}H", d, j + 4))
            break
    if ids is None:
        raise ErrorEscritura(f"no se encontró la playlist {nombre_playlist!r}")
    mapa = _primero(bloques, 0x1054)
    entradas = [c for c in mapa.hijos if c.tipo_contenido == 0x1052] if mapa else []
    canales = []
    for i in ids:
        regs, fun = [], []
        for e in (entradas[i].hijos if i < len(entradas) else []):
            if e.tipo_contenido != 0x1050:
                continue
            for f in e.hijos:
                if f.tipo_contenido != 0x104F:
                    continue
                ri = struct.unpack_from("<I", d, f.inicio + 4)[0]
                muestra = P._entero(d, f.inicio + 9, 5)
                if d[f.fin] == 1:
                    if ri < len(fundidos):
                        g = fundidos[ri]
                        fun.append({"pos": f.inicio, "ancla": muestra, "inicio": muestra - g["pre_roll"],
                                    "fin": muestra - g["pre_roll"] + g["muestras"], "tipo": g["tipo"]})
                elif ri < len(regiones):
                    regs.append({"pos": f.inicio, "muestra": muestra, "largo": regiones[ri]["muestras"]})
        canales.append((sorted(regs, key=lambda r: r["muestra"]), sorted(fun, key=lambda r: r["ancla"])))
    return canales


def _cambios_regiones(d, bloques, pista, cambios, out):
    """cambios: {índice del clip: {"muestra"?: int, "mute"?: bool}}."""
    canales = _colocaciones(d, bloques, pista)
    base = canales[0][0]
    for j, cambio in cambios.items():
        if j >= len(base):
            raise ErrorEscritura(f"{pista}: no existe el clip {j}")
        ref = base[j]
        delta = cambio["muestra"] - ref["muestra"] if "muestra" in cambio else 0
        if ref["muestra"] + delta < 0:
            raise ErrorEscritura(f"{pista}#{j}: posición negativa")
        for regs, fun in canales:
            r = next((x for x in regs if x["muestra"] == ref["muestra"] and x["largo"] == ref["largo"]), None)
            if r is None:
                raise ErrorEscritura(f"{pista}#{j}: los canales de la pista no coinciden")
            if delta:
                out[r["pos"] + 9] = (r["muestra"] + delta).to_bytes(5, "little")
                ini, fin = r["muestra"], r["muestra"] + r["largo"]
                for f in fun:
                    if f["fin"] <= ini or f["inicio"] >= fin:
                        continue
                    if f["tipo"] == "cruzado" or f["inicio"] < ini or f["fin"] > fin:
                        raise ErrorEscritura(f"{pista}#{j}: la región tiene un fundido cruzado; "
                                             "moverla aún no está soportado")
                    out[f["pos"] + 9] = (f["ancla"] + delta).to_bytes(5, "little")
            if "mute" in cambio:
                out[r["pos"] + 2] = bytes([1 if cambio["mute"] else 0])


# ------------------------------------------------------------------- mezcla

def _canal_mezcla(d, bloques, indice):
    raiz = _primero(bloques, 0x2624)
    if not raiz or indice >= len(raiz.hijos):
        raise ErrorEscritura(f"no hay canal de mezcla para la pista {indice}")
    sb = next((h for h in raiz.hijos[indice].hijos if h.tipo_contenido == 0x261B), None)
    fader = next((h for h in sb.hijos if h.tipo_contenido == 0x260D), None) if sb else None
    nivel = next((h for h in fader.hijos if h.tipo_contenido == 0x1029), None) if fader else None
    if not nivel:
        raise ErrorEscritura(f"el canal {indice} no tiene fader")
    return fader, nivel


def _punto_unico(d, lista, valor, nombre, out):
    """Actualiza la lista de automatización si tiene un solo punto."""
    if lista is None:
        return
    p = lista.inicio
    n = struct.unpack_from("<I", d, p + 12)[0]
    ancho = struct.unpack_from("<H", d, p + 16)[0]
    if n == 1:
        out[p + 28] = int(valor).to_bytes(ancho, "little", signed=True)
    elif n > 1:
        raise ErrorEscritura(f"{nombre} tiene automatización; cambiar su valor fijo aún no está soportado")


def _cambios_mezcla(d, bloques, indice, nombre, cambio, out):
    fader, nivel = _canal_mezcla(d, bloques, indice)
    directas = [h for h in fader.hijos if h.tipo_contenido == 0x260A]
    paneos = [k for h in fader.hijos if h.tipo_contenido == 0x260C
              for k in h.hijos if k.tipo_contenido == 0x260A]
    if "volumen_db" in cambio:
        v = round(cambio["volumen_db"] * 10)
        out[nivel.inicio + 3] = struct.pack("<i", v)
        _punto_unico(d, directas[0] if directas else None, v, f"{nombre}: volumen", out)
    if "mute" in cambio:
        v = 1 if cambio["mute"] else 0
        out[nivel.inicio + 7] = bytes([v])
        _punto_unico(d, directas[1] if len(directas) > 1 else None, v, f"{nombre}: mute", out)
    if "paneo" in cambio:
        lados = cambio["paneo"] if isinstance(cambio["paneo"], list) else [cambio["paneo"]]
        for k, (off, valor) in enumerate(zip((15, 43), lados)):
            v = round(valor * 100)
            if not -100 <= v <= 100:
                raise ErrorEscritura(f"{nombre}: paneo fuera de rango")
            out[nivel.inicio + off] = struct.pack("<i", v)
            _punto_unico(d, paneos[k] if k < len(paneos) else None, v, f"{nombre}: paneo", out)


# ------------------------------------------------------------------ entrada

def escribir_cambios(plantilla, salida, mezcla=None, regiones=None):
    """mezcla: {índice de pista: (nombre, {"volumen_db", "paneo", "mute"})}.
    regiones: {nombre de playlist: {índice de clip: {"muestra", "mute"}}}.
    Escribe una copia cifrada en 'salida' (que no debe existir)."""
    salida = Path(salida)
    if salida.exists():
        raise ErrorEscritura(f"{salida} ya existe")
    d = leer_descifrado(plantilla)
    bloques = arbol(d)
    out = {}
    for pista, cambios in (regiones or {}).items():
        _cambios_regiones(d, bloques, pista, cambios, out)
    for indice, (nombre, cambio) in (mezcla or {}).items():
        _cambios_mezcla(d, bloques, indice, nombre, cambio, out)
    nuevo = bytearray(d)
    for pos, b in out.items():
        nuevo[pos:pos + len(b)] = b
    salida.write_bytes(cifrar(bytes(nuevo)))
    return salida

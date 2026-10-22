"""Audio: leer y escribir WAV, convertir la frecuencia de muestreo y separar
canales en archivos mono (como los usa Pro Tools).

Requiere numpy y soxr (pip install "trarin[audio]")."""

import struct
from pathlib import Path


class ErrorAudio(Exception):
    pass


def _np():
    try:
        import numpy as np
    except ImportError as e:
        raise ErrorAudio('se necesita numpy: pip install "trarin[audio]"') from e
    return np


def info(ruta):
    """{'frecuencia', 'canales', 'bits', 'formato', 'muestras', 'datos': (inicio, largo)}
    de un WAV (PCM entero o flotante, también WAVE_FORMAT_EXTENSIBLE)."""
    with open(ruta, "rb") as f:
        cab = f.read(12)
        if cab[:4] != b"RIFF" or cab[8:12] != b"WAVE":
            raise ErrorAudio(f"{ruta} no es un WAV")
        fmt = datos = None
        while True:
            h = f.read(8)
            if len(h) < 8:
                break
            cid, largo = h[:4], struct.unpack("<I", h[4:])[0]
            pos = f.tell()
            if cid == b"fmt ":
                b = f.read(largo)
                formato, canales, frec, _, alin, bits = struct.unpack_from("<HHIIHH", b)
                if formato == 0xFFFE and largo >= 26:
                    formato = struct.unpack_from("<H", b, 24)[0]
                fmt = (formato, canales, frec, alin, bits)
            elif cid == b"data":
                datos = (pos, largo)
                if fmt:
                    break
            f.seek(pos + largo + (largo & 1))
    if not fmt or not datos:
        raise ErrorAudio(f"{ruta}: WAV sin 'fmt ' o 'data'")
    formato, canales, frec, alin, bits = fmt
    if formato not in (1, 3):
        raise ErrorAudio(f"{ruta}: formato WAV {formato} no soportado (solo PCM y flotante)")
    tam = Path(ruta).stat().st_size
    largo = min(datos[1], tam - datos[0])                 # algunos programas dejan el largo mal
    return {"frecuencia": frec, "canales": canales, "bits": bits, "formato": formato,
            "muestras": largo // alin, "datos": (datos[0], largo - largo % alin)}


def leer(ruta, desde=0, cuantas=None):
    """(señal float64 de forma (muestras, canales) en [-1, 1], info)."""
    np = _np()
    i = info(ruta)
    can, bits = i["canales"], i["bits"]
    ancho = bits // 8
    n = i["muestras"] - desde if cuantas is None else min(cuantas, i["muestras"] - desde)
    with open(ruta, "rb") as f:
        f.seek(i["datos"][0] + desde * can * ancho)
        crudo = f.read(max(n, 0) * can * ancho)
    if i["formato"] == 3:
        x = np.frombuffer(crudo, dtype="<f4" if bits == 32 else "<f8").astype(np.float64)
    elif bits == 8:
        x = (np.frombuffer(crudo, dtype=np.uint8).astype(np.float64) - 128) / 128
    elif bits == 16:
        x = np.frombuffer(crudo, dtype="<i2") / 32768.0
    elif bits == 24:
        b = np.zeros((len(crudo) // 3, 4), dtype=np.uint8)            # 3 bytes → int32 con signo
        b[:, 1:] = np.frombuffer(crudo, dtype=np.uint8).reshape(-1, 3)
        x = (b.view("<i4")[:, 0] >> 8) / float(1 << 23)
    elif bits == 32:
        x = np.frombuffer(crudo, dtype="<i4") / float(1 << 31)
    else:
        raise ErrorAudio(f"{ruta}: {bits} bits no soportado")
    return x.reshape(-1, can), i


def escribir(ruta, senal, frecuencia, bits=24):
    """Escribe un WAV PCM (16, 24 o 32 bits) o flotante (bits=-32)."""
    np = _np()
    x = np.asarray(senal, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    can = x.shape[1]
    if bits == -32:
        crudo, formato, b = x.astype("<f4").tobytes(), 3, 32
    else:
        b, formato = bits, 1
        tope = float(1 << (bits - 1))
        v = np.clip(np.round(x * tope), -tope, tope - 1).astype(np.int64)
        if bits == 16:
            crudo = v.astype("<i2").tobytes()
        elif bits == 24:
            u = (v & 0xFFFFFF).astype(np.uint32)
            crudo = np.stack([u & 0xFF, (u >> 8) & 0xFF, (u >> 16) & 0xFF], axis=-1).astype(np.uint8).tobytes()
        elif bits == 32:
            crudo = v.astype("<i4").tobytes()
        else:
            raise ErrorAudio(f"{bits} bits no soportado al escribir")
    alin = can * b // 8
    fmt = struct.pack("<HHIIHH", formato, can, int(frecuencia), int(frecuencia) * alin, alin, b)
    with open(ruta, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(crudo) + (len(crudo) & 1)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        f.write(b"data" + struct.pack("<I", len(crudo)) + crudo)
        if len(crudo) & 1:
            f.write(b"\0")


def remuestrear(senal, desde, hasta, calidad="VHQ"):
    """Cambia la frecuencia de muestreo con soxr (calidad muy alta por defecto)."""
    if desde == hasta:
        return senal
    try:
        import soxr
    except ImportError as e:
        raise ErrorAudio('se necesita soxr: pip install "trarin[audio]"') from e
    return soxr.resample(senal, desde, hasta, quality=calidad)


def convertir(entrada, salida, frecuencia=None, bits=None, mono=False):
    """Convierte un WAV a otra frecuencia (y bits). Con mono=True escribe un
    archivo por canal: 'salida' sin extensión + .L.wav/.R.wav (estéreo) o
    .wav (mono); con más canales, .1.wav, .2.wav… Devuelve las rutas escritas."""
    x, i = leer(entrada)
    fs = frecuencia or i["frecuencia"]
    y = remuestrear(x, i["frecuencia"], fs)
    b = bits or (24 if i["formato"] == 1 and i["bits"] >= 24 else (16 if i["formato"] == 1 else -32))
    salida = Path(salida)
    # el nombre puede tener puntos (voz.L_48k): solo se quita o agrega «.wav»
    base = salida.with_suffix("") if salida.suffix.lower() == ".wav" else salida
    if not mono or y.shape[1] == 1:
        destino = base.parent / (base.name + ".wav")
        escribir(destino, y, fs, b)
        return [str(destino)]
    sufijos = [".L", ".R"] if y.shape[1] == 2 else [f".{k + 1}" for k in range(y.shape[1])]
    rutas = []
    for k, s in enumerate(sufijos):
        destino = base.parent / (base.name + s + ".wav")
        escribir(destino, y[:, k], fs, b)
        rutas.append(str(destino))
    return rutas

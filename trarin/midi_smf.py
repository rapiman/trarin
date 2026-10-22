"""Lector mínimo de archivos MIDI estándar (.mid, formatos 0 y 1): pistas con
sus notas, tempo y compás. Solo lee."""

import struct


class ErrorMidi(Exception):
    pass


def _vlq(b, i):
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if c < 0x80:
            return v, i


def leer(ruta):
    """{'formato', 'ppq', 'tempo': [(tick, bpm)], 'compases': [(tick, num, den)],
    'pistas': [{'nombre', 'notas': [(tick, largo, nota, velocidad, canal)]}]}.
    Los ticks son del archivo (ppq por negra)."""
    b = open(ruta, "rb").read()
    if b[:4] != b"MThd":
        raise ErrorMidi(f"{ruta} no es un archivo MIDI")
    formato, ntr, div = struct.unpack(">HHH", b[8:14])
    if div & 0x8000:
        raise ErrorMidi(f"{ruta}: división SMPTE no soportada")
    i = 8 + struct.unpack(">I", b[4:8])[0]
    out = {"formato": formato, "ppq": div, "tempo": [], "compases": [], "pistas": []}
    for _ in range(ntr):
        while b[i:i + 4] != b"MTrk":
            if i + 8 > len(b):
                raise ErrorMidi(f"{ruta}: faltan pistas")
            i += 8 + struct.unpack(">I", b[i + 4:i + 8])[0]
        ln = struct.unpack(">I", b[i + 4:i + 8])[0]
        j, fin = i + 8, i + 8 + ln
        tick, estado, nombre = 0, 0, ""
        abiertas, notas = {}, []
        while j < fin:
            dt, j = _vlq(b, j)
            tick += dt
            c = b[j]
            if c == 0xFF:
                tipo = b[j + 1]
                n, k = _vlq(b, j + 2)
                datos = b[k:k + n]
                j = k + n
                if tipo == 0x03 and not nombre:
                    nombre = datos.decode("latin-1")
                elif tipo == 0x51 and n == 3:
                    out["tempo"].append((tick, round(60e6 / int.from_bytes(datos, "big"), 6)))
                elif tipo == 0x58 and n >= 2:
                    out["compases"].append((tick, datos[0], 2 ** datos[1]))
                continue
            if c in (0xF0, 0xF7):
                n, k = _vlq(b, j + 1)
                j = k + n
                continue
            if c & 0x80:
                estado = c
                j += 1
            alto, canal = estado & 0xF0, estado & 0x0F
            if alto in (0xC0, 0xD0):
                j += 1
                continue
            d1, d2 = b[j], b[j + 1]
            j += 2
            if alto == 0x90 and d2 > 0:
                abiertas.setdefault((canal, d1), []).append((tick, d2))
            elif alto == 0x80 or (alto == 0x90 and d2 == 0):
                pila = abiertas.get((canal, d1))
                if pila:
                    t0, vel = pila.pop(0)
                    notas.append((t0, max(tick - t0, 1), d1, vel, canal))
        for (canal, nota), pila in abiertas.items():      # notas sin apagar: hasta el final de la pista
            for t0, vel in pila:
                notas.append((t0, max(tick - t0, 1), nota, vel, canal))
        notas.sort()
        out["pistas"].append({"nombre": nombre, "notas": notas})
        i = fin
    return out

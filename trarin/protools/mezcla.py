"""Estado de mezcla de cada canal: volumen, paneo, mute, solo, envíos y plugins.

Descifrado con sesiones de prueba hechas en Pro Tools 12 HD, cada una con un
solo cambio respecto de la anterior (ver README).

Estructura (bloque 0x2624, un hijo por canal en el orden de las pistas):
  0x261C pista de audio / 0x261E auxiliar / otros tipos para MIDI, etc.
    0x261B
      0x102D   nombre del canal (bloque 0x2619) y, tras él, el byte de solo
      0x2627   inserts (plugins); el estado de cada uno va en 0x1038
      0x260D   fader principal; cada envío agrega otro 0x260D
        0x1029   +3 volumen (int32, décimas de dB), +7 mute (byte),
                 +15 paneo izquierdo/mono, +43 paneo derecho (int32, -100..100)
        0x260E   destino (para un envío, el nombre del bus o auxiliar)
        0x260A   lista de automatización de un parámetro (formato de puntos
                 aún no descifrado del todo)
Los plugins de la sesión están en 0x1018/0x1017 con nombre y un código de
fabricante y producto de 4 letras, que se usa para identificar cada insert.
"""

import struct

from .bloques import buscar


def _cadena(d, p):
    n = struct.unpack_from("<I", d, p)[0]
    return d[p + 4:p + 4 + n].decode("utf-8", "replace"), p + 4 + n


def _i32(d, p):
    return struct.unpack_from("<i", d, p)[0]


def _plugins_sesion(d, bloques):
    lista = buscar(bloques, 0x1018)
    plugins = {}
    if not lista:
        return plugins
    for c in lista[0].hijos:
        if c.tipo_contenido != 0x1017:
            continue
        nombre, p = _cadena(d, c.inicio + 3)
        codigos = d[p:p + 12]
        fabricante = codigos[0:4][::-1].decode("latin-1")
        producto = codigos[4:8][::-1].decode("latin-1")
        plugins[(fabricante, producto)] = nombre
    return plugins


def _nivel(d, b):
    return {
        "volumen_db": _i32(d, b.inicio + 3) / 10,
        "mute": d[b.inicio + 7] == 1,
        "paneo": [_i32(d, b.inicio + 15), _i32(d, b.inicio + 43)],
    }


def leer_mezcla(d, bloques, nombres_audio=()):
    """'nombres_audio': nombres actuales de las pistas de audio, en orden. El nombre
    guardado en 0x102D es el que tenía el canal al crearse y no cambia al renombrar."""
    raiz = buscar(bloques, 0x2624)
    if not raiz:
        return []
    plugins = _plugins_sesion(d, bloques)
    canales = []
    nombres = list(nombres_audio)
    for canal in raiz[0].hijos:
        sb = next((h for h in canal.hijos if h.tipo_contenido == 0x261B), None)
        if not sb:
            continue
        info = {"tipo": {0x261C: "audio", 0x261E: "auxiliar", 0x261D: "master"}.get(canal.tipo_contenido,
                                                                 f"0x{canal.tipo_contenido:04x}")}
        cab = next((h for h in sb.hijos if h.tipo_contenido == 0x102D), None)
        if cab and cab.hijos:
            nombre_b = cab.hijos[0]
            info["nombre"], _ = _cadena(d, nombre_b.inicio + 2)
            info["solo"] = d[nombre_b.fin] == 1
        if info["tipo"] == "audio" and nombres:
            actual = nombres.pop(0)
            if actual != info.get("nombre"):
                info["nombre_al_crear"] = info.get("nombre")
            info["nombre"] = actual
        faders = [h for h in sb.hijos if h.tipo_contenido == 0x260D]
        for i, f in enumerate(faders):
            nivel = next((h for h in f.hijos if h.tipo_contenido == 0x1029), None)
            destino = next((h for h in f.hijos if h.tipo_contenido == 0x260E), None)
            datos = _nivel(d, nivel) if nivel else {}
            if i == 0:
                info.update(datos)
                info["envios"] = []
            else:
                nombre_destino = None
                if destino:
                    texto = d[destino.inicio:destino.fin]
                    # el nombre del destino es la última cadena con prefijo de largo
                    for p in range(len(texto) - 5, 0, -1):
                        n = struct.unpack_from("<I", texto, p)[0]
                        if 0 < n < 64 and p + 4 + n <= len(texto) and texto[p + 4:p + 4 + n].isascii() \
                                and texto[p + 4:p + 4 + n].decode("latin-1").isprintable():
                            nombre_destino = texto[p + 4:p + 4 + n].decode("utf-8", "replace")
                            break
                info["envios"].append({"destino": nombre_destino, "volumen_db": datos.get("volumen_db"),
                                       "mute": datos.get("mute"), "paneo": datos.get("paneo")})
        inserts = []
        for est in buscar([sb], 0x1038):
            codigo = d[est.inicio + 18:est.inicio + 26]
            fab = codigo[0:4].decode("latin-1")
            prod = codigo[4:8].decode("latin-1")
            inserts.append({"plugin": plugins.get((fab, prod), f"{fab}/{prod}")})
        info["inserts"] = inserts
        canales.append(info)
    return canales

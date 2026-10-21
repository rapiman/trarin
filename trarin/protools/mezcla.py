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
        0x260A   lista de automatización: +2 código, +6 largo, +12 cantidad de
                 puntos (u32), +16 ancho del valor (u16), +18 cantidad - 1,
                 +24 puntos: posición (u32, muestras) y valor (entero con
                 signo del ancho indicado). Orden dentro de 0x260D: volumen
                 (décimas de dB), mute (0/1), paneo por lado (dentro de
                 0x260C, -100..100) y una última lista aún sin identificar.
Los plugins de la sesión están en 0x1018/0x1017 con nombre y un código de
fabricante y producto de 4 letras, que se usa para identificar cada insert.

El estado de cada insert (0x1038) usa el formato de chunks de AAX: cada chunk
lleva fabricante, producto, identificador del plugin, tipo de chunk, un nombre
de 32 bytes y los datos. Tipos vistos:
  'Complete Controls State'  lista genérica de Pro Tools con el valor de cada
                             parámetro en orden (sin nombres de parámetro)
  '<fabricante> Preset Name' nombre del preset cargado
  '<fabricante> Plugin State' estado propio del plugin; en FabFilter es 'FFBS'
                             con una lista de valores float32
  otros (p. ej. Avid 'All ')  datos propios, se informa solo su tamaño
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


def _lista_auto(d, b):
    p = b.inicio
    n = struct.unpack_from("<I", d, p + 12)[0]
    ancho = struct.unpack_from("<H", d, p + 16)[0]
    q, puntos = p + 24, []
    for _ in range(n):
        pos = struct.unpack_from("<I", d, q)[0]
        val = int.from_bytes(d[q + 4:q + 4 + ancho], "little", signed=True)
        puntos.append([pos, val])
        q += 4 + ancho
    return puntos


def _automatizacion(d, fader):
    """Listas con más de un punto, por parámetro. Posición en muestras."""
    directas = [h for h in fader.hijos if h.tipo_contenido == 0x260A]
    paneos = [k for h in fader.hijos if h.tipo_contenido == 0x260C
              for k in h.hijos if k.tipo_contenido == 0x260A]
    nombres = [("volumen_db", 10), ("mute", 1), ("desconocido", 1)]
    auto = {}
    for (nombre, escala), b in zip(nombres, directas):
        pts = _lista_auto(d, b)
        if len(pts) > 1:
            auto[nombre] = [{"muestra": m, "valor": v / escala if escala != 1 else v} for m, v in pts]
    for lado, b in zip(("paneo_izq", "paneo_der"), paneos):
        pts = _lista_auto(d, b)
        if len(pts) > 1:
            auto[lado] = [{"muestra": m, "valor": v} for m, v in pts]
    return auto


def _controles(datos):
    """'Complete Controls State' de Pro Tools: todos los parámetros del plugin en
    orden. Cada uno: tipo ('d_' double, 'l_' entero), identificador del parámetro
    como texto terminado en cero y rellenado a largo par (puede ir vacío), y el
    valor en big-endian. Los nombres legibles de los parámetros no se guardan."""
    p = datos.find(b"\x01\x01\x01\x01")
    if p < 0:
        return []
    p += 4
    out = []
    while p + 4 <= len(datos) and datos[p:p + 2] in (b"d_", b"l_"):
        tipo = datos[p:p + 1].decode()
        p += 2
        fin = datos.index(b"\x00", p)
        ident = datos[p:fin].decode("latin-1")
        p = fin + 1
        if p % 2:
            p += 1
        if tipo == "d":
            valor = struct.unpack_from(">d", datos, p)[0]
            p += 8
        else:
            valor = struct.unpack_from(">i", datos, p)[0]
            p += 4
        out.append({"id": ident, "valor": valor} if ident else valor)
    return out


def _chunks(dat, fab, prod):
    """Chunks AAX de un insert: fabricante, producto, plugin, tipo, nombre (32 bytes)."""
    marca = (fab + prod).encode("latin-1")
    pos = [i for i in range(len(dat)) if dat.startswith(marca, i)]
    out = []
    for n, i in enumerate(pos):
        fin = pos[n + 1] if n + 1 < len(pos) else len(dat)
        tipo = dat[i + 12:i + 16].decode("latin-1")
        nombre = dat[i + 16:i + 48].split(b"\x00")[0].decode("latin-1")
        datos = dat[i + 48:fin]
        chunk = {"tipo": tipo, "nombre": nombre, "bytes": len(datos)}
        if "Controls State" in nombre:
            chunk["controles"] = _controles(datos)
        elif nombre.endswith("Preset Name"):
            chunk["texto"] = datos.split(b"\x00")[0].decode("utf-8", "replace") or datos.strip(b"\x00").split(b"\x00")[0].decode("utf-8", "replace")
        elif datos[0:4] == b"FFBS":
            # FabFilter: 'FFBS', versión, cantidad y valores float32 little-endian
            n_val = struct.unpack_from("<I", datos, 8)[0]
            if 12 + 4 * n_val <= len(datos):
                chunk["valores"] = [round(v, 6) for v in struct.unpack_from(f"<{n_val}f", datos, 12)]
        out.append(chunk)
    return out


def _cadenas(texto):
    """Cadenas con prefijo de largo (u32) que parecen texto, en orden."""
    out, p = [], 0
    while p + 5 <= len(texto):
        n = struct.unpack_from("<I", texto, p)[0]
        t = texto[p + 4:p + 4 + n]
        if 0 < n < 64 and len(t) == n and all(32 <= c < 127 or c >= 0xC0 or 0x80 <= c < 0xC0 for c in t):
            try:
                out.append(t.decode("utf-8"))
                p += 4 + n
                continue
            except UnicodeDecodeError:
                pass
        p += 1
    return out


def _propios(d, b):
    """Bytes del bloque que no pertenecen a sus hijos."""
    own, p = bytearray(), b.inicio
    for h in b.hijos:
        own += d[p:h.pos]
        p = h.fin
    own += d[p:b.fin]
    return bytes(own)


def _nivel(d, b):
    return {
        "volumen_db": _i32(d, b.inicio + 3) / 10,
        "mute": d[b.inicio + 7] == 1,
        "paneo": [_i32(d, b.inicio + 15), _i32(d, b.inicio + 43)],
    }


def leer_mezcla(d, bloques, nombres=()):
    """'nombres': nombres actuales de todas las pistas en orden (0x2519); hay un
    hijo de 0x2624 por pista. El nombre guardado en 0x102D es el que tenía el
    canal al crearse y no cambia al renombrar."""
    raiz = buscar(bloques, 0x2624)
    if not raiz:
        return []
    plugins = _plugins_sesion(d, bloques)
    canales = []
    nombres = list(nombres)
    for indice, canal in enumerate(raiz[0].hijos):
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
        if indice < len(nombres):
            actual = nombres[indice]
            if actual != info.get("nombre"):
                info["nombre_al_crear"] = info.get("nombre")
            info["nombre"] = actual
        vista = buscar([canal], 0x2015)
        if vista:
            color = struct.unpack_from("<h", _propios(d, vista[0]), 25)[0]
            info["color"] = None if color < 0 else color
        entrada = _cadenas(_propios(d, sb))
        if entrada:
            info["entrada"] = entrada[0]
        faders = [h for h in sb.hijos if h.tipo_contenido == 0x260D]
        for i, f in enumerate(faders):
            nivel = next((h for h in f.hijos if h.tipo_contenido == 0x1029), None)
            destino = next((h for h in f.hijos if h.tipo_contenido == 0x260E), None)
            datos = _nivel(d, nivel) if nivel else {}
            auto = _automatizacion(d, f)
            if auto:
                datos["automatizacion"] = auto
            if i == 0:
                info.update(datos)
                if destino:
                    c = _cadenas(d[destino.inicio:destino.fin])
                    info["salida"] = c[-1] if c else None
                info["envios"] = []
            else:
                nombre_destino = None
                if destino:
                    c = _cadenas(d[destino.inicio:destino.fin])
                    nombre_destino = c[-1] if c else None
                info["envios"].append({"destino": nombre_destino, "volumen_db": datos.get("volumen_db"),
                                       "mute": datos.get("mute"), "paneo": datos.get("paneo"),
                                       **({"automatizacion": datos["automatizacion"]} if "automatizacion" in datos else {})})
        inserts = []
        for est in buscar([sb], 0x1038):
            dat = d[est.inicio:est.fin]
            codigo = dat[18:26]
            fab = codigo[0:4].decode("latin-1")
            prod = codigo[4:8].decode("latin-1")
            inserts.append({"plugin": plugins.get((fab, prod), f"{fab}/{prod}"),
                            "fabricante": fab, "producto": prod,
                            "estado": _chunks(dat, fab, prod)})
        info["inserts"] = inserts
        canales.append(info)
    return canales

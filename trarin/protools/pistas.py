"""Archivos de audio, regiones, pistas y MIDI de una sesión Pro Tools 10-12.

Implementación propia. Los tipos de bloque se identificaron con ayuda de
libptformat; dos diferencias respecto de esa librería, verificadas contra
Pro Tools 12 HD:

* Fades y crossfades también aparecen como colocaciones en la lista de una
  pista (bloque 0x1050). Se distinguen por el último byte del bloque 0x1050,
  que vale 1. Su índice no apunta a la lista de regiones de audio, así que se
  descartan; si no, aparecen regiones que no existen en la sesión.
* Una región MIDI es una ventana [inicio, inicio+largo) sobre un bloque de
  notas que pueden compartir varias regiones. Las notas se toman de esa
  ventana y se ubican respecto de su inicio, sin correrlas a la primera nota.

Unidades: audio en muestras (a la frecuencia de la sesión), MIDI en ticks
(960.000 por negra).
"""

import struct

from .bloques import buscar
from .tiempo import CERO


def _cadena(d, p):
    n = struct.unpack_from("<I", d, p)[0]
    return d[p + 4:p + 4 + n].decode("utf-8", "replace"), p + 4 + n


def _entero(d, p, n):
    return int.from_bytes(d[p:p + n], "little")


def _tres_puntos(d, j):
    """Inicio, desplazamiento y largo con ancho variable (nibbles en j+1..j+3)."""
    b_off = (d[j + 1] & 0xF0) >> 4
    b_len = (d[j + 2] & 0xF0) >> 4
    b_ini = (d[j + 3] & 0xF0) >> 4
    p = j + 5
    desplazamiento = _entero(d, p, b_off); p += b_off
    largo = _entero(d, p, b_len); p += b_len
    inicio = _entero(d, p, b_ini)
    return inicio, desplazamiento, largo


def _sin_cero(v):
    return v - CERO if v >= CERO else v


def _primero(bloques, tipo):
    b = buscar(bloques, tipo)
    return b[0] if b else None


# ------------------------------------------------------------------ audio

_TIPOS_AUDIO = ("WAVE", "EVAW", "AIFF", "FFIA", "fFXM", "MXFf")


def leer_archivos_audio(d, bloques):
    lista = _primero(bloques, 0x1004)
    if not lista:
        return []
    archivos = []
    for c in lista.hijos:
        if c.tipo_contenido != 0x103A:
            continue
        p = c.inicio + 11
        while p < c.fin:
            nombre, p = _cadena(d, p)
            tipo = d[p:p + 4].decode("latin-1")
            p += 9
            if ".grp" in nombre or "Audio Files" in nombre or "Fade Files" in nombre:
                continue
            if tipo[0] != "\0":
                if not any(t in tipo for t in _TIPOS_AUDIO):
                    continue
            elif not any(e in nombre.lower() for e in (".wav", ".aif", ".mxf")):
                continue
            archivos.append({"indice": len(archivos), "nombre": nombre})
    largos = [_entero(d, x.inicio + 8, 8) for c in lista.hijos if c.tipo_contenido == 0x1003
              for x in c.hijos if x.tipo_contenido == 0x1001]
    for a, n in zip(archivos, largos):
        a["muestras"] = n
    return archivos


def leer_regiones_audio(d, bloques, ganancias=None):
    lista = _primero(bloques, 0x262A) or _primero(bloques, 0x100B)
    if not lista:
        return []
    regiones = []
    for c in lista.hijos:
        if c.tipo_contenido not in (0x2629, 0x1008):
            continue
        nombre, j = _cadena(d, c.inicio + 11)
        inicio, desplazamiento, largo = _tres_puntos(d, j)
        grupo = c.hijos[0] if c.hijos else None
        archivo = struct.unpack_from("<I", d, grupo.fin)[0] if grupo else None
        region = {"indice": len(regiones), "nombre": nombre, "archivo": archivo,
                  "desde_muestra": desplazamiento, "muestras": largo,
                  "inicio_original": inicio}
        nombre_b = next((h for h in c.hijos if h.tipo_contenido == 0x2628), None)
        if nombre_b and ganancias:
            i = struct.unpack_from("<i", d, nombre_b.fin - 4)[0]
            if 0 <= i < len(ganancias):
                nodos = ganancias[i]
                if len(nodos) == 1:
                    region["clip_gain_db"] = nodos[0]["db"]
                else:
                    region["clip_gain"] = nodos
        regiones.append(region)
    return regiones


def leer_clip_gain(d, bloques):
    """Diccionario de clip gain (bloque 0x2637).

    u32 cantidad de registros; cada registro: 01 46 01 00, u32 tamaño (el
    registro mide 8 + tamaño), u32 cantidad de nodos en +10 y, desde +22,
    pares [u32 posición en muestras, float32 dB]. Un solo nodo = ganancia fija.
    Cada región (0x2628) guarda en sus últimos 4 bytes el índice (int32,
    -1 = sin clip gain)."""
    b = _primero(bloques, 0x2637)
    if not b:
        return []
    p = b.inicio + 2
    n = struct.unpack_from("<I", d, p)[0]
    k = p + 4
    out = []
    for _ in range(n):
        if d[k:k + 4] != b"\x01\x46\x01\x00" or k + 22 > b.fin:
            break
        tam = struct.unpack_from("<I", d, k + 4)[0]
        cant = struct.unpack_from("<I", d, k + 10)[0]
        nodos = []
        for j in range(cant):
            pos, db = struct.unpack_from("<If", d, k + 22 + 8 * j)
            nodos.append({"muestra": pos, "db": round(db, 2)})
        out.append(nodos)
        k += 8 + tam
    return out


_MODO_FUNDIDO = {1: "cruzado", 2: "salida", 3: "entrada"}
_FORMA_FUNDIDO = {1: "potencia", 2: "lineal"}


def leer_fundidos(d, bloques):
    """Geometrías de fundido (bloque 0x2630 con hijos 0x262f).

    Byte +5: anchos en nibbles (bajo = ancho del pre-roll, alto = ancho de la
    duración); desde +8 el pre-roll y la duración en muestras; luego el modo
    (1 cruzado, 2 salida, 3 entrada) y la forma. El pre-roll es la parte del
    fundido antes del ancla del evento: 0 en una entrada, igual a la duración
    en una salida."""
    lista = _primero(bloques, 0x2630)
    if not lista:
        return []
    out = []
    for g in lista.hijos:
        if g.tipo_contenido != 0x262F:
            continue
        p = g.inicio + 2
        anchos = d[p + 5]
        wp, wt = anchos & 15, anchos >> 4
        pre = _entero(d, p + 8, wp) if wp else 0
        dur = _entero(d, p + 8 + wp, wt) if wt else 0
        modo, forma = d[p + 8 + wp + wt], d[p + 9 + wp + wt]
        out.append({"tipo": _MODO_FUNDIDO.get(modo, modo), "pre_roll": pre, "muestras": dur,
                    "forma": _FORMA_FUNDIDO.get(forma, forma)})
    return out


def leer_lista_pistas(d, bloques):
    """Todas las pistas de la sesión en orden (bloque 0x2519): nombre y tipo.

    El nombre de una pista es el de su playlist activa. La lista aparece dos
    veces seguidas; se toma la primera mitad."""
    lista = _primero(bloques, 0x2519)
    if not lista:
        return []
    tipos = {0: "audio", 1: "midi", 2: "auxiliar"}
    pistas = []
    for c in lista.hijos:
        if c.tipo_contenido != 0x251A:
            continue
        tipo = struct.unpack_from("<H", d, c.inicio + 2)[0]
        nombre, _ = _cadena(d, c.inicio + 4)
        pistas.append({"nombre": nombre, "tipo": tipos.get(tipo, f"0x{tipo:04x}")})
    mitad = len(pistas) // 2
    if mitad and pistas[:mitad] == pistas[mitad:]:
        pistas = pistas[:mitad]
    return pistas


def leer_pistas_audio(d, bloques, regiones, activas=None, fundidos=None):
    """Playlists de audio (bloque 0x1014) con sus canales y regiones colocadas.

    Cada playlist trae los números de sus canales; cada número es la posición
    de su lista de colocaciones en el mapa 0x1054. 'activas' son los nombres de
    las pistas actuales (leer_lista_pistas): las playlists con otro nombre son
    alternativas guardadas y se devuelven aparte.
    Devuelve (pistas, playlists_inactivas)."""
    playlists = []
    lista = _primero(bloques, 0x1015)
    if lista:
        for c in lista.hijos:
            if c.tipo_contenido != 0x1014:
                continue
            nombre, j = _cadena(d, c.inicio + 2)
            j += 1
            n = struct.unpack_from("<I", d, j)[0]
            ids = list(struct.unpack_from(f"<{n}H", d, j + 4))
            playlists.append({"nombre": nombre, "ids": ids})

    mapa = _primero(bloques, 0x1054)
    entradas = [c for c in mapa.hijos if c.tipo_contenido == 0x1052] if mapa else []

    fundidos = fundidos or []

    def colocadas(c):
        # Evento 0x1050 -> 0x104F: +2 mute, +4 índice, +9 muestra (ancla).
        # El último byte del 0x1050 vale 1 si el índice apunta a un fundido.
        out, fun = [], []
        for e in c.hijos:
            if e.tipo_contenido != 0x1050:
                continue
            for f in e.hijos:
                if f.tipo_contenido != 0x104F:
                    continue
                ri = struct.unpack_from("<I", d, f.inicio + 4)[0]
                muestra = _entero(d, f.inicio + 9, 5)
                if d[e.fin - 1] == 1:
                    if ri < len(fundidos):
                        g = fundidos[ri]
                        fun.append({"tipo": g["tipo"], "muestra": muestra - g["pre_roll"],
                                    "muestras": g["muestras"], "forma": g["forma"]})
                elif ri < len(regiones):
                    item = {"region": regiones[ri]["nombre"], "indice_region": ri, "muestra": muestra}
                    if d[f.inicio + 2] == 1:
                        item["mute"] = True
                    out.append(item)
        return sorted(out, key=lambda r: r["muestra"]), sorted(fun, key=lambda r: r["muestra"])

    pistas, inactivas = [], []
    for pl in playlists:
        canales = []
        for k, i in enumerate(pl["ids"]):
            regs, fun = colocadas(entradas[i]) if i < len(entradas) else ([], [])
            canal = {"canal": k + 1, "regiones": regs}
            if fun:
                canal["fundidos"] = fun
            canales.append(canal)
        item = {"nombre": pl["nombre"], "canales": canales}
        if activas is None or pl["nombre"] in activas:
            pistas.append(item)
        else:
            inactivas.append(item)
    return pistas, inactivas


def leer_grupos(d, bloques):
    """Grupos de pistas (bloque 0x202A): nombre e índices de sus pistas en la
    lista de leer_lista_pistas. <ALL> es el grupo de todas las pistas."""
    raiz = _primero(bloques, 0x202B)
    if not raiz:
        return []
    grupos = []
    for g in raiz.hijos:
        if g.tipo_contenido != 0x202A:
            continue
        nombre, p = _cadena(d, g.inicio + 2)
        n = struct.unpack_from("<I", d, p + 7)[0]
        miembros = list(struct.unpack_from(f"<{n}H", d, p + 11)) if n < 4096 else []
        grupo = {"nombre": nombre, "pistas": miembros}
        if grupo not in grupos:          # la lista aparece repetida
            grupos.append(grupo)
    return grupos


# ------------------------------------------------------------------- MIDI

def _bloques_notas(d, bloques):
    b = _primero(bloques, 0x2000)
    if not b:
        return []
    bloques_n, k = [], b.pos
    while True:
        k = d.find(b"MdNLB", k, b.fin)
        if k < 0:
            break
        n = struct.unpack_from("<I", d, k + 11)[0]
        p, notas = k + 15, []
        for i in range(n):
            q = p + i * 35
            notas.append({"tick": _sin_cero(_entero(d, q, 5)), "nota": d[q + 8],
                          "ticks": _entero(d, q + 9, 5), "velocidad": d[q + 17]})
        bloques_n.append(notas)
        k = p + n * 35
    return bloques_n


def leer_regiones_midi(d, bloques):
    fuentes = _bloques_notas(d, bloques)
    lista = _primero(bloques, 0x2634) or _primero(bloques, 0x2002)
    if not lista:
        return []
    regiones = []
    for c in lista.hijos:
        if c.tipo_contenido not in (0x2633, 0x2001):
            continue
        g = next((x for x in c.hijos if x.tipo_contenido in (0x2628, 0x1007)), None)
        if not g:
            continue
        nombre, j = _cadena(d, g.inicio + 2)
        _, desplazamiento, largo = _tres_puntos(d, j)
        desplazamiento = _sin_cero(desplazamiento)
        fuente = struct.unpack_from("<I", d, g.fin)[0]
        notas = []
        if fuente < len(fuentes):
            for n in fuentes[fuente]:
                if desplazamiento <= n["tick"] < desplazamiento + largo:
                    notas.append({**n, "tick": n["tick"] - desplazamiento})
        regiones.append({"indice": len(regiones), "nombre": nombre, "ticks": largo, "notas": notas})
    return regiones


def leer_pistas_midi(d, bloques, regiones):
    mapa = _primero(bloques, 0x1058)
    if not mapa:
        return []
    pistas = []
    for c in mapa.hijos:
        if c.tipo_contenido != 0x1057:
            continue
        nombre, _ = _cadena(d, c.inicio + 2)
        colocadas = []
        for e in c.hijos:
            if e.tipo_contenido != 0x1056:
                continue
            for f in e.hijos:
                if f.tipo_contenido != 0x104F:
                    continue
                ri = struct.unpack_from("<I", d, f.inicio + 4)[0]
                tick = _sin_cero(_entero(d, f.inicio + 9, 5))
                if ri < len(regiones):
                    colocadas.append({"region": regiones[ri]["nombre"], "indice_region": ri, "tick": tick})
        colocadas.sort(key=lambda r: r["tick"])
        pistas.append({"nombre": nombre, "regiones": colocadas})
    return pistas

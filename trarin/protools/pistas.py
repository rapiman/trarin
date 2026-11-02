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
            i = _indice_clip_gain(d, nombre_b)
            if 0 <= i < len(ganancias):
                nodos = ganancias[i]
                if len(nodos) == 1:
                    region["clip_gain_db"] = nodos[0]["db"]
                else:
                    region["clip_gain"] = nodos
        regiones.append(region)
    return regiones


def leer_grupos_clips(d, bloques, regiones=None, ganancias=None):
    """Grupos de clips (Clip Groups; bloque 0x262C con hijos 0x262B) y su contenido.

    Cada 0x262B va como una región: nombre («Bombo.grp-01»), un byte (0 = el grupo
    es dueño de un contenido, 1 = copia de otro grupo), los tres puntos (inicio
    original, CERO + desplazamiento, largo en muestras) y, como en una región, el
    índice de su clip gain. Una colocación cuyo byte +20 del 0x104F vale 1 apunta a
    esta lista, no a la de regiones (Pro Tools la renumera en cada guardado).

    El contenido va en un 0x1054 dentro del 0x2428: una entrada 0x1052 por familia
    (un dueño y sus copias), en el orden en que cada familia aparece por primera vez
    en la lista. Una copia no apunta a su dueño: es el dueño con el mismo inicio base
    (inicio original − desplazamiento) cuya ventana la contiene (desempate: la misma
    ventana; después, el primero). Verificado en el show RAPIZUAM: 15 de 40 grupos
    aparecen clip por clip, sueltos, en otra versión del show (docs/formato_ptx.md).
    Las posiciones del contenido son CERO + muestra relativa al inicio de la ventana
    del dueño sin recortar; una colocación del contenido puede ser otro grupo.

    Con 'regiones', cada grupo trae "contenido": [(muestra desde el inicio de su
    ventana, índice de región, muestras extra de «desde» dentro de la región, largo,
    mute)] ya recortado a su ventana y con los grupos anidados expandidos."""
    lista = _primero(bloques, 0x262C)
    if not lista:
        return []
    grupos = []
    for c in lista.hijos:
        if c.tipo_contenido != 0x262B:
            continue
        nombre, j = _cadena(d, c.inicio + 11)
        inicio, desplazamiento, largo = _tres_puntos(d, j)
        off = desplazamiento - CERO if desplazamiento >= CERO else desplazamiento
        g = {"indice": len(grupos), "nombre": nombre, "muestras": largo, "inicio_original": inicio,
             "copia": d[j] == 1, "desplazamiento": off, "base": inicio - off}
        b = next((h for h in c.hijos if h.tipo_contenido == 0x2628), None)
        if b is not None and ganancias:
            i = _indice_clip_gain(d, b)
            if 0 <= i < len(ganancias):
                nodos = ganancias[i]
                if len(nodos) == 1:
                    g["clip_gain_db"] = nodos[0]["db"]
                else:
                    g["clip_gain"] = nodos
        grupos.append(g)
    _familias_grupos(grupos)
    if regiones is not None:
        _contenido_grupos(d, bloques, grupos, regiones)
    return grupos


def _familias_grupos(grupos):
    """'dueno' (índice del grupo dueño del contenido) y 'entrada' (índice de la
    entrada del 0x2428) de cada grupo; None si una copia no tiene dueño reconocible."""
    duenos = [g for g in grupos if not g["copia"]]
    entradas = {}
    for g in grupos:
        if not g["copia"]:
            g["dueno"] = g["indice"]
        else:
            a, b = g["desplazamiento"], g["desplazamiento"] + g["muestras"]
            cand = [o for o in duenos if o["base"] == g["base"]
                    and o["desplazamiento"] <= a and b <= o["desplazamiento"] + o["muestras"]]
            cand.sort(key=lambda o: (not (o["desplazamiento"] == a and o["muestras"] == g["muestras"]), o["indice"]))
            g["dueno"] = cand[0]["indice"] if cand else None
        if g["dueno"] is not None and g["dueno"] not in entradas:
            entradas[g["dueno"]] = len(entradas)
        g["entrada"] = entradas.get(g["dueno"])


def _contenido_grupos(d, bloques, grupos, regiones):
    b2428 = _primero(bloques, 0x2428)
    mapa = next((h for h in b2428.hijos if h.tipo_contenido == 0x1054), None) if b2428 else None
    entradas = [c for c in mapa.hijos if c.tipo_contenido == 0x1052] if mapa else []
    if len(entradas) != len({g["dueno"] for g in grupos if g["dueno"] is not None}):
        for g in grupos:          # no calza la cantidad: mejor no inventar contenido
            g["contenido"] = None
        return

    def eventos(e):
        out = []
        for ev in e.hijos:
            if ev.tipo_contenido != 0x1050:
                continue
            for f in ev.hijos:
                if f.tipo_contenido != 0x104F or d[f.fin] == 1:      # los fundidos no se leen aún
                    continue
                idx = struct.unpack_from("<I", d, f.inicio + 4)[0]
                es_grupo = f.inicio + 20 < f.fin and d[f.inicio + 20] == 1
                out.append((_entero(d, f.inicio + 9, 5) - CERO, idx, es_grupo, d[f.inicio + 2] == 1))
        return sorted(out)

    hechos, en_curso = {}, set()

    def expandir(gi):
        if gi in hechos:
            return hechos[gi]
        g = grupos[gi]
        if gi in en_curso or g["entrada"] is None or g["entrada"] >= len(entradas):
            return None
        en_curso.add(gi)
        a, b = g["desplazamiento"], g["desplazamiento"] + g["muestras"]
        out = []
        for p, idx, es_grupo, mute in eventos(entradas[g["entrada"]]):
            if es_grupo:
                sub = expandir(idx) if idx < len(grupos) else None
                if sub is None:
                    continue
                piezas = [(p + q, r, x, L, mute or m) for q, r, x, L, m in sub]
            elif idx < len(regiones):
                piezas = [(p, idx, 0, regiones[idx]["muestras"], mute)]
            else:
                continue
            for ini, r, x, L, m in piezas:
                x0, x1 = max(ini, a), min(ini + L, b)
                if x1 > x0:
                    out.append((x0 - a, r, x + (x0 - ini), x1 - x0, m))
        en_curso.discard(gi)
        hechos[gi] = sorted(out)
        return hechos[gi]

    for g in grupos:
        g["contenido"] = expandir(g["indice"])


def _indice_clip_gain(d, b):
    """Índice de clip gain (int32, -1 = ninguno) de un bloque 0x2628.

    Va 27 bytes después de la última marca FF FF 04 00 04 00. En PT 12 eso
    coincide con los últimos 4 bytes; Pro Tools 2026 agrega después 00 00 y un
    UUID de 16 bytes."""
    k = d.rfind(b"\xff\xff\x04\x00\x04\x00", b.inicio, b.fin)
    if k < 0 or k + 31 > b.fin:
        return -1
    return struct.unpack_from("<i", d, k + 27)[0]


def leer_clip_gain(d, bloques):
    """Diccionario de clip gain (bloque 0x2637).

    u32 cantidad de registros; cada registro: 01 46 01 00, u32 tamaño (el
    registro mide 8 + tamaño), u32 cantidad de nodos en +10 y, desde +22,
    pares [u32 posición en muestras, float32 dB]. Un solo nodo = ganancia fija.
    Cada región (0x2628) guarda el índice (ver _indice_clip_gain)."""
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
_PENDIENTE = {1: "potencia", 2: "ganancia"}      # Equal Power / Equal Gain
_CURVA = {0: "estandar", 1: "s", 2: "preset"}
# Posición de cada curva dentro de la cola que empieza en el byte de modo.
_CURVAS = {3: [("curva", 6)], 2: [("curva", 12)],
           1: [("curva_salida", 12), ("curva_entrada", 19)]}


def leer_fundidos(d, bloques):
    """Geometrías de fundido (bloque 0x2630 con hijos 0x262F).

    Byte +5: anchos en nibbles (bajo = ancho del pre-roll, alto = ancho de la
    duración); desde +8 el pre-roll y la duración en muestras. Sigue la cola:
    modo (1 cruzado, 2 salida, 3 entrada), pendiente (1 Equal Power,
    2 Equal Gain) y las curvas [tipo u8, preset i16] (tipo 0 estándar, 1 S,
    2 preset; el preset se cuenta desde la curva lineal). El pre-roll es la
    parte del fundido antes del ancla del evento: 0 en una entrada, igual a la
    duración en una salida."""
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
        m = p + 8 + wp + wt
        modo = d[m]
        item = {"tipo": _MODO_FUNDIDO.get(modo, modo), "pre_roll": pre, "muestras": dur,
                "pendiente": _PENDIENTE.get(d[m + 1], d[m + 1])}
        for clave, k in _CURVAS.get(modo, []):
            if m + k + 3 <= g.fin:
                tipo = d[m + k]
                curva = {"forma": _CURVA.get(tipo, tipo)}
                if tipo == 2:
                    curva["preset"] = struct.unpack_from("<h", d, m + k + 1)[0]
                item[clave] = curva
        out.append(item)
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


def leer_pistas_audio(d, bloques, regiones, activas=None, fundidos=None, grupos_clips=None):
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
    grupos_clips = grupos_clips or []

    def colocadas(c):
        # Evento 0x1050 -> 0x104F: +2 mute, +4 índice, +9 muestra (ancla),
        # +20 vale 1 si el índice apunta a un grupo de clips (lista 0x262C).
        # El primer byte después del 0x104F vale 1 si el índice apunta a un
        # fundido (PT 12.5 deja 1 byte ahí; sesiones más antiguas, 2).
        out, fun = [], []
        for e in c.hijos:
            if e.tipo_contenido != 0x1050:
                continue
            for f in e.hijos:
                if f.tipo_contenido != 0x104F:
                    continue
                ri = struct.unpack_from("<I", d, f.inicio + 4)[0]
                muestra = _entero(d, f.inicio + 9, 5)
                if d[f.fin] == 1:           # primer byte tras el 0x104F: 1 = fundido
                    if ri < len(fundidos):
                        g = fundidos[ri]
                        f_ = {k: v for k, v in g.items() if k != "pre_roll"}
                        f_["muestra"] = muestra - g["pre_roll"]
                        fun.append(f_)
                elif f.inicio + 20 < f.fin and d[f.inicio + 20] == 1:
                    g = grupos_clips[ri] if ri < len(grupos_clips) else {"nombre": f"grupo {ri}", "muestras": 0}
                    item = {"grupo": g["nombre"], "indice_grupo": ri, "muestra": muestra, "muestras": g["muestras"]}
                    if d[f.inicio + 2] == 1:
                        item["mute"] = True
                    out.append(item)
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

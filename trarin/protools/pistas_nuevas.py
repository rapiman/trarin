"""Agregar pistas de audio mono a una sesión .ptx.

Verificado creando una pista mono al final de la sesión en Pro Tools 12 y
comparando el archivo. Una pista nueva es una copia de una pista mono de audio
que ya existe (la plantilla), con nombre, números e identificadores propios:

* lista de pistas 0x2519: en sus bytes propios, la cantidad (u32 justo antes de
  la primera entrada) y una entrada nueva (u16 tipo, nombre, u16 estéreo, UUID
  de 16 bytes, u16 número, u8 0); un 0x251A al final de cada mitad de la lista
  y, entre las mitades, otra vez la cantidad (u32);
* una playlist 0x1014 y una entrada vacía 0x1052, al final de las playlists en
  uso (las alternativas quedan después y se corren sus números);
* un canal 0x261C al final de 0x2624 (copia del canal de la plantilla): nombre,
  UUID, identificadores nuevos en su 0x261B, número correlativo del 0x2015 y,
  tras el 0x261B, u32 número de canal de audio, u16 cantidad de playlists,
  u32 por playlist y FFFF;
* identificadores de 8 bytes (tras 2A 00 00 00) nuevos para la pista (251A y
  nombre del canal) y la playlist: repetidos, Pro Tools no abre la sesión;
* el grupo <ALL> (0x202A): cantidad y la lista de pistas (u16), y 0x202C;
* la ventana (0x258A con hijos 0x2589): una tira 0x2589 más, con su número;
* el índice 0x0002: posiciones en las entradas de lista de 0x1015, 0x1054,
  0x2519 y 0x2624, una entrada por el 0x251A nuevo de la segunda mitad, el
  número de la entrada de 0x2716 y una entrada para el 0x2627 del canal nuevo.

Una pista estéreo parte de la misma plantilla mono: marcas de estéreo en 0x2519
y 0x251A, dos entradas 0x1052, y en el canal la marca estéreo, la entrada
estéreo de la sesión, el paneo −100/+100 y una segunda lista de paneo
(verificado contra una pista estéreo creada en Pro Tools 12).
"""

import os
import struct

from .bloques import arbol, buscar
from .indice import Entrada, Grupo, Paso, leer_indice, escribir_indice
from .pistas import _cadena
from .reconstruccion import reconstruir, ErrorReconstruccion


class ErrorPista(Exception):
    pass


def _primero(bloques, tipo):
    b = buscar(bloques, tipo)
    return b[0] if b else None


def _str(texto):
    b = texto.encode("utf-8")
    return struct.pack("<I", len(b)) + b


def _ids_canal(d, sb):
    """Lista de identificadores del canal en los bytes del 0x261B que siguen a
    sus hijos: u16 n y n u32 consecutivos. Devuelve (posición del u16, ids).

    Se busca desde el último fader (0x260D): si un identificador termina en
    0x5A, el árbol puede ver ahí un hijo falso."""
    faders = [h for h in sb.hijos if h.tipo_contenido == 0x260D]
    p = faders[-1].fin if faders else (sb.hijos[-1].fin if sb.hijos else sb.inicio)
    for k in range(p, sb.fin - 6):
        n = struct.unpack_from("<H", d, k)[0]
        if 2 <= n <= 64 and k + 2 + 4 * n <= sb.fin:
            v = struct.unpack_from(f"<{n}I", d, k + 2)
            if all(v[i + 1] == v[i] + 1 for i in range(n - 1)) and v[0] > 0:
                return k, list(v)
    raise ErrorPista("identificadores del canal no reconocidos")


def _id_nuevo(d, viejo, usados=()):
    """Identificador de 8 bytes (los que siguen a 2A 00 00 00) que no exista en la sesión."""
    alto = viejo[:4]
    n = max((struct.unpack_from("<I", d, i + 8)[0] for i in _ocurrencias(d, b"\x2a\0\0\0" + alto)),
            default=struct.unpack_from("<I", viejo, 4)[0])
    while True:
        n = (n + 1) & 0xFFFFFFFF
        nuevo = alto + struct.pack("<I", n)
        if nuevo not in d and nuevo not in usados:
            return nuevo


def _ocurrencias(d, patron):
    i = d.find(patron)
    while i >= 0:
        yield i
        i = d.find(patron, i + 1)


def _lista_2519(d, L):
    """Entradas de los bytes propios de 0x2519: (inicio, fin, tipo, nombre, estéreo, uuid, número).

    La cabecera no tiene siempre el mismo largo: la lista se ubica buscando la
    primera pista (u16 tipo + nombre) precedida por la cantidad de pistas."""
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    if not hijos or len(hijos) % 2:
        raise ErrorPista("lista de pistas no reconocida")
    n = len(hijos) // 2
    h0 = hijos[0]
    tipo0 = struct.unpack_from("<H", d, h0.inicio + 2)[0]
    nombre0, _ = _cadena(d, h0.inicio + 4)
    patron = struct.pack("<IH", n, tipo0) + _str(nombre0)
    q = d.find(patron, L.inicio, h0.pos)
    if q < 0:
        raise ErrorPista("lista de pistas no reconocida")
    p = q + 4
    out = []
    for _ in range(n):
        ini = p
        tipo = struct.unpack_from("<H", d, p)[0]
        nombre, p = _cadena(d, p + 2)
        estereo = struct.unpack_from("<H", d, p)[0]
        uuid = d[p + 2:p + 18]
        numero = struct.unpack_from("<H", d, p + 18)[0]
        p += 21
        out.append((ini, p, tipo, nombre, estereo, uuid, numero))
    if p > h0.pos:
        raise ErrorPista("lista de pistas no reconocida")
    return out, p, q


def agregar_pistas(d, nombres, plantilla=None):
    """d: sesión descifrada. nombres: nombres de las pistas nuevas (al final),
    cada uno como texto (mono) o como (nombre, canales) con canales 1 o 2.
    plantilla: nombre de una pista mono de audio existente (por defecto, la
    primera que sirva). Devuelve la sesión descifrada con las pistas nuevas."""
    for x in nombres:
        nombre, canales = (x, 1) if isinstance(x, str) else x
        if canales not in (1, 2):
            raise ErrorPista("solo se pueden crear pistas mono o estéreo")
        try:
            d = _agregar_una(d, nombre, plantilla, canales)
        except ErrorPista as e:
            if plantilla or "de la plantilla no reconocida" not in str(e):
                raise
            # la primera pista mono no sirve de plantilla (p. ej. su paneo): se prueban las otras
            entradas = _lista_2519(d, _primero(arbol(d), 0x2519))[0]
            for otra in [x[3] for x in entradas if x[2] == 0 and x[4] == 0]:
                try:
                    d = _agregar_una(d, nombre, otra, canales)
                    break
                except ErrorPista:
                    continue
            else:
                raise
    return d


def _rehacer(d, blq, fijos, variables):
    """Bytes de un bloque copiado con cambios: 'fijos' (posición, bytes) del
    mismo largo y 'variables' (inicio, fin, bytes) que cambian el tamaño; se
    corrige el tamaño de cada bloque que los contiene. Posiciones de d."""
    base = blq.pos
    c = bytearray(d[blq.pos:blq.fin])
    for p, b in fijos:
        c[p - base:p - base + len(b)] = b
    for b in blq.recorrer():
        if any(i <= b.pos and b.fin <= f for i, f, _ in variables):
            continue                                  # bloque reemplazado entero
        delta = sum(len(n) - (f - i) for i, f, n in variables if i >= b.inicio and f <= b.fin)
        if delta:
            struct.pack_into("<I", c, b.pos - base + 3, b.tamano + delta)
    for i, f, n in sorted(variables, key=lambda x: x[0], reverse=True):
        c[i - base:f - base] = n
    return bytes(c)


def _agregar_una(d, nombre, plantilla, canales=1):
    bloques = arbol(d)
    L = _primero(bloques, 0x2519)
    entradas, fin_lista, pos_cantidad = _lista_2519(d, L)
    if any(e[3] == nombre for e in entradas):
        raise ErrorPista(f"ya existe una pista llamada {nombre!r}")
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    mitad = len(hijos) // 2
    if len(hijos) != 2 * len(entradas):
        raise ErrorPista("lista de pistas no reconocida")
    # plantilla: pista mono de audio
    playlists = {}
    for b in buscar(bloques, 0x1014):
        nom, j = _cadena(d, b.inicio + 2)
        k = struct.unpack_from("<I", d, j + 1)[0]
        playlists.setdefault(nom, (b, list(struct.unpack_from(f"<{k}H", d, j + 5))))
    candidatas = [e for e in entradas if e[2] == 0 and e[4] == 0 and e[3] in playlists
                  and len(playlists[e[3]][1]) == 1]
    raiz = _primero(bloques, 0x2624)
    if raiz is None or len(raiz.hijos) != len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")

    def una_playlist(e):
        c = raiz.hijos[entradas.index(e)]
        sb = next((h for h in c.hijos if h.tipo_contenido == 0x261B), None)
        return (c.tipo_contenido == 0x261C and sb is not None
                and struct.unpack_from("<H", d, sb.fin + 4)[0] == 1)

    candidatas = [e for e in candidatas if una_playlist(e)]
    if plantilla:
        candidatas = [e for e in candidatas if e[3] == plantilla]
    if not candidatas:
        raise ErrorPista("no hay una pista de audio mono que sirva de plantilla")
    t = candidatas[0]
    i_t = entradas.index(t)
    uuid_t = t[5]
    uuid = os.urandom(16)
    n_pistas = len(entradas)
    reemplazos = []

    # 1) lista de pistas 0x2519: entrada propia + dos 0x251A
    reemplazos.append((pos_cantidad, pos_cantidad + 4, struct.pack("<I", n_pistas + 1)))
    entrada = struct.pack("<H", 0) + _str(nombre) + struct.pack("<H", canales - 1) + uuid + struct.pack("<HB", n_pistas + 1, 0)
    reemplazos.append((fin_lista, fin_lista, entrada, L.pos))

    # identificador de 8 bytes de la pista (251A y nombre del canal lo comparten)
    q = d.find(b"\x2a\0\0\0", hijos[i_t].inicio, hijos[i_t].fin)
    id_t = d[q + 4:q + 12]
    id_n = _id_nuevo(d, id_t)

    def nuevo_251a(h):
        b = bytearray(d[h.pos:h.fin].replace(id_t, id_n))
        _, q = _cadena(b, 11)
        cola = bytearray(b[q:])
        struct.pack_into("<H", cola, 0, canales - 1)          # estéreo
        cola[-16:] = uuid
        n_off = len(cola) - 16 - 6
        struct.pack_into("<I", cola, n_off, n_pistas + 1)
        cont = b[7:9] + b[9:11] + _str(nombre) + bytes(cola)
        return b"\x5a" + b[1:3] + struct.pack("<I", len(cont)) + cont

    # entre las dos mitades de 0x251A va otra vez la cantidad de pistas (u32)
    m = hijos[mitad - 1].fin
    if struct.unpack_from("<I", d, m)[0] != n_pistas or hijos[mitad].pos != m + 4:
        raise ErrorPista("lista de pistas no reconocida (cantidad entre mitades)")
    reemplazos.append((m, m + 4, nuevo_251a(hijos[i_t]) + struct.pack("<I", n_pistas + 1), L.pos))
    reemplazos.append((hijos[-1].fin, hijos[-1].fin, nuevo_251a(hijos[mitad + i_t]), L.pos))

    # 2) playlist y entrada del mapa. Pro Tools guarda primero las playlists en uso
    #    (las que se llaman como su pista) y después las alternativas; la nueva va
    #    al final de las primeras y se corren los números de las segundas.
    lista_pl = _primero(bloques, 0x1015)
    mapa = _primero(bloques, 0x1054)
    n_pl = struct.unpack_from("<I", d, lista_pl.inicio + 2)[0]
    n_map = struct.unpack_from("<I", d, mapa.inicio + 2)[0]
    pls = [h for h in lista_pl.hijos if h.tipo_contenido == 0x1014]
    e1052 = [h for h in mapa.hijos if h.tipo_contenido == 0x1052]
    nombres_pistas = {e[3] for e in entradas}
    ids_pl = []
    for b in pls:
        nom, j = _cadena(d, b.inicio + 2)
        k = struct.unpack_from("<I", d, j + 1)[0]
        ids_pl.append((nom, j + 5, list(struct.unpack_from(f"<{k}H", d, j + 5))))
    en_uso = [i for i, (nom, _, _) in enumerate(ids_pl) if nom in nombres_pistas]
    i_pl = max(en_uso) + 1 if en_uso else len(pls)
    i_map = max((max(ids_pl[i][2]) for i in en_uso if ids_pl[i][2]), default=-1) + 1 if en_uso else n_map
    for nom, q, lista in ids_pl:
        if any(x >= i_map for x in lista):
            reemplazos.append((q, q + 2 * len(lista),
                               struct.pack(f"<{len(lista)}H", *[x + canales * (x >= i_map) for x in lista])))
    b_pl, _ = playlists[t[3]]
    _, j = _cadena(d, b_pl.inicio + 2)
    cola = bytearray(d[j + 7:b_pl.fin])
    if cola[4:8] == b"\x2a\0\0\0":           # identificador propio de la playlist
        cola[8:16] = _id_nuevo(d, bytes(cola[8:16]))
    struct.pack_into("<H", cola, len(cola) - 2, canales - 1)
    cont = (b"\x14\x10" + _str(nombre) + bytes([canales - 1]) + struct.pack("<I", canales)
            + struct.pack(f"<{canales}H", *range(i_map, i_map + canales)) + bytes(cola))
    reemplazos.append((lista_pl.inicio + 2, lista_pl.inicio + 6, struct.pack("<I", n_pl + 1)))
    donde = pls[i_pl].pos if i_pl < len(pls) else pls[-1].fin
    reemplazos.append((donde, donde, d[b_pl.pos:b_pl.pos + 3] + struct.pack("<I", len(cont)) + cont,
                       lista_pl.pos))
    cont = b"\x52\x10" + _str(nombre) + bytes(4) + b"\x01\x00"
    reemplazos.append((mapa.inicio + 2, mapa.inicio + 6, struct.pack("<I", n_map + canales)))
    donde = e1052[i_map].pos if i_map < len(e1052) else e1052[-1].fin
    reemplazos.append((donde, donde, (b"\x5a\x03\x00" + struct.pack("<I", len(cont)) + cont) * canales,
                       mapa.pos))

    # 3) canal de mezcla
    canales_mezcla = raiz.hijos
    canal_t = canales_mezcla[i_t]
    if canal_t.tipo_contenido != 0x261C:
        raise ErrorPista("el canal de la plantilla no es de audio")
    n_audio = sum(1 for c in canales_mezcla if c.tipo_contenido == 0x261C)
    ids = []
    for c in canales_mezcla:
        sb = next((h for h in c.hijos if h.tipo_contenido == 0x261B), None)
        if sb is None:
            continue
        try:
            ids += _ids_canal(d, sb)[1]
        except ErrorPista:
            pass
    nuevo_id = max(ids) + 1
    sb = next(h for h in canal_t.hijos if h.tipo_contenido == 0x261B)
    fijos, variables = [], []
    k, lista = _ids_canal(d, sb)
    fijos.append((k + 2, struct.pack(f"<{len(lista)}I", *range(nuevo_id, nuevo_id + len(lista)))))
    # identificadores de 16 bytes de los parámetros del canal (tras FF 7F, u16 n)
    q = d.find(b"\xff\x7f", k, sb.fin)
    if q > 0:
        n16 = struct.unpack_from("<H", d, q + 2)[0]
        fijos.append((q + 4, os.urandom(16 * n16)))
    # número de canal de audio y playlist (tras el 0x261B)
    if struct.unpack_from("<H", d, sb.fin + 4)[0] != 1:
        raise ErrorPista("el canal de la plantilla tiene varias playlists")
    fijos.append((sb.fin, struct.pack("<IHI", n_audio, 1, i_pl)))
    # número correlativo del 0x2015 del canal (u32 final)
    nums = [struct.unpack_from("<I", d, b.fin - 4)[0] for b in buscar(bloques, 0x2015)]
    for b in buscar([canal_t], 0x2015):
        fijos.append((b.fin - 4, struct.pack("<I", max(nums) + 1)))
    # nombre (0x102D → 0x2619, en +2)
    cab = next(h for h in sb.hijos if h.tipo_contenido == 0x102D)
    nb = cab.hijos[0]
    viejo = _str(_cadena(d, nb.inicio + 2)[0])
    variables.append((nb.inicio + 2, nb.inicio + 2 + len(viejo), _str(nombre)))
    if canales == 2:
        _canal_estereo(d, canales_sesion=canales_mezcla, sb=sb, k=k, n_ids=len(lista),
                       fijos=fijos, variables=variables)
    c = _rehacer(d, canal_t, fijos, variables).replace(uuid_t, uuid).replace(id_t, id_n)
    # playlists de los demás canales que se corren
    for ca in canales_mezcla:
        if ca.tipo_contenido != 0x261C:
            continue
        sbc = next(h for h in ca.hijos if h.tipo_contenido == 0x261B)
        a, n = struct.unpack_from("<IH", d, sbc.fin)
        lista = struct.unpack_from(f"<{n}I", d, sbc.fin + 6)
        if any(x >= i_pl for x in lista):
            reemplazos.append((sbc.fin + 6, sbc.fin + 6 + 4 * n,
                               struct.pack(f"<{n}I", *[x + (x >= i_pl) for x in lista])))
    reemplazos.append((raiz.inicio + 2, raiz.inicio + 6, struct.pack("<I", len(canales_mezcla) + 1)))
    reemplazos.append((canales_mezcla[-1].fin, canales_mezcla[-1].fin, bytes(c), raiz.pos))

    # 4) grupo <ALL>
    for grp in buscar(bloques, 0x202A):
        nom, p = _cadena(d, grp.inicio + 2)
        if nom != "<ALL>":
            continue
        cnt = struct.unpack_from("<I", d, p + 7)[0]
        fin_l = p + 11 + 2 * cnt
        reemplazos.append((p + 7, p + 11, struct.pack("<I", cnt + 1)))
        reemplazos.append((fin_l, fin_l, struct.pack("<H", n_pistas)))
    for gc in buscar(bloques, 0x202C):
        a1, a2 = struct.unpack_from("<II", d, gc.inicio + 14)
        if struct.unpack_from("<I", d, gc.inicio + 2)[0] == 1 and (a1, a2) == (n_pistas - 1, n_pistas):
            reemplazos.append((gc.inicio + 14, gc.inicio + 22, struct.pack("<II", n_pistas, n_pistas + 1)))

    # 5) tira de la ventana
    for v in buscar(bloques, 0x258A):
        tiras = [h for h in v.hijos if h.tipo_contenido == 0x2589]
        if not tiras:
            continue
        mono = next((h for h in tiras if struct.unpack_from("<H", d, h.inicio + 2)[0] == i_t), tiras[0])
        b = bytearray(d[mono.pos:mono.fin])
        struct.pack_into("<H", b, 9, n_pistas)
        q = tiras[0].pos - 4                     # u32 cantidad justo antes de las tiras
        reemplazos.append((q, q + 4, struct.pack("<I", len(tiras) + 1)))
        reemplazos.append((tiras[-1].fin, tiras[-1].fin, bytes(b), v.pos))

    nuevo = reconstruir(d, reemplazos)
    return _actualizar_indice(nuevo, n_pistas, len(canales_mezcla), i_pl, i_map, canales)


def _canal_estereo(d, canales_sesion, sb, k, n_ids, fijos, variables):
    """Convierte la copia de un canal mono en estéreo, como lo hace Pro Tools 12
    al crear una pista estéreo: marca de estéreo (u8 1, u8 0) tras el nombre 0x102D del 0x261B, entrada
    estéreo, paneo izquierdo −100 y derecho +100 en el fader 0x1029 (+15/+43 y
    su copia 86 bytes después) y una segunda lista de automatización de paneo."""
    cab = next(h for h in sb.hijos if h.tipo_contenido == 0x102D)
    fijos.append((cab.fin, b"\x01\x00"))       # tras el nombre: u8 estéreo, u8 canal de entrada
    # entrada: texto 9 bytes después de los identificadores
    p = k + 2 + 4 * n_ids + 9
    viejo, fin = _cadena(d, p)
    if fin - p > 68:
        raise ErrorPista("entrada del canal de la plantilla no reconocida")
    nombre_e, id_e = _entrada_estereo(d, canales_sesion, viejo)
    variables.append((p, fin, _str(nombre_e)))
    # la entrada se identifica por los 8 bytes que siguen a 00000000 2A000000
    if id_e is not None and d[fin:fin + 8] == b"\0\0\0\0\x2a\0\0\0":
        fijos.append((fin + 8, id_e))
    z = next(h for h in sb.hijos if h.tipo_contenido == 0x260D)
    f = next(h for h in z.hijos if h.tipo_contenido == 0x1029)
    for o, v in ((15, -100), (43, 100), (101, -100), (129, 100)):
        if f.inicio + o + 4 <= f.fin:
            fijos.append((f.inicio + o, struct.pack("<i", v)))
    listas = [h for h in z.hijos if h.tipo_contenido == 0x260C]
    if len(listas) < 2 or len(listas[0].hijos) != 1 or listas[1].hijos:
        raise ErrorPista("automatización de paneo de la plantilla no reconocida")
    pan = listas[0].hijos[0]
    fijos.append((pan.inicio + 28, struct.pack("<h", -100)))
    der = bytearray(d[listas[0].pos:listas[0].fin])
    struct.pack_into("<h", der, pan.inicio + 28 - listas[0].pos, 100)
    variables.append((listas[1].pos, listas[1].fin, bytes(der)))


def _entrada_estereo(d, canales_sesion, mono):
    """Nombre e identificador de la entrada estéreo: los de un canal de audio
    estéreo de la sesión; si no hay, el nombre de la entrada mono sin su sufijo
    .L/.R y sin identificador."""
    for c in canales_sesion:
        sb = next((h for h in c.hijos if h.tipo_contenido == 0x261B), None)
        if (c.tipo_contenido != 0x261C or sb is None or not sb.hijos
                or sb.hijos[0].tipo_contenido != 0x102D or d[sb.hijos[0].fin] != 1):
            continue
        try:
            k, ids = _ids_canal(d, sb)
            nombre, fin = _cadena(d, k + 2 + 4 * len(ids) + 9)
        except (ErrorPista, struct.error):
            continue
        if d[fin:fin + 8] == b"\0\0\0\0\x2a\0\0\0":
            return nombre, d[fin + 8:fin + 16]
    base, _, suf = mono.rpartition(".")      # sin canal estéreo: Pro Tools la deja sin entrada
    return (base if base and suf in ("L", "R") else mono), None


def _insertar_en_grupos(e, lugar, pos):
    """Las listas del índice tienen un grupo por bloque: el primero es la lista y
    después un grupo por hijo, en orden. Inserta el hijo nuevo en su lugar."""
    if not all(len(g.posiciones) == 1 for g in e.grupos):
        raise ErrorPista("lista del índice no reconocida")
    e.grupos.insert(1 + lugar, Grupo([pos]))


def _actualizar_indice(d, n_pistas, n_canales, i_pl, i_map, canales=1, playlists=True):
    """Agrega al índice las posiciones de los bloques nuevos (los últimos de cada
    lista). playlists=False para un auxiliar (sin 0x1014 ni 0x1052)."""
    bloques = arbol(d)
    final = bloques[-1]
    entradas, resto = leer_indice(d[final.inicio:final.fin])
    L = _primero(bloques, 0x2519)
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    mitad = len(hijos) // 2
    lista_pl = _primero(bloques, 0x1015)
    mapa = _primero(bloques, 0x1054)
    raiz = _primero(bloques, 0x2624)
    nuevo_pl = [h for h in lista_pl.hijos if h.tipo_contenido == 0x1014][i_pl] if playlists else None
    e1052 = [h for h in mapa.hijos if h.tipo_contenido == 0x1052] if playlists else []
    canal = raiz.hijos[-1]
    sb = next(h for h in canal.hijos if h.tipo_contenido == 0x261B)
    ins = next((h for h in sb.hijos if h.tipo_contenido == 0x2627), None)
    hechos = set()
    for e in entradas:
        c = e.camino
        tipos = [s.tipo for s in c]
        if (len(c) == 1 and c[0].marca == 1 and c[0].numero == 0
                and tipos[0] in ((0x1015, 0x1054, 0x2624) if playlists else (0x2624,))):
            if tipos[0] == 0x1054:
                for i in range(canales):
                    _insertar_en_grupos(e, i_map + i, e1052[i_map + i].pos)
            else:
                nuevo = {0x1015: nuevo_pl, 0x2624: canal}[tipos[0]]
                lugar = {0x1015: i_pl, 0x2624: n_canales}[tipos[0]]
                _insertar_en_grupos(e, lugar, nuevo.pos)
            hechos.add(tipos[0])
        elif len(c) == 1 and tipos[0] == 0x2519 and c[0].marca == 1 and c[0].numero == 0:
            e.grupos.append(Grupo([hijos[-1].pos]))
            hechos.add("2519 lista")
        elif tipos == [0x2519, 0x251A] and c[0].marca == 0:
            e.grupos[0].posiciones.append(hijos[mitad - 1].pos)
            hechos.add("2519 mitad")
        elif tipos == [0x2519, 0x2716] and c[0].numero == n_pistas:
            c[0].numero = n_pistas + 1
            hechos.add("2716")
    faltan = ({0x1015, 0x1054} if playlists else set()) | {0x2624, "2519 lista", "2519 mitad", "2716"}
    faltan -= hechos
    if faltan:
        raise ErrorPista(f"índice no reconocido (faltan entradas: {faltan})")
    raiz_pos = 0xFFFFFFFF
    nueva_251a = Entrada([Paso(0x2519, raiz_pos, 1, n_pistas + 1), Paso(0x251A, L.pos, 0, 0)],
                         [Grupo([hijos[-1].pos])])
    i_2716 = next(i for i, e in enumerate(entradas) if [s.tipo for s in e.camino] == [0x2519, 0x2716])
    entradas.insert(i_2716, nueva_251a)
    if ins is not None:
        ult = max(i for i, e in enumerate(entradas) if [s.tipo for s in e.camino][:2] in
                  ([0x2624, 0x261C], [0x2624, 0x261E], [0x2624, 0x2621]))
        entradas.insert(ult + 1, Entrada([Paso(0x2624, raiz_pos, 1, n_canales + 1),
                                          Paso(canal.tipo_contenido, raiz.pos, 0, 0),
                                          Paso(0x261B, canal.pos, 0, 0),
                                          Paso(0x2627, sb.pos, 0, 0)], [Grupo([ins.pos])]))
    nuevo = escribir_indice(entradas, resto)
    out = bytearray(d[:final.inicio]) + nuevo
    struct.pack_into("<I", out, final.pos + 3, len(nuevo))
    return bytes(out)


# -------------------------------------------------------- pistas auxiliares

def agregar_auxiliares(d, nombres, plantilla=None, externa=None):
    """Agrega pistas auxiliares al final, cada una como texto (estéreo) o como
    (nombre, canales). Son copia de un auxiliar de la sesión con la misma
    cantidad de canales (plantilla: su nombre), con el mismo ruteo.

    externa: (sesión descifrada, nombre de un auxiliar) para copiar un
    auxiliar de otra sesión cuando esta no tiene uno que sirva. Sus inserts
    quedan vacíos y su ruteo es el de la otra sesión: hay que copiarlos
    después (inserts.copiar_inserts y ruteo.copiar_ruteo)."""
    for x in nombres:
        nombre, canales = (x, 2) if isinstance(x, str) else x
        d = _agregar_aux(d, nombre, plantilla, canales, externa)
    return d


def _inserts_vacios(dt, ins):
    """Bytes de un 0x2627 como 'ins' (de la sesión dt) con los 11 lugares vacíos."""
    vacio = next((h for h in ins.hijos if h.tipo_contenido == 0x2625), None)
    if vacio is None:
        raise ErrorPista("no hay un lugar de insert vacío que copiar")
    cuerpo = bytes(dt[ins.inicio:ins.hijos[0].pos]) + bytes(dt[vacio.pos:vacio.fin]) * (len(ins.hijos))
    return bytes(dt[ins.pos:ins.pos + 3]) + struct.pack("<I", len(cuerpo)) + cuerpo


def _agregar_aux(d, nombre, plantilla, canales, externa=None):
    """Verificado creando un auxiliar estéreo y uno mono al final en Pro Tools 12
    y comparando: entrada de tipo 2 en 0x2519 y un 0x251A en cada mitad, un
    canal 0x261E al final de 0x2624, el grupo <ALL>, una tira 0x2589 y el
    índice (listas de 0x2519 y 0x2624, la entrada del 0x2627 del canal). No
    tiene playlists y 0x202C no cambia. El canal nuevo es copia del de la
    plantilla con nombre (también en 0x102D → 0x2619), uuid, identificadores
    propios y el número siguiente del 0x2015."""
    bloques = arbol(d)
    L = _primero(bloques, 0x2519)
    entradas, fin_lista, pos_cantidad = _lista_2519(d, L)
    if any(e[3] == nombre for e in entradas):
        raise ErrorPista(f"ya existe una pista llamada {nombre!r}")
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    mitad = len(hijos) // 2
    raiz = _primero(bloques, 0x2624)
    if raiz is None or len(raiz.hijos) != len(entradas) or len(hijos) != 2 * len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")
    canales_mezcla = raiz.hijos
    # la plantilla: de esta sesión, o de otra (externa)
    dt, bt = (externa[0], arbol(externa[0])) if externa else (d, bloques)
    Lt = _primero(bt, 0x2519)
    entradas_t = _lista_2519(dt, Lt)[0]
    hijos_t = [h for h in Lt.hijos if h.tipo_contenido == 0x251A]
    mitad_t = len(hijos_t) // 2
    canales_t = _primero(bt, 0x2624).hijos
    cands = [i for i, e in enumerate(entradas_t) if e[2] == 2 and e[4] == canales - 1
             and canales_t[i].tipo_contenido == 0x261E]
    if plantilla or externa:
        cands = [i for i in cands if entradas_t[i][3] == (externa[1] if externa else plantilla)]
    if not cands:
        raise ErrorPista(f"no hay un auxiliar {'estéreo' if canales == 2 else 'mono'} que sirva de plantilla")
    i_t = cands[0]
    t = entradas_t[i_t]
    uuid_t, uuid = t[5], os.urandom(16)
    n_pistas = len(entradas)
    reemplazos = []
    # 1) 0x2519
    reemplazos.append((pos_cantidad, pos_cantidad + 4, struct.pack("<I", n_pistas + 1)))
    entrada = (struct.pack("<H", 2) + _str(nombre) + struct.pack("<H", canales - 1) + uuid
               + struct.pack("<HB", n_pistas + 1, 0))
    reemplazos.append((fin_lista, fin_lista, entrada, L.pos))
    q = dt.find(b"\x2a\0\0\0", hijos_t[i_t].inicio, hijos_t[i_t].fin)
    id_t = bytes(dt[q + 4:q + 12])
    id_n = _id_nuevo(d, id_t)

    def nuevo_251a(h):
        b = bytearray(dt[h.pos:h.fin].replace(id_t, id_n))
        _, q = _cadena(b, 11)
        cola = bytearray(b[q:])
        cola[-16:] = uuid
        struct.pack_into("<I", cola, len(cola) - 16 - 6, n_pistas + 1)
        cont = b[7:9] + b[9:11] + _str(nombre) + bytes(cola)
        return b"\x5a" + b[1:3] + struct.pack("<I", len(cont)) + cont

    m = hijos[mitad - 1].fin
    if struct.unpack_from("<I", d, m)[0] != n_pistas or hijos[mitad].pos != m + 4:
        raise ErrorPista("lista de pistas no reconocida (cantidad entre mitades)")
    reemplazos.append((m, m + 4, nuevo_251a(hijos_t[i_t]) + struct.pack("<I", n_pistas + 1), L.pos))
    reemplazos.append((hijos[-1].fin, hijos[-1].fin, nuevo_251a(hijos_t[mitad_t + i_t]), L.pos))
    # 2) canal 0x261E
    canal_t = canales_t[i_t]
    ids = []
    for c in canales_mezcla:
        sb = next((h for h in c.hijos if h.tipo_contenido == 0x261B), None)
        if sb is None:
            continue
        try:
            ids += _ids_canal(d, sb)[1]
        except ErrorPista:
            pass
    sb = next(h for h in canal_t.hijos if h.tipo_contenido == 0x261B)
    k, lista = _ids_canal(dt, sb)
    nuevo_id = max(ids) + 1
    fijos = [(k + 2, struct.pack(f"<{len(lista)}I", *range(nuevo_id, nuevo_id + len(lista))))]
    q = dt.find(b"\xff\x7f", k, sb.fin)
    if q > 0:
        fijos.append((q + 4, os.urandom(16 * struct.unpack_from("<H", dt, q + 2)[0])))
    nums = [struct.unpack_from("<I", d, b.fin - 4)[0] for b in buscar(bloques, 0x2015)]
    for b in buscar([canal_t], 0x2015):
        fijos.append((b.fin - 4, struct.pack("<I", max(nums) + 1)))
    cab = next(h for h in sb.hijos if h.tipo_contenido == 0x102D)
    nb = cab.hijos[0]
    viejo = _str(_cadena(dt, nb.inicio + 2)[0])
    variables = [(nb.inicio + 2, nb.inicio + 2 + len(viejo), _str(nombre))]
    if externa:                                     # los inserts de la otra sesión no sirven aquí
        ins = next((h for h in sb.hijos if h.tipo_contenido == 0x2627), None)
        if ins is not None:
            variables.append((ins.pos, ins.fin, _inserts_vacios(dt, ins)))
    c = _rehacer(dt, canal_t, fijos, variables).replace(uuid_t, uuid).replace(id_t, id_n)
    reemplazos.append((raiz.inicio + 2, raiz.inicio + 6, struct.pack("<I", len(canales_mezcla) + 1)))
    reemplazos.append((canales_mezcla[-1].fin, canales_mezcla[-1].fin, bytes(c), raiz.pos))
    # 3) grupo <ALL> (0x202C no cambia)
    for grp in buscar(bloques, 0x202A):
        nom, p = _cadena(d, grp.inicio + 2)
        if nom != "<ALL>":
            continue
        cnt = struct.unpack_from("<I", d, p + 7)[0]
        fin_l = p + 11 + 2 * cnt
        reemplazos.append((p + 7, p + 11, struct.pack("<I", cnt + 1)))
        reemplazos.append((fin_l, fin_l, struct.pack("<H", n_pistas)))
    # 4) tira de la ventana, copia de la de la plantilla
    tiras_t = [h for v in buscar(bt, 0x258A) for h in v.hijos if h.tipo_contenido == 0x2589]
    propia = next((h for h in tiras_t if struct.unpack_from("<H", dt, h.inicio + 2)[0] == i_t), None)
    for v in buscar(bloques, 0x258A):
        tiras = [h for h in v.hijos if h.tipo_contenido == 0x2589]
        if not tiras:
            continue
        if propia is None:
            raise ErrorPista("tira del auxiliar de plantilla no encontrada")
        b = bytearray(dt[propia.pos:propia.fin])
        struct.pack_into("<H", b, 9, n_pistas)
        q = tiras[0].pos - 4
        reemplazos.append((q, q + 4, struct.pack("<I", len(tiras) + 1)))
        reemplazos.append((tiras[-1].fin, tiras[-1].fin, bytes(b), v.pos))
    nuevo = reconstruir(d, reemplazos)
    return _actualizar_indice(nuevo, n_pistas, len(canales_mezcla), None, None, playlists=False)


# ------------------------------------------------------------- pistas MIDI

def agregar_pistas_midi(d, nombres, plantilla=None, interna=False):
    """Agrega pistas MIDI vacías al final. plantilla: nombre de una pista MIDI
    de la sesión (por defecto la primera; si no hay, se usan los bloques de una
    pista MIDI creada en Pro Tools 12)."""
    for nombre in nombres:
        d = _agregar_midi(d, nombre, plantilla, interna)
    return d


def _bloque_suelto(b):
    """(bytes, bloque) de un bloque copiado aparte, para editarlo con _rehacer."""
    from .bloques import _parsear
    return b, _parsear(b, 0, len(b))


def _agregar_midi(d, nombre, plantilla, interna=False):
    """Verificado creando una pista MIDI en Pro Tools 12 y comparando: suma una
    entrada a 0x2519 y un 0x251A en cada mitad (tipo 1), una playlist 0x1057
    vacía en 0x1058, un 0x2005 (salida MIDI propia) al final de 0x2004, un canal 0x2620 al
    final de 0x2624, el grupo <ALL>, una tira 0x2589 y sus posiciones en el
    índice (listas de 0x1058, 0x2519 y 0x2624; el 0x2620 no tiene 0x2627)."""
    from . import plantillas_midi as T
    bloques = arbol(d)
    L = _primero(bloques, 0x2519)
    entradas, fin_lista, pos_cantidad = _lista_2519(d, L)
    if any(e[3] == nombre for e in entradas):
        raise ErrorPista(f"ya existe una pista llamada {nombre!r}")
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    mitad = len(hijos) // 2
    raiz = _primero(bloques, 0x2624)
    if raiz is None or len(raiz.hijos) != len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")
    mapa = _primero(bloques, 0x1058)
    salidas = _primero(bloques, 0x2004)
    if mapa is None or salidas is None:
        raise ErrorPista("la sesión no tiene listas de pistas MIDI")
    l1057 = [h for h in mapa.hijos if h.tipo_contenido == 0x1057]
    l2005 = [h for h in salidas.hijos if h.tipo_contenido == 0x2005]
    canales = raiz.hijos
    cands = [i for i, e in enumerate(entradas) if e[2] == 1 and canales[i].tipo_contenido == 0x2620]
    if interna:
        cands = []
    if plantilla:
        cands = [i for i in cands if entradas[i][3] == plantilla]
        if not cands:
            raise ErrorPista(f"no hay una pista MIDI llamada {plantilla!r}")
    n_pistas = len(entradas)
    uuid = os.urandom(16)
    if cands:
        i_t = cands[0]
        t = entradas[i_t]
        uuid_t = t[5]
        b251a = (d[hijos[i_t].pos:hijos[i_t].fin], d[hijos[mitad + i_t].pos:hijos[mitad + i_t].fin])
        pl = next((h for h in l1057 if _cadena(d, h.inicio + 2)[0] == t[3]), None)
        if pl is None:
            raise ErrorPista(f"no se encontró la playlist MIDI de {t[3]!r}")
        t1057 = _bloque_suelto(d[pl.pos:pl.fin])
        t2620 = _bloque_suelto(d[canales[i_t].pos:canales[i_t].fin])
        tira = None
    else:
        i_t = None
        b251a = (T.PISTA_MIDI_251A, T.PISTA_MIDI_251A)
        uuid_t = T.PISTA_MIDI_251A[-16:]
        t1057 = _bloque_suelto(T.PISTA_MIDI_1057)
        t2620 = _bloque_suelto(T.PISTA_MIDI_2620)
        tira = T.PISTA_MIDI_2589
    q = b251a[0].find(b"\x2a\0\0\0", 7)
    id_t = b251a[0][q + 4:q + 12]
    if cands:
        id_n = _id_nuevo(d, id_t)
    else:                       # misma parte alta que los identificadores de las pistas de la sesión
        q0 = d.find(b"\x2a\0\0\0", hijos[0].inicio, hijos[0].fin)
        id_n = _id_nuevo(d, d[q0 + 4:q0 + 8] + id_t[4:])
    reemplazos = []

    # 1) 0x2519: cantidad, entrada, dos 0x251A y la cantidad entre mitades
    reemplazos.append((pos_cantidad, pos_cantidad + 4, struct.pack("<I", n_pistas + 1)))
    entrada = (struct.pack("<H", 1) + _str(nombre) + struct.pack("<H", 0) + uuid
               + struct.pack("<HB", n_pistas + 1, 0))
    reemplazos.append((fin_lista, fin_lista, entrada, L.pos))

    def nuevo_251a(h):
        b = bytearray(h.replace(id_t, id_n))
        _, q = _cadena(b, 11)
        cola = bytearray(b[q:])
        cola[-16:] = uuid
        struct.pack_into("<I", cola, len(cola) - 16 - 6, n_pistas + 1)
        cont = b[7:9] + b[9:11] + _str(nombre) + bytes(cola)
        return b"\x5a" + b[1:3] + struct.pack("<I", len(cont)) + cont

    m = hijos[mitad - 1].fin
    if struct.unpack_from("<I", d, m)[0] != n_pistas or hijos[mitad].pos != m + 4:
        raise ErrorPista("lista de pistas no reconocida (cantidad entre mitades)")
    reemplazos.append((m, m + 4, nuevo_251a(b251a[0]) + struct.pack("<I", n_pistas + 1), L.pos))
    reemplazos.append((hijos[-1].fin, hijos[-1].fin, nuevo_251a(b251a[1]), L.pos))

    # 2) playlist 0x1057 vacía al final de 0x1058
    tb, tq = t1057
    n_viejo = struct.unpack_from("<I", tb, tq.inicio + 2)[0]
    p_cant = tq.inicio + 6 + n_viejo
    variables = [(tq.inicio + 2, p_cant, _str(nombre))]
    variables += [(h.pos, h.fin, b"") for h in tq.hijos if h.tipo_contenido == 0x1056]
    nuevo_1057 = _rehacer(tb, tq, [(p_cant, struct.pack("<I", 0))], variables)
    n_1058 = struct.unpack_from("<I", d, mapa.inicio + 2)[0]
    reemplazos.append((mapa.inicio + 2, mapa.inicio + 6, struct.pack("<I", n_1058 + 1)))
    punto = l1057[-1].fin if l1057 else mapa.inicio + 6
    reemplazos.append((punto, punto, nuevo_1057, mapa.pos))

    # 3) 0x2004: lista de salidas MIDI (dispositivo y canal). Cada canal MIDI
    #    apunta a la suya con un u32 (1 = la primera) en +14 del 0x2036; si dos
    #    pistas comparten la entrada, Pro Tools se cae al cerrar la sesión. Se
    #    agrega una entrada propia al final, copia de la de la plantilla.
    if cands:
        b36 = buscar([canales[i_t]], 0x2036)
        k = struct.unpack_from("<I", d, b36[0].inicio + 14)[0] if b36 else 0
        # sin salida válida (p. ej. 0): la de la pista creada en Pro Tools 12
        t2005 = d[l2005[k - 1].pos:l2005[k - 1].fin] if 1 <= k <= len(l2005) else T.PISTA_MIDI_2005
    else:
        t2005 = T.PISTA_MIDI_2005
    n_2004 = struct.unpack_from("<I", d, salidas.inicio + 2)[0]
    if n_2004 != len(l2005):
        raise ErrorPista("lista de salidas MIDI no reconocida")
    reemplazos.append((salidas.inicio + 2, salidas.inicio + 6, struct.pack("<I", n_2004 + 1)))
    punto = l2005[-1].fin if l2005 else salidas.inicio + 6
    reemplazos.append((punto, punto, t2005, salidas.pos))

    # 4) canal 0x2620: nombre, número del 0x2015, UUID e identificador
    cb, cq = t2620
    cab = next(h for h in cq.hijos if h.tipo_contenido == 0x102D)
    nb = cab.hijos[0]
    viejo = _str(_cadena(cb, nb.inicio + 2)[0])
    nums = [struct.unpack_from("<I", d, b.fin - 4)[0] for b in buscar(bloques, 0x2015)]
    fijos = [(b.fin - 4, struct.pack("<I", max(nums, default=0) + 1)) for b in buscar([cq], 0x2015)]
    fijos += [(b.inicio + 14, struct.pack("<I", len(l2005) + 1)) for b in buscar([cq], 0x2036)]
    # tras el nombre 0x102D del 0x2620: u32 n, u32 1, u32 n, u8 1, donde n es el
    # número del canal entre los canales MIDI e instrumento (0x2620/0x2621), desde
    # 0; repetido, Pro Tools se cae al cerrar la sesión
    n_midi = sum(1 for c in canales if c.tipo_contenido in (0x2620, 0x2621))
    q = cq.hijos[0].fin
    if (cq.hijos[0].tipo_contenido != 0x102D or struct.unpack_from("<I", cb, q + 4)[0] != 1
            or cb[q + 12] != 1):
        raise ErrorPista("canal MIDI de plantilla no reconocido")
    fijos += [(q, struct.pack("<I", n_midi)), (q + 8, struct.pack("<I", n_midi))]
    canal = _rehacer(cb, cq, fijos, [(nb.inicio + 2, nb.inicio + 2 + len(viejo), _str(nombre))])
    canal = canal.replace(uuid_t, uuid).replace(id_t, id_n)
    reemplazos.append((raiz.inicio + 2, raiz.inicio + 6, struct.pack("<I", len(canales) + 1)))
    reemplazos.append((canales[-1].fin, canales[-1].fin, canal, raiz.pos))

    # 5) grupo <ALL>
    for grp in buscar(bloques, 0x202A):
        nom, p = _cadena(d, grp.inicio + 2)
        if nom != "<ALL>":
            continue
        cnt = struct.unpack_from("<I", d, p + 7)[0]
        fin_l = p + 11 + 2 * cnt
        reemplazos.append((p + 7, p + 11, struct.pack("<I", cnt + 1)))
        reemplazos.append((fin_l, fin_l, struct.pack("<H", n_pistas)))
    # (0x202C no cambia al crear una pista MIDI, a diferencia de una de audio)

    # 6) tira de la ventana
    for v in buscar(bloques, 0x258A):
        tiras = [h for h in v.hijos if h.tipo_contenido == 0x2589]
        if not tiras:
            continue
        if i_t is not None:
            propia = next((h for h in tiras if struct.unpack_from("<H", d, h.inicio + 2)[0] == i_t), None)
            if propia is None:
                raise ErrorPista("tira de la pista MIDI de plantilla no encontrada")
            b = bytearray(d[propia.pos:propia.fin])
        else:
            b = bytearray(tira)
        struct.pack_into("<H", b, 9, n_pistas)
        q = tiras[0].pos - 4
        reemplazos.append((q, q + 4, struct.pack("<I", len(tiras) + 1)))
        reemplazos.append((tiras[-1].fin, tiras[-1].fin, bytes(b), v.pos))

    nuevo = reconstruir(d, reemplazos)
    return _actualizar_indice_midi(nuevo, n_pistas, len(canales), len(l1057))


def _actualizar_indice_midi(d, n_pistas, n_canales, n_1057):
    bloques = arbol(d)
    final = bloques[-1]
    entradas, resto = leer_indice(d[final.inicio:final.fin])
    L = _primero(bloques, 0x2519)
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    mitad = len(hijos) // 2
    mapa = _primero(bloques, 0x1058)
    raiz = _primero(bloques, 0x2624)
    nuevo_pl = [h for h in mapa.hijos if h.tipo_contenido == 0x1057][n_1057]
    hechos = set()
    for e in entradas:
        c = e.camino
        tipos = [s.tipo for s in c]
        if len(c) == 1 and c[0].marca == 1 and c[0].numero == 0 and tipos[0] in (0x1058, 0x2624):
            nuevo, lugar = (nuevo_pl, n_1057) if tipos[0] == 0x1058 else (raiz.hijos[-1], n_canales)
            _insertar_en_grupos(e, lugar, nuevo.pos)
            hechos.add(tipos[0])
        elif len(c) == 1 and tipos[0] == 0x2519 and c[0].marca == 1 and c[0].numero == 0:
            e.grupos.append(Grupo([hijos[-1].pos]))
            hechos.add("2519 lista")
        elif tipos == [0x2519, 0x251A] and c[0].marca == 0:
            e.grupos[0].posiciones.append(hijos[mitad - 1].pos)
            hechos.add("2519 mitad")
        elif tipos == [0x2519, 0x2716] and c[0].numero == n_pistas:
            c[0].numero = n_pistas + 1
            hechos.add("2716")
    faltan = {0x1058, 0x2624, "2519 lista", "2519 mitad", "2716"} - hechos
    if faltan:
        raise ErrorPista(f"índice no reconocido (faltan entradas: {faltan})")
    nueva_251a = Entrada([Paso(0x2519, 0xFFFFFFFF, 1, n_pistas + 1), Paso(0x251A, L.pos, 0, 0)],
                         [Grupo([hijos[-1].pos])])
    i_2716 = next(i for i, e in enumerate(entradas) if [s.tipo for s in e.camino] == [0x2519, 0x2716])
    entradas.insert(i_2716, nueva_251a)
    nuevo = escribir_indice(entradas, resto)
    out = bytearray(d[:final.inicio]) + nuevo
    struct.pack_into("<I", out, final.pos + 3, len(nuevo))
    return bytes(out)


# ------------------------------------------------------- pistas de instrumento

def agregar_instrumento(d, nombre, externa):
    """Agrega al final una pista de instrumento vacía, copia de la pista de
    instrumento externa = (sesión descifrada, nombre) de otra sesión.

    Verificado creando una pista de instrumento estéreo en Pro Tools 12
    (v9_antes_inst → v10_instrumento): como una pista MIDI (entrada de tipo 7
    en 0x2519 y un 0x251A en cada mitad, playlist 0x1057 vacía en 0x1058, grupo
    <ALL>, tira 0x2589 y en el índice las listas de 0x1058, 0x2519 y 0x2624 y
    el número de 0x2716), pero el canal es un 0x2621 con 0x102D, 0x2627
    (inserts, con su entrada en el índice), 0x260D (fader), 0x2010, 0x206F y
    0x2611 (canal MIDI). No agrega salida MIDI a 0x2004 (u32 en +14 del 0x2036
    en 0); tras el 0x2010 menos 11 bytes: u32 n, u16 1, u32 n, u8 1, con n el
    número entre los canales MIDI e instrumento. Del canal de la otra sesión se
    cambian nombre, UUID, identificadores, número del 0x2015, los
    identificadores de parámetros y los UUID tras FF7F; los inserts quedan
    vacíos y el ruteo es el de la otra sesión: hay que copiarlos después
    (inserts.copiar_inserts, ruteo.copiar_ruteo, ruteo.copiar_midi)."""
    dt, pista_t = externa
    bloques = arbol(d)
    bt = arbol(dt)
    L = _primero(bloques, 0x2519)
    entradas, fin_lista, pos_cantidad = _lista_2519(d, L)
    if any(e[3] == nombre for e in entradas):
        raise ErrorPista(f"ya existe una pista llamada {nombre!r}")
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    mitad = len(hijos) // 2
    raiz = _primero(bloques, 0x2624)
    mapa = _primero(bloques, 0x1058)
    if raiz is None or len(raiz.hijos) != len(entradas) or len(hijos) != 2 * len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")
    if mapa is None:
        raise ErrorPista("la sesión no tiene la lista de playlists MIDI (0x1058)")
    l1057 = [h for h in mapa.hijos if h.tipo_contenido == 0x1057]
    canales = raiz.hijos
    # plantilla en la otra sesión
    Lt = _primero(bt, 0x2519)
    entradas_t = _lista_2519(dt, Lt)[0]
    hijos_t = [h for h in Lt.hijos if h.tipo_contenido == 0x251A]
    mitad_t = len(hijos_t) // 2
    canales_t = _primero(bt, 0x2624).hijos
    i_t = next((i for i, e in enumerate(entradas_t) if e[3] == pista_t and e[2] == 7
                and canales_t[i].tipo_contenido == 0x2621), None)
    if i_t is None:
        raise ErrorPista(f"{pista_t!r} no es una pista de instrumento")
    t = entradas_t[i_t]
    uuid_t, uuid = t[5], os.urandom(16)
    pl = next((h for h in [x for x in _primero(bt, 0x1058).hijos if x.tipo_contenido == 0x1057]
               if _cadena(dt, h.inicio + 2)[0] == pista_t), None)
    if pl is None:
        raise ErrorPista(f"no se encontró la playlist de {pista_t!r}")
    q = dt.find(b"\x2a\0\0\0", hijos_t[i_t].inicio, hijos_t[i_t].fin)
    id_t = bytes(dt[q + 4:q + 12])
    id_n = _id_nuevo(d, id_t)
    n_pistas = len(entradas)
    reemplazos = []

    # 1) 0x2519
    reemplazos.append((pos_cantidad, pos_cantidad + 4, struct.pack("<I", n_pistas + 1)))
    entrada = (struct.pack("<H", 7) + _str(nombre) + struct.pack("<H", t[4]) + uuid
               + struct.pack("<HB", n_pistas + 1, 0))
    reemplazos.append((fin_lista, fin_lista, entrada, L.pos))

    def nuevo_251a(h):
        b = bytearray(dt[h.pos:h.fin].replace(id_t, id_n))
        _, q = _cadena(b, 11)
        cola = bytearray(b[q:])
        cola[-16:] = uuid
        struct.pack_into("<I", cola, len(cola) - 16 - 6, n_pistas + 1)
        cont = b[7:9] + b[9:11] + _str(nombre) + bytes(cola)
        return b"\x5a" + b[1:3] + struct.pack("<I", len(cont)) + cont

    m = hijos[mitad - 1].fin
    if struct.unpack_from("<I", d, m)[0] != n_pistas or hijos[mitad].pos != m + 4:
        raise ErrorPista("lista de pistas no reconocida (cantidad entre mitades)")
    reemplazos.append((m, m + 4, nuevo_251a(hijos_t[i_t]) + struct.pack("<I", n_pistas + 1), L.pos))
    reemplazos.append((hijos[-1].fin, hijos[-1].fin, nuevo_251a(hijos_t[mitad_t + i_t]), L.pos))

    # 2) playlist 0x1057 vacía
    n_viejo = struct.unpack_from("<I", dt, pl.inicio + 2)[0]
    p_cant = pl.inicio + 6 + n_viejo
    variables = [(pl.inicio + 2, p_cant, _str(nombre))]
    variables += [(h.pos, h.fin, b"") for h in pl.hijos if h.tipo_contenido == 0x1056]
    nuevo_1057 = _rehacer(dt, pl, [(p_cant, struct.pack("<I", 0))], variables)
    n_1058 = struct.unpack_from("<I", d, mapa.inicio + 2)[0]
    reemplazos.append((mapa.inicio + 2, mapa.inicio + 6, struct.pack("<I", n_1058 + 1)))
    punto = l1057[-1].fin if l1057 else mapa.inicio + 6
    reemplazos.append((punto, punto, nuevo_1057, mapa.pos))

    # 3) canal 0x2621
    canal_t = canales_t[i_t]
    ids = []
    for c in canales:
        sb = next((h for h in c.hijos if h.tipo_contenido == 0x261B), None) or \
            (c if c.tipo_contenido == 0x2621 else None)
        if sb is None:
            continue
        try:
            ids += _ids_canal(d, sb)[1]
        except ErrorPista:
            pass
    k, lista = _ids_canal(dt, canal_t)
    nuevo_id = max(ids, default=0) + 1
    fijos = [(k + 2, struct.pack(f"<{len(lista)}I", *range(nuevo_id, nuevo_id + len(lista))))]
    q = dt.find(b"\xff\x7f", k, canal_t.fin)
    if q > 0:
        fijos.append((q + 4, os.urandom(16 * struct.unpack_from("<H", dt, q + 2)[0])))
    nums = [struct.unpack_from("<I", d, b.fin - 4)[0] for b in buscar(bloques, 0x2015)]
    fijos += [(b.fin - 4, struct.pack("<I", max(nums, default=0) + 1)) for b in buscar([canal_t], 0x2015)]
    fijos += [(b.inicio + 14, struct.pack("<I", 0)) for b in buscar([canal_t], 0x2036)]
    n_midi = sum(1 for c in canales if c.tipo_contenido in (0x2620, 0x2621))
    h2010 = next((h for h in canal_t.hijos if h.tipo_contenido == 0x2010), None)
    if h2010 is None or struct.unpack_from("<H", dt, h2010.pos - 7)[0] != 1 or dt[h2010.pos - 1] != 1:
        raise ErrorPista("canal de instrumento de plantilla no reconocido")
    fijos += [(h2010.pos - 11, struct.pack("<I", n_midi)), (h2010.pos - 5, struct.pack("<I", n_midi))]
    cab = next(h for h in canal_t.hijos if h.tipo_contenido == 0x102D)
    nb = cab.hijos[0]
    viejo = _str(_cadena(dt, nb.inicio + 2)[0])
    variables = [(nb.inicio + 2, nb.inicio + 2 + len(viejo), _str(nombre))]
    ins = next((h for h in canal_t.hijos if h.tipo_contenido == 0x2627), None)
    if ins is not None:
        variables.append((ins.pos, ins.fin, _inserts_vacios(dt, ins)))
    canal = _rehacer(dt, canal_t, fijos, variables).replace(uuid_t, uuid).replace(id_t, id_n)
    reemplazos.append((raiz.inicio + 2, raiz.inicio + 6, struct.pack("<I", len(canales) + 1)))
    reemplazos.append((canales[-1].fin, canales[-1].fin, canal, raiz.pos))

    # 4) grupo <ALL>
    for grp in buscar(bloques, 0x202A):
        nom, p = _cadena(d, grp.inicio + 2)
        if nom != "<ALL>":
            continue
        cnt = struct.unpack_from("<I", d, p + 7)[0]
        fin_l = p + 11 + 2 * cnt
        reemplazos.append((p + 7, p + 11, struct.pack("<I", cnt + 1)))
        reemplazos.append((fin_l, fin_l, struct.pack("<H", n_pistas)))

    # 5) tira de la ventana
    tiras_t = [h for v in buscar(bt, 0x258A) for h in v.hijos if h.tipo_contenido == 0x2589]
    propia = next((h for h in tiras_t if struct.unpack_from("<H", dt, h.inicio + 2)[0] == i_t), None)
    for v in buscar(bloques, 0x258A):
        tiras = [h for h in v.hijos if h.tipo_contenido == 0x2589]
        if not tiras:
            continue
        if propia is None:
            raise ErrorPista("tira de la pista de instrumento de plantilla no encontrada")
        b = bytearray(dt[propia.pos:propia.fin])
        struct.pack_into("<H", b, 9, n_pistas)
        q = tiras[0].pos - 4
        reemplazos.append((q, q + 4, struct.pack("<I", len(tiras) + 1)))
        reemplazos.append((tiras[-1].fin, tiras[-1].fin, bytes(b), v.pos))

    nuevo = reconstruir(d, reemplazos)
    nuevo = _actualizar_indice_midi(nuevo, n_pistas, len(canales), len(l1057))
    return _indice_2627(nuevo, len(canales))


def _indice_2627(d, n_canales):
    """Agrega al índice la entrada del 0x2627 del último canal (0x2621, sin 0x261B)."""
    bloques = arbol(d)
    final = bloques[-1]
    entradas, resto = leer_indice(d[final.inicio:final.fin])
    raiz = _primero(bloques, 0x2624)
    canal = raiz.hijos[-1]
    ins = next((h for h in canal.hijos if h.tipo_contenido == 0x2627), None)
    if ins is None:
        return d
    ult = max((i for i, e in enumerate(entradas) if len(e.camino) > 1 and e.camino[0].tipo == 0x2624),
              default=next(i for i, e in enumerate(entradas) if e.camino[0].tipo == 0x2624))
    entradas.insert(ult + 1, Entrada([Paso(0x2624, 0xFFFFFFFF, 1, n_canales + 1),
                                      Paso(0x2621, raiz.pos, 0, 0),
                                      Paso(0x2627, canal.pos, 0, 0)], [Grupo([ins.pos])]))
    nuevo = escribir_indice(entradas, resto)
    out = bytearray(d[:final.inicio]) + nuevo
    struct.pack_into("<I", out, final.pos + 3, len(nuevo))
    return bytes(out)

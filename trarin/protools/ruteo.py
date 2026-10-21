"""Copiar el ruteo (entrada, salida y envíos) y la automatización de mezcla de
una pista de una sesión a una pista de otra, y juntar la automatización de dos
pistas.

Comparado con un envío agregado en Pro Tools 12 (v5_plugin → v6_envio) y con
el corpus (145 sesiones):

* las rutas de la sesión están en 0x1022 (u32 cantidad) → 0x1021: 2110, u8
  clase (00 entrada, 02 bus, 03 insert…), u8, u32 largo y nombre, …,
  2A000000 y el identificador de 8 bytes de la ruta; las salidas (y los buses)
  en 0x2603 → 0x2602: 0226, u8 clase, u8, u32 largo y nombre, …, 2A000000 e
  identificador (las mitades de un par llevan después el del par);
* la entrada del canal está en los bytes del 0x261B que siguen a sus hijos:
  tras la lista de identificadores del canal (u16 n y n u32) y 9 bytes, u32
  largo y nombre, u32 0, 2A000000 (o 0), identificador de 8 bytes de la ruta
  (o 0) y 7 bytes (00 02 … si la entrada es un bus); luego FFFFFFFF;
* cada 0x260D del 0x261B es un fader: el primero el del canal y los demás los
  envíos. Tras el primero va u16 cantidad de envíos y, por envío, u16 lugar
  (0 = A) y su 0x260D. Cada fader tiene 0x1029 (valores), 0x260E (destino:
  u16 clase 0x13 salida, 0x43 bus, FFFF ninguno; 01 01 u32 0; 2A000000 e
  identificador de 8 bytes de la ruta; ceros; u32 largo, nombre y 01) y listas
  de automatización 0x260A (también dentro de 0x260C, el paneo);
* 0x260A: 0A26 01 4601 00, u32 largo desde +12, 0000, u32 cantidad de
  puntos (+12), u16 ancho del valor (+16, siempre 2), u32 cantidad − 1 (+18),
  0000, y desde +24 los puntos: u32 muestra y el valor; 2 bytes al final.
  El primer punto va en la muestra 0: si no, Pro Tools descarta la lista al
  abrir la sesión y deja solo su primer valor [verificado en Pro Tools 12];
* el índice 0x0002 no cambia.

Las rutas se traducen por clase y nombre: un bus «Aux 1» de una canción va al
bus «Aux 1» del show. Lo que no existe en el show no se copia (se avisa).
"""

import struct

from .bloques import arbol, buscar
from .pistas import _cadena
from .pistas_nuevas import ErrorPista, _ids_canal, _lista_2519, _primero
from .reconstruccion import reconstruir


def rutas(d, bloques=None):
    """[(clase, nombre, identificador de 8 bytes)] de la sesión: las rutas de
    0x1022 (clase = el u8 de la entrada) y las salidas y buses de 0x2603
    (clase = 0x100 + el u8 de la entrada)."""
    bloques = bloques if bloques is not None else arbol(d)
    out = []
    for lista, tipo, desde, extra in ((0x1022, 0x1021, 4, 0), (0x2603, 0x2602, 4, 0x100)):
        for L in buscar(bloques, lista):
            for r in L.hijos:
                if r.tipo_contenido != tipo:
                    continue
                n = struct.unpack_from("<I", d, r.inicio + desde)[0]
                if n > 256:
                    continue
                nombre, p = _cadena(d, r.inicio + desde)
                q = d.find(b"\x2a\x00\x00\x00", p, r.fin)
                if q > 0 and q + 12 <= r.fin:
                    out.append((extra + d[r.inicio + 2], nombre, bytes(d[q + 4:q + 12])))
    return out


class _Tabla:
    def __init__(self, d, bloques):
        self.rutas = rutas(d, bloques)

    def clase(self, ident):
        return next((c for c, _, i in self.rutas if i == ident), None)

    def buscar(self, clase, nombre):
        xs = {i for c, n, i in self.rutas if n == nombre and (clase is None or c == clase)}
        return xs.pop() if len(xs) == 1 else None


def _canal(d, bloques, nombre):
    entradas = _lista_2519(d, _primero(bloques, 0x2519))[0]
    i = next((k for k, e in enumerate(entradas) if e[3] == nombre), None)
    if i is None:
        raise ErrorPista(f"no existe la pista {nombre!r}")
    raiz = _primero(bloques, 0x2624)
    if raiz is None or len(raiz.hijos) != len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")
    canal = raiz.hijos[i]
    sb = next((h for h in canal.hijos if h.tipo_contenido == 0x261B), None)
    if sb is None:
        raise ErrorPista(f"{nombre}: canal sin 0x261B (las pistas MIDI todavía no se copian)")
    return sb


def _faders(d, sb):
    """(faders 0x260D, inicio, fin del tramo de faders y envíos, posición del
    u16 de la lista de identificadores)."""
    fs = [h for h in sb.hijos if h.tipo_contenido == 0x260D]
    if not fs or any(h.pos > fs[-1].pos and h.tipo_contenido in (0x102D, 0x2627) for h in sb.hijos):
        raise ErrorPista("canal no reconocido: los faders no van al final del 0x261B")
    k, _ = _ids_canal(d, sb)
    fin = k - 2                                   # u8 u8 antes de la lista de identificadores
    previo = fs[-1].fin
    if fin < previo or fin - previo not in (0, 2):
        raise ErrorPista("canal no reconocido: bytes inesperados tras los faders")
    return fs, fs[0].pos, fin, k


def _entrada(d, sb):
    """(inicio, fin) del campo de entrada: u32 largo, nombre y 23 bytes."""
    k, ids = _ids_canal(d, sb)
    p = k + 2 + 4 * len(ids) + 9
    n = struct.unpack_from("<I", d, p)[0]
    fin = p + 4 + n + 23
    if n > 256 or d[fin:fin + 4] != b"\xff\xff\xff\xff":
        raise ErrorPista("canal no reconocido: entrada")
    return p, fin


def _destino_260e(d, e):
    """(clase, identificador, nombre) del destino de un 0x260E."""
    clase = struct.unpack_from("<H", d, e.inicio + 2)[0]
    if clase == 0xFFFF:
        return clase, None, None
    if d[e.inicio + 10:e.inicio + 14] != b"\x2a\x00\x00\x00":
        raise ErrorPista("destino de fader (0x260E) no reconocido")
    ident = bytes(d[e.inicio + 14:e.inicio + 22])
    for n in range(0, 128):
        p = e.fin - 1 - n - 4
        if p > e.inicio + 22 and struct.unpack_from("<I", d, p)[0] == n:
            return clase, ident, d[p + 4:p + 4 + n].decode("utf-8", "replace")
    raise ErrorPista("destino de fader (0x260E) sin nombre")


def _puntos(d, a):
    """(cantidad, ancho, [(muestra, valor en bytes)]) de un 0x260A."""
    n = struct.unpack_from("<I", d, a.inicio + 12)[0]
    w = struct.unpack_from("<H", d, a.inicio + 16)[0]
    p = a.inicio + 24
    if p + n * (4 + w) + 2 != a.fin:
        raise ErrorPista("lista de automatización (0x260A) no reconocida")
    return w, [(struct.unpack_from("<I", d, p + j * (4 + w))[0],
                bytes(d[p + j * (4 + w) + 4:p + (j + 1) * (4 + w)])) for j in range(n)]


def _contenido_260a(d, a, puntos):
    """Contenido nuevo (desde a.inicio) de un 0x260A con otros puntos."""
    w, _ = _puntos(d, a)
    cab = bytearray(d[a.inicio:a.inicio + 24])
    cuerpo = b"".join(struct.pack("<I", m) + v for m, v in puntos) + bytes(d[a.fin - 2:a.fin])
    struct.pack_into("<I", cab, 6, 12 + len(cuerpo))
    struct.pack_into("<I", cab, 12, len(puntos))
    struct.pack_into("<I", cab, 18, max(len(puntos) - 1, 0))
    return bytes(cab) + cuerpo


def copiar_ruteo(d, pista, d_origen, pista_origen, desplazamiento=0, cancion=""):
    """Devuelve (d, avisos): d con la entrada, el fader (salida, valores y
    automatización) y los envíos de 'pista' reemplazados por los de
    'pista_origen' de la sesión d_origen. La automatización se corre
    'desplazamiento' muestras. Las rutas se buscan por clase y nombre en d."""
    b, bo = arbol(d), arbol(d_origen)
    sb, sb_o = _canal(d, b, pista), _canal(d_origen, bo, pista_origen)
    tabla, tabla_o = _Tabla(d, b), _Tabla(d_origen, bo)
    avisos = []
    reemplazos = []

    # buses estéreo que usa la pista de origen y no existen en d: se crean
    nuevos_buses = []
    for ident, nom in _referencias(d_origen, sb_o):
        if tabla_o.clase(ident) != 2 or tabla.buscar(2, nom) is not None:
            continue
        par = _par_estereo(d_origen, bo, nom)
        if par and par not in nuevos_buses and tabla.buscar(2, par) is None:
            nuevos_buses.append(par)
    if nuevos_buses:
        for n in nuevos_buses:
            d = crear_bus(d, n)
            avisos.append(f"{pista}: se creó el bus «{n}»")
        b = arbol(d)
        sb, tabla = _canal(d, b, pista), _Tabla(d, b)

    # entrada
    ei, ef = _entrada(d, sb)
    oi, of = _entrada(d_origen, sb_o)
    campo = bytearray(d_origen[oi:of])
    nombre, p = _cadena(d_origen, oi)
    if d_origen[p + 4:p + 8] == b"\x2a\x00\x00\x00":
        ident = bytes(d_origen[p + 8:p + 16])
        nuevo = tabla.buscar(tabla_o.clase(ident), nombre)
        if nuevo is None:
            avisos.append(f"{pista}: la entrada «{nombre}» no existe en el show (queda la de la plantilla)")
            campo = None
        else:
            campo[p - oi + 8:p - oi + 16] = nuevo
    if campo is not None and bytes(campo) != bytes(d[ei:ef]):
        reemplazos.append((ei, ef, bytes(campo)))
        # tras el 0x102D del canal: u8 (1 = entrada de dos canales), u16 número
        # global de la ruta de entrada (FFFF = ninguna) y u8 1
        cab = next(h for h in sb.hijos if h.tipo_contenido == 0x102D)
        cab_o = next(h for h in sb_o.hijos if h.tipo_contenido == 0x102D)
        ancho, num = d_origen[cab_o.fin], struct.unpack_from("<H", d_origen, cab_o.fin + 1)[0]
        nid = bytes(campo[p - oi + 8:p - oi + 16]) if campo[p - oi + 4:p - oi + 8] == b"\x2a\x00\x00\x00" else None
        if nid is not None and num != 0xFFFF:
            num = _numero_ruta(d, b, nid)
            if num is None:
                raise ErrorPista(f"{pista}: no se reconoce la numeración de la entrada «{nombre}»")
        reemplazos.append((cab.fin, cab.fin + 3, bytes([ancho]) + struct.pack("<H", num)))

    # faders: el del canal y los envíos
    fs_o, ti, tf, _ = _faders(d_origen, sb_o)
    _, di, df, _ = _faders(d, sb)
    tramo = bytearray(d_origen[ti:tf])
    quitar = []                                    # envíos sin destino en el show
    salida_plantilla = False                       # la salida no existe en el show
    for j, f in enumerate(fs_o):
        e = next((h for h in f.hijos if h.tipo_contenido == 0x260E), None)
        if e is None:
            raise ErrorPista(f"{pista_origen}: fader sin destino (0x260E)")
        _, ident, nom = _destino_260e(d_origen, e)
        if ident is not None:
            nuevo = tabla.buscar(tabla_o.clase(ident), nom)
            if nuevo is not None:
                tramo[e.inicio + 14 - ti:e.inicio + 22 - ti] = nuevo
            elif j == 0:
                salida_plantilla = True
                avisos.append(f"{pista}: la salida «{nom}» no existe en el show (queda la de la plantilla)")
            else:
                quitar.append(j)
                avisos.append(f"{pista}: el envío a «{nom}» no existe en el show (no se copia)")
    if quitar:
        # se rearma el tramo sin esos envíos: u16 cantidad y, por envío, u16 lugar y fader
        n = struct.unpack_from("<H", d_origen, fs_o[0].fin)[0]
        if n != len(fs_o) - 1:
            raise ErrorPista(f"{pista_origen}: lista de envíos no reconocida")
        quedan = [j for j in range(1, len(fs_o)) if j not in quitar]
        partes = [bytes(tramo[:fs_o[0].fin - ti]), struct.pack("<H", len(quedan))]
        partes += [bytes(tramo[fs_o[j].pos - 2 - ti:fs_o[j].fin - ti]) for j in quedan]
        partes.append(bytes(tramo[fs_o[-1].fin - ti:]))
        tramo = bytearray(b"".join(partes))
    if salida_plantilla:
        e_d = next(h for h in _faders(d, sb)[0][0].hijos if h.tipo_contenido == 0x260E)
        e_o = next(h for h in fs_o[0].hijos if h.tipo_contenido == 0x260E)
        viejo = bytes(d[e_d.pos:e_d.fin])
        tramo = tramo[:e_o.pos - ti] + viejo + tramo[e_o.fin - ti:]
        delta = len(viejo) - (e_o.fin - e_o.pos)          # cambia el tamaño del 0x260D del canal
        struct.pack_into("<I", tramo, 3, struct.unpack_from("<I", tramo, 3)[0] + delta)
    reemplazos.append((di, df, bytes(tramo), sb.pos))
    d = reconstruir(d, reemplazos)
    return (_correr(d, pista, desplazamiento) if desplazamiento else d), avisos


def _numero_ruta(d, bloques, ident):
    """Número global de una ruta: el de su entrada en 0x1022 para las entradas
    (clase 00), y para salidas y buses el de su entrada en 0x2603 más la
    diferencia que usan las mitades (b) de esa lista. None si no se reconoce."""
    L1, L2 = _primero(bloques, 0x1022), _primero(bloques, 0x2603)
    e1 = [h for h in (L1.hijos if L1 else []) if h.tipo_contenido == 0x1021]
    e2 = [h for h in (L2.hijos if L2 else []) if h.tipo_contenido == 0x2602]
    i = next((k for k, h in enumerate(e1) if _id_de(d, h) == ident), None)
    if i is not None and d[e1[i].inicio + 2] == 0:
        return i
    j = next((k for k, h in enumerate(e2) if _id_de(d, h) == ident), None)
    difs = {_campos(d, e2[k + 1])[4] - k for k, h in enumerate(e2[:-1])
            if d[h.inicio + 3] == 1 and d[e2[k + 1].inicio + 3] == 0}
    if j is None or len(difs) != 1:
        return None
    return difs.pop() + j


def _referencias(d, sb):
    """[(identificador, nombre)] de las rutas que usa un canal: su entrada y los
    destinos de sus faders (salida y envíos)."""
    out = []
    oi, _ = _entrada(d, sb)
    nombre, p = _cadena(d, oi)
    if d[p + 4:p + 8] == b"\x2a\x00\x00\x00":
        out.append((bytes(d[p + 8:p + 16]), nombre))
    for f in _faders(d, sb)[0]:
        e = next((h for h in f.hijos if h.tipo_contenido == 0x260E), None)
        if e is not None:
            _, ident, nom = _destino_260e(d, e)
            if ident is not None:
                out.append((ident, nom))
    return out


def _par_estereo(d, bloques, nombre):
    """Nombre del bus estéreo (par de 0x1022) al que pertenece la ruta
    'nombre' (él mismo o su mitad .L/.R), o None si es mono o no se reconoce."""
    L = _primero(bloques, 0x1022)
    e = {(_campos(d, h)[0], d[h.inicio + 3]): _campos(d, h)[2]
         for h in (L.hijos if L else []) if h.tipo_contenido == 0x1021 and d[h.inicio + 2] == 2}
    for n in (nombre, nombre.rsplit(".", 1)[0]):
        if e.get((n, 1)) == 2 and (n + ".L", 0) in e and (n + ".R", 0) in e:
            return n
    return None


def _corrida(puntos, desde):
    """Puntos de una lista corridos 'desde' muestras. La lista empieza siempre en
    la muestra 0 (Pro Tools descarta una que no, y deja solo su primer valor):
    se agrega el primer valor en 0; una lista de un solo punto queda igual."""
    if len(puntos) <= 1:
        return list(puntos)
    return [(0, puntos[0][1])] + [(m + desde, v) for m, v in puntos]


def _correr(d, pista, desde):
    """d con la automatización del canal de 'pista' corrida 'desde' muestras."""
    b = arbol(d)
    sb = _canal(d, b, pista)
    reemplazos = []
    for f in _faders(d, sb)[0]:
        for a in buscar([f], 0x260A):
            _, pts = _puntos(d, a)
            nuevos = _corrida(pts, desde)
            if nuevos != pts:
                reemplazos.append((a.inicio + 2, a.fin, _contenido_260a(d, a, nuevos)[2:]))
    return reconstruir(d, reemplazos) if reemplazos else d


def juntar_automatizacion(d, pista, d_origen, pista_origen, desde, cancion=""):
    """Devuelve (d, avisos): en cada lista de automatización del fader del
    canal de 'pista' (y de los envíos al mismo destino) quedan los puntos
    anteriores a la muestra 'desde' y, desde ahí, los de 'pista_origen' de
    d_origen corridos 'desde' muestras. Si ninguna de las dos listas cambia de
    valor, no se toca."""
    b, bo = arbol(d), arbol(d_origen)
    sb, sb_o = _canal(d, b, pista), _canal(d_origen, bo, pista_origen)
    fs, _, _, _ = _faders(d, sb)
    fs_o, _, _, _ = _faders(d_origen, sb_o)
    avisos, reemplazos = [], []

    def destino(dd, f):
        e = next(h for h in f.hijos if h.tipo_contenido == 0x260E)
        c, _, n = _destino_260e(dd, e)
        return c, n

    pares = [(fs[0], fs_o[0])]
    for f in fs_o[1:]:
        par = next((g for g in fs[1:] if destino(d, g) == destino(d_origen, f)), None)
        if par is None:
            avisos.append(f"{pista}: el envío a «{destino(d_origen, f)[1]}» de {cancion or pista_origen} "
                          f"no está en la pista del show (no se copia)")
        else:
            pares.append((par, f))
    for f, f_o in pares:
        la, lo = buscar([f], 0x260A), buscar([f_o], 0x260A)
        if len(la) != len(lo):
            avisos.append(f"{pista}: la automatización de {cancion or pista_origen} no corresponde "
                          f"(distinta cantidad de listas): no se copia")
            continue
        for a, o in zip(la, lo):
            w, pa = _puntos(d, a)
            w_o, po = _puntos(d_origen, o)
            if w != w_o:
                continue
            nuevos = [x for x in pa if x[0] < desde] + [(m + desde, v) for m, v in po]
            if not nuevos or nuevos[0][0] != 0:            # la lista empieza en la muestra 0
                nuevos = [(0, (pa or po)[0][1])] + nuevos
            if len({v for _, v in nuevos}) <= 1 and len(pa) == 1:
                continue                            # el mismo valor fijo en las dos
            if nuevos != pa:
                reemplazos.append((a.inicio + 2, a.fin, _contenido_260a(d, a, nuevos)[2:]))
    return (reconstruir(d, reemplazos) if reemplazos else d), avisos


def _canal_bloque(d, bloques, nombre):
    entradas = _lista_2519(d, _primero(bloques, 0x2519))[0]
    i = next((k for k, e in enumerate(entradas) if e[3] == nombre), None)
    if i is None:
        raise ErrorPista(f"no existe la pista {nombre!r}")
    raiz = _primero(bloques, 0x2624)
    if raiz is None or len(raiz.hijos) != len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")
    return raiz.hijos[i]


def copiar_midi(d, pista, d_origen, pista_origen, desplazamiento=0, cancion=""):
    """Devuelve (d, avisos): d con el canal MIDI (0x2611: valores 0x2036 y
    automatización) de 'pista' reemplazado por el de 'pista_origen' de
    d_origen, y la salida MIDI de la pista de origen.

    La salida MIDI es el u32 en +14 del 0x2036: número (desde 1; 0 = ninguna)
    de una entrada 0x2005 de la lista 0x2004 (u32 cantidad), con el puerto y el
    canal. Cada pista tiene la suya (si dos la comparten, Pro Tools se cae al
    cerrar la sesión): la entrada de 'pista' pasa a ser una copia de la de
    origen; si el origen no tiene salida, se quita y bajan los números
    mayores de los demás canales."""
    b, bo = arbol(d), arbol(d_origen)
    c, c_o = _canal_bloque(d, b, pista), _canal_bloque(d_origen, bo, pista_origen)
    m = next((h for h in c.hijos if h.tipo_contenido == 0x2611), None)
    m_o = next((h for h in c_o.hijos if h.tipo_contenido == 0x2611), None)
    if m is None or m_o is None:
        raise ErrorPista(f"{pista}: canal MIDI sin 0x2611")
    p = next((h for h in m.hijos if h.tipo_contenido == 0x2036), None)
    p_o = next((h for h in m_o.hijos if h.tipo_contenido == 0x2036), None)
    if p is None or p_o is None:
        raise ErrorPista(f"{pista}: canal MIDI sin 0x2036")
    nuevo = bytearray(d_origen[m_o.pos:m_o.fin])
    avisos, reemplazos = [], []
    k_o = struct.unpack_from("<I", d_origen, p_o.inicio + 14)[0]
    k = struct.unpack_from("<I", d, p.inicio + 14)[0]
    L = _primero(b, 0x2004)
    ld = [h for h in (L.hijos if L else []) if h.tipo_contenido == 0x2005]
    lo = [h for h in (_primero(bo, 0x2004) or _Vacio).hijos if h.tipo_contenido == 0x2005]
    if k_o > len(lo) or k > len(ld):
        raise ErrorPista(f"{pista}: salida MIDI no reconocida")
    if k_o and k:                                   # la entrada propia pasa a ser la de origen
        reemplazos.append((ld[k - 1].pos, ld[k - 1].fin, bytes(d_origen[lo[k_o - 1].pos:lo[k_o - 1].fin]), L.pos))
        struct.pack_into("<I", nuevo, p_o.inicio + 14 - m_o.pos, k)
    elif k_o:                                       # la pista no tenía: se agrega al final
        cant = struct.unpack_from("<I", d, L.inicio + 2)[0]
        if cant != len(ld):
            raise ErrorPista("lista de salidas MIDI (0x2004) no reconocida")
        punto = ld[-1].fin if ld else L.inicio + 6
        reemplazos.append((L.inicio + 2, L.inicio + 6, struct.pack("<I", cant + 1)))
        reemplazos.append((punto, punto, bytes(d_origen[lo[k_o - 1].pos:lo[k_o - 1].fin]), L.pos))
        struct.pack_into("<I", nuevo, p_o.inicio + 14 - m_o.pos, len(ld) + 1)
    elif k:                                         # el origen no tiene salida: se quita la propia
        refs = [x for ch in _primero(b, 0x2624).hijos if ch.tipo_contenido in (0x2620, 0x2621)
                for x in buscar([ch], 0x2036)]          # solo los de canales MIDI
        if any(struct.unpack_from("<I", d, x.inicio + 14)[0] == k for x in refs if x.pos != p.pos):
            raise ErrorPista(f"{pista}: su salida MIDI la usa otro canal")
        cant = struct.unpack_from("<I", d, L.inicio + 2)[0]
        reemplazos.append((L.inicio + 2, L.inicio + 6, struct.pack("<I", cant - 1)))
        reemplazos.append((ld[k - 1].pos, ld[k - 1].fin, b"", L.pos))
        for x in refs:
            v = struct.unpack_from("<I", d, x.inicio + 14)[0]
            if x.pos != p.pos and v > k:
                reemplazos.append((x.inicio + 14, x.inicio + 18, struct.pack("<I", v - 1)))
    reemplazos.append((m.pos, m.fin, bytes(nuevo), c.pos))
    d = reconstruir(d, reemplazos)
    if desplazamiento:
        b = arbol(d)
        m = next(h for h in _canal_bloque(d, b, pista).hijos if h.tipo_contenido == 0x2611)
        r = []
        for a in buscar([m], 0x260A):
            _, pts = _puntos(d, a)
            nuevos = _corrida(pts, desplazamiento)
            if nuevos != pts:
                r.append((a.inicio + 2, a.fin, _contenido_260a(d, a, nuevos)[2:]))
        if r:
            d = reconstruir(d, r)
    return d, avisos


class _Vacio:
    hijos = []


def _campos(d, h):
    """(nombre, posición de u32 canales, canales, a, b) de una entrada 0x1021/0x2602."""
    nombre, p = _cadena(d, h.inicio + 4)
    nch = struct.unpack_from("<I", d, p)[0]
    a, b = struct.unpack_from("<HH", d, p + 4)
    return nombre, p, nch, a, b


def _id_de(d, h):
    q = d.find(b"\x2a\x00\x00\x00", _campos(d, h)[1], h.fin)
    return bytes(d[q + 4:q + 12])


def _entrada_nueva(d, h, nombre, a, b, ids):
    """Bytes de una copia de la entrada h (bloque entero) con otro nombre (y el
    nombre corto del final, si lo tiene), canales a y b, y los identificadores
    cambiados según 'ids' {viejo: nuevo}."""
    viejo, p, _, _, _ = _campos(d, h)
    x = bytearray(d[h.pos:h.fin])
    off = h.pos
    cola = bytearray(x[p - off:])
    struct.pack_into("<HH", cola, 4, a, b)
    for v, n in ids.items():
        cola = cola.replace(b"\x2a\x00\x00\x00" + v, b"\x2a\x00\x00\x00" + n)
    vb, nb = viejo.encode("utf-8"), nombre.encode("utf-8")
    k = cola.rfind(struct.pack("<I", len(vb)) + vb)
    if k > 0:                                     # nombre corto al final (0x1021)
        cola = cola[:k] + struct.pack("<I", len(nb)) + nb + cola[k + 4 + len(vb):]
    cuerpo = bytes(x[:h.inicio + 4 - off]) + struct.pack("<I", len(nb)) + nb + bytes(cola)
    cuerpo = bytearray(cuerpo)
    struct.pack_into("<I", cuerpo, 3, struct.unpack_from("<I", cuerpo, 3)[0] + len(cuerpo) - len(x))
    return bytes(cuerpo)


def crear_bus(d, nombre):
    """Devuelve d con un bus estéreo nuevo 'nombre' (con sus mitades
    'nombre.L' y 'nombre.R'), como lo crea Pro Tools 12 (v5_plugin → v6_envio):

    * tres entradas en 0x1022 (par y mitades) tras el último bus, y en las
      mitades de los inserts (que van después) el u16 b, que es el número de
      su par en la lista, sube en 3;
    * tres entradas al final de 0x2603;
    * a y b del par: los canales (siguientes al mayor usado en 0x2603); b de
      las mitades: el número del par en 0x2603 más la diferencia que tienen
      las demás mitades de buses y salidas;
    * identificadores nuevos; el índice 0x0002 no cambia.
    La plantilla es el último bus estéreo de la sesión."""
    from .pistas_nuevas import _id_nuevo
    bl = arbol(d)
    L1, L2 = _primero(bl, 0x1022), _primero(bl, 0x2603)
    if L1 is None or L2 is None:
        raise ErrorPista("la sesión no tiene las listas de rutas (0x1022/0x2603)")
    e1 = [h for h in L1.hijos if h.tipo_contenido == 0x1021]
    e2 = [h for h in L2.hijos if h.tipo_contenido == 0x2602]
    if any(_campos(d, h)[0] == nombre for h in e1 + e2):
        raise ErrorPista(f"ya existe la ruta {nombre!r}")
    for L, e in ((L1, e1), (L2, e2)):
        if struct.unpack_from("<I", d, L.inicio + 2)[0] != len(e):
            raise ErrorPista(f"lista de rutas 0x{L.tipo_contenido:04x} no reconocida")
    # plantilla: último par de bus (clase 02, 2 canales) seguido de sus dos mitades
    k = max((i for i, h in enumerate(e1[:-2]) if d[h.inicio + 2] == 2 and d[h.inicio + 3] == 1
             and _campos(d, h)[2] == 2 and d[e1[i + 1].inicio + 2] == 2 and d[e1[i + 2].inicio + 2] == 2),
            default=None)
    if k is None:
        raise ErrorPista("la sesión no tiene un bus estéreo que sirva de plantilla")
    t1 = e1[k:k + 3]
    ids_t = [_id_de(d, h) for h in t1]
    k2 = next((i for i, h in enumerate(e2[:-2]) if _id_de(d, h) == ids_t[0]), None)
    if k2 is None:
        raise ErrorPista("el bus de plantilla no está en 0x2603")
    t2 = e2[k2:k2 + 3]
    # diferencia entre b de una mitad y el número de su par en 0x2603
    difs = {_campos(d, e2[i + 1])[4] - i for i, h in enumerate(e2[:-1])
            if d[h.inicio + 3] == 1 and d[e2[i + 1].inicio + 3] == 0}
    if len(difs) != 1:
        raise ErrorPista("numeración de la lista 0x2603 no reconocida")
    dif = difs.pop()
    a1 = max(_campos(d, h)[3] for h in e2) + 1
    b_mitad = dif + len(e2)
    par = _id_nuevo(d, ids_t[0])
    izq = _id_nuevo(d, ids_t[1], (par,))
    der = _id_nuevo(d, ids_t[2], (par, izq))
    ids = {ids_t[0]: par, ids_t[1]: izq, ids_t[2]: der}
    nombres = [nombre, nombre + ".L", nombre + ".R"]
    ab = [(a1, a1 + 1), (a1, b_mitad), (a1 + 1, b_mitad)]
    nuevos1 = b"".join(_entrada_nueva(d, h, n, a, b, ids) for h, n, (a, b) in zip(t1, nombres, ab))
    nuevos2 = b"".join(_entrada_nueva(d, h, n, a, b, ids) for h, n, (a, b) in zip(t2, nombres, ab))
    # tras el último bus de 0x1022
    ultimo = max(i for i, h in enumerate(e1) if d[h.inicio + 2] == 2)
    reemplazos = [(L1.inicio + 2, L1.inicio + 6, struct.pack("<I", len(e1) + 3)),
                  (e1[ultimo].fin, e1[ultimo].fin, nuevos1, L1.pos),
                  (L2.inicio + 2, L2.inicio + 6, struct.pack("<I", len(e2) + 3)),
                  (e2[-1].fin, e2[-1].fin, nuevos2, L2.pos)]
    for h in e1[ultimo + 1:]:
        if d[h.inicio + 3] == 0:                     # mitad: b es el número de su par
            _, p, _, _, b = _campos(d, h)
            if b > ultimo:
                reemplazos.append((p + 6, p + 8, struct.pack("<H", b + 3)))
    return reconstruir(d, reemplazos)

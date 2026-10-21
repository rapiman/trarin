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

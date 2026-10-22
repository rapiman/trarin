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
    if sb is None and canal.tipo_contenido == 0x2621:     # instrumento: el canal hace de 0x261B
        sb = canal
    if sb is None:
        raise ErrorPista(f"{nombre}: canal sin 0x261B (pista MIDI: ver copiar_midi)")
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


def copiar_ruteo(d, pista, d_origen, pista_origen, desplazamiento=0, cancion="", sufijo_buses="",
                 escala=1.0, hasta=None, renombrar=None, salida=None, envios=True):
    """Devuelve (d, avisos): d con la entrada, el fader (salida, valores y
    automatización) y los envíos de 'pista' reemplazados por los de
    'pista_origen' de la sesión d_origen. La automatización se corre
    'desplazamiento' muestras (ver _corrida para 'escala' y 'hasta'). Las rutas
    se buscan por clase y nombre en d.

    sufijo_buses: si no está vacío, cada bus de la pista de origen va a un bus
    propio con ese sufijo en el nombre («Aux 1» → «Aux 1 (Sombra)»), que se
    crea si no existe; si está vacío, al bus del mismo nombre.

    renombrar: {nombre de ruta en el origen: nombre en d} (p. ej. el bus de
    entrada de un auxiliar). salida: nombre de la ruta de d (bus o salida) a la
    que va el fader del canal, en vez de la del origen. envios=False: no se
    copian los envíos."""
    b, bo = arbol(d), arbol(d_origen)
    sb, sb_o = _canal(d, b, pista), _canal(d_origen, bo, pista_origen)
    tabla, tabla_o = _Tabla(d, b), _Tabla(d_origen, bo)
    avisos = []
    reemplazos = []

    def destino(ident, nom):
        """(clase, nombre) de la ruta en d para la ruta 'nom' de origen."""
        clase = tabla_o.clase(ident)
        if renombrar and nom in renombrar:
            return clase, renombrar[nom]
        if clase == 2 and sufijo_buses:
            par = _par_estereo(d_origen, bo, nom)
            if par:
                return clase, par + sufijo_buses + nom[len(par):]
            return clase, nom + sufijo_buses
        return clase, nom

    # buses estéreo que usa la pista de origen y no existen en d: se crean
    nuevos_buses = []
    for ident, nom in _referencias(d_origen, sb_o, solo_entrada=salida is not None and not envios):
        clase, nom_d = destino(ident, nom)
        if clase != 2 or tabla.buscar(2, nom_d) is not None:
            continue
        par = _par_estereo(d_origen, bo, nom)
        if par:
            par_d = destino(ident, par)[1]
            if par_d not in [n for n, _ in nuevos_buses] and tabla.buscar(2, par_d) is None:
                nuevos_buses.append((par_d, 2))
        elif _es_bus_mono(d_origen, bo, nom) and nom_d not in [n for n, _ in nuevos_buses]:
            nuevos_buses.append((nom_d, 1))
    if nuevos_buses:
        for n, canales in nuevos_buses:
            d = crear_bus(d, n, canales)
            avisos.append(f"{pista}: se creó el bus {'mono ' if canales == 1 else ''}«{n}»")
        b = arbol(d)
        sb, tabla = _canal(d, b, pista), _Tabla(d, b)

    # entrada (una pista de instrumento no tiene entrada de audio)
    try:
        ei, ef = _entrada(d, sb)
        oi, of = _entrada(d_origen, sb_o)
    except ErrorPista:
        if sb.tipo_contenido != 0x2621:
            raise
        ei = None
    if ei is not None:
        nombre, p = _cadena(d_origen, oi)
        cola = bytearray(d_origen[p:of])                  # u32 0, 2A000000, id y 7 bytes
        nombre_d, nid = nombre, None
        if cola[4:8] == b"\x2a\x00\x00\x00":
            ident = bytes(cola[8:16])
            clase, nombre_d = destino(ident, nombre)
            nid = tabla.buscar(clase, nombre_d)
            if nid is None:
                avisos.append(f"{pista}: la entrada «{nombre_d}» no existe en el show (queda la de la plantilla)")
            else:
                cola[8:16] = nid
        if cola[4:8] != b"\x2a\x00\x00\x00" or nid is not None:
            nb = nombre_d.encode("utf-8")
            campo = struct.pack("<I", len(nb)) + nb + bytes(cola)
            if campo != bytes(d[ei:ef]):
                reemplazos.append((ei, ef, campo))
                # tras el 0x102D del canal: u8 (1 = entrada de dos canales), u16
                # número global de la ruta de entrada (FFFF = ninguna) y u8 1
                cab = next(h for h in sb.hijos if h.tipo_contenido == 0x102D)
                cab_o = next(h for h in sb_o.hijos if h.tipo_contenido == 0x102D)
                ancho, num = d_origen[cab_o.fin], struct.unpack_from("<H", d_origen, cab_o.fin + 1)[0]
                if nid is not None and num != 0xFFFF:
                    num = _numero_ruta(d, b, nid)
                    if num is None:
                        raise ErrorPista(f"{pista}: no se reconoce la numeración de la entrada «{nombre_d}»")
                reemplazos.append((cab.fin, cab.fin + 3, bytes([ancho]) + struct.pack("<H", num)))

    # faders: el del canal, u16 cantidad de envíos y, por envío, u16 lugar y su fader
    fs_o, ti, tf, _ = _faders(d_origen, sb_o)
    fs_d, di, df, _ = _faders(d, sb)
    if (struct.unpack_from("<H", d_origen, fs_o[0].fin)[0] != len(fs_o) - 1
            or tf != fs_o[-1].fin + (2 if len(fs_o) == 1 else 0)):
        raise ErrorPista(f"{pista_origen}: lista de envíos no reconocida")
    piezas = []                                    # (lugar o None, bytes del fader)
    for j, f in enumerate(fs_o):
        e = next((h for h in f.hijos if h.tipo_contenido == 0x260E), None)
        if e is None:
            raise ErrorPista(f"{pista_origen}: fader sin destino (0x260E)")
        lugar = None if j == 0 else bytes(d_origen[f.pos - 2:f.pos])
        if j and not envios:
            continue
        if j == 0 and salida is not None:
            nid, clase_e = tabla.buscar(2, salida), 0x43
            if nid is None:
                nid, clase_e = next((i for c, n, i in tabla.rutas if n == salida and c >= 0x100), None), 0x13
            if nid is None:
                raise ErrorPista(f"{pista}: no existe la salida o bus «{salida}»")
            e_d = next(h for h in fs_d[0].hijos if h.tipo_contenido == 0x260E)
            if _destino_260e(d_origen, e)[1] is not None:
                nuevo_e = _e_con_destino(d_origen, e, nid, salida, clase_e)
            else:                                   # el origen no tiene salida: la de la plantilla
                nuevo_e = _e_con_destino(d, e_d, nid, salida, clase_e)
            piezas.append((lugar, _fader_con_destino(d_origen, f, e, None, None, nuevo_e)))
            continue
        _, ident, nom = _destino_260e(d_origen, e)
        if ident is None:
            piezas.append((lugar, bytes(d_origen[f.pos:f.fin])))
            continue
        clase, nom_d = destino(ident, nom)
        nid = tabla.buscar(clase, nom_d)
        if nid is not None:
            piezas.append((lugar, _fader_con_destino(d_origen, f, e, nid, nom_d)))
        elif j == 0:
            avisos.append(f"{pista}: la salida «{nom_d}» no existe en el show (queda la de la plantilla)")
            e_d = next(h for h in fs_d[0].hijos if h.tipo_contenido == 0x260E)
            _, id_d, nom_p = _destino_260e(d, e_d)
            piezas.append((lugar, _fader_con_destino(d_origen, f, e, id_d, nom_p, bytes(d[e_d.pos:e_d.fin]))))
        else:
            avisos.append(f"{pista}: el envío a «{nom_d}» no existe en el show (no se copia)")
    tramo = piezas[0][1] + struct.pack("<H", len(piezas) - 1) + b"".join(l + x for l, x in piezas[1:])
    reemplazos.append((di, df, tramo, sb.pos))
    d = reconstruir(d, reemplazos)
    cambia = desplazamiento or escala != 1 or hasta is not None
    return (_correr(d, pista, desplazamiento, escala, hasta) if cambia else d), avisos


def _e_con_destino(d, e, ident, nombre, clase=None):
    """Bytes del 0x260E e (de d) apuntando a la ruta 'ident' de nombre 'nombre'
    (y con otra clase: 0x13 salida, 0x43 bus, si se indica)."""
    x = bytearray(d[e.pos:e.fin])
    x[e.inicio + 14 - e.pos:e.inicio + 22 - e.pos] = ident
    _, _, viejo = _destino_260e(d, e)
    vb, nb = viejo.encode("utf-8"), nombre.encode("utf-8")
    k = len(x) - 1 - len(vb) - 4                      # u32 largo, nombre y 01 al final
    x = x[:k] + struct.pack("<I", len(nb)) + nb + x[k + 4 + len(vb):]
    struct.pack_into("<I", x, 3, struct.unpack_from("<I", x, 3)[0] + len(nb) - len(vb))
    if clase is not None:
        struct.pack_into("<H", x, e.inicio + 2 - e.pos, clase)
    return bytes(x)


def _fader_con_destino(d, f, e, ident, nombre, entero=None):
    """Bytes del fader f (bloque 0x260D de d) con su 0x260E (e) apuntando a la
    ruta 'ident' de nombre 'nombre', o reemplazado entero por 'entero'."""
    if entero is None:
        entero = _e_con_destino(d, e, ident, nombre)
    delta = len(entero) - (e.fin - e.pos)
    y = bytearray(d[f.pos:e.pos]) + entero + bytearray(d[e.fin:f.fin])
    struct.pack_into("<I", y, 3, struct.unpack_from("<I", y, 3)[0] + delta)
    return bytes(y)


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


def _referencias(d, sb, solo_entrada=False):
    """[(identificador, nombre)] de las rutas que usa un canal: su entrada y los
    destinos de sus faders (salida y envíos)."""
    out = []
    try:
        oi, _ = _entrada(d, sb)
    except ErrorPista:
        if sb.tipo_contenido != 0x2621:
            raise
        oi = None
    if oi is not None:
        nombre, p = _cadena(d, oi)
        if d[p + 4:p + 8] == b"\x2a\x00\x00\x00":
            out.append((bytes(d[p + 8:p + 16]), nombre))
    if solo_entrada:
        return out
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


def _es_bus_mono(d, bloques, nombre):
    """True si 'nombre' es un bus mono propio (no la mitad de un par) en 0x1022."""
    L = _primero(bloques, 0x1022)
    for h in (L.hijos if L else []):
        if h.tipo_contenido == 0x1021 and d[h.inicio + 2] == 2 and d[h.inicio + 3] == 0:
            n, _, nch, _, b = _campos(d, h)
            if n == nombre:
                return nch == 1 and b == 0xFFFF
    return False


def _transformar(puntos, escala=1.0, hasta=None):
    """Puntos (en muestras de origen) hasta la muestra 'hasta' (excluida; ahí se
    agrega un punto con el último valor) y con las muestras multiplicadas por
    'escala' (cambio de frecuencia de muestreo)."""
    if hasta is not None:
        antes = [x for x in puntos if x[0] < hasta]
        if len(antes) < len(puntos) and antes:
            antes.append((hasta, antes[-1][1]))
        puntos = antes or puntos[:1]
    if escala != 1:
        puntos = [(int(round(m * escala)), v) for m, v in puntos]
    return puntos


def _corrida(puntos, desde, escala=1.0, hasta=None):
    """Puntos de una lista corridos 'desde' muestras (antes se aplican 'hasta' y
    'escala', ver _transformar). La lista empieza siempre en la muestra 0 (Pro
    Tools descarta una que no, y deja solo su primer valor): se agrega el primer
    valor en 0; una lista de un solo punto queda igual."""
    if len(puntos) <= 1:
        return list(puntos)
    puntos = _transformar(puntos, escala, hasta)
    if len(puntos) <= 1:
        return list(puntos)
    return [(0, puntos[0][1])] + [(m + desde, v) for m, v in puntos]


def _correr(d, pista, desde, escala=1.0, hasta=None):
    """d con la automatización del canal de 'pista' corrida 'desde' muestras."""
    b = arbol(d)
    sb = _canal(d, b, pista)
    reemplazos = []
    for f in _faders(d, sb)[0]:
        for a in buscar([f], 0x260A):
            _, pts = _puntos(d, a)
            nuevos = _corrida(pts, desde, escala, hasta)
            if nuevos != pts:
                reemplazos.append((a.inicio + 2, a.fin, _contenido_260a(d, a, nuevos)[2:]))
    return reconstruir(d, reemplazos) if reemplazos else d


def juntar_automatizacion(d, pista, d_origen, pista_origen, desde, cancion="", escala=1.0, hasta=None):
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
            nuevos = [x for x in pa if x[0] < desde] + [(m + desde, v) for m, v in _transformar(po, escala, hasta)]
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


def copiar_midi(d, pista, d_origen, pista_origen, desplazamiento=0, cancion="", escala=1.0, hasta=None):
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
    if desplazamiento or escala != 1 or hasta is not None:
        b = arbol(d)
        m = next(h for h in _canal_bloque(d, b, pista).hijos if h.tipo_contenido == 0x2611)
        r = []
        for a in buscar([m], 0x260A):
            _, pts = _puntos(d, a)
            nuevos = _corrida(pts, desplazamiento, escala, hasta)
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


def _entrada_mono(d, h, nombre, a, ident_viejo, ident):
    """Entrada de bus mono a partir de la del par de un bus estéreo (h): una
    sola entrada con 1 canal; tras el u32 canales va u16 a y FFFF (el par lleva
    a, b y FFFF), bandera 0 y, en 0x1021, el nombre corto termina en 00."""
    viejo, p, _, _, _ = _campos(d, h)
    x = bytearray(d[h.pos:h.fin])
    off = h.pos
    cola = bytearray(x[p - off:])
    if bytes(cola[8:10]) != b"\xff\xff" or struct.unpack_from("<I", cola, 0)[0] != 2:
        raise ErrorPista("el bus de plantilla no es un par estéreo")
    cola = bytearray(struct.pack("<IHH", 1, a, 0xFFFF)) + cola[10:]
    cola = cola.replace(b"\x2a\x00\x00\x00" + ident_viejo, b"\x2a\x00\x00\x00" + ident)
    vb, nb = viejo.encode("utf-8"), nombre.encode("utf-8")
    k = cola.rfind(struct.pack("<I", len(vb)) + vb)
    if k > 0:                                     # nombre corto al final (0x1021): termina en 00 00
        cola = cola[:k] + struct.pack("<I", len(nb)) + nb + b"\x00" + cola[k + 4 + len(vb) + 1:]
    cuerpo = bytearray(x[:h.inicio + 4 - off]) + struct.pack("<I", len(nb)) + nb + cola
    cuerpo[h.inicio + 3 - off] = 0                  # sin mitades
    struct.pack_into("<I", cuerpo, 3, struct.unpack_from("<I", cuerpo, 3)[0] + len(cuerpo) - len(x))
    return bytes(cuerpo)


def crear_bus(d, nombre, canales=2):
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
    if canales == 1:
        # bus mono (Pro Tools 12, v13_antes_mono → v14_bus_mono): una entrada
        # en cada lista y, en las mitades de los inserts, b sube en 1
        nuevos1 = _entrada_mono(d, t1[0], nombre, a1, ids_t[0], par)
        nuevos2 = _entrada_mono(d, t2[0], nombre, a1, ids_t[0], par)
        n_nuevas = 1
    else:
        izq = _id_nuevo(d, ids_t[1], (par,))
        der = _id_nuevo(d, ids_t[2], (par, izq))
        ids = {ids_t[0]: par, ids_t[1]: izq, ids_t[2]: der}
        nombres = [nombre, nombre + ".L", nombre + ".R"]
        ab = [(a1, a1 + 1), (a1, b_mitad), (a1 + 1, b_mitad)]
        nuevos1 = b"".join(_entrada_nueva(d, h, n, a, b, ids) for h, n, (a, b) in zip(t1, nombres, ab))
        nuevos2 = b"".join(_entrada_nueva(d, h, n, a, b, ids) for h, n, (a, b) in zip(t2, nombres, ab))
        n_nuevas = 3
    # tras el último bus de 0x1022
    ultimo = max(i for i, h in enumerate(e1) if d[h.inicio + 2] == 2)
    reemplazos = [(L1.inicio + 2, L1.inicio + 6, struct.pack("<I", len(e1) + n_nuevas)),
                  (e1[ultimo].fin, e1[ultimo].fin, nuevos1, L1.pos),
                  (L2.inicio + 2, L2.inicio + 6, struct.pack("<I", len(e2) + n_nuevas)),
                  (e2[-1].fin, e2[-1].fin, nuevos2, L2.pos)]
    for h in e1[ultimo + 1:]:
        if d[h.inicio + 3] == 0:                     # mitad: b es el número de su par
            _, p, _, _, b = _campos(d, h)
            if b > ultimo and b != 0xFFFF:
                reemplazos.append((p + 6, p + 8, struct.pack("<H", b + n_nuevas)))
    return reconstruir(d, reemplazos)


def _pos_propia(b, k):
    """Posición absoluta del byte k de los bytes propios de b (sin sus hijos)."""
    p = b.inicio
    for h in b.hijos + [None]:
        tramo = (h.pos if h is not None else b.fin) - p
        if k < tramo:
            return p + k
        k -= tramo
        if h is None:
            break
        p = h.fin
    raise ErrorPista("bloque más corto de lo esperado")


def copiar_color(d, pista, d_origen, pista_origen):
    """d con el color de 'pista' igual al de 'pista_origen' de d_origen: i16
    en el byte 25 de los bytes propios del 0x2015 del canal (−1 = sin color)."""
    b, bo = arbol(d), arbol(d_origen)
    v = buscar([_canal_bloque(d, b, pista)], 0x2015)
    vo = buscar([_canal_bloque(d_origen, bo, pista_origen)], 0x2015)
    if not v or not vo:
        raise ErrorPista(f"{pista}: canal sin 0x2015")
    p, po = _pos_propia(v[0], 25), _pos_propia(vo[0], 25)
    if d[p:p + 2] == d_origen[po:po + 2]:
        return d
    return reconstruir(d, [(p, p + 2, bytes(d_origen[po:po + 2]))])


def _byte_solo(d, bloques, pista):
    """Posición del byte de solo de una pista: el que sigue al 0x2619 (nombre)
    del 0x102D de su canal (1 = en solo)."""
    cab = buscar([_canal_bloque(d, bloques, pista)], 0x102D)
    if not cab or not cab[0].hijos or cab[0].hijos[0].tipo_contenido != 0x2619:
        raise ErrorPista(f"{pista}: canal sin nombre (0x102D)")
    return cab[0].hijos[0].fin


def solo(d, pista):
    return d[_byte_solo(d, arbol(d), pista)] == 1


def poner_solo(d, pista, valor):
    """d con la pista en solo (valor True) o no."""
    p = _byte_solo(d, arbol(d), pista)
    nuevo = 1 if valor else 0
    return d if d[p] == nuevo else reconstruir(d, [(p, p + 1, bytes([nuevo]))])


# Listas de automatización del fader del canal, como las lee mezcla._automatizacion:
# (nombre, lugar entre los 0x260A directos o los de 0x260C, escala del valor)
_LISTAS_AUTO = {"volumen_db": ("directa", 0, 10), "mute": ("directa", 1, 1),
                "desconocido": ("directa", 2, 1), "paneo_izq": ("paneo", 0, 1), "paneo_der": ("paneo", 1, 1)}


def escribir_automatizacion(d, pista, auto):
    """d con la automatización del fader de 'pista' reemplazada por 'auto'
    ({"volumen_db"|"mute"|"paneo_izq"|"paneo_der": [{"muestra", "valor"}]},
    como en el modelo común). Una lista que falta en 'auto' queda fija en su
    primer valor. El volumen va en dB (décimas en el archivo), el mute 0/1 y
    el paneo de −100 a 100. Cada lista empieza en la muestra 0: si el primer
    punto va después, se agrega su valor en 0."""
    desconocidas = set(auto) - set(_LISTAS_AUTO)
    if desconocidas:
        raise ErrorPista(f"{pista}: automatización no soportada: {sorted(desconocidas)}")
    b = arbol(d)
    f = _faders(d, _canal(d, b, pista))[0][0]
    listas = {"directa": [h for h in f.hijos if h.tipo_contenido == 0x260A],
              "paneo": [k for h in f.hijos if h.tipo_contenido == 0x260C
                        for k in h.hijos if k.tipo_contenido == 0x260A]}
    reemplazos = []
    for nombre, (cual, i, escala) in _LISTAS_AUTO.items():
        if i >= len(listas[cual]):
            if nombre in auto:
                raise ErrorPista(f"{pista}: el canal no tiene la lista de {nombre}")
            continue
        a = listas[cual][i]
        w, pts = _puntos(d, a)
        if w != 2:
            raise ErrorPista(f"{pista}: lista de {nombre} no reconocida")
        if nombre in auto:
            nuevos = []
            for x in sorted(auto[nombre], key=lambda x: x["muestra"]):
                v = round(float(x["valor"]) * escala)
                if nombre == "volumen_db":
                    v = max(v, -1440)
                if not -32768 <= v <= 32767 or int(x["muestra"]) < 0:
                    raise ErrorPista(f"{pista}: punto de {nombre} fuera de rango: {x}")
                nuevos.append((int(x["muestra"]), struct.pack("<h", v)))
            if not nuevos:
                nuevos = pts[:1]
            if nuevos[0][0] != 0:
                nuevos.insert(0, (0, nuevos[0][1]))
        else:
            nuevos = pts[:1]
        if nuevos != pts:
            reemplazos.append((a.inicio + 2, a.fin, _contenido_260a(d, a, nuevos)[2:]))
    return reconstruir(d, reemplazos) if reemplazos else d

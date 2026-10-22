"""Quitar pistas de audio, MIDI o auxiliares de una sesión .ptx.

Verificado borrando una pista de audio con clips y fundidos en Pro Tools 12 y
comparando con la misma sesión guardada de nuevo sin cambios. Es lo inverso de
agregar una pista (ver pistas_nuevas.py):

* lista de pistas 0x2519: la cantidad (antes de la primera entrada y entre las
  mitades de 0x251A), la entrada y sus dos 0x251A; las pistas siguientes bajan
  su número (u16 de la entrada y u32 del 0x251A);
* las playlists del canal (u32 tras su 0x261B, índices en 0x1015) con sus
  entradas 0x1052; las demás playlists y canales corren sus índices;
* el canal 0x261C de 0x2624; los siguientes bajan su número de canal de audio,
  y los 0x2015 su número correlativo;
* los grupos 0x202A (la pista sale de la lista y las siguientes bajan), 0x202C
  y la tira 0x2589 de la ventana;
* el índice 0x0002: los grupos de los bloques quitados y los números de las
  entradas por pista y por canal.

Una pista MIDI no tiene playlists 0x1014/0x1052: se quita su 0x1057 de 0x1058,
su salida 0x2005 de 0x2004 si ningún otro canal la usa (los u32 en +14 de los
0x2036 que apuntan más allá bajan), y los canales 0x2620/0x2621 siguientes bajan
su número n; 0x202C no cambia.

Un auxiliar (0x261E) no tiene playlists; 0x202C no cambia (como al crearlo).

Las regiones y archivos de la pista quedan en la lista de clips, como al borrar
la pista en Pro Tools. Las definiciones de fundidos (0x2630) también quedan.
"""

import struct

from .bloques import arbol, buscar
from .indice import leer_indice, escribir_indice
from .pistas import _cadena
from .pistas_nuevas import ErrorPista, _lista_2519, _primero
from .reconstruccion import reconstruir


def quitar_pistas(d, nombres, de_a_una=False):
    """d: sesión descifrada. nombres: pistas de audio, MIDI o auxiliares a quitar. Devuelve la
    sesión descifrada sin esas pistas.

    Varias pistas se quitan en una sola pasada (los mismos bytes que quitándolas
    de a una, en el orden dado). de_a_una=True usa el camino antiguo."""
    nombres = list(nombres)
    if de_a_una or len(nombres) <= 1:
        for nombre in nombres:
            d = _quitar_una(d, nombre)
        return d
    return _quitar_varias(d, nombres)


def _menos(x, ordenados):
    """Cuántos de 'ordenados' son menores que x."""
    from bisect import bisect_left
    return bisect_left(ordenados, x)


def _secuencia(valores):
    """Los valores que se quitan, como quedan al momento de quitar cada uno si se
    quitan de a una en ese orden (cada quitado baja en 1 a los mayores)."""
    cur, seq = list(valores), []
    for i, r in enumerate(cur):
        seq.append(r)
        for j in range(i + 1, len(cur)):
            if cur[j] > r:
                cur[j] -= 1
    return seq


def _bajar(v, seq):
    """Valor final de v tras quitar los de 'seq' (ver _secuencia)."""
    for r in seq:
        if v > r:
            v -= 1
    return v


def _quitar_varias(d, nombres):
    bloques = arbol(d)
    L = _primero(bloques, 0x2519)
    entradas, fin_lista, pos_cantidad = _lista_2519(d, L)
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    mitad = len(hijos) // 2
    if len(hijos) != 2 * len(entradas):
        raise ErrorPista("lista de pistas no reconocida")
    raiz = _primero(bloques, 0x2624)
    if raiz is None or len(raiz.hijos) != len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")
    n = len(entradas)
    elegidas = []                       # índices en el orden pedido
    for nombre in nombres:              # con nombres repetidos, la siguiente con ese nombre
        i = next((k for k, e in enumerate(entradas) if e[3] == nombre and k not in elegidas), None)
        if i is None:
            raise ErrorPista(f"no existe la pista {nombre!r}")
        tipo = entradas[i][2]
        if tipo not in (0, 1, 2):
            raise ErrorPista(f"{nombre}: por ahora solo se pueden quitar pistas de audio, MIDI o auxiliares")
        if raiz.hijos[i].tipo_contenido != {0: 0x261C, 1: 0x2620, 2: 0x261E}[tipo]:
            raise ErrorPista(f"{nombre}: el canal no corresponde al tipo de pista")
        elegidas.append(i)
    if n - len(elegidas) < 1:
        raise ErrorPista("la sesión debe conservar al menos una pista")
    Q = sorted(elegidas)
    fuera = set(Q)
    k = len(Q)
    canales = [raiz.hijos[i] for i in Q]
    reemplazos = []
    quitados = []

    def quitar(b, contenedor):
        reemplazos.append((b.pos, b.fin, b"", contenedor.pos))
        quitados.append((b.pos, b.fin))

    # 1) lista de pistas 0x2519
    reemplazos.append((pos_cantidad, pos_cantidad + 4, struct.pack("<I", n - k)))
    numeros = _secuencia([entradas[i][6] for i in elegidas if entradas[i][6]])
    for j, e in enumerate(entradas):
        if j in fuera:
            reemplazos.append((e[0], e[1], b""))
        elif e[6] and _bajar(e[6], numeros) != e[6]:
            reemplazos.append((e[1] - 3, e[1] - 1, struct.pack("<H", _bajar(e[6], numeros))))
    m = hijos[mitad - 1].fin
    if struct.unpack_from("<I", d, m)[0] != n or hijos[mitad].pos != m + 4:
        raise ErrorPista("lista de pistas no reconocida (cantidad entre mitades)")
    reemplazos.append((m, m + 4, struct.pack("<I", n - k)))
    for j, h in enumerate(hijos):
        if j % mitad in fuera:
            quitar(h, L)
            continue
        v = struct.unpack_from("<I", d, h.fin - 22)[0]
        if _bajar(v, numeros) != v:
            b = bytearray(d[h.pos:h.fin])
            struct.pack_into("<I", b, len(b) - 22, _bajar(v, numeros))
            reemplazos.append((h.pos, h.fin, bytes(b), L.pos))

    audio = [raiz.hijos[i] for i in elegidas if entradas[i][2] == 0]
    midi = [(raiz.hijos[i], entradas[i][3]) for i in elegidas if entradas[i][2] == 1]
    if audio:
        _quitar_audio_varias(d, bloques, raiz, audio, quitar, reemplazos)
    if midi:
        _quitar_midi_varias(d, bloques, raiz, midi, quitar, reemplazos)
    for c in canales:
        quitar(c, raiz)
    reemplazos.append((raiz.inicio + 2, raiz.inicio + 6, struct.pack("<I", len(raiz.hijos) - k)))
    cortes = _secuencia([struct.unpack_from("<I", d, bs[0].fin - 4)[0]
                         for bs in (buscar([raiz.hijos[i]], 0x2015) for i in elegidas) if bs])
    if cortes:
        rangos = sorted((c.pos, c.fin) for c in canales)
        for b in buscar(bloques, 0x2015):
            if _dentro(b.pos, rangos):
                continue
            v = struct.unpack_from("<I", d, b.fin - 4)[0]
            if _bajar(v, cortes) != v:
                reemplazos.append((b.fin - 4, b.fin, struct.pack("<I", _bajar(v, cortes))))

    # 4) grupos
    for grp in buscar(bloques, 0x202A):
        nom, p = _cadena(d, grp.inicio + 2)
        cnt = struct.unpack_from("<I", d, p + 7)[0]
        lista = list(struct.unpack_from(f"<{cnt}H", d, p + 11))
        nueva = [x - _menos(x, Q) for x in lista if x not in fuera]
        if nueva != lista:
            if not nueva:
                ultima = next(entradas[i][3] for i in reversed(elegidas) if i in lista)
                raise ErrorPista(f"{ultima}: es la única pista del grupo {nom!r}")
            reemplazos.append((p + 7, p + 11 + 2 * cnt, struct.pack(f"<I{len(nueva)}H", len(nueva), *nueva)))
    for gc in buscar(bloques, 0x202C):
        if struct.unpack_from("<I", d, gc.inicio + 2)[0] != 1:
            continue
        a1, a2 = viejo = struct.unpack_from("<II", d, gc.inicio + 14)
        actual = n                      # como al quitarlas de a una, en el orden pedido
        for i in elegidas:
            if entradas[i][2] == 0 and (a1, a2) == (actual - 1, actual):
                a1, a2 = actual - 2, actual - 1
            actual -= 1
        if (a1, a2) != viejo:
            reemplazos.append((gc.inicio + 14, gc.inicio + 22, struct.pack("<II", a1, a2)))

    # 5) tiras de la ventana
    for v in buscar(bloques, 0x258A):
        tiras = [h for h in v.hijos if h.tipo_contenido == 0x2589]
        if not tiras:
            continue
        q = tiras[0].pos - 4
        if struct.unpack_from("<I", d, q)[0] != len(tiras):
            raise ErrorPista("tiras de la ventana no reconocidas")
        sale = 0
        for t in tiras:
            kt = struct.unpack_from("<H", d, t.inicio + 2)[0]
            if kt in fuera:
                quitar(t, v)
                sale += 1
            elif _menos(kt, Q):
                b = bytearray(d[t.pos:t.fin])
                struct.pack_into("<H", b, t.inicio + 2 - t.pos, kt - _menos(kt, Q))
                reemplazos.append((t.pos, t.fin, bytes(b), v.pos))
        if sale:
            reemplazos.append((q, q + 4, struct.pack("<I", len(tiras) - sale)))

    d = _indice_sin(d, bloques, quitados, [i + 1 for i in Q], [i + 1 for i in Q])
    return reconstruir(d, reemplazos)


def _dentro(p, rangos):
    """True si p cae en alguno de los rangos (ordenados, sin solaparse)."""
    from bisect import bisect_right
    j = bisect_right(rangos, (p, float("inf"))) - 1
    return j >= 0 and rangos[j][0] <= p < rangos[j][1]


def _quitar_audio_varias(d, bloques, raiz, quitar_canales, quitar, reemplazos):
    """Como _quitar_audio, para varios canales de audio a la vez."""
    lista_pl = _primero(bloques, 0x1015)
    mapa = _primero(bloques, 0x1054)
    pls = [h for h in lista_pl.hijos if h.tipo_contenido == 0x1014]
    e1052 = [h for h in mapa.hijos if h.tipo_contenido == 0x1052]
    refs = []
    for b in pls:
        _, j = _cadena(d, b.inicio + 2)
        kk = struct.unpack_from("<I", d, j + 1)[0]
        refs.append((j + 5, list(struct.unpack_from(f"<{kk}H", d, j + 5))))
    propias, n_audios = set(), []
    for canal in quitar_canales:
        sb = next(h for h in canal.hijos if h.tipo_contenido == 0x261B)
        n_audio, k_pl = struct.unpack_from("<IH", d, sb.fin)
        p_c = set(struct.unpack_from(f"<{k_pl}I", d, sb.fin + 6))
        if any(p >= len(pls) for p in p_c):
            raise ErrorPista("playlists del canal no reconocidas")
        propias |= p_c
        n_audios.append(n_audio)
    m_quitar = sorted({x for p in propias for x in refs[p][1]})
    if any(x >= len(e1052) for x in m_quitar):
        raise ErrorPista("entradas de playlist no reconocidas")
    for p in sorted(propias):
        quitar(pls[p], lista_pl)
    for x in m_quitar:
        quitar(e1052[x], mapa)
    for p, (q, lista) in enumerate(refs):
        if p in propias:
            continue
        nueva = [x - _menos(x, m_quitar) for x in lista]
        if nueva != lista:
            reemplazos.append((q, q + 2 * len(lista), struct.pack(f"<{len(lista)}H", *nueva)))
    n_pl = struct.unpack_from("<I", d, lista_pl.inicio + 2)[0]
    n_map = struct.unpack_from("<I", d, mapa.inicio + 2)[0]
    reemplazos.append((lista_pl.inicio + 2, lista_pl.inicio + 6, struct.pack("<I", n_pl - len(propias))))
    reemplazos.append((mapa.inicio + 2, mapa.inicio + 6, struct.pack("<I", n_map - len(m_quitar))))
    propias_o, n_audios = sorted(propias), _secuencia(n_audios)
    fuera = {c.pos for c in quitar_canales}
    for c in raiz.hijos:
        if c.pos in fuera or c.tipo_contenido != 0x261C:
            continue
        sbc = next(h for h in c.hijos if h.tipo_contenido == 0x261B)
        a, kk = struct.unpack_from("<IH", d, sbc.fin)
        lista = list(struct.unpack_from(f"<{kk}I", d, sbc.fin + 6))
        nueva = [x - _menos(x, propias_o) for x in lista]
        a2 = _bajar(a, n_audios)
        if (a2, nueva) != (a, lista):
            reemplazos.append((sbc.fin, sbc.fin + 6 + 4 * kk, struct.pack(f"<IH{kk}I", a2, kk, *nueva)))


def _quitar_midi_varias(d, bloques, raiz, quitar_canales, quitar, reemplazos):
    """Como _quitar_midi, para varios canales MIDI a la vez. quitar_canales: [(canal, nombre)]."""
    mapa = _primero(bloques, 0x1058)
    for canal, nombre in quitar_canales:
        pls = [h for h in (mapa.hijos if mapa else []) if h.tipo_contenido == 0x1057
               and _cadena(d, h.inicio + 2)[0] == nombre]
        if len(pls) != 1:
            raise ErrorPista(f"{nombre}: playlist MIDI no reconocida")
        quitar(pls[0], mapa)
    n_1058 = struct.unpack_from("<I", d, mapa.inicio + 2)[0]
    reemplazos.append((mapa.inicio + 2, mapa.inicio + 6, struct.pack("<I", n_1058 - len(quitar_canales))))
    salidas = _primero(bloques, 0x2004)
    l2005 = [h for h in (salidas.hijos if salidas else []) if h.tipo_contenido == 0x2005]
    refs = {c.pos: (buscar([c], 0x2036) if c.tipo_contenido in (0x2620, 0x2621) else [])
            for c in raiz.hijos}
    fuera = {c.pos for c, _ in quitar_canales}
    propias = {struct.unpack_from("<I", d, b.inicio + 14)[0] for c, _ in quitar_canales for b in refs[c.pos]}
    otras = {struct.unpack_from("<I", d, b.inicio + 14)[0] for c in raiz.hijos if c.pos not in fuera
             for b in refs[c.pos]}
    sale = sorted(kk for kk in propias if kk not in otras and 1 <= kk <= len(l2005))
    for kk in sale:
        quitar(l2005[kk - 1], salidas)
    if sale:
        reemplazos.append((salidas.inicio + 2, salidas.inicio + 6, struct.pack("<I", len(l2005) - len(sale))))
        for c in raiz.hijos:
            if c.pos in fuera:
                continue
            for b in refs[c.pos]:
                v = struct.unpack_from("<I", d, b.inicio + 14)[0]
                if _menos(v, sale):
                    reemplazos.append((b.inicio + 14, b.inicio + 18, struct.pack("<I", v - _menos(v, sale))))
    ns = _secuencia([_n_midi(d, c)[2] for c, _ in quitar_canales])
    for c in raiz.hijos:
        if c.pos in fuera or c.tipo_contenido not in (0x2620, 0x2621):
            continue
        q, fmt, v = _n_midi(d, c)
        if _bajar(v, ns) != v:
            v2 = _bajar(v, ns)
            reemplazos.append((q, q + struct.calcsize(fmt), struct.pack(fmt, v2, 1, v2)))


def _quitar_una(d, nombre):
    bloques = arbol(d)
    L = _primero(bloques, 0x2519)
    entradas, fin_lista, pos_cantidad = _lista_2519(d, L)
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    mitad = len(hijos) // 2
    if len(hijos) != 2 * len(entradas):
        raise ErrorPista("lista de pistas no reconocida")
    i = next((k for k, e in enumerate(entradas) if e[3] == nombre), None)
    if i is None:
        raise ErrorPista(f"no existe la pista {nombre!r}")
    tipo = entradas[i][2]
    midi, aux = tipo == 1, tipo == 2
    if tipo not in (0, 1, 2):
        raise ErrorPista(f"{nombre}: por ahora solo se pueden quitar pistas de audio, MIDI o auxiliares")
    if len(entradas) < 2:
        raise ErrorPista("la sesión debe conservar al menos una pista")
    raiz = _primero(bloques, 0x2624)
    if raiz is None or len(raiz.hijos) != len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")
    canal = raiz.hijos[i]
    if canal.tipo_contenido != {0: 0x261C, 1: 0x2620, 2: 0x261E}[tipo]:
        raise ErrorPista(f"{nombre}: el canal no corresponde al tipo de pista")
    n = len(entradas)
    reemplazos = []
    quitados = []                       # (pos, fin) de bloques que se quitan enteros

    def quitar(b, contenedor):
        reemplazos.append((b.pos, b.fin, b"", contenedor.pos))
        quitados.append((b.pos, b.fin))

    # 1) lista de pistas 0x2519
    reemplazos.append((pos_cantidad, pos_cantidad + 4, struct.pack("<I", n - 1)))
    ini, fin = entradas[i][0], entradas[i][1]
    reemplazos.append((ini, fin, b""))
    numero = entradas[i][6]              # 0 = pista sin número; no corre a las demás
    for e in entradas:
        if numero and e[6] > numero:
            reemplazos.append((e[1] - 3, e[1] - 1, struct.pack("<H", e[6] - 1)))
    m = hijos[mitad - 1].fin
    if struct.unpack_from("<I", d, m)[0] != n or hijos[mitad].pos != m + 4:
        raise ErrorPista("lista de pistas no reconocida (cantidad entre mitades)")
    reemplazos.append((m, m + 4, struct.pack("<I", n - 1)))
    for k, h in enumerate(hijos):
        if k % mitad == i:
            quitar(h, L)
        else:
            v = struct.unpack_from("<I", d, h.fin - 22)[0]
            if numero and v > numero:
                b = bytearray(d[h.pos:h.fin])          # el bloque entero: sus identificadores
                struct.pack_into("<I", b, len(b) - 22, v - 1)   # pueden parecer hijos
                reemplazos.append((h.pos, h.fin, bytes(b), L.pos))

    if midi:
        _quitar_midi(d, bloques, raiz, canal, nombre, quitar, reemplazos)
    elif not aux:                       # un auxiliar no tiene playlists
        _quitar_audio(d, bloques, raiz, canal, nombre, quitar, reemplazos)
    quitar(canal, raiz)
    reemplazos.append((raiz.inicio + 2, raiz.inicio + 6, struct.pack("<I", len(raiz.hijos) - 1)))
    num = [struct.unpack_from("<I", d, b.fin - 4)[0] for b in buscar([canal], 0x2015)]
    if num:
        corte = num[0]
        for b in buscar(bloques, 0x2015):
            if canal.pos <= b.pos < canal.fin:
                continue
            v = struct.unpack_from("<I", d, b.fin - 4)[0]
            if v > corte:
                reemplazos.append((b.fin - 4, b.fin, struct.pack("<I", v - 1)))

    # 4) grupos (0x202C no cambia con las pistas MIDI)
    for grp in buscar(bloques, 0x202A):
        nom, p = _cadena(d, grp.inicio + 2)
        cnt = struct.unpack_from("<I", d, p + 7)[0]
        lista = list(struct.unpack_from(f"<{cnt}H", d, p + 11))
        nueva = [x - (x > i) for x in lista if x != i]
        if nueva != lista:
            if not nueva:
                raise ErrorPista(f"{nombre}: es la única pista del grupo {nom!r}")
            reemplazos.append((p + 7, p + 11 + 2 * cnt, struct.pack(f"<I{len(nueva)}H", len(nueva), *nueva)))
    for gc in buscar(bloques, 0x202C) if tipo == 0 else []:
        a1, a2 = struct.unpack_from("<II", d, gc.inicio + 14)
        if struct.unpack_from("<I", d, gc.inicio + 2)[0] == 1 and (a1, a2) == (n - 1, n):
            reemplazos.append((gc.inicio + 14, gc.inicio + 22, struct.pack("<II", n - 2, n - 1)))

    # 5) tiras de la ventana
    for v in buscar(bloques, 0x258A):
        tiras = [h for h in v.hijos if h.tipo_contenido == 0x2589]
        if not tiras:
            continue
        q = tiras[0].pos - 4
        if struct.unpack_from("<I", d, q)[0] != len(tiras):
            raise ErrorPista("tiras de la ventana no reconocidas")
        sale = [t for t in tiras if struct.unpack_from("<H", d, t.inicio + 2)[0] == i]
        for t in tiras:
            k = struct.unpack_from("<H", d, t.inicio + 2)[0]
            if t in sale:
                quitar(t, v)
            elif k > i:
                b = bytearray(d[t.pos:t.fin])
                struct.pack_into("<H", b, t.inicio + 2 - t.pos, k - 1)
                reemplazos.append((t.pos, t.fin, bytes(b), v.pos))
        if sale:
            reemplazos.append((q, q + 4, struct.pack("<I", len(tiras) - len(sale))))

    d = _indice_sin(d, bloques, quitados, i + 1, i + 1)
    return reconstruir(d, reemplazos)


def _indice_sin(d, bloques, quitados, n_pista, n_canal):
    """Índice 0x0002 sin las posiciones de los bloques quitados. Las listas
    tienen un grupo por hijo: el del hijo quitado desaparece. Las entradas por
    pista (2519/n, 251A) y por canal (2624/n) bajan su número; la de 0x2716
    lleva la cantidad de pistas. n_pista y n_canal: un número (desde 1) o una
    lista, si se quitan varias."""
    final = bloques[-1]
    entradas, resto = leer_indice(d[final.inicio:final.fin])
    rangos = sorted(quitados)
    dentro = lambda p: _dentro(p, rangos)
    pistas = sorted(n_pista) if isinstance(n_pista, (list, tuple)) else [n_pista]
    canales = sorted(n_canal) if isinstance(n_canal, (list, tuple)) else [n_canal]
    out = []
    for e in entradas:
        grupos = []
        for g in e.grupos:
            pos = [p for p in g.posiciones if not dentro(p)]
            if pos:
                g.posiciones = pos
                grupos.append(g)
        if e.grupos and not grupos:
            continue
        e.grupos = grupos
        c = e.camino
        tipos = [s.tipo for s in c]
        if len(c) >= 2 and c[0].marca == 1 and c[0].numero:
            if tipos[:2] == [0x2519, 0x2716]:
                c[0].numero -= len(pistas)
            elif tipos[0] == 0x2519:
                c[0].numero -= _menos(c[0].numero, pistas)
            elif tipos[0] == 0x2624:
                c[0].numero -= _menos(c[0].numero, canales)
        if any(dentro(s.padre) for s in c if s.padre != 0xFFFFFFFF):
            raise ErrorPista("el índice apunta dentro de la pista quitada")
        out.append(e)
    nuevo = escribir_indice(out, resto)
    salida = bytearray(d[:final.inicio]) + nuevo
    struct.pack_into("<I", salida, final.pos + 3, len(nuevo))
    return bytes(salida)


def _quitar_audio(d, bloques, raiz, canal, nombre, quitar, reemplazos):
    """Playlists del canal (índices de 0x1015 tras su 0x261B) con sus entradas
    0x1052; las demás playlists y canales de audio corren sus índices."""
    sb = next(h for h in canal.hijos if h.tipo_contenido == 0x261B)
    n_audio, k_pl = struct.unpack_from("<IH", d, sb.fin)
    propias = set(struct.unpack_from(f"<{k_pl}I", d, sb.fin + 6))
    lista_pl = _primero(bloques, 0x1015)
    mapa = _primero(bloques, 0x1054)
    pls = [h for h in lista_pl.hijos if h.tipo_contenido == 0x1014]
    e1052 = [h for h in mapa.hijos if h.tipo_contenido == 0x1052]
    refs = []
    for b in pls:
        _, j = _cadena(d, b.inicio + 2)
        k = struct.unpack_from("<I", d, j + 1)[0]
        refs.append((j + 5, list(struct.unpack_from(f"<{k}H", d, j + 5))))
    if any(p >= len(pls) for p in propias):
        raise ErrorPista(f"{nombre}: playlists del canal no reconocidas")
    m_quitar = {x for p in propias for x in refs[p][1]}
    if any(x >= len(e1052) for x in m_quitar):
        raise ErrorPista(f"{nombre}: entradas de playlist no reconocidas")
    for p in sorted(propias):
        quitar(pls[p], lista_pl)
    for x in sorted(m_quitar):
        quitar(e1052[x], mapa)
    for p, (q, lista) in enumerate(refs):
        if p in propias:
            continue
        nueva = [x - sum(1 for y in m_quitar if y < x) for x in lista]
        if nueva != lista:
            reemplazos.append((q, q + 2 * len(lista), struct.pack(f"<{len(lista)}H", *nueva)))
    n_pl = struct.unpack_from("<I", d, lista_pl.inicio + 2)[0]
    n_map = struct.unpack_from("<I", d, mapa.inicio + 2)[0]
    reemplazos.append((lista_pl.inicio + 2, lista_pl.inicio + 6, struct.pack("<I", n_pl - len(propias))))
    reemplazos.append((mapa.inicio + 2, mapa.inicio + 6, struct.pack("<I", n_map - len(m_quitar))))

    # 3) canal de mezcla; los demás corren su número de audio y sus playlists
    for c in raiz.hijos:
        if c is canal or c.tipo_contenido != 0x261C:
            continue
        sbc = next(h for h in c.hijos if h.tipo_contenido == 0x261B)
        a, k = struct.unpack_from("<IH", d, sbc.fin)
        lista = list(struct.unpack_from(f"<{k}I", d, sbc.fin + 6))
        nueva = [x - sum(1 for y in propias if y < x) for x in lista]
        a2 = a - (a > n_audio)
        if (a2, nueva) != (a, lista):
            reemplazos.append((sbc.fin, sbc.fin + 6 + 4 * k, struct.pack(f"<IH{k}I", a2, k, *nueva)))


def _n_midi(d, c):
    """Número n del canal entre los 0x2620/0x2621 (desde 0): en un 0x2620,
    'u32 n | u32 1 | u32 n | u8 1' tras su 0x102D; en un 0x2621 (instrumento),
    'u32 n | u16 1 | u32 n | u8 1' justo antes de su 0x2010. Devuelve
    (posición, formato, n)."""
    if c.tipo_contenido == 0x2620:
        cab = next(h for h in c.hijos if h.tipo_contenido == 0x102D)
        q, fmt = cab.fin, "<III"
    else:
        h = next((h for h in c.hijos if h.tipo_contenido == 0x2010), None)
        if h is None:
            raise ErrorPista("número del canal de instrumento no reconocido")
        q, fmt = h.pos - 11, "<IHI"
    n, uno, n2 = struct.unpack_from(fmt, d, q)
    if uno != 1 or n != n2 or d[q + struct.calcsize(fmt)] != 1:
        raise ErrorPista("número del canal MIDI no reconocido")
    return q, fmt, n


def _quitar_midi(d, bloques, raiz, canal, nombre, quitar, reemplazos):
    """Playlist 0x1057 de la pista, su salida 0x2005 (si ningún otro canal la
    usa; los 0x2036 que apuntan más allá bajan) y el número n de los canales
    0x2620/0x2621 siguientes [verificado borrando una pista MIDI en Pro Tools 12]."""
    mapa = _primero(bloques, 0x1058)
    pls = [h for h in (mapa.hijos if mapa else []) if h.tipo_contenido == 0x1057
           and _cadena(d, h.inicio + 2)[0] == nombre]
    if len(pls) != 1:
        raise ErrorPista(f"{nombre}: playlist MIDI no reconocida")
    quitar(pls[0], mapa)
    n_1058 = struct.unpack_from("<I", d, mapa.inicio + 2)[0]
    reemplazos.append((mapa.inicio + 2, mapa.inicio + 6, struct.pack("<I", n_1058 - 1)))
    # salidas MIDI
    salidas = _primero(bloques, 0x2004)
    l2005 = [h for h in (salidas.hijos if salidas else []) if h.tipo_contenido == 0x2005]
    refs = {c.pos: (buscar([c], 0x2036) if c.tipo_contenido in (0x2620, 0x2621) else [])
            for c in raiz.hijos}
    propias = {struct.unpack_from("<I", d, b.inicio + 14)[0] for b in refs[canal.pos]}
    otras = {struct.unpack_from("<I", d, b.inicio + 14)[0] for c in raiz.hijos if c is not canal
             for b in refs[c.pos]}
    sale = sorted(k for k in propias if k not in otras and 1 <= k <= len(l2005))
    for k in sale:
        quitar(l2005[k - 1], salidas)
    if sale:
        reemplazos.append((salidas.inicio + 2, salidas.inicio + 6, struct.pack("<I", len(l2005) - len(sale))))
        for c in raiz.hijos:
            if c is canal:
                continue
            for b in refs[c.pos]:
                v = struct.unpack_from("<I", d, b.inicio + 14)[0]
                v2 = v - sum(1 for k in sale if k < v)
                if v2 != v:
                    reemplazos.append((b.inicio + 14, b.inicio + 18, struct.pack("<I", v2)))
    # número de los canales MIDI e instrumento siguientes
    _, _, n_q = _n_midi(d, canal)
    for c in raiz.hijos:
        if c is canal or c.tipo_contenido not in (0x2620, 0x2621):
            continue
        q, fmt, v = _n_midi(d, c)
        if v > n_q:
            reemplazos.append((q, q + struct.calcsize(fmt), struct.pack(fmt, v - 1, 1, v - 1)))

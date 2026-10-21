"""Quitar pistas de audio de una sesión .ptx.

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

Las regiones y archivos de la pista quedan en la lista de clips, como al borrar
la pista en Pro Tools. Las definiciones de fundidos (0x2630) también quedan.
"""

import struct

from .bloques import arbol, buscar
from .indice import leer_indice, escribir_indice
from .pistas import _cadena
from .pistas_nuevas import ErrorPista, _lista_2519, _primero
from .reconstruccion import reconstruir


def quitar_pistas(d, nombres):
    """d: sesión descifrada. nombres: pistas de audio a quitar. Devuelve la
    sesión descifrada sin esas pistas."""
    for nombre in nombres:
        d = _quitar_una(d, nombre)
    return d


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
    if entradas[i][2] != 0:
        raise ErrorPista(f"{nombre}: por ahora solo se pueden quitar pistas de audio")
    if len(entradas) < 2:
        raise ErrorPista("la sesión debe conservar al menos una pista")
    raiz = _primero(bloques, 0x2624)
    if raiz is None or len(raiz.hijos) != len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")
    canal = raiz.hijos[i]
    if canal.tipo_contenido != 0x261C:
        raise ErrorPista(f"{nombre}: el canal no es de audio")
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

    # 2) playlists del canal y sus entradas 0x1052
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

    # 4) grupos
    for grp in buscar(bloques, 0x202A):
        nom, p = _cadena(d, grp.inicio + 2)
        cnt = struct.unpack_from("<I", d, p + 7)[0]
        lista = list(struct.unpack_from(f"<{cnt}H", d, p + 11))
        nueva = [x - (x > i) for x in lista if x != i]
        if nueva != lista:
            if not nueva:
                raise ErrorPista(f"{nombre}: es la única pista del grupo {nom!r}")
            reemplazos.append((p + 7, p + 11 + 2 * cnt, struct.pack(f"<I{len(nueva)}H", len(nueva), *nueva)))
    for gc in buscar(bloques, 0x202C):
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
    lleva la cantidad de pistas."""
    final = bloques[-1]
    entradas, resto = leer_indice(d[final.inicio:final.fin])
    dentro = lambda p: any(a <= p < b for a, b in quitados)
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
                c[0].numero -= 1
            elif tipos[0] == 0x2519 and c[0].numero > n_pista:
                c[0].numero -= 1
            elif tipos[0] == 0x2624 and c[0].numero > n_canal:
                c[0].numero -= 1
        if any(dentro(s.padre) for s in c if s.padre != 0xFFFFFFFF):
            raise ErrorPista("el índice apunta dentro de la pista quitada")
        out.append(e)
    nuevo = escribir_indice(out, resto)
    salida = bytearray(d[:final.inicio]) + nuevo
    struct.pack_into("<I", salida, final.pos + 3, len(nuevo))
    return bytes(salida)

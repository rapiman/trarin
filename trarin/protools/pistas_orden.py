"""Cambiar el orden de las pistas de una sesión .ptx de Pro Tools.

Lo que Pro Tools 12 cambia al mover pistas (comparando sesiones antes y después
de mover pistas a mano) y que reordenar() reproduce:
* la lista 0x2519 (entradas y sus dos 0x251A) en el orden nuevo; el número de
  cada entrada (u16) y de sus 0x251A (u32 en fin−22) es la posición + 1;
* los canales 0x2624 en el mismo orden; su 0x2015 = posición + 1;
* los canales de audio numerados en orden (u32 tras el 0x261B), sus playlists
  0x1014 (en 0x1015) en ese orden, y las entradas 0x1052 del mapa 0x1054 en el
  orden de las playlists, con los números de cada 0x1014 renumerados;
* los canales MIDI numerados en orden (u32 n de _n_midi) y sus playlists 0x1057
  (en 0x1058) en ese orden: Pro Tools asocia canal y playlist MIDI por posición,
  y si quedan en otro orden muestra las pistas MIDI en el orden viejo con la
  mezcla de otra pista;
* los grupos 0x202A con las posiciones nuevas, ordenadas; las tiras 0x2589 con
  la posición nueva (su propio orden no cambia);
* el índice 0x0002: posiciones corridas de los bloques movidos, números de
  pista y canal renumerados, y las posiciones de cada grupo del índice (y los
  grupos) ordenadas de menor a mayor, como las deja Pro Tools.
Todo se mueve sin cambiar tamaños, así que nada más se corre.
Verificado en Pro Tools 12 [2026-10-27]: dos pistas de audio con sus entradas,
y el show RAPIZUAM (126 pistas, 42 MIDI): contenido, color, salida y volumen de
cada pista iguales a la sesión original."""
import struct

from .bloques import arbol, buscar
from .indice import leer_indice, escribir_indice
from .pistas import _cadena
from .pistas_nuevas import ErrorPista, _lista_2519, _primero
from .pistas_quitar import _n_midi
from .reconstruccion import _posiciones_indice, _todos


def reordenar(d, orden):
    """d: sesión descifrada. orden: nombres de TODAS las pistas en el orden nuevo."""
    b = arbol(d)
    L = _primero(b, 0x2519)
    ent, fin_lista, pos_cant = _lista_2519(d, L)
    n = len(ent)
    nombres = [e[3] for e in ent]
    if sorted(orden) != sorted(nombres) or len(set(nombres)) != n:
        raise ErrorPista("el orden pedido no tiene exactamente las mismas pistas (o hay nombres repetidos)")
    P = [nombres.index(x) for x in orden]          # P[nuevo] = viejo
    nuevo_de = {v: k for k, v in enumerate(P)}     # viejo → nuevo
    hijos = [h for h in L.hijos if h.tipo_contenido == 0x251A]
    mitad = len(hijos) // 2
    raiz = _primero(b, 0x2624)
    if len(raiz.hijos) != n or len(hijos) != 2 * n:
        raise ErrorPista("lista de pistas o canales no reconocidos")
    out = bytearray(d)
    mov = []                                       # (ini viejo, fin viejo, ini nuevo)

    def permutar(segs, nuevos_bytes):
        """segs: [(ini, fin)] contiguos en orden viejo; nuevos_bytes[k]: bytes del seg P[k] ya parchado."""
        for (a, f), (a2, f2) in zip(segs, segs[1:]):
            if f != a2:
                raise ErrorPista("segmentos no contiguos")
        p = segs[0][0]
        for k, viejo in enumerate(P):
            a, f = segs[viejo]
            out[p:p + (f - a)] = nuevos_bytes[k]
            mov.append((a, f, p))
            p += f - a

    # 1) entradas de 0x2519
    segs = [(e[0], e[1]) for e in ent]
    nb = []
    for k, viejo in enumerate(P):
        x = bytearray(d[ent[viejo][0]:ent[viejo][1]])
        if ent[viejo][6]:
            struct.pack_into("<H", x, len(x) - 3, k + 1)
        nb.append(bytes(x))
    permutar(segs, nb)
    # 2) las dos mitades de 0x251A
    for mitad_hijos in (hijos[:mitad], hijos[mitad:]):
        segs = [(h.pos, h.fin) for h in mitad_hijos]
        nb = []
        for k, viejo in enumerate(P):
            h = mitad_hijos[viejo]
            x = bytearray(d[h.pos:h.fin])
            v = struct.unpack_from("<I", x, len(x) - 22)[0]
            if v != ent[viejo][6]:
                raise ErrorPista(f"{nombres[viejo]}: número del 0x251A no reconocido")
            if v:
                struct.pack_into("<I", x, len(x) - 22, k + 1)
            nb.append(bytes(x))
        permutar(segs, nb)

    # 3) canales 0x2624: 0x2015, número de audio y playlists, número MIDI
    audio_viejo = {}                               # índice viejo → (n_audio, [playlists])
    for i, c in enumerate(raiz.hijos):
        if c.tipo_contenido == 0x261C:
            sb = next(h for h in c.hijos if h.tipo_contenido == 0x261B)
            a, k = struct.unpack_from("<IH", d, sb.fin)
            audio_viejo[i] = (a, list(struct.unpack_from(f"<{k}I", d, sb.fin + 6)), sb.fin - c.pos)
    orden_audio = [v for v in P if v in audio_viejo]
    n_audio_nuevo = {v: j for j, v in enumerate(orden_audio)}
    lista_pl = _primero(b, 0x1015)
    pls = [h for h in lista_pl.hijos if h.tipo_contenido == 0x1014]
    todas = sorted(p for x in audio_viejo.values() for p in x[1])
    if todas != list(range(len(pls))):
        raise ErrorPista("las playlists no corresponden una a una con los canales de audio")
    orden_pl = [p for v in orden_audio for p in audio_viejo[v][1]]
    pl_nueva = {p: j for j, p in enumerate(orden_pl)}
    midi = [i for i, c in enumerate(raiz.hijos) if c.tipo_contenido in (0x2620, 0x2621)]
    n_midi_viejo = {i: _n_midi(d, raiz.hijos[i]) for i in midi}
    if sorted(x[2] for x in n_midi_viejo.values()) != list(range(len(midi))):
        raise ErrorPista("números MIDI no reconocidos")
    orden_midi = [v for v in P if v in n_midi_viejo]
    n_midi_nuevo = {v: j for j, v in enumerate(orden_midi)}
    segs = [(c.pos, c.fin) for c in raiz.hijos]
    nb = []
    for k, viejo in enumerate(P):
        c = raiz.hijos[viejo]
        x = bytearray(d[c.pos:c.fin])
        q15 = buscar([c], 0x2015)
        for q in q15:
            v = struct.unpack_from("<I", d, q.fin - 4)[0]
            if v != viejo + 1:
                raise ErrorPista(f"{nombres[viejo]}: 0x2015 no corresponde a la posición")
            struct.pack_into("<I", x, q.fin - 4 - c.pos, k + 1)
        if viejo in audio_viejo:
            a, pl, off = audio_viejo[viejo]
            struct.pack_into(f"<IH{len(pl)}I", x, off, n_audio_nuevo[viejo], len(pl), *[pl_nueva[p] for p in pl])
        if viejo in n_midi_viejo:
            q, fmt, v = n_midi_viejo[viejo]
            w = n_midi_nuevo[viejo]
            struct.pack_into(fmt, x, q - c.pos, w, 1, w)
        nb.append(bytes(x))
    permutar(segs, nb)

    # 4) playlists 0x1014 en el orden nuevo
    segs = [(h.pos, h.fin) for h in pls]
    for (a, f), (a2, f2) in zip(segs, segs[1:]):
        if f != a2:
            raise ErrorPista("playlists no contiguas")
    p = segs[0][0]
    for viejo_pl in orden_pl:
        a, f = segs[viejo_pl]
        out[p:p + f - a] = d[a:f]
        mov.append((a, f, p))
        p += f - a
    # 4b) entradas 0x1052 del mapa 0x1054 en el orden de las playlists nuevas, y sus
    #     números (u16 tras el nombre de cada 0x1014) renumerados en orden
    mapa = _primero(b, 0x1054)
    e1052 = [h for h in mapa.hijos if h.tipo_contenido == 0x1052] if mapa else []
    ids_pl = []
    for h in pls:
        _, j = _cadena(d, h.inicio + 2)
        j += 1
        k = struct.unpack_from("<I", d, j)[0]
        ids_pl.append((j + 4 - h.pos, list(struct.unpack_from(f"<{k}H", d, j + 4))))
    orden_e = [i for viejo_pl in orden_pl for i in ids_pl[viejo_pl][1]]
    if sorted(orden_e) != list(range(len(e1052))):
        raise ErrorPista("las entradas 0x1052 no corresponden una a una con las playlists")
    e_nueva = {v: k for k, v in enumerate(orden_e)}
    segs_e = [(h.pos, h.fin) for h in e1052]
    for (a, f), (a2, f2) in zip(segs_e, segs_e[1:]):
        if f != a2:
            raise ErrorPista("entradas 0x1052 no contiguas")
    p = segs_e[0][0] if segs_e else 0
    for viejo_e in orden_e:
        a, f = segs_e[viejo_e]
        out[p:p + f - a] = d[a:f]
        mov.append((a, f, p))
        p += f - a
    for viejo_pl in orden_pl:
        off, lst = ids_pl[viejo_pl]
        donde = _nueva_pos(pls[viejo_pl].pos, mov) + off
        struct.pack_into(f"<{len(lst)}H", out, donde, *[e_nueva[i] for i in lst])

    # 4c) playlists MIDI 0x1057 (por nombre) en el orden nuevo de las pistas MIDI;
    #     las que no son de una pista actual quedan al final, en su orden
    mapa58 = _primero(b, 0x1058)
    l1057 = [h for h in mapa58.hijos if h.tipo_contenido == 0x1057] if mapa58 else []
    if l1057:
        nom57 = [_cadena(d, h.inicio + 2)[0] for h in l1057]
        orden57 = []
        for v in orden_midi:
            k = [i for i, x in enumerate(nom57) if x == nombres[v]]
            if len(k) != 1:
                raise ErrorPista(f"{nombres[v]}: playlist MIDI no reconocida")
            orden57.append(k[0])
        orden57 += [i for i in range(len(l1057)) if i not in orden57]
        segs57 = [(h.pos, h.fin) for h in l1057]
        for (a, f), (a2, f2) in zip(segs57, segs57[1:]):
            if f != a2:
                raise ErrorPista("playlists MIDI no contiguas")
        p = segs57[0][0]
        for i in orden57:
            a, f = segs57[i]
            out[p:p + f - a] = d[a:f]
            mov.append((a, f, p))
            p += f - a

    # 5) grupos y tiras
    for grp in buscar(b, 0x202A):
        nom, p = _cadena(d, grp.inicio + 2)
        cnt = struct.unpack_from("<I", d, p + 7)[0]
        lista = struct.unpack_from(f"<{cnt}H", d, p + 11)
        struct.pack_into(f"<{cnt}H", out, _nueva_pos(p + 11, mov), *sorted(nuevo_de[x] for x in lista))
    for v in buscar(b, 0x258A):
        for t in (h for h in v.hijos if h.tipo_contenido == 0x2589):
            kt = struct.unpack_from("<H", d, t.inicio + 2)[0]
            if kt < n:
                struct.pack_into("<H", out, _nueva_pos(t.inicio + 2, mov), nuevo_de[kt])

    # 6) índice: posiciones corridas y números de pista/canal
    final = b[-1]
    por_pos = {x.pos: x for x in _todos(b)}
    idx_pos = _posiciones_indice(d, final, por_pos)
    for k in idx_pos:
        v = struct.unpack_from("<I", d, k)[0]
        struct.pack_into("<I", out, k, _nueva_pos(v, mov))
    entradas, resto = leer_indice(bytes(out[final.inicio:final.fin]))
    for e in entradas:
        c0 = e.camino[0] if e.camino else None
        if c0 and c0.marca == 1 and c0.numero and len(e.camino) >= 2 and c0.tipo in (0x2519, 0x2624) \
                and e.camino[1].tipo != 0x2716:
            if not 1 <= c0.numero <= n:
                raise ErrorPista("número de pista del índice fuera de rango")
            c0.numero = nuevo_de[c0.numero - 1] + 1
    # Pro Tools guarda las posiciones de cada grupo y los grupos ordenados
    for e in entradas:
        for g in e.grupos:
            g.posiciones.sort()
        e.grupos.sort(key=lambda g: g.posiciones[0] if g.posiciones else -1)
    # mismas entradas, en orden de número dentro de cada tramo de igual camino
    i = 0
    while i < len(entradas):
        clave = _clave(entradas[i])
        j = i
        while j < len(entradas) and _clave(entradas[j]) == clave:
            j += 1
        if clave is not None:
            entradas[i:j] = sorted(entradas[i:j], key=lambda e: e.camino[0].numero)
        i = j
    nuevo = escribir_indice(entradas, resto)
    if len(nuevo) != final.fin - final.inicio:
        raise ErrorPista("el índice cambió de tamaño")
    out[final.inicio:final.fin] = nuevo
    return bytes(out)


def _clave(e):
    c = e.camino
    if len(c) >= 2 and c[0].marca == 1 and c[0].tipo in (0x2519, 0x2624) and c[1].tipo != 0x2716 and c[0].numero:
        return c[0].tipo
    return None


def _nueva_pos(v, mov):
    for a, f, p in mov:
        if a <= v < f:
            return p + (v - a)
    return v


def verificar_orden(antes, despues, orden):
    """Compara dos modelos comunes (antes y después de reordenar): el orden de
    las pistas y, por pista, sus clips, notas, fundidos, mezcla y automatización.
    Devuelve una lista de problemas (vacía si todo coincide)."""
    def huella(x):
        return {q["nombre"]: ([(k.get("tick"), k.get("muestra"), k.get("largo_muestras"), k.get("archivo"),
                               k.get("notas"), k.get("fundidos")) for k in q["clips"]],
                              q.get("mezcla"), q.get("automatizacion")) for q in x["pistas"]}
    problemas = []
    if [q["nombre"] for q in despues["pistas"]] != list(orden):
        problemas.append("el orden leído no es el pedido")
    a, b = huella(antes), huella(despues)
    distintas = [n for n in a if a[n] != b.get(n)]
    if distintas:
        problemas.append(f"pistas con contenido distinto: {', '.join(distintas[:10])}")
    for clave in ("marcadores", "tempo", "compases"):
        if antes.get(clave) != despues.get(clave):
            problemas.append(f"{clave} distintos")
    return problemas


def orden_por_grupos(comun, grupos):
    """Orden propuesto para reordenar(): master y auxiliares arriba; después las
    pistas de audio por grupo (en el orden de 'grupos', nombres de grupos de la
    sesión) y dentro de cada grupo por su primer clip; al final las MIDI con el
    mismo criterio. Las pistas sin grupo van al final de su bloque.
    Devuelve (orden, {pista: grupo})."""
    por_nombre = {g["nombre"]: g["pistas"] for g in (comun.get("nativo") or {}).get("grupos", [])}
    faltan = [g for g in grupos if g not in por_nombre]
    if faltan:
        raise ErrorPista(f"grupos que no están en la sesión: {', '.join(faltan)}")
    de = {}
    for g in grupos:
        for n in por_nombre[g]:
            de.setdefault(n, g)
    pistas = comun["pistas"]

    def inicio(p):
        s = [k.get("segundos") for k in p.get("clips") or [] if k.get("segundos") is not None]
        return min(s) if s else float("inf")

    def clave(p):
        return (grupos.index(de[p["nombre"]]) if p["nombre"] in de else len(grupos), inicio(p))

    es_midi = lambda p: p["tipo"] in ("midi", "instrumento")
    arriba = [p for p in pistas if p["tipo"] != "audio" and not es_midi(p)]
    audio = sorted((p for p in pistas if p["tipo"] == "audio"), key=clave)
    midi = sorted((p for p in pistas if es_midi(p)), key=clave)
    return [p["nombre"] for p in arriba + audio + midi], {n: de[n] for n in de}

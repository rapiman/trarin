"""Escritura de sesiones .ptx: cambios sobre una copia de la plantilla.

Todo lo que se escribe aquí tiene tamaño fijo, así que se edita en el lugar
sin mover bloques (los cambios de tamaño pasan por reconstruccion.py):

* Colocación de una región (0x1050 → 0x104F): desde el inicio del bloque
  (tipo de contenido incluido), +2 mute (byte) y +9 muestra del ancla (5
  bytes). En pistas estéreo se cambian todos los canales, y los
  fundidos de entrada y salida de la región se mueven con ella.
* Fader principal de un canal (0x2624 → canal → 0x261B → 0x260D → 0x1029):
  +3 volumen (int32, décimas de dB), +7 mute, +15 y +43 paneo (int32,
  −100..100). Pro Tools repite el valor fijo como único punto de la lista de
  automatización del parámetro (0x260A: volumen y mute directos, paneo dentro
  de 0x260C); ese punto se actualiza también. Si la lista tiene más de un
  punto, el parámetro está automatizado (Pro Tools sigue la curva e ignora el
  valor fijo): se desplaza toda la curva lo mismo que cambió el valor.
"""

import os
from pathlib import Path
import struct

from .bloques import _parsear, arbol, buscar
from .descifrado import leer_descifrado
from .pistas_nuevas import _id_nuevo, _rehacer
from .reconstruccion import cifrar, reconstruir
from .tiempo import CERO, _EV_TEMPO
from . import pistas as P


class ErrorEscritura(Exception):
    pass


def _primero(bloques, tipo):
    b = buscar(bloques, tipo)
    return b[0] if b else None


# ------------------------------------------------------------------ regiones

def _colocaciones(d, bloques, nombre_playlist):
    """Por canal de la playlist: (regiones, fundidos) con la posición del 0x104F,
    en el mismo orden que leer_pistas_audio."""
    regiones = P.leer_regiones_audio(d, bloques)
    fundidos = P.leer_fundidos(d, bloques)
    lista = _primero(bloques, 0x1015)
    ids = None
    for c in (lista.hijos if lista else []):
        if c.tipo_contenido != 0x1014:
            continue
        nombre, j = P._cadena(d, c.inicio + 2)
        if nombre == nombre_playlist:
            j += 1
            n = struct.unpack_from("<I", d, j)[0]
            ids = list(struct.unpack_from(f"<{n}H", d, j + 4))
            break
    if ids is None:
        raise ErrorEscritura(f"no se encontró la playlist {nombre_playlist!r}")
    mapa = _primero(bloques, 0x1054)
    entradas = [c for c in mapa.hijos if c.tipo_contenido == 0x1052] if mapa else []
    canales = []
    for i in ids:
        regs, fun = [], []
        for e in (entradas[i].hijos if i < len(entradas) else []):
            if e.tipo_contenido != 0x1050:
                continue
            for f in e.hijos:
                if f.tipo_contenido != 0x104F:
                    continue
                ri = struct.unpack_from("<I", d, f.inicio + 4)[0]
                muestra = P._entero(d, f.inicio + 9, 5)
                if d[f.fin] == 1:
                    if ri < len(fundidos):
                        g = fundidos[ri]
                        fun.append({"pos": f.inicio, "ancla": muestra, "inicio": muestra - g["pre_roll"],
                                    "fin": muestra - g["pre_roll"] + g["muestras"], "tipo": g["tipo"],
                                    "bloque": e.pos, "fin_bloque": e.fin})
                elif ri < len(regiones):
                    regs.append({"pos": f.inicio, "muestra": muestra, "largo": regiones[ri]["muestras"],
                                 "indice": ri, "bloque": e.pos, "fin_bloque": e.fin})
        canales.append((sorted(regs, key=lambda r: r["muestra"]), sorted(fun, key=lambda r: r["ancla"])))
    return canales


def _cambios_regiones(d, bloques, pista, cambios, out):
    """cambios: {índice del clip: {"muestra"?: int, "mute"?: bool}}.

    Un fundido se mueve con las regiones que toca si todas se mueven lo mismo;
    un fundido cruzado entre una región que se mueve y otra que no (o que se
    mueve distinto) da error: habría que recalcularlo."""
    canales = _colocaciones(d, bloques, pista)
    base = canales[0][0]
    for j in cambios:
        if j >= len(base):
            raise ErrorEscritura(f"{pista}: no existe el clip {j}")
    for regs, fun in canales:
        deltas = []                                   # (región, delta) por región del canal
        for j, ref in enumerate(base):
            r = next((x for x in regs if x["muestra"] == ref["muestra"] and x["largo"] == ref["largo"]), None)
            if r is None:
                if j in cambios:
                    raise ErrorEscritura(f"{pista}#{j}: los canales de la pista no coinciden")
                continue
            cambio = cambios.get(j, {})
            delta = cambio["muestra"] - ref["muestra"] if "muestra" in cambio else 0
            if r["muestra"] + delta < 0:
                raise ErrorEscritura(f"{pista}#{j}: posición negativa")
            if delta:
                out[r["pos"] + 9] = (r["muestra"] + delta).to_bytes(5, "little")
            if "mute" in cambio:
                out[r["pos"] + 2] = bytes([1 if cambio["mute"] else 0])
            deltas.append((r, delta, j))
        for f in fun:
            toca = [(delta, j) for r, delta, j in deltas
                    if f["inicio"] < r["muestra"] + r["largo"] and r["muestra"] < f["fin"]]
            movs = {delta for delta, _ in toca}
            if len(movs) > 1:
                raise ErrorEscritura(f"{pista}: el fundido cruzado entre los clips {sorted(j for _, j in toca)} "
                                     "quedaría desarmado; mover ambos clips lo mismo o quitar el fundido en Pro Tools")
            if movs and movs != {0}:
                out[f["pos"] + 9] = (f["ancla"] + movs.pop()).to_bytes(5, "little")


def _cambios_midi(d, bloques, pista, cambios, out):
    """Mueve clips MIDI. cambios: {índice del clip: tick}. Colocación MIDI:
    0x1058 → 0x1057 (nombre) → 0x1056 → 0x104F con el tick (+10**12) en +9."""
    mapa = _primero(bloques, 0x1058)
    pl = next((c for c in (mapa.hijos if mapa else []) if c.tipo_contenido == 0x1057
               and P._cadena(d, c.inicio + 2)[0] == pista), None)
    if pl is None:
        raise ErrorEscritura(f"no se encontró la pista MIDI {pista!r}")
    n_reg = len(P.leer_regiones_midi(d, bloques))
    col = []
    for e in pl.hijos:
        if e.tipo_contenido != 0x1056:
            continue
        for f in e.hijos:
            if f.tipo_contenido == 0x104F and struct.unpack_from("<I", d, f.inicio + 4)[0] < n_reg:
                col.append((P._sin_cero(P._entero(d, f.inicio + 9, 5)), f.inicio))
    col.sort(key=lambda x: x[0])
    for j, tick in cambios.items():
        if j >= len(col):
            raise ErrorEscritura(f"{pista}: no existe el clip MIDI {j}")
        if tick < 0:
            raise ErrorEscritura(f"{pista}#m{j}: posición negativa")
        out[col[j][1] + 9] = (tick + CERO).to_bytes(5, "little")


# --------------------------------------------------------------- notas MIDI

_COLA_NOTA = bytes.fromhex("4000000000000000400000000000000040")


def _md(x, p):
    """Chunk 'Md…' en p: (etiqueta, versión, largo, inicio del contenido)."""
    j = p + 2
    while j < len(x) and (65 <= x[j] <= 90 or 97 <= x[j] <= 122):
        j += 1
    ver, largo = struct.unpack_from("<HI", x, j)
    return x[p:j], ver, largo, j + 6


def _chunks_notas(d, b):
    """Chunks 'MdChun' del bloque 0x2000: lista de (inicio, fin) absolutos."""
    p, out = b.inicio + 6, []
    while p < b.fin:
        tag, _, largo, c0 = _md(d, p)
        if tag != b"MdChun":
            raise ErrorEscritura("bloque de notas MIDI no reconocido")
        out.append((p, c0 + largo))
        p = c0 + largo
    if len(out) != struct.unpack_from("<I", d, b.inicio + 2)[0]:
        raise ErrorEscritura("bloque de notas MIDI no reconocido")
    return out


def _chunk_con_notas(d, ini, fin, notas_ventana, desde, largo_ventana):
    """Copia del MdChun [ini, fin) con las notas de la ventana [desde,
    desde+largo) reemplazadas por notas_ventana (ticks relativos a 'desde').

    MdChun → MdNLB (versión 3): u32 n, n eventos de 35 bytes (posición 5 bytes,
    00 00 40, nota, largo 5 bytes, 00 00 40, velocidad, y 17 bytes), una copia de
    la interpretación original (u32 n, u32 11, n × 11 bytes = bytes 8..18 de
    cada evento), u32 n y n + 6 bytes (01 00 01 00 y ceros). El chunk termina con
    2A 00 00 00, un identificador de 8 bytes y 01 (se genera uno nuevo).
    Verificado editando y borrando notas en Pro Tools 12: Pro Tools crea un
    chunk nuevo y la región pasa a usarlo; los demás chunks no cambian."""
    tag, ver, l_chun, c0 = _md(d, ini)
    tag2, ver2, l_nlb, n0 = _md(d, c0)
    if tag2 != b"MdNLB" or ver2 != 3:
        raise ErrorEscritura("formato de notas MIDI no reconocido en esta versión")
    n = struct.unpack_from("<I", d, n0)[0]
    p = n0 + 4 + 35 * n
    n2, tam = struct.unpack_from("<II", d, p)
    p2 = p + 8 + tam * n2
    n3 = struct.unpack_from("<I", d, p2)[0]
    if n2 != n or tam != 11 or n3 != n or n0 + l_nlb != p2 + 4 + n + 6:
        raise ErrorEscritura("formato de notas MIDI no reconocido en esta versión")
    eventos = [d[n0 + 4 + 35 * k:n0 + 39 + 35 * k] for k in range(n)]
    originales = [d[p + 8 + 11 * k:p + 19 + 11 * k] for k in range(n)]
    marcas = [d[p2 + 4 + k:p2 + 5 + k] for k in range(n)]
    con_cero = any(P._entero(e, 0, 5) >= CERO for e in eventos)

    def tick(e):
        return P._sin_cero(P._entero(e, 0, 5))

    dentro = [k for k in range(n) if desde <= tick(eventos[k]) < desde + largo_ventana]
    libres = list(dentro)
    nuevos = [(eventos[k], originales[k], marcas[k]) for k in range(n) if k not in dentro]
    for nota in notas_ventana:
        t = desde + nota["tick"]
        if not (0 <= nota["nota"] <= 127 and 1 <= nota["velocidad"] <= 127 and nota["largo_ticks"] > 0
                and 0 <= nota["tick"] < largo_ventana):
            raise ErrorEscritura(f"nota inválida: {nota}")
        # se reutiliza el evento original si la nota ya estaba (misma posición y tecla)
        k = next((k for k in libres if tick(eventos[k]) == t and eventos[k][8] == nota["nota"]), None)
        if k is not None:
            libres.remove(k)
            ev, orig, marca = bytearray(eventos[k]), originales[k], marcas[k]
        else:
            ev = bytearray(((t + CERO) if con_cero else t).to_bytes(5, "little") + b"\x00\x00\x40"
                           + bytes(1) + bytes(5) + b"\x00\x00\x40" + bytes(1) + _COLA_NOTA)
            orig, marca = None, b"\x00"
        ev[8] = nota["nota"]
        ev[9:14] = int(nota["largo_ticks"]).to_bytes(5, "little")
        ev[17] = nota["velocidad"]
        nuevos.append((bytes(ev), orig or bytes(ev[8:19]), marca))
    nuevos.sort(key=lambda x: (tick(x[0]), x[0][8]))
    m = len(nuevos)
    # tras la copia original: u32 n y n + 6 bytes, siempre 01 00 01 00 y ceros
    # (en los 890 chunks del corpus); no son marcas por nota
    seccion = d[p2 + 4:p2 + 10 + n]
    if seccion[4:] != bytes(len(seccion) - 4):
        raise ErrorEscritura("formato de notas MIDI no reconocido en esta versión")
    nlb = (struct.pack("<I", m) + b"".join(e for e, _, _ in nuevos)
           + struct.pack("<II", m, 11) + b"".join(o for _, o, _ in nuevos)
           + struct.pack("<I", m) + bytes(seccion[:4]) + bytes(m + 2))
    resto = bytearray(d[n0 + l_nlb:c0 + l_chun])
    if resto[-13:-9] != b"\x2a\x00\x00\x00":
        raise ErrorEscritura("formato de notas MIDI no reconocido en esta versión")
    resto[-9:-1] = os.urandom(8)
    contenido = b"MdNLB" + struct.pack("<HI", 3, len(nlb)) + nlb + bytes(resto)
    return b"MdChun" + struct.pack("<HI", ver, len(contenido)) + contenido


# Plantillas de Pro Tools 12 (una nota sola, región "MIDI 1-01" y su colocación),
# para sesiones que aún no tienen MIDI.
_PLANTILLA_CHUNK = bytes.fromhex(
    "4d644368756e01007e0000004d644e4c420300450000000100000000afc9d4e80000403e005307000000004050"
    "4000000000000000400000000000000040010000000b0000003e005307000000004050400100000001000100"
    "000000000000004d64455301000f0000004d6454454c01000400000000000000000000002a000000b7638f02"
    "0f48b03001")
_PLANTILLA_2633 = bytes.fromhex(
    "5a01005600000033265a0100490000002826090000004d49444920312d303100513000080010a5d4e800f22b"
    "fffffffffffffffffeff00000000ffff04000400000000000000000000000000000000000000000000ffffff"
    "ff00000000")
_PLANTILLA_1056 = bytes.fromhex(
    "5a01002b00000056105a0800220000004f10000000000000000010a5d4e800004003feff00000100ffffffff"
    "ffffffff0000")


def _cambios_notas(d, bloques, cambios, nuevas=()):
    """cambios: {(pista, índice del clip): notas}; nuevas: [(notas, largo)] de
    regiones MIDI nuevas (cada una con su chunk). Devuelve (reemplazos, out,
    índices de los chunks de las regiones nuevas)."""
    b = _primero(bloques, 0x2000)
    lista = _primero(bloques, 0x2634) or _primero(bloques, 0x2002)
    if not b or not lista:
        raise ErrorEscritura("la sesión no tiene notas MIDI")
    chunks = _chunks_notas(d, b)
    regiones = []
    for c in lista.hijos:
        if c.tipo_contenido not in (0x2633, 0x2001):
            continue
        g = next((x for x in c.hijos if x.tipo_contenido in (0x2628, 0x1007)), None)
        if g:
            regiones.append(g)
    por_region, out = {}, {}
    for (pista, j), notas in cambios.items():
        mapa = _primero(bloques, 0x1058)
        pl = next((c for c in (mapa.hijos if mapa else []) if c.tipo_contenido == 0x1057
                   and P._cadena(d, c.inicio + 2)[0] == pista), None)
        col = sorted((P._sin_cero(P._entero(d, f.inicio + 9, 5)), struct.unpack_from("<I", d, f.inicio + 4)[0])
                     for e in (pl.hijos if pl else []) if e.tipo_contenido == 0x1056
                     for f in e.hijos if f.tipo_contenido == 0x104F
                     and struct.unpack_from("<I", d, f.inicio + 4)[0] < len(regiones))
        if j >= len(col):
            raise ErrorEscritura(f"{pista}: no existe el clip MIDI {j}")
        ri = col[j][1]
        if ri in por_region and por_region[ri] != notas:
            raise ErrorEscritura(f"{pista}#m{j}: otra aparición de la misma región tiene otras notas")
        por_region[ri] = notas
    insercion = b""
    for k, (ri, notas) in enumerate(sorted(por_region.items())):
        g = regiones[ri]
        _, j2 = P._cadena(d, g.inicio + 2)
        _, desde, largo = P._tres_puntos(d, j2)
        desde = P._sin_cero(desde)
        fuente = struct.unpack_from("<I", d, g.fin)[0]
        ini, fin = chunks[fuente]
        insercion += _chunk_con_notas(d, ini, fin, notas, desde, largo)
        out[g.fin] = struct.pack("<I", len(chunks) + k)
    # regiones nuevas: un chunk cada una, copia de uno de la sesión (o de la plantilla)
    if chunks:
        base, ini, fin = d, chunks[0][0], chunks[0][1]
    else:
        base, ini, fin = _PLANTILLA_CHUNK, 0, len(_PLANTILLA_CHUNK)
    indices = []
    for notas, largo in nuevas:
        if any(n["tick"] >= largo for n in notas):
            raise ErrorEscritura("una nota empieza después del final del clip MIDI nuevo")
        indices.append(len(chunks) + len(por_region) + len(indices))
        insercion += _chunk_con_notas(base, ini, fin, notas, 0, 1 << 39)
    if not insercion:
        return [], out, indices
    reemplazos = [(b.inicio + 2, b.inicio + 6, struct.pack("<I", len(chunks) + len(por_region) + len(nuevas))),
                  (b.fin, b.fin, insercion, b.pos)]
    return reemplazos, out, indices


def _regiones_midi(d, bloques):
    lista = _primero(bloques, 0x2634) or _primero(bloques, 0x2002)
    return lista, [c for c in (lista.hijos if lista else []) if c.tipo_contenido in (0x2633, 0x2001)]


def _altas_midi(d, bloques, regiones, chunks, clips, bajas=None):
    """regiones: [{"nombre", "largo"}] nuevas, con su chunk en chunks[k];
    clips: {pista: [(región, tick)]}, región = índice existente o ("nueva", k).
    bajas: {pista: [índice del clip]} (orden por tick, como al leer): se quita su
    0x1056; la región queda en la lista de clips, como al borrar en Pro Tools.

    Verificado en Pro Tools 12 dibujando una nota y duplicando el clip: una
    región MIDI es un 0x2633 al final de 0x2634 (u32 cantidad en +2) con un
    0x2628 (nombre, desplazamiento CERO + 0 y largo en ticks con ancho variable)
    y el u32 del chunk de notas; cada aparición es un 0x1056 (con su 0x104F:
    región en +4 y tick + CERO en +9, 5 bytes) en el 0x1057 de la pista, que
    lleva la cantidad (u32) tras su nombre."""
    reemplazos = []
    lista, existentes = _regiones_midi(d, bloques)
    if regiones:
        if lista is None or lista.tipo_contenido != 0x2634:
            raise ErrorEscritura("la sesión no tiene lista de regiones MIDI de Pro Tools 12")
        plantilla = d[existentes[0].pos:existentes[0].fin] if existentes else _PLANTILLA_2633
        nuevas = b""
        for r, chunk in zip(regiones, chunks):
            nuevas += _region_midi(plantilla, r["nombre"], r["largo"], chunk)
        n = struct.unpack_from("<I", d, lista.inicio + 2)[0]
        reemplazos.append((lista.inicio + 2, lista.inicio + 6, struct.pack("<I", n + len(regiones))))
        punto = existentes[-1].fin if existentes else lista.inicio + 6
        reemplazos.append((punto, punto, nuevas, lista.pos))
    plantilla_col = next((d[e.pos:e.fin] for e in buscar(bloques, 0x1056) if e.tamano == 0x2B), _PLANTILLA_1056)
    mapa = _primero(bloques, 0x1058)
    bajas = bajas or {}
    n_reg = len(P.leer_regiones_midi(d, bloques))
    for pista in sorted(set(clips) | set(bajas)):
        lista_clips = clips.get(pista, [])
        pl = next((c for c in (mapa.hijos if mapa else []) if c.tipo_contenido == 0x1057
                   and P._cadena(d, c.inicio + 2)[0] == pista), None)
        if pl is None:
            raise ErrorEscritura(f"no se encontró la pista MIDI {pista!r}")
        col = sorted(((P._sin_cero(P._entero(d, f.inicio + 9, 5)), e) for e in pl.hijos
                      if e.tipo_contenido == 0x1056 for f in e.hijos if f.tipo_contenido == 0x104F
                      and struct.unpack_from("<I", d, f.inicio + 4)[0] < n_reg), key=lambda x: x[0])
        quitar = []
        for j in bajas.get(pista, []):
            if j >= len(col):
                raise ErrorEscritura(f"{pista}: no existe el clip MIDI {j}")
            e = col[j][1]
            quitar.append((e.pos, e.fin))
            reemplazos.append((e.pos, e.fin, b"", pl.pos))
        nuevos_col = []
        for region, tick in lista_clips:
            ri = len(existentes) + region[1] if isinstance(region, tuple) else region
            if tick < 0:
                raise ErrorEscritura(f"{pista}: posición negativa")
            col = bytearray(plantilla_col)
            f = 9                                   # 0x104F dentro del 0x1056
            col[f + 7 + 2] = 0                      # mute
            struct.pack_into("<I", col, f + 7 + 4, ri)
            col[f + 7 + 9:f + 7 + 14] = (int(tick) + CERO).to_bytes(5, "little")
            nuevos_col.append((int(tick) + CERO, bytes(col)))
        largo_nombre = struct.unpack_from("<I", d, pl.inicio + 2)[0]
        p_cant = pl.inicio + 6 + largo_nombre
        cantidad = struct.unpack_from("<I", d, p_cant)[0]
        reemplazos.append((p_cant, p_cant + 4, struct.pack("<I", cantidad + len(lista_clips) - len(quitar))))
        hijos = [h for h in pl.hijos if h.tipo_contenido == 0x1056]
        reemplazos += _insertar_ordenado(d, hijos, nuevos_col, quitar, p_cant + 4, pl.pos)
    return reemplazos


def _region_midi(plantilla, nombre, largo, chunk):
    """Bytes de un 0x2633 nuevo a partir de otro: nombre, desplazamiento CERO
    (5 bytes), largo y el u32 del chunk de notas."""
    b = bytearray(plantilla)
    g = 9                                           # 0x2628 dentro del 0x2633
    viejo, j = P._cadena(b, g + 7 + 2)
    b_off = (b[j + 1] >> 4)
    b_len = (b[j + 2] >> 4)
    b_ini = (b[j + 3] >> 4)
    valores_fin = j + 5 + b_off + b_len + b_ini
    n_len = max(1, (int(largo).bit_length() + 7) // 8)
    cab = bytes([b[j], (5 << 4) | (b[j + 1] & 0x0F), (n_len << 4) | (b[j + 2] & 0x0F), b[j + 3] & 0x0F, b[j + 4]])
    valores = CERO.to_bytes(5, "little") + int(largo).to_bytes(n_len, "little")
    nombre_b = nombre.encode("utf-8")
    g_fin = g + 7 + struct.unpack_from("<I", b, g + 3)[0]
    cont_2628 = (bytes(b[g + 7:g + 9]) + struct.pack("<I", len(nombre_b)) + nombre_b + cab + valores
                 + bytes(b[valores_fin:g_fin]))
    resto = bytearray(b[g_fin:])
    struct.pack_into("<I", resto, 0, chunk)
    cont = bytes(b[7:9]) + bytes(b[g:g + 3]) + struct.pack("<I", len(cont_2628)) + cont_2628 + bytes(resto)
    return bytes(b[:3]) + struct.pack("<I", len(cont)) + cont


def _entradas_playlist(d, bloques, nombre_playlist):
    """Bloques 0x1052 de los canales de una playlist de audio, en orden."""
    lista = _primero(bloques, 0x1015)
    for c in (lista.hijos if lista else []):
        if c.tipo_contenido != 0x1014:
            continue
        nombre, j = P._cadena(d, c.inicio + 2)
        if nombre == nombre_playlist:
            n = struct.unpack_from("<I", d, j + 1)[0]
            ids = struct.unpack_from(f"<{n}H", d, j + 5)
            mapa = _primero(bloques, 0x1054)
            entradas = [x for x in mapa.hijos if x.tipo_contenido == 0x1052]
            return [entradas[i] for i in ids]
    raise ErrorEscritura(f"no se encontró la playlist {nombre_playlist!r}")


def _plantilla_colocacion(d, bloques):
    """Bytes de un 0x1050 de región (no de fundido) de la sesión."""
    for e in buscar(bloques, 0x1050):
        f = next((h for h in e.hijos if h.tipo_contenido == 0x104F), None)
        if f is not None and d[f.fin] == 0 and e.tamano == 44:
            return bytes(d[e.pos:e.fin])
    raise ErrorEscritura("no hay una colocación de región que sirva de plantilla en esta sesión")


def _bytes_geometria(f):
    """Bloque 0x262F de una geometría de fundido: {"tipo", "pre_roll", "muestras",
    "pendiente", curvas}. Formato fijo en todo el corpus (16 792 geometrías):
    2F 26, 5 ceros, anchos en nibbles (bajo pre-roll, alto duración), 00 00,
    pre-roll y duración con el mínimo de bytes, y la cola de cada modo:
    entrada  modo, pendiente, 4 ceros, curva, 3 ceros;
    salida   modo, pendiente, 10 ceros, curva;
    cruzado  modo, pendiente, 10 ceros, curva de salida, 4 ceros, curva de entrada.
    Una curva es [tipo u8, preset i16] (0 estándar, 1 S, 2 preset)."""
    modos = {v: k for k, v in P._MODO_FUNDIDO.items()}
    pendientes = {v: k for k, v in P._PENDIENTE.items()}
    formas = {v: k for k, v in P._CURVA.items()}
    tipo = f.get("tipo")
    if tipo not in modos or f.get("pendiente") not in pendientes:
        raise ErrorEscritura(f"fundido no reconocido: {f}")

    def ancho(v):
        if not 0 <= v < 1 << 24:
            raise ErrorEscritura(f"fundido fuera de rango: {f}")
        return 0 if v == 0 else (v.bit_length() + 7) // 8

    def curva(c):
        c = c or {"forma": "estandar"}
        if c.get("forma") not in formas:
            raise ErrorEscritura(f"curva de fundido no reconocida: {c}")
        return bytes([formas[c["forma"]]]) + struct.pack("<h", int(c.get("preset", 0)))

    pre, dur = int(f["pre_roll"]), int(f["muestras"])
    wp, wt = ancho(pre), ancho(dur)
    cont = (b"\x2f\x26" + bytes(5) + bytes([wp | wt << 4]) + bytes(2)
            + pre.to_bytes(wp, "little") + dur.to_bytes(wt, "little")
            + bytes([modos[tipo], pendientes[f["pendiente"]]]))
    if tipo == "entrada":
        cont += bytes(4) + curva(f.get("curva")) + bytes(3)
    elif tipo == "salida":
        cont += bytes(10) + curva(f.get("curva"))
    else:
        cont += bytes(10) + curva(f.get("curva_salida")) + bytes(4) + curva(f.get("curva_entrada"))
    return b"\x5a\x02\x00" + struct.pack("<I", len(cont)) + cont


def _altas_fundidos(d, bloques, geometrias):
    """Agrega a 0x2630 las geometrías que no estén ya (iguales byte a byte) y
    devuelve (reemplazos, índice de cada una). El índice 0x0002 solo apunta a
    la lista, no a sus hijos."""
    lista = _primero(bloques, 0x2630)
    if lista is None:
        raise ErrorEscritura("la sesión no tiene lista de fundidos (0x2630)")
    hijos = [h for h in lista.hijos if h.tipo_contenido == 0x262F]
    if struct.unpack_from("<I", d, lista.inicio + 2)[0] != len(hijos):
        raise ErrorEscritura("lista de fundidos no reconocida")
    conocidas = {bytes(d[h.inicio:h.fin]): k for k, h in enumerate(hijos)}
    nuevas, indices = [], []
    for g in geometrias:
        cont = g[7:]
        if cont not in conocidas:
            conocidas[cont] = len(hijos) + len(nuevas)
            nuevas.append(g)
        indices.append(conocidas[cont])
    reemplazos = []
    if nuevas:
        reemplazos.append((lista.inicio + 2, lista.inicio + 6, struct.pack("<I", len(hijos) + len(nuevas))))
        punto = hijos[-1].fin if hijos else lista.inicio + 6
        reemplazos.append((punto, punto, b"".join(nuevas), lista.pos))
    return reemplazos, indices


def _altas_bajas_clips(d, bloques, pista, nuevos, borrados, fundidos=()):
    """nuevos: [(índices de región por canal, muestra)]; borrados: [índice del
    clip]; fundidos: [(índice de geometría, ancla)] nuevos, iguales en cada
    canal. Cada colocación es un 0x1050 (con su 0x104F: +18 mute, +20 índice de
    región o de fundido, +25 muestra, relativos al 0x1050; el byte que sigue al
    0x104F vale 1 en un fundido) dentro del 0x1052 de cada canal,
    que lleva la cantidad (u32) tras su nombre. Un clip borrado se lleva sus
    fundidos de entrada y salida; si tiene un fundido cruzado, da error."""
    entradas = _entradas_playlist(d, bloques, pista)
    canales = _colocaciones(d, bloques, pista)
    base = canales[0][0]
    plantilla = _plantilla_colocacion(d, bloques) if nuevos or fundidos else None
    reemplazos = []
    for k, (e, (regs, fun)) in enumerate(zip(entradas, canales)):
        quitar = []
        for j in borrados:
            ref = base[j]
            r = next(x for x in regs if x["muestra"] == ref["muestra"] and x["largo"] == ref["largo"])
            quitar.append((r["bloque"], r["fin_bloque"]))
            ini, fin = r["muestra"], r["muestra"] + r["largo"]
            for f in fun:
                if f["fin"] <= ini or f["inicio"] >= fin:
                    continue
                if f["tipo"] == "cruzado" or f["inicio"] < ini or f["fin"] > fin:
                    raise ErrorEscritura(f"{pista}#{j}: el clip tiene un fundido cruzado; quitarlo en Pro Tools")
                quitar.append((f["bloque"], f["fin_bloque"]))
        for a, b in quitar:
            reemplazos.append((a, b, b"", e.pos))
        largo_nombre = struct.unpack_from("<I", d, e.inicio + 2)[0]
        p_cant = e.inicio + 6 + largo_nombre
        cantidad = struct.unpack_from("<I", d, p_cant)[0]
        reemplazos.append((p_cant, p_cant + 4, struct.pack("<I", cantidad - len(quitar) + len(nuevos)
                                                          + len(fundidos))))
        hijos = [h for h in e.hijos if h.tipo_contenido == 0x1050]
        fin_vacio = p_cant + 4 + (2 if d[p_cant + 4:p_cant + 6] == bytes(2) and e.fin - (p_cant + 4) > 2 else 0)
        nuevos_col = []
        for indices, muestra in nuevos:
            if muestra < 0:
                raise ErrorEscritura(f"{pista}: posición negativa")
            col = bytearray(plantilla)
            col[18] = 0
            struct.pack_into("<I", col, 20, indices[k])
            col[25:30] = int(muestra).to_bytes(5, "little")
            col[16 + 17] = 3      # +17 del 0x104F: 3 en las regiones (22 715 de 23 121 en el corpus); con
                                  # 1, Pro Tools descarta al guardar el fundido de salida que termina en ella
            nuevos_col.append((int(muestra), bytes(col)))
        for indice, ancla in fundidos:
            col = bytearray(plantilla)
            col[18] = 0
            struct.pack_into("<I", col, 20, indice)
            col[25:30] = int(ancla).to_bytes(5, "little")
            col[16 + struct.unpack_from("<I", col, 12)[0]] = 1     # byte tras el 0x104F
            col[16 + 17] = 1      # +17 del 0x104F: 1 en los fundidos, 3 en las regiones (corpus)
            nuevos_col.append((int(ancla), bytes(col), True))
        reemplazos += _insertar_ordenado(d, hijos, nuevos_col, quitar, fin_vacio, e.pos)
    return reemplazos


def _pos_colocacion(d, e):
    """Muestra (audio) o tick + CERO (MIDI) del 0x104F de una colocación."""
    f = next(x for x in e.hijos if x.tipo_contenido == 0x104F)
    return P._entero(d, f.inicio + 9, 5)


def _es_fundido(d, e):
    f = next(x for x in e.hijos if x.tipo_contenido == 0x104F)
    return f.fin < e.fin and d[f.fin] == 1


def _insertar_ordenado(d, hijos, nuevos, quitar, punto_vacio, contenedor):
    """Inserta colocaciones nuevas [(posición, bytes[, es_fundido])] entre las
    existentes manteniendo el orden por posición: Pro Tools las guarda
    ordenadas, y si una queda fuera de orden pierde colocaciones al leer
    [verificado con MIDI]. En la misma posición, el fundido va antes que la
    región (como en las sesiones de Pro Tools)."""
    quedan = [h for h in hijos if not any(h.pos == a for a, _ in quitar)]
    grupos = {}
    nuevos = [(x[0], x[1], x[2] if len(x) > 2 else False) for x in nuevos]
    for pos, b, fun in sorted(nuevos, key=lambda x: (x[0], not x[2])):
        sig = next((h for h in quedan if _pos_colocacion(d, h) > pos
                    or (fun and _pos_colocacion(d, h) == pos and not _es_fundido(d, h))), None)
        if sig is not None:
            punto = sig.pos
        elif quedan:
            punto = quedan[-1].fin
        elif hijos:
            punto = hijos[-1].fin
        else:
            punto = punto_vacio
        grupos.setdefault(punto, b"")
        grupos[punto] += b
    return [(p, p, b, contenedor) for p, b in grupos.items()]


# ------------------------------------------------- archivos y regiones nuevas

_COLA_2628 = bytes.fromhex("feff00000000ffff04000400" + "00" * 21 + "ffffffff")
_COLA_2629 = bytes.fromhex("00000000803f0000803f0000000000000000ff")
_TIPO_ARCHIVO = {".wav": b"EVAW", ".aif": b"FFIA", ".aiff": b"FFIA"}


def info_wav(ruta):
    """(muestras, bits, canales, frecuencia) de un WAV (RIFF, chunks fmt y data)."""
    datos = Path(ruta).read_bytes()
    if datos[:4] != b"RIFF" or datos[8:12] != b"WAVE":
        raise ErrorEscritura(f"{ruta}: no es un WAV")
    p, fmt, largo_datos = 12, None, None
    while p + 8 <= len(datos):
        cid, tam = datos[p:p + 4], struct.unpack_from("<I", datos, p + 4)[0]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", datos, p + 8)
        elif cid == b"data":
            largo_datos = tam
        p += 8 + tam + (tam & 1)
    if not fmt or largo_datos is None:
        raise ErrorEscritura(f"{ruta}: WAV incompleto")
    _, canales, frecuencia, _, bloque, bits = fmt
    return largo_datos // bloque, bits, canales, frecuencia


def ids_wav(ruta):
    """Datos de un WAV que Pro Tools guarda en la sesión: {'umid': id de 8 bytes
    del chunk 'umid' (tras 00000000 2A000000), 'minf': FILETIME de 8 bytes del
    chunk 'minf', 'inicio_datos': posición del audio en el archivo, 'referencia':
    TimeReference del chunk 'bext' (muestras)}. umid y minf son None si el WAV no
    los tiene (los WAV que crea Pro Tools sí: la sesión debe usar los mismos o
    Pro Tools no la abre)."""
    datos = Path(ruta).read_bytes()
    out = {"umid": None, "minf": None, "inicio_datos": None, "referencia": 0}
    p = 12
    while p + 8 <= len(datos):
        cid, tam = datos[p:p + 4], struct.unpack_from("<I", datos, p + 4)[0]
        c = datos[p + 8:p + 8 + tam]
        if cid == b"umid" and len(c) >= 16 and c[4:8] == b"\x2a\0\0\0":
            out["umid"] = c[8:16]
        elif cid == b"minf" and len(c) >= 8:
            out["minf"] = c[:8]
        elif cid == b"bext" and len(c) >= 346:
            out["referencia"] = struct.unpack_from("<Q", c, 338)[0]
        elif cid == b"data":
            out["inicio_datos"] = p + 8
        p += 8 + tam + (tam & 1)
    return out


def _datos_2106(d, h, arch, variables):
    """Cambios en el 0x2106 (metadatos BWF) de un archivo nuevo: FILETIME en +31
    (el del chunk 'minf'), u32 posición del audio en el WAV en +41, y tras el
    texto 'Pro Tools' y 33 ceros: 01, un byte con (largo − 1) en el nibble alto,
    3 bytes, la referencia de tiempo (largo bytes) y otro FILETIME."""
    fijos = []
    if arch.get("minf") and d[h.inicio + 38] in (0x01, 0x02):
        fijos.append((h.inicio + 31, arch["minf"]))
    if arch.get("inicio_datos"):
        fijos.append((h.inicio + 41, struct.pack("<I", arch["inicio_datos"])))
    n = struct.unpack_from("<I", d, h.inicio + 51)[0]
    s = h.inicio + 55 + n
    if d[s:s + 33] == bytes(33) and d[s + 33] == 1:
        largo = (d[s + 34] >> 4) + 1
        ref = int(arch.get("referencia") or 0)
        nuevo_largo = max(1, (ref.bit_length() + 7) // 8)
        nuevo = bytes([(nuevo_largo - 1) << 4]) + d[s + 35:s + 38] + ref.to_bytes(nuevo_largo, "little")
        variables.append((s + 34, s + 38 + largo, nuevo))
        if arch.get("minf"):
            fijos.append((s + 38 + largo, arch["minf"]))
    return fijos


def _lista_rutas(d, a):
    """Entradas del 0x103A: (tipo, padre, nombre, extra). Tipo 0 = volumen,
    1 = carpeta, 2 = archivo; 'padre' es el índice de la entrada padre
    (FFFFFFFF en un volumen) y 'extra' 4 bytes (serie del volumen, 0 en una
    carpeta, tipo de archivo en un archivo)."""
    p = a.inicio + 2
    n = struct.unpack_from("<I", d, p)[0]
    p += 4
    out = []
    for _ in range(n):
        tipo, padre, largo = d[p], struct.unpack_from("<I", d, p + 1)[0], struct.unpack_from("<I", d, p + 5)[0]
        nombre = d[p + 9:p + 9 + largo]
        out.append((tipo, padre, nombre, d[p + 9 + largo:p + 13 + largo]))
        p += 13 + largo
    if p != a.fin:
        raise ErrorEscritura("lista de archivos de audio no reconocida en esta versión")
    return out


def _altas_archivos_regiones(d, bloques, archivos, regiones):
    """archivos: [{"ruta", "muestras", "bits", "frecuencia"}] (rutas absolutas de Windows);
    regiones: [{"nombre", "archivo" (índice en la sesión, o −(k+1) para el
    archivo nuevo k), "desde", "largo"}].

    Verificado importando un archivo en Pro Tools 12: el archivo suma una
    cadena de entradas al 0x103A (carpeta, archivo, volumen y carpetas hasta la
    raíz; cada entrada apunta a su padre por índice) y un 0x1003 (copia de otro,
    con número, frecuencia, largo y bits nuevos) al 0x1004, que lleva la cantidad en +2. Cada
    región es un 0x2629 (con su 0x2628: nombre, desplazamiento y largo) al final
    del 0x262A, que también lleva la cantidad. El índice 0x0002 no apunta a
    estos bloques."""
    reemplazos = []
    lista = _primero(bloques, 0x1004)
    regs = _primero(bloques, 0x262A)
    if lista is None or regs is None:
        raise ErrorEscritura("la sesión no tiene lista de archivos o de regiones")
    n_arch = struct.unpack_from("<I", d, lista.inicio + 2)[0]
    if archivos:
        a = next(h for h in lista.hijos if h.tipo_contenido == 0x103A)
        entradas = _lista_rutas(d, a)
        series = {e[2].lower(): e[3] for e in entradas if e[0] == 0}
        nuevas = b""
        k = len(entradas)
        tipo_de = lambda t, padre, nombre, extra: (bytes([t]) + struct.pack("<II", padre, len(nombre))
                                                   + nombre + extra)
        bloques_1003 = b""
        h1003 = [h for h in lista.hijos if h.tipo_contenido == 0x1003]
        if not h1003:
            raise ErrorEscritura("no hay un archivo de audio que sirva de plantilla en esta sesión")
        plantilla = h1003[0]
        usados = set()
        for arch in archivos:
            partes = arch["ruta"].replace("/", "\\").split("\\")
            if len(partes) < 3 or not partes[0].endswith(":"):
                raise ErrorEscritura(f"ruta de archivo no válida (debe ser absoluta): {arch['ruta']}")
            ext = Path(partes[-1]).suffix.lower()
            if ext not in _TIPO_ARCHIVO:
                raise ErrorEscritura(f"tipo de archivo no soportado: {partes[-1]}")
            vol = (partes[0].lower() + "\\").encode("utf-8")
            carpetas = partes[1:-1]
            i_carpeta, i_vol = k, k + 2
            nuevas += tipo_de(1, i_vol + len(carpetas) - 1 if len(carpetas) > 1 else i_vol,
                              carpetas[-1].encode("utf-8"), bytes(4))
            nuevas += tipo_de(2, i_carpeta, partes[-1].encode("utf-8"), _TIPO_ARCHIVO[ext])
            nuevas += tipo_de(0, 0xFFFFFFFF, vol, series.get(vol, bytes(4)))
            for j, c in enumerate(carpetas[:-1]):
                nuevas += tipo_de(1, i_vol + j, c.encode("utf-8"), bytes(4))
            k = i_vol + 1 + len(carpetas[:-1])
            numero = i_carpeta + 1          # el número del 0x1003 es la entrada del archivo en el 0x103A
            fijos, variables = [(plantilla.inicio + 2, struct.pack("<I", numero))], []
            for h in plantilla.hijos:
                if h.tipo_contenido == 0x1001:
                    fijos.append((h.inicio + 8, struct.pack("<Q", int(arch["muestras"]))))
                    if arch.get("bits") in (16, 24, 32):
                        fijos.append((h.inicio + 7, bytes([arch["bits"]])))
                    if arch.get("frecuencia"):            # u32 frecuencia de muestreo en +2
                        fijos.append((h.inicio + 2, struct.pack("<I", int(arch["frecuencia"]))))
                    q = d.find(b"\x2a\0\0\0", h.inicio, h.fin)    # identificador propio del archivo
                    if q > 0 and q + 12 <= h.fin:
                        nuevo_id = arch.get("umid") or _id_nuevo(d, d[q + 4:q + 12], usados)
                        usados.add(nuevo_id)
                        fijos.append((q + 4, nuevo_id))
                elif h.tipo_contenido == 0x2106 and h.hijos:
                    fijos.append((h.hijos[0].pos - 16, os.urandom(16)))
                    fijos += _datos_2106(d, h, arch, variables)
            b = _rehacer(d, plantilla, fijos, variables)
            bloques_1003 += bytes(b)
        total = len(entradas) + sum(len(x["ruta"].replace("/", "\\").split("\\")) for x in archivos)
        reemplazos.append((a.inicio + 2, a.inicio + 6, struct.pack("<I", total)))
        reemplazos.append((a.fin, a.fin, nuevas, a.pos))
        reemplazos.append((lista.inicio + 2, lista.inicio + 6, struct.pack("<I", n_arch + len(archivos))))
        reemplazos.append((h1003[-1].fin, h1003[-1].fin, bloques_1003, lista.pos))
    if regiones:
        n_reg = struct.unpack_from("<I", d, regs.inicio + 2)[0]
        nuevas = b""
        # clip gain: un registro nuevo al final del diccionario 0x2637 por región
        # con ganancia (u32 cantidad; 01460100, u32 tamaño, 0000, u32 nodos,
        # 0400, u16 nodos − 1, u32 0 y los nodos [u32 muestra, float32 dB])
        dic = _primero(bloques, 0x2637)
        registros = b""
        n_dic = struct.unpack_from("<I", d, dic.inicio + 2)[0] if dic is not None else 0
        con_ganancia = 0
        for r in regiones:
            nombre = r["nombre"].encode("utf-8")
            desde, largo = int(r.get("desde", 0)), int(r["largo"])
            w_off = (max(desde.bit_length(), 1) + 7) // 8 if desde else 0
            w_len = (largo.bit_length() + 7) // 8
            cola = _COLA_2628
            if r.get("ganancia"):
                if dic is None:
                    raise ErrorEscritura("la sesión no tiene el diccionario de clip gain (0x2637)")
                nodos = r["ganancia"]
                registros += (b"\x01\x46\x01\x00" + struct.pack("<I", 14 + 8 * len(nodos)) + b"\0\0"
                              + struct.pack("<I", len(nodos)) + b"\x04\x00"
                              + struct.pack("<HI", len(nodos) - 1, 0)
                              + b"".join(struct.pack("<If", int(x["muestra"]), float(x["db"])) for x in nodos))
                cola = cola[:-4] + struct.pack("<i", n_dic + con_ganancia)
                con_ganancia += 1
            datos = (struct.pack("<I", len(nombre)) + nombre + bytes([0, w_off << 4, w_len << 4, 0, 0])
                     + (desde.to_bytes(w_off, "little") if w_off else b"") + largo.to_bytes(w_len, "little")
                     + cola)
            b2628 = b"\x5a\x01\x00" + struct.pack("<I", 2 + len(datos)) + b"\x28\x26" + datos
            arch = r["archivo"] if r["archivo"] >= 0 else n_arch - r["archivo"] - 1
            cont = b"\x29\x26" + b2628 + struct.pack("<I", arch) + _COLA_2629
            nuevas += b"\x5a\x02\x00" + struct.pack("<I", len(cont)) + cont
        ultimo = [h for h in regs.hijos if h.tipo_contenido in (0x2629, 0x1008)]
        punto = ultimo[-1].fin if ultimo else regs.fin
        reemplazos.append((regs.inicio + 2, regs.inicio + 6, struct.pack("<I", n_reg + len(regiones))))
        reemplazos.append((punto, punto, nuevas, regs.pos))
        if registros:
            reemplazos.append((dic.inicio + 2, dic.inicio + 6, struct.pack("<I", n_dic + con_ganancia)))
            reemplazos.append((dic.fin, dic.fin, registros))
    return reemplazos


# ------------------------------------------------------------------- mezcla

def _canal_mezcla(d, bloques, indice):
    raiz = _primero(bloques, 0x2624)
    if not raiz or indice >= len(raiz.hijos):
        raise ErrorEscritura(f"no hay canal de mezcla para la pista {indice}")
    sb = next((h for h in raiz.hijos[indice].hijos if h.tipo_contenido == 0x261B), None)
    fader = next((h for h in sb.hijos if h.tipo_contenido == 0x260D), None) if sb else None
    nivel = next((h for h in fader.hijos if h.tipo_contenido == 0x1029), None) if fader else None
    if not nivel:
        raise ErrorEscritura(f"el canal {indice} no tiene fader")
    return fader, nivel


SILENCIO = -1440          # décimas de dB: -144 dB, el mínimo que usa Pro Tools en la automatización


def _automatizacion(d, lista, nuevo, viejo, nombre, out, minimo=None, maximo=None):
    """Actualiza la lista de automatización del parámetro.

    Un solo punto: es la copia del valor fijo y toma el valor nuevo. Varios
    puntos: el parámetro está automatizado (Pro Tools ignora el valor fijo) y
    se desplaza toda la curva lo que cambió el valor, dentro de [minimo,
    maximo]; los puntos en silencio (volumen ≤ −144 dB) no se mueven. Sin
    rango (mute) la curva no se puede desplazar y da error."""
    if lista is None:
        return
    p = lista.inicio
    n = struct.unpack_from("<I", d, p + 12)[0]
    ancho = struct.unpack_from("<H", d, p + 16)[0]
    if n == 1:
        out[p + 28] = int(nuevo).to_bytes(ancho, "little", signed=True)
        return
    if n == 0:
        return
    if minimo is None:
        raise ErrorEscritura(f"{nombre} tiene automatización; cambiarlo aún no está soportado")
    delta = nuevo - viejo
    q = p + 24
    for _ in range(n):
        v = int.from_bytes(d[q + 4:q + 4 + ancho], "little", signed=True)
        if not (minimo == SILENCIO and v <= SILENCIO):
            v = max(minimo, min(maximo, v + delta))
            out[q + 4] = v.to_bytes(ancho, "little", signed=True)
        q += 4 + ancho


def _cambios_mezcla(d, bloques, indice, nombre, cambio, out):
    fader, nivel = _canal_mezcla(d, bloques, indice)
    directas = [h for h in fader.hijos if h.tipo_contenido == 0x260A]
    paneos = [k for h in fader.hijos if h.tipo_contenido == 0x260C
              for k in h.hijos if k.tipo_contenido == 0x260A]
    i32 = lambda off: struct.unpack_from("<i", d, nivel.inicio + off)[0]
    if "volumen_db" in cambio:
        v = round(cambio["volumen_db"] * 10)
        if not SILENCIO <= v <= 120:
            raise ErrorEscritura(f"{nombre}: volumen fuera de rango (−144 a +12 dB)")
        out[nivel.inicio + 3] = struct.pack("<i", v)
        _automatizacion(d, directas[0] if directas else None, v, i32(3), f"{nombre}: volumen", out,
                        SILENCIO, 120)
    if "mute" in cambio:
        v = 1 if cambio["mute"] else 0
        out[nivel.inicio + 7] = bytes([v])
        _automatizacion(d, directas[1] if len(directas) > 1 else None, v, d[nivel.inicio + 7],
                        f"{nombre}: mute", out)
    if "paneo" in cambio:
        lados = cambio["paneo"] if isinstance(cambio["paneo"], list) else [cambio["paneo"]]
        for k, (off, valor) in enumerate(zip((15, 43), lados)):
            v = round(valor * 100)
            if not -100 <= v <= 100:
                raise ErrorEscritura(f"{nombre}: paneo fuera de rango")
            if v == i32(off):
                continue
            out[nivel.inicio + off] = struct.pack("<i", v)
            _automatizacion(d, paneos[k] if k < len(paneos) else None, v, i32(off), f"{nombre}: paneo", out,
                            -100, 100)


# --------------------------------------------------------------- marcadores

def _cambios_marcadores(d, bloques, cambios):
    """cambios: {número: {"nombre"?, "tick"?, "fin_tick"?, "comentario"?}}. Cada marcador está
    en las dos listas 0x2030 de la sesión; se cambian ambas copias. El nombre
    cambia el tamaño del bloque, por eso devuelve reemplazos para reconstruir().

    Carga del 0x2077: u16 número, +3 banderas (bit 1 = selección), u32 largo en
    +6 y nombre desde +10; al final del nombre (E): inicio en E y fin en E+8
    (5 bytes de tick + 10**12 cada uno)."""
    reemplazos, vistos = [], {}
    for b in buscar(bloques, 0x2077):
        p = b.inicio + 2
        numero = struct.unpack_from("<H", d, p)[0]
        if numero not in cambios:
            continue
        vistos[numero] = vistos.get(numero, 0) + 1
        cambio = cambios[numero]
        seleccion = d[p + 3] & 2
        largo = struct.unpack_from("<I", d, p + 6)[0]
        e = p + 10 + largo
        if "nombre" in cambio:
            nombre = cambio["nombre"].encode("utf-8")
            reemplazos.append((p + 6, e, struct.pack("<I", len(nombre)) + nombre))
        if "comentario" in cambio:
            # El comentario va dos veces: u32 largo + texto en E+80 y 4 bytes
            # después del último hijo. Solo se escribe si ambas copias coinciden
            # (en otras versiones E+80 no es el comentario).
            n1 = struct.unpack_from("<I", d, e + 80)[0] if e + 84 <= b.fin else -1
            q = b.hijos[-1].fin + 4 if b.hijos else b.fin
            n2 = struct.unpack_from("<I", d, q)[0] if q + 4 <= b.fin else -2
            if n1 != n2 or d[e + 84:e + 84 + n1] != d[q + 4:q + 4 + n2]:
                raise ErrorEscritura(f"marcador {numero}: formato de comentario no reconocido en esta versión")
            texto = (cambio["comentario"] or "").encode("utf-8")
            nuevo = struct.pack("<I", len(texto)) + texto
            reemplazos.append((e + 80, e + 84 + n1, nuevo))
            reemplazos.append((q, q + 4 + n2, nuevo))
        if "tick" in cambio or "fin_tick" in cambio:
            ini = cambio.get("tick", int.from_bytes(d[e:e + 5], "little") - CERO)
            fin = cambio.get("fin_tick", int.from_bytes(d[e + 8:e + 13], "little") - CERO) if seleccion else ini
            if ini < 0 or fin < ini:
                raise ErrorEscritura(f"marcador {numero}: posición inválida")
            reemplazos.append((e, e + 5, (ini + CERO).to_bytes(5, "little")))
            reemplazos.append((e + 8, e + 13, (fin + CERO).to_bytes(5, "little")))
    faltan = set(cambios) - set(vistos)
    if faltan:
        raise ErrorEscritura(f"no se encontraron los marcadores {sorted(faltan)}")
    return reemplazos


# Marcador de Pro Tools 12.5 (sesión e11 de las pruebas), para sesiones sin
# marcadores que sirvan de plantilla.
_PLANTILLA_MARCADOR_12 = bytes.fromhex(
    "5a0c0025010000772001000101000004000000436f726f00a8dfd4e800004000a8dfd4e800004000"
    "0000000000f0bfffffffffffffffffffffffffffbfffffffffffffffbf0000000000000040000000"
    "0000000040ffffffff040000000000000002000000000014000000636f6d656e746172696f206465"
    "20707275656261ffffffff00000000ffffffff000000000000000000000000ffffffff0000000000"
    "00000000000000feff040000005a03000a0000000625ffffffff000000005a03000a0000000625ff"
    "ffffff000000005a03000a0000000625ffffffff000000005a03000a0000000625ffffffff000000"
    "000000000014000000636f6d656e746172696f206465207072756562610000000000000000000000"
    "0000ffff17f61820b440bf4385f83568bb7fadc7")


# Selección (ubicación de memoria con inicio y fin) de Pro Tools 12.5 (sesión
# e12 de las pruebas), para sesiones sin selecciones que sirvan de plantilla.
_PLANTILLA_SELECCION_12 = bytes.fromhex(
    "5a0c00ff0000007720020001020000060000005075656e746500401ad5e8000040008c37d5e80000"
    "40000000000000f0bfffffffffffffffffffffffffffbfffffffffffffffbf000000000000004000"
    "00000000000040ffffffff04000000e3e3a3a302000000000000000000ffffffff00000000ffffff"
    "ff000000000000000000000000ffffffff000000000000000000000000feff040000005a03000a00"
    "0000062500030000610000005a03000a000000062500030000610000005a03000a00000006250003"
    "0000610000005a03000a000000062500030000610000000000000000000000000000000000000000"
    "00000000ffff952eb1a5a684e0408cecfcfd35d3b719")


def _estructura_marcador(tb):
    """Offsets dentro de los bytes de un bloque 0x2077 completo (cabecera incluida):
    (E, n1, q, n2) o None si el comentario no está en las dos copias esperadas."""
    p = 9
    largo = struct.unpack_from("<I", tb, p + 6)[0]
    e = p + 10 + largo
    b = _parsear(tb, 0, len(tb))
    if b is None or not b.hijos or e + 84 > len(tb):
        return None
    n1 = struct.unpack_from("<I", tb, e + 80)[0]
    q = b.hijos[-1].fin + 4
    if q + 4 > len(tb):
        return None
    n2 = struct.unpack_from("<I", tb, q)[0]
    if n1 != n2 or tb[e + 84:e + 84 + n1] != tb[q + 4:q + 4 + n2]:
        return None
    return e, n1, q, n2


def _marcador_nuevo(tb, numero, nombre, tick, comentario, fin_tick=None, n_pistas=None):
    """Bloque 0x2077 nuevo a partir de la plantilla tb (un marcador común, o
    una selección si lleva fin_tick)."""
    e, n1, q, n2 = _estructura_marcador(tb)
    p = 9
    nombre = nombre.encode("utf-8")
    texto = (comentario or "").encode("utf-8")
    com = struct.pack("<I", len(texto)) + texto
    pos = (tick + CERO).to_bytes(5, "little")
    medio = bytearray(tb[e:e + 80])
    medio[0:5] = pos
    medio[8:13] = pos if fin_tick is None else (fin_tick + CERO).to_bytes(5, "little")
    out = bytearray(tb[:p] + struct.pack("<H", numero) + tb[p + 2:p + 6]
                    + struct.pack("<I", len(nombre)) + nombre + medio + com
                    + tb[e + 84 + n1:q] + com + tb[q + 4 + n2:-16] + os.urandom(16))
    if fin_tick is not None and n_pistas is not None:
        # una selección lleva una entrada 0x2506 por pista, tras «FEFF u32 cantidad»
        blq = _parsear(bytes(out), 0, len(out))
        hijos = [h for h in blq.hijos if h.tipo_contenido == 0x2506] if blq else []
        if hijos and hijos[-1].fin - hijos[0].pos == sum(h.fin - h.pos for h in hijos) \
                and struct.unpack_from("<I", out, hijos[0].pos - 4)[0] == len(hijos):
            uno = bytes(out[hijos[0].pos:hijos[0].fin])
            out = (out[:hijos[0].pos - 4] + struct.pack("<I", n_pistas) + uno * n_pistas
                   + out[hijos[-1].fin:])
    struct.pack_into("<I", out, 3, len(out) - 7)
    return bytes(out)


def _altas_bajas_marcadores(d, bloques, nuevos, borrados):
    """nuevos: [{"nombre", "tick", "fin_tick"? (selección), "comentario"?}]; borrados: [número].
    Cada marcador va en las dos listas 0x2030 (u32 cantidad en +2, hijos 0x2077 y
    4 bytes finales). Devuelve reemplazos para reconstruir()."""
    listas = [b for b in buscar(bloques, 0x2030)
              if b.tamano >= 10 and d[b.fin - 4:b.fin] == bytes(4)]
    con_marcas = [b for b in buscar(bloques, 0x2030) if any(h.tipo_contenido == 0x2077 for h in b.hijos)]
    if len(listas) != 2 or any(b not in listas for b in con_marcas):
        raise ErrorEscritura("estructura de las listas de marcadores no reconocida en esta versión")
    plantilla = None
    for h in listas[0].hijos:
        tb = d[h.pos:h.fin]
        if h.tipo_contenido == 0x2077 and not d[h.inicio + 5] & 2 and _estructura_marcador(tb):
            plantilla = tb
            break
    if plantilla is None and any(m.get("fin_tick") is None for m in nuevos):
        if not any(h.tipo_contenido == 0x2077 and not d[h.inicio + 5] & 2 for h in listas[0].hijos):
            plantilla = _PLANTILLA_MARCADOR_12
        else:
            raise ErrorEscritura("no hay un marcador que sirva de plantilla en esta sesión")
    # selecciones (bit 1 de las banderas): copia de una selección de la sesión
    # o de una de Pro Tools 12.5
    plantilla_sel = next((d[h.pos:h.fin] for h in listas[0].hijos if h.tipo_contenido == 0x2077
                          and d[h.inicio + 5] & 2 and _estructura_marcador(d[h.pos:h.fin])),
                         _PLANTILLA_SELECCION_12)
    numeros = [struct.unpack_from("<H", d, h.inicio + 2)[0] for L in listas for h in L.hijos
               if h.tipo_contenido == 0x2077]
    siguiente = max(numeros, default=0) + 1
    bloques_nuevos = b""
    for m in nuevos:
        if m.get("fin_tick") is not None:
            if m["fin_tick"] < m["tick"]:
                raise ErrorEscritura(f"selección {m['nombre']!r}: el fin va antes del inicio")
            L = _primero(bloques, 0x2519)
            n_pistas = len([h for h in L.hijos if h.tipo_contenido == 0x251A]) // 2 if L else None
            bloques_nuevos += _marcador_nuevo(plantilla_sel, siguiente, m["nombre"], m["tick"],
                                              m.get("comentario"), m["fin_tick"], n_pistas)
        else:
            bloques_nuevos += _marcador_nuevo(plantilla, siguiente, m["nombre"], m["tick"], m.get("comentario"))
        m["numero"] = siguiente
        siguiente += 1
    reemplazos = []
    for L in listas:
        hijos = [h for h in L.hijos if h.tipo_contenido == 0x2077]
        quitar = [h for h in hijos if struct.unpack_from("<H", d, h.inicio + 2)[0] in borrados]
        for h in quitar:
            reemplazos.append((h.pos, h.fin, b"", L.pos))
        cantidad = struct.unpack_from("<I", d, L.inicio + 2)[0]
        reemplazos.append((L.inicio + 2, L.inicio + 6, struct.pack("<I", cantidad - len(quitar) + len(nuevos))))
        if bloques_nuevos:
            reemplazos.append((L.fin - 4, L.fin - 4, bloques_nuevos, L.pos))
    return reemplazos


# ------------------------------------------------------------------ nombres

def _cadena_en(d, p, texto):
    """Reemplazo de la cadena (u32 largo + texto) que empieza en p."""
    n = struct.unpack_from("<I", d, p)[0]
    nuevo = texto.encode("utf-8")
    return (p, p + 4 + n, struct.pack("<I", len(nuevo)) + nuevo)


def _renombrar_pistas(d, bloques, nombres):
    """nombres: {nombre actual: nombre nuevo}. Lugares donde Pro Tools guarda el
    nombre de una pista (verificado renombrando en Pro Tools 12):
    - lista de pistas 0x2519: en sus bytes propios (u32 largo + nombre) y en
      cada 0x251A (nombre en +4; la lista viene dos veces);
    - audio: la playlist activa 0x1014 (nombre en +2) y las entradas 0x1052
      de sus canales (nombre en +2); MIDI: la playlist 0x1057 (nombre en +2);
    - auxiliar o master: el nombre del canal (0x102D → 0x2619, en +2). En las
      pistas de audio ese nombre es el de creación y Pro Tools no lo cambia."""
    reemplazos = []
    viejos = {v.encode("utf-8"): v for v in nombres}
    lista = buscar(bloques, 0x2519)
    if not lista:
        raise ErrorEscritura("no se encontró la lista de pistas")
    lista = lista[0]
    fin_propios = lista.hijos[0].pos if lista.hijos else lista.fin
    for viejo_b, viejo in viejos.items():
        clave = struct.pack("<I", len(viejo_b)) + viejo_b
        k = d.find(clave, lista.inicio, fin_propios)
        if k < 0 or d.find(clave, k + 1, fin_propios) >= 0:
            raise ErrorEscritura(f"no se pudo ubicar la pista {viejo!r} en la lista de pistas")
        reemplazos.append(_cadena_en(d, k, nombres[viejo]))
    tipos = {}
    for b in buscar(bloques, 0x251A):
        nombre = P._cadena(d, b.inicio + 4)[0]
        if nombre in nombres:
            tipos[nombre] = struct.unpack_from("<H", d, b.inicio + 2)[0]
            reemplazos.append(_cadena_en(d, b.inicio + 4, nombres[nombre]))
    mapa = _primero(bloques, 0x1054)
    entradas = [c for c in mapa.hijos if c.tipo_contenido == 0x1052] if mapa else []
    for b in buscar(bloques, 0x1014):
        nombre, j = P._cadena(d, b.inicio + 2)
        if nombre not in nombres:
            continue
        reemplazos.append(_cadena_en(d, b.inicio + 2, nombres[nombre]))
        n = struct.unpack_from("<I", d, j + 1)[0]
        for i in struct.unpack_from(f"<{n}H", d, j + 5):
            e = entradas[i]
            if P._cadena(d, e.inicio + 2)[0] == nombre:
                reemplazos.append(_cadena_en(d, e.inicio + 2, nombres[nombre]))
    for b in buscar(bloques, 0x1057):
        nombre = P._cadena(d, b.inicio + 2)[0]
        if nombre in nombres:
            reemplazos.append(_cadena_en(d, b.inicio + 2, nombres[nombre]))
    raiz = _primero(bloques, 0x2624)
    for canal in (raiz.hijos if raiz else []):
        if canal.tipo_contenido == 0x261C:          # audio: nombre de creación, no cambia
            continue
        for cab in buscar([canal], 0x102D):
            for nb in cab.hijos[:1]:
                nombre = P._cadena(d, nb.inicio + 2)[0]
                if nombre in nombres:
                    reemplazos.append(_cadena_en(d, nb.inicio + 2, nombres[nombre]))
    return reemplazos


# ------------------------------------------------------------------- tempo

def _cambios_tempo(d, bloques, tempo):
    """Reescribe el mapa de tempo en las dos copias del bloque 0x2028 (verificado
    agregando un cambio de tempo en Pro Tools 12). Tras 'Tempo' 02 00: u32 largo
    (4 + 61 por evento), u32 cantidad, 4 bytes, eventos de 61 bytes ('Const' …
    'TMS'; tick int64 + 10**12 en +30, BPM double en +40, figura del pulso u32 en
    +49, y dos banderas en +21 y +39 que valen 1 en el primer evento) y 13 bytes
    finales. Los eventos existentes se reutilizan en orden; los nuevos se copian
    de un evento que no sea el primero (o del primero, con las banderas en 0)."""
    if not tempo or tempo[0]["tick"] != 0:
        raise ErrorEscritura("el primer tempo debe estar en el tick 0")
    reemplazos = []
    for b in buscar(bloques, 0x2028):
        m = d.find(b"Tempo\x02\x00", b.inicio, b.fin)
        if m < 0:
            raise ErrorEscritura("bloque de tempo no reconocido")
        m += 7
        largo, n = struct.unpack_from("<II", d, m)
        ini = m + 12
        if largo != 4 + 61 * n or b.fin - (ini + 61 * n) != 13 or d[ini:ini + 5] != b"Const":
            raise ErrorEscritura("bloque de tempo con otra estructura en esta versión")
        viejos = [bytearray(d[ini + 61 * k:ini + 61 * (k + 1)]) for k in range(n)]
        if len(viejos) > 1:
            otro = viejos[1]
        else:
            otro = bytearray(viejos[0])
            otro[21] = otro[39] = 0
        cuerpo = b""
        for k, t in enumerate(sorted(tempo, key=lambda x: x["tick"])):
            if not 5 <= t["bpm"] <= 500:
                raise ErrorEscritura(f"BPM fuera de rango (5 a 500): {t['bpm']}")
            ev = bytearray(viejos[k] if k < n else otro)
            struct.pack_into("<q", ev, 30, t["tick"] + CERO)
            struct.pack_into("<d", ev, 40, float(t["bpm"]))
            cuerpo += ev
        k = len(tempo)
        reemplazos.append((m, ini + 61 * n, struct.pack("<II", 4 + 61 * k, k) + d[m + 8:ini] + cuerpo))
    return reemplazos


# ----------------------------------------------------------------- compases

_RESTO_COMPAS = bytes.fromhex("01000300000000000200000003000000")


def _cambios_compases(d, bloques, compases):
    """Reescribe la lista de compases en las dos copias del bloque 0x2029
    (verificado agregando compases en Pro Tools 12). Tras 'Meter' 02 00:
    u32 largo del resto, u32 cantidad, eventos de 36 bytes (int64 tick + 10**12,
    u32 compás, u32 numerador, u32 denominador y 16 bytes fijos), una tabla de
    16 bytes por evento (u32 compás mostrado, u32 1, 8 bytes en cero) y 8 bytes
    finales. Sin eventos, Pro Tools usa 4/4."""
    eventos, compas, previo = [], 1, None
    for c in sorted(compases, key=lambda x: x["tick"]):
        num, den = int(c["numerador"]), int(c["denominador"])
        if not (1 <= num <= 99 and den in (1, 2, 4, 8, 16, 32, 64)):
            raise ErrorEscritura(f"compás inválido: {num}/{den}")
        if previo is None:
            if c["tick"] != 0:
                raise ErrorEscritura("el primer compás debe empezar en el tick 0")
        else:
            largo = previo[1] * 4 * 960_000 // previo[2]
            dif = c["tick"] - previo[0]
            if dif <= 0 or dif % largo:
                raise ErrorEscritura(f"el cambio de compás en el tick {c['tick']} no cae al inicio de un compás")
            compas += dif // largo
        eventos.append((c["tick"], compas, num, den))
        previo = (c["tick"], num, den)
    reemplazos = []
    for b in buscar(bloques, 0x2029):
        m = d.find(b"Meter\x02\x00", b.inicio, b.fin)
        if m < 0:
            raise ErrorEscritura("bloque de compases no reconocido")
        m += 7
        n = struct.unpack_from("<I", d, m + 4)[0]
        fin = m + 8 + 36 * n + 16 * n + 8
        if fin != b.fin or struct.unpack_from("<I", d, m)[0] != fin - m - 4:
            raise ErrorEscritura("bloque de compases con otra estructura en esta versión")
        resto = d[m + 8 + 20:m + 8 + 36] if n else _RESTO_COMPAS
        desfase = (struct.unpack_from("<i", d, m + 8 + 36 * n)[0]
                   - struct.unpack_from("<I", d, m + 8 + 8)[0]) if n else 0
        cuerpo = struct.pack("<I", len(eventos))
        for tick, compas, num, den in eventos:
            cuerpo += struct.pack("<qIII", tick + CERO, compas, num, den) + resto
        for _, compas, _, _ in eventos:
            cuerpo += struct.pack("<iIQ", compas + desfase, 1, 0)
        cuerpo += d[fin - 8:fin]
        reemplazos.append((m, fin, struct.pack("<I", len(cuerpo)) + cuerpo))
    return reemplazos


# ------------------------------------------------------------------ entrada

def escribir_cambios(plantilla, salida, mezcla=None, regiones=None, marcadores=None, tempo=None, midi=None,
                     marcadores_nuevos=None, marcadores_borrados=None, nombres=None, compases=None,
                     notas=None, clips_nuevos=None, clips_borrados=None, archivos_nuevos=None,
                     regiones_nuevas=None, midi_nuevos=None, fundidos_nuevos=None):
    """mezcla: {índice de pista: (nombre, {"volumen_db", "paneo", "mute"})}.
    regiones: {nombre de playlist: {índice de clip: {"muestra", "mute"}}}.
    marcadores: {número: {"nombre", "tick", "fin_tick"}}.
    midi_nuevos: {"regiones": [{"nombre", "notas", "largo"}], "clips": {pista: [(región, tick)]}}
    (región: índice de una región MIDI existente o ("nueva", k)).
    fundidos_nuevos: {pista: [{"tipo", "pre_roll", "muestras", "pendiente",
    curvas, "muestra"}]} (muestra = inicio del fundido; ancla = muestra + pre_roll).
    Escribe una copia cifrada en 'salida' (que no debe existir)."""
    salida = Path(salida)
    if salida.exists():
        raise ErrorEscritura(f"{salida} ya existe")
    d = leer_descifrado(plantilla)
    bloques = arbol(d)
    out = {}
    for pista, cambios in (regiones or {}).items():
        _cambios_regiones(d, bloques, pista, cambios, out)
    for pista, cambios in (midi or {}).items():
        _cambios_midi(d, bloques, pista, cambios, out)
    for indice, (nombre, cambio) in (mezcla or {}).items():
        _cambios_mezcla(d, bloques, indice, nombre, cambio, out)
    reemplazos_notas = []
    midi_nuevos = midi_nuevos or {}
    regiones_midi = midi_nuevos.get("regiones", [])
    if notas or regiones_midi:
        reemplazos_notas, out_notas, chunks_nuevos = _cambios_notas(
            d, bloques, notas or {}, [(r["notas"], r["largo"]) for r in regiones_midi])
        out.update(out_notas)
    if midi_nuevos.get("clips") or midi_nuevos.get("bajas"):
        reemplazos_notas += _altas_midi(d, bloques, regiones_midi, chunks_nuevos if regiones_midi else [],
                                        midi_nuevos.get("clips", {}), midi_nuevos.get("bajas"))
    nuevo = bytearray(d)
    for pos, b in out.items():
        nuevo[pos:pos + len(b)] = b
    reemplazos = _cambios_marcadores(d, bloques, marcadores) if marcadores else []
    if nombres:
        reemplazos += _renombrar_pistas(d, bloques, nombres)
    if compases is not None:
        reemplazos += _cambios_compases(d, bloques, compases)
    if tempo is not None:
        reemplazos += _cambios_tempo(d, bloques, tempo)
    reemplazos += reemplazos_notas
    if archivos_nuevos or regiones_nuevas:
        reemplazos += _altas_archivos_regiones(d, bloques, archivos_nuevos or [], regiones_nuevas or [])
    fundidos_nuevos = fundidos_nuevos or {}
    por_pista = {}
    if fundidos_nuevos:
        todos = [(pista, f) for pista, fs in fundidos_nuevos.items() for f in fs]
        r, indices = _altas_fundidos(d, bloques, [_bytes_geometria(f) for _, f in todos])
        reemplazos += r
        for (pista, f), k in zip(todos, indices):
            por_pista.setdefault(pista, []).append((k, int(f["muestra"]) + int(f["pre_roll"])))
    for pista in set(clips_nuevos or {}) | set(clips_borrados or {}) | set(por_pista):
        reemplazos += _altas_bajas_clips(d, bloques, pista, (clips_nuevos or {}).get(pista, []),
                                         (clips_borrados or {}).get(pista, []), por_pista.get(pista, []))
    if marcadores_nuevos or marcadores_borrados:
        reemplazos += _altas_bajas_marcadores(d, bloques, marcadores_nuevos or [], marcadores_borrados or [])
    nuevo = reconstruir(bytes(nuevo), reemplazos) if reemplazos else bytes(nuevo)
    salida.write_bytes(cifrar(nuevo))
    return salida

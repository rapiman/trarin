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
                                    "fin": muestra - g["pre_roll"] + g["muestras"], "tipo": g["tipo"]})
                elif ri < len(regiones):
                    regs.append({"pos": f.inicio, "muestra": muestra, "largo": regiones[ri]["muestras"]})
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


def _marcador_nuevo(tb, numero, nombre, tick, comentario):
    """Bloque 0x2077 nuevo a partir de la plantilla tb (un marcador común)."""
    e, n1, q, n2 = _estructura_marcador(tb)
    p = 9
    nombre = nombre.encode("utf-8")
    texto = (comentario or "").encode("utf-8")
    com = struct.pack("<I", len(texto)) + texto
    pos = (tick + CERO).to_bytes(5, "little")
    medio = bytearray(tb[e:e + 80])
    medio[0:5] = pos
    medio[8:13] = pos
    out = bytearray(tb[:p] + struct.pack("<H", numero) + tb[p + 2:p + 6]
                    + struct.pack("<I", len(nombre)) + nombre + medio + com
                    + tb[e + 84 + n1:q] + com + tb[q + 4 + n2:-16] + os.urandom(16))
    struct.pack_into("<I", out, 3, len(out) - 7)
    return bytes(out)


def _altas_bajas_marcadores(d, bloques, nuevos, borrados):
    """nuevos: [{"nombre", "tick", "comentario"?}]; borrados: [número].
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
    if plantilla is None and nuevos:
        if not any(h.tipo_contenido == 0x2077 for h in listas[0].hijos):
            plantilla = _PLANTILLA_MARCADOR_12
        else:
            raise ErrorEscritura("no hay un marcador que sirva de plantilla en esta sesión")
    numeros = [struct.unpack_from("<H", d, h.inicio + 2)[0] for L in listas for h in L.hijos
               if h.tipo_contenido == 0x2077]
    siguiente = max(numeros, default=0) + 1
    bloques_nuevos = b""
    for m in nuevos:
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

def _cambios_tempo(d, bloques, tempo, out):
    """Cambia BPM y posición de los eventos de tempo existentes (mismo número de
    eventos) en las dos copias del bloque 0x2028. Evento 'TMS': tick (int64,
    +10**12) en +19, BPM (double) en +29."""
    copias = buscar(bloques, 0x2028)
    for b in copias:
        evs = [b.pos + m.end() - 3 for m in _EV_TEMPO.finditer(d[b.pos:b.fin])]
        evs.sort(key=lambda p: struct.unpack_from("<q", d, p + 19)[0])
        if len(evs) != len(tempo):
            raise ErrorEscritura("agregar o quitar cambios de tempo aún no está soportado")
        for p, t in zip(evs, tempo):
            if not 5 <= t["bpm"] <= 999:
                raise ErrorEscritura(f"BPM fuera de rango: {t['bpm']}")
            out[p + 19] = struct.pack("<q", t["tick"] + CERO)
            out[p + 29] = struct.pack("<d", float(t["bpm"]))


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
                     marcadores_nuevos=None, marcadores_borrados=None, nombres=None, compases=None):
    """mezcla: {índice de pista: (nombre, {"volumen_db", "paneo", "mute"})}.
    regiones: {nombre de playlist: {índice de clip: {"muestra", "mute"}}}.
    marcadores: {número: {"nombre", "tick", "fin_tick"}}.
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
    if tempo is not None:
        _cambios_tempo(d, bloques, tempo, out)
    nuevo = bytearray(d)
    for pos, b in out.items():
        nuevo[pos:pos + len(b)] = b
    reemplazos = _cambios_marcadores(d, bloques, marcadores) if marcadores else []
    if nombres:
        reemplazos += _renombrar_pistas(d, bloques, nombres)
    if compases is not None:
        reemplazos += _cambios_compases(d, bloques, compases)
    if marcadores_nuevos or marcadores_borrados:
        reemplazos += _altas_bajas_marcadores(d, bloques, marcadores_nuevos or [], marcadores_borrados or [])
    nuevo = reconstruir(bytes(nuevo), reemplazos) if reemplazos else bytes(nuevo)
    salida.write_bytes(cifrar(nuevo))
    return salida

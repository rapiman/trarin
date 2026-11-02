"""Cambiar el clip gain de regiones de audio que ya existen.

El clip gain es un registro del diccionario 0x2637 (nodos [u32 muestra,
float32 dB]; un nodo = ganancia fija) y cada región 0x2628 guarda el índice de
su registro (int32, −1 = sin clip gain). Sumar un delta a una región con
registro es sobrescribir los float32 de sus nodos (mismo tamaño). Una región
sin registro recibe uno nuevo al final del diccionario (un nodo en la muestra 0)
y su índice. Dos clips que comparten la región comparten el clip gain (así lo
guarda Pro Tools)."""

import struct

from .pistas import _primero


def _registros(d, dic):
    """[(posición del registro, cantidad de nodos, posición del primer nodo)]."""
    p = dic.inicio + 2
    n = struct.unpack_from("<I", d, p)[0]
    k = p + 4
    out = []
    for _ in range(n):
        if d[k:k + 4] != b"\x01\x46\x01\x00" or k + 22 > dic.fin:
            break
        tam = struct.unpack_from("<I", d, k + 4)[0]
        cant = struct.unpack_from("<I", d, k + 10)[0]
        out.append((k, cant, k + 22))
        k += 8 + tam
    return out


def _indice_pos(d, g):
    """Posición del int32 con el índice de clip gain de un 0x2628, o None."""
    k = d.rfind(b"\xff\xff\x04\x00\x04\x00", g.inicio, g.fin)
    if k < 0 or k + 31 > g.fin:
        return None
    return k + 27


def cambiar(d, deltas):
    """deltas: {índice de región: dB a sumar}. Devuelve (sesión descifrada
    nueva, {índice: ganancia resultante del primer nodo})."""
    from .reconstruccion import reconstruir
    from .bloques import arbol
    bloques = arbol(d)
    lista = _primero(bloques, 0x262A)
    dic = _primero(bloques, 0x2637)
    if lista is None or dic is None:
        raise ValueError("la sesión no tiene regiones o no tiene el diccionario de clip gain (0x2637)")
    regiones = [c for c in lista.hijos if c.tipo_contenido == 0x2629]
    registros = _registros(d, dic)
    n_dic = len(registros)
    reemplazos, nuevos, resultado = [], b"", {}
    agregados = 0
    tocados = {}  # registro del diccionario -> (delta ya aplicado, primer nodo resultante)
    for indice, delta in sorted(deltas.items()):
        if not 0 <= indice < len(regiones):
            raise ValueError(f"no existe la región {indice}")
        g = next((h for h in regiones[indice].hijos if h.tipo_contenido == 0x2628), None)
        pos_idx = _indice_pos(d, g) if g else None
        if pos_idx is None:
            raise ValueError(f"la región {indice} no tiene el campo de clip gain")
        idx = struct.unpack_from("<i", d, pos_idx)[0]
        if 0 <= idx < n_dic and idx in tocados and abs(tocados[idx][0] - delta) < 0.005:
            # varias regiones comparten el registro: ya quedó corrido con este delta
            resultado[indice] = tocados[idx][1]
        elif 0 <= idx < n_dic and idx in tocados:
            # mismo registro con otro delta: se le da a esta región una copia nueva
            _, cant, p0 = registros[idx]
            nodos, primero = b"", None
            for j in range(cant):
                muestra, db = struct.unpack_from("<If", d, p0 + 8 * j)
                nuevo = min(max(db + delta, -144.0), 36.0)
                primero = nuevo if primero is None else primero
                nodos += struct.pack("<If", muestra, nuevo)
            nuevos += (b"\x01\x46\x01\x00" + struct.pack("<I", 14 + 8 * cant) + b"\0\0" + struct.pack("<I", cant)
                       + b"\x04\x00" + struct.pack("<HI", 0, 0) + nodos)
            reemplazos.append((pos_idx, pos_idx + 4, struct.pack("<i", n_dic + agregados)))
            agregados += 1
            resultado[indice] = round(primero, 2)
        elif 0 <= idx < n_dic:
            _, cant, p0 = registros[idx]
            primero = None
            for j in range(cant):
                db = struct.unpack_from("<f", d, p0 + 8 * j + 4)[0]
                nuevo = min(max(db + delta, -144.0), 36.0)
                primero = nuevo if primero is None else primero
                reemplazos.append((p0 + 8 * j + 4, p0 + 8 * j + 8, struct.pack("<f", nuevo)))
            resultado[indice] = round(primero, 2)
            tocados[idx] = (delta, resultado[indice])
        else:
            nuevo = min(max(delta, -144.0), 36.0)
            nuevos += (b"\x01\x46\x01\x00" + struct.pack("<I", 22) + b"\0\0" + struct.pack("<I", 1) + b"\x04\x00"
                       + struct.pack("<HI", 0, 0) + struct.pack("<If", 0, nuevo))
            reemplazos.append((pos_idx, pos_idx + 4, struct.pack("<i", n_dic + agregados)))
            agregados += 1
            resultado[indice] = round(nuevo, 2)
    if agregados:
        reemplazos.append((dic.inicio + 2, dic.inicio + 6, struct.pack("<I", n_dic + agregados)))
        reemplazos.append((dic.fin, dic.fin, nuevos))
    if not reemplazos:
        return d, resultado
    return reconstruir(d, reemplazos), resultado

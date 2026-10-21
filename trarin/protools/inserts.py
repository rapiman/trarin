"""Copiar los inserts (plugins) de una pista de una sesión a una pista de otra.

Comparado con un plugin agregado en Pro Tools 12 (v4_solo → v5_plugin):

* cada canal tiene en su 0x261B un 0x2627 con 11 lugares de insert: un lugar
  vacío es un 0x2625 y uno ocupado un 0x2616 (con 0x2615 → 0x2613 → estado
  0x1038 del plugin y 0x260E). Dos instancias del mismo plugin no tienen
  identificadores propios: el bloque se puede copiar tal cual;
* el plugin se declara en la lista de la sesión 0x1018 (u32 cantidad; cada
  0x1017: u8 categoría, nombre, códigos de fabricante y producto de 4 letras
  al revés y el identificador del paquete AAX; los 0x1017 con categoría FF
  son lugares vacíos). La lista va ordenada por nombre y Pro Tools pone el
  plugin nuevo en su lugar. Cada insert apunta a su plugin con el u16 que
  sigue al 0x2626 dentro de su 0x2615: la posición en esta lista. Al agregar un plugin, Pro Tools
  renumera ese u16 en todos los inserts de la sesión [verificado en Pro Tools
  12: con el número sin traducir, el insert carga el plugin de esa posición];
* cada insert apunta con el u16 en +2 de su 0x2613 a un registro 0x2064 de la
  lista 0x2027 (u32 cantidad): el archivo de ajustes del plugin («Plug-In
  Settings», …«Fact.tfx» con los códigos del plugin); FFFF = sin registro.
  Verificado en Pro Tools 12 (un plugin agregado a mano suma su 0x2064 al final
  y el insert apunta a él) y en el corpus (268 de 336 inserts apuntan a un
  registro con su mismo código). Si el número apunta a otro registro, Pro Tools
  carga otro plugin: al copiar hay que copiar el registro y traducir el número;
* el índice 0x0002 no cambia (solo apunta al 0x2627, que sigue en su lugar).
"""

import struct

from .bloques import arbol, buscar
from .pistas import _cadena
from .pistas_nuevas import ErrorPista, _lista_2519, _primero
from .reconstruccion import reconstruir


def _canal(d, bloques, nombre, con_ancho=False):
    L = _primero(bloques, 0x2519)
    entradas = _lista_2519(d, L)[0]
    i = next((k for k, e in enumerate(entradas) if e[3] == nombre), None)
    if i is None:
        raise ErrorPista(f"no existe la pista {nombre!r}")
    raiz = _primero(bloques, 0x2624)
    if raiz is None or len(raiz.hijos) != len(entradas):
        raise ErrorPista("los canales de mezcla no corresponden con las pistas")
    canal = raiz.hijos[i]
    sb = next((h for h in canal.hijos if h.tipo_contenido == 0x261B), None)
    ins = next((h for h in sb.hijos if h.tipo_contenido == 0x2627), None) if sb else None
    if ins is None:
        raise ErrorPista(f"{nombre}: el canal no tiene lugares de insert (0x2627)")
    return (sb, ins, entradas[i][4] + 1) if con_ancho else (sb, ins)


def _codigo_1038(d, b):
    return bytes(d[b.inicio + 18:b.inicio + 26])


def _plugins_lista(d, bloques):
    """Lista de plugins de la sesión en orden: [(códigos fabricante+producto
    como en 0x1038, bloque 0x1017)], sin los lugares vacíos (categoría FF)."""
    lista = _primero(bloques, 0x1018)
    out = []
    if lista is None:
        return lista, out
    for h in lista.hijos:
        if h.tipo_contenido != 0x1017 or d[h.inicio + 2] == 0xFF:
            continue
        _, p = _cadena(d, h.inicio + 3)
        out.append((bytes(d[p:p + 4][::-1] + d[p + 4:p + 8][::-1]), h))
    return lista, out


def _pos_plugin(x):
    """Posición del u16 'plugin' de un 0x2615: justo después de su 0x2626."""
    h = next((h for h in x.hijos if h.tipo_contenido == 0x2626), None)
    if h is None:
        raise ErrorPista("lugar de insert (0x2615) no reconocido")
    return h.fin


def _nombre(d_origen, ajenos, codigo):
    h = next(x for c, x in ajenos if c == codigo)
    return _cadena(d_origen, h.inicio + 3)[0]


def _registros(d, bloques):
    lista = _primero(bloques, 0x2027)
    if lista is None:
        return None, []
    return lista, [h for h in lista.hijos if h.tipo_contenido == 0x2064]


def copiar_inserts(d, pista, d_origen, pista_origen):
    """Devuelve d con los inserts de 'pista' reemplazados por los de
    'pista_origen' de la sesión d_origen (ambas descifradas). Los plugins y los
    registros de ajustes (0x2064) que falten se agregan al final de sus listas
    y cada insert pasa a apuntar a su registro en esta sesión."""
    b = arbol(d)
    bo = arbol(d_origen)
    sb, ins, ancho = _canal(d, b, pista, True)
    _, ins_o, ancho_o = _canal(d_origen, bo, pista_origen, True)
    if ancho != ancho_o:               # un plugin mono no sirve en un canal estéreo, ni al revés
        raise ErrorPista(f"{pista_origen} ({ancho_o} canales) y {pista} ({ancho} canales) no tienen "
                         f"la misma cantidad de canales")
    lista, propios = _plugins_lista(d, b)
    _, ajenos = _plugins_lista(d_origen, bo)
    lista_r, regs = _registros(d, b)
    _, regs_o = _registros(d_origen, bo)
    contenido_regs = [bytes(d[h.inicio:h.fin]) for h in regs]
    codigos = [c for c, _ in propios]
    agregar_pl, agregar_reg = [], []
    nuevo = bytearray(d_origen[ins_o.pos:ins_o.fin])
    for x in buscar([ins_o], 0x2613):
        e = next((h for h in x.hijos if h.tipo_contenido == 0x1038), None)
        if e is None:
            continue
        cod = _codigo_1038(d_origen, e)
        if cod not in codigos:
            fuente = next((h for c, h in ajenos if c == cod), None)
            if fuente is None:
                raise ErrorPista(f"{pista_origen}: plugin {cod!r} sin declarar en la sesión de origen")
            codigos.append(cod)
            agregar_pl.append(fuente)
        k = struct.unpack_from("<H", d_origen, x.inicio + 2)[0]
        if k == 0xFFFF:
            continue
        if k >= len(regs_o):
            raise ErrorPista(f"{pista_origen}: el insert {cod!r} apunta a un registro de ajustes que no existe")
        r = bytes(d_origen[regs_o[k].inicio:regs_o[k].fin])
        if r not in contenido_regs:
            contenido_regs.append(r)
            agregar_reg.append(bytes(d_origen[regs_o[k].pos:regs_o[k].fin]))
        struct.pack_into("<H", nuevo, x.inicio + 2 - ins_o.pos, contenido_regs.index(r))
    # posición final de cada plugin en la lista ordenada y renumeración de los
    # u16 de los 0x2615 (plugin) de toda la sesión
    orden = [c for c, _ in propios]
    for h in agregar_pl:
        nom = _cadena(d_origen, h.inicio + 3)[0]
        e = next(c for c, x in ajenos if x is h)
        k = next((i for i, (_, x) in enumerate(propios) if _cadena(d, x.inicio + 3)[0] > nom), len(propios))
        # tras los ya agregados con el mismo punto de inserción y nombre menor
        orden_k = [c for c in orden]
        pos = orden_k.index(propios[k][0]) if k < len(propios) else len(orden_k)
        while pos > 0 and orden_k[pos - 1] not in [c for c, _ in propios] \
                and _nombre(d_origen, ajenos, orden_k[pos - 1]) > nom:
            pos -= 1
        orden.insert(pos, e)
    viejo_a_nuevo = {i: orden.index(c) for i, (c, _) in enumerate(propios)}
    reemplazos_2615 = []
    if agregar_pl:
        for x in buscar(b, 0x2615):
            if ins.pos <= x.pos < ins.fin:
                continue
            q = _pos_plugin(x)
            k = struct.unpack_from("<H", d, q)[0]
            if k in viejo_a_nuevo and viejo_a_nuevo[k] != k:
                reemplazos_2615.append((q, q + 2, struct.pack("<H", viejo_a_nuevo[k])))
    for x in buscar([ins_o], 0x2615):
        e = next((h for h in buscar([x], 0x1038)), None)
        if e is not None:
            struct.pack_into("<H", nuevo, _pos_plugin(x) - ins_o.pos, orden.index(_codigo_1038(d_origen, e)))
    reemplazos = [(ins.pos, ins.fin, bytes(nuevo), sb.pos)] + reemplazos_2615
    if agregar_pl:
        # la lista de plugins va ordenada por nombre (114 de 121 sesiones del
        # corpus; Pro Tools pone el plugin nuevo en su lugar): cada plugin nuevo
        # va antes del primero con nombre mayor
        if lista is None:
            raise ErrorPista("la sesión no tiene la lista de plugins (0x1018)")
        cant = struct.unpack_from("<I", d, lista.inicio + 2)[0]
        if cant != len(propios):
            raise ErrorPista("lista de plugins no reconocida")
        reemplazos.append((lista.inicio + 2, lista.inicio + 6, struct.pack("<I", cant + len(agregar_pl))))
        grupos = {}
        for h in agregar_pl:
            nom = _cadena(d_origen, h.inicio + 3)[0]
            sig = next((x for _, x in propios if _cadena(d, x.inicio + 3)[0] > nom), None)
            punto = sig.pos if sig is not None else (lista.hijos[-1].fin if lista.hijos else lista.inicio + 6)
            grupos.setdefault(punto, []).append((nom, bytes(d_origen[h.pos:h.fin])))
        for punto, xs in grupos.items():
            reemplazos.append((punto, punto, b"".join(b for _, b in sorted(xs)), lista.pos))
    for lst, hijos, extra in ((lista_r, regs, agregar_reg),):
        if not extra:
            continue
        if lst is None:
            raise ErrorPista("la sesión no tiene la lista de ajustes de plugins (0x2027)")
        cant = struct.unpack_from("<I", d, lst.inicio + 2)[0]
        if cant != len(hijos):
            raise ErrorPista(f"lista 0x{lst.tipo_contenido:04x} no reconocida")
        reemplazos.append((lst.inicio + 2, lst.inicio + 6, struct.pack("<I", cant + len(extra))))
        punto = lst.hijos[-1].fin if lst.hijos else lst.inicio + 6
        reemplazos.append((punto, punto, b"".join(extra), lst.pos))
    return reconstruir(d, reemplazos)


def plugins_de(d, pista):
    """Nombres de los plugins de una pista, en orden de lugar."""
    b = arbol(d)
    _, ins = _canal(d, b, pista)
    _, lista = _plugins_lista(d, b)
    nombres = {c: _cadena(d, h.inicio + 3)[0] for c, h in lista}
    out = []
    for x in buscar([ins], 0x2613):
        e = next((h for h in x.hijos if h.tipo_contenido == 0x1038), None)
        if e is not None:
            cod = _codigo_1038(d, e)
            out.append(nombres.get(cod, cod.decode("latin-1")))
    return out

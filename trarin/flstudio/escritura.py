"""Escritura de proyectos de FL Studio (.flp) editando un proyecto existente.

Se parte de la lista de eventos del .flp original y solo se reemplazan los
eventos que el JSON trarin/0 cambió; los demás se escriben tal cual (sin
cambios, el resultado es idéntico byte a byte). Se modifican:

- tempo (156) y compás (17/18);
- patrones: nombre (193) y notas (224, registros de 24 bytes);
- arreglos: items de la playlist (233, registros de 32 o 60 bytes);
- canales: nombre (203) e insert de destino (22);
- inserts: volumen y paneo (registros del evento 225).
"""

from pathlib import Path
import struct

from .proyecto import (db_a_fader, leer, tamano_item, leer_eventos, escribir_eventos, _version_tupla, _texto,
                       E_TEMPO, E_TIEMPO_NUM, E_TIEMPO_DEN, E_CANAL, E_CANAL_INSERT, E_PATRON,
                       E_PATRON_NOMBRE, E_NOTAS, E_ARREGLO, E_PLAYLIST, E_PISTA, E_INSERT,
                       E_NOMBRE, E_MEZCLA_PARAMS, E_VERSION, TICKS)


class ErrorEscritura(Exception):
    pass


def _cod_texto(s, unicode):
    return (s + "\x00").encode("utf-16-le") if unicode else (s + "\x00").encode("latin-1")


def _notas_bytes(notas, escala, originales):
    out = bytearray()
    modelo = originales[:24] if len(originales) >= 24 else None
    for k, n in enumerate(notas):
        base = bytearray(originales[k * 24:(k + 1) * 24]) if len(originales) >= (k + 1) * 24 else None
        if base is None:
            base = bytearray(modelo) if modelo else bytearray(
                struct.pack("<IHHIHHBBBBBBBB", 0, 0x4000, 0, 0, 60, 0, 120, 0, 64, 0, 64, 100, 128, 128))
        struct.pack_into("<I", base, 0, n["tick"] // escala)
        struct.pack_into("<H", base, 6, n.get("canal", 0))
        struct.pack_into("<I", base, 8, max(1, n["largo_ticks"] // escala))
        struct.pack_into("<H", base, 12, n["nota"])
        base[20] = max(0, min(127, 64 + n.get("paneo", 0)))
        base[21] = max(0, min(255, n.get("velocidad", 100)))
        out += base
    return bytes(out)


def _items_bytes(items, escala, originales, tam, pistas_max):
    out = bytearray()
    for k, it in enumerate(items):
        if len(originales) >= (k + 1) * tam:
            base = bytearray(originales[k * tam:(k + 1) * tam])
        elif len(originales) >= tam:
            base = bytearray(originales[:tam])
        else:
            raise ErrorEscritura("la playlist original está vacía: no hay item que usar de modelo")
        base_patron = struct.unpack_from("<H", base, 4)[0]
        indice = base_patron + it["patron"] if "patron" in it else it["canal"]
        struct.pack_into("<IHHIH", base, 0, it["tick"] // escala, base_patron, indice,
                         it["largo_ticks"] // escala, pistas_max - it["pista"])
        out += base
    return bytes(out)


def escribir(datos, plantilla, salida):
    """Aplica 'datos' (trarin/0 leído de 'plantilla' y editado) sobre los
    eventos de 'plantilla' y guarda el resultado en 'salida'."""
    if Path(salida).resolve() == Path(plantilla).resolve():
        raise ErrorEscritura("la salida no puede ser la misma plantilla")
    original = leer(plantilla)
    cab, eventos = leer_eventos(Path(plantilla).read_bytes())
    escala = TICKS // cab["ppq"]
    version = next((_texto(v, False) for i, v in eventos if i == E_VERSION), "")
    unicode = _version_tupla(version) >= (11, 5)
    pistas_max = 500 if _version_tupla(version) >= (12, 9) else 199

    def cambio(clave):
        return datos.get(clave) is not None and datos.get(clave) != original.get(clave)

    bpm = datos["tempo"][0]["bpm"] if datos.get("tempo") else None
    if datos.get("tempo") and len(datos["tempo"]) > 1:
        raise ErrorEscritura("FL Studio: los cambios de tempo van en un canal de automatización (aún no soportado)")
    compas = datos["compases"][0] if datos.get("compases") else None

    patrones = {p["numero"]: p for p in datos.get("patrones", [])}
    patrones_orig = {p["numero"]: p for p in original["patrones"]}
    canales = {c["indice"]: c for c in datos.get("canales", [])}
    canales_orig = {c["indice"]: c for c in original["canales"]}
    arreglos = datos.get("arreglos", [])
    arreglos_orig = original["arreglos"]

    nuevos = []
    canal = patron = None
    n_arreglo = -1
    insert = None
    for i, v in eventos:
        if i == E_TEMPO and bpm is not None:
            v = struct.pack("<I", round(bpm * 1000))
        elif i == E_TIEMPO_NUM and compas:
            v = bytes([compas["numerador"]])
        elif i == E_TIEMPO_DEN and compas and "valor_18_no_reconocido" not in compas:
            v = bytes([compas["denominador"]])
        elif i == E_CANAL:
            canal, patron, insert = int.from_bytes(v, "little"), None, None
        elif i == E_PATRON:
            patron = int.from_bytes(v, "little")
        elif i == E_ARREGLO:
            n_arreglo += 1
            canal = patron = None
        elif i in (E_PLAYLIST, E_PISTA) and n_arreglo < 0 and insert is None:
            n_arreglo = 0                       # FL anterior a 20: arreglo implícito
            canal = patron = None
        elif i == E_INSERT:
            insert, canal, patron = True, None, None

        if i == E_PATRON_NOMBRE and patron in patrones:
            p = patrones[patron]
            if p.get("nombre") is not None and p["nombre"] != patrones_orig.get(patron, {}).get("nombre"):
                v = _cod_texto(p["nombre"], unicode)
        elif i == E_NOTAS and patron in patrones:
            p = patrones[patron]
            if p["notas"] != patrones_orig.get(patron, {}).get("notas"):
                v = _notas_bytes(sorted(p["notas"], key=lambda n: (n["tick"], n["nota"])), escala, v)
        elif i == E_PLAYLIST and 0 <= n_arreglo < len(arreglos):
            a, ao = arreglos[n_arreglo], arreglos_orig[n_arreglo]
            if [_clave(x) for x in a["items"]] != [_clave(x) for x in ao["items"]]:
                tam = tamano_item(version)
                v = _items_bytes(a["items"], escala, v, tam, pistas_max)
        elif i == E_NOMBRE and canal in canales and insert is None:
            c = canales[canal]
            if c.get("nombre") and c["nombre"] != canales_orig.get(canal, {}).get("nombre"):
                v = _cod_texto(c["nombre"], unicode)
        elif i == E_CANAL_INSERT and canal in canales:
            c = canales[canal]
            if c.get("insert") is not None and c["insert"] != canales_orig.get(canal, {}).get("insert"):
                v = bytes([c["insert"] & 0xFF])
        elif i == E_MEZCLA_PARAMS and datos.get("mezcla") is not None:
            v = _mezcla(v, datos["mezcla"], original["mezcla"])
        nuevos.append((i, v))

    Path(salida).write_bytes(escribir_eventos(cab, nuevos))
    return salida


def _clave(it):
    return (it["tick"], it["largo_ticks"], it["pista"], it.get("patron"), it.get("canal"))


def _mezcla(datos, inserts, inserts_orig):
    orig = {m["numero"]: m for m in inserts_orig}
    pedidos = {}
    for m in inserts:
        o = orig.get(m["numero"], {})
        if "volumen_db" in m and m["volumen_db"] != o.get("volumen_db"):
            pedidos[(m["numero"], 192)] = db_a_fader(m["volumen_db"])
        elif m.get("volumen_fader") is not None and m["volumen_fader"] != o.get("volumen_fader"):
            pedidos[(m["numero"], 192)] = round(m["volumen_fader"] * 16000)
        if m.get("paneo") is not None and m["paneo"] != o.get("paneo"):
            pedidos[(m["numero"], 193)] = round(m["paneo"] * 6400)
    if not pedidos:
        return datos
    out = bytearray(datos)
    for k in range(0, len(out) - 11, 12):
        pid = out[k + 4]
        n = (struct.unpack_from("<H", out, k + 6)[0] >> 6) & 0x7F
        if (n, pid) in pedidos:
            struct.pack_into("<i", out, k + 8, pedidos.pop((n, pid)))
    if pedidos:
        raise ErrorEscritura(f"inserts sin registro de volumen/paneo en la plantilla: {sorted(pedidos)}")
    return bytes(out)

"""Lectura de proyectos de FL Studio (.flp).

Implementación propia, sin dependencias, a partir de la descripción pública
del formato y de la observación de proyectos guardados por FL 12 y FL 20.

Estructura: cabecera "FLhd" (u32 tamaño 6, i16 formato, u16 canales, u16 PPQ)
y bloque "FLdt" (u32 tamaño) con una secuencia de eventos. Cada evento empieza
con un byte de id: 0–63 llevan 1 byte, 64–127 u16, 128–191 u32 y 192–255 un
largo varint (7 bits por byte) seguido de los datos. Los eventos son
secuenciales: un evento "nuevo" (canal 64, patrón 65, arreglo 99, insert del
mezclador 236) abre el contexto al que pertenecen los siguientes.
"""

from pathlib import Path
import math
import struct

from ..protools import tiempo

FORMATO = "trarin/0"
TICKS = tiempo.TICKS_NEGRA

# Ids de evento usados.
E_TIEMPO_NUM, E_TIEMPO_DEN = 17, 18
E_CANAL_TIPO, E_CANAL_INSERT = 21, 22
E_TM_NUM, E_TM_DEN = 33, 34
E_CANAL, E_PATRON, E_ARREGLO_ACTUAL = 64, 65, 100
E_SLOT, E_ARREGLO = 98, 99
E_TM_POS, E_TEMPO = 148, 156
E_PATRON_NOMBRE, E_TITULO, E_SAMPLE, E_VERSION = 193, 194, 196, 199
E_PLUGIN_INTERNO, E_NOMBRE, E_INSERT_NOMBRE, E_TM_NOMBRE = 201, 203, 204, 205
E_NOTAS, E_MEZCLA_PARAMS, E_PLAYLIST = 224, 225, 233
E_INSERT, E_PISTA, E_PISTA_NOMBRE, E_ARREGLO_NOMBRE = 236, 238, 239, 241
TEXTO = {231, 239, 241}           # ids de datos que en realidad son texto
BASE_PATRON = 20480               # item de playlist > base => patrón
TIPOS_CANAL = {0: "sampler", 2: "generador", 3: "capas", 4: "instrumento", 5: "automatizacion"}


class ErrorFLP(Exception):
    pass


def leer_eventos(datos):
    """(cabecera, [(id, bytes)]) de un .flp."""
    if datos[:4] != b"FLhd":
        raise ErrorFLP("no es un proyecto de FL Studio")
    largo = struct.unpack_from("<I", datos, 4)[0]
    formato, canales, ppq = struct.unpack_from("<hHH", datos, 8)
    p = 8 + largo
    if datos[p:p + 4] != b"FLdt":
        raise ErrorFLP("falta el bloque FLdt")
    fin = p + 8 + struct.unpack_from("<I", datos, p + 4)[0]
    p += 8
    eventos = []
    while p < fin:
        i = datos[p]
        p += 1
        if i < 64:
            n = 1
        elif i < 128:
            n = 2
        elif i < 192:
            n = 4
        else:
            n, s = 0, 0
            while True:
                b = datos[p]
                p += 1
                n |= (b & 0x7F) << s
                s += 7
                if b < 128:
                    break
        eventos.append((i, datos[p:p + n]))
        p += n
    return {"formato": formato, "canales": canales, "ppq": ppq}, eventos


def escribir_eventos(cabecera, eventos):
    """Inversa de leer_eventos (la usa la escritura y la prueba de ida y vuelta)."""
    cuerpo = bytearray()
    for i, v in eventos:
        cuerpo.append(i)
        if i >= 192:
            n = len(v)
            while True:
                b = n & 0x7F
                n >>= 7
                cuerpo.append(b | (0x80 if n else 0))
                if not n:
                    break
        cuerpo += v
    h = struct.pack("<4sIhHH", b"FLhd", 6, cabecera["formato"], cabecera["canales"], cabecera["ppq"])
    return h + b"FLdt" + struct.pack("<I", len(cuerpo)) + bytes(cuerpo)


def _entero(v):
    return int.from_bytes(v, "little")


def _texto(v, unicode=True):
    if unicode:
        return v.decode("utf-16-le", "replace").rstrip("\x00")
    return v.decode("latin-1").rstrip("\x00")


def leer(ruta):
    """Lee un .flp y devuelve un dict con el formato trarin/0."""
    ruta = Path(ruta)
    cab, eventos = leer_eventos(ruta.read_bytes())
    escala = TICKS // cab["ppq"]
    version = next((_texto(v, False) for i, v in eventos if i == E_VERSION), "")
    unicode = _version_tupla(version) >= (11, 5)

    pistas_max = 500 if _version_tupla(version) >= (12, 9) else 199
    tam_item = tamano_item(version)

    def texto(v):
        return _texto(v, unicode)

    tempo = [{"tick": 0, "bpm": 120.0, "pulso_ticks": TICKS}]
    compas = {"numerador": 4, "denominador": 4}
    titulo = None
    canales, patrones, arreglos, inserts = [], {}, [], []
    canal = patron = arreglo = insert = None
    slot = {}
    marca = None
    params_mezcla = b""

    for i, v in eventos:
        if i == E_TEMPO:
            tempo[0]["bpm"] = _entero(v) / 1000
        elif i == E_TIEMPO_NUM:
            compas["numerador"] = v[0]
        elif i == E_TIEMPO_DEN:
            if v[0] in (1, 2, 4, 8, 16, 32):
                compas["denominador"] = v[0]
            else:
                compas["valor_18_no_reconocido"] = v[0]
        elif i == E_TITULO:
            titulo = texto(v) or None
        elif i == E_CANAL:
            canal = {"indice": _entero(v), "nombre": None, "tipo": None}
            canales.append(canal)
            patron = None
        elif i == E_CANAL_TIPO and canal is not None:
            canal["tipo"] = TIPOS_CANAL.get(v[0], v[0])
        elif i == E_CANAL_INSERT and canal is not None:
            canal["insert"] = v[0] if v[0] < 128 else v[0] - 256
        elif i == E_SAMPLE and canal is not None:
            canal["archivo"] = texto(v)
        elif i == E_PATRON:
            numero = _entero(v)
            patron = patrones.setdefault(numero, {"numero": numero, "nombre": None, "notas": []})
        elif i == E_PATRON_NOMBRE and patron is not None:
            patron["nombre"] = texto(v)
        elif i == E_NOTAS and patron is not None:
            patron["notas"] += _notas(v, escala)
        elif i == E_ARREGLO:
            arreglo = {"numero": _entero(v), "nombre": None, "marcadores": [], "pistas": [], "items": []}
            arreglos.append(arreglo)
            canal = patron = None
        elif i == E_ARREGLO_NOMBRE and arreglo is not None:
            arreglo["nombre"] = texto(v)
        elif i in (E_PLAYLIST, E_PISTA) and arreglo is None and insert is None:
            # FL anterior a la versión 20: una sola playlist, sin arreglos.
            arreglo = {"numero": 0, "nombre": None, "marcadores": [], "pistas": [], "items": []}
            arreglos.append(arreglo)
            canal = patron = None
            if i == E_PLAYLIST:
                arreglo["items"] += _playlist(v, escala, pistas_max, tam_item)
            else:
                arreglo["pistas"].append({"numero": 1, "nombre": None})
        elif i == E_PLAYLIST and arreglo is not None:
            arreglo["items"] += _playlist(v, escala, pistas_max, tam_item)
        elif i == E_PISTA and arreglo is not None:
            arreglo["pistas"].append({"numero": len(arreglo["pistas"]) + 1, "nombre": None})
        elif i == E_PISTA_NOMBRE and arreglo is not None and arreglo["pistas"]:
            arreglo["pistas"][-1]["nombre"] = texto(v)
        elif i == E_TM_POS:
            pos = _entero(v)
            marca = {"tick": (pos & 0xFFFFFF) * escala, "nombre": None}
            if pos >> 24 == 8:
                marca["tipo"] = "compas"
            if arreglo is not None:
                arreglo["marcadores"].append(marca)
            elif patron is not None:
                patron.setdefault("marcadores", []).append(marca)
        elif i == E_TM_NOMBRE and marca is not None:
            marca["nombre"] = texto(v)
        elif i == E_TM_NUM and marca is not None:
            marca["numerador"] = v[0]
        elif i == E_TM_DEN and marca is not None:
            marca["denominador"] = v[0]
        elif i == E_INSERT:
            insert = {"numero": len(inserts), "nombre": None, "efectos": []}
            inserts.append(insert)
            arreglo = canal = patron = None
            slot = {}
        elif i == E_INSERT_NOMBRE and insert is not None:
            insert["nombre"] = texto(v)
        elif i == E_NOMBRE:
            if insert is not None:
                slot["nombre"] = texto(v)
            elif canal is not None:
                canal["nombre"] = texto(v)
        elif i == E_PLUGIN_INTERNO:
            nombre = texto(v)
            if insert is not None:
                slot["plugin"] = nombre
            elif canal is not None and nombre:
                canal["plugin"] = nombre
        elif i == E_SLOT and insert is not None:
            if slot:
                insert["efectos"].append(dict(slot, slot=_entero(v) + 1))
            slot = {}
        elif i == E_MEZCLA_PARAMS:
            params_mezcla = v

    _volumen_paneo(params_mezcla, inserts)

    for a in arreglos:
        for it in a["items"]:
            it["pista_nombre"] = next((p["nombre"] for p in a["pistas"] if p["numero"] == it["pista"]), None)
        a["pistas"] = [p for p in a["pistas"] if p["nombre"] or any(it["pista"] == p["numero"] for it in a["items"])]
        # Se conserva el orden del archivo: el escritor empareja cada item con su registro.
        a["marcadores"] = [m for m in a["marcadores"] if m.get("tipo") != "compas" or m.get("nombre")]

    for c in canales:
        if c.get("tipo") == "sampler" and "archivo" not in c:
            c["archivo"] = None

    compases = [{"tick": 0, "compas": 1, **compas}]
    return {
        "formato": FORMATO,
        "origen": {"daw": "flstudio", "producto": "FL Studio", "version_completa": version,
                   "version": _version_tupla(version)[0] or None, "ppq": cab["ppq"],
                   "titulo": titulo, "archivo": ruta.name},
        "unidades": {"ticks_por_negra": TICKS},
        "tempo": tempo,
        "compases": compases,
        "canales": canales,
        "patrones": [p for _, p in sorted(patrones.items()) if p["notas"] or p["nombre"]],
        "arreglos": arreglos,
        "mezcla": [ins for ins in inserts if ins["nombre"] or ins["efectos"]
                   or ins.get("volumen_fader", 0.8) != 0.8 or ins["numero"] == 0],
        "pendiente": ["automatización (canales de tipo automatización)", "estado de plugins",
                      "clips de audio de la playlist", "ruteo entre inserts"],
    }


def tamano_item(version):
    """Bytes por item de playlist: 60 desde FL 20.99 (FL 21), 32 antes."""
    return 60 if _version_tupla(version) >= (20, 99) else 32


def _version_tupla(texto):
    partes = []
    for x in texto.split("."):
        try:
            partes.append(int(x))
        except ValueError:
            break
    return tuple(partes) or (0,)


def _notas(v, escala):
    """Notas de 24 bytes: posición u32, banderas u16, canal u16, largo u32,
    tecla u16, grupo u16, afinación fina, (byte), release, canal MIDI, paneo,
    velocidad, mod X, mod Y."""
    out = []
    for k in range(0, len(v) - 23, 24):
        pos, _, canal, largo, tecla = struct.unpack_from("<IHHIH", v, k)
        paneo, velocidad = v[k + 20], v[k + 21]
        out.append({"tick": pos * escala, "nota": tecla, "largo_ticks": largo * escala,
                    "velocidad": velocidad, "canal": canal, "paneo": paneo - 64})
    return out


def _playlist(v, escala, pistas_max, tam_item):
    """Items de playlist: 32 bytes (60 desde FL 21). Posición u32, base u16,
    índice u16 (mayor que la base = patrón), largo u32, pista u16 invertida
    (pistas_max − valor; 500 pistas desde FL 12.9, 199 antes)."""
    tam = tam_item
    out = []
    for k in range(0, len(v) - tam + 1, tam):
        pos, base, indice, largo, pista = struct.unpack_from("<IHHIH", v, k)
        item = {"tick": pos * escala, "largo_ticks": largo * escala, "pista": pistas_max - pista}
        if indice > base:
            item["patron"] = indice - base
        else:
            item["canal"] = indice
        out.append(item)
    return out


# Curva del fader del mezclador (medida en FL Studio 21 escribiendo valores en dB):
# ganancia = (e^(3x) − 1) / (e^2,4 − 1), con x = valor / 16000. 0 dB es x = 0,8
# (12800) y el máximo x = 1 da +5,6 dB. Error < 0,03 dB entre +3 y −48 dB.
_K_FADER = math.expm1(2.4)


def fader_a_db(valor):
    x = valor / 16000
    return None if x <= 0 else round(20 * math.log10(math.expm1(3 * x) / _K_FADER), 2)


def db_a_fader(db):
    """dB → valor almacenado (0..16000). None o −inf = silencio."""
    if db is None or db == float("-inf"):
        return 0
    return max(0, min(16000, round(16000 * math.log1p(_K_FADER * 10 ** (db / 20)) / 3)))


def _volumen_paneo(datos, inserts):
    """Registros de 12 bytes: [4] id del parámetro, [6:8] u16 con insert<<6|slot,
    [8:12] valor int32. 192 = volumen (12800 = posición por defecto), 193 = paneo."""
    for k in range(0, len(datos) - 11, 12):
        pid = datos[k + 4]
        if pid not in (192, 193):
            continue
        cd = struct.unpack_from("<H", datos, k + 6)[0]
        n = (cd >> 6) & 0x7F
        valor = struct.unpack_from("<i", datos, k + 8)[0]
        if n < len(inserts):
            if pid == 192:
                inserts[n]["volumen_fader"] = round(valor / 16000, 4)
                inserts[n]["volumen_db"] = fader_a_db(valor)
            else:
                inserts[n]["paneo"] = round(valor / 6400, 4)

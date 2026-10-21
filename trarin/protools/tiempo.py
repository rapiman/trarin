"""Mapa de tempo, compases y marcadores de una sesión Pro Tools 12.

Formato descifrado a partir de sesiones reales de Pro Tools 12 HD (Windows).
Todas las posiciones musicales usan la misma unidad: 960.000 ticks por negra,
contados desde un cero fijo de 10**12.

Bloques:
  0x2028 "Tempo"   eventos 'Const' + 'TMS': posición, BPM, figura del pulso.
  0x2029 "Meter"   eventos de 36 bytes: posición, compás, numerador, denominador.
  0x2077           marcador: número, nombre UTF-8, posición.
Los tres bloques aparecen dos veces en la sesión con el mismo contenido; se usa
la primera aparición.
"""

import re
import struct

from .bloques import buscar

CERO = 10 ** 12
TICKS_NEGRA = 960_000

_EV_TEMPO = re.compile(rb"Const\x01\x00\x2e\x00\x00\x00TMS")
_EV_MARCA = re.compile(rb"\x5a\x0c\x00.\x02\x00\x00\x77\x20", re.S)


def _tramo(d, bloques, tipo):
    encontrados = buscar(bloques, tipo)
    if not encontrados:
        return None
    b = encontrados[0]
    return d[b.pos:b.fin], b.pos


def leer_tempo(d, bloques):
    t = _tramo(d, bloques, 0x2028)
    if not t:
        return []
    datos, _ = t
    eventos = []
    for m in _EV_TEMPO.finditer(datos):
        p = m.end() - 3  # inicio de 'TMS'
        tick = struct.unpack_from("<q", datos, p + 19)[0] - CERO
        bpm = struct.unpack_from("<d", datos, p + 29)[0]
        pulso = struct.unpack_from("<I", datos, p + 37)[0]
        eventos.append({"tick": tick, "bpm": round(bpm, 6), "pulso_ticks": pulso})
    eventos.sort(key=lambda e: e["tick"])
    return eventos


def leer_compases(d, bloques):
    m = re.search(rb"\x29\x20Meter\x02\x00", d)
    if not m:
        return []
    p = m.end()
    n = struct.unpack_from("<I", d, p + 4)[0]
    p += 8
    eventos = []
    for _ in range(n):
        tick, compas, num, den = struct.unpack_from("<qIII", d, p)
        eventos.append({"tick": tick - CERO, "compas": compas, "numerador": num, "denominador": den})
        p += 36
    return eventos


def leer_marcadores(d, bloques):
    """Ubicaciones de memoria (bloques 0x2077 dentro de la primera lista 0x2030).

    Carga del 0x2077: u16 número; byte +3 banderas (bit 1 = selección; 1 y 9 son marcadores);
    u32 largo del nombre en +6 y nombre desde +10. Al final del nombre (E):
    inicio en E y fin en E+8, cada uno de 5 bytes de tick (+10**12) seguidos de
    00 00 40; el comentario es u32 largo en E+80 y texto en E+84.
    """
    from .bloques import buscar
    listas = [b for b in buscar(bloques, 0x2030)
              if any(h.tipo_contenido == 0x2077 for h in b.hijos)]
    if not listas:
        return _leer_marcadores_patron(d, bloques)
    marcas = []
    for b in listas[0].hijos:
        if b.tipo_contenido != 0x2077:
            continue
        p = b.inicio + 2
        numero = struct.unpack_from("<H", d, p)[0]
        tipo = d[p + 3]
        largo = struct.unpack_from("<I", d, p + 6)[0]
        if largo > 1024 or p + 10 + largo + 13 > b.fin:
            continue
        nombre = d[p + 10:p + 10 + largo].decode("utf-8", "replace")
        e = p + 10 + largo
        ev = {"numero": numero, "nombre": nombre,
              "tick": int.from_bytes(d[e:e + 5], "little") - CERO}
        if tipo & 2:
            ev["tipo"] = "seleccion"
            ev["fin_tick"] = int.from_bytes(d[e + 8:e + 13], "little") - CERO
        if e + 84 <= b.fin:
            n = struct.unpack_from("<I", d, e + 80)[0]
            if 0 < n <= 65536 and e + 84 + n <= b.fin:
                ev["comentario"] = d[e + 84:e + 84 + n].decode("utf-8", "replace")
        marcas.append(ev)
    return marcas


def _leer_marcadores_patron(d, bloques):
    t = _tramo(d, bloques, 0x271A)
    if not t:
        return []
    datos, _ = t
    marcas = []
    for m in _EV_MARCA.finditer(datos):
        p = m.end()
        numero = struct.unpack_from("<H", datos, p)[0]
        largo = struct.unpack_from("<I", datos, p + 6)[0]
        if largo > 255:
            continue
        nombre = datos[p + 10:p + 10 + largo].decode("utf-8", "replace")
        q = p + 10 + largo
        tick = int.from_bytes(datos[q:q + 5], "little") - CERO
        marcas.append({"numero": numero, "nombre": nombre, "tick": tick})
    return marcas


# ---------------------------------------------------------------- conversiones

def segundos(tick, tempo):
    """Segundos desde el cero hasta 'tick', con el mapa de tempo escalonado.

    Antes del primer evento rige el BPM del primer evento. 'pulso_ticks' indica
    la figura que lleva el BPM (960000 = negra, 480000 = corchea).
    """
    if not tempo:
        tempo = [{"tick": 0, "bpm": 120.0, "pulso_ticks": TICKS_NEGRA}]
    total, previo = 0.0, None
    for e in tempo:
        if previo is None:
            previo = {**e, "tick": min(0, e["tick"])}
        if e["tick"] >= tick:
            break
        total += (e["tick"] - previo["tick"]) / previo.get("pulso_ticks", TICKS_NEGRA) * 60.0 / previo["bpm"]
        previo = e
    total += (tick - previo["tick"]) / previo.get("pulso_ticks", TICKS_NEGRA) * 60.0 / previo["bpm"]
    return total


def compas_tiempo(tick, compases):
    """Posición 'compás|tiempo|ticks' como la muestra Pro Tools (tiempos en la figura del denominador)."""
    if not compases:
        compases = [{"tick": 0, "compas": 1, "numerador": 4, "denominador": 4}]
    vigente = compases[0]
    for c in compases:
        if c["tick"] <= tick:
            vigente = c
    t_tiempo = TICKS_NEGRA * 4 // vigente["denominador"]
    t_compas = t_tiempo * vigente["numerador"]
    resto = tick - vigente["tick"]
    compas = vigente["compas"] + resto // t_compas
    resto %= t_compas
    return f"{compas}|{resto // t_tiempo + 1}|{(resto % t_tiempo) * 960 // TICKS_NEGRA:03d}"


def tick_de_segundos(seg, tempo):
    """Inversa de segundos(): el tick que cae en 'seg' (cálculo directo por tramos)."""
    if not tempo:
        tempo = [{"tick": 0, "bpm": 120.0, "pulso_ticks": TICKS_NEGRA}]
    previo = {**tempo[0], "tick": min(0, tempo[0]["tick"])}
    acumulado = 0.0
    for e in tempo:
        pulso = previo.get("pulso_ticks", TICKS_NEGRA)
        dur = (e["tick"] - previo["tick"]) / pulso * 60.0 / previo["bpm"]
        if acumulado + dur > seg:
            break
        acumulado += dur
        previo = e
    pulso = previo.get("pulso_ticks", TICKS_NEGRA)
    return previo["tick"] + round((seg - acumulado) * previo["bpm"] / 60.0 * pulso)

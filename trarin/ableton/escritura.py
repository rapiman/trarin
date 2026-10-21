"""Escritura de sets de Ableton Live (.als) editando un set existente.

Nunca se genera un set desde cero: se parte del .als original (la plantilla),
se aplican los cambios del JSON trarin/0 y todo lo que el JSON no modela queda
como estaba. Se modifican:

- tempo y compás (valor y envolvente de la pista principal);
- marcadores (Locators): mover, renombrar, borrar y agregar;
- pistas: nombre, volumen, paneo y mute;
- clips del Arrangement: posición y fin, nombre y notas MIDI.

Al agregar elementos con Id global se actualiza NextPointeeId.
"""

from math import log2
from pathlib import Path
import copy
import gzip
import xml.etree.ElementTree as ET

from .sesion import leer_xml, leer, TICKS, ANTES_DEL_CERO, _v, _destino, _db, _tick, _notas as _notas_de


class ErrorEscritura(Exception):
    pass


def _set(e, ruta, valor):
    x = e.find(ruta) if ruta else e
    if x is None:
        raise ErrorEscritura(f"no existe {ruta}")
    if isinstance(valor, bool):
        valor = "true" if valor else "false"
    elif isinstance(valor, float):
        valor = repr(round(valor, 10)).rstrip("0").rstrip(".") if valor != int(valor) else str(int(valor))
    x.set("Value", str(valor))


def _beats(tick):
    b = tick / TICKS
    return int(b) if b == int(b) else b


def _id_compas(numerador, denominador):
    return 99 * int(log2(denominador)) + numerador - 1


def _envolvente(pista, pointee):
    for env in pista.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
        if _v(env, "EnvelopeTarget/PointeeId") == pointee:
            return env
    return None


def _reescribir_eventos(env, etiqueta, puntos):
    """puntos: [(tick, valor)] escalonados. Cada cambio se escribe con dos
    puntos en el mismo instante (valor anterior y valor nuevo)."""
    eventos = env.find("Automation/Events")
    for e in list(eventos):
        eventos.remove(e)
    lista = [(ANTES_DEL_CERO, puntos[0][1])]
    previo = puntos[0][1]
    for tick, valor in puntos[1:]:
        lista += [(_beats(tick), previo), (_beats(tick), valor)]
        previo = valor
    for k, (t, v) in enumerate(lista):
        ET.SubElement(eventos, etiqueta, {"Id": str(k), "Time": str(t), "Value": str(v)})


def _locators(ls, marcadores):
    cont = ls.find("Locators/Locators")
    existentes = {int(l.get("Id")): l for l in cont.findall("Locator")}
    modelo = next(iter(existentes.values()), None)
    pedidos = {m.get("numero"): m for m in marcadores if m.get("numero") is not None}
    for i, l in existentes.items():
        if i not in pedidos:
            cont.remove(l)
    siguiente = max(list(existentes) + [-1]) + 1
    for m in marcadores:
        l = existentes.get(m.get("numero"))
        if l is None:
            if modelo is not None:
                l = copy.deepcopy(modelo)
            else:
                l = ET.Element("Locator")
                for etiqueta, valor in (("LomId", "0"), ("Time", "0"), ("Name", ""),
                                        ("Annotation", ""), ("IsSongStart", "false")):
                    ET.SubElement(l, etiqueta, {"Value": valor})
            l.set("Id", str(siguiente))
            m["numero"] = siguiente
            siguiente += 1
            cont.append(l)
            _set(l, "Annotation", "")
            _set(l, "IsSongStart", False)
        _set(l, "Time", _beats(m["tick"]))
        _set(l, "Name", m.get("nombre", ""))
    # Live espera los locators ordenados por tiempo.
    orden = sorted(cont.findall("Locator"), key=lambda l: float(_v(l, "Time", 0)))
    for l in list(cont):
        cont.remove(l)
    for l in orden:
        cont.append(l)


def _notas(clip, notas):
    kts = clip.find("Notes/KeyTracks")
    modelo_nota = clip.find("Notes/KeyTracks/KeyTrack/Notes/MidiNoteEvent")
    modelo_kt = clip.find("Notes/KeyTracks/KeyTrack")
    for kt in list(kts):
        kts.remove(kt)
    por_tecla = {}
    for n in sorted(notas, key=lambda n: (n["nota"], n["tick"])):
        por_tecla.setdefault(n["nota"], []).append(n)
    nid = 1
    for k, (tecla, lista) in enumerate(sorted(por_tecla.items())):
        if modelo_kt is not None:
            kt = copy.deepcopy(modelo_kt)
            for x in list(kt.find("Notes")):
                kt.find("Notes").remove(x)
        else:
            kt = ET.Element("KeyTrack")
            ET.SubElement(kt, "Notes")
            ET.SubElement(kt, "MidiKey", {"Value": "0"})
        kt.set("Id", str(k))
        _set(kt, "MidiKey", tecla)
        for n in lista:
            atributos = dict(modelo_nota.attrib) if modelo_nota is not None else {
                "OffVelocity": "64", "VelocityDeviation": "0", "Probability": "1", "IsEnabled": "true"}
            atributos.update({"Time": str(_beats(n["tick"])), "Duration": str(_beats(n["largo_ticks"])),
                              "Velocity": str(n.get("velocidad", 100))})
            if "NoteId" in atributos or modelo_nota is None:
                atributos["NoteId"] = str(nid)
            nid += 1
            ET.SubElement(kt.find("Notes"), "MidiNoteEvent", atributos)
        kts.append(kt)
    gen = clip.find("Notes/NoteIdGenerator/NextId")
    if gen is not None:
        gen.set("Value", str(nid))


def escribir(datos, plantilla, salida):
    """Aplica 'datos' (trarin/0 leído de 'plantilla' y luego editado) sobre una
    copia de 'plantilla' y la guarda en 'salida' (gzip, como Live)."""
    if Path(salida).resolve() == Path(plantilla).resolve():
        raise ErrorEscritura("la salida no puede ser la misma plantilla")
    raiz = leer_xml(plantilla)
    ls = raiz.find("LiveSet")
    principal = ls.find("MainTrack")
    if principal is None:
        principal = ls.find("MasterTrack")
    mx = principal.find("DeviceChain/Mixer")

    original = leer(plantilla)

    def igual(a, b, claves):
        return [{k: x.get(k) for k in claves} for x in a] == [{k: x.get(k) for k in claves} for x in b]

    tempo = datos.get("tempo") or []
    if tempo and not igual(tempo, original["tempo"], ("tick", "bpm")):
        _set(mx, "Tempo/Manual", float(tempo[0]["bpm"]))
        env = _envolvente(principal, _destino(mx.find("Tempo")))
        if env is not None:
            _reescribir_eventos(env, "FloatEvent", [(t["tick"], float(t["bpm"])) for t in tempo])
    compases = datos.get("compases") or []
    if compases and not igual(compases, original["compases"], ("tick", "numerador", "denominador")):
        ids = [(c["tick"], _id_compas(c["numerador"], c["denominador"])) for c in compases]
        _set(mx, "TimeSignature/Manual", ids[0][1])
        env = _envolvente(principal, _destino(mx.find("TimeSignature")))
        if env is not None:
            _reescribir_eventos(env, "EnumEvent", ids)

    if "marcadores" in datos and not igual(datos["marcadores"], original["marcadores"], ("numero", "tick", "nombre")):
        _locators(ls, datos["marcadores"])

    pistas = {int(p.get("Id")): p for p in ls.find("Tracks")}
    for p in datos.get("pistas", []):
        x = pistas.get(p.get("id"))
        if x is None:
            raise ErrorEscritura(f"pista sin equivalente en la plantilla: {p.get('nombre')!r}")
        if p.get("nombre") and p["nombre"] != _v(x, "Name/EffectiveName"):
            _set(x, "Name/UserName", p["nombre"])
            _set(x, "Name/EffectiveName", p["nombre"])
        m = p.get("mezcla", {})
        m_xml = x.find("DeviceChain/Mixer")
        if m.get("volumen_db") is not None and m["volumen_db"] != _db(_v(m_xml, "Volume/Manual", tipo=float)):
            _set(m_xml, "Volume/Manual", 10 ** (m["volumen_db"] / 20))
        if m.get("paneo") is not None and m["paneo"] != _v(m_xml, "Pan/Manual", tipo=float):
            _set(m_xml, "Pan/Manual", float(m["paneo"]))
        if "mute" in m and m["mute"] != (not _v(m_xml, "Speaker/Manual", True, bool)):
            _set(m_xml, "Speaker/Manual", not m["mute"])
        clips = {int(c.get("Id")): c for c in x.findall(".//ArrangerAutomation/Events/*")}
        for c in p.get("clips", []):
            cx = clips.get(c.get("id"))
            if cx is None:
                raise ErrorEscritura(f"clip sin equivalente en la plantilla: {c.get('nombre')!r}")
            if (c["tick"], c["fin_tick"]) != (_tick(_v(cx, "CurrentStart", 0, float)), _tick(_v(cx, "CurrentEnd", 0, float))):
                inicio, fin = _beats(c["tick"]), _beats(c["fin_tick"])
                cx.set("Time", str(inicio))
                _set(cx, "CurrentStart", inicio)
                _set(cx, "CurrentEnd", fin)
            if c.get("nombre") is not None and c["nombre"] != _v(cx, "Name", ""):
                _set(cx, "Name", c["nombre"])
            if "notas" in c and cx.tag == "MidiClip" and c["notas"] != _notas_de(cx):
                _notas(cx, c["notas"])

    texto = ET.tostring(raiz, encoding="unicode")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + texto + "\n"
    Path(salida).write_bytes(gzip.compress(xml.encode("utf-8")))
    return salida

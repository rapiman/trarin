"""Lectura de sets de Ableton Live (.als): XML comprimido con gzip.

Implementación propia a partir de la observación de sets guardados por
Live 11 y 12, y de la documentación de ableton-als (MIT). Las posiciones de
Ableton están en negras ("beats"); trarin las pasa a ticks (960000 por negra).
"""

from math import log10
from pathlib import Path
import gzip
import re
import xml.etree.ElementTree as ET

from ..protools import tiempo

FORMATO = "trarin/0"
TICKS = tiempo.TICKS_NEGRA
ANTES_DEL_CERO = -63072000          # tiempo del valor inicial de una envolvente


def leer_xml(ruta):
    datos = Path(ruta).read_bytes()
    if datos[:2] == b"\x1f\x8b":
        datos = gzip.decompress(datos)
    return ET.fromstring(datos)


def _v(e, ruta, defecto=None, tipo=str):
    x = e.find(ruta) if ruta else e
    if x is None or x.get("Value") is None:
        return defecto
    valor = x.get("Value")
    if tipo is bool:
        return valor == "true"
    try:
        return tipo(valor)
    except ValueError:
        return defecto


def _tick(beats):
    return round(float(beats) * TICKS)


def _db(lineal):
    return round(20 * log10(lineal), 2) if lineal and lineal > 0 else None


def _compas_de_id(i):
    """Live guarda el compás como 99·log2(denominador) + (numerador − 1)."""
    return {"numerador": i % 99 + 1, "denominador": 2 ** (i // 99)}


def _eventos(env):
    out = []
    for e in env.findall("Automation/Events/*"):
        t = float(e.get("Time"))
        out.append({"tick": 0 if t <= ANTES_DEL_CERO else _tick(t), "valor": e.get("Value")})
    return out


def _envolventes(nodo):
    """{PointeeId: [eventos]} de un AutomationEnvelopes."""
    out = {}
    for env in nodo.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
        pid = _v(env, "EnvelopeTarget/PointeeId")
        if pid is not None:
            out[pid] = _eventos(env)
    return out


def _destino(param):
    t = param.find("AutomationTarget")
    return t.get("Id") if t is not None else None


def _mezcla(pista, nombres_retorno):
    m = pista.find("DeviceChain/Mixer")
    if m is None:
        return {}, {}
    vol = _v(m, "Volume/Manual", tipo=float)
    out = {
        "volumen_db": _db(vol),
        "paneo": _v(m, "Pan/Manual", tipo=float),
        "mute": not _v(m, "Speaker/Manual", True, bool),
        "solo": _v(m, "SoloSink", False, bool),
        "salida": _v(pista, "DeviceChain/AudioOutputRouting/UpperDisplayString"),
        "entrada": _v(pista, "DeviceChain/AudioInputRouting/UpperDisplayString")
                   or _v(pista, "DeviceChain/MidiInputRouting/UpperDisplayString"),
    }
    parametros = {_destino(m.find("Volume")): "volumen", _destino(m.find("Pan")): "paneo",
                  _destino(m.find("Speaker")): "activo"}
    envios = []
    for k, h in enumerate(m.findall("Sends/TrackSendHolder")):
        s = h.find("Send")
        envios.append({"destino": nombres_retorno[k] if k < len(nombres_retorno) else k,
                       "volumen_db": _db(_v(s, "Manual", tipo=float))})
        parametros[_destino(s)] = f"envio_{k + 1}"
    out["envios"] = envios
    return out, parametros


def _dispositivos(pista):
    devs = pista.find("DeviceChain/DeviceChain/Devices")
    out = []
    for d in devs if devs is not None else []:
        item = {"tipo": d.tag, "activo": _v(d, "On/Manual", True, bool)}
        nombre = _v(d, "UserName") or None
        if d.tag == "PluginDevice":
            info = d.find("PluginDesc/*")
            if info is not None:
                item["formato"] = info.tag.replace("PluginInfo", "")
                nombre = nombre or _v(info, "PlugName") or _v(info, "Name")
        if nombre:
            item["nombre"] = nombre
        out.append(item)
    return out


def _notas(clip):
    notas = []
    for kt in clip.findall("Notes/KeyTracks/KeyTrack"):
        tecla = _v(kt, "MidiKey", tipo=int)
        for n in kt.findall("Notes/MidiNoteEvent"):
            if n.get("IsEnabled") == "false":
                continue
            notas.append({"tick": _tick(n.get("Time")), "nota": tecla,
                          "largo_ticks": _tick(n.get("Duration")),
                          "velocidad": round(float(n.get("Velocity")))})
    return sorted(notas, key=lambda n: (n["tick"], n["nota"]))


def _clip(c, es_audio):
    item = {
        "id": int(c.get("Id")),
        "nombre": _v(c, "Name", ""),
        "tick": _tick(_v(c, "CurrentStart", 0, float)),
        "fin_tick": _tick(_v(c, "CurrentEnd", 0, float)),
        "color": _v(c, "Color", tipo=int),
        "desactivado": _v(c, "Disabled", False, bool),
        "bucle": _v(c, "Loop/LoopOn", False, bool),
        "inicio_contenido_ticks": _tick(_v(c, "Loop/LoopStart", 0, float)),
    }
    if es_audio:
        ref = c.find("SampleRef/FileRef")
        item["archivo"] = _v(ref, "Path") if ref is not None else None
        item["archivo_relativo"] = _v(ref, "RelativePath") if ref is not None else None
        item["warp"] = _v(c, "IsWarped", False, bool)
        item["ganancia"] = _v(c, "SampleVolume", tipo=float)
        item["marcas_warp"] = [{"segundos": float(w.get("SecTime")), "tick": _tick(w.get("BeatTime"))}
                               for w in c.findall("WarpMarkers/WarpMarker")]
    else:
        item["notas"] = _notas(c)
    return item


_TIPOS = {"AudioTrack": "audio", "MidiTrack": "midi", "ReturnTrack": "retorno", "GroupTrack": "grupo"}


def leer(ruta):
    """Lee un .als y devuelve un dict con el formato trarin/0."""
    ruta = Path(ruta)
    raiz = leer_xml(ruta)
    ls = raiz.find("LiveSet")
    principal = ls.find("MainTrack")
    if principal is None:
        principal = ls.find("MasterTrack")
    mx = principal.find("DeviceChain/Mixer")
    envs = _envolventes(principal)

    tempo_id = _destino(mx.find("Tempo"))
    tempo = [{"tick": e["tick"], "bpm": float(e["valor"]), "pulso_ticks": TICKS}
             for e in envs.get(tempo_id, [])]
    if not tempo:
        tempo = [{"tick": 0, "bpm": _v(mx, "Tempo/Manual", 120.0, float), "pulso_ticks": TICKS}]
    compas_id = _destino(mx.find("TimeSignature"))
    eventos_compas = envs.get(compas_id) or [{"tick": 0, "valor": _v(mx, "TimeSignature/Manual", "201")}]
    compases = _compases_pt([dict(tick=e["tick"], **_compas_de_id(int(e["valor"]))) for e in eventos_compas])

    def ubicar(ev):
        ev["posicion"] = tiempo.compas_tiempo(ev["tick"], compases)
        ev["segundos"] = round(tiempo.segundos(ev["tick"], tempo), 6)
        return ev

    marcadores = [ubicar({"numero": int(l.get("Id")), "nombre": _v(l, "Name", ""),
                          "tick": _tick(_v(l, "Time", 0, float))})
                  for l in ls.findall("Locators/Locators/Locator")]

    pistas_xml = [p for p in ls.find("Tracks") if p.tag in _TIPOS]
    nombres_retorno = [_v(p, "Name/EffectiveName") for p in pistas_xml if p.tag == "ReturnTrack"]
    ids = {p.get("Id"): _v(p, "Name/EffectiveName") for p in pistas_xml}
    pistas = []
    for p in pistas_xml:
        mezcla, parametros = _mezcla(p, nombres_retorno)
        dc = p.find("DeviceChain")
        clips = [_clip(c, True) for c in dc.findall("MainSequencer/Sample/ArrangerAutomation/Events/AudioClip")]
        clips += [_clip(c, False) for c in dc.findall("MainSequencer/ClipTimeable/ArrangerAutomation/Events/MidiClip")]
        for c in clips:
            ubicar(c)
        session = []
        for k, slot in enumerate(dc.findall("MainSequencer/ClipSlotList/ClipSlot")):
            for c in slot.findall("ClipSlot/Value/*"):
                if c.tag in ("AudioClip", "MidiClip"):
                    item = _clip(c, c.tag == "AudioClip")
                    for clave in ("tick", "fin_tick"):
                        item.pop(clave)
                    item["escena"] = k + 1
                    item["largo_ticks"] = _tick(_v(c, "Loop/LoopEnd", 0, float) - _v(c, "Loop/LoopStart", 0, float))
                    session.append(item)
        auto = {}
        for pid, ev in _envolventes(p).items():
            nombre = parametros.get(pid)
            if nombre:
                auto[nombre] = [{"tick": e["tick"], "valor": float(e["valor"]) if e["valor"] not in ("true", "false") else e["valor"] == "true"} for e in ev]
        grupo = _v(p, "TrackGroupId", tipo=str)
        pistas.append({
            "id": int(p.get("Id")),
            "nombre": _v(p, "Name/EffectiveName"),
            "tipo": _TIPOS[p.tag],
            "grupo": ids.get(grupo) if grupo not in (None, "-1") else None,
            "color": _v(p, "Color", tipo=int),
            "clips": sorted(clips, key=lambda c: c["tick"]),
            "clips_session": session,
            "mezcla": mezcla,
            "dispositivos": _dispositivos(p),
            "automatizacion": auto,
        })

    mezcla_principal, _ = _mezcla(principal, [])
    escenas = [_v(e, "Name", "") for e in ls.findall("Scenes/Scene")]
    return {
        "formato": FORMATO,
        "escenas": escenas,
        "origen": {"daw": "ableton", "producto": raiz.get("Creator"),
                   "version": _version(raiz.get("MinorVersion")), "version_completa": raiz.get("MinorVersion"),
                   "archivo": ruta.name},
        "unidades": {"ticks_por_negra": TICKS},
        "tempo": [ubicar(e) for e in tempo],
        "compases": [ubicar(e) for e in compases],
        "marcadores": marcadores,
        "pistas": pistas,
        "principal": {"mezcla": mezcla_principal, "dispositivos": _dispositivos(principal)},
        "pendiente": ["parámetros de dispositivos",
                      "interpolación lineal entre puntos de tempo (se informan los puntos)"],
    }


def _version(minor):
    m = re.match(r"(\d+)", minor or "")
    return int(m.group(1)) if m else None


def _compases_pt(compases):
    """Adapta los compases al formato que usa tiempo.compas_tiempo."""
    out, compas, previo = [], 1, None
    for c in compases:
        if previo:
            largo = previo["numerador"] * 4 * TICKS // previo["denominador"]
            compas += (c["tick"] - previo["tick"]) // largo
        out.append({"tick": c["tick"], "compas": compas, "numerador": c["numerador"],
                    "denominador": c["denominador"]})
        previo = c
    return out

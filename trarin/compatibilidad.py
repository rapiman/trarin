"""Consulta de compatibilidad entre sesiones antes de juntarlas en un show.

Solo lee: no cambia ninguna sesión. Informa, por sesión, frecuencia de muestreo,
bits de los archivos, tempo, compases, duración y pistas; y entre sesiones:

* qué pistas coinciden y cómo (mismo nombre, o nombre parecido: sin
  mayúsculas, tildes, espacios ni sufijos como «.01» o «_2»);
* para cada pista que coincide, las diferencias de tipo, canales, mezcla,
  inserts (plugins), entrada y salida;
* el ruteo de cada sesión (entradas, salidas y envíos) y lo que no comparten;
* una propuesta de match (qué pista de cada canción va a qué pista del show),
  con las dudas marcadas para decidir a mano.

Por ahora solo sesiones de Pro Tools (.ptx).
"""

import re
import unicodedata
from pathlib import Path

from .comun import a_comun
from .protools import leer
from .protools.bloques import arbol, buscar
from .protools.descifrado import leer_descifrado


class ErrorCompatibilidad(Exception):
    pass


def clave(nombre):
    """Nombre normalizado para buscar coincidencias: minúsculas, sin tildes ni
    espacios, guiones o puntos, y sin sufijos numéricos (.01, _2, -3, «1»)."""
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"([._\- ]\d{1,3})+$", "", s.strip())
    return re.sub(r"[\s._\-]+", "", s)


def _tipo(t):
    return {"0x0007": "instrumento"}.get(t, t)


def _bits(ruta):
    """Bits de los archivos de audio de la sesión (0x1001: u8 en +7)."""
    d = leer_descifrado(ruta)
    out = set()
    for b in buscar(arbol(d), 0x1001):
        if b.inicio + 8 <= b.fin:
            out.add(d[b.inicio + 7])
    return sorted(x for x in out if x in (16, 24, 32))


def _sesion(ruta):
    ruta = Path(ruta)
    if ruta.suffix.lower() != ".ptx":
        raise ErrorCompatibilidad(f"{ruta.name}: por ahora solo sesiones de Pro Tools (.ptx)")
    nativo = leer(str(ruta))
    comun = a_comun(nativo)
    fs = comun["unidades"]["frecuencia_muestreo"]
    canales = {a["nombre"]: len(a["canales"]) for a in nativo.get("pistas_audio", [])}
    mezcla = {m.get("nombre"): m for m in nativo.get("mezcla", [])}
    fin = 0
    pistas = []
    for p in comun["pistas"]:
        m = p.get("mezcla") or {}
        nat = mezcla.get(p["nombre"], {})
        for c in p["clips"]:
            fin = max(fin, c.get("segundos", 0) or 0)
            if c.get("tipo") == "audio" and c.get("muestra") is not None and c.get("largo_muestras"):
                fin = max(fin, (c["muestra"] + c["largo_muestras"]) / fs)
        pistas.append({
            "nombre": p["nombre"],
            "tipo": p["tipo"],
            "canales": canales.get(p["nombre"]) or (2 if isinstance(m.get("paneo"), list) else None),
            "clips": len(p["clips"]),
            "volumen_db": m.get("volumen_db"),
            "paneo": m.get("paneo"),
            "mute": m.get("mute"),
            "entrada": m.get("entrada"),
            "salida": m.get("salida"),
            "envios": [e.get("destino") for e in m.get("envios") or []],
            "inserts": [i["plugin"] for i in nat.get("inserts", [])],
        })
    tempo = comun["tempo"]
    return {
        "archivo": str(ruta),
        "nombre": ruta.stem,
        "version": comun["origen"].get("version_completa"),
        "frecuencia_muestreo": fs,
        "bits": _bits(str(ruta)),
        "tempo_inicial": tempo[0]["bpm"] if tempo else None,
        "tempo_final": tempo[-1]["bpm"] if tempo else None,
        "cambios_tempo": max(len(tempo) - 1, 0),
        "compas_inicial": (f"{comun['compases'][0]['numerador']}/{comun['compases'][0]['denominador']}"
                           if comun["compases"] else None),
        "cambios_compas": max(len(comun["compases"]) - 1, 0),
        "duracion_s": round(fin, 2),
        "pistas": pistas,
    }


def informe(rutas):
    """Consulta de compatibilidad de varias sesiones (en el orden del show)."""
    if len(rutas) < 2:
        raise ErrorCompatibilidad("se necesitan al menos dos sesiones")
    sesiones = [_sesion(r) for r in rutas]
    avisos = []
    fss = {s["frecuencia_muestreo"] for s in sesiones}
    if len(fss) > 1:
        avisos.append({"nivel": "bloqueo", "texto": "frecuencias de muestreo distintas: "
                       + ", ".join(f"{s['nombre']} {s['frecuencia_muestreo']} Hz" for s in sesiones)})
    bits = {tuple(s["bits"]) for s in sesiones if s["bits"]}      # sin archivos de audio, no cuenta
    if len(bits) > 1:
        avisos.append({"nivel": "aviso", "texto": "bits de los archivos distintos: "
                       + ", ".join(f"{s['nombre']} {s['bits']}" for s in sesiones if s["bits"])})
    for a, b in zip(sesiones, sesiones[1:]):
        if a["tempo_final"] != b["tempo_inicial"]:
            avisos.append({"nivel": "info", "texto": f"cambio de tempo entre {a['nombre']} "
                           f"({a['tempo_final']}) y {b['nombre']} ({b['tempo_inicial']})"})

    # coincidencias de pistas entre sesiones: primero por nombre exacto; las que
    # quedan solas, por nombre parecido, una por sesión. Dentro de una misma
    # sesión nunca se juntan pistas.
    todas = [(i, p) for i, s_ in enumerate(sesiones) for p in s_["pistas"] if p["tipo"] != "master"]
    exactos = {}
    for i, p in todas:
        exactos.setdefault(p["nombre"], []).append((i, p))
    grupos, solas = [], []
    for nombre, miembros in exactos.items():
        (grupos if len({i for i, _ in miembros}) > 1 else solas).append(miembros)
    parecidas = {}
    for miembros in solas:
        for i, p in miembros:
            parecidas.setdefault(clave(p["nombre"]), []).append((i, p))
    propias = {}
    for k, miembros in parecidas.items():
        sesiones_k = [i for i, _ in miembros]
        if len(set(sesiones_k)) > 1 and len(set(sesiones_k)) == len(sesiones_k):
            grupos.append(miembros)
        else:
            for i, p in miembros:
                propias.setdefault(sesiones[i]["nombre"], []).append(p["nombre"])
    match = []
    for miembros in grupos:
        nombres = sorted({p["nombre"] for _, p in miembros})
        por_sesion = {}
        for i, p in miembros:
            por_sesion.setdefault(i, []).append(p)
        dudas = []
        if len(nombres) > 1:
            dudas.append("nombres parecidos, no iguales: " + ", ".join(nombres))
        if any(len(v) > 1 for v in por_sesion.values()):
            dudas.append("más de una pista con este nombre en la misma sesión")
        ps = [p for _, p in miembros]
        tipos = {_tipo(p["tipo"]) for p in ps}
        if len(tipos) > 1:
            dudas.append("tipos distintos: " + ", ".join(sorted(tipos)))
        diferencias = [campo for campo in ("canales", "volumen_db", "paneo", "mute", "entrada", "salida",
                                           "inserts", "envios")
                       if len({repr(p[campo]) for p in ps}) > 1]
        match.append({
            "pista_show": min(nombres, key=lambda n: (len(n), n)),
            "tipo": "/".join(sorted(tipos)),
            "sesiones": {sesiones[i]["nombre"]: [p["nombre"] for p in v] for i, v in sorted(por_sesion.items())},
            "en_todas": len(por_sesion) == len(sesiones),
            "diferencias": diferencias,
            "dudas": dudas,
            "detalle": {sesiones[i]["nombre"]: [{c: p[c] for c in ("tipo", "canales", "volumen_db", "paneo",
                                                                   "mute", "entrada", "salida", "inserts")}
                                                for p in v] for i, v in sorted(por_sesion.items())},
        })
    match.sort(key=lambda m: (not m["en_todas"], m["pista_show"].lower()))

    # ruteo: cada entrada, salida y destino de envío con las sesiones que lo usan
    ruteo = {"entradas": {}, "salidas": {}, "envios": {}}
    for s_ in sesiones:
        for p in s_["pistas"]:
            for k, vals in (("entradas", [p["entrada"]]), ("salidas", [p["salida"]]), ("envios", p["envios"])):
                for v in vals:
                    if v:
                        ruteo[k].setdefault(v, set()).add(s_["nombre"])
    n = len(sesiones)
    ruteo_out = {k: {"en_todas": sorted(x for x, ss in v.items() if len(ss) == n),
                     "no_en_todas": {x: sorted(ss) for x, ss in sorted(v.items()) if len(ss) < n}}
                 for k, v in ruteo.items()}

    plugins = {}
    for s in sesiones:
        for p in s["pistas"]:
            for x in p["inserts"]:
                plugins.setdefault(x, set()).add(s["nombre"])
    faltan = {x: sorted(set(s["nombre"] for s in sesiones) - v) for x, v in plugins.items()
              if len(v) < len(sesiones)}
    return {
        "sesiones": [{k: v for k, v in s.items() if k != "pistas"} | {"pistas": len(s["pistas"])}
                     for s in sesiones],
        "avisos": avisos,
        "match": match,
        "propias": {s_["nombre"]: sorted(propias.get(s_["nombre"], []), key=str.lower) for s_ in sesiones},
        "ruteo": ruteo_out,
        "plugins": {x: sorted(v) for x, v in sorted(plugins.items())},
        "plugins_que_faltan": faltan,
    }


def texto(inf):
    """Informe legible."""
    L = ["# Compatibilidad", ""]
    L.append("## Sesiones")
    for s in inf["sesiones"]:
        L.append(f"- {s['nombre']}: {s['frecuencia_muestreo']} Hz, bits {s['bits']}, tempo "
                 f"{s['tempo_inicial']}→{s['tempo_final']} ({s['cambios_tempo']} cambios), compás "
                 f"{s['compas_inicial']} ({s['cambios_compas']} cambios), {s['duracion_s']} s, "
                 f"{s['pistas']} pistas, Pro Tools {s['version']}")
    if inf["avisos"]:
        L += ["", "## Avisos"]
        L += [f"- [{a['nivel']}] {a['texto']}" for a in inf["avisos"]]
    L += ["", "## Propuesta de match (pistas en más de una sesión)"]
    if not inf["match"]:
        L.append("- ninguna pista coincide entre sesiones")
    for m in inf["match"]:
        donde = "; ".join(f"{s}: {', '.join(v)}" for s, v in m["sesiones"].items())
        L.append(f"- **{m['pista_show']}** ({m['tipo']}) ← {donde}"
                 + ("" if m["en_todas"] else " (no está en todas)"))
        if m["diferencias"]:
            L.append(f"  - difiere en: {', '.join(m['diferencias'])}")
        for dd in m["dudas"]:
            L.append(f"  - duda: {dd}")
    L += ["", "## Pistas propias de cada sesión (van como pistas aparte)"]
    for n, v in inf["propias"].items():
        L.append(f"- {n} ({len(v)}): {', '.join(v) if v else '—'}")
    L += ["", "## Ruteo"]
    for k, v in inf["ruteo"].items():
        if v["en_todas"]:
            L.append(f"- {k} en todas: {', '.join(v['en_todas'])}")
        for x, ss in v["no_en_todas"].items():
            L.append(f"- {k[:-1] if k != 'envios' else 'envío'} «{x}» solo en {', '.join(ss)}")
    if inf["plugins_que_faltan"]:
        L += ["", "## Plugins que no están en todas las sesiones"]
        L += [f"- {x}: falta en {', '.join(v)}" for x, v in inf["plugins_que_faltan"].items()]
    return "\n".join(L) + "\n"

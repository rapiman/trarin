"""Modelo común trarin/1: un mismo JSON para Pro Tools, Ableton Live y FL Studio.

    leer_comun(ruta)                       -> dict trarin/1
    aplicar(comun_editado, plantilla, salida)  -> escribe la sesión editada

Convenciones:
- Posiciones musicales en ticks (960 000 por negra). En Pro Tools la fuente de
  verdad del audio son las muestras ("base_tiempo": "muestras"); en Ableton y
  FL Studio, los ticks ("base_tiempo": "ticks"). Los campos "posicion"
  (compás|tiempo|ticks) y "segundos" son solo de lectura.
- Volumen en dB; paneo de −1 (izquierda) a 1 (derecha); en pistas estéreo de
  Pro Tools el paneo es [izquierdo, derecho].
- Notas de un clip MIDI en ticks relativos al inicio del clip.
- Lo propio de cada DAW que no encaja en lo común va en "nativo" y no se edita.
- FL Studio: cada canal es una pista; cada aparición de un patrón en la
  playlist genera un clip en las pistas de los canales que suenan en él. Los
  clips de un mismo patrón comparten notas: editar las notas de uno cambia el
  patrón (todas sus apariciones).
"""

from pathlib import Path

from .protools import tiempo as T

FORMATO = "trarin/1"


class ErrorComun(Exception):
    pass


# --------------------------------------------------------------------- lectura

def leer_nativo(ruta):
    ext = Path(ruta).suffix.lower()
    if ext == ".als":
        from .ableton import leer
    elif ext == ".flp":
        from .flstudio import leer
    else:
        from .protools import leer
    return leer(ruta)


def leer_comun(ruta):
    return a_comun(leer_nativo(ruta))


def a_comun(n):
    daw = n["origen"]["daw"]
    base = {
        "formato": FORMATO,
        "origen": n["origen"],
        "unidades": {"ticks_por_negra": T.TICKS_NEGRA,
                     "frecuencia_muestreo": n["origen"].get("frecuencia_muestreo")},
        "base_tiempo": "muestras" if daw == "protools" else "ticks",
        "tempo": [{"tick": t["tick"], "bpm": t["bpm"]} for t in n["tempo"]],
        "compases": [{"tick": c["tick"], "numerador": c["numerador"], "denominador": c["denominador"]}
                     for c in n["compases"]],
    }
    if daw == "protools":
        base.update(_desde_protools(n))
    elif daw == "ableton":
        base.update(_desde_ableton(n))
    else:
        base.update(_desde_flstudio(n))
    compases = n["compases"] if daw != "flstudio" else [{"tick": 0, "compas": 1, **base["compases"][0]}]
    for p in base["pistas"]:
        for c in p["clips"]:
            c["posicion"] = T.compas_tiempo(c["tick"], compases)
            c["segundos"] = round(T.segundos(c["tick"], n["tempo"]), 6)
    for m in base["marcadores"]:
        m["posicion"] = T.compas_tiempo(m["tick"], compases)
    return base


def _marcador(m, id_):
    out = {"id": id_, "nombre": m.get("nombre"), "tick": m["tick"]}
    for k in ("fin_tick", "comentario"):
        if k in m:
            out[k] = m[k]
    return out


# ---- Pro Tools

def _desde_protools(n):
    fs = n["origen"].get("frecuencia_muestreo") or 48000
    regiones = n["audio"]["regiones"]
    midi = n["midi"]["regiones"]
    mezcla = {m.get("nombre"): m for m in n.get("mezcla", [])}
    tracks_audio = {p["nombre"]: p for p in n["pistas_audio"]}
    tracks_midi = {p["nombre"]: p for p in n["pistas_midi"]}
    pistas = []
    for k, p in enumerate(n["pistas"]):
        m = mezcla.get(p["nombre"], {})
        ta = tracks_audio.get(p["nombre"])
        paneo = m.get("paneo")
        if isinstance(paneo, list):
            estereo = (ta and len(ta["canales"]) == 2) or (not ta and paneo[1] != 0)
            paneo = [paneo[0] / 100, paneo[1] / 100] if estereo else paneo[0] / 100
        elif isinstance(paneo, (int, float)):
            paneo = paneo / 100
        clips = []
        if ta and ta["canales"]:
            canal = ta["canales"][0]
            for j, r in enumerate(canal["regiones"]):
                reg = regiones[r["indice_region"]]
                c = {"id": f"{p['nombre']}#{j}", "tipo": "audio", "nombre": reg["nombre"],
                     "muestra": r["muestra"], "largo_muestras": reg["muestras"],
                     "tick": T.tick_de_segundos(r["muestra"] / fs, n["tempo"]),
                     "archivo": reg["archivo"], "desde_muestra": reg["desde_muestra"],
                     "canales": len(ta["canales"])}
                fin = T.tick_de_segundos((r["muestra"] + reg["muestras"]) / fs, n["tempo"])
                c["largo_ticks"] = fin - c["tick"]
                if "clip_gain_db" in reg:
                    c["ganancia_db"] = reg["clip_gain_db"]
                if r.get("mute"):
                    c["mute"] = True
                clips.append(c)
            fades = canal.get("fundidos", [])
            if fades:
                for c in clips:
                    ini, fin = c["muestra"], c["muestra"] + c["largo_muestras"]
                    f = [x for x in fades if ini <= x["muestra"] < fin or ini < x["muestra"] + x["muestras"] <= fin]
                    if f:
                        c["fundidos"] = [{k2: v for k2, v in x.items() if k2 not in ("segundos", "posicion")} for x in f]
        tm = tracks_midi.get(p["nombre"])
        if tm:
            for j, r in enumerate(tm["regiones"]):
                reg = midi[r["indice_region"]]
                clips.append({"id": f"{p['nombre']}#m{j}", "tipo": "midi", "nombre": reg["nombre"],
                              "tick": r["tick"], "largo_ticks": reg["ticks"],
                              "notas": [{"tick": x["tick"], "largo_ticks": x["ticks"], "nota": x["nota"],
                                         "velocidad": x["velocidad"]} for x in reg["notas"]]})
        pistas.append({
            "id": p["nombre"], "nombre": p["nombre"],
            "tipo": {"auxiliar": "retorno"}.get(p["tipo"], p["tipo"]),
            "color": m.get("color"),
            "mezcla": {"volumen_db": m.get("volumen_db"), "paneo": paneo, "mute": m.get("mute"),
                       "solo": m.get("solo"), "entrada": m.get("entrada"), "salida": m.get("salida"),
                       "envios": [{"destino": e.get("destino"), "volumen_db": e.get("volumen_db")}
                                  for e in m.get("envios", [])]},
            "efectos": [{"nombre": i.get("plugin"), "fabricante": i.get("fabricante")} for i in m.get("inserts", [])],
            "automatizacion": m.get("automatizacion", {}),
            "clips": sorted(clips, key=lambda c: c["tick"]),
        })
    master = [x for x in n.get("mezcla", []) if x.get("tipo") == "master"]
    return {
        "marcadores": [_marcador(m, m["numero"]) for m in n["marcadores"]],
        "archivos": [{"id": a["indice"], "ruta": a["nombre"], "muestras": a.get("muestras")}
                     for a in n["audio"]["archivos"]],
        "pistas": pistas,
        "nativo": {"timecode": n.get("timecode"), "grupos": n.get("grupos"),
                   "playlists_inactivas": [p["nombre"] for p in n.get("playlists_inactivas", [])],
                   "master": master},
    }


# ---- Ableton

def _desde_ableton(n):
    archivos, idx = [], {}
    pistas = []
    for p in n["pistas"]:
        clips = []
        for c in p["clips"]:
            x = {"id": c["id"], "tipo": "audio" if "archivo" in c else "midi", "nombre": c["nombre"],
                 "tick": c["tick"], "largo_ticks": c["fin_tick"] - c["tick"]}
            if c.get("desactivado"):
                x["mute"] = True
            if x["tipo"] == "audio":
                ruta = c.get("archivo") or c.get("archivo_relativo")
                if ruta not in idx:
                    idx[ruta] = len(archivos)
                    archivos.append({"id": idx[ruta], "ruta": ruta})
                x["archivo"] = idx[ruta]
                if c.get("ganancia"):
                    from math import log10
                    x["ganancia_db"] = round(20 * log10(c["ganancia"]), 2)
            else:
                x["notas"] = [{k: v for k, v in nt.items()} for nt in c["notas"]]
            clips.append(x)
        m = p["mezcla"]
        pistas.append({
            "id": p["id"], "nombre": p["nombre"], "tipo": p["tipo"], "grupo": p.get("grupo"),
            "color": p.get("color"),
            "mezcla": {"volumen_db": m.get("volumen_db"), "paneo": m.get("paneo"), "mute": m.get("mute"),
                       "solo": m.get("solo"), "entrada": m.get("entrada"), "salida": m.get("salida"),
                       "envios": m.get("envios", [])},
            "efectos": [{"nombre": d.get("nombre") or d["tipo"], "activo": d.get("activo")} for d in p["dispositivos"]],
            "automatizacion": p.get("automatizacion", {}),
            "clips": clips,
        })
    return {
        "marcadores": [_marcador(m, m["numero"]) for m in n["marcadores"]],
        "archivos": archivos,
        "pistas": pistas,
        "nativo": {"escenas": n.get("escenas"), "principal": n.get("principal"),
                   "clips_session": {p["id"]: p["clips_session"] for p in n["pistas"] if p.get("clips_session")}},
    }


# ---- FL Studio

def _desde_flstudio(n):
    patrones = {p["numero"]: p for p in n["patrones"]}
    archivos, idx = [], {}
    pistas = {}
    for c in n["canales"]:
        if c.get("tipo") == "automatizacion":
            continue
        pistas[c["indice"]] = {
            "id": c["indice"], "nombre": c.get("nombre"), "tipo": "audio" if c.get("tipo") == "sampler" else "midi",
            "color": None,
            "mezcla": {"insert": c.get("insert")},
            "efectos": [], "automatizacion": {}, "clips": [],
        }
        if c.get("archivo"):
            pistas[c["indice"]]["archivo_sampler"] = c["archivo"]
    marcadores = []
    for a_i, a in enumerate(n["arreglos"]):
        for m in a["marcadores"]:
            marcadores.append(_marcador(m, f"{a_i}:{len(marcadores)}"))
        for i_i, it in enumerate(a["items"]):
            if "patron" in it:
                pat = patrones.get(it["patron"])
                if not pat:
                    continue
                for canal in sorted({nt["canal"] for nt in pat["notas"]}):
                    if canal not in pistas:
                        continue
                    pistas[canal]["clips"].append({
                        "id": f"{a_i}:{i_i}:{canal}", "tipo": "midi",
                        "nombre": pat.get("nombre") or f"Patrón {it['patron']}",
                        "patron": it["patron"], "tick": it["tick"], "largo_ticks": it["largo_ticks"],
                        "notas": [{"tick": nt["tick"], "largo_ticks": nt["largo_ticks"], "nota": nt["nota"],
                                   "velocidad": nt["velocidad"]} for nt in pat["notas"] if nt["canal"] == canal],
                    })
            elif it.get("canal") in pistas:
                p = pistas[it["canal"]]
                ruta = p.get("archivo_sampler")
                if ruta not in idx:
                    idx[ruta] = len(archivos)
                    archivos.append({"id": idx[ruta], "ruta": ruta})
                p["clips"].append({"id": f"{a_i}:{i_i}:{it['canal']}", "tipo": "audio", "nombre": p["nombre"],
                                   "tick": it["tick"], "largo_ticks": it["largo_ticks"], "archivo": idx[ruta]})
    for p in pistas.values():
        p.pop("archivo_sampler", None)
        p["clips"].sort(key=lambda c: c["tick"])
        if p["mezcla"].get("insert") is not None:
            p["mezcla"]["salida"] = f"insert:{p['mezcla']['insert']}"
    inserts = [_pista_insert(m) for m in n.get("mezcla") or []]
    return {
        "marcadores": marcadores,
        "archivos": archivos,
        "pistas": list(pistas.values()) + inserts,
        "nativo": {"arreglos": [{"nombre": a["nombre"], "pistas": a["pistas"]} for a in n["arreglos"]]},
    }


def _pista_insert(m):
    """Un insert del mezclador de FL como pista: 'master' (insert 0) o 'retorno'."""
    num = m["numero"]
    return {
        "id": f"insert:{num}", "nombre": m.get("nombre") or ("Master" if num == 0 else f"Insert {num}"),
        "tipo": "master" if num == 0 else "retorno", "color": None,
        "mezcla": {"volumen_db": m.get("volumen_db", 0.0), "paneo": m.get("paneo", 0.0)},
        "efectos": [{"nombre": e.get("nombre") or e.get("plugin"), "slot": e.get("slot")} for e in m["efectos"]],
        "automatizacion": {}, "clips": [],
    }


# ------------------------------------------------------------------- escritura

def aplicar(comun, plantilla, salida):
    """Aplica los cambios de un JSON común editado sobre una copia de 'plantilla'."""
    nativo = leer_nativo(plantilla)
    original = a_comun(nativo)
    daw = nativo["origen"]["daw"]
    if daw == "protools":
        return _a_protools(comun, original, nativo, plantilla, salida)
    if comun["tempo"] != original["tempo"]:
        nativo["tempo"] = [dict(t, pulso_ticks=T.TICKS_NEGRA) for t in comun["tempo"]]
    if comun["compases"] != original["compases"]:
        nativo["compases"] = [dict(c) for c in comun["compases"]]
    if daw == "ableton":
        _a_ableton(comun, original, nativo)
        from .ableton import escribir
    else:
        _a_flstudio(comun, original, nativo)
        from .flstudio import escribir
    return escribir(nativo, plantilla, salida)


def _por_id(lista):
    return {x["id"]: x for x in lista}


def _verificar_mismas_entidades(comun, original):
    po, pn = _por_id(original["pistas"]), _por_id(comun["pistas"])
    if set(po) != set(pn):
        raise ErrorComun("agregar o quitar pistas aún no está soportado")
    for i, p in pn.items():
        if set(_por_id(p["clips"])) != set(_por_id(po[i]["clips"])):
            raise ErrorComun(f"agregar o quitar clips aún no está soportado (pista {p['nombre']!r})")


def _a_ableton(comun, original, nativo):
    _verificar_mismas_entidades(comun, original)
    nativo["marcadores"] = [{"numero": m["id"] if isinstance(m.get("id"), int) else None,
                             "nombre": m["nombre"], "tick": m["tick"]} for m in comun["marcadores"]]
    pistas = _por_id(nativo["pistas"])
    for p in comun["pistas"]:
        x = pistas[p["id"]]
        x["nombre"] = p["nombre"]
        for k in ("volumen_db", "paneo", "mute"):
            x["mezcla"][k] = p["mezcla"].get(k)
        clips = _por_id(x["clips"])
        for c in p["clips"]:
            y = clips[c["id"]]
            y["tick"], y["fin_tick"], y["nombre"] = c["tick"], c["tick"] + c["largo_ticks"], c["nombre"]
            if c["tipo"] == "midi":
                y["notas"] = c["notas"]


def _a_flstudio(comun, original, nativo):
    _verificar_mismas_entidades(comun, original)
    if len(comun["tempo"]) > 1:
        raise ErrorComun("FL Studio: los cambios de tempo van en automatización (aún no soportado)")
    canales = {c["indice"]: c for c in nativo["canales"]}
    patrones = {p["numero"]: p for p in nativo["patrones"]}
    orig_clips = {c["id"]: c for p in original["pistas"] for c in p["clips"]}
    items, notas_patron = {}, {}
    orig_pistas = _por_id(original["pistas"])
    mezcla = {m["numero"]: m for m in nativo["mezcla"]}
    for p in comun["pistas"]:
        o = orig_pistas[p["id"]]
        if isinstance(p["id"], str) and p["id"].startswith("insert:"):
            if p["nombre"] != o["nombre"]:
                raise ErrorComun("FL Studio: cambiar el nombre de un insert aún no está soportado")
            m = mezcla[int(p["id"].split(":")[1])]
            for k in ("volumen_db", "paneo"):
                if p["mezcla"].get(k) != o["mezcla"].get(k):
                    m[k] = p["mezcla"][k]
            continue
        if p["mezcla"].get("salida") != o["mezcla"].get("salida"):
            raise ErrorComun("FL Studio: para cambiar el insert de un canal, editar 'mezcla.insert'")
        canales[p["id"]]["nombre"] = p["nombre"]
        if p["mezcla"].get("insert") != o["mezcla"].get("insert"):
            canales[p["id"]]["insert"] = p["mezcla"]["insert"]
        for c in p["clips"]:
            a_i, i_i, canal = (int(x) for x in c["id"].split(":"))
            pos = (c["tick"], c["largo_ticks"])
            if items.setdefault((a_i, i_i), pos) != pos:
                raise ErrorComun(f"los clips del item {a_i}:{i_i} quedaron en posiciones distintas; "
                                 "en FL todos los canales de un patrón se mueven juntos")
            if c["tipo"] == "midi" and c["notas"] != orig_clips[c["id"]]["notas"]:
                clave = (c["patron"], canal)
                if clave in notas_patron and notas_patron[clave] != c["notas"]:
                    raise ErrorComun(f"dos apariciones del patrón {c['patron']} tienen notas distintas para el canal {canal}")
                notas_patron[clave] = c["notas"]
    for (a_i, i_i), (tick, largo) in items.items():
        it = nativo["arreglos"][a_i]["items"][i_i]
        it["tick"], it["largo_ticks"] = tick, largo
    for (num, canal), notas in notas_patron.items():
        pat = patrones[num]
        otras = [x for x in pat["notas"] if x["canal"] != canal]
        paneos = {(x["tick"], x["nota"]): x.get("paneo", 0) for x in pat["notas"] if x["canal"] == canal}
        pat["notas"] = otras + [dict(x, canal=canal, paneo=paneos.get((x["tick"], x["nota"]), 0)) for x in notas]
    clave = lambda ms: [(m["nombre"], m["tick"]) for m in ms]
    if clave(comun["marcadores"]) != clave(original["marcadores"]):
        raise ErrorComun("FL Studio: la escritura de marcadores aún no está soportada")


_SOLO_LECTURA = {"posicion", "segundos"}


def _a_protools(comun, original, nativo, plantilla, salida):
    """Pro Tools: por ahora volumen, paneo y mute de pistas, y posición y mute
    de regiones de audio. Cualquier otro cambio da un error."""
    from .protools.escritura import escribir_cambios
    _verificar_mismas_entidades(comun, original)
    if [_sin(x) for x in comun["compases"]] != [_sin(x) for x in original["compases"]]:
        raise ErrorComun("Pro Tools: cambiar compases aún no está soportado")
    tempo = None
    if [_sin(x) for x in comun["tempo"]] != [_sin(x) for x in original["tempo"]]:
        tempo = [{"tick": t["tick"], "bpm": t["bpm"]} for t in comun["tempo"]]
        if tempo[0]["tick"] != original["tempo"][0]["tick"]:
            raise ErrorComun("Pro Tools: el primer evento de tempo no se puede mover")
    marcadores, altas, bajas = _cambios_marcadores_pt(comun["marcadores"], original["marcadores"])
    fs = nativo["origen"].get("frecuencia_muestreo") or 48000
    indices = {p["nombre"]: i for i, p in enumerate(nativo["pistas"])}
    orig = _por_id(original["pistas"])
    mezcla, regiones, midi, nombres = {}, {}, {}, {}
    for p in comun["pistas"]:
        o = orig[p["id"]]
        for k in set(p) | set(o):
            if k not in ("mezcla", "clips", "nombre") and p.get(k) != o.get(k):
                raise ErrorComun(f"Pro Tools: cambiar '{k}' de la pista {p['id']!r} aún no está soportado")
        if p["nombre"] != o["nombre"]:
            if not p["nombre"] or p["nombre"] in {x["nombre"] for x in comun["pistas"] if x is not p}:
                raise ErrorComun(f"Pro Tools: nombre de pista vacío o repetido: {p['nombre']!r}")
            nombres[o["nombre"]] = p["nombre"]
        cambio = {}
        for k in set(p["mezcla"]) | set(o["mezcla"]):
            if p["mezcla"].get(k) == o["mezcla"].get(k):
                continue
            if k not in ("volumen_db", "paneo", "mute"):
                raise ErrorComun(f"Pro Tools: cambiar mezcla.{k} de {p['id']!r} aún no está soportado")
            cambio[k] = p["mezcla"][k]
        if cambio:
            mezcla[indices[p["id"]]] = (p["id"], cambio)
        oc = _por_id(o["clips"])
        for c in p["clips"]:
            x = oc[c["id"]]
            if c["tipo"] != "audio":
                libres = _SOLO_LECTURA | {"tick"}
                if {k: v for k, v in c.items() if k not in libres} != {k: v for k, v in x.items() if k not in libres}:
                    raise ErrorComun(f"Pro Tools: en el clip MIDI {c['id']} solo se puede cambiar la posición (tick)")
                if c["tick"] != x["tick"]:
                    midi.setdefault(p["id"], {})[int(c["id"].rsplit("#m", 1)[1])] = c["tick"]
                continue
            cambio = {}
            if c["muestra"] != x["muestra"]:
                cambio["muestra"] = c["muestra"]
            elif c["tick"] != x["tick"]:
                cambio["muestra"] = round(T.segundos(c["tick"], nativo["tempo"]) * fs)
            if bool(c.get("mute")) != bool(x.get("mute")):
                cambio["mute"] = bool(c.get("mute"))
            libres = _SOLO_LECTURA | {"muestra", "tick", "mute", "largo_ticks", "fundidos"}
            fun = lambda y: [{k: v for k, v in f.items() if k != "muestra"} for f in y.get("fundidos", [])]
            if ({k: v for k, v in c.items() if k not in libres} != {k: v for k, v in x.items() if k not in libres}
                    or fun(c) != fun(x)):
                raise ErrorComun(f"Pro Tools: en {c['id']} solo se puede cambiar la posición y el mute")
            if cambio:
                j = int(c["id"].rsplit("#", 1)[1])
                regiones.setdefault(p["id"], {})[j] = cambio
    return escribir_cambios(plantilla, salida, mezcla=mezcla, regiones=regiones, marcadores=marcadores, tempo=tempo, midi=midi,
                            marcadores_nuevos=altas, marcadores_borrados=bajas, nombres=nombres)


def _cambios_marcadores_pt(nuevos, originales):
    """Cambios en marcadores existentes (por 'id'), marcadores nuevos (sin 'id'
    o con un 'id' que no existía) y marcadores borrados (los que faltan)."""
    orig = {m["id"]: m for m in originales}
    cambios, altas = {}, []
    vistos = set()
    for m in nuevos:
        o = orig.get(m.get("id"))
        if o is None or m.get("id") in vistos:
            if m.get("fin_tick") is not None:
                raise ErrorComun("Pro Tools: crear selecciones (marcadores con fin) aún no está soportado")
            if not m.get("nombre") or m.get("tick") is None or m["tick"] < 0:
                raise ErrorComun("Pro Tools: un marcador nuevo necesita 'nombre' y 'tick'")
            altas.append({"nombre": m["nombre"], "tick": m["tick"], "comentario": m.get("comentario")})
            continue
        vistos.add(m["id"])
        cambio = {k: m.get(k) for k in ("nombre", "tick", "fin_tick", "comentario") if m.get(k) != o.get(k)}
        if "fin_tick" in cambio and "fin_tick" not in o:
            raise ErrorComun(f"Pro Tools: el marcador {m['id']} no es una selección; no tiene fin")
        if cambio:
            cambios[m["id"]] = cambio
    bajas = [i for i in orig if i not in vistos]
    if any(not isinstance(i, int) for i in bajas):
        raise ErrorComun("Pro Tools: no se puede identificar un marcador a borrar")
    return cambios, altas, bajas

def _sin(x):
    return {k: v for k, v in x.items() if k not in _SOLO_LECTURA} if isinstance(x, dict) else x

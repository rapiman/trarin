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
    mapa = T.MapaTempo(n["tempo"])
    for p in base["pistas"]:
        for c in p["clips"]:
            c["posicion"] = T.compas_tiempo(c["tick"], compases)
            c["segundos"] = round(T.segundos(c["tick"], mapa), 6)
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
    mapa = T.MapaTempo(n["tempo"])
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
                     "tick": T.tick_de_segundos(r["muestra"] / fs, mapa),
                     "archivo": reg["archivo"], "desde_muestra": reg["desde_muestra"],
                     "canales": len(ta["canales"])}
                fin = T.tick_de_segundos((r["muestra"] + reg["muestras"]) / fs, mapa)
                c["largo_ticks"] = fin - c["tick"]
                if "clip_gain_db" in reg:
                    c["ganancia_db"] = reg["clip_gain_db"]
                elif reg.get("clip_gain"):                 # curva: [{muestra (desde el inicio del clip), db}]
                    c["ganancia_curva"] = [dict(x) for x in reg["clip_gain"]]
                if r.get("mute"):
                    c["mute"] = True
                if len(ta["canales"]) > 1:
                    c["archivos_canales"] = [
                        regiones[next((x for x in can["regiones"] if x["muestra"] == r["muestra"]), r)
                                 ["indice_region"]]["archivo"] for can in ta["canales"]]
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
    ids_orig = set(_por_id(original["pistas"]))
    ids_nuevos = {p.get("id") for p in comun["pistas"]}
    quitadas = [p for p in original["pistas"] if p["id"] not in ids_nuevos]
    if quitadas:
        return _pistas_quitadas_pt(comun, quitadas, plantilla, salida)
    nuevas = [p for p in comun["pistas"] if p.get("id") not in ids_orig]
    if nuevas:
        return _pistas_nuevas_pt(comun, original, nuevas, plantilla, salida)
    compases = None
    if [_sin(x) for x in comun["compases"]] != [_sin(x) for x in original["compases"]]:
        compases = [{k: c[k] for k in ("tick", "numerador", "denominador")} for c in comun["compases"]]
    tempo = None
    if [_sin(x) for x in comun["tempo"]] != [_sin(x) for x in original["tempo"]]:
        tempo = [{"tick": t["tick"], "bpm": t["bpm"]} for t in comun["tempo"]]
        if tempo[0]["tick"] != 0:
            raise ErrorComun("Pro Tools: el primer tempo debe estar en el tick 0")
    marcadores, altas, bajas = _cambios_marcadores_pt(comun["marcadores"], original["marcadores"])
    fs = nativo["origen"].get("frecuencia_muestreo") or 48000
    indices = {p["nombre"]: i for i, p in enumerate(nativo["pistas"])}
    orig = _por_id(original["pistas"])
    mezcla, regiones, midi, nombres, notas = {}, {}, {}, {}, {}
    clips_nuevos, clips_borrados, fundidos_nuevos = {}, {}, {}
    midi_nuevos = {"regiones": [], "clips": {}}
    altas_audio = {"archivos": [], "regiones": [], "indices": {}, "largo": {},
                   "base_regiones": len(nativo["audio"]["regiones"])}
    for p in comun["pistas"]:
        o = orig[p["id"]]
        for k in set(p) | set(o):
            if k not in ("mezcla", "clips", "nombre", "automatizacion") and p.get(k) != o.get(k):
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
        vistos = set()
        fundidos_pista = []
        for c in p["clips"]:
            x = oc.get(c.get("id"))
            if x is None or c.get("id") in vistos:
                if c.get("tipo") == "midi":
                    _clip_midi_nuevo_pt(c, p, original, nativo, midi_nuevos)
                else:
                    _clip_nuevo_pt(c, p, original, nativo, fs, clips_nuevos, altas_audio, comun)
                    fundidos_pista += c.get("fundidos") or []
                continue
            vistos.add(c["id"])
            if c["tipo"] != "audio":
                libres = _SOLO_LECTURA | {"tick", "notas"}
                if {k: v for k, v in c.items() if k not in libres} != {k: v for k, v in x.items() if k not in libres}:
                    raise ErrorComun(f"Pro Tools: en el clip MIDI {c['id']} solo se puede cambiar la posición y las notas")
                j = int(c["id"].rsplit("#m", 1)[1])
                if c["tick"] != x["tick"]:
                    midi.setdefault(p["id"], {})[j] = c["tick"]
                if c["notas"] != x["notas"]:
                    notas[(p["id"], j)] = c["notas"]
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
        if fundidos_pista:
            fundidos_nuevos[p["id"]] = _fundidos_nuevos_pt(p, fundidos_pista, nativo, fs)
        for i, x in oc.items():
            if i not in vistos:
                if x["tipo"] == "midi":
                    midi_nuevos.setdefault("bajas", {}).setdefault(p["id"], []).append(int(i.rsplit("#m", 1)[1]))
                    continue
                clips_borrados.setdefault(p["id"], []).append(int(i.rsplit("#", 1)[1]))
    # puntos de automatización cambiados (se escriben después del resto)
    autos = {p.get("nombre") or p["id"]: p.get("automatizacion") or {} for p in comun["pistas"]
             if p.get("id") in orig and (p.get("automatizacion") or {}) != (orig[p["id"]].get("automatizacion") or {})}
    res = escribir_cambios(plantilla, salida, mezcla=mezcla, regiones=regiones, marcadores=marcadores, tempo=tempo, midi=midi,
                           marcadores_nuevos=altas, marcadores_borrados=bajas, nombres=nombres,
                           compases=compases, notas=notas, clips_nuevos=clips_nuevos,
                           clips_borrados=clips_borrados, archivos_nuevos=altas_audio["archivos"],
                           regiones_nuevas=altas_audio["regiones"], midi_nuevos=midi_nuevos,
                           fundidos_nuevos=fundidos_nuevos)
    if autos:
        from .protools.descifrado import leer_descifrado
        from .protools.reconstruccion import cifrar
        from .protools.ruteo import en_lote, escribir_automatizacion
        d = leer_descifrado(str(salida))

        def una(nombre, auto):
            def f(dd, lote):
                try:
                    r = escribir_automatizacion(dd, nombre, auto, lote=lote)
                except Exception as e:
                    raise ErrorComun(f"Pro Tools: automatización de {nombre!r}: {e}") from e
                return (r, []) if lote is None else None
            return f
        d, _ = en_lote(d, [una(n, a) for n, a in autos.items()])     # todas las pistas de una vez
        Path(salida).write_bytes(cifrar(d))
    return res


def _pistas_quitadas_pt(comun, quitadas, plantilla, salida):
    """Quita primero las pistas que faltan en el modelo (de audio, MIDI o auxiliares) en una
    copia temporal y después aplica sobre esa copia el resto de los cambios."""
    import os
    import tempfile
    from .protools import leer
    from .protools.descifrado import leer_descifrado
    from .protools.pistas_quitar import quitar_pistas
    from .protools.reconstruccion import cifrar
    for p in quitadas:
        if p["tipo"] not in ("audio", "midi", "retorno"):
            raise ErrorComun(f"Pro Tools: por ahora solo se pueden quitar pistas de audio, MIDI o auxiliares ({p['id']!r})")
    d = quitar_pistas(leer_descifrado(plantilla), [p["id"] for p in quitadas])
    fd, tmp = tempfile.mkstemp(suffix=".ptx", dir=str(Path(salida).parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(cifrar(d))
        nativo2 = leer(tmp)
        return _a_protools(comun, a_comun(nativo2), nativo2, tmp, salida)
    finally:
        os.unlink(tmp)


def _pistas_nuevas_pt(comun, original, nuevas, plantilla, salida):
    """Crea primero las pistas nuevas (audio mono o estéreo según 'canales', a
    partir de una pista mono existente; MIDI con tipo 'midi'; auxiliares con tipo
    'retorno', copia de un auxiliar de la sesión; vacías) en una copia temporal y después aplica sobre
    esa copia el resto de los cambios, incluidos los clips y la mezcla de las
    pistas nuevas."""
    import os
    import tempfile
    from .protools import leer
    from .protools.descifrado import leer_descifrado
    from .protools.pistas_nuevas import agregar_auxiliares, agregar_pistas, agregar_pistas_midi
    from .protools.reconstruccion import cifrar
    usados = {p["nombre"] for p in original["pistas"]}
    nombres = []
    for p in nuevas:
        nombre = p.get("nombre")
        if not nombre or nombre in usados:
            raise ErrorComun(f"Pro Tools: una pista nueva necesita un nombre no repetido ({nombre!r})")
        tipo = p.get("tipo", "audio")
        canales = p.get("canales", 2 if tipo == "retorno" else 1)
        if not (tipo == "midi" or (tipo in ("audio", "retorno") and canales in (1, 2))):
            raise ErrorComun("Pro Tools: por ahora solo se pueden crear pistas de audio o auxiliares "
                             "(mono o estéreo), o MIDI")
        if tipo == "retorno" and p.get("clips"):
            raise ErrorComun(f"Pro Tools: un auxiliar no lleva clips ({nombre!r})")
        usados.add(nombre)
        nombres.append((tipo, nombre, canales))
    d = leer_descifrado(plantilla)
    tramo, tipo_tramo = [], None        # pistas seguidas del mismo tipo: en una sola pasada
    for tipo, nombre, canales in nombres + [(None, None, None)]:
        if tipo != tipo_tramo and tramo:
            if tipo_tramo == "midi":
                d = agregar_pistas_midi(d, [n for n, _ in tramo])
            elif tipo_tramo == "retorno":
                d = agregar_auxiliares(d, tramo)
            else:
                d = agregar_pistas(d, tramo)
            tramo = []
        tipo_tramo = tipo
        if tipo is not None:
            tramo.append((nombre, canales))
    fd, tmp = tempfile.mkstemp(suffix=".ptx", dir=str(Path(salida).parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(cifrar(d))
        nativo2 = leer(tmp)
        original2 = a_comun(nativo2)
        base = _por_id(original2["pistas"])
        pistas = []
        for p in comun["pistas"]:
            if p in nuevas:
                b = base[p["nombre"]]
                if p.get("tipo") == "midi":
                    mezcla = dict(b["mezcla"])
                else:
                    canales = p.get("canales", 2 if p.get("tipo") == "retorno" else 1)
                    paneo = [-1.0, 1.0] if canales == 2 else 0.0
                    mezcla = dict(b["mezcla"], volumen_db=0.0, paneo=paneo, mute=False)
                mezcla.update({k: v for k, v in (p.get("mezcla") or {}).items() if v is not None})
                p = dict(b, mezcla=mezcla, clips=p.get("clips", []))
            pistas.append(p)
        comun2 = dict(comun, pistas=pistas)
        return _a_protools(comun2, original2, nativo2, tmp, salida)
    finally:
        os.unlink(tmp)


def _cambios_marcadores_pt(nuevos, originales):
    """Cambios en marcadores existentes (por 'id'), marcadores nuevos (sin 'id'
    o con un 'id' que no existía) y marcadores borrados (los que faltan)."""
    orig = {m["id"]: m for m in originales}
    cambios, altas = {}, []
    vistos = set()
    for m in nuevos:
        o = orig.get(m.get("id"))
        if o is None or m.get("id") in vistos:
            if not m.get("nombre") or m.get("tick") is None or m["tick"] < 0:
                raise ErrorComun("Pro Tools: un marcador nuevo necesita 'nombre' y 'tick'")
            altas.append({"nombre": m["nombre"], "tick": m["tick"], "comentario": m.get("comentario"),
                          "fin_tick": m.get("fin_tick")})
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

def _fundidos_nuevos_pt(pista, fundidos, nativo, fs):
    """Fundidos de los clips nuevos de una pista, sin repetir (un cruce figura
    en los dos clips), con su pre-roll: 0 en una entrada, la duración en una
    salida, y en un cruce lo que va del inicio del fundido al inicio del clip
    que empieza dentro de él (el ancla de Pro Tools)."""
    inicios = []
    for c in pista["clips"]:
        if c.get("tipo") == "audio":
            m = c.get("muestra")
            inicios.append(m if m is not None else round(T.segundos(c["tick"], nativo["tempo"]) * fs))
    out, vistos = [], set()
    for f in fundidos:
        k = (f.get("tipo"), f.get("muestra"), f.get("muestras"))
        if k in vistos:
            continue
        vistos.add(k)
        ini, dur = int(f["muestra"]), int(f["muestras"])
        if f["tipo"] == "entrada":
            pre = 0
        elif f["tipo"] == "salida":
            pre = dur
        elif f["tipo"] == "cruzado":
            dentro = [m for m in inicios if ini < m < ini + dur]
            if not dentro:                  # cruzado que empieza o termina justo en el inicio del clip
                dentro = [m for m in inicios if m in (ini, ini + dur)]
            if len(dentro) != 1:
                raise ErrorComun(f"Pro Tools: el fundido cruzado en {ini} de {pista['id']!r} debe abarcar "
                                 f"el inicio de un clip")
            pre = dentro[0] - ini
        else:
            raise ErrorComun(f"Pro Tools: tipo de fundido desconocido {f.get('tipo')!r}")
        out.append(dict(f, pre_roll=pre))
    return out


def _clip_nuevo_pt(c, pista, original, nativo, fs, clips_nuevos, altas, comun):
    """Clip de audio nuevo en Pro Tools. Si hay un clip de la sesión con el mismo
    archivo, desde_muestra y largo_muestras (y la misma cantidad de canales), se
    reutilizan sus regiones. Si no, se crea una región nueva por canal (en
    estéreo, con los archivos mono de 'archivos_canales' y nombres .L y .R), y
    cada archivo que no esté en la sesión se agrega: debe venir en 'archivos'
    con su 'ruta' absoluta (WAV mono a la frecuencia de la sesión)."""
    if c.get("tipo") != "audio":
        raise ErrorComun("Pro Tools: agregar clips MIDI aún no está soportado")
    canales = next((len(t["canales"]) for t in nativo["pistas_audio"] if t["nombre"] == pista["id"]), None)
    if not canales:
        raise ErrorComun(f"Pro Tools: la pista {pista['id']!r} no es de audio")
    muestra = c.get("muestra")
    if muestra is None:
        muestra = round(T.segundos(c["tick"], nativo["tempo"]) * fs)
    clave = (c.get("archivo"), c.get("desde_muestra", 0), c.get("largo_muestras"))
    for t in original["pistas"]:
        for x in t["clips"]:
            if x["tipo"] == "audio" and (x["archivo"], x.get("desde_muestra", 0), x["largo_muestras"]) == clave \
                    and x.get("canales", 1) == canales and _ganancia_pt(x) == _ganancia_pt(c):
                ta = next(a for a in nativo["pistas_audio"] if a["nombre"] == t["id"])
                j = int(x["id"].rsplit("#", 1)[1])
                ref = ta["canales"][0]["regiones"][j]
                indices = [next(r for r in canal["regiones"] if r["muestra"] == ref["muestra"])["indice_region"]
                           for canal in ta["canales"]]
                clips_nuevos.setdefault(pista["id"], []).append((indices, muestra))
                return
    archivos = c.get("archivos_canales") or [c.get("archivo")]
    if len(archivos) != canales:
        raise ErrorComun(f"Pro Tools: un clip nuevo en la pista {pista['id']!r} ({canales} canales) "
                         f"necesita 'archivos_canales' con un archivo mono por canal")
    fuentes = [_archivo_clip_pt(x, original, comun, fs, altas) for x in archivos]
    desde = int(c.get("desde_muestra") or 0)
    totales = [t for _, t in fuentes if t]
    largo = int(c.get("largo_muestras") or (min(totales) - desde if totales else 0))
    if largo <= 0 or any(desde + largo > t for t in totales):
        raise ErrorComun(f"Pro Tools: rango de audio inválido en el clip nuevo ({desde}+{largo} de {totales})")
    nombre = c.get("nombre") or Path(str(archivos[0])).stem
    if canales == 2:
        base = nombre[:-2] if nombre.endswith((".L", ".R")) else nombre
        nombres = [base + ".L", base + ".R"]
    else:
        nombres = [nombre]
    indices = []
    for (indice, _), nom in zip(fuentes, nombres):
        indices.append(altas["base_regiones"] + len(altas["regiones"]))
        altas["regiones"].append({"nombre": nom, "archivo": indice, "desde": desde, "largo": largo,
                                  "ganancia": _ganancia_pt(c)})
    clips_nuevos.setdefault(pista["id"], []).append((indices, muestra))


def _ganancia_pt(c):
    """Clip gain de un clip como nodos [{muestra, db}] (None = sin clip gain)."""
    if c.get("ganancia_curva"):
        return [{"muestra": int(x["muestra"]), "db": float(x["db"])} for x in c["ganancia_curva"]]
    if c.get("ganancia_db") is not None:
        return [{"muestra": 0, "db": float(c["ganancia_db"])}]
    return None


def _clip_midi_nuevo_pt(c, pista, original, nativo, midi_nuevos):
    """Clip MIDI nuevo en Pro Tools. Si otro clip MIDI de la sesión tiene las
    mismas notas y el mismo largo, se reutiliza su región (como al duplicar en
    Pro Tools); si no, se crea una región nueva con sus notas."""
    tm = next((t for t in nativo["pistas_midi"] if t["nombre"] == pista["id"]), None)
    if tm is None:
        raise ErrorComun(f"Pro Tools: la pista {pista['id']!r} no es MIDI")
    if c.get("tick") is None or c["tick"] < 0:
        raise ErrorComun("Pro Tools: un clip MIDI nuevo necesita 'tick'")
    notas = [{k: int(n[k]) for k in ("tick", "largo_ticks", "nota", "velocidad")} for n in c.get("notas", [])]
    largo = c.get("largo_ticks") or max((n["tick"] + n["largo_ticks"] for n in notas), default=0)
    if largo <= 0:
        raise ErrorComun("Pro Tools: un clip MIDI nuevo necesita 'largo_ticks' o notas")
    clave = lambda ns: sorted((n["tick"], n["nota"], n["largo_ticks"], n["velocidad"]) for n in ns)
    region = None
    for t in original["pistas"]:
        ta = next((x for x in nativo["pistas_midi"] if x["nombre"] == t["id"]), None)
        for x in t["clips"]:
            if ta and x["tipo"] == "midi" and x["largo_ticks"] == largo and clave(x["notas"]) == clave(notas):
                region = ta["regiones"][int(x["id"].rsplit("#m", 1)[1])]["indice_region"]
                break
        if region is not None:
            break
    if region is None:
        region = next((("nueva", k) for k, r in enumerate(midi_nuevos["regiones"])
                       if r["largo"] == largo and clave(r["notas"]) == clave(notas)), None)
    if region is None:
        k = len(midi_nuevos["regiones"])
        nombre = c.get("nombre") or f"{pista['id']}-trarin-{k + 1:02d}"
        midi_nuevos["regiones"].append({"nombre": nombre, "notas": notas, "largo": int(largo)})
        region = ("nueva", k)
    midi_nuevos["clips"].setdefault(pista["id"], []).append((region, int(c["tick"])))


def _archivo_clip_pt(archivo, original, comun, fs, altas):
    """Índice del archivo de un clip nuevo (negativo si es un archivo nuevo, que
    debe venir en 'archivos' con su 'ruta': WAV mono a la frecuencia de la
    sesión) y su largo en muestras."""
    originales = {a["id"]: a for a in original["archivos"]}
    if archivo in originales:
        return archivo, originales[archivo].get("muestras")
    arch = next((a for a in comun["archivos"] if a.get("id") == archivo), None)
    if not arch or not arch.get("ruta"):
        raise ErrorComun(f"Pro Tools: el archivo {archivo!r} del clip nuevo no está en 'archivos' con su 'ruta'")
    clave_a = arch["ruta"]
    if clave_a not in altas["indices"]:
        datos = {"ruta": arch["ruta"], "muestras": arch.get("muestras"), "bits": arch.get("bits"),
                 "frecuencia": fs}
        if Path(arch["ruta"]).exists():
            from .protools.escritura import ids_wav, info_wav
            m, bits, can, frec = info_wav(arch["ruta"])
            datos.update(ids_wav(arch["ruta"]))
            if can != 1:
                raise ErrorComun(f"Pro Tools: {arch['ruta']} no es mono")
            if frec != fs:
                raise ErrorComun(f"Pro Tools: {arch['ruta']} está a {frec} Hz y la sesión a {fs} Hz")
            datos.update(muestras=m, bits=bits)
        if not datos["muestras"]:
            raise ErrorComun(f"Pro Tools: falta 'muestras' del archivo {arch['ruta']} (no se pudo leer)")
        altas["indices"][clave_a] = -(len(altas["archivos"]) + 1)      # archivo nuevo k → −(k+1)
        altas["archivos"].append(datos)
        altas["largo"][clave_a] = datos["muestras"]
    return altas["indices"][clave_a], altas["largo"][clave_a]


def _sin(x):
    return {k: v for k, v in x.items() if k not in _SOLO_LECTURA} if isinstance(x, dict) else x

"""Los bordes entre las canciones de un show: dónde termina de sonar cada una,
dónde empieza la siguiente y el hueco entre ambas; los clips que cruzan un
marcador; los huecos sin ningún clip; y, por canción, cómo caen el audio, el
MIDI y la automatización respecto de su marcador (sirve para comparar dos
versiones: si el MIDI se corrió respecto del audio, aquí se ve). Solo lee."""

from .protools import tiempo as T
from .vistas import _canciones


def _fs(comun):
    return comun["unidades"].get("frecuencia_muestreo") or 48000


def _tramos(comun, mapa):
    """[(inicio_s, fin_s, pista, tipo, mute)] de todos los clips, ordenados."""
    fs = _fs(comun)
    out = []
    for p in comun["pistas"]:
        mute_pista = bool((p.get("mezcla") or {}).get("mute"))
        for k in p.get("clips") or []:
            if k.get("tipo") == "audio":
                a = k["muestra"] / fs
                out.append((a, (k["muestra"] + k["largo_muestras"]) / fs, p["nombre"], "audio",
                            mute_pista or bool(k.get("mute"))))
            elif k.get("tipo") == "midi":
                out.append((T.segundos(k["tick"], mapa), T.segundos(k["tick"] + k["largo_ticks"], mapa),
                            p["nombre"], "midi", mute_pista))
    out.sort()
    return out


def _vigente(eventos, tick):
    v = None
    for e in sorted(eventos, key=lambda e: e["tick"]):
        if e["tick"] <= tick:
            v = e
    return v


def _r(x, n=2):
    return None if x is None else round(x, n)


def por_cancion(comun, margen_s=3.0):
    """Por canción (marcador de nombre único): inicio, primer audio y primera
    nota MIDI (corrimiento respecto del marcador, en segundos), distancia entre
    la primera nota y el primer audio, fin del último audio y primer punto de
    automatización. Una canción va desde margen_s antes de su marcador hasta
    margen_s antes del siguiente (lo que empieza justo antes de un marcador es
    de la canción que viene)."""
    mapa = T.MapaTempo(comun["tempo"])
    fs = _fs(comun)
    canciones = _canciones(comun)
    out = []
    for i, (nombre, s0) in enumerate(canciones):
        s1 = canciones[i + 1][1] if i + 1 < len(canciones) else float("inf")
        audio, fin, midi, auto = [], None, [], []
        for p in comun["pistas"]:
            if (p.get("mezcla") or {}).get("mute"):
                continue
            for k in p.get("clips") or []:
                if k.get("tipo") == "audio" and not k.get("mute"):
                    a = k["muestra"] / fs
                    if s0 - margen_s <= a < s1 - margen_s:
                        audio.append(a)
                        b = (k["muestra"] + k["largo_muestras"]) / fs
                        fin = b if fin is None else max(fin, b)
                elif k.get("tipo") == "midi":
                    for n in k.get("notas") or []:
                        t = T.segundos(k["tick"] + n["tick"], mapa)
                        if s0 - margen_s <= t < s1 - margen_s:
                            midi.append(t)
            for puntos in (p.get("automatizacion") or {}).values():
                for x in puntos or []:
                    t = x["muestra"] / fs
                    if s0 <= t < s1:
                        auto.append(t)
        pa, pm = (min(audio) if audio else None), (min(midi) if midi else None)
        out.append({"cancion": nombre, "inicio_s": _r(s0, 3),
                    "primer_audio_s": _r(pa - s0 if pa is not None else None, 3),
                    "primera_nota_s": _r(pm - s0 if pm is not None else None, 3),
                    "midi_menos_audio_s": _r(pm - pa if pa is not None and pm is not None else None, 3),
                    "fin_audio_s": _r(fin, 2),
                    "primera_automatizacion_s": _r(min(auto) - s0 if auto else None, 2)})
    return out


def uniones(comun, margen_s=3.0, hueco_min_s=0.5, cruce_min_s=1.0):
    """Los bordes del show. Devuelve {"canciones", "uniones", "huecos", "cruzan"}.

    uniones: por cada par de canciones seguidas, dónde termina el último audio
    con sonido de la primera (y en qué pistas), el marcador de la siguiente,
    dónde empieza su primer audio (y en qué pistas), el hueco entre ambos, lo
    mismo para el MIDI, y el tempo y compás vigentes a cada lado del marcador.
    huecos: tramos de más de hueco_min_s sin ningún clip que suene.
    cruzan: clips que pasan por encima de un marcador de canción (empiezan más
    de cruce_min_s antes y terminan más de cruce_min_s después)."""
    mapa = T.MapaTempo(comun["tempo"])
    canciones = _canciones(comun)
    tramos = _tramos(comun, mapa)
    suenan = [t for t in tramos if not t[4]]

    def ultimo(desde, hasta, tipo):
        cand = [t for t in suenan if t[3] == tipo and desde - margen_s <= t[0] < hasta - margen_s]
        if not cand:
            return None, []
        fin = max(t[1] for t in cand)
        return fin, sorted({t[2] for t in cand if t[1] == fin})

    def primero(desde, hasta, tipo):
        cand = [t for t in suenan if t[3] == tipo and desde - margen_s <= t[0] < hasta - margen_s]
        if not cand:
            return None, []
        ini = min(t[0] for t in cand)
        return ini, sorted({t[2] for t in cand if t[0] == ini})

    lista = []
    for i in range(len(canciones) - 1):
        (na, a), (nb, b) = canciones[i], canciones[i + 1]
        c = canciones[i + 2][1] if i + 2 < len(canciones) else float("inf")
        fin_a, quien_fin = ultimo(a, b, "audio")
        ini_b, quien_ini = primero(b, c, "audio")
        fin_m, _ = ultimo(a, b, "midi")
        ini_m, _ = primero(b, c, "midi")
        tick = T.tick_de_segundos(b, mapa)
        t_antes, t_despues = _vigente(comun["tempo"], tick - 1), _vigente(comun["tempo"], tick)
        m_antes, m_despues = _vigente(comun["compases"], tick - 1), _vigente(comun["compases"], tick)
        u = {"de": na, "a": nb, "marcador_s": _r(b),
             "ultimo_audio_s": _r(fin_a), "ultimo_audio_pistas": quien_fin[:3],
             "primer_audio_s": _r(ini_b), "primer_audio_pistas": quien_ini[:3],
             "hueco_audio_s": _r(ini_b - fin_a) if fin_a is not None and ini_b is not None else None,
             "ultimo_midi_s": _r(fin_m), "primer_midi_s": _r(ini_m),
             "hueco_midi_s": _r(ini_m - fin_m) if fin_m is not None and ini_m is not None else None,
             "bpm": [t_antes and t_antes.get("bpm"), t_despues and t_despues.get("bpm")],
             "compas": [m_antes and f"{m_antes['numerador']}/{m_antes['denominador']}",
                        m_despues and f"{m_despues['numerador']}/{m_despues['denominador']}"]}
        if fin_a is not None:
            u["ultimo_audio_tras_marcador_s"] = _r(fin_a - b)   # positivo: la canción pisa a la siguiente
        if ini_b is not None:
            u["primer_audio_tras_marcador_s"] = _r(ini_b - b)
        lista.append(u)

    huecos, fin, quien = [], None, None
    for a, b, nombre, _, _ in suenan:
        if fin is not None and a > fin + hueco_min_s:
            huecos.append({"desde_s": _r(fin), "hasta_s": _r(a), "largo_s": _r(a - fin),
                           "termina": quien, "empieza": nombre})
        if fin is None or b > fin:
            fin, quien = b, nombre

    cruzan = []
    for nombre, s in canciones[1:]:
        for a, b, pista, tipo, mute in tramos:
            if a < s - cruce_min_s and b > s + cruce_min_s:
                cruzan.append({"marcador": nombre, "pista": pista, "tipo": tipo, "desde_s": _r(a), "hasta_s": _r(b),
                               "mute": mute})

    return {"canciones": [{"nombre": n, "inicio_s": _r(s)} for n, s in canciones],
            "uniones": lista, "huecos": huecos, "cruzan": cruzan}


def comparar_por_cancion(antes, despues, tolerancia_s=0.002):
    """Qué cambió, canción por canción, entre dos versiones (por_cancion de cada
    una): solo los campos que difieren más de tolerancia_s."""
    pa = {x["cancion"]: x for x in por_cancion(antes)}
    pb = {x["cancion"]: x for x in por_cancion(despues)}
    out = []
    for n in pb:
        if n not in pa:
            out.append({"cancion": n, "nueva": True})
            continue
        dif = {}
        for k in pa[n]:
            if k == "cancion":
                continue
            x, y = pa[n][k], pb[n][k]
            if x is None or y is None:
                if x != y:
                    dif[k] = [x, y]
            elif abs(x - y) > tolerancia_s:
                dif[k] = [x, y]
        if dif:
            out.append({"cancion": n, **dif})
    for n in pa:
        if n not in pb:
            out.append({"cancion": n, "desaparece": True})
    return out


def _s(x):
    return "-" if x is None else f"{x:.2f}"


def texto(u):
    lineas = []
    for x in u["uniones"]:
        lineas.append(f"{x['de']} → {x['a']} (marcador {_s(x['marcador_s'])} s)")
        lineas.append(f"   audio: termina {_s(x['ultimo_audio_s'])} ({', '.join(x['ultimo_audio_pistas']) or '-'}), "
                      f"empieza {_s(x['primer_audio_s'])} ({', '.join(x['primer_audio_pistas']) or '-'}), "
                      f"hueco {_s(x['hueco_audio_s'])} s")
        if x["ultimo_midi_s"] is not None or x["primer_midi_s"] is not None:
            lineas.append(f"   midi: termina {_s(x['ultimo_midi_s'])}, empieza {_s(x['primer_midi_s'])}, "
                          f"hueco {_s(x['hueco_midi_s'])} s")
        lineas.append(f"   tempo {x['bpm'][0]} → {x['bpm'][1]} bpm, compás {x['compas'][0]} → {x['compas'][1]}")
    if u["huecos"]:
        lineas.append(f"huecos sin ningún clip ({len(u['huecos'])}):")
        for h in u["huecos"]:
            lineas.append(f"   {_s(h['desde_s'])} → {_s(h['hasta_s'])} ({_s(h['largo_s'])} s): "
                          f"termina {h['termina']}, empieza {h['empieza']}")
    if u["cruzan"]:
        lineas.append(f"clips que cruzan un marcador ({len(u['cruzan'])}):")
        for c in u["cruzan"]:
            lineas.append(f"   {c['marcador']}: {c['pista']} ({c['tipo']}{', mute' if c['mute'] else ''}) "
                          f"{_s(c['desde_s'])} → {_s(c['hasta_s'])}")
    return "\n".join(lineas)


def _d(x):
    return "-" if x is None else f"{x:+.2f}"


def texto_por_cancion(filas):
    """Los corrimientos van respecto del marcador de la canción (+ después, − antes)."""
    lineas = []
    for f in filas:
        lineas.append(f"{f['cancion']} ({_s(f['inicio_s'])} s): audio {_d(f['primer_audio_s'])}, "
                      f"midi {_d(f['primera_nota_s'])} (midi−audio {_d(f['midi_menos_audio_s'])}), "
                      f"fin audio {_s(f['fin_audio_s'])}, automatización {_d(f['primera_automatizacion_s'])}")
    return "\n".join(lineas)


def texto_comparacion_por_cancion(cambios):
    if not cambios:
        return "sin cambios por canción"
    lineas = []
    for c in cambios:
        if c.get("nueva"):
            lineas.append(f"{c['cancion']}: nueva")
        elif c.get("desaparece"):
            lineas.append(f"{c['cancion']}: desaparece")
        else:
            lineas.append(f"{c['cancion']}: " + "; ".join(f"{k} {v[0]} → {v[1]}" for k, v in c.items() if k != "cancion"))
    return "\n".join(lineas)

"""Proponer el tempo de una sesión de Pro Tools a partir de una pista de
percusión (kultrun, bombo), con el detector de bpm: el bpm medido, dónde cae el
primer pulso en la sesión, cuánto se desvía la grilla actual de los golpes y
qué habría que escribir para que la grilla caiga en el pulso.

En una sesión suelta (una canción): un solo evento de tempo en el tick 0 y el
ancla de la línea de tiempo (`inicio_segundos`) puesta en un pulso, así el
compás 1 cae en un golpe. En un show (o con 'cancion'): no se mueve el tick 0;
se propone un cambio de tempo en el pulso de la grilla actual más cercano al
primer golpe de la canción, con el bpm medido.

Solo propone; con 'salida' escribe una copia con ese tempo por el camino ya
verificado (modelo común → tempo + ancla)."""

import math
from pathlib import Path

from .protools import tiempo as T
from .vistas import _normal

_PERCUSION = ("kultr", "cultr", "bombo", "kick", "bass drum", "perc", "kask", "cascab", "tambor", "caja", "snare", "drum")


def _clips(p, fs, a, b):
    return [k for k in p.get("clips") or [] if k.get("tipo") == "audio" and not k.get("mute")
            and k.get("archivo") is not None and a <= k["muestra"] / fs < b]


def _pista(comun, nombre, fs, a, b):
    """La pista pedida (por nombre o parte) o, sin nombre, la de percusión con
    más audio en la ventana [a, b)."""
    audio = [p for p in comun["pistas"] if p["tipo"] == "audio"]
    if nombre:
        c = [p for p in audio if p["nombre"] == nombre] or [p for p in audio if _normal(nombre) in _normal(p["nombre"])]
        if not c:
            raise ValueError(f"no hay una pista de audio que se llame {nombre!r}")
        return c[0]
    c = [(sum(k["largo_muestras"] for k in _clips(p, fs, a, b)), p) for p in audio
         if any(k in _normal(p["nombre"]) for k in _PERCUSION)]
    c = [x for x in c if x[0] > 0]
    if not c:
        raise ValueError("no se encontró una pista de percusión con audio ahí; indicar 'pista'")
    return max(c, key=lambda x: x[0])[1]


def proponer(sesion, pista=None, cancion=None, desde_s=None, hasta_s=None, minimo=40.0, maximo=200.0):
    from . import bpm as B
    from .comun import leer_comun
    from .consolidar import rutas_de_sesion
    from .vistas import _canciones
    sesion = str(Path(sesion).resolve())
    comun = leer_comun(sesion)
    fs = comun["unidades"].get("frecuencia_muestreo") or 48000
    mapa = T.MapaTempo(comun["tempo"])
    rutas = rutas_de_sesion(sesion, comun)
    canciones = _canciones(comun)
    es_show = len(canciones) > 1
    a, b = desde_s if desde_s is not None else -1e9, hasta_s if hasta_s is not None else 1e9
    nombre_c = None
    if cancion:
        from .fichas import _ventana_cancion
        nombre_c, a, b = _ventana_cancion(comun, cancion)
        a -= 3.0
    p = _pista(comun, pista, fs, a, b)
    clips = _clips(p, fs, a, b)
    if not clips:
        raise ValueError(f"la pista {p['nombre']!r} no tiene clips de audio" + (f" en {nombre_c}" if nombre_c else ""))
    k = max(clips, key=lambda c: c["largo_muestras"])
    ruta = rutas.get(k["archivo"])
    if not ruta or not Path(ruta).exists():
        raise ValueError(f"no se encuentra el archivo del clip ({ruta})")
    desde_arch = int(k.get("desde_muestra") or 0) / fs
    largo = k["largo_muestras"] / fs
    m = B.medir(ruta, minimo, maximo, desde_arch, desde_arch + largo, tramo_s=30.0)
    inicio_clip = k["muestra"] / fs
    pulso = inicio_clip + (m["primer_pulso_s"] - desde_arch)
    beat = 60.0 / m["bpm"]
    # la grilla actual: tempo vigente y a cuántos ms del pulso cae la negra más cercana
    tk = T.tick_de_segundos(pulso, mapa)
    vig = max((e for e in comun["tempo"] if e["tick"] <= tk), key=lambda e: e["tick"], default=comun["tempo"][0] if comun["tempo"] else {"bpm": 120.0})
    resto = tk % T.TICKS_NEGRA
    cerca = tk - resto if resto < T.TICKS_NEGRA / 2 else tk + T.TICKS_NEGRA - resto
    desvio_ms = (T.segundos(cerca, mapa) - pulso) * 1000
    out = {"sesion": sesion, "pista": p["nombre"], "cancion": nombre_c, "archivo": ruta,
           "medido_entre_s": [round(inicio_clip, 2), round(inicio_clip + largo, 2)],
           "bpm": m["bpm"], "bpm_entero": round(m["bpm"]), "confianza": m["confianza"],
           "pulsos_por_compas": m.get("pulsos_por_compas"), "candidatos": m["candidatos"],
           "tramos": [{"desde_s": round(inicio_clip + (t["desde_s"] - desde_arch), 1), "bpm": t["bpm"]} for t in m["tramos"]],
           "primer_pulso_s": round(pulso, 4),
           "grilla_actual": {"bpm": vig["bpm"], "negra_mas_cercana_al_pulso_ms": round(desvio_ms, 1),
                             "tick0_en_s": round(mapa.inicio, 4)}}
    # la grilla actual puede ser el mismo pulso contado distinto (95 = 126,67 × 3/4 en el show)
    relaciones = (("la mitad", 0.5), ("2/3", 2 / 3), ("3/4", 0.75), ("igual", 1.0), ("4/3", 4 / 3), ("3/2", 1.5), ("el doble", 2.0))
    for nombre, f in relaciones:
        if abs(vig["bpm"] - m["bpm"] * f) / (m["bpm"] * f) < 0.005:
            out["grilla_actual"]["relacion"] = (f"la grilla actual es {nombre} del bpm medido" if nombre != "igual"
                                                else "la grilla actual coincide con el bpm medido")
            break
    estable = not m["tramos"] or (max(t["bpm"] for t in m["tramos"]) - min(t["bpm"] for t in m["tramos"])) < 0.03 * m["bpm"]
    out["tempo_estable"] = estable
    if es_show or cancion:
        # el bpm propuesto rige hasta el siguiente evento de tempo que ya tenga la sesión (una rampa
        # hecha a mano se respeta); lo que viene después corre en segundos lo que cambie este tramo
        siguiente = min((e["tick"] for e in comun["tempo"] if e["tick"] > cerca), default=None)
        out["propuesta"] = {"tipo": "cambio de tempo en el pulso más cercano de la grilla actual",
                            "tick": int(cerca), "en_s": round(T.segundos(cerca, mapa), 4), "bpm": m["bpm"],
                            "nota": "no se mueve el tick 0; desde ese pulso la grilla sigue a este bpm"}
        if siguiente is not None:
            out["propuesta"]["rige_hasta_s"] = round(T.segundos(siguiente, mapa), 2)
            if siguiente - cerca < 4 * T.TICKS_NEGRA:
                out["aviso_tempo"] = (f"la sesión ya tiene otro evento de tempo a {(siguiente - cerca) / T.TICKS_NEGRA:.2f} negras "
                                      "del pulso (¿una rampa hecha a mano?): el bpm propuesto solo regiría hasta ahí")
        if abs(desvio_ms) > 15:
            # un «puente»: desde la negra anterior al pulso, un tempo que dura justo hasta el pulso,
            # así la negra siguiente cae EN el golpe y desde ahí sigue el bpm medido
            previo = tk - resto
            delta = pulso - T.segundos(previo, mapa)
            negras = 1
            while delta < 0.1 and previo - T.TICKS_NEGRA >= 0:
                previo -= T.TICKS_NEGRA
                delta = pulso - T.segundos(previo, mapa)
                negras += 1
            if delta > 0.05:
                out["propuesta"] = {"tipo": "puente de una negra y cambio de tempo en el golpe",
                                    "tick": int(previo + negras * T.TICKS_NEGRA), "en_s": round(pulso, 4), "bpm": m["bpm"],
                                    **{k: out["propuesta"][k] for k in ("rige_hasta_s",) if k in out["propuesta"]},
                                    "puente": {"tick": int(previo), "bpm": round(negras * 60.0 / delta, 3), "negras": negras},
                                    "nota": f"no se mueve el tick 0; la negra anterior dura {delta:.3f} s (puente a "
                                            f"{negras * 60.0 / delta:.1f} bpm) para que la siguiente caiga en el golpe"}
    else:
        inicio = pulso - math.floor(pulso / beat) * beat
        out["propuesta"] = {"tipo": "tempo único y ancla de la línea de tiempo", "bpm": m["bpm"],
                            "inicio_segundos": round(inicio, 4),
                            "nota": f"el tick 0 (compás 1) cae en {inicio:.3f} s y cada negra en un golpe"}
    if not estable:
        out["aviso"] = "el tempo cambia a lo largo del clip (ver tramos): una grilla fija no va a cuadrar todo"
    return out


def escribir(propuesta, salida):
    """Escribe una copia de la sesión con el tempo propuesto (modelo común →
    aplicar). Devuelve el resultado de tras_escribir."""
    from .comun import leer_comun, aplicar
    from .vistas import tras_escribir
    sesion = propuesta["sesion"]
    comun = leer_comun(sesion)
    pr = propuesta["propuesta"]
    if "inicio_segundos" in pr:
        comun["tempo"] = [{"tick": 0, "bpm": pr["bpm"], "pulso_ticks": T.TICKS_NEGRA, "inicio_segundos": pr["inicio_segundos"]}]
    else:
        quitar = {pr["tick"]}
        nuevos = [{"tick": pr["tick"], "bpm": pr["bpm"], "pulso_ticks": T.TICKS_NEGRA}]
        if pr.get("puente"):
            pu = pr["puente"]
            quitar.add(pu["tick"])
            # eventos que caían dentro del puente se quitan: la negra del puente es un solo tempo
            quitar |= {e["tick"] for e in comun["tempo"] if pu["tick"] < e["tick"] < pr["tick"]}
            nuevos.append({"tick": pu["tick"], "bpm": pu["bpm"], "pulso_ticks": T.TICKS_NEGRA})
        tempo = [e for e in comun["tempo"] if e["tick"] not in quitar]
        tempo.extend(nuevos)
        tempo.sort(key=lambda e: e["tick"])
        comun["tempo"] = tempo
    aplicar(comun, sesion, salida)
    return tras_escribir(sesion, salida, f"tempo propuesto {pr['bpm']} bpm desde {propuesta['pista']}")


def texto(r):
    g = r["grilla_actual"]
    l = [f"{r['pista']}" + (f" en {r['cancion']}" if r["cancion"] else "") + f": {r['bpm']} bpm (≈ {r['bpm_entero']}), "
         f"confianza {r['confianza']}" + (f", acento cada {r['pulsos_por_compas']} pulsos" if r.get("pulsos_por_compas") else ""),
         f"  medido en {Path(r['archivo']).name} entre {r['medido_entre_s'][0]} y {r['medido_entre_s'][1]} s de la sesión",
         f"  primer pulso en {r['primer_pulso_s']} s; grilla actual {g['bpm']} bpm, la negra más cercana cae a "
         f"{g['negra_mas_cercana_al_pulso_ms']:+.0f} ms del pulso" + (f" (tick 0 en {g['tick0_en_s']} s)" if g["tick0_en_s"] else "")
         + (f"; {g['relacion']}" if g.get("relacion") else "")]
    if r["tramos"]:
        l.append("  por tramos: " + ", ".join(f"{t['desde_s']:.0f}s {t['bpm']}" for t in r["tramos"]))
    p = r["propuesta"]
    l.append(f"  propuesta: {p['tipo']} — " + ", ".join(f"{k} {v}" for k, v in p.items() if k not in ("tipo", "nota")) + f" ({p['nota']})")
    for k in ("aviso", "aviso_tempo"):
        if r.get(k):
            l.append(f"  aviso: {r[k]}")
    return "\n".join(l)

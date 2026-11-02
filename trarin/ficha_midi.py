"""Ficha de una pista MIDI (las fichas de sonido son solo de audio): cuántas
notas y qué tan denso, rango y nota más usada, velocidad, largo de las notas,
si está cuantizada a la grilla de la sesión, a qué puerto y canal sale, y si
dobla a una pista de audio (qué parte de sus notas caen sobre un golpe
detectado). En palabras, como las fichas de audio. Solo lee."""

from .protools import tiempo as T
from .parecido import _NOTAS


def _nombre(m):
    return f"{_NOTAS[m % 12].capitalize()}{m // 12 - 1}"


def _notas_en(p, mapa, a, b):
    """[(segundo, nota, velocidad, largo_s)] de la pista en [a, b)."""
    out = []
    for k in p.get("clips") or []:
        if k.get("tipo") != "midi":
            continue
        for n in k.get("notas") or []:
            t = T.segundos(k["tick"] + n["tick"], mapa)
            if a <= t < b:
                fin = T.segundos(k["tick"] + n["tick"] + n["largo_ticks"], mapa)
                out.append((t, n["nota"], n.get("velocidad", 0), fin - t))
    out.sort()
    return out


def ficha(sesion, pista, cancion=None, audio=None, tolerancia_ms=50.0):
    """audio: pista de audio contra la que ver si dobla (por omisión, la de
    audio con el nombre más parecido). Necesita fichas para eso; sin fichas,
    el resto igual."""
    import math
    from . import fichas as F
    from .vistas import _normal
    ses = F.Sesion(str(sesion))
    comun, mapa, reloj = ses.comun, ses.reloj.mapa, ses.reloj
    p = next((x for x in comun["pistas"] if x["tipo"] == "midi" and x["nombre"] == pista), None) or \
        next((x for x in comun["pistas"] if x["tipo"] == "midi" and _normal(pista) in _normal(x["nombre"])), None)
    if p is None:
        raise F.ErrorFicha(f"no hay una pista MIDI que se llame como {pista!r}")
    a, b, nombre_c = 0.0, float("inf"), None
    if cancion:
        nombre_c, a, b = F._ventana_cancion(comun, cancion)
        a -= 3.0
        if b != float("inf"):
            b -= 3.0
    notas = _notas_en(p, mapa, a, b)
    out = {"pista": p["nombre"], "cancion": nombre_c, "notas": len(notas)}
    salida = None
    try:
        from .protools.descifrado import leer_descifrado
        from .protools.salidas_midi import leer_salidas
        salida = leer_salidas(leer_descifrado(ses.sesion)).get(p["nombre"])
    except Exception:
        pass
    out["salida"] = salida or (p.get("mezcla") or {}).get("salida")
    out["mute"] = bool((p.get("mezcla") or {}).get("mute"))
    if not notas:
        out["palabras"] = "sin notas" + (f" en {nombre_c}" if nombre_c else "")
        return out
    ts = [t for t, *_ in notas]
    t0, t1 = ts[0], ts[-1]
    activo = max(t1 - t0, 0.1)
    alturas = sorted({n for _, n, _, _ in notas})
    cuenta = {}
    for _, n, _, _ in notas:
        cuenta[n] = cuenta.get(n, 0) + 1
    mas = sorted(cuenta.items(), key=lambda x: -x[1])
    vel = sorted(v for _, _, v, _ in notas)
    largos = sorted(l for _, _, _, l in notas)
    med = lambda xs: xs[len(xs) // 2]
    # cuantización: qué parte de las notas cae en la grilla de semicorcheas (ticks)
    semi = T.TICKS_NEGRA // 4
    dentro = 0
    for k in p.get("clips") or []:
        if k.get("tipo") != "midi":
            continue
        for n in k.get("notas") or []:
            t = T.segundos(k["tick"] + n["tick"], mapa)
            if a <= t < b:
                r = (k["tick"] + n["tick"]) % semi
                if min(r, semi - r) <= semi * 0.05:
                    dentro += 1
    out.update({
        "desde": reloj(t0), "hasta": reloj(t1),
        "notas_por_s": round(len(notas) / activo, 2),
        "notas_por_compas": round(len(notas) / max(reloj.compas_de(t1) - reloj.compas_de(t0) + 1, 1), 1),
        "rango": [_nombre(alturas[0]), _nombre(alturas[-1])], "alturas_distintas": len(alturas),
        "mas_usadas": [{"nota": _nombre(n), "veces": c} for n, c in mas[:4]],
        "velocidad": {"min": vel[0], "mediana": med(vel), "max": vel[-1]},
        "largo_s": {"min": round(largos[0], 3), "mediana": round(med(largos), 3), "max": round(largos[-1], 3)},
        "en_grilla_pct": round(100 * dentro / len(notas)),
    })
    # acordes: notas que empiezan juntas (a menos de 10 ms)
    grupos, i = 0, 0
    while i < len(ts):
        j = i
        while j + 1 < len(ts) and ts[j + 1] - ts[i] < 0.01:
            j += 1
        if j > i:
            grupos += 1
        i = j + 1
    out["acordes"] = grupos
    # dobla al audio?
    dobla = None
    if ses.hay():
        candidatas = [x for x in comun["pistas"] if x["tipo"] == "audio"]
        if audio:
            pa = next((x for x in candidatas if x["nombre"] == audio), None) or \
                next((x for x in candidatas if _normal(audio) in _normal(x["nombre"])), None)
        else:
            base = _normal(p["nombre"]).replace("midi", "").strip()
            pa = next((x for x in candidatas if base and (base in _normal(x["nombre"]) or _normal(x["nombre"]) in base)), None)
        if pa is not None:
            j = ses.tramo(pa, (a, b) if cancion else None)[0].juntar()
            if j is not None and len(j["golpes"]):
                g = sorted(float(x) for x in j["golpes"][:, 0])
                import bisect
                tol = tolerancia_ms / 1000
                sobre = 0
                for t in ts:
                    i = bisect.bisect_left(g, t)
                    if (i < len(g) and abs(g[i] - t) <= tol) or (i and abs(g[i - 1] - t) <= tol):
                        sobre += 1
                dobla = {"audio": pa["nombre"], "notas_sobre_golpe_pct": round(100 * sobre / len(ts)), "golpes_audio": len(g)}
    if dobla:
        out["dobla"] = dobla
    # en palabras
    partes = [f"{len(notas)} notas de {reloj(t0).split(' ')[0]} a {reloj(t1).split(' ')[0]}, {out['notas_por_s']} por segundo"]
    if len(alturas) == 1:
        partes.append(f"una sola nota ({out['rango'][0]}): percusión o disparo")
    else:
        partes.append(f"de {out['rango'][0]} a {out['rango'][1]}, la más usada {out['mas_usadas'][0]['nota']}")
    if grupos > len(notas) / 6:
        partes.append(f"con acordes ({grupos})")
    partes.append(f"velocidad {vel[0]}–{vel[-1]} (mediana {med(vel)})")
    partes.append("notas cortas" if med(largos) < 0.15 else "notas largas" if med(largos) > 1.0 else "notas medianas")
    partes.append("cuantizada" if out["en_grilla_pct"] >= 85 else "a medias en la grilla" if out["en_grilla_pct"] >= 50 else "tocada a mano")
    if dobla:
        pct = dobla["notas_sobre_golpe_pct"]
        partes.append(f"{'dobla' if pct >= 70 else 'dobla a medias' if pct >= 40 else 'no dobla'} a {dobla['audio']} ({pct} % de las notas sobre un golpe)")
    if out["salida"]:
        s = out["salida"]
        partes.append(f"sale por {s['puerto']} canal {s['canal'] + 1}" if isinstance(s, dict) else f"sale por {s}")
    if out["mute"]:
        partes.append("MUTE")
    out["palabras"] = "; ".join(partes)
    return out

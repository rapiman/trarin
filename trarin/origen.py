"""Comparar una canción del show con la sesión de la que salió: qué clips del
original están en el show (y en qué pista quedaron), cuáles faltan, cuáles
están muteados, cuáles están en otro lugar, qué pistas del original no se
llevaron, qué hay en el show que no viene del original (grabaciones agregadas)
y las diferencias de fader. Solo lee.

Los clips se reconocen por el archivo (sin el sufijo de conversión
`_48k_xxxxxx` ni el `.L`/`.R` de canal), el pedazo del archivo que usan y su
posición respecto del primer audio de la canción (el show tiene separación y
marcadores, así que se alinea por el primer sonido, no por el 0)."""

import re
from pathlib import Path

from .vistas import _canciones, _normal

_SUFIJO = re.compile(r"(\.[LR]|_\d+k_[0-9a-f]{6})$", re.I)


def _base(ruta):
    """Nombre del archivo sin extensión, sin el sufijo de conversión
    (`_48k_b8dab4`) ni el de canal (`.L`/`.R`), en cualquier orden."""
    n = Path(ruta or "?").stem
    while True:
        m = _SUFIJO.sub("", n)
        if m == n:
            return _normal(n)
        n = m


def _clips(comun, rutas, fs, a=-1e18, b=1e18, con_mute=True):
    """[{pista, archivo, desde_s (en el archivo), inicio_s, largo_s, mute, volumen_db}]."""
    out = []
    for p in comun["pistas"]:
        mp = bool((p.get("mezcla") or {}).get("mute"))
        for k in p.get("clips") or []:
            if k.get("tipo") != "audio" or k.get("archivo") is None:
                continue
            ini = k["muestra"] / fs
            if not a <= ini < b:
                continue
            mute = mp or bool(k.get("mute"))
            if mute and not con_mute:
                continue
            out.append({"pista": p["nombre"], "archivo": _base(rutas.get(k["archivo"]) or str(k["archivo"])),
                        "desde_s": int(k.get("desde_muestra") or 0) / fs, "inicio_s": ini,
                        "largo_s": k["largo_muestras"] / fs, "mute": mute,
                        "volumen_db": (p.get("mezcla") or {}).get("volumen_db")})
    return out


def comparar(show, cancion, original, tolerancia_s=0.05):
    from .comun import leer_comun
    from .consolidar import rutas_de_sesion
    from .fichas import _ventana_cancion
    show, original = str(show), str(original)
    cs, co = leer_comun(show), leer_comun(original)
    fs_s = cs["unidades"].get("frecuencia_muestreo") or 48000
    fs_o = co["unidades"].get("frecuencia_muestreo") or 48000
    rs, ro = rutas_de_sesion(show, cs), rutas_de_sesion(original, co)
    nombre, a, b = _ventana_cancion(cs, cancion)
    en_show = _clips(cs, rs, fs_s, a - 3.0, b - 3.0)
    en_orig = _clips(co, ro, fs_o)
    if not en_orig:
        raise ValueError("la sesión original no tiene clips de audio")
    if not en_show:
        raise ValueError(f"el show no tiene clips de audio en {nombre}")
    # alineación por votación: el corrimiento más repetido entre los clips que coinciden
    # por archivo, pedazo y largo (alinear por el primer audio falla si esa pista no se llevó)
    def mismo(s, o):
        return (s["archivo"] == o["archivo"] and abs(s["desde_s"] - o["desde_s"]) <= tolerancia_s
                and abs(s["largo_s"] - o["largo_s"]) <= tolerancia_s)
    deltas = sorted(s["inicio_s"] - o["inicio_s"] for o in en_orig for s in en_show if mismo(s, o))
    if deltas:
        mejor, votos = deltas[0], 0
        for i, d in enumerate(deltas):
            n = sum(1 for x in deltas[i:] if x - d <= tolerancia_s)
            if n > votos:
                mejor, votos = d, n
        delta = mejor
    else:
        p_show = min((c["inicio_s"] for c in en_show if not c["mute"]), default=en_show[0]["inicio_s"])
        p_orig = min((c["inicio_s"] for c in en_orig if not c["mute"]), default=en_orig[0]["inicio_s"])
        delta = p_show - p_orig
    libres = list(range(len(en_show)))
    hallados, faltan, movidos, muteados, mapa = [], [], [], [], {}
    for o in en_orig:
        esperado = o["inicio_s"] + delta
        cand = [i for i in libres if mismo(en_show[i], o)]
        if not cand:
            if o["mute"]:
                continue                                    # un clip muteado del original que no se llevó: normal
            faltan.append(o)
            continue
        i = min(cand, key=lambda i: abs(en_show[i]["inicio_s"] - esperado))
        s = en_show[i]
        libres.remove(i)
        mapa.setdefault(o["pista"], {}).setdefault(s["pista"], 0)
        mapa[o["pista"]][s["pista"]] += 1
        corr = s["inicio_s"] - esperado
        if abs(corr) > tolerancia_s:
            movidos.append({**o, "en_show": s["pista"], "corrimiento_s": round(corr, 3)})
        elif s["mute"] and not o["mute"]:
            muteados.append({**o, "en_show": s["pista"]})
        else:
            hallados.append({**o, "en_show": s["pista"]})
    extras = [en_show[i] for i in libres]
    pistas_orig = {p["nombre"] for p in co["pistas"] if p["tipo"] == "audio" and any(k.get("tipo") == "audio" for k in p.get("clips") or [])}
    no_llevadas = sorted(pistas_orig - set(mapa))
    vol = []
    vo = {p["nombre"]: (p.get("mezcla") or {}).get("volumen_db") for p in co["pistas"]}
    vs = {p["nombre"]: (p.get("mezcla") or {}).get("volumen_db") for p in cs["pistas"]}
    for po, destinos in mapa.items():
        for ps in destinos:
            if vo.get(po) is not None and vs.get(ps) is not None and abs(vo[po] - vs[ps]) > 0.05:
                vol.append({"original": po, "show": ps, "volumen_original_db": vo[po], "volumen_show_db": vs[ps]})

    def r(c, extra=()):
        d = {"pista": c["pista"], "archivo": c["archivo"], "inicio_s": round(c["inicio_s"], 2), "largo_s": round(c["largo_s"], 1)}
        for k in extra:
            d[k] = c[k]
        return d
    return {"cancion": nombre, "original": original, "alineacion_s": round(delta, 3),
            "clips_original": len(en_orig), "hallados": len(hallados),
            "faltan": [r(c) for c in faltan], "muteados_en_show": [r(c, ("en_show",)) for c in muteados],
            "movidos": [r(c, ("en_show", "corrimiento_s")) for c in movidos],
            "agregados_en_show": [r(c, ("mute",)) for c in extras],
            "pistas": {po: dict(d) for po, d in mapa.items()}, "pistas_no_llevadas": no_llevadas,
            "faders_distintos": vol}


def texto(c):
    L = [f"{c['cancion']} contra {Path(c['original']).name}: {c['hallados']} de {c['clips_original']} clips del original "
         f"están en el show (alineación {c['alineacion_s']:+.3f} s)"]
    if c["faltan"]:
        L.append(f"faltan ({len(c['faltan'])}):")
        L += [f"  {x['pista']}: {x['archivo']} en {x['inicio_s']} s ({x['largo_s']} s)" for x in c["faltan"][:30]]
    if c["muteados_en_show"]:
        L.append(f"muteados en el show ({len(c['muteados_en_show'])}):")
        L += [f"  {x['pista']} → {x['en_show']}: {x['archivo']} en {x['inicio_s']} s" for x in c["muteados_en_show"][:30]]
    if c["movidos"]:
        L.append(f"en otro lugar ({len(c['movidos'])}):")
        L += [f"  {x['pista']} → {x['en_show']}: {x['archivo']} corrido {x['corrimiento_s']:+.3f} s" for x in c["movidos"][:30]]
    if c["agregados_en_show"]:
        L.append(f"en el show y no en el original ({len(c['agregados_en_show'])}):")
        L += [f"  {x['pista']}: {x['archivo']} en {x['inicio_s']} s{' (mute)' if x['mute'] else ''}" for x in c["agregados_en_show"][:30]]
    if c["pistas_no_llevadas"]:
        L.append("pistas del original que no están en el show: " + ", ".join(c["pistas_no_llevadas"]))
    L.append("pistas: " + "; ".join(f"{po} → " + ", ".join(f"{ps} ({n})" for ps, n in d.items()) for po, d in c["pistas"].items()))
    if c["faders_distintos"]:
        L.append("faders distintos: " + "; ".join(f"{x['original']} {x['volumen_original_db']} dB → {x['show']} {x['volumen_show_db']} dB"
                                                  for x in c["faders_distintos"]))
    return "\n".join(L)

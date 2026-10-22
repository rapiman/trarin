"""Vistas acotadas de una sesión, para que una IA (o una persona) sepa lo
necesario sin leer el modelo entero: el resumen de las pistas, el detalle de
una, las rutas, los archivos de audio y la comparación entre dos versiones."""

import re
import unicodedata
from pathlib import Path


def _normal(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def _canciones(comun):
    """[(nombre, segundo de inicio)] de los marcadores, en orden (para ubicar
    los clips). Los marcadores con el mismo nombre cuentan una vez."""
    fs = comun["unidades"].get("frecuencia_muestreo") or 48000
    vistos, out = set(), []
    from .protools import tiempo as T
    mapa = T.MapaTempo(comun["tempo"])
    for m in sorted(comun.get("marcadores") or [], key=lambda m: m["tick"]):
        if m["nombre"] in vistos:
            continue
        vistos.add(m["nombre"])
        out.append((m["nombre"], T.segundos(m["tick"], mapa)))
    return out


def _pan(p):
    if p is None:
        return "-"
    if isinstance(p, list):
        return f"{round(p[0] * 100)}/{round(p[1] * 100)}"
    return str(round(p * 100))


def _seg(c):
    return c.get("segundos")


def resumen(comun, patron=None, cancion=None):
    """Una fila por pista: nombre, tipo, canales, salida, entrada, paneo,
    volumen, mute, clips (cuántos y entre qué segundos), clip gain, listas de
    automatización y plugins. patron: expresión regular sobre el nombre (sin
    tildes, minúsculas). cancion: solo las pistas con clips en esa canción (por
    nombre de marcador)."""
    canciones = _canciones(comun)
    rango = None
    if cancion:
        nombres = [n for n, _ in canciones]
        cand = [n for n in nombres if _normal(cancion) in _normal(n)]
        if not cand:
            raise ValueError(f"no hay un marcador que se llame como {cancion!r} (hay: {nombres})")
        k = nombres.index(cand[0])
        rango = (canciones[k][1], canciones[k + 1][1] if k + 1 < len(canciones) else float("inf"))
    filas = []
    for p in comun["pistas"]:
        if patron and not re.search(patron, _normal(p["nombre"])):
            continue
        clips = p.get("clips") or []
        if rango:
            clips = [c for c in clips if c.get("segundos") is not None and rango[0] <= c["segundos"] < rango[1]]
            if not clips:
                continue
        m = p.get("mezcla") or {}
        fila = {"pista": p["nombre"], "tipo": p["tipo"],
                "canales": 2 if isinstance(m.get("paneo"), list) else (1 if p["tipo"] == "audio" else None),
                "salida": m.get("salida"), "entrada": m.get("entrada"), "paneo": _pan(m.get("paneo")),
                "volumen_db": m.get("volumen_db"), "mute": bool(m.get("mute")),
                "clips": len(clips)}
        if clips:
            seg = [c["segundos"] for c in clips if c.get("segundos") is not None]
            if seg:
                fila["desde_s"] = round(min(seg), 1)
                fin = [c["segundos"] + c.get("largo_muestras", 0) / (comun["unidades"].get("frecuencia_muestreo") or 48000)
                       for c in clips if c.get("segundos") is not None and c.get("tipo") == "audio"]
                if fin:
                    fila["hasta_s"] = round(max(fin), 1)
            gan = sorted({c["ganancia_db"] for c in clips if c.get("ganancia_db") is not None})
            if gan:
                fila["clip_gain_db"] = gan if len(gan) <= 4 else [gan[0], "…", gan[-1]]
        auto = p.get("automatizacion") or {}
        if auto:
            fila["automatizacion"] = {k: len(v) for k, v in auto.items()}
        efectos = [e.get("nombre") for e in p.get("efectos") or []]
        if efectos:
            fila["plugins"] = efectos
        filas.append(fila)
    return {"pistas": filas, "total": len(filas),
            "canciones": [{"nombre": n, "inicio_s": round(s, 1)} for n, s in canciones]}


def texto_resumen(r):
    lineas = []
    for f in r["pistas"]:
        partes = [f["pista"], f["tipo"] + (f" {f['canales']}c" if f.get("canales") else ""),
                  f"→ {f['salida']}" if f.get("salida") else "", f"pan {f['paneo']}" if f["paneo"] != "-" else "",
                  f"{f['volumen_db']} dB" if f.get("volumen_db") is not None else "", "MUTE" if f["mute"] else "",
                  f"{f['clips']} clips" + (f" {f['desde_s']}–{f.get('hasta_s', '?')} s" if "desde_s" in f else ""),
                  ("auto " + ",".join(f["automatizacion"])) if f.get("automatizacion") else "",
                  ("plugins " + ", ".join(f["plugins"])) if f.get("plugins") else ""]
        lineas.append(" | ".join(x for x in partes if x))
    return "\n".join(lineas)


def detalle(comun, pista, max_clips=60):
    """La pista entera, con sus clips (hasta max_clips) y su automatización."""
    p = next((x for x in comun["pistas"] if x["nombre"] == pista), None)
    if p is None:
        raise ValueError(f"no existe la pista {pista!r}")
    out = {k: v for k, v in p.items() if k != "clips"}
    clips = p.get("clips") or []
    out["clips_total"] = len(clips)
    out["clips"] = [{k: v for k, v in c.items() if k not in ("notas",)} | ({"notas": len(c["notas"])} if "notas" in c else {})
                    for c in clips[:max_clips]]
    if len(clips) > max_clips:
        out["clips_omitidos"] = len(clips) - max_clips
    return out


def rutas(ruta):
    """Entradas, salidas y buses que existen en la sesión (Pro Tools)."""
    from .protools.descifrado import leer_descifrado
    from .protools.ruteo import rutas as rutas_pt
    clases = {0x0: "entrada", 0x1: "salida", 0x2: "bus", 0x3: "insert", 0x102: "bus (salida)"}
    out = {}
    for c, n, _ in rutas_pt(leer_descifrado(ruta)):
        out.setdefault(clases.get(c, hex(c)), [])
        if n not in out[clases.get(c, hex(c))]:
            out[clases.get(c, hex(c))].append(n)
    return out


def archivos_audio(ruta, solo_faltantes=False):
    """Archivos de audio de la sesión con su ruta en el disco y si existen."""
    from .comun import leer_comun
    from .consolidar import rutas_de_sesion
    from .show import _ruta_audio
    c = leer_comun(ruta)
    rutas_s = rutas_de_sesion(ruta, c)
    out = []
    for a in c["archivos"]:
        r = rutas_s.get(a["id"]) or _ruta_audio(ruta, a["ruta"])
        existe = Path(r).exists()
        if solo_faltantes and existe:
            continue
        out.append({"id": a["id"], "nombre": a["ruta"], "ruta": r, "existe": existe, "muestras": a.get("muestras")})
    return {"archivos": out, "total": len(c["archivos"]), "faltan": sum(1 for x in out if not x["existe"])}


def comparar(a, b):
    """Qué cambió de la sesión a a la b: pistas que aparecen o desaparecen y,
    en las que siguen, salida, entrada, paneo, volumen, mute, cantidad de
    clips, automatización y plugins; además tempo, compases y marcadores."""
    from .comun import leer_comun
    ca, cb = (x if isinstance(x, dict) else leer_comun(x) for x in (a, b))
    pa = {p["nombre"]: p for p in ca["pistas"]}
    pb = {p["nombre"]: p for p in cb["pistas"]}
    cambios = []
    for n in pa.keys() & pb.keys():
        x, y = pa[n], pb[n]
        mx, my = x.get("mezcla") or {}, y.get("mezcla") or {}
        dif = {}
        if x["tipo"] != y["tipo"]:
            dif["tipo"] = [x["tipo"], y["tipo"]]
        for k in ("salida", "entrada", "paneo", "volumen_db", "mute"):
            if mx.get(k) != my.get(k):
                dif[k] = [mx.get(k), my.get(k)]
        if len(x.get("clips") or []) != len(y.get("clips") or []):
            dif["clips"] = [len(x.get("clips") or []), len(y.get("clips") or [])]
        elif [c.get("muestra", c.get("tick")) for c in x.get("clips") or []] != \
                [c.get("muestra", c.get("tick")) for c in y.get("clips") or []]:
            dif["clips"] = "movidos"
        ax, ay = x.get("automatizacion") or {}, y.get("automatizacion") or {}
        if ax != ay:
            dif["automatizacion"] = {k: [len(ax.get(k) or []), len(ay.get(k) or [])]
                                     for k in ax.keys() | ay.keys() if ax.get(k) != ay.get(k)}
        ex = [e.get("nombre") for e in x.get("efectos") or []]
        ey = [e.get("nombre") for e in y.get("efectos") or []]
        if ex != ey:
            dif["plugins"] = [ex, ey]
        if dif:
            cambios.append({"pista": n, **dif})
    out = {"aparecen": sorted(pb.keys() - pa.keys(), key=lambda n: list(pb).index(n)),
           "desaparecen": sorted(pa.keys() - pb.keys(), key=lambda n: list(pa).index(n)),
           "cambian": sorted(cambios, key=lambda c: list(pb).index(c["pista"])),
           "pistas": [len(pa), len(pb)]}
    if ca["tempo"] != cb["tempo"]:
        out["tempo"] = [len(ca["tempo"]), len(cb["tempo"])]
    if ca["compases"] != cb["compases"]:
        out["compases"] = [len(ca["compases"]), len(cb["compases"])]
    ma = {(m["nombre"], m["tick"]) for m in ca.get("marcadores") or []}
    mb = {(m["nombre"], m["tick"]) for m in cb.get("marcadores") or []}
    if ma != mb:
        out["marcadores"] = {"aparecen": sorted(n for n, _ in mb - ma), "desaparecen": sorted(n for n, _ in ma - mb)}
    return out


def texto_comparacion(c):
    lineas = [f"pistas: {c['pistas'][0]} → {c['pistas'][1]}"]
    if c["aparecen"]:
        lineas.append(f"aparecen ({len(c['aparecen'])}): " + ", ".join(c["aparecen"]))
    if c["desaparecen"]:
        lineas.append(f"desaparecen ({len(c['desaparecen'])}): " + ", ".join(c["desaparecen"]))
    for x in c["cambian"]:
        lineas.append(f"{x['pista']}: " + "; ".join(f"{k} {v}" for k, v in x.items() if k != "pista"))
    for k in ("tempo", "compases", "marcadores"):
        if k in c:
            lineas.append(f"{k}: {c[k]}")
    return "\n".join(lineas)


def tras_escribir(origen, salida, que):
    """Verificación de lo escrito: lee la salida, la compara con el origen (si
    hay) y anota en las notas del proyecto. Devuelve {"verificacion", "notas"}."""
    from .comun import leer_comun
    from . import notas
    cb = leer_comun(salida)
    out = {"verificacion": {"pistas": len(cb["pistas"]), "lee_bien": True}}
    linea = f"{que}: {Path(salida).name} ({len(cb['pistas'])} pistas)"
    if origen:
        c = comparar(origen, cb)
        out["verificacion"]["cambios"] = c
        out["verificacion"]["texto"] = texto_comparacion(c)
        linea = (f"{que}: {Path(origen).name} → {Path(salida).name} ({c['pistas'][0]} → {c['pistas'][1]} pistas; "
                 f"{len(c['aparecen'])} nuevas, {len(c['desaparecen'])} quitadas, {len(c['cambian'])} cambiadas)")
    out["notas"] = notas.anotar(salida, linea)
    return out


def tras_trabajo(argumentos, resultado):
    """Para trabajos.lanzar(despues=...): verifica y anota lo que escribió."""
    salida = resultado.get("salida") or argumentos.get("salida")
    if not salida or not str(salida).lower().endswith(".ptx"):
        return {}
    que = "consolidar" if "reglas" in argumentos else "armar show"
    return tras_escribir(argumentos.get("sesion"), salida, que)

"""Consolidar pistas de una sesión de Pro Tools (pensado para un show armado
con show.py): las pistas del mismo instrumento de distintas canciones pasan a
unas pocas pistas comunes, sobre la misma sesión.

Reglas (JSON):

    {"canciones": [nombres de marcadores, en orden: cada canción va desde su
                   marcador hasta el de la siguiente],
     "live": {canción: set de Ableton Live (.als) de donde se toma el paneo},
     "categorias": [{"nombre", "patron" (regex sobre el nombre sin tildes, en
                     minúsculas), "modo": "lcr" | "juntar", "salida": bus o
                     salida, "grupo": nombre del grupo (por defecto el de la
                     categoría)}],
     "lado": {pista: "L" | "C" | "R"}         (fuerza el lado de una pista mono),
     "dejar": [pistas que no se juntan aunque calcen],
     "borrar": [pistas que se quitan],
     "auxiliares": [{"nombre", "bus", "plantilla": auxiliar de la sesión que se
                     copia (entrada renombrada al bus)}],
     "rutas": [{"bus", "grupo", "pistas": [nombres]}] (pistas que no se juntan:
              salida a ese bus y su grupo),
     "midi": [{"nombre", "patron"}]          (pistas MIDI que se juntan),
     "grupo_midi": nombre del grupo con todas las MIDI,
     "salida_principal": "Speakers 1-2",
     "normalizar_db": -18}

En cada canción todos los stems suenan juntos, así que dos pistas de una
misma canción no pueden ir a la misma pista: en modo "lcr" las mono van a
«<categoría> L/C/R» según su paneo (el de Live si la pista está en el set, si
no el de Pro Tools; ±0.25 es el centro) y, si ese lado ya está ocupado en la
canción, a «<categoría> <lado> 2», 3…; las estéreo van a «<categoría>
estéreo», «… estéreo 2»… En modo "juntar" las mono van a «<categoría>», «…
2»… y las estéreo a «<categoría> estéreo»… (sin «estéreo» si la categoría
no tiene mono). Las pistas silenciadas (mute fijo) no se tocan.

Cada pista nueva lleva: los clips (con el clip gain llevado a 'normalizar_db'
de RMS, medido por pista de origen y canción sobre los tramos con sonido), el
paneo promedio de lo que recibe, el fader en 0 dB, la automatización de
volumen y mute de cada canción (el volumen relativo a su valor al empezar la
canción, para que el fader quede en 0), los plugins y el color de la primera
pista de origen que los tenga y la salida de su categoría."""

import re
import unicodedata
from pathlib import Path

from . import comun as C
from .protools import tiempo as T


class ErrorConsolidar(Exception):
    pass


def _normal(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def _clave(t):
    return re.sub(r"[^a-z0-9]", "", _normal(t))


# ---------------------------------------------------------------- paneo de Live

def paneos_live(ruta):
    """{clave de nombre: paneo (-1..1)} de las pistas de audio de un .als: por
    el nombre de la pista y por el de sus archivos, sin el número delante ni
    los sufijos _01, _01-02 de los archivos."""
    import gzip
    import xml.etree.ElementTree as ET
    x = ET.fromstring(gzip.open(ruta).read())
    out = {}

    def limpiar(n):
        n = re.sub(r"^\d+[- ]", "", n.strip())
        n = re.sub(r"\.(wav|aif+|mp3)$", "", n, flags=re.I)
        return re.sub(r"(_\d+)+(-\d+)*$", "", n)

    for t in x.iter("AudioTrack"):
        nm = t.find("Name/EffectiveName")
        pan = t.find("DeviceChain/Mixer/Pan/Manual")
        if pan is None:
            continue
        v = float(pan.get("Value"))
        nombres = [nm.get("Value")] if nm is not None else []
        for fr in t.iter("FileRef"):
            r = fr.find("RelativePath")
            if r is not None and r.get("Value", "").lower().endswith(".wav"):
                nombres.append(Path(r.get("Value")).name)
        for n in nombres:
            out.setdefault(_clave(limpiar(n)), v)
    return out


# ---------------------------------------------------------------- plan

def _ventanas(comun, canciones, fs):
    """[(canción, muestra de inicio, muestra de fin)] según los marcadores."""
    inicio = {}
    for m in comun["marcadores"]:
        if m["nombre"] in canciones and m["nombre"] not in inicio:
            inicio[m["nombre"]] = round(T.segundos(m["tick"], comun["tempo"]) * fs)
    faltan = [c for c in canciones if c not in inicio]
    if faltan:
        raise ErrorConsolidar(f"no hay marcador para: {faltan}")
    orden = sorted(canciones, key=lambda c: inicio[c])
    out = []
    for k, c in enumerate(orden):
        fin = inicio[orden[k + 1]] if k + 1 < len(orden) else 1 << 62
        out.append((c, inicio[c], fin))
    return out


def _cancion(ventanas, muestra):
    for c, a, b in ventanas:
        if a <= muestra < b:
            return c
    return ventanas[0][0] if muestra < ventanas[0][1] else None


def _lado(p):
    if p < -0.25:
        return "L"
    if p > 0.25:
        return "R"
    return "C"


def planear(comun, reglas):
    """Plan de la consolidación (sin tocar audio): pistas nuevas con sus
    fuentes [(pista, canción)], lado, paneo, salida y grupo; pistas a quitar;
    MIDI; rutas; avisos."""
    fs = comun["unidades"]["frecuencia_muestreo"]
    ventanas = _ventanas(comun, reglas["canciones"], fs)
    live = {c: paneos_live(r) for c, r in (reglas.get("live") or {}).items() if Path(r).exists()}
    dejar = set(reglas.get("dejar") or [])
    borrar = set(reglas.get("borrar") or [])
    lado_forzado = reglas.get("lado") or {}
    cats = reglas.get("categorias") or []
    avisos = []
    nuevas = {}             # nombre → {categoria, canales, fuentes: [(pista, canción)], paneos: [...]}
    ocupado = {}            # (nombre nuevo, canción) → pista de origen
    quitar = set()
    pistas = {p["nombre"]: p for p in comun["pistas"]}
    for nombre in borrar:
        if nombre not in pistas:
            avisos.append(f"borrar: no existe la pista {nombre!r}")
    for p in comun["pistas"]:
        if p["tipo"] != "audio" or p["nombre"] in dejar or p["nombre"] in borrar:
            continue
        cat = next((c for c in cats if re.search(c["patron"], _normal(p["nombre"]))), None)
        if cat is None:
            continue
        m = p.get("mezcla") or {}
        if m.get("mute"):
            avisos.append(f"{p['nombre']}: silenciada, queda como está")
            continue
        estereo = isinstance(m.get("paneo"), list)
        por_cancion = {}
        for c in p["clips"]:
            por_cancion.setdefault(_cancion(ventanas, c["muestra"]), []).append(c)
        if None in por_cancion:
            raise ErrorConsolidar(f"{p['nombre']}: clips fuera de las canciones")
        for cancion in [v[0] for v in ventanas if v[0] in por_cancion]:
            if estereo:
                pan = m["paneo"]
                base, lado = f"{cat['nombre']} estéreo", None
            else:
                pl = live.get(cancion, {}).get(_clave(p["nombre"]))
                pan = pl if pl is not None else float(m.get("paneo") or 0.0)
                lado = lado_forzado.get(p["nombre"]) or _lado(pan)
                if p["nombre"] in lado_forzado:
                    pan = {"L": -1.0, "C": 0.0, "R": 1.0}[lado]
                base = f"{cat['nombre']} {lado}" if cat["modo"] == "lcr" else cat["nombre"]
            k = 1
            destino = base
            while (destino, cancion) in ocupado:
                k += 1
                destino = f"{base} {k}"
            ocupado[(destino, cancion)] = p["nombre"]
            n = nuevas.setdefault(destino, {"categoria": cat, "canales": 2 if estereo else 1,
                                            "fuentes": [], "paneos": [], "lado": lado})
            n["fuentes"].append((p["nombre"], cancion))
            n["paneos"].append(pan)
        quitar.add(p["nombre"])
    # «X estéreo» sin «X» mono en la categoría: sin «estéreo»
    for cat in cats:
        mias = [n for n, x in nuevas.items() if x["categoria"] is cat]
        if mias and all(nuevas[n]["canales"] == 2 for n in mias) and cat["modo"] == "juntar":
            for n in mias:
                nuevo = n.replace(" estéreo", "", 1)
                nuevas[nuevo] = nuevas.pop(n)
    midi, quitar_midi = _planear_midi(comun, reglas, avisos)
    fuera = quitar | borrar | set(quitar_midi)
    for n, x in nuevas.items():
        if x["canales"] == 1:
            x["paneo"] = round(sum(x["paneos"]) / len(x["paneos"]), 2)
        else:
            x["paneo"] = [round(sum(v[0] for v in x["paneos"]) / len(x["paneos"]), 2),
                          round(sum(v[1] for v in x["paneos"]) / len(x["paneos"]), 2)]
        if n in pistas and n not in fuera:
            raise ErrorConsolidar(f"la pista nueva {n!r} ya existe en la sesión")
    for n in midi:
        if n in pistas and n not in fuera:
            raise ErrorConsolidar(f"la pista nueva {n!r} ya existe en la sesión")
    return {"ventanas": ventanas, "nuevas": nuevas, "quitar": sorted(quitar), "borrar": sorted(borrar & set(pistas)),
            "midi": midi, "quitar_midi": quitar_midi, "avisos": avisos}


def _planear_midi(comun, reglas, avisos):
    """Pistas MIDI que se juntan por categoría: cada pista de origen (todos
    sus clips) va a la primera «MIDI <categoría> n» donde no choca con otra."""
    out, quitar = {}, []
    for regla in reglas.get("midi") or []:
        fuentes = [p for p in comun["pistas"] if p["tipo"] == "midi" and p["clips"]
                   and re.search(regla["patron"], _normal(p["nombre"]))]
        if len(fuentes) < 2:
            continue
        carriles = []           # [(nombres, [(ini, fin)])]
        for p in sorted(fuentes, key=lambda q: min(c["tick"] for c in q["clips"])):
            tramos = [(c["tick"], c["tick"] + c["largo_ticks"]) for c in p["clips"]]
            for nombres, ocupados in carriles:
                if not any(a < y and x < b for a, b in tramos for x, y in ocupados):
                    nombres.append(p["nombre"])
                    ocupados += tramos
                    break
            else:
                carriles.append(([p["nombre"]], tramos))
        for k, (nombres, _) in enumerate(carriles):
            nombre = f"MIDI {regla['nombre']}" + (f" {k + 1}" if len(carriles) > 1 else "")
            out[nombre] = {"fuentes": nombres, "grupo": regla.get("grupo", regla["nombre"])}
            quitar += nombres
    return out, quitar


def texto(plan):
    lineas = []
    for c, a, b in plan["ventanas"]:
        lineas.append(f"canción {c}: desde {a / 48000:.1f} s")
    for n, x in sorted(plan["nuevas"].items()):
        pan = x["paneo"]
        lineas.append(f"{n}  ({'estéreo' if x['canales'] == 2 else 'mono'}, paneo {pan}, "
                      f"salida {x['categoria'].get('salida')})")
        for (f, c), p in zip(x["fuentes"], x["paneos"]):
            lineas.append(f"    {c:22s} {f}  (paneo {p})")
    for n, x in sorted(plan["midi"].items()):
        lineas.append(f"{n}: {', '.join(x['fuentes'])}")
    lineas.append(f"se quitan ({len(plan['quitar'])} de audio, {len(plan['quitar_midi'])} MIDI, "
                  f"{len(plan['borrar'])} borradas): {', '.join(plan['quitar'] + plan['quitar_midi'] + plan['borrar'])}")
    lineas += [f"aviso: {a}" for a in plan["avisos"]]
    return "\n".join(lineas)


# ---------------------------------------------------------------- normalización

def _db(x):
    import math
    return 10 * math.log10(x) if x > 0 else -999.0


def _ganancia(c):
    if c.get("ganancia_curva"):
        return float(c["ganancia_curva"][0]["db"])
    return float(c.get("ganancia_db") or 0.0)


def medir(clips, rutas, fs, bloque_s=0.1, umbral_db=-50.0):
    """RMS en dBFS de un conjunto de clips (con su clip gain actual), sobre los
    bloques de 'bloque_s' con más de 'umbral_db'. rutas: {archivo: ruta}."""
    import numpy as np
    from . import audio
    n = max(int(fs * bloque_s), 1)
    potencias = []
    for c in clips:
        archivos = c.get("archivos_canales") or [c["archivo"]]
        g = 10 ** (_ganancia(c) / 10)
        suma = None
        for a in archivos:
            x, _ = audio.leer(rutas[a], int(c.get("desde_muestra") or 0), int(c["largo_muestras"]))
            x = x[:, 0]
            k = len(x) // n
            if k == 0:
                continue
            pot = (x[:k * n].reshape(k, n) ** 2).mean(axis=1)
            suma = pot if suma is None else suma[:len(pot)] + pot[:len(suma)]
        if suma is not None:
            potencias.append(suma / len(archivos) * g)
    if not potencias:
        return None
    p = np.concatenate(potencias)
    activos = p[p > 10 ** (umbral_db / 10)]
    if len(activos) == 0:
        return None
    return _db(float(activos.mean()))


def _clip_nuevo(c, delta):
    x = {k: v for k, v in c.items() if k not in ("id", "posicion", "segundos", "tick", "largo_ticks", "canales")}
    if delta is not None:
        if x.get("ganancia_curva"):
            x["ganancia_curva"] = [dict(nd, db=min(max(float(nd["db"]) + delta, -144.0), 36.0))
                                   for nd in x["ganancia_curva"]]
        else:
            x["ganancia_db"] = round(min(max(_ganancia(c) + delta, -144.0), 36.0), 2)
    return x


def rutas_de_sesion(sesion, comun):
    """{id de archivo: ruta absoluta} según la lista de rutas de la sesión
    (0x103A: volumen, carpetas y archivo, cada entrada con su padre)."""
    from .protools.bloques import arbol, buscar
    from .protools.descifrado import leer_descifrado
    from .protools.escritura import _lista_rutas
    d = leer_descifrado(sesion)
    listas = buscar(arbol(d), 0x103A)
    if not listas:
        return {}
    e = _lista_rutas(d, listas[0])

    def ruta(i):
        partes = []
        while i != 0xFFFFFFFF and len(partes) < 64:
            partes.append(e[i][2].decode("utf-8", "replace"))
            i = e[i][1]
        return str(Path(*partes[::-1]))

    archivos = [(i, x[2].decode("utf-8", "replace")) for i, x in enumerate(e) if x[0] == 2]
    out, k = {}, 0
    for a in comun["archivos"]:
        while k < len(archivos) and archivos[k][1] != a["ruta"]:
            k += 1
        if k == len(archivos):
            break
        out[a["id"]] = ruta(archivos[k][0])
        k += 1
    return out


# ---------------------------------------------------------------- automatización

def _valor_en(puntos, m):
    """Valor de una lista de automatización en la muestra m (lineal entre puntos)."""
    if not puntos:
        return None
    if m <= puntos[0]["muestra"]:
        return puntos[0]["valor"]
    for a, b in zip(puntos, puntos[1:]):
        if a["muestra"] <= m <= b["muestra"]:
            if b["muestra"] == a["muestra"]:
                return b["valor"]
            t = (m - a["muestra"]) / (b["muestra"] - a["muestra"])
            return a["valor"] + t * (b["valor"] - a["valor"])
    return puntos[-1]["valor"]


def automatizacion(fuentes, pistas, ventanas):
    """Volumen y mute de una pista nueva: por cada canción, los puntos de su
    pista de origen dentro de la canción si cambian (el volumen relativo al
    primero), y si no, 0 dB y sin mute desde el inicio de la canción."""
    de = dict((c, f) for f, c in fuentes)
    out = {"volumen_db": [], "mute": []}
    for c, a, b in ventanas:
        f = de.get(c)
        auto = (pistas[f].get("automatizacion") or {}) if f else {}
        vol = [x for x in auto.get("volumen_db", []) if a <= x["muestra"] < b]
        if len({x["valor"] for x in vol}) > 1:
            base = vol[0]["valor"]
            seg = [{"muestra": a, "valor": 0.0}] + [
                {"muestra": x["muestra"], "valor": round(max(x["valor"] - base, -144.0), 1)} for x in vol]
        else:
            seg = [{"muestra": a, "valor": 0.0}]
        out["volumen_db"] += seg
        mu = auto.get("mute", [])
        dentro = [x for x in mu if a <= x["muestra"] < b]
        if dentro:
            antes = [x for x in mu if x["muestra"] <= a]
            v0 = antes[-1]["valor"] if antes else dentro[0]["valor"]
            out["mute"] += [{"muestra": a, "valor": v0}] + dentro
        else:
            out["mute"].append({"muestra": a, "valor": 0})
    res = {}
    for k, pts in out.items():
        limpio = []
        for x in sorted(pts, key=lambda y: y["muestra"]):
            if limpio and limpio[-1]["muestra"] == x["muestra"]:
                limpio[-1] = x
            elif not limpio or limpio[-1]["valor"] != x["valor"] or k == "volumen_db":
                limpio.append(x)
        # puntos iguales seguidos sobran (salvo el que cierra una rampa)
        compacto = [p for i, p in enumerate(limpio)
                    if i == 0 or i == len(limpio) - 1 or not (limpio[i - 1]["valor"] == p["valor"] == limpio[i + 1]["valor"])]
        if len({x["valor"] for x in compacto}) > 1:
            if compacto[0]["muestra"] != 0:
                compacto.insert(0, {"muestra": 0, "valor": compacto[0]["valor"]})
            res[k] = compacto
    return res


# ---------------------------------------------------------------- ejecución

def _par(r, lote, d):
    """Adapta una operación que devuelve d (o None en lote) a la forma de en_lote."""
    return None if lote is not None else (r, [])


def consolidar(sesion, reglas, salida, tmp=None, rutas_audio=None, registro=print):
    """Escribe 'salida' con la sesión consolidada. Devuelve (plan, avisos)."""
    import os
    import tempfile
    from .comun import aplicar, leer_comun
    from .protools.descifrado import leer_descifrado
    from .protools.grupos import agregar_grupos
    from .protools.inserts import copiar_inserts, plugins_de
    from .protools.reconstruccion import cifrar
    from .protools.ruteo import copiar_color, copiar_midi, copiar_ruteo
    from .show import _ruta_audio

    comun = leer_comun(sesion)
    fs = comun["unidades"]["frecuencia_muestreo"]
    plan = planear(comun, reglas)
    avisos = list(plan["avisos"])
    pistas = {p["nombre"]: p for p in comun["pistas"]}
    rutas = rutas_audio or {a["id"]: _ruta_audio(sesion, a["ruta"]) for a in comun["archivos"]}
    if not rutas_audio:
        rutas.update(rutas_de_sesion(sesion, comun))
        faltan = [r for r in rutas.values() if not Path(r).exists()]
        if faltan:
            avisos.append(f"{len(faltan)} archivos de audio no se encuentran (p. ej. {faltan[0]})")
    objetivo = reglas.get("normalizar_db")
    tmp = Path(tmp or tempfile.mkdtemp(prefix="trarin_consolidar_"))
    tmp.mkdir(parents=True, exist_ok=True)

    # 1) modelo: se quitan las de origen y las borradas, se agregan las nuevas
    ventanas = plan["ventanas"]
    nuevas_modelo = []
    ganancias = {}
    for nombre, x in plan["nuevas"].items():
        clips = []
        for f, cancion in x["fuentes"]:
            propios = [c for c in pistas[f]["clips"] if _cancion(ventanas, c["muestra"]) == cancion]
            delta = None
            if objetivo is not None:
                try:
                    rms = medir(propios, rutas, fs)
                except Exception as e:
                    rms = None
                    avisos.append(f"{f} ({cancion}): no se pudo medir ({e})")
                if rms is not None:
                    delta = objetivo - rms
                ganancias[(f, cancion)] = (rms, delta)
                registro(f"medido {f} ({cancion}): {rms if rms is None else round(rms, 1)} dBFS")
            clips += [_clip_nuevo(c, delta) for c in propios]
        clips.sort(key=lambda c: c["muestra"])
        nuevas_modelo.append({"nombre": nombre, "tipo": "audio", "canales": x["canales"], "clips": clips,
                              "mezcla": {"volumen_db": 0.0, "paneo": x["paneo"], "mute": False}})
    for nombre, x in plan["midi"].items():
        clips = []
        for f in x["fuentes"]:
            clips += [{"tipo": "midi", "nombre": c.get("nombre"), "tick": c["tick"], "largo_ticks": c["largo_ticks"],
                       "notas": [{k: n[k] for k in ("tick", "largo_ticks", "nota", "velocidad")} for n in c["notas"]]}
                      for c in pistas[f]["clips"]]
        nuevas_modelo.append({"nombre": nombre, "tipo": "midi", "clips": sorted(clips, key=lambda c: c["tick"]),
                              "mezcla": {}})
    for a in reglas.get("auxiliares") or []:
        if a["nombre"] in pistas:
            raise ErrorConsolidar(f"el auxiliar {a['nombre']!r} ya existe")
        nuevas_modelo.append({"nombre": a["nombre"], "tipo": "retorno", "canales": 2, "clips": [],
                              "mezcla": {"volumen_db": 0.0, "paneo": [-1.0, 1.0], "mute": False}})
    fuera = set(plan["quitar"]) | set(plan["quitar_midi"]) | set(plan["borrar"])
    modelo = dict(comun, pistas=[p for p in comun["pistas"] if p["nombre"] not in fuera] + nuevas_modelo)
    paso1 = tmp / "paso1.ptx"
    registro("escribiendo las pistas nuevas…")
    aplicar(modelo, sesion, str(paso1))

    # 2) plugins, color, salida MIDI y ruteo: cada cosa en un lote (un solo reconstruir)
    from .protools.ruteo import en_lote
    d = leer_descifrado(str(paso1))
    o = leer_descifrado(sesion)
    principal = reglas.get("salida_principal", "Speakers 1-2")

    def avisando(etq, f):
        def g(dd, lote):
            try:
                r = f(dd, lote)
            except Exception as e:
                if type(e).__name__ == "NoAgrupable":
                    raise
                avisos.append(f"{etq} ({e})")
                return (dd, []) if lote is None else []
            return r
        return g

    def color(nombre, f):
        return avisando(f"{nombre}: no se copió el color",
                        lambda dd, lote: _par(copiar_color(dd, nombre, o, f, lote=lote), lote, dd))

    def plugins(nombre, f):
        return avisando(f"{nombre}: no se copiaron los plugins de {f}",
                        lambda dd, lote: _par(copiar_inserts(dd, nombre, o, f, lote=lote), lote, dd))

    def salida_de(nombre, bus, origen=None, renombrar=None):
        def f(dd, lote):
            r = copiar_ruteo(dd, nombre, dd, origen or nombre, renombrar=renombrar, salida=bus,
                             envios=False, lote=lote)
            return r if lote is not None else r
        return f

    colores, insertos = [], []
    for nombre, x in plan["nuevas"].items():
        origenes = list(dict.fromkeys(f for f, _ in x["fuentes"]))
        colores.append(color(nombre, origenes[0]))
        con = [f for f in origenes if plugins_de(o, f)]
        if con:
            insertos.append(plugins(nombre, con[0]))
            for f in con[1:]:
                avisos.append(f"{nombre}: los plugins de {f} ({', '.join(plugins_de(o, f))}) no se copiaron "
                              f"(ya lleva los de {con[0]})")
        elif plugins_de(d, nombre):
            # la pista nueva es copia de otra de la sesión (con sus plugins): se dejan sin plugins
            insertos.append(avisando(f"{nombre}: no se quitaron los plugins de la plantilla",
                                     lambda dd, lote, n=nombre, f=origenes[0]:
                                     _par(copiar_inserts(dd, n, o, f, lote=lote), lote, dd)))
    for nombre, x in plan["midi"].items():
        colores.append(color(nombre, x["fuentes"][0]))
    for grupo in (colores, insertos):
        d, av = en_lote(d, grupo)
        avisos += av
    for nombre, x in plan["midi"].items():
        try:
            d, av = copiar_midi(d, nombre, o, x["fuentes"][0])
            avisos += av
        except Exception as e:
            avisos.append(f"{nombre}: no se copió la salida MIDI ({e})")
    rutas_lote = [salida_de(nombre, x["categoria"].get("salida") or principal)
                  for nombre, x in plan["nuevas"].items()]
    for a in reglas.get("auxiliares") or []:
        entrada = pistas[a["plantilla"]]["mezcla"]["entrada"]
        rutas_lote.append(salida_de(a["nombre"], principal, a["plantilla"], {entrada: a["bus"]}))
    for r in reglas.get("rutas") or []:
        for nombre in r["pistas"]:
            if nombre not in pistas or nombre in fuera:
                avisos.append(f"rutas: la pista {nombre!r} no está")
                continue
            if r.get("bus"):
                rutas_lote.append(salida_de(nombre, r["bus"]))
    d, av = en_lote(d, rutas_lote)
    avisos += av
    paso2 = tmp / "paso2.ptx"
    paso2.write_bytes(cifrar(d))

    # 3) automatización de las pistas nuevas
    m2 = leer_comun(str(paso2))
    for p in m2["pistas"]:
        x = plan["nuevas"].get(p["nombre"])
        if x is not None:
            p["automatizacion"] = automatizacion(x["fuentes"], pistas, ventanas)
            p["mezcla"]["volumen_db"] = 0.0
            p["mezcla"]["paneo"] = x["paneo"]
    paso3 = tmp / "paso3.ptx"
    aplicar(m2, str(paso2), str(paso3))

    # 4) grupos
    d = leer_descifrado(str(paso3))
    grupos = {}
    for nombre, x in plan["nuevas"].items():
        grupos.setdefault(x["categoria"].get("grupo", x["categoria"]["nombre"]), []).append(nombre)
    for nombre, x in plan["midi"].items():
        grupos.setdefault(x["grupo"], []).append(nombre)
    for r in reglas.get("rutas") or []:
        if r.get("grupo"):
            grupos.setdefault(r["grupo"], []).extend(n for n in r["pistas"] if n in pistas and n not in fuera)
    m3 = leer_comun(str(paso3))
    if reglas.get("grupo_midi"):
        grupos[reglas["grupo_midi"]] = [p["nombre"] for p in m3["pistas"] if p["tipo"] in ("midi", "0x0007")]
    existentes = {g["nombre"] for g in (m3.get("nativo") or {}).get("grupos", [])}
    lista = [{"nombre": g, "pistas": v} for g, v in grupos.items() if g not in existentes]
    for g in grupos:
        if g in existentes:
            avisos.append(f"el grupo {g!r} ya existe en la sesión (no se crea)")
    d = agregar_grupos(d, lista)
    Path(salida).write_bytes(cifrar(d))
    for f in (paso1, paso2, paso3):
        try:
            os.unlink(f)
        except OSError:
            pass
    return plan, avisos, ganancias

"""Escuchar sin abrir Pro Tools: una mezcla rápida de un tramo de la sesión en
un WAV estéreo de 16 bits, para oír lo que describe la ficha.

Se suman las pistas de audio con: clip gain (fijo o curva), fundidos de
entrada, salida y cruzados, paneo (y su automatización), fader (y su
automatización), mute (fijo o automatizado), solo y el fader del master. No
hay plugins, buses ni auxiliares (todo va directo a la suma): es una
referencia, no la mezcla final. Si la suma pasa 0 dBFS se baja el total lo
justo y se informa.

Como en Pizarrita Edit Pro, el montaje es una lista de datos (clips con su
pedazo de archivo y su lugar) y el resultado es siempre un archivo nuevo, en
`trarin/escuchas/` junto a la sesión. Se arma por tramos de 20 s, así que
sirve también para un show entero."""

import math
import os
import re
import wave
from pathlib import Path

TRAMO_S = 20.0


class ErrorEscucha(Exception):
    pass


def _pan(p):
    """(izquierda, derecha) con ley de potencia constante (-3 dB al centro)."""
    import numpy as np
    a = (np.clip(p, -1, 1) + 1) * math.pi / 4
    return np.cos(a), np.sin(a)


def _curva(xs, puntos, clave="valor", escala=1.0):
    import numpy as np
    return np.interp(xs, [p["muestra"] for p in puntos], [p[clave] * escala for p in puntos])


def _escalon(xs, puntos):
    import numpy as np
    m = np.array([p["muestra"] for p in puntos], float)
    v = np.array([p["valor"] for p in puntos], float)
    return v[np.clip(np.searchsorted(m, xs, side="right") - 1, 0, len(v) - 1)]


def _rango(ses, cancion, desde_compas, hasta_compas, desde_s, hasta_s):
    """(a, b, nombre) en segundos de la sesión."""
    from . import fichas
    fin_sesion = max((c["segundos"] + c["largo_muestras"] / ses.fs for p in ses.comun["pistas"]
                      for c in p.get("clips") or [] if c.get("segundos") is not None and c.get("largo_muestras")),
                     default=0.0)
    if cancion:
        cn, a, b = fichas._ventana_cancion(ses.comun, cancion)
        b = min(b, fin_sesion)
        nombre = cn
        if desde_compas or hasta_compas:
            primero = ses.reloj.compas_de(a)
            if desde_compas:
                a = ses.reloj.segundo_de_compas(primero + desde_compas - 1)
            if hasta_compas:
                b = ses.reloj.segundo_de_compas(primero + hasta_compas)          # hasta el final de ese compás
            nombre += f" c{desde_compas or 1}-{hasta_compas or 'fin'}"
    elif desde_compas or hasta_compas:
        a = ses.reloj.segundo_de_compas(desde_compas or 1)
        b = ses.reloj.segundo_de_compas(hasta_compas + 1) if hasta_compas else fin_sesion
        nombre = f"c{desde_compas or 1}-{hasta_compas or 'fin'}"
    else:
        a = desde_s if desde_s is not None else 0.0
        b = hasta_s if hasta_s is not None else fin_sesion
        nombre = f"{int(a // 60)}m{int(a % 60):02d}-{int(b // 60)}m{int(b % 60):02d}"
    if b <= a:
        raise ErrorEscucha("el tramo pedido está vacío")
    return a, b, nombre


def _salida_libre(carpeta, nombre):
    carpeta.mkdir(parents=True, exist_ok=True)
    base = re.sub(r'[<>:"/\\|?*]+', "_", nombre).strip() or "escucha"
    r = carpeta / f"{base}.wav"
    k = 2
    while r.exists():
        r = carpeta / f"{base} ({k}).wav"
        k += 1
    return r


def mezclar(sesion, cancion=None, desde_compas=None, hasta_compas=None, desde_s=None, hasta_s=None,
            pistas=None, frecuencia=None, salida=None, registro=print):
    """Escribe la mezcla rápida del tramo pedido. Devuelve {salida, desde,
    hasta, segundos, pistas, bajada_db, avisos}."""
    import numpy as np
    from . import audio, fichas
    from .vistas import _normal
    ses = fichas.Sesion(sesion)
    fs = ses.fs
    a, b, nombre = _rango(ses, cancion, desde_compas, hasta_compas, desde_s, hasta_s)
    m0, m1 = int(round(a * fs)), int(round(b * fs))
    avisos = []

    todas = [p for p in ses.comun["pistas"] if p["tipo"] == "audio" and p.get("clips")]
    en_solo = [p for p in todas if (p.get("mezcla") or {}).get("solo")]
    if en_solo:
        avisos.append(f"hay pistas en solo: suenan solo esas ({', '.join(p['nombre'] for p in en_solo)})")
        todas = en_solo
    if pistas:
        todas = [p for p in todas if re.search(pistas, _normal(p["nombre"]))]
        if not todas:
            raise ErrorEscucha(f"ninguna pista calza con {pistas!r}")
    activas = []
    for p in todas:
        clips = [c for c in p["clips"] if c.get("tipo") == "audio" and c.get("archivo") is not None
                 and c.get("muestra") is not None and c["muestra"] < m1 and c["muestra"] + c["largo_muestras"] > m0]
        if clips:
            activas.append((p, clips))
    if not activas:
        raise ErrorEscucha("no hay audio en el tramo pedido")
    otros = [p["nombre"] for p in ses.comun["pistas"] if p["tipo"] in ("midi", "0x0007")]
    if otros:
        avisos.append(f"las pistas MIDI y de instrumento no suenan ({len(otros)})")
    master = next((x for x in (ses.comun.get("nativo") or {}).get("master") or [] if x.get("tipo") == "master"), None)
    g_master = 10 ** (float((master or {}).get("volumen_db") or 0.0) / 20)
    if master and master.get("inserts"):
        avisos.append("el master tiene plugins (" + ", ".join(i.get("plugin", "?") for i in master["inserts"])
                      + ") que aquí no se aplican")

    rutas = {}
    for ids, rs in ses.fuentes.items():
        for i, r in zip(ids, rs):
            rutas[i] = r
    faltan = set()
    salida = Path(salida) if salida else _salida_libre(fichas.carpeta_fichas(ses.sesion).parent / "escuchas", nombre)
    crudo = salida.with_suffix(".f32.tmp")
    pico = 0.0
    total = m1 - m0
    paso = int(TRAMO_S * fs)
    registro(f"mezclando {nombre}: {len(activas)} pistas, {total / fs:.0f} s")
    try:
        with open(crudo, "wb") as fcrudo:
            for c0 in range(m0, m1, paso):
                c1 = min(c0 + paso, m1)
                n = c1 - c0
                xs = np.arange(c0, c1, dtype=np.float64)
                suma = np.zeros((n, 2), np.float32)
                for p, clips in activas:
                    can = 2 if any(c.get("archivos_canales") and len(c["archivos_canales"]) == 2 for c in clips) else 1
                    buf = np.zeros((n, can), np.float32)
                    hay = False
                    for c in clips:
                        x0, x1 = max(c["muestra"], c0), min(c["muestra"] + c["largo_muestras"], c1)
                        if x1 <= x0:
                            continue
                        off = int(c.get("desde_muestra") or 0) + (x0 - c["muestra"])
                        ids = c.get("archivos_canales") or [c["archivo"]]
                        señal = []
                        for i in ids[:can]:
                            r = rutas.get(i)
                            if not r or not os.path.exists(r):
                                faltan.add(str(i))
                                señal = None
                                break
                            y, info = audio.leer(r, off, x1 - x0)
                            if info["frecuencia"] != fs:
                                avisos.append(f"{p['nombre']}: un archivo está a {info['frecuencia']} Hz (no suena)")
                                señal = None
                                break
                            señal.append(y.mean(axis=1))
                        if not señal:
                            continue
                        largo = min(len(s) for s in señal)
                        y = np.stack([s[:largo] for s in señal], axis=1).astype(np.float32)
                        if y.shape[1] < can:
                            y = np.repeat(y, can, axis=1)
                        pos = np.arange(x0, x0 + largo, dtype=np.float64)
                        # clip gain (curva relativa al inicio del clip, o fijo)
                        if c.get("ganancia_curva"):
                            g = 10 ** (_curva(pos - c["muestra"], c["ganancia_curva"], "db") / 20)
                        else:
                            g = np.full(largo, 10 ** (float(c.get("ganancia_db") or 0.0) / 20))
                        # fundidos
                        for f in c.get("fundidos") or []:
                            fi, fn = int(f.get("muestra", 0)), int(f.get("muestras", 0))
                            if fn <= 0:
                                continue
                            dentro = (pos >= fi) & (pos < fi + fn)
                            if not dentro.any():
                                continue
                            t = (pos[dentro] - fi) / fn
                            entra = f["tipo"] == "entrada" or (f["tipo"] == "cruzado" and abs(fi - c["muestra"]) < fn)
                            forma = (f.get("curva") or {}).get("forma")
                            if forma == "lineal":
                                k = t if entra else 1 - t
                            else:                                       # potencia constante
                                k = np.sin(t * math.pi / 2) if entra else np.cos(t * math.pi / 2)
                            g[dentro] *= k
                        buf[x0 - c0:x0 - c0 + largo] += y * g[:, None].astype(np.float32)
                        hay = True
                    if not hay:
                        continue
                    # fader, mute y paneo
                    mz = p.get("mezcla") or {}
                    au = p.get("automatizacion") or {}
                    vol = au.get("volumen_db") or []
                    if len({q["valor"] for q in vol}) > 1:
                        gv = 10 ** (_curva(xs, vol) / 20)
                    else:
                        gv = np.full(n, 10 ** (float(vol[0]["valor"] if vol else (mz.get("volumen_db") or 0.0)) / 20))
                    if au.get("mute"):
                        gv = np.where(_escalon(xs, au["mute"]) > 0.5, 0.0, gv)
                    elif mz.get("mute"):
                        continue
                    pan = mz.get("paneo")
                    if can == 1:
                        pa = (_curva(xs, au["paneo_izq"], escala=0.01) if au.get("paneo_izq")
                              else np.full(n, float(pan if isinstance(pan, (int, float)) else 0.0)))
                        l, r = _pan(pa)
                        suma[:, 0] += buf[:, 0] * (gv * l).astype(np.float32)
                        suma[:, 1] += buf[:, 0] * (gv * r).astype(np.float32)
                    else:
                        pl, pr = pan if isinstance(pan, list) and len(pan) == 2 else (-1.0, 1.0)
                        for ch, (clave, fijo) in enumerate((("paneo_izq", pl), ("paneo_der", pr))):
                            pa = _curva(xs, au[clave], escala=0.01) if au.get(clave) else np.full(n, float(fijo))
                            l, r = _pan(pa)              # paneado a su lado: 0 dB en ese lado
                            suma[:, 0] += buf[:, ch] * (gv * l).astype(np.float32)
                            suma[:, 1] += buf[:, ch] * (gv * r).astype(np.float32)
                suma *= g_master
                pico = max(pico, float(np.abs(suma).max()) if n else 0.0)
                suma.astype("<f4").tofile(fcrudo)
                registro(f"  {(c1 - m0) / fs:.0f} de {total / fs:.0f} s")
        # a 16 bits, bajando lo justo si la suma pasa 0 dBFS
        tope = 10 ** (-0.3 / 20)
        escala = min(1.0, tope / pico) if pico > 0 else 1.0
        bajada = round(-20 * math.log10(escala), 1) if escala < 1 else 0.0
        fs_sal = int(frecuencia) if frecuencia else fs
        flujo = None
        if fs_sal != fs:
            import soxr
            flujo = soxr.ResampleStream(fs, fs_sal, 2, dtype="float32", quality="HQ")
        with wave.open(str(salida), "wb") as w, open(crudo, "rb") as fcrudo:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(fs_sal)
            while True:
                bloque = np.fromfile(fcrudo, dtype="<f4", count=paso * 2)
                ultimo = len(bloque) < paso * 2
                y = bloque.reshape(-1, 2) * escala
                if flujo is not None:
                    y = flujo.resample_chunk(y, last=ultimo)
                w.writeframes((np.clip(y, -1, 1) * 32767).round().astype("<i2").tobytes())
                if ultimo:
                    break
    finally:
        if crudo.exists():
            crudo.unlink()
    if faltan:
        avisos.append(f"faltan {len(faltan)} archivos de audio (esos clips no suenan)")
    if bajada:
        avisos.append(f"la suma pasaba 0 dBFS (pico {20 * math.log10(pico):+.1f} dBFS): se bajó todo {bajada} dB")
    avisos.append("sin plugins, buses ni auxiliares: es una referencia, no la mezcla final")
    return {"salida": str(salida), "desde": ses.reloj(a), "hasta": ses.reloj(b), "segundos": round(b - a, 1),
            "pistas": len(activas), "bajada_db": bajada, "frecuencia": fs_sal, "avisos": avisos}

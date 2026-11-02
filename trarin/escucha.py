"""Escuchar sin abrir Pro Tools: una mezcla rápida de un tramo de la sesión en
un WAV estéreo de 16 bits, para oír lo que describe la ficha.

Se suman las pistas de audio con: clip gain (fijo o curva), fundidos de
entrada, salida y cruzados, paneo (y su automatización), fader (y su
automatización), mute (fijo o automatizado), solo, el fader (automatización y
mute) de los auxiliares por los que pasa cada pista (pista → bus → aux → bus →
aux…, hasta 4 niveles; el paneo del aux no se aplica) y el fader del master.
No hay plugins (ni los de los auxiliares): es una referencia, no la mezcla
final. Si la suma pasa 0 dBFS se baja el total lo justo y se informa.
Salida WAV de 16 bits o MP3 (formato="mp3", con lameenc: pip install
"trarin[mp3]").

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


def _salida_libre(carpeta, nombre, ext=".wav"):
    carpeta.mkdir(parents=True, exist_ok=True)
    base = re.sub(r'[<>:"/\\|?*]+', "_", nombre).strip() or "escucha"
    r = carpeta / f"{base}{ext}"
    k = 2
    while r.exists():
        r = carpeta / f"{base} ({k}){ext}"
        k += 1
    return r


def _cadena_aux(pista, retornos, maximo=4):
    """Los auxiliares por los que pasa la pista, en orden: su salida va a un bus
    que es la entrada de un retorno, cuya salida puede ir a otro bus."""
    out, salida = [], (pista.get("mezcla") or {}).get("salida")
    while salida and len(out) < maximo:
        aux = retornos.get(salida)
        if aux is None or aux in out:
            break
        out.append(aux)
        salida = (aux.get("mezcla") or {}).get("salida")
    return out


def _ganancia_pista(p, xs, n):
    """(ganancia por muestra del fader con su automatización, muteada: bool fijo)."""
    import numpy as np
    mz = p.get("mezcla") or {}
    au = p.get("automatizacion") or {}
    vol = au.get("volumen_db") or []
    if len({q["valor"] for q in vol}) > 1:
        gv = 10 ** (_curva(xs, vol) / 20)
    else:
        gv = np.full(n, 10 ** (float(vol[0]["valor"] if vol else (mz.get("volumen_db") or 0.0)) / 20))
    if au.get("mute"):
        gv = np.where(_escalon(xs, au["mute"]) > 0.5, 0.0, gv)
        return gv, False
    return gv, bool(mz.get("mute"))


def _codificador_mp3(fs, kbps):
    try:
        import lameenc
    except ImportError as e:
        raise ErrorEscucha('para MP3 hace falta lameenc: pip install "trarin[mp3]"') from e
    enc = lameenc.Encoder()
    enc.set_bit_rate(int(kbps))
    enc.set_in_sample_rate(int(fs))
    enc.set_channels(2)
    enc.set_quality(2)
    return enc


def mezclar(sesion, cancion=None, desde_compas=None, hasta_compas=None, desde_s=None, hasta_s=None,
            pistas=None, frecuencia=None, salida=None, registro=print, formato="wav", mp3_kbps=192,
            auxiliares=True, crudo=False, exacta=False):
    """Escribe la mezcla rápida del tramo pedido. Devuelve {salida, desde,
    hasta, segundos, pistas, bajada_db, avisos}. formato: "wav" (16 bits) o
    "mp3" (lameenc). auxiliares: aplicar el fader de los auxiliares por los que
    pasa cada pista. crudo: solo clip gain y fundidos (sin fader, paneo, mute,
    solo, auxiliares ni master; una pista mono va igual a los dos lados): «la
    pista tal cual está grabada». exacta: 'pistas' es el nombre exacto de una
    pista, no una expresión regular."""
    import numpy as np
    from . import audio, fichas
    from .vistas import _normal
    ses = sesion if isinstance(sesion, fichas.Sesion) else fichas.Sesion(sesion)
    fs = ses.fs
    a, b, nombre = _rango(ses, cancion, desde_compas, hasta_compas, desde_s, hasta_s)
    m0, m1 = int(round(a * fs)), int(round(b * fs))
    avisos = []

    todas = [p for p in ses.comun["pistas"] if p["tipo"] == "audio" and p.get("clips")]
    en_solo = [p for p in todas if (p.get("mezcla") or {}).get("solo")]
    if en_solo and not crudo and not pistas:
        avisos.append(f"hay pistas en solo: suenan solo esas ({', '.join(p['nombre'] for p in en_solo)})")
        todas = en_solo
    if pistas:
        if exacta:
            todas = [p for p in todas if p["nombre"] == pistas]
        else:
            todas = [p for p in todas if re.search(pistas, _normal(p["nombre"]))]
        if not todas:
            raise ErrorEscucha(f"ninguna pista calza con {pistas!r}")
    if crudo:
        auxiliares = False
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
    # el master como pista del modelo común: fader, su automatización y mute, igual que una pista
    master_p = next((p for p in ses.comun["pistas"] if p["tipo"] in ("0x0005", "master")), None)
    if master_p is None and master:
        master_p = {"mezcla": {"volumen_db": master.get("volumen_db"), "mute": master.get("mute")},
                    "automatizacion": master.get("automatizacion") or {}}
    if not crudo and master_p and (master_p.get("automatizacion") or {}).get("volumen_db"):
        avisos.append("con la automatización de volumen del master")
    if master and master.get("inserts") and not crudo:
        avisos.append("el master tiene plugins (" + ", ".join(i.get("plugin", "?") for i in master["inserts"])
                      + ") que aquí no se aplican")
    retornos = {}
    if auxiliares:
        for p in ses.comun["pistas"]:
            if p["tipo"] == "retorno" and (p.get("mezcla") or {}).get("entrada"):
                retornos.setdefault(p["mezcla"]["entrada"], p)
    cadenas = {p["nombre"]: _cadena_aux(p, retornos) for p, _ in activas}
    usados = sorted({a["nombre"] for c in cadenas.values() for a in c})
    if usados:
        plugins = sorted({e.get("nombre") or "?" for a in retornos.values() if a["nombre"] in usados
                          for e in a.get("efectos") or []})
        avisos.append("con el fader de los auxiliares " + ", ".join(usados)
                      + (" (sin sus plugins: " + ", ".join(plugins) + ")" if plugins else ""))

    rutas = {}
    for ids, rs in ses.fuentes.items():
        for i, r in zip(ids, rs):
            rutas[i] = r
    faltan = set()
    if formato not in ("wav", "mp3"):
        raise ErrorEscucha("formato: wav o mp3")
    ext = "." + formato
    if pistas:
        nombre += " [" + (pistas if exacta else ", ".join(p["nombre"] for p in todas[:3]) + ("…" if len(todas) > 3 else "")) + "]"
    if crudo:
        nombre += " crudo"
    salida = Path(salida) if salida else _salida_libre(fichas.carpeta_fichas(ses.sesion).parent / "escuchas", nombre, ext)
    if salida.suffix.lower() != ext:
        salida = salida.with_suffix(ext)
    temporal = salida.with_suffix(".f32.tmp")
    pico = 0.0
    total = m1 - m0
    paso = int(TRAMO_S * fs)
    registro(f"mezclando {nombre}: {len(activas)} pistas, {total / fs:.0f} s")
    try:
        with open(temporal, "wb") as fcrudo:
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
                    if crudo:                                  # tal cual está grabada
                        if can == 1:
                            suma[:, 0] += buf[:, 0]
                            suma[:, 1] += buf[:, 0]
                        else:
                            suma += buf
                        continue
                    # fader, mute y paneo; después, el fader de cada auxiliar de su cadena
                    mz = p.get("mezcla") or {}
                    au = p.get("automatizacion") or {}
                    gv, muteada = _ganancia_pista(p, xs, n)
                    if muteada:
                        continue
                    for aux in cadenas[p["nombre"]]:
                        ga, muteado = _ganancia_pista(aux, xs, n)
                        if muteado:
                            gv = np.zeros(n)
                            break
                        gv = gv * ga
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
                if not crudo and master_p:
                    gm, mudo = _ganancia_pista(master_p, xs, n)
                    suma *= (0.0 if mudo else 1.0) * gm[:, None].astype(np.float32)
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
        enc = _codificador_mp3(fs_sal, mp3_kbps) if formato == "mp3" else None
        destino = open(salida, "wb") if enc else wave.open(str(salida), "wb")
        with destino as w, open(temporal, "rb") as fcrudo:
            if not enc:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(fs_sal)
            while True:
                bloque = np.fromfile(fcrudo, dtype="<f4", count=paso * 2)
                ultimo = len(bloque) < paso * 2
                y = bloque.reshape(-1, 2) * escala
                if flujo is not None:
                    y = flujo.resample_chunk(y, last=ultimo)
                pcm = (np.clip(y, -1, 1) * 32767).round().astype("<i2").tobytes()
                if enc:
                    w.write(enc.encode(pcm))
                    if ultimo:
                        w.write(enc.flush())
                else:
                    w.writeframes(pcm)
                if ultimo:
                    break
    finally:
        if temporal.exists():
            temporal.unlink()
    if faltan:
        avisos.append(f"faltan {len(faltan)} archivos de audio (esos clips no suenan)")
    if bajada:
        avisos.append(f"la suma pasaba 0 dBFS (pico {20 * math.log10(pico):+.1f} dBFS): se bajó todo {bajada} dB")
    if crudo:
        avisos.append("crudo: solo clip gain y fundidos; sin fader, paneo, mute, auxiliares, master ni plugins")
    else:
        avisos.append("sin plugins" + ("" if usados else " ni auxiliares") + ": es una referencia, no la mezcla final")
    return {"salida": str(salida), "desde": ses.reloj(a), "hasta": ses.reloj(b), "segundos": round(b - a, 1),
            "pistas": len(activas), "nombres": [p["nombre"] for p, _ in activas], "bajada_db": bajada,
            "frecuencia": fs_sal, "avisos": avisos}


def por_pista(sesion, cancion=None, desde_compas=None, hasta_compas=None, desde_s=None, hasta_s=None,
              pistas=None, frecuencia=None, carpeta=None, registro=print, formato="wav", mp3_kbps=192,
              crudo=False, auxiliares=True):
    """Un archivo por pista de audio con sonido en el tramo («dame las pistas
    sin efectos»), en trarin/escuchas/<tramo>/ junto a la sesión. Cada pista
    con su fader, paneo y auxiliares (sin plugins); con crudo=True, tal cual
    grabada. Devuelve {carpeta, archivos: [{pista, salida, bajada_db}], avisos}."""
    import re as _re
    from . import fichas
    from .vistas import _normal
    ses = fichas.Sesion(sesion)
    a, b, nombre = _rango(ses, cancion, desde_compas, hasta_compas, desde_s, hasta_s)
    fs = ses.fs
    m0, m1 = int(round(a * fs)), int(round(b * fs))
    candidatas = []
    for p in ses.comun["pistas"]:
        if p["tipo"] != "audio" or (pistas and not _re.search(pistas, _normal(p["nombre"]))):
            continue
        if any(c.get("tipo") == "audio" and c.get("muestra") is not None and c["muestra"] < m1
               and c["muestra"] + c["largo_muestras"] > m0 for c in p.get("clips") or []):
            candidatas.append(p["nombre"])
    if not candidatas:
        raise ErrorEscucha("no hay pistas de audio con sonido en el tramo pedido")
    carpeta = Path(carpeta) if carpeta else fichas.carpeta_fichas(ses.sesion).parent / "escuchas" / \
        (_re.sub(r'[<>:"/\\|?*]+', "_", nombre).strip() + (" crudo" if crudo else ""))
    carpeta.mkdir(parents=True, exist_ok=True)
    archivos, avisos = [], set()
    registro(f"{len(candidatas)} pistas en {nombre} → {carpeta}")
    for k, n in enumerate(candidatas, 1):
        salida = _salida_libre(carpeta, n, "." + formato)
        try:
            r = mezclar(sesion, cancion, desde_compas, hasta_compas, desde_s, hasta_s, pistas=n, frecuencia=frecuencia,
                        salida=salida, registro=lambda *x: None, formato=formato, mp3_kbps=mp3_kbps,
                        auxiliares=auxiliares, crudo=crudo, exacta=True)
        except ErrorEscucha as e:                  # una pista muteada o sin archivos no frena el resto
            registro(f"  {k}/{len(candidatas)} {n}: {e}")
            continue
        archivos.append({"pista": n, "salida": r["salida"], "bajada_db": r["bajada_db"]})
        avisos.update(x for x in r["avisos"] if "MIDI" not in x and not x.startswith("el master"))
        registro(f"  {k}/{len(candidatas)} {n}")
    return {"carpeta": str(carpeta), "desde": ses.reloj(a), "hasta": ses.reloj(b), "archivos": archivos,
            "avisos": sorted(avisos)}

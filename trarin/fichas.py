"""Fichas de sonido: lo que una IA (o una persona) necesita saber de un audio
sin escucharlo ni leerlo entero. Es información de referencia, aproximada.

Se analiza cada «fuente» (un archivo, o los archivos L/R de un clip estéreo de
Pro Tools) una sola vez, en bloques de 0,1 s, y se guarda la serie en
`trarin/fichas/` junto a la sesión (o junto al archivo suelto). Con esa serie
se arma la ficha de cualquier tramo: un archivo, un clip, una pista entera o
una pista dentro de una canción, sin volver a leer el audio. Si el archivo
cambia (tamaño o fecha), su serie se vuelve a calcular.

Por bloque se guarda: RMS, pico, sonoridad con ponderación K (BS.1770, por el
espectro del bloque), reparto en cinco bandas, centroide y planitud del
espectro, muestras saturadas y, si hay dos canales, correlación y balance.

La ficha de un tramo trae:
- tiempo: largo, dónde empieza y termina el sonido de verdad, cuánto suena y
  los silencios largos;
- nivel: pico, RMS de lo que suena, sonoridad integrada (LUFS), rango de
  sonoridad y los momentos más fuertes;
- problemas: saturación en el archivo, picos sobre 0 dBFS por el clip gain y
  corriente continua;
- estéreo: mono, mono duplicado, casi mono, estéreo o abierto; lado y fase;
- color: bandas, grave/medio/agudo, brillante u opaco, tonal o ruidoso;
- en palabras: una línea con lo anterior.
Los tiempos van en segundos (m:ss) y, si se mira desde una sesión, también en
compás|tiempo."""

import hashlib
import json
import math
import os
import re
from pathlib import Path

VERSION = 1
BLOQUE_S = 0.1
BANDAS = (("sub", 0, 60), ("graves", 60, 250), ("medios", 250, 2000), ("presencia", 2000, 6000),
          ("agudos", 6000, None))
SILENCIO_DB = -55.0          # RMS de bloque bajo esto (o 45 dB bajo el máximo) = no suena
SATURA = 0.999               # |muestra| desde aquí cuenta como tope
CAMPOS = ("rms", "pico", "k", "bandas", "centroide", "planitud", "satura", "corr", "bal")


class ErrorFicha(Exception):
    pass


# ------------------------------------------------------------------ análisis

def _coef_k(fs):
    """Coeficientes de los dos filtros de la ponderación K (BS.1770) para la
    frecuencia fs, por transformación bilineal de los parámetros de la norma
    (a 48 kHz dan los coeficientes publicados en ella)."""
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / fs)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    est = ((Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0), \
          (1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0)
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / fs)
    a0 = 1 + K / Q + K * K
    alto = (1.0, -2.0, 1.0), (1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0)
    return est, alto


def _respuesta_k(f, fs):
    """|H|² de la ponderación K (estante de +4 dB en agudos y paso alto en
    ~38 Hz) en las frecuencias f."""
    import numpy as np
    z = np.exp(-1j * 2 * np.pi * np.asarray(f, dtype=float) / fs)
    h = 1
    for b, a in _coef_k(fs):
        h = h * (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    return np.abs(h) ** 2


def analizar(rutas, registro=None):
    """Serie por bloques de una fuente (uno o más WAV que suenan juntos: sus
    canales se juntan). Devuelve (series {campo: array}, meta)."""
    import numpy as np
    from . import audio
    infos = [audio.info(r) for r in rutas]
    fs = infos[0]["frecuencia"]
    if any(i["frecuencia"] != fs for i in infos):
        raise ErrorFicha(f"los archivos de la fuente tienen frecuencias distintas: {rutas}")
    total = min(i["muestras"] for i in infos)
    n = max(int(round(fs * BLOQUE_S)), 1)
    nb = total // n
    canales = sum(i["canales"] for i in infos)
    f = np.fft.rfftfreq(n, 1 / fs)
    ventana = np.hanning(n)
    peso_k = _respuesta_k(f, fs)
    idx_bandas = [(f >= lo) & (f < (hi if hi else fs)) for _, lo, hi in BANDAS]
    util = f >= 40
    out = {k: [] for k in CAMPOS}
    dc = np.zeros(canales)
    por_tanda = 300
    for b0 in range(0, nb, por_tanda):
        k = min(por_tanda, nb - b0)
        x = np.concatenate([audio.leer(r, b0 * n, k * n)[0][:k * n] for r in rutas], axis=1)
        dc += x.sum(axis=0)
        x = x.astype(np.float32).reshape(k, n, canales)
        ms_c = (x * x).mean(axis=1, dtype=np.float64)                  # (k, canales)
        ms = ms_c.mean(axis=1)
        ax = np.abs(x)
        pico = ax.max(axis=(1, 2)).astype(np.float64)
        lleno = ax >= SATURA
        sat = (lleno & np.roll(lleno, 1, axis=1)).sum(axis=(1, 2))      # muestras al tope seguidas
        mono = x.mean(axis=2)
        P = np.abs(np.fft.rfft(mono * ventana, axis=1)) ** 2
        tot = P.sum(axis=1) + 1e-30
        bandas = np.stack([P[:, m].sum(axis=1) / tot for m in idx_bandas], axis=1)
        cent = (P * f).sum(axis=1) / tot
        Pu = P[:, util] + 1e-30
        plan = np.exp(np.log(Pu).mean(axis=1)) / Pu.mean(axis=1)
        razon_k = (P * peso_k).sum(axis=1) / tot
        kms = ms_c.sum(axis=1) * razon_k                                # suma de canales, como BS.1770
        out["rms"].append(10 * np.log10(ms + 1e-15))
        out["pico"].append(20 * np.log10(pico + 1e-15))
        out["k"].append(10 * np.log10(kms + 1e-15))
        out["bandas"].append(bandas)
        out["centroide"].append(cent)
        out["planitud"].append(plan)
        out["satura"].append(sat)
        if canales == 2:
            L, R = x[:, :, 0], x[:, :, 1]
            out["corr"].append((L * R).sum(axis=1, dtype=np.float64)
                               / np.sqrt(ms_c[:, 0] * ms_c[:, 1] * n * n + 1e-30))
            out["bal"].append(10 * np.log10((ms_c[:, 0] + 1e-15) / (ms_c[:, 1] + 1e-15)))
        if registro and b0 and b0 % 3000 == 0:
            registro(f"  {b0 * BLOQUE_S / 60:.0f} min")
    series = {}
    for c, v in out.items():
        if v:
            series[c] = np.concatenate(v).astype(np.float32)
    meta = {"frecuencia": fs, "canales": canales, "bloques": int(nb), "muestras_bloque": n,
            "duracion_s": round(total / fs, 3), "dc": [round(float(v), 5) for v in dc / max(total, 1)]}
    return series, meta


# ------------------------------------------------------------------ almacén

def _firma(rutas):
    out = []
    for r in rutas:
        s = os.stat(r)
        out.append([s.st_size, s.st_mtime_ns])
    return out


def _clave(rutas):
    base = re.sub(r"[^\w.-]+", "_", Path(rutas[0]).stem)[:60]
    h = hashlib.sha1("|".join(str(Path(r).resolve()).lower() for r in rutas).encode("utf-8")).hexdigest()[:8]
    return f"{base}-{h}"


class Almacen:
    """Las series guardadas en una carpeta `fichas` (indice.json + un .npz por
    fuente)."""

    def __init__(self, carpeta):
        self.carpeta = Path(carpeta)
        self.indice_ruta = self.carpeta / "indice.json"
        self.indice = json.loads(self.indice_ruta.read_text(encoding="utf-8")) if self.indice_ruta.exists() else {}
        self._cache = {}

    def guardar_indice(self):
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.indice_ruta.write_text(json.dumps(self.indice, ensure_ascii=False, indent=1), encoding="utf-8")

    def vigente(self, rutas):
        e = self.indice.get(_clave(rutas))
        try:
            return bool(e) and e.get("version") == VERSION and e["firma"] == _firma(rutas) \
                and (self.carpeta / e["serie"]).exists()
        except OSError:
            return False

    def calcular(self, rutas, registro=None):
        clave, entrada = _calcular_guardar(rutas, str(self.carpeta), registro)
        self.anotar(clave, entrada)
        return clave

    def anotar(self, clave, entrada):
        self.indice[clave] = entrada
        self._cache.pop(clave, None)

    def serie(self, rutas):
        """(series, meta) de la fuente, o None si no está calculada o cambió."""
        import numpy as np
        if not self.vigente(rutas):
            return None
        clave = _clave(rutas)
        if clave not in self._cache:
            with np.load(self.carpeta / self.indice[clave]["serie"]) as z:
                self._cache[clave] = ({k: z[k] for k in z.files}, self.indice[clave])
        return self._cache[clave]


def _calcular_guardar(rutas, carpeta, registro=None):
    """Analiza una fuente y guarda su serie (también corre en otro proceso).
    Devuelve (clave, entrada del índice)."""
    import numpy as np
    firma = _firma(rutas)                 # antes de leer: si cambia mientras tanto, se recalcula después
    series, meta = analizar(rutas, registro)
    clave = _clave(rutas)
    Path(carpeta).mkdir(parents=True, exist_ok=True)
    np.savez_compressed(Path(carpeta) / f"{clave}.npz", **series)
    return clave, {"archivos": [str(r) for r in rutas], "firma": firma, "version": VERSION,
                   "serie": f"{clave}.npz", **meta}


def carpeta_fichas(ruta):
    """`trarin/fichas` junto a la sesión o al archivo."""
    from .notas import carpeta
    return carpeta(ruta) / "fichas"


# ------------------------------------------------------------------ tramos

class Tramo:
    """Bloques juntados de una o más fuentes (los clips de una pista), cada uno
    con su segundo en la sesión (o en el archivo) y el clip gain sumado."""

    def __init__(self):
        self.partes = []          # (series, meta, b0, b1, ganancia_db, t0)

    def agregar(self, series, meta, b0, b1, ganancia_db=0.0, t0=0.0):
        b0, b1 = max(int(b0), 0), min(int(b1), meta["bloques"])
        if b1 > b0:
            self.partes.append((series, meta, b0, b1, ganancia_db, t0))

    def juntar(self):
        import numpy as np
        if not self.partes:
            return None
        cols, ts = {}, []
        for s, m, b0, b1, g, t0 in self.partes:
            ts.append(t0 + np.arange(b1 - b0) * BLOQUE_S)
            for c in CAMPOS:
                if c in s:
                    v = s[c][b0:b1].astype(np.float64)
                    if c in ("rms", "pico", "k"):
                        v = v + g
                    cols.setdefault(c, []).append(v)
                elif c in ("corr", "bal"):
                    cols.setdefault(c, []).append(np.full(b1 - b0, np.nan))
        out = {c: np.concatenate(v) for c, v in cols.items()}
        out["t"] = np.concatenate(ts)
        orden = np.argsort(out["t"], kind="stable")
        out = {c: v[orden] for c, v in out.items()}
        out["canales"] = max(m["canales"] for _, m, *_ in self.partes)
        out["dc"] = max((abs(x) for _, m, *_ in self.partes for x in m.get("dc") or [0]), default=0)
        out["satura_archivo"] = None
        return out


def _reloj(t):
    t = max(t, 0)
    return f"{int(t // 60)}:{t % 60:04.1f}"


class Reloj:
    """Segundos → «m:ss.s» y, si hay sesión, también «compás|tiempo»."""

    def __init__(self, comun=None):
        self.comun = comun
        if comun:
            from .protools import tiempo as T
            self.mapa = T.MapaTempo(comun["tempo"])
            cs, num = [], 1
            for i, c in enumerate(sorted(comun.get("compases") or [], key=lambda c: c["tick"])):
                if cs:
                    p = cs[-1]
                    largo = T.TICKS_NEGRA * 4 // p["denominador"] * p["numerador"]
                    num = p["compas"] + -(-(c["tick"] - p["tick"]) // largo)
                cs.append({**c, "compas": num})
            self.compases = cs

    def __call__(self, t):
        if not self.comun:
            return _reloj(t)
        from .protools import tiempo as T
        pos = T.compas_tiempo(T.tick_de_segundos(t + 1e-6, self.mapa), self.compases)
        return f"{_reloj(t)} (c. {pos.rsplit('|', 1)[0]})"


def resumir(j, reloj=None):
    """La ficha de un tramo ya juntado (ver Tramo.juntar)."""
    import numpy as np
    reloj = reloj or Reloj()
    t, rms, pico = j["t"], j["rms"], j["pico"]
    largo = len(t) * BLOQUE_S
    umbral = max(SILENCIO_DB, float(rms.max()) - 45) if len(rms) else SILENCIO_DB
    suena = rms > umbral
    out = {"largo_s": round(largo, 1)}
    if not suena.any():
        out["tiempo"] = {"suena_pct": 0}
        out["palabras"] = "silencio (no se oye nada sobre " + f"{SILENCIO_DB:.0f} dBFS)"
        return out
    ia = np.flatnonzero(suena)
    desde, hasta = float(t[ia[0]]), float(t[ia[-1]]) + BLOQUE_S
    silencios = []
    saltos = np.diff(t[ia])
    for k in np.flatnonzero(saltos > 2.0)[:8]:
        silencios.append([reloj(float(t[ia[k]]) + BLOQUE_S), reloj(float(t[ia[k + 1]]))])
    pct = round(100 * suena.mean())
    out["tiempo"] = {"desde": reloj(float(t[0])), "hasta": reloj(float(t[-1]) + BLOQUE_S),
                     "sonido_desde": reloj(desde), "sonido_hasta": reloj(hasta), "suena_pct": pct}
    if silencios:
        out["tiempo"]["silencios"] = silencios

    # nivel
    ms = 10 ** (rms[suena] / 10)
    kms = 10 ** (j["k"] / 10)
    corto = np.convolve(kms, np.ones(4) / 4, mode="same")              # 400 ms
    lu = -0.691 + 10 * np.log10(corto + 1e-15)
    g = lu > -70
    integrada = None
    if g.any():
        rel = -0.691 + 10 * np.log10(corto[g].mean()) - 10
        g2 = lu > max(-70, rel)
        integrada = round(float(-0.691 + 10 * np.log10(corto[g2].mean())), 1)
    st = -0.691 + 10 * np.log10(np.convolve(kms, np.ones(30) / 30, mode="same") + 1e-15)   # 3 s
    activos = st[suena & (st > -70)]
    nivel = {"pico_dbfs": round(float(pico.max()), 1) + 0.0, "rms_dbfs": round(float(10 * np.log10(ms.mean())), 1),
             "lufs": integrada}
    if len(activos) > 10:
        nivel["rango_lu"] = round(float(np.percentile(activos, 95) - np.percentile(activos, 10)), 1)
    fuertes, orden = [], np.argsort(-st)
    for i in orden:
        if len(fuertes) == 3:
            break
        if all(abs(t[i] - t[k]) > 10 for k in fuertes):
            fuertes.append(i)
    nivel["mas_fuerte"] = [{"en": reloj(float(t[i])), "lufs_3s": round(float(st[i]), 1)} for i in fuertes]
    out["nivel"] = nivel

    # problemas
    prob = {}
    sat = j["satura"]
    if sat.sum() > 0:
        eventos = _eventos(t, sat > 0)
        prob["saturacion"] = {"muestras": int(sat.sum()), "veces": len(eventos),
                              "donde": [reloj(e) for e in eventos[:5]]}
    sobre = pico > 0.05
    if sobre.any():
        eventos = _eventos(t, sobre)
        prob["sobre_0dbfs"] = {"veces": len(eventos), "donde": [reloj(e) for e in eventos[:5]],
                               "pico_dbfs": round(float(pico.max()), 1)}
    if j["dc"] > 0.01:
        prob["corriente_continua"] = round(float(j["dc"]), 3)
    out["problemas"] = prob

    # estéreo
    est = {"canales": j["canales"]}
    if j["canales"] == 2 and "corr" in j and np.isfinite(j["corr"][suena]).any():
        corr = j["corr"][suena]
        pesos = ms[np.isfinite(corr)]
        c = float(np.average(corr[np.isfinite(corr)], weights=pesos))
        b = float(np.average(j["bal"][suena][np.isfinite(corr)], weights=pesos))
        est.update({"correlacion": round(c, 2), "balance_db": round(b, 1)})
        if c > 0.995 and abs(b) < 0.5:
            est["tipo"] = "mono duplicado"
        elif c > 0.9:
            est["tipo"] = "casi mono"
        elif c < -0.5:
            est["tipo"] = "fase invertida"
        elif c < -0.2:
            est["tipo"] = "contrafase parcial (pierde al sumarse en mono)"
        elif c < 0.3:
            est["tipo"] = "estéreo abierto"
        else:
            est["tipo"] = "estéreo"
        if b > 1.5:
            est["lado"] = "carga a la izquierda"
        elif b < -1.5:
            est["lado"] = "carga a la derecha"
    elif j["canales"] > 2:
        est["tipo"] = f"{j['canales']} canales"
    else:
        est["tipo"] = "mono"
    out["estereo"] = est

    # color
    bandas = (j["bandas"][suena] * ms[:, None]).sum(axis=0) / ms.sum()
    cent = float(np.median(j["centroide"][suena]))
    plan = float(np.median(j["planitud"][suena]))
    color = {"bandas_pct": {n: round(float(100 * v)) for (n, _, _), v in zip(BANDAS, bandas)},
             "centroide_hz": round(cent), "planitud": round(plan, 3)}
    color["palabras"] = _color(bandas, cent, plan)
    out["color"] = color
    out["palabras"] = _palabras(out)
    return out


def _eventos(t, marca):
    """Comienzos de los grupos de bloques marcados (separados por más de 1 s)."""
    import numpy as np
    i = np.flatnonzero(marca)
    if not len(i):
        return []
    out = [float(t[i[0]])]
    for a, b in zip(i, i[1:]):
        if t[b] - t[a] > 1.0:
            out.append(float(t[b]))
    return out


def _color(b, cent, plan):
    sub, graves, medios, presencia, agudos = (float(x) for x in b)
    p = []
    if sub > 0.35:
        p.append("muy grave")
    elif sub + graves > 0.55:
        p.append("grave")
    elif presencia + agudos > 0.45:
        p.append("agudo")
    elif medios > 0.5:
        p.append("de registro medio")
    else:
        p.append("de registro amplio")
    if cent < 500:
        p.append("opaco")
    elif cent > 3500:
        p.append("brillante")
    # planitud del espectro (calibrada con el show RAPIZUAM): pifilkas y sintes ~0,
    # cascabeles 0,001-0,005, kaskawillas 0,004-0,03; lo grave siempre se ve tonal
    if plan < 0.0005 and sub + graves < 0.5:
        p.append("tonal")
    elif plan > 0.004:
        p.append("ruidoso")
    return ", ".join(p)


def _palabras(f):
    t, n, pr, e = f["tiempo"], f["nivel"], f["problemas"], f["estereo"]
    partes = [f["color"]["palabras"]]
    if t["suena_pct"] >= 95:
        partes.append("suena todo el tramo")
    else:
        partes.append(f"suena el {t['suena_pct']} % (de {t['sonido_desde']} a {t['sonido_hasta']})")
    nivel = f"{n['lufs']} LUFS" if n.get("lufs") is not None else f"{n['rms_dbfs']} dBFS RMS"
    partes.append(f"{nivel}, pico {n['pico_dbfs']} dBFS")
    if n.get("rango_lu") is not None:
        partes.append("nivel parejo" if n["rango_lu"] < 4 else "bastante dinámico" if n["rango_lu"] > 12 else "")
    if "saturacion" in pr:
        s = pr["saturacion"]
        partes.append(f"satura {s['veces']} {'vez' if s['veces'] == 1 else 'veces'} (primera en {s['donde'][0]})")
    if "sobre_0dbfs" in pr:
        partes.append(f"con el clip gain pasa 0 dBFS (pico {pr['sobre_0dbfs']['pico_dbfs']}, primera en "
                      f"{pr['sobre_0dbfs']['donde'][0]})")
    if "corriente_continua" in pr:
        partes.append("tiene corriente continua")
    partes.append(e["tipo"] + (f", {e['lado']}" if e.get("lado") else ""))
    return "; ".join(p for p in partes if p)


# ------------------------------------------------------------------ archivo suelto

def ficha_archivo(ruta, desde_s=None, hasta_s=None, registro=None):
    """Ficha de un WAV suelto (o de un tramo). Calcula la serie si no está."""
    ruta = str(Path(ruta).resolve())
    alm = Almacen(carpeta_fichas(ruta))
    x = alm.serie([ruta])
    if x is None:
        alm.calcular([ruta], registro)
        alm.guardar_indice()
        x = alm.serie([ruta])
    s, m = x
    b0 = 0 if desde_s is None else int(desde_s / BLOQUE_S)
    b1 = m["bloques"] if hasta_s is None else int(math.ceil(hasta_s / BLOQUE_S))
    tr = Tramo()
    tr.agregar(s, m, b0, b1, 0.0, b0 * BLOQUE_S)
    j = tr.juntar()
    if j is None:
        raise ErrorFicha("el tramo pedido está fuera del archivo")
    out = {"archivo": ruta, "duracion_s": m["duracion_s"], "frecuencia": m["frecuencia"], **resumir(j)}
    return out


# ------------------------------------------------------------------ sesión

def _fuentes_sesion(sesion, comun):
    """{tupla de ids de archivo: [rutas]} de todos los clips de audio."""
    from .consolidar import rutas_de_sesion
    from .show import _ruta_audio
    rutas = rutas_de_sesion(sesion, comun)
    nombres = {a["id"]: a["ruta"] for a in comun["archivos"]}

    def ruta(i):
        return rutas.get(i) or _ruta_audio(sesion, nombres.get(i, str(i)))

    out = {}
    for p in comun["pistas"]:
        for c in p.get("clips") or []:
            if c.get("tipo") != "audio" or c.get("archivo") is None:
                continue
            ids = tuple(c.get("archivos_canales") or [c["archivo"]])
            if ids not in out:
                out[ids] = [ruta(i) for i in ids]
    return out


def calcular_sesion(sesion, recalcular=False, registro=print, procesos=None):
    """Calcula (o pone al día) las series de todas las fuentes de una sesión.
    Solo recalcula lo que falta o cambió, salvo con recalcular=True. Calcula
    varias fuentes a la vez (procesos: por defecto hasta 4)."""
    from .comun import leer_comun
    from . import notas
    import time
    sesion = str(Path(sesion).resolve())
    comun = leer_comun(sesion)
    fuentes = _fuentes_sesion(sesion, comun)
    alm = Almacen(carpeta_fichas(sesion))
    hechas, al_dia, faltan, errores = 0, 0, [], []
    t0 = time.time()
    pendientes = []
    for ids, rutas in fuentes.items():
        nombre = " + ".join(Path(r).name for r in rutas)
        if not all(Path(r).exists() for r in rutas):
            faltan.append(nombre)
        elif not recalcular and alm.vigente(rutas):
            al_dia += 1
        else:
            pendientes.append((nombre, rutas))
    registro(f"{len(fuentes)} fuentes: {al_dia} ya estaban, {len(faltan)} sin archivo, {len(pendientes)} por calcular")
    procesos = max(1, min(procesos or 4, (os.cpu_count() or 2) - 1, len(pendientes)))

    def listo(nombre, fut):
        nonlocal hechas
        try:
            alm.anotar(*fut.result())
            hechas += 1
            registro(f"{hechas + len(errores)}/{len(pendientes)} {nombre}")
            if hechas % 20 == 0:
                alm.guardar_indice()
        except Exception as e:                         # un archivo raro no frena el resto
            errores.append(f"{nombre}: {e}")
            registro(f"  error en {nombre}: {e}")

    if procesos == 1:
        from concurrent.futures import Future
        for nombre, rutas in pendientes:
            f = Future()
            try:
                f.set_result(_calcular_guardar(rutas, str(alm.carpeta)))
            except Exception as e:
                f.set_exception(e)
            listo(nombre, f)
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(procesos) as ex:
            futs = {ex.submit(_calcular_guardar, rutas, str(alm.carpeta)): nombre for nombre, rutas in pendientes}
            for f in as_completed(futs):
                listo(futs[f], f)
    alm.guardar_indice()
    res = {"fuentes": len(fuentes), "calculadas": hechas, "ya_estaban": al_dia, "faltan_archivos": faltan,
           "errores": errores, "carpeta": str(alm.carpeta), "segundos": round(time.time() - t0),
           "procesos": procesos}
    notas.anotar(sesion, f"fichas de sonido de {Path(sesion).name}: {hechas} calculadas, {al_dia} ya estaban"
                         + (f", {len(faltan)} archivos faltan" if faltan else "")
                         + (f", {len(errores)} con error" if errores else ""))
    return res


def _ventana_cancion(comun, cancion):
    from .vistas import _canciones, _normal
    canciones = _canciones(comun)
    nombres = [n for n, _ in canciones]
    cand = [n for n in nombres if _normal(cancion) in _normal(n)]
    if not cand:
        raise ErrorFicha(f"no hay un marcador que se llame como {cancion!r} (hay: {nombres})")
    k = nombres.index(cand[0])
    return cand[0], canciones[k][1], (canciones[k + 1][1] if k + 1 < len(canciones) else float("inf"))


class Sesion:
    """Fichas de las pistas de una sesión, a partir de las series guardadas."""

    def __init__(self, sesion, comun=None):
        from .comun import leer_comun
        self.sesion = str(Path(sesion).resolve())
        self.comun = comun or leer_comun(self.sesion)
        self.fuentes = _fuentes_sesion(self.sesion, self.comun)
        self.alm = Almacen(carpeta_fichas(self.sesion))
        self.reloj = Reloj(self.comun)
        self.fs = self.comun["unidades"].get("frecuencia_muestreo") or 48000

    def hay(self):
        return bool(self.alm.indice)

    def tramo(self, pista, rango=None):
        """(Tramo de la pista, clips sin serie). rango: (desde_s, hasta_s) de la sesión."""
        from .consolidar import _ganancia
        tr, sin = Tramo(), 0
        for c in pista.get("clips") or []:
            if c.get("tipo") != "audio" or c.get("archivo") is None or c.get("segundos") is None:
                continue
            ini = c["segundos"]
            fin = ini + c["largo_muestras"] / self.fs
            if rango and (fin <= rango[0] or ini >= rango[1]):
                continue
            x = self.alm.serie(self.fuentes[tuple(c.get("archivos_canales") or [c["archivo"]])])
            if x is None:
                sin += 1
                continue
            s, m = x
            n = m["muestras_bloque"]
            desde = int(c.get("desde_muestra") or 0)
            a, b = ini, fin
            if rango:
                a, b = max(a, rango[0]), min(b, rango[1])
            b0 = (desde + (a - ini) * self.fs) / n
            b1 = (desde + (b - ini) * self.fs) / n
            tr.agregar(s, m, math.floor(b0), math.ceil(b1), _ganancia(c), ini + (math.floor(b0) * n - desde) / self.fs)
        return tr, sin

    def ficha(self, nombre, cancion=None):
        p = next((x for x in self.comun["pistas"] if x["nombre"] == nombre), None)
        if p is None:
            raise ErrorFicha(f"no existe la pista {nombre!r}")
        rango, cn = None, None
        if cancion:
            cn, a, b = _ventana_cancion(self.comun, cancion)
            rango = (a, b)
        tr, sin = self.tramo(p, rango)
        j = tr.juntar()
        out = {"pista": nombre}
        if cn:
            out["cancion"] = cn
        if j is None:
            out["palabras"] = "sin ficha (calcular con fichas_calcular)" if sin else "sin clips de audio aquí"
            return out
        out.update(resumir(j, self.reloj))
        out["nota"] = "Con el clip gain; sin fader, plugins ni fundidos."
        if sin:
            out["clips_sin_ficha"] = sin
        return out

    def palabras(self, cancion=None, nombres=None):
        """{pista: línea en palabras} de las pistas de audio (en la canción, si se pide)."""
        rango = None
        if cancion:
            _, a, b = _ventana_cancion(self.comun, cancion)
            rango = (a, b)
        out = {}
        for p in self.comun["pistas"]:
            if p["tipo"] != "audio" or (nombres is not None and p["nombre"] not in nombres):
                continue
            tr, _ = self.tramo(p, rango)
            j = tr.juntar()
            if j is not None:
                out[p["nombre"]] = resumir(j, self.reloj)["palabras"]
        return out

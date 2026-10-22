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
espectro, muestras saturadas, cresta (de la envolvente de 10 ms), posibles
clics, cortes a cero, frecuencia dominante y, si hay dos canales, correlación
y balance. Aparte, los golpes (ataques) con su tiempo y nivel.

La ficha de un tramo trae:
- tiempo: largo, dónde empieza y termina el sonido de verdad, cuánto suena y
  los silencios largos;
- nivel: pico, RMS de lo que suena, sonoridad integrada (LUFS), rango de
  sonoridad y los momentos más fuertes;
- problemas: saturación en el archivo, picos sobre 0 dBFS por el clip gain,
  corriente continua, posibles clics, cortes a cero y bordes de clip sin
  fundido que pueden sonar a golpe;
- estéreo: mono, mono duplicado, casi mono, estéreo o abierto; lado, fase y
  cuánto pierde (o gana) al sumarse en mono;
- color: bandas, grave/medio/agudo, brillante u opaco, tonal o ruidoso;
- altura: la nota que más suena (si es tonal);
- articulación: golpes por segundo, regularidad y pulso, percusivo o
  sostenido;
- forma: la curva de nivel en 20 tramos, su palabra (constante, crescendo…)
  y dónde cambia el carácter;
- en palabras: una línea con lo anterior.
Los tiempos van en segundos (m:ss) y, si se mira desde una sesión, también en
compás|tiempo."""

import hashlib
import json
import math
import os
import re
from pathlib import Path

VERSION = 4
BLOQUE_S = 0.1
BANDAS = (("sub", 0, 60), ("graves", 60, 250), ("medios", 250, 2000), ("presencia", 2000, 6000),
          ("agudos", 6000, None))
SILENCIO_DB = -55.0          # RMS de bloque bajo esto (o 45 dB bajo el máximo) = no suena
SATURA = 0.999               # |muestra| desde aquí cuenta como tope
CAMPOS = ("rms", "pico", "k", "bandas", "centroide", "planitud", "satura", "cresta", "clics", "cortes",
          "fdom", "fprom", "dcb", "infra", "corr", "bal")
SUBBLOQUES = 10              # envolvente de 10 ms para golpes y cresta
NOTAS = ("Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si")


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
    tonal = (f >= 50) & (f <= 5000)
    i_tonal = np.flatnonzero(tonal)
    sub = n // SUBBLOQUES
    corte_min = max(int(fs * 0.001), 2)                                # 1 ms de ceros seguidos
    out = {k: [] for k in CAMPOS}
    golpes, cola, ultimo = [], np.full(3, -150.0), -1.0
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
        X = np.fft.rfft(mono * ventana, axis=1)
        P = np.abs(X) ** 2
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
        # envolvente de 10 ms: cresta del bloque y golpes (subida de más de 6 dB en 30 ms)
        e = (x[:, :sub * SUBBLOQUES, :] ** 2).reshape(k, SUBBLOQUES, sub, canales).mean(axis=(2, 3), dtype=np.float64)
        out["cresta"].append(10 * np.log10((e.max(axis=1) + 1e-15) / (e.mean(axis=1) + 1e-15)))
        ed = np.concatenate([cola, 10 * np.log10(e.ravel() + 1e-15)])
        previo = np.minimum(np.minimum(ed[:-3], ed[1:-2]), ed[2:-1])
        actual = ed[3:]
        for i in np.flatnonzero((actual - previo > 6) & (actual > -50)):
            tg = (b0 * SUBBLOQUES + i) * sub / fs
            if tg - ultimo >= 0.05:
                pico_g = actual[i:i + 3].max()
                despues = actual[min(i + 15, len(actual) - 1)]                  # 150 ms después
                golpes.append((tg, pico_g, min(pico_g - despues, 60.0)))                 # tope: 60 dB
                ultimo = tg
        cola = ed[-3:]
        # clics: un salto entre muestras grande y muy por sobre lo normal del bloque
        dx = np.abs(np.diff(x, axis=1))
        lim = np.maximum(0.25, 25 * dx.mean(axis=1, keepdims=True))
        out["clics"].append((dx > lim).sum(axis=(1, 2)))
        # cortes: 1 ms o más de ceros exactos en todos los canales
        z = np.concatenate([np.zeros((k, 1), np.int32), (x == 0).all(axis=2).cumsum(axis=1, dtype=np.int32)], axis=1)
        out["cortes"].append(((z[:, corte_min:] - z[:, :-corte_min]) == corte_min).any(axis=1).astype(np.int8))
        # frecuencia dominante (pico más alto entre 50 Hz y 5 kHz, afinado con una parábola)
        Pt = P[:, tonal] + 1e-30
        im = Pt.argmax(axis=1)
        lp = np.log(P + 1e-30)
        j0 = i_tonal[im]
        a, b_, c_ = lp[np.arange(k), j0 - 1], lp[np.arange(k), j0], lp[np.arange(k), np.minimum(j0 + 1, len(f) - 1)]
        den = a - 2 * b_ + c_
        delta = np.where(np.abs(den) > 1e-12, 0.5 * (a - c_) / np.where(den == 0, 1, den), 0)
        out["fdom"].append((j0 + np.clip(delta, -0.5, 0.5)) * fs / n)
        out["fprom"].append(10 * np.log10(Pt.max(axis=1) / Pt.mean(axis=1)))
        # continua por bloque (el soplo o el viento en el micrófono la mueven) e infrasonido
        out["dcb"].append(X[:, 0].real / ventana.sum())      # media con ventana: lo grave no se cuela
        out["infra"].append(P[:, f < 20].sum(axis=1) / tot)
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
    series["golpes"] = np.array(golpes, dtype=np.float32).reshape(-1, 3)
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
    """`trarin/fichas` junto a la sesión o al archivo (o la de la carpeta
    `trarin` que ya lo contiene, p. ej. para una escucha)."""
    from .notas import carpeta
    for padre in Path(ruta).resolve().parents:
        if padre.name == "trarin" and (padre / "notas.md").exists():
            return padre / "fichas"
    return carpeta(ruta) / "fichas"


# ------------------------------------------------------------------ tramos

class Tramo:
    """Bloques juntados de una o más fuentes (los clips de una pista), cada uno
    con su segundo en la sesión (o en el archivo) y el clip gain sumado."""

    def __init__(self):
        self.partes = []          # (series, meta, b0, b1, ganancia_db, t0)
        self.bordes = []          # (segundo, "inicio" | "fin") de clips que empiezan o terminan sin fundido

    def agregar(self, series, meta, b0, b1, ganancia_db=0.0, t0=0.0):
        b0, b1 = max(int(b0), 0), min(int(b1), meta["bloques"])
        if b1 > b0:
            self.partes.append((series, meta, b0, b1, ganancia_db, t0))

    def juntar(self):
        import numpy as np
        if not self.partes:
            return None
        cols, ts, gs = {}, [], []
        for s, m, b0, b1, g, t0 in self.partes:
            ts.append(t0 + np.arange(b1 - b0) * BLOQUE_S)
            if "golpes" in s and len(s["golpes"]):
                gp = s["golpes"].astype(np.float64)
                gp = gp[(gp[:, 0] >= b0 * BLOQUE_S) & (gp[:, 0] < b1 * BLOQUE_S)]
                gs.append(np.stack([t0 + gp[:, 0] - b0 * BLOQUE_S, gp[:, 1] + g, gp[:, 2]], axis=1))
            for c in CAMPOS:
                if c in s:
                    v = s[c][b0:b1].astype(np.float64)
                    if c in ("rms", "pico", "k"):
                        v = v + g
                    cols.setdefault(c, []).append(v)
                else:
                    cols.setdefault(c, []).append(np.full((b1 - b0,) + s["bandas"].shape[1:] if c == "bandas"
                                                          else b1 - b0, np.nan))
        out = {c: np.concatenate(v) for c, v in cols.items()}
        out["t"] = np.concatenate(ts)
        orden = np.argsort(out["t"], kind="stable")
        out = {c: v[orden] for c, v in out.items()}
        out["canales"] = max(m["canales"] for _, m, *_ in self.partes)
        out["dc"] = max((abs(x) for _, m, *_ in self.partes for x in m.get("dc") or [0]), default=0)
        g = np.concatenate(gs) if gs else np.zeros((0, 3))
        out["golpes"] = g[np.argsort(g[:, 0], kind="stable")]
        out["bordes"] = sorted(self.bordes)
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

    def compas_de(self, t):
        """Número de compás en el segundo t."""
        from .protools import tiempo as T
        return int(T.compas_tiempo(T.tick_de_segundos(t + 1e-6, self.mapa), self.compases).split("|")[0])

    def segundo_de_compas(self, num):
        """Segundo en que empieza el compás 'num'."""
        from .protools import tiempo as T
        c = [x for x in self.compases if x["compas"] <= num]
        c = c[-1] if c else self.compases[0]
        largo = T.TICKS_NEGRA * 4 // c["denominador"] * c["numerador"]
        return T.segundos(c["tick"] + (num - c["compas"]) * largo, self.mapa)


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
    dcb = np.abs(j["dcb"][suena])
    dc_fijo = float(j["dc"])
    dc_mov = float(np.percentile(dcb, 90)) if len(dcb) else 0.0
    if dc_fijo > 0.005 or dc_mov > 0.01:
        prob["continua"] = {"fija": round(dc_fijo, 4), "por_momentos_p90": round(dc_mov, 4),
                            "tipo": "fija (desplazamiento constante)" if dc_fijo > 0.005 and dc_mov < 2 * dc_fijo
                            else "variable (sube y baja: típico de soplo, viento o golpes de aire en el micrófono)"}
    infra = float((j["infra"][suena] * ms).sum() / ms.sum())
    if infra > 0.1:
        prob["infrasonido"] = {"pct": round(100 * infra),
                               "nota": "energía bajo 20 Hz, que no se oye pero gasta nivel y mueve los parlantes"}
    clics = j["clics"] * suena
    if clics.sum() > 0:
        eventos = _eventos(t, clics > 0)
        prob["posibles_clics"] = {"veces": len(eventos), "donde": [reloj(e) for e in eventos[:5]]}
    vecinos = np.zeros(len(t), bool)
    vecinos[1:-1] = suena[:-2] & suena[2:]
    cortes = (j["cortes"] > 0) & vecinos
    if cortes.any():
        eventos = _eventos(t, cortes)
        prob["cortes"] = {"veces": len(eventos), "donde": [reloj(e) for e in eventos[:5]]}
        if len(eventos) > 20:
            prob["cortes"]["nota"] = "frecuentes: probablemente una puerta (gate), un efecto o una edición a propósito"
    bordes = [(tb, q) for tb, q in j.get("bordes") or []]
    if bordes:
        prob["bordes_sin_fundido"] = {"veces": len(bordes),
                                      "donde": [f"{'inicio' if q == 'inicio' else 'final'} de clip en {reloj(tb)}"
                                                for tb, q in bordes[:5]]}
    out["problemas"] = prob

    # estéreo
    est = {"canales": j["canales"]}
    if j["canales"] == 2 and "corr" in j and np.isfinite(j["corr"][suena]).any():
        corr = j["corr"][suena]
        pesos = ms[np.isfinite(corr)]
        c = float(np.average(corr[np.isfinite(corr)], weights=pesos))
        b = float(np.average(j["bal"][suena][np.isfinite(corr)], weights=pesos))
        est.update({"correlacion": round(c, 2), "balance_db": round(b, 1)})
        ok = np.isfinite(j["corr"]) & suena
        r = 10 ** (j["bal"][ok] / 10)
        msb = 10 ** (rms[ok] / 10)
        L, R = 2 * msb * r / (1 + r), 2 * msb / (1 + r)
        cruz = (j["corr"][ok] * np.sqrt(L * R)).sum()
        est["suma_mono_db"] = round(float(10 * np.log10(max(1 + 2 * cruz / (L.sum() + R.sum()), 1e-6))), 1)
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
    out["altura"] = _altura(j, suena, ms) if "ruidoso" not in color["palabras"] else {}
    out["articulacion"] = _articulacion(j, suena, umbral, reloj)
    out["forma"] = _forma(j, suena, st, reloj)
    out["palabras"] = _palabras(out)
    return out


def _nota(fr):
    m = int(round(69 + 12 * math.log2(fr / 440)))
    return f"{NOTAS[m % 12]}{m // 12 - 1}"


def _altura(j, suena, ms):
    """La nota que más suena, contando los bloques con un pico claro (tonales)."""
    import numpy as np
    ok = (j["fprom"][suena] > 20) & (j["fdom"][suena] > 50)
    if ok.sum() < 5 or ok.mean() < 0.3:
        return {"tonal_pct": round(100 * float(ok.mean())) if len(ok) else 0}
    fr, w = j["fdom"][suena][ok], ms[ok]
    midi = np.round(69 + 12 * np.log2(fr / 440)).astype(int)
    pesos = {}
    for m_, w_ in zip(midi, w):
        pesos[m_] = pesos.get(m_, 0) + w_
    tot = sum(pesos.values())
    orden = sorted(pesos.items(), key=lambda x: -x[1])[:4]
    notas = [{"nota": f"{NOTAS[m_ % 12]}{m_ // 12 - 1}", "hz": round(float(440 * 2 ** ((m_ - 69) / 12)), 1),
              "pct": round(float(100 * w_ / tot))} for m_, w_ in orden]
    return {"tonal_pct": round(100 * float(ok.mean())), "notas": notas}


def _articulacion(j, suena, umbral, reloj=None):
    """Golpes por segundo, si es percusivo (el sonido cae rápido después de
    cada golpe) o sostenido, regularidad y, en una sesión, cuántos golpes caen
    en la grilla de semicorcheas del tempo."""
    import numpy as np
    t = j["t"]
    zona = np.convolve(suena.astype(float), np.ones(21), mode="same") > 0      # lo que suena ±1 s
    activo_s = float(zona.sum()) * BLOQUE_S
    g = j["golpes"]
    if len(g):
        idx = np.clip(np.searchsorted(t, g[:, 0], side="right") - 1, 0, len(t) - 1)
        g = g[suena[idx] & (g[:, 1] > umbral + 6)]
    out = {"golpes": int(len(g)), "golpes_por_s": round(len(g) / activo_s, 2) if activo_s else 0,
           "cresta_db": round(float(np.median(j["cresta"][suena])), 1)}
    caida = float(np.median(g[:, 2])) if len(g) >= 5 else 0.0
    out["caida_db"] = round(caida, 1)
    if len(g) < 5 or out["golpes_por_s"] < 0.2 or caida < 4:
        out["ataque"] = "sostenido"
    elif caida > 9:                       # calibrado: kultrun 10-12 dB, cascabeles 6-7, sintes < 4
        out["ataque"] = "percusivo"
    else:
        out["ataque"] = "mixto"
    if len(g) >= 8:
        ioi = np.diff(g[:, 0])
        ioi = ioi[(ioi > 0.06) & (ioi < 2.0)]
        if len(ioi) >= 6:
            med = float(np.median(ioi))
            cv = float(np.median(np.abs(ioi - med)) / med)          # dispersión robusta
            out["regularidad"] = "regular" if cv < 0.08 else "bastante regular" if cv < 0.2 else "irregular"
            if cv < 0.08:
                bpm = 60 / med
                while bpm < 70:
                    bpm *= 2
                while bpm >= 180:
                    bpm /= 2
                out["pulso_bpm"] = round(bpm)
        if reloj is not None and getattr(reloj, "comun", None):
            from .protools import tiempo as T
            semi = T.TICKS_NEGRA // 4
            dev = []
            for tg in g[:, 0]:
                tk = T.tick_de_segundos(float(tg), reloj.mapa)
                r = tk % semi
                dev.append(min(r, semi - r) / semi)
            dev = np.array(dev)
            out["en_grilla_pct"] = round(100 * float((dev < 0.15).mean()))     # al azar daría ~30 %
    return out


def _forma(j, suena, st, reloj):
    """Curva de nivel en 20 tramos (dB respecto del más fuerte), su palabra y
    los momentos donde cambia el carácter (nivel o brillo)."""
    import numpy as np
    t = j["t"]
    n = len(t)
    if n < 40:
        return {}
    cortes = np.linspace(0, n, 21).astype(int)
    kms = 10 ** (j["k"] / 10)
    niv = []
    for a, b in zip(cortes, cortes[1:]):
        x = kms[a:b][suena[a:b]]
        niv.append(-0.691 + 10 * math.log10(x.mean()) if len(x) and b - a and suena[a:b].mean() > 0.2 else None)
    vivos = [v for v in niv if v is not None]
    tope = max(vivos) if vivos else 0
    curva = [None if v is None else round(v - tope) for v in niv]
    out = {"curva_db": curva}
    pal = []
    huecos = sum(1 for v in niv if v is None)
    if len(vivos) >= 3:
        rango = max(vivos) - min(vivos)
        x = np.array([i for i, v in enumerate(niv) if v is not None], float)
        y = np.array(vivos)
        pend = float(np.polyfit(x, y, 1)[0]) * 20 if len(x) > 2 else 0
        tercio = len(vivos) // 3 or 1
        ini, fin = np.mean(vivos[:tercio]), np.mean(vivos[-tercio:])
        if huecos >= 5 and suena.mean() < 0.6:
            pal.append("intermitente")
        if rango < 4:
            pal.append("constante")
        elif pend > 6 and fin > ini + 4:
            pal.append("crescendo")
        elif pend < -6 and fin < ini - 4:
            pal.append("decrescendo")
        else:
            final = [v for v in niv[-3:] if v is not None]
            resto = [v for v in niv[:-3] if v is not None]
            if final and resto:
                if np.mean(final) > np.mean(resto) + 4:
                    pal.append("crece al final")
                elif np.mean(final) < np.mean(resto) - 6:
                    pal.append("se apaga al final")
            if not pal:
                pal.append("con subidas y bajadas")
    out["palabra"] = ", ".join(pal)
    # cambios de carácter: ventanas de 5 s comparadas con la anterior
    v = 50
    cambios = []
    if n >= 3 * v:
        prev = None
        for a in range(0, n - v + 1, v):
            sel = suena[a:a + v]
            if sel.mean() < 0.5:
                prev = None
                continue
            nivel = -0.691 + 10 * math.log10(kms[a:a + v][sel].mean())
            brillo = math.log2(float(np.median(j["centroide"][a:a + v][sel])) + 1)
            if prev:
                d_niv, d_bri = nivel - prev[0], brillo - prev[1]
                que = []
                if abs(d_niv) > 6:
                    que.append(f"{'sube' if d_niv > 0 else 'baja'} {abs(d_niv):.0f} dB")
                if abs(d_bri) > 0.8:
                    que.append("más brillante" if d_bri > 0 else "más opaco")
                if que:
                    cambios.append({"en": reloj(float(t[a])), "cambio": ", ".join(que)})
            prev = (nivel, brillo)
    if cambios:
        out["cambios"] = cambios[:6]
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
    """Una línea: color y ataque; golpes; nota; cuándo suena; forma; nivel;
    problemas; estéreo."""
    t, n, pr, e = f["tiempo"], f["nivel"], f["problemas"], f["estereo"]
    a, h, fo = f.get("articulacion") or {}, f.get("altura") or {}, f.get("forma") or {}
    partes = [f["color"]["palabras"] + (f", {a['ataque']}" if a.get("ataque") else "")]
    if a.get("ataque") in ("percusivo", "mixto") and a.get("golpes_por_s"):
        g = f"{a['golpes_por_s']} golpes/s"
        if a.get("regularidad"):
            g += f", {a['regularidad']}" + (f" (~{a['pulso_bpm']} bpm)" if a.get("pulso_bpm") else "")
        if a.get("en_grilla_pct") is not None:
            p_ = a["en_grilla_pct"]
            g += (", cuadra con la grilla" if p_ >= 70 else ", cuadra a medias con la grilla" if p_ >= 45
                  else ", suelto respecto de la grilla") + f" ({p_} % de los golpes)"
        partes.append(g)
    if h.get("notas"):
        partes.append(f"nota más presente {h['notas'][0]['nota']}")
    if t["suena_pct"] >= 95:
        partes.append("suena todo el tramo")
    else:
        partes.append(f"suena el {t['suena_pct']} % (de {t['sonido_desde']} a {t['sonido_hasta']})")
    if fo.get("palabra"):
        partes.append(fo["palabra"])
    nivel = f"{n['lufs']} LUFS" if n.get("lufs") is not None else f"{n['rms_dbfs']} dBFS RMS"
    partes.append(f"{nivel}, pico {n['pico_dbfs']} dBFS")
    if "saturacion" in pr:
        s = pr["saturacion"]
        partes.append(f"satura {s['veces']} {'vez' if s['veces'] == 1 else 'veces'} (primera en {s['donde'][0]})")
    if "sobre_0dbfs" in pr:
        partes.append(f"con el clip gain pasa 0 dBFS (pico {pr['sobre_0dbfs']['pico_dbfs']}, primera en "
                      f"{pr['sobre_0dbfs']['donde'][0]})")
    if "continua" in pr:
        partes.append("continua " + ("fija" if pr["continua"]["tipo"].startswith("fija") else "variable"))
    if "infrasonido" in pr:
        partes.append(f"{pr['infrasonido']['pct']} % de infrasonido")
    if "posibles_clics" in pr:
        partes.append(f"{pr['posibles_clics']['veces']} posibles clics (primero en {pr['posibles_clics']['donde'][0]})")
    if "cortes" in pr:
        partes.append("cortes a cero frecuentes (¿puerta o efecto?)" if pr["cortes"].get("nota")
                      else f"{pr['cortes']['veces']} cortes a cero (primero en {pr['cortes']['donde'][0]})")
    if "bordes_sin_fundido" in pr:
        partes.append(f"{pr['bordes_sin_fundido']['veces']} bordes de clip sin fundido")
    est = e["tipo"] + (f", {e['lado']}" if e.get("lado") else "")
    if e.get("suma_mono_db") is not None and e["suma_mono_db"] <= -1:
        est += f", pierde {-e['suma_mono_db']} dB en mono"
    partes.append(est)
    return "; ".join(x for x in partes if x)


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
        bordes_pista = [(c["segundos"], c["segundos"] + c["largo_muestras"] / self.fs)
                        for c in pista.get("clips") or [] if c.get("segundos") is not None and c.get("largo_muestras")]
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
            g = _ganancia(c)
            tr.agregar(s, m, math.floor(b0), math.ceil(b1), g, ini + (math.floor(b0) * n - desde) / self.fs)
            # bordes que pueden sonar a golpe: el clip empieza o termina con sonido, sin fundido
            # y sin otro clip pegado
            tipos = {fd.get("tipo") for fd in c.get("fundidos") or []}
            rms = s["rms"]
            i0, i1 = int(desde / n), int((desde + c["largo_muestras"]) / n) - 1
            if a == ini and "entrada" not in tipos and 0 <= i0 < len(rms) and rms[i0] + g > -40 \
                    and not any(abs(f_ - ini) < 0.002 for _, f_ in bordes_pista):
                tr.bordes.append((ini, "inicio"))
            if b == fin and "salida" not in tipos and 0 <= i1 < len(rms) and rms[i1] + g > -40 \
                    and not any(abs(i_ - fin) < 0.002 for i_, _ in bordes_pista):
                tr.bordes.append((fin, "fin"))
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

    # -------------------------------------------------------------- canción

    def _pistas_en(self, rango):
        """[(pista, tramo juntado, umbral de sonido)] de las pistas de audio con
        sonido en el rango."""
        out = []
        for p in self.comun["pistas"]:
            if p["tipo"] != "audio":
                continue
            j = self.tramo(p, rango)[0].juntar()
            if j is None or not len(j["rms"]):
                continue
            umbral = max(SILENCIO_DB, float(j["rms"].max()) - 45)
            if (j["rms"] > umbral).any():
                out.append((p, j, umbral))
        return out

    @staticmethod
    def _en_ventana(j, umbral, ta, tb):
        """(fracción del tiempo que suena, potencia K media) de la pista entre ta y tb."""
        sel = (j["t"] >= ta) & (j["t"] < tb)
        if not sel.any():
            return 0.0, 0.0
        suena = j["rms"][sel] > umbral
        frac = float(suena.sum()) * BLOQUE_S / max(tb - ta, BLOQUE_S)
        return frac, float((10 ** (j["k"][sel] / 10)).mean())

    def mapa(self, cancion, compases_por_tramo=4):
        """La canción por tramos de N compases: qué pistas suenan, cuáles entran
        y salen, sonoridad aproximada de la suma; los tramos iguales seguidos se
        juntan en secciones y se proponen los lugares donde cambia mucho."""
        cn, a, b = _ventana_cancion(self.comun, cancion)
        pistas = self._pistas_en((a, b))
        if not pistas:
            raise ErrorFicha(f"no hay audio con ficha en {cn} (¿falta fichas_calcular?)")
        b = min(b, max(float(j["t"][-1]) + BLOQUE_S for _, j, _ in pistas))
        primero = self.reloj.compas_de(a)
        N = max(int(compases_por_tramo), 1)
        tramos, k = [], primero
        while True:
            ta, tb = max(self.reloj.segundo_de_compas(k), a), min(self.reloj.segundo_de_compas(k + N), b)
            if ta >= b:
                break
            suenan = []
            total = 0.0
            for p, j, u in pistas:
                frac, pot = self._en_ventana(j, u, ta, tb)
                total += pot
                if frac > 0.3:
                    suenan.append((pot, p["nombre"]))
            tramos.append({"k": k, "ta": ta, "tb": tb, "suenan": [n for _, n in sorted(suenan, reverse=True)],
                           "lufs": round(-0.691 + 10 * math.log10(total), 1) if total > 0 else None})
            k += N
        secciones = []
        for tr in tramos:
            if secciones and set(secciones[-1]["suenan"]) == set(tr["suenan"]):
                s_ = secciones[-1]
                s_["hasta_k"], s_["tb"] = tr["k"] + N - 1, tr["tb"]
                s_["niveles"].append(tr["lufs"])
            else:
                secciones.append({**tr, "hasta_k": tr["k"] + N - 1, "niveles": [tr["lufs"]]})
        out, previo, propuestas = [], set(), []
        for i, s_ in enumerate(secciones):
            actual = set(s_["suenan"])
            entran = [n for n in s_["suenan"] if n not in previo]
            salen = sorted(previo - actual)
            niv = [x for x in s_["niveles"] if x is not None]
            lufs = round(sum(niv) / len(niv), 1) if niv else None
            fila = {"compases": f"{s_['k']}–{s_['hasta_k']}",
                    "en_cancion": f"{s_['k'] - primero + 1}–{s_['hasta_k'] - primero + 1}",
                    "desde": self.reloj(s_["ta"]), "suenan": s_["suenan"], "lufs": lufs}
            if i:
                fila["entran"], fila["salen"] = entran, salen
                cambio_nivel = (lufs is not None and out[-1]["lufs"] is not None and abs(lufs - out[-1]["lufs"]) >= 6)
                if len(entran) + len(salen) >= 2 or cambio_nivel:
                    propuestas.append({"compas": s_["k"], "en_cancion": s_["k"] - primero + 1,
                                       "por": (f"entran {len(entran)}, salen {len(salen)}"
                                               + (f", nivel {lufs - out[-1]['lufs']:+.0f} dB" if cambio_nivel else ""))})
            out.append(fila)
            previo = actual
        texto = []
        for f_ in out:
            linea = f"c. {f_['compases']} (compás {f_['en_cancion']} de la canción, {f_['desde'].split(' ')[0]})"
            linea += f": {len(f_['suenan'])} pistas"
            if f_.get("lufs") is not None:
                linea += f", {f_['lufs']} LUFS"
            if f_.get("entran"):
                linea += "; entran " + ", ".join(f_["entran"])
            if f_.get("salen"):
                linea += "; salen " + ", ".join(f_["salen"])
            if "entran" not in f_:
                linea += ("; suenan " + ", ".join(f_["suenan"])) if f_["suenan"] else "; silencio"
            texto.append(linea)
        return {"cancion": cn, "primer_compas": primero, "compases_por_tramo": N, "secciones": out,
                "posibles_secciones": propuestas, "texto": texto,
                "nota": "Suma aproximada: con el clip gain, sin fader, plugins ni fundidos."}

    def que_suena(self, compas=None, segundo=None, cancion=None):
        """Las pistas que suenan en un compás (de la sesión o, con 'cancion', contado
        desde el inicio de la canción) o en el compás que contiene 'segundo', de la
        más fuerte a la más suave, con su color en ese momento."""
        import numpy as np
        primero = None
        if cancion:
            cn, a, _ = _ventana_cancion(self.comun, cancion)
            primero = self.reloj.compas_de(a)
        if compas is not None:
            num = (primero + compas - 1) if primero is not None else compas
        elif segundo is not None:
            num = self.reloj.compas_de(segundo)
        else:
            raise ErrorFicha("indicar compas o segundo")
        ta, tb = self.reloj.segundo_de_compas(num), self.reloj.segundo_de_compas(num + 1)
        filas = []
        for p, j, u in self._pistas_en((ta, tb)):
            frac, pot = self._en_ventana(j, u, ta, tb)
            if frac < 0.2:
                continue
            sel = (j["t"] >= ta) & (j["t"] < tb) & (j["rms"] > u)
            ms = 10 ** (j["rms"][sel] / 10)
            bandas = (j["bandas"][sel] * ms[:, None]).sum(axis=0) / ms.sum()
            color = _color(bandas, float(np.median(j["centroide"][sel])), float(np.median(j["planitud"][sel])))
            filas.append({"pista": p["nombre"], "lufs": round(-0.691 + 10 * math.log10(pot), 1),
                          "suena_pct": round(100 * min(frac, 1)), "color": color})
        filas.sort(key=lambda x: -x["lufs"])
        out = {"compas": num, "desde": self.reloj(ta), "hasta": self.reloj(tb), "suenan": filas}
        if primero is not None:
            out["cancion"], out["en_cancion"] = cn, num - primero + 1
        return out

    # -------------------------------------------------------------- mezcla y niveles

    def ganancia_mezcla(self, pista, t):
        """dB que suma la mezcla de la pista en los segundos t: fader (o su
        automatización de volumen, interpolada entre puntos) y mute (fijo o
        automatizado; en mute: -150 dB)."""
        import numpy as np
        m = pista.get("mezcla") or {}
        auto = pista.get("automatizacion") or {}
        t = np.asarray(t, dtype=float)
        vol = auto.get("volumen_db") or []
        if len(vol) > 1 or (vol and len({p["valor"] for p in vol}) > 1):
            xs = np.array([p["muestra"] for p in vol], float) / self.fs
            g = np.interp(t, xs, np.array([p["valor"] for p in vol], float))
        else:
            g = np.full(len(t), float(m.get("volumen_db") or 0.0))
        mute = auto.get("mute") or []
        if mute:
            xs = np.array([p["muestra"] for p in mute], float) / self.fs
            k = np.searchsorted(xs, t, side="right") - 1
            v = np.array([p["valor"] for p in mute], float)
            g = np.where(v[np.clip(k, 0, len(v) - 1)] > 0.5, -150.0, g)
        elif m.get("mute"):
            g = np.full(len(t), -150.0)
        return g

    def con_mezcla(self, pista, j):
        """El tramo juntado de la pista con la mezcla sumada (ver ganancia_mezcla)."""
        g = self.ganancia_mezcla(pista, j["t"])
        j = dict(j)
        for c in ("rms", "pico", "k"):
            j[c] = j[c] + g
        if len(j["golpes"]):
            gg = self.ganancia_mezcla(pista, j["golpes"][:, 0])
            j["golpes"] = j["golpes"].copy()
            j["golpes"][:, 1] += gg
        return j

    def revisar_niveles(self, cancion=None):
        """Picos sobre 0 dBFS por pista con clip gain, fader y su automatización
        (no ve plugins ni la suma en buses) y la sonoridad aproximada de cada
        canción con la mezcla (suma de las pistas sin mute)."""
        import numpy as np
        rango = None
        if cancion:
            _, a, b = _ventana_cancion(self.comun, cancion)
            rango = (a, b)
        pistas = []
        for p in self.comun["pistas"]:
            if p["tipo"] != "audio":
                continue
            j = self.tramo(p, rango)[0].juntar()
            if j is None:
                continue
            jm = self.con_mezcla(p, j)
            sobre = jm["pico"] > 0.05
            if sobre.any():
                eventos = _eventos(jm["t"], sobre)
                solo_clip = float(j["pico"].max()) > 0.05
                fader = float(self.ganancia_mezcla(p, jm["t"][sobre]).max())
                causa = ("clip gain" if solo_clip and fader <= 0.05 else "fader" if not solo_clip
                         else "clip gain y fader")
                pistas.append({"pista": p["nombre"], "pico_dbfs": round(float(jm["pico"].max()), 1),
                               "veces": len(eventos), "donde": [self.reloj(e) for e in eventos[:5]],
                               "causa": causa, "fader_db_max": round(fader, 1)})
        pistas.sort(key=lambda x: -x["pico_dbfs"])
        canciones = []
        nombres = [n for n, _ in _canciones_de(self.comun)] if not cancion else [cancion]
        for n in nombres:
            try:
                cn, a, b = _ventana_cancion(self.comun, n)
            except ErrorFicha:
                continue
            total = None
            for p in self.comun["pistas"]:
                if p["tipo"] != "audio":
                    continue
                j = self.tramo(p, (a, b))[0].juntar()
                if j is None:
                    continue
                jm = self.con_mezcla(p, j)
                paso = np.floor((jm["t"] - a) / BLOQUE_S).astype(int)
                ok = paso >= 0
                pot = 10 ** (jm["k"][ok] / 10)
                if total is None or paso[ok].max(initial=0) >= len(total):
                    largo = int(max(paso[ok].max(initial=0) + 1, 0 if total is None else len(total)))
                    nuevo = np.zeros(largo)
                    if total is not None:
                        nuevo[:len(total)] = total
                    total = nuevo
                np.add.at(total, paso[ok], pot)
            if total is None or not (total > 0).any():
                continue
            corto = np.convolve(total, np.ones(4) / 4, mode="same")
            lu = -0.691 + 10 * np.log10(corto + 1e-15)
            g = lu > -70
            rel = -0.691 + 10 * np.log10(corto[g].mean()) - 10
            g2 = lu > max(-70, rel)
            st = -0.691 + 10 * np.log10(np.convolve(total, np.ones(30) / 30, mode="same") + 1e-15)
            canciones.append({"cancion": cn, "lufs": round(float(-0.691 + 10 * np.log10(corto[g2].mean())), 1),
                              "lufs_3s_max": round(float(st.max()), 1)})
        return {"sobre_0dbfs": pistas, "canciones": canciones,
                "nota": "Con clip gain, fader, su automatización y mute; sin plugins, buses ni fader master. "
                        "La sonoridad de la canción es la suma aproximada de las pistas."}

    def marcadores_de_secciones(self, cancion, compases=None, compases_por_tramo=4):
        """[(nombre, tick)] de los marcadores de sección de una canción: en los
        compases pedidos (contados desde el inicio de la canción) o en los que
        propone el mapa. Se llaman «<canción> B», «<canción> C»… (la canción ya
        tiene su marcador)."""
        from .protools import tiempo as T
        cn, a, _ = _ventana_cancion(self.comun, cancion)
        primero = self.reloj.compas_de(a)
        if compases is None:
            compases = [x["en_cancion"] for x in self.mapa(cancion, compases_por_tramo)["posibles_secciones"]]
        out = []
        for i, k in enumerate(sorted(set(int(x) for x in compases if int(x) > 1))):
            seg = self.reloj.segundo_de_compas(primero + k - 1)
            letra = chr(ord("B") + i) if i < 25 else f"Z{i - 24}"
            out.append((f"{cn} {letra}", T.tick_de_segundos(seg + 1e-6, self.reloj.mapa), primero + k - 1, k))
        return out


def _canciones_de(comun):
    from .vistas import _canciones
    return _canciones(comun)


def escribir_marcadores(sesion, salida, cancion, compases=None, compases_por_tramo=4):
    """Escribe 'salida' (sesión nueva) con los marcadores de sección de la
    canción agregados. Devuelve la lista de marcadores puestos."""
    from .comun import aplicar
    ses = Sesion(sesion)
    nuevos = ses.marcadores_de_secciones(cancion, compases, compases_por_tramo)
    if not nuevos:
        raise ErrorFicha("no hay secciones que marcar")
    comun = ses.comun
    existentes = {(m["nombre"], m["tick"]) for m in comun["marcadores"]}
    for nombre, tick, _, _ in nuevos:
        if (nombre, tick) not in existentes:
            comun["marcadores"].append({"nombre": nombre, "tick": tick})
    aplicar(comun, ses.sesion, str(salida))
    return [{"marcador": n, "compas": c, "en_cancion": k, "en": ses.reloj(ses.reloj.mapa.segundos(t))}
            for n, t, c, k in nuevos]

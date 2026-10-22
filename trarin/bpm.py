"""Detector de tempo: calcula el bpm de un audio (mejor una pista de percusión:
bombo, kultrun) a partir de sus golpes.

1. Envolvente de golpes: flujo espectral (aumento de energía por banda) de la
   señal en mono, a ~86 cuadros por segundo.
2. Candidatos: autocorrelación de la envolvente entre 'minimo' y 'maximo' bpm.
3. Afinado: para cada candidato se prueba una grilla fina de bpm y fases; el
   puntaje es la energía media de la envolvente en los pulsos de la grilla.
4. Por tramos: el mismo afinado en ventanas, para ver si el tempo cambia.

Requiere numpy y soxr (pip install "trarin[audio]")."""

from . import audio

FS_ANALISIS = 11025
HOP = 128
VENTANA = 1024
LATENCIA_S = 0.077      # retraso de la envolvente respecto del golpe (medido con clics)


def envolvente(ruta, desde_s=0.0, hasta_s=None):
    """(envolvente de golpes normalizada, cuadros por segundo)."""
    np = audio._np()
    i = audio.info(ruta)
    fs = i["frecuencia"]
    desde = int(desde_s * fs)
    cuantas = None if hasta_s is None else int((hasta_s - desde_s) * fs)
    x, _ = audio.leer(ruta, desde, cuantas)
    x = audio.remuestrear(x.mean(axis=1), fs, FS_ANALISIS, calidad="HQ")
    n = 1 + max(0, (len(x) - VENTANA) // HOP)
    if n < 8:
        raise audio.ErrorAudio(f"{ruta}: audio demasiado corto para medir el tempo")
    idx = np.arange(VENTANA)[None, :] + HOP * np.arange(n)[:, None]
    cuadros = x[idx] * np.hanning(VENTANA)[None, :]
    mag = np.log1p(1000 * np.abs(np.fft.rfft(cuadros, axis=1)))
    flujo = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    fps = FS_ANALISIS / HOP
    # quitar la media local (~1 s) y normalizar
    k = int(fps)
    media = np.convolve(flujo, np.ones(k) / k, mode="same")
    e = np.maximum(flujo - media, 0)
    if e.max() > 0:
        e = e / e.max()
    return np.concatenate([[0.0], e]), fps


def _autocorrelacion(e):
    np = audio._np()
    n = len(e)
    f = np.fft.rfft(e - e.mean(), 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    return ac / ac[0] if ac[0] > 0 else ac


def _puntaje(e, fps, bpm, paso_fase=0.5):
    """(puntaje, fase en cuadros) de una grilla de pulsos a 'bpm' sobre e."""
    np = audio._np()
    p = 60.0 * fps / bpm
    fases = np.arange(0, p, paso_fase)
    k = np.arange(int((len(e) - 1) / p))
    if len(k) < 2:
        return 0.0, 0.0
    pos = fases[:, None] + k[None, :] * p
    pos = pos[:, :] if pos[-1, -1] < len(e) - 1 else pos[:, :-1]
    v = np.interp(pos, np.arange(len(e)), e).mean(axis=1)
    j = int(v.argmax())
    return float(v[j]), float(fases[j])


def _afinar(e, fps, centro, ancho=0.03, paso=0.01):
    np = audio._np()
    mejor = (-1.0, centro, 0.0)
    for b in np.arange(centro * (1 - ancho), centro * (1 + ancho) + paso, paso):
        s, f = _puntaje(e, fps, b)
        if s > mejor[0]:
            mejor = (s, float(b), f)
    return mejor


def medir(ruta, minimo=30.0, maximo=240.0, desde_s=0.0, hasta_s=None, tramo_s=30.0, candidatos=6,
          umbral_pulso=0.7, maximo_pulso=200.0):
    """Mide el tempo de un audio. Devuelve:
    {'bpm', 'primer_pulso_s', 'confianza', 'pulsos_por_compas' (si se ve un acento
     cada 2, 3 o 4 pulsos), 'candidatos': [{'bpm', 'puntaje'}],
     'tramos': [{'desde_s', 'hasta_s', 'bpm', 'puntaje'}], 'duracion_s'}.
    'confianza' compara el puntaje del bpm con el de una grilla al azar (1 = nada)."""
    np = audio._np()
    e, fps = envolvente(ruta, desde_s, hasta_s)
    ac = _autocorrelacion(e)
    lags = np.arange(int(60 * fps / maximo), min(int(60 * fps / minimo) + 1, len(ac) - 1))
    v = ac[lags]
    picos = [i for i in range(1, len(v) - 1) if v[i] >= v[i - 1] and v[i] >= v[i + 1] and v[i] > 0]
    picos = sorted(picos, key=lambda i: -v[i])[: candidatos * 2]
    vistos = []
    for i in picos:
        b = 60 * fps / lags[i]
        if all(abs(b - x) / x > 0.02 for x in vistos):
            vistos.append(b)
    res = []
    for b in vistos[:candidatos]:
        s, b2, f = _afinar(e, fps, b)
        res.append({"bpm": round(b2, 2), "puntaje": round(s, 4), "_fase": f})
    if not res:
        raise audio.ErrorAudio(f"{ruta}: no se encontraron pulsos")
    res.sort(key=lambda r: -r["puntaje"])
    base = float(e.mean()) or 1e-9
    # el pulso: el candidato más rápido que puntúa casi como el mejor (el mejor
    # suele ser el acento del compás, que cae en menos golpes pero más fuertes)
    tope = res[0]["puntaje"] * umbral_pulso
    mejor = max((r for r in res if r["puntaje"] >= tope and r["bpm"] <= maximo_pulso),
                key=lambda r: r["bpm"], default=res[0])
    compas = None
    for k in (4, 3, 2):
        c = next((r for r in res if abs(r["bpm"] * k - mejor["bpm"]) / mejor["bpm"] < 0.01), None)
        if c and (compas is None or c["puntaje"] > compas[1]):
            compas = (k, c["puntaje"])
    out = {"archivo": str(ruta), "bpm": mejor["bpm"],
           "primer_pulso_s": round(desde_s + (mejor["_fase"] / fps + LATENCIA_S) % (60.0 / mejor["bpm"]), 4),
           "confianza": round(mejor["puntaje"] / base, 2),
           "pulsos_por_compas": compas[0] if compas else None,
           "candidatos": [{k: r[k] for k in ("bpm", "puntaje")} for r in res],
           "duracion_s": round(len(e) / fps, 2), "tramos": []}
    # tramos: el tempo afinado alrededor del general (±15 %)
    n = int(tramo_s * fps)
    if tramo_s and len(e) > 2 * n:
        for a in range(0, len(e) - n // 2, n):
            seg = e[a:a + n]
            if seg.max() <= 0 or len(seg) < n // 2:
                continue
            s, b, _ = _afinar(seg, fps, mejor["bpm"], ancho=0.15, paso=0.05)
            s2, b2, _ = _afinar(seg, fps, b, ancho=0.005, paso=0.01)
            out["tramos"].append({"desde_s": round(desde_s + a / fps, 1),
                                  "hasta_s": round(desde_s + min(a + n, len(e)) / fps, 1),
                                  "bpm": round(b2, 2), "puntaje": round(s2, 4)})
    return out


def texto(r):
    l = [f"{r['archivo']}",
         f"  bpm {r['bpm']}  (primer pulso en {r['primer_pulso_s']} s, confianza {r['confianza']}"
         + (f", acento cada {r['pulsos_por_compas']} pulsos" if r.get("pulsos_por_compas") else "") + ")",
         "  candidatos: " + ", ".join(f"{c['bpm']} ({c['puntaje']})" for c in r["candidatos"])]
    if r["tramos"]:
        l.append("  por tramos: " + ", ".join(f"{t['desde_s']:.0f}-{t['hasta_s']:.0f}s {t['bpm']}"
                                             for t in r["tramos"]))
    return "\n".join(l)

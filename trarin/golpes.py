"""Golpes de percusión de una pista de audio → notas MIDI.

Pensado para instrumentos de ataque claro (kultrun, bombo): cada golpe es una
nota, para disparar un sampler o una luz. Las sacudidas continuas (kaskawillas,
semillas) dan golpes falsos.

Detección: nivel en ventanas de 5 ms cada 1 ms; hay golpe donde el nivel sube más
de 'subida_db' respecto del mínimo de los 10 ms anteriores, sobre un piso de
'piso_db' bajo el máximo del tramo, y al menos 'separacion_ms' después del
golpe anterior. La fuerza es el pico de los 40 ms siguientes; la velocidad la
reparte en 'rango_db' bajo el golpe más fuerte del tramo (127 = el más fuerte).
El largo es lo que suena el golpe: hasta que cae 'caida_db' bajo su pico, sin
pasar del golpe siguiente.

Las notas se cuantizan a la grilla ('1/16' por omisión, según el mapa de tempo
de la sesión; 'no' para dejar el tiempo exacto). Con fase 'auto' (por omisión)
la grilla se corre lo que calce mejor con los golpes: en un show la grilla de
una canción puede no coincidir con la de la sesión (Katrümongen: 36 ms). Dos
golpes en el mismo lugar de la grilla quedan en uno (el más fuerte). El plan
dice qué parte de los golpes queda lejos de la grilla, y con cuál calzarían mejor.

Probado con el kultrun de Katrümongen (show RAPIZUAM): las 576 notas del MIDI
Bombo, que está alineado a mano, tienen un golpe detectado a menos de 50 ms
(mediana −4 ms antes de cuantizar)."""
import struct

from . import audio as A
from .consolidar import _ganancia, rutas_de_sesion
from .protools import tiempo as T


class ErrorGolpes(Exception):
    pass


GRILLAS = {"1/4": 1, "1/8": 2, "1/16": 4, "1/32": 8, "1/8t": 3, "1/16t": 6}


def _np():
    return A._np()


def senal(comun, rutas, pista, a, b, fs, canal=None):
    """Mezcla mono de los clips de audio no muteados de la pista entre a y b
    segundos, con su clip gain. canal (desde 0): solo ese canal de una pista
    estéreo (p. ej. dos pifilkas paneadas)."""
    np = _np()
    p = next((x for x in comun["pistas"] if x["nombre"] == pista), None)
    if p is None or p["tipo"] != "audio":
        raise ErrorGolpes(f"{pista!r} no es una pista de audio de la sesión")
    n = int((b - a) * fs)
    y = np.zeros(n)
    for k in p["clips"]:
        if k.get("tipo") != "audio" or k.get("mute"):
            continue
        i0 = k["muestra"] - int(round(a * fs))
        i1 = i0 + k["largo_muestras"]
        if i1 <= 0 or i0 >= n:
            continue
        desde = k.get("desde_muestra", 0) + max(0, -i0)
        cuantas = min(i1, n) - max(i0, 0)
        archivos = k.get("archivos_canales") or [k["archivo"]]
        if canal is not None and len(archivos) > 1:
            archivos = [archivos[min(canal, len(archivos) - 1)]]
        x = 0
        for i in archivos:
            r = rutas.get(i)
            if not r:
                raise ErrorGolpes(f"no se encuentra el audio de un clip de {pista!r}")
            s, _ = A.leer(r, desde, cuantas)
            if canal is not None and s.shape[1] > 1:
                x = x + s[:, min(canal, s.shape[1] - 1)]
            else:
                x = x + s.mean(axis=1)
        x = x / len(archivos) * 10 ** (_ganancia(k) / 20)
        y[max(i0, 0):max(i0, 0) + len(x)] += x
    return y


def detectar(y, fs, subida_db=9.0, separacion_ms=60.0, piso_db=-36.0, caida_db=24.0, rango_db=36.0,
             rearme_db=6.0):
    """Golpes de la señal y: [{"t" (s desde el inicio de y), "db", "largo" (s), "velocidad"}]."""
    np = _np()
    h = max(1, int(fs / 1000))                 # 1 ms
    w = max(1, int(fs * 5 / 1000))             # ventana de 5 ms
    n = len(y) // h
    if n < 20:
        return []
    y2 = y[:n * h] ** 2
    c = np.concatenate([[0.0], np.cumsum(y2)])
    idx = np.arange(n) * h
    e = 10 * np.log10((c[np.minimum(idx + w, len(y2))] - c[idx]) / w + 1e-12)
    vmax = float(e.max())
    minimo = np.full(n, np.inf)
    for j in range(1, 11):
        minimo[j:] = np.minimum(minimo[j:], e[:-j])
    cand = np.where((e - minimo > subida_db) & (e > vmax + piso_db))[0]
    sep = int(separacion_ms)
    out, i = [], 0
    while i < len(cand):
        j0 = int(cand[i])
        pico_i = j0 + int(np.argmax(e[j0:j0 + 40]))
        pico = float(e[pico_i])
        fin, lim = pico_i, min(n, j0 + 4000)
        while fin < lim and e[fin] > pico - caida_db:
            fin += 1
        out.append({"t": j0 * h / fs, "db": pico, "largo": (fin - j0) * h / fs})
        # rearme: el golpe siguiente necesita que este haya bajado 'rearme_db' bajo su
        # pico (o que suba más fuerte): el cuerpo áspero de un golpe largo (un bombo
        # legüero) tiene subidas propias que no son golpes
        bajo = np.where(e[pico_i:lim] < pico - rearme_db)[0]
        rearmado = pico_i + int(bajo[0]) if len(bajo) else lim
        i = int(np.searchsorted(cand, j0 + sep))
        while i < len(cand) and cand[i] < rearmado and e[cand[i]] <= pico + 3:
            i += 1
    for g1, g2 in zip(out, out[1:]):
        g1["largo"] = min(g1["largo"], g2["t"] - g1["t"])
    dbmax = max((g["db"] for g in out), default=0.0)
    for g in out:
        g["velocidad"] = int(min(127, max(1, round(127 + (g["db"] - dbmax) * 126 / rango_db))))
    return out


def separar_redobles(golpes, intervalo_ms=120.0, minimo=3):
    """(golpes sueltos, golpes de redoble): un redoble es una racha de al menos
    'minimo' golpes seguidos a menos de 'intervalo_ms' uno de otro (en el bombo
    de Sombra en el Desierto, unos 13 por segundo)."""
    sueltos, redobles, racha = [], [], []

    def cerrar():
        (redobles if len(racha) >= minimo else sueltos).extend(racha)
    for g in golpes:
        if racha and (g["t"] - racha[-1]["t"]) * 1000 > intervalo_ms:
            cerrar()
            racha = []
        racha.append(g)
    cerrar()
    return sueltos, redobles


def _grilla_ticks(grilla):
    if grilla in (None, "", "no"):
        return None
    if grilla not in GRILLAS:
        raise ErrorGolpes(f"grilla desconocida {grilla!r}: {', '.join(GRILLAS)} o 'no'")
    return T.TICKS_NEGRA // GRILLAS[grilla]


def fase_de(ticks, paso):
    """Corrimiento (ticks, entre −paso/2 y paso/2) de la grilla que mejor calza con
    los golpes: promedio circular de su resto respecto de la grilla."""
    import cmath
    import math
    if not ticks or not paso:
        return 0
    z = sum(cmath.exp(2j * math.pi * (t % paso) / paso) for t in ticks)
    return int(round(cmath.phase(z) / (2 * math.pi) * paso))


def a_notas(golpes, mapa, grilla="1/16", nota=36, fase="auto"):
    """Golpes (t en segundos de la sesión) → notas con tick absoluto, cuantizadas.
    fase: "sesion" (la grilla de los compases de la sesión), "auto" (la grilla
    corrida para calzar con los golpes: en un show la grilla de una canción puede
    no coincidir con la de la sesión) o un corrimiento en ticks."""
    paso = _grilla_ticks(grilla)
    crudos = [(g, T.tick_de_segundos(g["t"], mapa)) for g in golpes]
    if fase == "auto":
        f = fase_de([t for _, t in crudos], paso)
    elif fase in ("sesion", None):
        f = 0
    else:
        f = int(fase)
    por_tick = {}
    for g, t0 in crudos:
        t1 = T.tick_de_segundos(g["t"] + g["largo"], mapa)
        q = int(round((t0 - f) / paso) * paso + f) if paso else int(round(t0))
        x = {"tick": q, "fin": max(t1, q + 1), "nota": int(nota), "velocidad": g["velocidad"], "segundos": g["t"]}
        if q not in por_tick or por_tick[q]["velocidad"] < x["velocidad"]:
            por_tick[q] = x
    notas = [por_tick[k] for k in sorted(por_tick)]
    for a, b in zip(notas, notas[1:]):
        a["fin"] = min(a["fin"], b["tick"])
    minimo = (paso or T.TICKS_NEGRA // 4) // 4
    for x in notas:
        x["largo_ticks"] = max(minimo, x.pop("fin") - x["tick"])
    for a, b in zip(notas, notas[1:]):           # que no se pisen tras el largo mínimo
        a["largo_ticks"] = max(1, min(a["largo_ticks"], b["tick"] - a["tick"]))
    return notas, f


def planear(sesion, pista, cancion=None, desde_compas=None, hasta_compas=None, desde_s=None, hasta_s=None,
            nota=36, grilla="1/16", fase="auto", **ajustes):
    """Detecta los golpes de 'pista' en el tramo pedido y los pasa a notas.
    Devuelve el plan (no escribe nada)."""
    from .comun import leer_comun
    from .escucha import _rango
    from .fichas import Reloj
    comun = leer_comun(sesion)
    fs = comun["unidades"].get("frecuencia_muestreo") or 48000

    class _S:
        pass
    s = _S()
    s.comun, s.fs, s.reloj = comun, fs, Reloj(comun)
    a, b, nombre = _rango(s, cancion, desde_compas, hasta_compas, desde_s, hasta_s)
    y = senal(comun, rutas_de_sesion(sesion, comun), pista, a, b, fs)
    golpes = detectar(y, fs, **ajustes)
    for g in golpes:
        g["t"] += a
    mapa = T.MapaTempo(comun["tempo"])
    notas, f = a_notas(golpes, mapa, grilla, nota, fase)
    corrimiento = [abs(T.segundos(x["tick"], mapa) - x["segundos"]) for x in notas]

    def lejos(g):
        """Fracción de golpes a más de un cuarto de paso de la grilla g (con su fase)."""
        paso = _grilla_ticks(g)
        ticks = [T.tick_de_segundos(x["t"], mapa) for x in golpes]
        if not paso or not ticks:
            return 0.0
        fg = fase_de(ticks, paso) if fase == "auto" else (0 if fase in ("sesion", None) else int(fase))
        return round(sum(1 for t in ticks if abs((t - fg + paso / 2) % paso - paso / 2) > paso / 4) / len(ticks), 3)
    otras = {g: lejos(g) for g in ("1/8", "1/16", "1/32", "1/8t", "1/16t")} if grilla not in (None, "", "no") else {}
    return {"sesion": sesion, "pista": pista, "tramo": nombre, "desde_s": round(a, 3), "hasta_s": round(b, 3),
            "grilla": grilla, "nota": nota,
            "fase_ms": round((T.segundos(T.tick_de_segundos(a, mapa) + f, mapa) - a) * 1000, 1) if notas else 0, "golpes": len(golpes), "notas": notas,
            "corrimiento_max_ms": round(max(corrimiento, default=0) * 1000, 1),
            "juntados": len(golpes) - len(notas),
            "lejos_de_la_grilla": otras.get(grilla, 0.0),
            "lejos_por_grilla": otras,
            "velocidad": [min((x["velocidad"] for x in notas), default=0), max((x["velocidad"] for x in notas), default=0)],
            "ejemplos": [f"{s.reloj(x['segundos'])} vel {x['velocidad']} largo {T.segundos(x['tick'] + x['largo_ticks'], mapa) - T.segundos(x['tick'], mapa):.3f} s"
                         for x in notas[:8]]}


def texto(plan):
    aviso = ""
    if plan.get("lejos_de_la_grilla", 0) > 0.1:
        mejor = min(plan["lejos_por_grilla"], key=plan["lejos_por_grilla"].get)
        aviso = (f"\n  ojo: {plan['lejos_de_la_grilla']:.0%} de los golpes cae lejos de la grilla {plan['grilla']} "
                 f"({plan['juntados']} se juntaron con otro)")
        if mejor != plan["grilla"] and plan["lejos_por_grilla"][mejor] < plan["lejos_de_la_grilla"] - 0.05:
            aviso += f"; con {mejor} serían {plan['lejos_por_grilla'][mejor]:.0%}"
        if plan["lejos_por_grilla"][mejor] > 0.25:
            aviso += "; el audio no sigue bien la grilla de la sesión: probar sin cuantizar (grilla 'no')"
    return (f"{plan['pista']}, {plan['tramo']}: {plan['golpes']} golpes → {len(plan['notas'])} notas "
            f"(nota {plan['nota']}, grilla {plan['grilla']}, velocidades {plan['velocidad'][0]}–{plan['velocidad'][1]}, "
            f"grilla corrida {plan['fase_ms']} ms; la cuantización mueve hasta {plan['corrimiento_max_ms']} ms)" + aviso + "\n" + "\n".join("  " + e for e in plan["ejemplos"]))


def publico(plan):
    return {k: v for k, v in plan.items() if k != "notas"} | {"cantidad_notas": len(plan["notas"])}


def escribir(plan, salida, destino=None):
    """Escribe 'salida' (un .ptx nuevo) con las notas en la pista MIDI 'destino':
    si no existe se crea (por omisión «<pista> MIDI»); si existe, no debe tener
    clips en el tramo. Un solo clip desde el compás del primer golpe."""
    from .comun import aplicar, leer_comun
    notas = plan["notas"]
    if not notas:
        raise ErrorGolpes("no hay golpes en el tramo")
    comun = leer_comun(plan["sesion"])
    destino = destino or f"{plan['pista']} MIDI"
    mapa = T.MapaTempo(comun["tempo"])
    compases = sorted(comun.get("compases") or [{"tick": 0, "numerador": 4, "denominador": 4}], key=lambda c: c["tick"])
    c = [x for x in compases if x["tick"] <= notas[0]["tick"]][-1] if compases[0]["tick"] <= notas[0]["tick"] else compases[0]
    largo_c = T.TICKS_NEGRA * 4 // c["denominador"] * c["numerador"]
    inicio = c["tick"] + (notas[0]["tick"] - c["tick"]) // largo_c * largo_c
    fin = max(x["tick"] + x["largo_ticks"] for x in notas)
    clip = {"tipo": "midi", "nombre": f"{destino} {plan['tramo']}", "tick": int(inicio), "largo_ticks": int(fin - inicio),
            "notas": [{"tick": x["tick"] - inicio, "largo_ticks": x["largo_ticks"], "nota": x["nota"],
                       "velocidad": x["velocidad"]} for x in notas]}
    p = next((x for x in comun["pistas"] if x["nombre"] == destino), None)
    if p is None:
        comun["pistas"].append({"id": destino, "nombre": destino, "tipo": "midi", "clips": [clip]})
    else:
        if p["tipo"] != "midi":
            raise ErrorGolpes(f"{destino!r} existe y no es una pista MIDI")
        for k in p["clips"]:
            if k.get("tipo") == "midi" and k["tick"] < fin and k["tick"] + k.get("largo_ticks", 0) > inicio:
                raise ErrorGolpes(f"{destino!r} ya tiene clips MIDI en ese tramo")
        p["clips"].append(clip)
    aplicar(comun, plan["sesion"], salida)
    return {"pista": destino, "clip_desde_s": round(T.segundos(inicio, mapa), 3), "notas": len(notas)}


def escribir_mid(plan, ruta, tempo):
    """Archivo .mid nuevo (formato 0, 960 por negra, canal 10) con el tempo de la
    sesión y las notas; empieza en la negra del primer golpe."""
    notas = plan["notas"]
    if not notas:
        raise ErrorGolpes("no hay golpes en el tramo")
    div = T.TICKS_NEGRA // 960
    inicio = notas[0]["tick"] // T.TICKS_NEGRA * T.TICKS_NEGRA
    eventos = []
    vig = [t for t in tempo if t["tick"] <= inicio]
    eventos.append((0, 0, b"\xff\x51\x03" + int(round(60_000_000 / (vig[-1] if vig else tempo[0])["bpm"])).to_bytes(3, "big")))
    for t in tempo:
        if inicio < t["tick"] <= notas[-1]["tick"] + notas[-1]["largo_ticks"]:
            eventos.append(((t["tick"] - inicio) // div, 0, b"\xff\x51\x03" + int(round(60_000_000 / t["bpm"])).to_bytes(3, "big")))
    for x in notas:
        a = (x["tick"] - inicio) // div
        b = max(a + 1, (x["tick"] + x["largo_ticks"] - inicio) // div)
        eventos.append((a, 2, bytes([0x99, x["nota"], x["velocidad"]])))
        eventos.append((b, 1, bytes([0x89, x["nota"], 0])))
    eventos.sort(key=lambda e: (e[0], e[1]))

    def vlq(v):
        o = [v & 0x7F]
        v >>= 7
        while v:
            o.append(0x80 | (v & 0x7F))
            v >>= 7
        return bytes(reversed(o))
    pista, ant = b"", 0
    for t, _, ev in eventos:
        pista += vlq(t - ant) + ev
        ant = t
    pista += b"\x00\xff\x2f\x00"
    with open(ruta, "xb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, 960) + b"MTrk" + struct.pack(">I", len(pista)) + pista)
    return {"archivo": str(ruta), "notas": len(notas)}

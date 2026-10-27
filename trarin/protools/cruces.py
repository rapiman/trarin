"""Fundidos cruzados en los clips de audio pegados de una o varias pistas .ptx.

Una unión es el lugar donde un clip termina y el siguiente empieza, en el mismo
canal de la pista, pegados o con un hueco o una superposición de hasta
'tolerancia_ms'. Un hueco se cierra corriendo el clip de después hasta que
toque al anterior (no se estira ni se recorta ningún clip).

El cruce va centrado en la unión: la mitad antes (con el audio que el clip de
después tiene antes de su inicio en el archivo) y la mitad después (con el que
el de antes tiene después de su fin). Si un lado no alcanza, se corre al otro;
si entre los dos no llegan a 'minimo_ms', la unión se salta.

Se saltan y se informan: uniones que ya tienen un fundido (entrada, salida o
cruzado) y huecos que no se pueden cerrar (el clip de después tiene un cruce
al otro lado, o no está igual en todos los canales). Los clips muteados se
cruzan igual. En una pista estéreo, si los canales tienen los cortes en
lugares distintos, cada canal lleva sus propios cruces.

Los cruces se agregan como colocaciones de fundido sin tocar las de los clips,
así que un clip que ya tiene un cruce en el otro extremo no es problema.

Curvas (las de Pro Tools): 'potencia' = Equal Power con su curva preset; 'lineal'
= Equal Gain con curvas estándar (el crossfade por omisión de Pro Tools)."""
from .bloques import arbol
from .descifrado import leer_descifrado
from . import pistas as P
from .escritura import _colocaciones, escribir_cambios
from .sesion import _frecuencia

CURVAS = {
    "potencia": {"pendiente": "potencia", "curva_salida": {"forma": "preset", "preset": -1},
                 "curva_entrada": {"forma": "preset", "preset": -1}},
    "lineal": {"pendiente": "ganancia", "curva_salida": {"forma": "estandar"},
               "curva_entrada": {"forma": "estandar"}},
}


class ErrorCruces(Exception):
    pass


def planear(d, pistas=None, largo_ms=20.0, tolerancia_ms=1.0, minimo_ms=5.0, curva="potencia"):
    """d: sesión descifrada. pistas: nombres de pistas de audio (None = todas).
    Devuelve {"frecuencia", "uniones": [...]}; cada unión: pista, canal (desde 1,
    o None si es igual en todos), muestra (corte), hueco (muestras; > 0 hueco,
    < 0 superposición), accion ("cruzar" o "saltar"), motivo, y para cruzar
    'fundido' (listo para escribir) y 'mover' (muestras que se corre el clip de
    después, 0 si ya tocaba)."""
    if curva not in CURVAS:
        raise ErrorCruces(f"curva desconocida {curva!r}: 'potencia' o 'lineal'")
    b = arbol(d)
    fs = _frecuencia(d, b) or 48000
    regiones = P.leer_regiones_audio(d, b)
    archivos = P.leer_archivos_audio(d, b)
    audio = [p["nombre"] for p in P.leer_lista_pistas(d, b) if p["tipo"] == "audio"]
    if pistas is None:
        pistas = audio
    faltan = [p for p in pistas if p not in audio]
    if faltan:
        raise ErrorCruces(f"no son pistas de audio de la sesión: {', '.join(faltan)}")
    largo = max(2, round(largo_ms * fs / 1000))
    tol = round(tolerancia_ms * fs / 1000)
    minimo = round(minimo_ms * fs / 1000)
    uniones = []
    for pista in pistas:
        uniones += _pista(d, b, pista, regiones, archivos, fs, largo, tol, minimo, curva)
    return {"frecuencia": fs, "largo_muestras": largo, "curva": curva, "uniones": uniones}


def _largo_archivo(regiones, archivos, r):
    reg = regiones[r["indice"]]
    a = reg.get("archivo")
    if a is None or not 0 <= a < len(archivos) or archivos[a].get("muestras") is None:
        return None
    return archivos[a]["muestras"]


def _pista(d, b, pista, regiones, archivos, fs, largo, tol, minimo, curva):
    canales = _colocaciones(d, b, pista)
    n = len(canales)
    base = canales[0][0]
    # uniones por canal: clave (inicio y largo de los dos clips) → (r1, r2, fundidos del canal)
    por_canal = []
    for regs, fun in canales:
        u = {}
        for r1, r2 in zip(regs, regs[1:]):
            hueco = r2["muestra"] - (r1["muestra"] + r1["largo"])
            if abs(hueco) <= tol:
                u[(r1["muestra"], r1["largo"], r2["muestra"], r2["largo"])] = (r1, r2, fun)
        por_canal.append(u)
    claves = sorted(set().union(*por_canal), key=lambda k: k[2])
    out, corrido = [], {}            # corrido: inicio original del clip → muestras que se corrió
    for k in claves:
        en = [c for c in range(n) if k in por_canal[c]]
        todos = len(en) == n
        for c in ([None] if todos else en):
            r1, r2, fun = por_canal[0 if c is None else c][k]
            fila = _union(pista, c, r1, r2, fun, canales, base, regiones, archivos, fs, largo, tol, minimo,
                          curva, todos, corrido)
            out.append(fila)
    return out


def _union(pista, c, r1, r2, fun, canales, base, regiones, archivos, fs, largo, tol, minimo, curva, todos,
           corrido):
    fin1 = r1["muestra"] + corrido.get(r1["muestra"], 0) + r1["largo"]
    ini2 = r2["muestra"]
    hueco = ini2 - fin1
    fila = {"pista": pista, "canal": None if c is None else c + 1, "muestra": fin1 if hueco > 0 else ini2,
            "segundos": round((fin1 if hueco > 0 else ini2) / fs, 3), "hueco": hueco}

    def saltar(motivo):
        fila.update(accion="saltar", motivo=motivo)
        return fila

    if abs(hueco) > tol:
        return saltar(f"hueco de {hueco / fs * 1000:.1f} ms tras correr el clip anterior")
    # fundidos que ya tocan la unión (en los canales en juego)
    zona = (min(fin1, ini2) - 1, max(fin1, ini2) + 1)
    fundidos = [f for cc, (_, fu) in enumerate(canales) if c is None or cc == c for f in fu]
    if any(f["inicio"] <= zona[1] and f["fin"] >= zona[0] for f in fundidos):
        return saltar("ya tiene un fundido")
    mover = 0
    if hueco > 0:
        if not todos or not any(x["muestra"] == r2["muestra"] and x["largo"] == r2["largo"] for x in base):
            return saltar("hay un hueco y el clip de después no está igual en todos los canales")
        a, z = r2["muestra"], r2["muestra"] + r2["largo"]
        if any(f["tipo"] == "cruzado" and f["inicio"] < z and f["fin"] > a for f in fundidos):
            return saltar("hay un hueco y el clip de después tiene un cruce al otro lado")
        mover = -hueco
    p = fin1 if hueco > 0 else ini2
    l1, l2 = _largo_archivo(regiones, archivos, r1), _largo_archivo(regiones, archivos, r2)
    if l1 is None or l2 is None:
        return saltar("no se conoce el largo del archivo de audio")
    reg1, reg2 = regiones[r1["indice"]], regiones[r2["indice"]]
    despues = l1 - (reg1["desde_muestra"] + (p - (r1["muestra"] + corrido.get(r1["muestra"], 0))))
    antes = reg2["desde_muestra"] - (ini2 + mover - p)
    pre = max(0, min(largo // 2, antes))
    post = max(0, min(largo - pre, despues))
    pre = max(0, min(largo - post, antes))
    if pre + post < minimo:
        return saltar(f"los clips no tienen audio sobrante para cruzar ({(pre + post) / fs * 1000:.1f} ms)")
    if mover:
        corrido[r2["muestra"]] = mover
    f = {"tipo": "cruzado", "muestra": p - pre, "muestras": pre + post, "pre_roll": pre, **CURVAS[curva]}
    if c is not None:
        f["canal"] = c
    fila.update(accion="cruzar", motivo="", mover=mover, antes_ms=round(pre / fs * 1000, 1),
                despues_ms=round(post / fs * 1000, 1), fundido=f,
                _clip=(r2["muestra"], r2["largo"]))
    return fila


def escribir(sesion, salida, plan):
    """Escribe 'salida' (nueva) con los cruces del plan. Devuelve cuántos."""
    d = leer_descifrado(sesion)
    b = arbol(d)
    fundidos, mover = {}, {}
    for u in plan["uniones"]:
        if u["accion"] != "cruzar":
            continue
        fundidos.setdefault(u["pista"], []).append(u["fundido"])
        if u["mover"]:
            base = _colocaciones(d, b, u["pista"])[0][0]
            j = next(i for i, x in enumerate(base) if (x["muestra"], x["largo"]) == tuple(u["_clip"]))
            mover.setdefault(u["pista"], {})[j] = {"muestra": base[j]["muestra"] + u["mover"]}
    if not fundidos:
        raise ErrorCruces("no hay uniones para cruzar")
    escribir_cambios(sesion, salida, regiones=mover, fundidos_nuevos=fundidos)
    return sum(len(v) for v in fundidos.values())


def verificar(salida, plan):
    """Relee 'salida' y comprueba que cada cruce del plan esté (por canal).
    Devuelve la lista de faltantes."""
    d = leer_descifrado(salida)
    b = arbol(d)
    faltan = []
    cache = {}
    for u in plan["uniones"]:
        if u["accion"] != "cruzar":
            continue
        if u["pista"] not in cache:
            cache[u["pista"]] = _colocaciones(d, b, u["pista"])
        f = u["fundido"]
        canales = range(len(cache[u["pista"]])) if f.get("canal") is None else [f["canal"]]
        for c in canales:
            fu = cache[u["pista"]][c][1]
            if not any(x["tipo"] == "cruzado" and x["inicio"] == f["muestra"] and x["fin"] == f["muestra"] + f["muestras"]
                       for x in fu):
                faltan.append(f"{u['pista']} canal {c + 1} en {u['segundos']} s")
    return faltan


def texto(plan):
    """Resumen legible: cuántas uniones se cruzan y se saltan, y el detalle de
    lo que se cruza o se salta por un motivo distinto de 'ya tiene un fundido'."""
    fs = plan["frecuencia"]

    def reloj(s):
        return f"{int(s // 60)}:{s % 60:06.3f}"
    us = plan["uniones"]
    cruzar = [u for u in us if u["accion"] == "cruzar"]
    ya = sum(1 for u in us if u["accion"] == "saltar" and u["motivo"] == "ya tiene un fundido")
    lineas = [f"{len(cruzar)} uniones para cruzar ({plan['largo_muestras'] / fs * 1000:.0f} ms, {plan['curva']}); "
              f"{ya} ya tienen un fundido"]
    for u in us:
        if u["accion"] == "saltar" and u["motivo"] == "ya tiene un fundido":
            continue
        canal = f" canal {u['canal']}" if u["canal"] else ""
        if u["accion"] == "cruzar":
            extra = f" (se corre el clip {u['mover'] / fs * 1000:+.2f} ms)" if u["mover"] else ""
            lineas.append(f"  cruzar  {u['pista']}{canal} en {reloj(u['segundos'])}: "
                          f"{u['antes_ms']} ms antes y {u['despues_ms']} ms después{extra}")
        else:
            lineas.append(f"  saltar  {u['pista']}{canal} en {reloj(u['segundos'])}: {u['motivo']}")
    return "\n".join(lineas)


def publico(plan):
    """El plan sin los campos internos, para mostrar."""
    return {**{k: v for k, v in plan.items() if k != "uniones"},
            "uniones": [{k: v for k, v in u.items() if k not in ("fundido", "_clip")} for u in plan["uniones"]]}

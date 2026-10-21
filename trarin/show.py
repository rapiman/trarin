"""Armador de shows: plan de tiempo para juntar varias sesiones en una.

El show se describe en un JSON (o YAML, con pyyaml):

    {
      "salida": "D:/Grabaciones/Pro tool/Show/show.ptx",
      "base": "D:/.../Base.ptx",                 # opcional; si no, la primera canción
      "separacion": {"compases": 2},             # o {"segundos": 4}; puede ser negativa
      "cambio_tempo": {"modo": "antes", "negras": 2},   # o {"modo": "mitad"}
      "canciones": [
        {"sesion": "D:/.../Cancion1.ptx"},
        {"sesion": "D:/.../Cancion2.ptx", "separacion": {"compases": 4}, "fin_compas": 120}
      ]
    }

Reglas del plan (decididas con quien usa trarin):

* Cada canción empieza al inicio de un compás del show: el primer compás
  después de que termina la anterior (su último clip, o 'fin_compas'), más la
  separación. Una separación en compases usa el último compás de la canción
  anterior; una en segundos se redondea hacia arriba a compases.
* Tempo en la separación positiva: el de la canción que termina hasta el punto
  de cambio, y el de la que empieza desde ahí. Punto de cambio: 'antes' (por
  defecto una blanca, 2 negras, antes del inicio de la siguiente, sin entrar en
  la canción anterior) o 'mitad' (mitad de la separación).
* Separación negativa (canciones encimadas): el tempo cambia en la mitad de la
  zona encimada. El tempo y los compases de la canción que termina se cortan
  ahí, y los de la que empieza rigen desde ahí.
* Todo se ubica por tiempo real: cada evento de una canción va al instante
  (inicio de la canción en el show + segundos dentro de la canción). Así el
  audio y el MIDI suenan igual que en su sesión aunque cambie el tempo del show
  en una zona encimada. Donde el tempo del show coincide con el de la canción,
  la posición en ticks se traslada exacta (sin redondeos).
"""

import json
import math
from pathlib import Path

from .protools import tiempo as T

NEGRA = T.TICKS_NEGRA


class ErrorShow(Exception):
    pass


def cargar(ruta):
    texto = Path(ruta).read_text(encoding="utf-8-sig")   # acepta el BOM de Windows
    if str(ruta).lower().endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError as e:
            raise ErrorShow("un show en YAML requiere pyyaml (pip install pyyaml); en JSON no") from e
        return yaml.safe_load(texto)
    return json.loads(texto)


def _largo_compas(c):
    return NEGRA * 4 * c["numerador"] // c["denominador"]


def _fin_cancion(comun):
    """Tick donde termina el material de la canción (último clip o marcador)."""
    fin = 0
    for p in comun["pistas"]:
        for c in p["clips"]:
            if c.get("tick") is not None and c.get("largo_ticks"):
                fin = max(fin, c["tick"] + c["largo_ticks"])
    for m in comun.get("marcadores", []):
        fin = max(fin, m.get("fin_tick") or m["tick"])
    return fin


def _vigente(eventos, tick):
    v = eventos[0]
    for e in eventos:
        if e["tick"] <= tick:
            v = e
    return v


def _compas_desde(compases, numero):
    """Tick donde empieza el compás 'numero' (1 = el primero)."""
    restantes, previo = numero - 1, compases[0]
    for c in compases[1:]:
        n = (c["tick"] - previo["tick"]) // _largo_compas(previo)
        if restantes < n:
            break
        restantes -= n
        previo = c
    return previo["tick"] + restantes * _largo_compas(previo)


def _separacion(sep, compas, tempo_bpm):
    """Separación en ticks, múltiplo del compás vigente (puede ser negativa)."""
    largo = _largo_compas(compas)
    if "compases" in sep:
        return round(float(sep["compases"])) * largo
    if "segundos" in sep:
        s = float(sep["segundos"])
        n = math.ceil(abs(s) * tempo_bpm / 60.0 * NEGRA / largo)
        return int(math.copysign(n, s)) * largo if s else 0
    raise ErrorShow(f"separación no reconocida: {sep} (use 'compases' o 'segundos')")


def planear(show, leer_comun=None):
    """Plan de tiempo del show: inicio de cada canción, mapa de tempo, compases y
    marcadores, en ticks del show. 'leer_comun' permite pasar sesiones ya leídas
    (para pruebas); por defecto se leen con el modelo común."""
    if leer_comun is None:
        from .comun import leer_comun
    canciones = show.get("canciones") or []
    if len(canciones) < 1:
        raise ErrorShow("el show no tiene canciones")
    sep_def = show.get("separacion", {"compases": 2})
    cambio = show.get("cambio_tempo", {"modo": "antes", "negras": 2})
    if cambio.get("modo") not in ("antes", "mitad"):
        raise ErrorShow("cambio_tempo.modo debe ser 'antes' o 'mitad'")
    temas = []
    for i, c in enumerate(canciones):
        comun = leer_comun(c["sesion"]) if isinstance(c.get("sesion"), str) else c["sesion"]
        tempo = [{"tick": e["tick"], "bpm": e["bpm"]} for e in comun["tempo"]] or [{"tick": 0, "bpm": 120.0}]
        compases = comun["compases"] or [{"tick": 0, "numerador": 4, "denominador": 4}]
        if compases[0]["tick"] != 0 or tempo[0]["tick"] != 0:
            raise ErrorShow(f"{c['sesion']}: el tempo y el compás deben empezar en el tick 0")
        fin = _compas_desde(compases, int(c["fin_compas"])) if c.get("fin_compas") else _fin_cancion(comun)
        ultimo = _vigente(compases, fin)
        fin_compas = ultimo["tick"] + math.ceil((fin - ultimo["tick"]) / _largo_compas(ultimo)) * _largo_compas(ultimo)
        temas.append({"comun": comun, "nombre": Path(str(c.get("sesion"))).stem if isinstance(c.get("sesion"), str)
                      else comun["origen"].get("archivo", f"cancion{i + 1}"),
                      "tempo": tempo, "compases": compases, "fin": fin, "fin_compas": fin_compas,
                      "frecuencia": comun["unidades"]["frecuencia_muestreo"]})
    fs = {t["frecuencia"] for t in temas}
    if len(fs) > 1:
        raise ErrorShow(f"frecuencias de muestreo distintas: {sorted(fs)}")

    # inicio de cada canción (ticks del show) y punto de cambio de tempo
    inicios, cortes = [0], []
    for i in range(1, len(temas)):
        a, b = temas[i - 1], temas[i]
        sep = canciones[i].get("separacion", sep_def)
        ultimo = _vigente(a["compases"], a["fin_compas"] - 1)
        g = _separacion(sep, ultimo, _vigente(a["tempo"], a["fin"])["bpm"])
        inicio = inicios[-1] + a["fin_compas"] + g
        if inicio <= inicios[-1]:
            raise ErrorShow(f"la separación negativa antes de {b['nombre']} es más larga que {a['nombre']}")
        fin_a = inicios[-1] + a["fin"]
        if inicio >= fin_a:                       # separación positiva
            if cambio["modo"] == "mitad":
                corte = (fin_a + inicio) // 2
            else:
                corte = max(fin_a, inicio - int(float(cambio.get("negras", 2)) * NEGRA))
        else:                                     # canciones encimadas
            corte = (inicio + fin_a) // 2
        inicios.append(inicio)
        cortes.append(corte)

    # mapa de tempo y compases del show
    tempo, compases = [], []
    for i, t in enumerate(temas):
        desde = cortes[i - 1] if i else 0               # rige el tempo de esta canción
        hasta = cortes[i] if i < len(cortes) else None
        s = inicios[i]
        tempo.append({"tick": desde, "bpm": _vigente(t["tempo"], max(desde - s, 0))["bpm"]})
        tempo += [{"tick": s + e["tick"], "bpm": e["bpm"]} for e in t["tempo"]
                  if s + e["tick"] > desde and (hasta is None or s + e["tick"] < hasta)]
        # compases: desde el inicio de la canción (o el corte, si se encima), hasta el inicio de la siguiente
        fin_c = inicios[i + 1] if i + 1 < len(inicios) else None
        for e in t["compases"]:
            k = s + e["tick"]
            if fin_c is None or k < fin_c:
                compases.append({"tick": k, "numerador": e["numerador"], "denominador": e["denominador"]})
    tempo = _limpiar(tempo, ("bpm",))
    compases = _limpiar(sorted(compases, key=lambda x: x["tick"]), ("numerador", "denominador"))
    _verificar_compases(compases, inicios[1:])

    plan = {"frecuencia_muestreo": temas[0]["frecuencia"], "tempo": tempo, "compases": compases,
            "canciones": [], "marcadores": []}
    for i, t in enumerate(temas):
        s = inicios[i]
        ini_s = T.segundos(s, tempo)
        plan["canciones"].append({
            "nombre": t["nombre"], "sesion": canciones[i].get("sesion") if isinstance(canciones[i].get("sesion"), str) else None,
            "inicio_tick": s,
            "inicio_s": round(ini_s, 6), "fin_s": round(ini_s + T.segundos(t["fin"], t["tempo"]), 6),
            "inicio_compas": _compas(s, compases),
            "cambio_tempo_siguiente": cortes[i] if i < len(cortes) else None,
        })
        plan["canciones"][-1]["fin_tick"] = a_show(plan, i, t["fin"], temas)
        plan["marcadores"].append({"nombre": t["nombre"], "tick": s})
        for m in t["comun"].get("marcadores", []):
            item = {"nombre": m["nombre"], "tick": a_show(plan, i, m["tick"], temas)}
            if m.get("fin_tick") is not None:
                item["fin_tick"] = a_show(plan, i, m["fin_tick"], temas)
            plan["marcadores"].append(item)
    plan["_temas"] = temas
    return plan


def a_show(plan, i, tick, temas=None):
    """Tick del show para un tick de la canción i: exacto donde el tempo del show
    es el de la canción; por tiempo real donde no (zona encimada)."""
    temas = temas or plan["_temas"]
    t = temas[i]
    c = plan["canciones"][i] if i < len(plan["canciones"]) else None
    s = c["inicio_tick"] if c else _inicio_en_construccion(plan, i)
    desde = plan["canciones"][i - 1]["cambio_tempo_siguiente"] if i and c else 0
    hasta = c["cambio_tempo_siguiente"] if c else None
    k = s + tick
    if (desde is None or k >= desde) and (hasta is None or k < hasta):
        return k
    seg = T.segundos(s, plan["tempo"]) + T.segundos(tick, t["tempo"])
    return T.tick_de_segundos(seg, plan["tempo"])


def _inicio_en_construccion(plan, i):
    raise ErrorShow("plan incompleto")


def _limpiar(eventos, claves):
    out = []
    for e in sorted(eventos, key=lambda x: x["tick"]):
        if out and out[-1]["tick"] == e["tick"]:
            out[-1] = e
        elif not out or any(out[-1][k] != e[k] for k in claves):
            out.append(e)
    return out


def _verificar_compases(compases, inicios):
    """Cada canción agregada empieza al inicio de un compás del show (los cambios
    de compás propios de cada sesión se respetan tal como vienen)."""
    for k in inicios:
        previo = [c for c in compases if c["tick"] < k]
        if previo and (k - previo[-1]["tick"]) % _largo_compas(previo[-1]):
            raise ErrorShow(f"una canción quedó a mitad de compás (tick {k})")


def _compas(tick, compases):
    numero, previo = 1, compases[0]
    for c in compases[1:]:
        if c["tick"] > tick:
            break
        numero += (c["tick"] - previo["tick"]) // _largo_compas(previo)
        previo = c
    return numero + (tick - previo["tick"]) // _largo_compas(previo)


def texto(plan):
    L = ["# Plan del show", ""]
    for i, c in enumerate(plan["canciones"]):
        L.append(f"{i + 1}. {c['nombre']}: compás {c['inicio_compas']}, {c['inicio_s']:.2f} s → {c['fin_s']:.2f} s")
    L += ["", "Tempo:"]
    L += [f"  {_compas(e['tick'], plan['compases'])}|{e['tick']} {e['bpm']:g} BPM" for e in plan["tempo"]]
    L += ["", "Compases:"]
    L += [f"  compás {_compas(e['tick'], plan['compases'])}: {e['numerador']}/{e['denominador']}" for e in plan["compases"]]
    L += ["", f"Marcadores: {len(plan['marcadores'])}"]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------ armar la sesión

def _canales(p):
    pan = (p.get("mezcla") or {}).get("paneo")
    return 2 if isinstance(pan, list) else 1


def _ruta_audio(sesion, nombre):
    """Ruta absoluta de un archivo de audio de una sesión: en su carpeta
    «Audio Files» o, si no, en cualquier lugar bajo la carpeta de la sesión."""
    carpeta = Path(sesion).parent
    r = carpeta / "Audio Files" / nombre
    if r.exists():
        return str(r)
    for x in carpeta.rglob(nombre):
        return str(x)
    return str(r)


def _destino(p, cancion, mapa, pistas_show, match):
    """Pista del show para la pista p de una canción: la indicada en 'match'
    ({canción: {pista: pista del show o null}}), o la del mismo nombre, tipo y
    canales; si no hay, la de nombre parecido (una sola). None = pista nueva."""
    from .compatibilidad import clave
    m = (match or {}).get(cancion, {})
    if p["nombre"] in m:
        return m[p["nombre"]]
    compatibles = [q for q in pistas_show if q["tipo"] == p["tipo"] and _canales(q) == _canales(p)]
    exacta = next((q for q in compatibles if q["nombre"] == p["nombre"]), None)
    if exacta:
        return exacta["nombre"]
    parecidas = [q for q in compatibles if clave(q["nombre"]) == clave(p["nombre"])]
    return parecidas[0]["nombre"] if len(parecidas) == 1 else None


def armar(show, salida=None):
    """Escribe la sesión del show. Devuelve (ruta, avisos). La base es
    show["base"] o, si no hay, la primera canción (que queda tal cual, con el
    tempo, los compases y los marcadores del show). Cada pista de las demás
    canciones va a la pista del show que indique el match (por defecto, la del
    mismo nombre, tipo y canales); si no hay, se crea una pista nueva con la
    mezcla, los plugins, el ruteo (entrada, salida y envíos, buscados por
    nombre en la base) y la automatización de la pista de la canción. En las
    pistas que ya existen, la automatización de la canción reemplaza a la de
    la pista desde el comienzo de la canción."""
    import os
    import tempfile
    from .comun import leer_comun, aplicar
    from .protools.descifrado import leer_descifrado
    from .protools.inserts import copiar_inserts, plugins_de
    from .protools.ruteo import copiar_ruteo, juntar_automatizacion
    from .protools.reconstruccion import cifrar
    salida = Path(salida or show.get("salida") or "")
    if not str(salida) or salida.suffix.lower() != ".ptx":
        raise ErrorShow("falta 'salida' (.ptx) del show")
    if salida.exists():
        raise ErrorShow(f"{salida} ya existe: elija otro nombre")
    plan = planear(show)
    canciones = show["canciones"]
    base = show.get("base") or canciones[0]["sesion"]
    desde = 0 if show.get("base") else 1       # la primera canción ya está en la base
    comun = leer_comun(base)
    fs = comun["unidades"]["frecuencia_muestreo"]
    if fs != plan["frecuencia_muestreo"]:
        raise ErrorShow(f"la base está a {fs} Hz y las canciones a {plan['frecuencia_muestreo']} Hz")
    avisos = []
    comun["tempo"] = [dict(e) for e in plan["tempo"]]
    comun["compases"] = [dict(e) for e in plan["compases"]]
    existentes = {(m["nombre"], m["tick"]) for m in comun["marcadores"]}
    for m in plan["marcadores"]:
        if (m["nombre"], m["tick"]) not in existentes:
            comun["marcadores"].append(dict(m))
    pistas_show = comun["pistas"]
    anchos_aux = {_canales(q) for q in pistas_show if q["tipo"] == "retorno"}
    copias = []     # (nueva?, nombre en el show, sesión, pista de origen, tipo, muestra de inicio, canción)
    for i in range(desde, len(canciones)):
        tema = plan["_temas"][i]
        cs, sesion = tema["comun"], canciones[i]["sesion"]
        cancion = plan["canciones"][i]["nombre"]
        ini_m = round(plan["canciones"][i]["inicio_s"] * fs)
        archivos = {a["id"]: a for a in cs["archivos"]}
        for p in cs["pistas"]:
            if p["tipo"] not in ("audio", "midi", "retorno"):
                avisos.append(f"{cancion}: la pista {p['nombre']!r} ({p['tipo']}) no se copia (tipo no soportado)")
                continue
            if not p["clips"] and p["tipo"] != "retorno":
                continue
            dest = _destino(p, cancion, None, pistas_show, show.get("match"))
            if dest is None:
                if p["tipo"] == "retorno" and _canales(p) not in anchos_aux:
                    avisos.append(f"{cancion}: el auxiliar {p['nombre']!r} no se copia (la base no tiene un "
                                  f"auxiliar {'estéreo' if _canales(p) == 2 else 'mono'} que sirva de plantilla)")
                    continue
                nombre = p["nombre"]
                usados = {q["nombre"] for q in pistas_show}
                if nombre in usados:
                    nombre = f"{p['nombre']} ({cancion})"
                m = p.get("mezcla") or {}
                q = {"nombre": nombre, "tipo": p["tipo"], "canales": _canales(p), "clips": [],
                     "mezcla": {k: m.get(k) for k in ("volumen_db", "paneo", "mute") if m.get(k) is not None}}
                pistas_show.append(q)
                copias.append((True, nombre, sesion, p["nombre"], p["tipo"], ini_m, cancion))
            else:
                q = next((x for x in pistas_show if x["nombre"] == dest), None)
                if q is None:
                    raise ErrorShow(f"match de {cancion}: no existe la pista del show {dest!r}")
                copias.append((False, dest, sesion, p["nombre"], p["tipo"], ini_m, cancion))
            for c in p["clips"]:
                q["clips"].append(_clip_al_show(c, i, plan, ini_m, archivos, sesion, comun))
    with tempfile.TemporaryDirectory(dir=str(salida.parent)) as tmp:
        intermedia = Path(tmp) / "show.ptx"
        aplicar(comun, base, str(intermedia))
        d = leer_descifrado(str(intermedia))
        origenes = {}
        for nueva, nombre, sesion, pista, tipo, ini_m, cancion in copias:
            if tipo == "midi":
                if nueva:
                    avisos.append(f"{nombre}: pista MIDI: todavía no se copian sus plugins, ruteo ni automatización")
                continue
            if sesion not in origenes:
                origenes[sesion] = leer_descifrado(sesion)
            o = origenes[sesion]
            if not nueva:
                try:
                    d, av = juntar_automatizacion(d, nombre, o, pista, ini_m, cancion)
                    avisos += av
                except Exception as e:
                    avisos.append(f"{nombre}: no se copió la automatización de {cancion} ({e})")
                continue
            try:
                if plugins_de(o, pista):
                    d = copiar_inserts(d, nombre, o, pista)
            except Exception as e:                      # se avisa y se sigue sin esos plugins
                avisos.append(f"{nombre}: no se copiaron los plugins ({e})")
            try:
                d, av = copiar_ruteo(d, nombre, o, pista, ini_m, cancion)
                avisos += av
            except Exception as e:                      # se avisa y se sigue con el de la plantilla
                avisos.append(f"{nombre}: no se copió el ruteo ni la automatización ({e})")
        salida.write_bytes(cifrar(d))
    return salida, avisos


def _clip_al_show(c, i, plan, ini_m, archivos, sesion, comun):
    """Clip de la canción i como clip nuevo del show (sin 'id')."""
    if c["tipo"] == "midi":
        t0 = a_show(plan, i, c["tick"])
        notas = []
        for n in c.get("notas", []):
            a = a_show(plan, i, c["tick"] + n["tick"])
            b = a_show(plan, i, c["tick"] + n["tick"] + n["largo_ticks"])
            notas.append({"tick": a - t0, "largo_ticks": max(b - a, 1), "nota": n["nota"], "velocidad": n["velocidad"]})
        return {"tipo": "midi", "nombre": c.get("nombre"), "tick": t0,
                "largo_ticks": a_show(plan, i, c["tick"] + c["largo_ticks"]) - t0, "notas": notas}
    canciones = plan["canciones"][i]["nombre"]

    def arch(k):
        clave = f"{canciones}:{k}"
        if not any(a.get("id") == clave for a in comun["archivos"]):
            a = archivos.get(k)
            if a is None:
                raise ErrorShow(f"{canciones}: el clip {c.get('nombre')!r} apunta a un archivo desconocido")
            comun["archivos"].append({"id": clave, "ruta": _ruta_audio(sesion, a["ruta"]),
                                      "muestras": a.get("muestras")})
        return clave

    nuevo = {"tipo": "audio", "nombre": c.get("nombre"), "muestra": ini_m + c["muestra"],
             "largo_muestras": c["largo_muestras"], "desde_muestra": c.get("desde_muestra", 0),
             "archivo": arch(c["archivo"])}
    if c.get("archivos_canales"):
        nuevo["archivos_canales"] = [arch(k) for k in c["archivos_canales"]]
    if c.get("mute"):
        nuevo["mute"] = True
    if c.get("fundidos"):
        nuevo["fundidos"] = [dict(f, muestra=ini_m + f["muestra"]) for f in c["fundidos"]]
    return nuevo

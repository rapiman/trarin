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
    for c in [{"cambio_tempo": cambio}] + list(canciones):
        if c.get("cambio_tempo") and c["cambio_tempo"].get("modo") not in ("antes", "mitad", "fin"):
            raise ErrorShow("cambio_tempo.modo debe ser 'antes', 'mitad' o 'fin'")
    temas = []
    for i, c in enumerate(canciones):
        comun = c.get("_comun")
        if comun is None:
            comun = leer_comun(c["sesion"]) if isinstance(c.get("sesion"), str) else c["sesion"]
            if c.get("pistas_extra"):
                comun = _con_extras(comun, c["pistas_extra"])
            c["_comun"] = comun
        tempo = [{"tick": e["tick"], "bpm": e["bpm"]} for e in comun["tempo"]] or [{"tick": 0, "bpm": 120.0}]
        compases = [dict(e) for e in comun["compases"]] or [{"tick": 0, "numerador": 4, "denominador": 4}]
        # antes del primer evento de tempo (o de compás) rige ese mismo: en Pro
        # Tools el primero puede estar más adelante (en el inicio de la canción)
        if tempo[0]["tick"] != 0:
            tempo.insert(0, {"tick": 0, "bpm": tempo[0]["bpm"]})
        if compases[0]["tick"] != 0:
            compases.insert(0, dict(compases[0], tick=0))
        fin = _compas_desde(compases, int(c["fin_compas"])) if c.get("fin_compas") else _fin_cancion(comun)
        # con 'fin_compas' la canción se corta ahí (salvo "cortar": false): los
        # clips, notas, automatización y marcadores posteriores no se copian
        corte = fin if c.get("fin_compas") and c.get("cortar", True) else None
        ultimo = _vigente(compases, fin)
        fin_compas = ultimo["tick"] + math.ceil((fin - ultimo["tick"]) / _largo_compas(ultimo)) * _largo_compas(ultimo)
        temas.append({"comun": comun, "nombre": c.get("nombre") or (
                          Path(str(c.get("sesion"))).stem if isinstance(c.get("sesion"), str)
                          else comun["origen"].get("archivo", f"cancion{i + 1}")),
                      "tempo": tempo, "compases": compases, "fin": fin, "fin_compas": fin_compas,
                      "corte": corte, "frecuencia": comun["unidades"]["frecuencia_muestreo"]})
    fs = {t["frecuencia"] for t in temas}
    fs_show = show.get("frecuencia")
    if len(fs) > 1 and not fs_show:
        raise ErrorShow(f"frecuencias de muestreo distintas: {sorted(fs)} (con \"frecuencia\" en el show, "
                        f"el audio de las canciones con otra se convierte; requiere trarin[audio])")

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
        cb = canciones[i].get("cambio_tempo", cambio)        # cada canción puede tener el suyo
        if cb["modo"] == "fin":
            # el tempo de la siguiente rige desde que termina la anterior (útil si
            # la anterior termina muy lenta: la separación no se estira)
            corte = fin_a
            g = _separacion(sep, ultimo, _vigente(b["tempo"], 0)["bpm"])
            inicio = inicios[-1] + a["fin_compas"] + g
        if inicio <= inicios[-1]:
            raise ErrorShow(f"la separación negativa antes de {b['nombre']} es más larga que {a['nombre']}")
        if cb["modo"] == "fin":
            pass
        elif inicio >= fin_a:                       # separación positiva
            if cb["modo"] == "mitad":
                corte = (fin_a + inicio) // 2
            else:
                corte = max(fin_a, inicio - int(float(cb.get("negras", 2)) * NEGRA))
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
                  if s + e["tick"] > desde and (hasta is None or s + e["tick"] < hasta)
                  and (t["corte"] is None or e["tick"] < t["corte"])]
        # compases: desde el inicio de la canción (o el corte, si se encima), hasta el inicio de la siguiente
        fin_c = inicios[i + 1] if i + 1 < len(inicios) else None
        for e in t["compases"]:
            k = s + e["tick"]
            if (fin_c is None or k < fin_c) and (t["corte"] is None or e["tick"] < t["corte"]):
                compases.append({"tick": k, "numerador": e["numerador"], "denominador": e["denominador"]})
    tempo = _limpiar(tempo, ("bpm",))
    compases = _limpiar(sorted(compases, key=lambda x: x["tick"]), ("numerador", "denominador"))
    _verificar_compases(compases, inicios[1:])

    plan = {"frecuencia_muestreo": fs_show or temas[0]["frecuencia"], "tempo": tempo, "compases": compases,
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
            if t["corte"] is not None and m["tick"] >= t["corte"]:
                continue
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
    if sesion is None or Path(nombre).is_absolute():
        return str(nombre)
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
    import tempfile
    salida = Path(salida or show.get("salida") or "")
    if not str(salida) or salida.suffix.lower() != ".ptx":
        raise ErrorShow("falta 'salida' (.ptx) del show")
    if salida.exists():
        raise ErrorShow(f"{salida} ya existe: elija otro nombre")
    plan = planear(show)
    canciones = show["canciones"]
    base = show.get("base") or canciones[0]["sesion"]
    desde = 0 if show.get("base") else 1       # la primera canción ya está en la base
    tmp = tempfile.TemporaryDirectory(dir=str(salida.parent))
    try:
        return _armar(show, salida, plan, canciones, base, desde, tmp.name)
    finally:
        tmp.cleanup()


def _instrumentos_nuevos(plan, canciones, desde, pistas, match):
    """Pistas de instrumento de las canciones que no van a una pista de la base:
    {nombre en el show: (sesión, pista de origen)}. Se crean antes de armar."""
    pistas = list(pistas)
    out = {}
    for i in range(desde, len(canciones)):
        cancion = plan["canciones"][i]["nombre"]
        for p in plan["_temas"][i]["comun"]["pistas"]:
            if p["tipo"] != "0x0007" or _destino(p, cancion, None, pistas, match) is not None:
                continue
            nombre = p["nombre"]
            if nombre in {q["nombre"] for q in pistas}:
                nombre = f"{p['nombre']} ({cancion})"
            out[nombre] = (canciones[i]["sesion"], p["nombre"])
            pistas.append(dict(p, nombre=nombre, clips=[]))
    return out


def _armar(show, salida, plan, canciones, base, desde, tmp):
    from .comun import leer_comun, aplicar
    from .protools.descifrado import leer_descifrado
    from .protools.inserts import copiar_inserts, plugins_de
    from .protools.pistas_nuevas import agregar_auxiliares, agregar_instrumento
    from .protools.ruteo import (copiar_color, copiar_midi, copiar_ruteo, juntar_automatizacion,
                                 poner_solo, solo)
    from .protools.reconstruccion import cifrar
    avisos = []
    # opciones: buses "juntar" (el bus «Aux 1» de cada canción va al «Aux 1» del
    # show) o "separar" (cada canción usa los suyos: «Aux 1 (canción)»), y
    # copiar_solo (por defecto no: una pista en solo callaría a las demás)
    modo_buses = show.get("buses", "juntar")
    if modo_buses not in ("juntar", "separar"):
        raise ErrorShow("'buses' debe ser \"juntar\" o \"separar\"")
    separar_buses = modo_buses == "separar"
    copiar_solo = bool(show.get("copiar_solo", False))
    origenes = {}

    def origen(sesion):
        if sesion not in origenes:
            origenes[sesion] = leer_descifrado(sesion)
        return origenes[sesion]

    # las pistas de instrumento nuevas se crean primero en una copia de la base
    instrumentos = _instrumentos_nuevos(plan, canciones, desde, leer_comun(base)["pistas"], show.get("match"))
    if instrumentos:
        d0 = leer_descifrado(base)
        for nombre, (sesion, pista) in list(instrumentos.items()):
            try:
                d0 = agregar_instrumento(d0, nombre, (origen(sesion), pista))
            except Exception as e:
                avisos.append(f"la pista de instrumento {pista!r} no se copia ({e})")
                del instrumentos[nombre]
        base = str(Path(tmp) / "base.ptx")
        Path(base).write_bytes(cifrar(d0))
    comun = leer_comun(base)
    fs = comun["unidades"]["frecuencia_muestreo"]
    if show.get("buses_show") and show["buses_show"].get("salida_principal"):
        from .protools.ruteo import rutas
        sp = show["buses_show"]["salida_principal"]
        nombres = {n for c, n, _ in rutas(leer_descifrado(base)) if c >= 0x100}
        if sp not in nombres:
            raise ErrorShow(f"buses_show: la salida principal «{sp}» no existe en la base (hay: {sorted(nombres)})")
    if fs != plan["frecuencia_muestreo"]:
        raise ErrorShow(f"la base está a {fs} Hz y las canciones a {plan['frecuencia_muestreo']} Hz")
    comun["tempo"] = [dict(e) for e in plan["tempo"]]
    comun["compases"] = [dict(e) for e in plan["compases"]]
    existentes = {(m["nombre"], m["tick"]) for m in comun["marcadores"]}
    for m in plan["marcadores"]:
        if (m["nombre"], m["tick"]) not in existentes:
            comun["marcadores"].append(dict(m))
    pistas_show = comun["pistas"]
    anchos_aux = {_canales(q) for q in pistas_show if q["tipo"] == "retorno"}
    externos = {}                   # auxiliares copiados de la canción (sin plantilla en la base): canales
    salidas_show = {}               # con buses_show: pista del show → bus (None = salida principal)
    copias = []     # (nueva?, nombre en el show, sesión, pista de origen, tipo, muestra de inicio, canción)
    conversor = _Conversor(salida, fs)
    buses_show = show.get("buses_show")         # buses y auxiliares propios del show
    for i in range(desde, len(canciones)):
        tema = plan["_temas"][i]
        cs = tema["comun"]
        sesion = canciones[i]["sesion"] if isinstance(canciones[i].get("sesion"), str) else None
        cancion = plan["canciones"][i]["nombre"]
        ini_m = round(plan["canciones"][i]["inicio_s"] * fs)
        archivos = {a["id"]: a for a in cs["archivos"]}
        escala = fs / tema["frecuencia"]
        corte = tema["corte"]
        corte_m = (round(T.segundos(corte, tema["tempo"]) * tema["frecuencia"]) if corte is not None else None)
        ctx = {"escala": escala, "corte": corte, "corte_m": corte_m, "conversor": conversor, "fs": fs}
        tema["_ctx"] = ctx
        for p in cs["pistas"]:
            if p["tipo"] not in ("audio", "midi", "retorno", "0x0007"):
                avisos.append(f"{cancion}: la pista {p['nombre']!r} ({p['tipo']}) no se copia (tipo no soportado)")
                continue
            if buses_show and p["tipo"] == "retorno":
                continue                            # los auxiliares de las canciones se reemplazan
            clips = [x for x in (_clip_al_show(c, i, plan, ini_m, archivos, sesion, comun, ctx)
                                 for c in p["clips"]) if x is not None]
            if not clips and p["tipo"] not in ("retorno", "0x0007"):
                continue
            externa = p.get("_extra")
            dest = None if externa else _destino(p, cancion, None, pistas_show, show.get("match"))
            if (separar_buses and p["tipo"] == "retorno"
                    and p["nombre"] not in (show.get("match") or {}).get(cancion, {})):
                dest = None                     # con buses separados, cada canción lleva sus auxiliares
            if dest is None:
                nombre = p["nombre"]
                usados = {q["nombre"] for q in pistas_show} | set(externos)
                if nombre in usados:
                    nombre = f"{p['nombre']} ({cancion})"
                if p["tipo"] == "retorno" and _canales(p) not in anchos_aux:
                    # la base no tiene un auxiliar de ese ancho: se copia el de la canción
                    externos[nombre] = _canales(p)
                    copias.append((True, nombre, sesion, p["nombre"], "retorno_externo", ini_m, cancion))
                    continue
                m = p.get("mezcla") or {}
                q = {"nombre": nombre, "tipo": p["tipo"], "canales": _canales(p), "clips": [],
                     "mezcla": {k: m.get(k) for k in ("volumen_db", "paneo", "mute") if m.get(k) is not None}}
                pistas_show.append(q)
                if p.get("_origen"):                # pista armada desde archivos, con ruteo de otra sesión
                    o_s, o_p = p["_origen"]
                    copias.append((True, nombre, o_s, o_p, p["tipo"], ini_m, cancion,
                                   {"escala": fs / leer_comun(o_s)["unidades"]["frecuencia_muestreo"]}))
                else:
                    copias.append((True, nombre, None if externa else sesion, p["nombre"], p["tipo"], ini_m, cancion))
            else:
                q = next((x for x in pistas_show if x["nombre"] == dest), None)
                if q is None:
                    raise ErrorShow(f"match de {cancion}: no existe la pista del show {dest!r}")
                if instrumentos.get(dest) == (sesion, p["nombre"]):      # creada para esta pista
                    copias.append((True, dest, sesion, p["nombre"], "instrumento", ini_m, cancion))
                else:
                    copias.append((False, dest, sesion, p["nombre"], p["tipo"], ini_m, cancion))
            q["clips"] += clips
            if buses_show and p["tipo"] in ("audio", "0x0007"):
                salidas_show[q["nombre"]] = _bus_de(p["nombre"], buses_show)
    if buses_show:
        _auxiliares_show(comun, buses_show)
    intermedia = Path(tmp) / "show.ptx"
    aplicar(comun, base, str(intermedia))
    d = leer_descifrado(str(intermedia))
    if buses_show:
        d = _crear_buses(d, buses_show)
    ctx_de = {plan["canciones"][i]["nombre"]: plan["_temas"][i].get("_ctx", {}) for i in range(len(canciones))}
    for nueva, nombre, sesion, pista, tipo, ini_m, cancion, *propio in copias:
        if sesion is None:                          # pista armada desde archivos (sin sesión de origen)
            continue
        o = origen(sesion)
        cx = propio[0] if propio else ctx_de.get(cancion, {})
        esc, hasta = cx.get("escala", 1.0), cx.get("corte_m")
        forzar = {}
        if buses_show and nombre in salidas_show:
            forzar = {"salida": salidas_show[nombre] or buses_show.get("salida_principal"), "envios": False}
        if nueva and tipo != "retorno_externo":
            try:
                d = copiar_color(d, nombre, o, pista)
                d = poner_solo(d, nombre, copiar_solo and solo(o, pista))
            except Exception as e:
                avisos.append(f"{nombre}: no se copió el color o el solo ({e})")
        if tipo == "midi":
            if nueva:                               # salida MIDI, valores y automatización
                try:
                    d, av = copiar_midi(d, nombre, o, pista, ini_m, cancion, esc, hasta)
                    avisos += av
                except Exception as e:
                    avisos.append(f"{nombre}: no se copió la salida MIDI ni la automatización ({e})")
            continue
        if tipo == "retorno_externo":
            try:
                d = agregar_auxiliares(d, [(nombre, externos[nombre])], externa=(o, pista))
            except Exception as e:
                avisos.append(f"{cancion}: el auxiliar {pista!r} no se copia ({e})")
                continue
        if tipo == "retorno_externo":
            d = copiar_color(d, nombre, o, pista)
            d = poner_solo(d, nombre, copiar_solo and solo(o, pista))
        if not nueva:
            try:
                d, av = juntar_automatizacion(d, nombre, o, pista, ini_m, cancion, esc, hasta)
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
            d, av = copiar_ruteo(d, nombre, o, pista, ini_m, cancion,
                                 f" ({cancion})" if separar_buses else "", esc, hasta, **forzar)
            avisos += av
        except Exception as e:                      # se avisa y se sigue con el de la plantilla
            avisos.append(f"{nombre}: no se copió el ruteo ni la automatización ({e})")
        if tipo == "instrumento":                   # además, su canal MIDI y salida MIDI
            try:
                d, av = copiar_midi(d, nombre, o, pista, ini_m, cancion, esc, hasta)
                avisos += av
            except Exception as e:
                avisos.append(f"{nombre}: no se copió la salida MIDI ({e})")
    if buses_show:
        d, av = _ruteo_show(d, buses_show, salidas_show, copias, origen, tmp)
        avisos += av
    salida.write_bytes(cifrar(d))
    if buses_show and buses_show.get("quitar_pistas"):
        _quitar_y_renombrar(salida, buses_show)
    return salida, avisos


def _clip_al_show(c, i, plan, ini_m, archivos, sesion, comun, ctx=None):
    """Clip de la canción i como clip nuevo del show (sin 'id'), o None si queda
    después del corte de la canción. Con otra frecuencia de muestreo, las
    posiciones se escalan y el audio se convierte (ctx: escala, corte en ticks y
    en muestras de la canción, conversor)."""
    ctx = ctx or {}
    corte, corte_m = ctx.get("corte"), ctx.get("corte_m")
    if c["tipo"] == "midi":
        tick, largo = c["tick"], c["largo_ticks"]
        notas_c = c.get("notas", [])
        if corte is not None:
            if tick >= corte:
                return None
            if tick + largo > corte:
                largo = corte - tick
                notas_c = [dict(n, largo_ticks=min(n["largo_ticks"], largo - n["tick"]))
                           for n in notas_c if n["tick"] < largo]
        t0 = a_show(plan, i, tick)
        largo_show = a_show(plan, i, tick + largo) - t0
        notas = []
        for n in notas_c:
            a = a_show(plan, i, tick + n["tick"])
            b = a_show(plan, i, tick + n["tick"] + n["largo_ticks"])
            if a - t0 >= largo_show:            # notas que empiezan después del final del clip (ocultas)
                continue
            notas.append({"tick": a - t0, "largo_ticks": max(b - a, 1), "nota": n["nota"], "velocidad": n["velocidad"]})
        return {"tipo": "midi", "nombre": c.get("nombre"), "tick": t0,
                "largo_ticks": largo_show, "notas": notas}
    canciones = plan["canciones"][i]["nombre"]
    escala = ctx.get("escala", 1.0)
    conv = ctx.get("conversor")
    muestra, largo, desde = c["muestra"], c["largo_muestras"], c.get("desde_muestra", 0)
    recortado = False
    if corte_m is not None:
        if muestra >= corte_m:
            return None
        if muestra + largo > corte_m:
            largo, recortado = corte_m - muestra, True
    fin_origen = muestra + largo

    def arch(k):
        clave = f"{canciones}:{k}"
        hecho = next((a for a in comun["archivos"] if a.get("id") == clave), None)
        if hecho is None:
            a = archivos.get(k)
            if a is None:
                raise ErrorShow(f"{canciones}: el clip {c.get('nombre')!r} apunta a un archivo desconocido")
            ruta, muestras, e = _ruta_audio(sesion, a["ruta"]), a.get("muestras"), escala
            if a.get("canal") is not None:          # archivo agregado (pistas_extra): un canal suyo
                if conv is None:
                    raise ErrorShow("los archivos agregados necesitan el conversor (trarin[audio])")
                ruta, muestras = conv.canales(ruta)[a["canal"]]
            elif conv is not None:
                ruta, muestras, e = conv.a_show(ruta, muestras, escala)
            hecho = {"id": clave, "ruta": ruta, "muestras": muestras, "_escala": e}
            comun["archivos"].append(hecho)
        return hecho

    def esc(x, e=escala):
        return int(round(x * e))

    a0 = arch(c["archivo"])
    ef = a0.get("_escala", escala)                # escala del archivo (puede no ser la de la canción)
    d_show, l_show = esc(desde, ef), esc(largo, ef)
    if a0.get("muestras"):
        l_show = min(l_show, a0["muestras"] - d_show)
    nuevo = {"tipo": "audio", "nombre": c.get("nombre"), "muestra": ini_m + esc(muestra),
             "largo_muestras": l_show, "desde_muestra": d_show, "archivo": a0["id"]}
    if c.get("archivos_canales"):
        nuevo["archivos_canales"] = [arch(k)["id"] for k in c["archivos_canales"]]
    if c.get("mute"):
        nuevo["mute"] = True
    if c.get("ganancia_db") is not None:                       # clip gain
        nuevo["ganancia_db"] = c["ganancia_db"]
    if c.get("ganancia_curva"):
        nodos = [x for x in c["ganancia_curva"] if x["muestra"] <= largo]
        nuevo["ganancia_curva"] = [dict(x, muestra=min(esc(x["muestra"], ef), l_show)) for x in nodos] \
            or [{"muestra": 0, "db": c["ganancia_curva"][0]["db"]}]
    fundidos = []
    for f in c.get("fundidos") or []:
        if f["muestra"] + f["muestras"] > fin_origen:
            continue                                # quedó después del corte
        fundidos.append(dict(f, muestra=ini_m + esc(f["muestra"]), muestras=max(esc(f["muestras"]), 1)))
    if recortado:                                   # fundido corto de salida en el corte
        n = max(int(ctx.get("fs", 48000) * 0.01), 1)
        fin = nuevo["muestra"] + l_show
        if not any(f["muestra"] + f["muestras"] > fin - n for f in fundidos) and l_show > 2 * n:
            fundidos.append({"tipo": "salida", "muestras": n, "pendiente": "ganancia",
                             "curva": {"forma": "estandar"}, "muestra": fin - n})
    if fundidos:
        nuevo["fundidos"] = fundidos
    return nuevo


class _Conversor:
    """Archivos de audio a la frecuencia del show: los que tienen otra se
    convierten (una vez) a «Audio Files» junto a la sesión del show, en mono
    (un archivo por canal: .L y .R si son estéreo)."""

    def __init__(self, salida, fs):
        self.carpeta = Path(salida).parent / "Audio Files"
        self.fs = fs
        self.hechos = {}

    def _info(self, ruta):
        from . import audio
        try:
            return audio.info(ruta)
        except (OSError, audio.ErrorAudio):
            return None

    def a_show(self, ruta, muestras, escala):
        """(ruta, muestras, escala del archivo) de un archivo mono para el show."""
        i = self._info(ruta)
        if i is None:
            return ruta, (int(round(muestras * escala)) if muestras else muestras), escala
        if i["frecuencia"] == self.fs and i["canales"] == 1:
            return ruta, i["muestras"], 1.0
        return self.canales(ruta)[0] + (self.fs / i["frecuencia"],)

    def canales(self, ruta):
        """[(ruta, muestras)] de archivos mono a la frecuencia del show, uno por
        canal del archivo (convertidos si hace falta)."""
        if ruta in self.hechos:
            return self.hechos[ruta]
        from . import audio
        i = audio.info(ruta)
        if i["frecuencia"] == self.fs and i["canales"] == 1:
            out = [(ruta, i["muestras"])]
        else:
            import hashlib
            self.carpeta.mkdir(parents=True, exist_ok=True)
            # nombre fijo por archivo de origen: si ya se convirtió (otro armado), se reutiliza
            marca = hashlib.md5(str(Path(ruta).resolve()).encode("utf-8")).hexdigest()[:6]
            base = self.carpeta / f"{Path(ruta).stem}_{self.fs // 1000}k_{marca}"
            sufijos = [".wav"] if i["canales"] == 1 else ([".L.wav", ".R.wav"] if i["canales"] == 2
                                                          else [f".{k + 1}.wav" for k in range(i["canales"])])
            previos = [Path(str(base) + x) for x in sufijos]
            esperadas = int(round(i["muestras"] * self.fs / i["frecuencia"]))
            if all(x.exists() for x in previos) and all(
                    abs(audio.info(x)["muestras"] - esperadas) <= 2 for x in previos):
                out = [(str(x), audio.info(x)["muestras"]) for x in previos]
            else:
                bits = i["bits"] if i["formato"] == 1 and i["bits"] in (16, 24) else 24
                out = [(r, audio.info(r)["muestras"]) for r in audio.convertir(ruta, base, self.fs, bits, mono=True)]
        self.hechos[ruta] = out
        return out


# ------------------------------------------------- pistas agregadas desde archivos

def _con_extras(comun, extras):
    """Copia de 'comun' con las pistas de 'pistas_extra' (archivos WAV o MIDI que
    no están en la sesión), en las unidades de la canción:

    audio: {"nombre", "clips": [{"archivo": WAV, "segundos": dónde empieza en la
           canción, "desde_s": desde dónde del archivo (0), "largo_s": cuánto
           (hasta el final), "ganancia_db"}]} (o "archivo" y "segundos" directo
           en la pista). Pista estéreo si el archivo lo es.
    midi:  {"nombre", "tipo": "midi", "midi": archivo .mid, "pista_midi": nombre
           de su pista, "segundos": dónde cae su inicio en la canción (0),
           "tempo_midi": bpm con que se leen sus negras (por defecto el del
           archivo), "desde_s"/"hasta_s": solo las notas en ese tramo (en
           segundos del archivo), "copiar_de": {"sesion", "pista"} (pista MIDI de
           otra sesión de la que se copian la salida MIDI y el canal)}."""
    from . import audio
    comun = dict(comun, pistas=list(comun["pistas"]), archivos=list(comun["archivos"]))
    fs = comun["unidades"]["frecuencia_muestreo"]
    tempo = comun["tempo"] or [{"tick": 0, "bpm": 120.0}]
    usados = {p["nombre"] for p in comun["pistas"]}
    for x in extras:
        nombre = x["nombre"]
        if nombre in usados:
            raise ErrorShow(f"pistas_extra: la pista {nombre!r} ya existe en la canción")
        usados.add(nombre)
        p = {"nombre": nombre, "tipo": x.get("tipo", "audio"), "clips": [], "mezcla": dict(x.get("mezcla") or {}),
             "_extra": True}
        if x.get("copiar_de"):
            p["_origen"] = (x["copiar_de"]["sesion"], x["copiar_de"]["pista"])
        if p["tipo"] == "midi":
            p["clips"].append(_clip_midi_extra(x, tempo))
            comun["pistas"].append(p)
            continue
        canales = None
        for c in x.get("clips") or [x]:
            i = audio.info(c["archivo"])
            if canales is None:
                canales = i["canales"]
            elif canales != i["canales"]:
                raise ErrorShow(f"pistas_extra {nombre!r}: los archivos tienen distinta cantidad de canales")
            if canales > 2:
                raise ErrorShow(f"pistas_extra {nombre!r}: solo archivos mono o estéreo")
            r = fs / i["frecuencia"]                   # unidades de la canción
            total = int(i["muestras"] * r)
            ids = []
            for k in range(canales):
                ident = f"extra:{c['archivo']}:{k}"
                if not any(a["id"] == ident for a in comun["archivos"]):
                    comun["archivos"].append({"id": ident, "ruta": c["archivo"], "canal": k, "muestras": total})
                ids.append(ident)
            desde = int(round(float(c.get("desde_s", 0)) * fs))
            largo = int(round(float(c["largo_s"]) * fs)) if c.get("largo_s") else total - desde
            largo = min(largo, total - desde)
            seg = float(c["segundos"])
            clip = {"tipo": "audio", "nombre": c.get("nombre") or Path(c["archivo"]).stem,
                    "muestra": int(round(seg * fs)), "largo_muestras": largo, "desde_muestra": desde,
                    "archivo": ids[0], "tick": T.tick_de_segundos(seg, tempo),
                    "largo_ticks": T.tick_de_segundos(seg + largo / fs, tempo) - T.tick_de_segundos(seg, tempo)}
            if canales == 2:
                clip["archivos_canales"] = ids
            if c.get("ganancia_db") is not None:
                clip["ganancia_db"] = float(c["ganancia_db"])
            p["clips"].append(clip)
        p["mezcla"].setdefault("paneo", [-1.0, 1.0] if canales == 2 else 0.0)
        comun["pistas"].append(p)
    return comun


def _clip_midi_extra(x, tempo):
    from .midi_smf import leer as leer_smf
    m = leer_smf(x["midi"])
    pista = next((t for t in m["pistas"] if t["nombre"] == x["pista_midi"]), None)
    if pista is None:
        raise ErrorShow(f"{x['midi']}: no tiene la pista {x['pista_midi']!r} "
                        f"(hay: {[t['nombre'] for t in m['pistas']]})")
    bpm = float(x.get("tempo_midi") or (m["tempo"][0][1] if m["tempo"] else 120.0))
    base = float(x.get("segundos", 0.0))
    desde_s, hasta_s = x.get("desde_s"), x.get("hasta_s")
    notas = []
    for t, largo, nota, vel, _ in pista["notas"]:
        a = t / m["ppq"] * 60.0 / bpm
        b = (t + largo) / m["ppq"] * 60.0 / bpm
        if (desde_s is not None and a < desde_s) or (hasta_s is not None and a >= hasta_s):
            continue
        if hasta_s is not None:
            b = min(b, hasta_s)
        notas.append((T.tick_de_segundos(base + a, tempo), T.tick_de_segundos(base + b, tempo), nota, vel))
    if not notas:
        raise ErrorShow(f"{x['midi']} {x['pista_midi']!r}: sin notas en el tramo pedido")
    t0 = min(n[0] for n in notas)
    t1 = max(n[1] for n in notas)
    return {"tipo": "midi", "nombre": x.get("nombre_clip") or x["nombre"], "tick": t0, "largo_ticks": t1 - t0,
            "notas": [{"tick": a - t0, "largo_ticks": max(b - a, 1), "nota": n, "velocidad": v}
                      for a, b, n, v in notas]}


# ------------------------------------------------------- buses del show

def _normal(texto):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(ch) != "Mn")


def _bus_de(nombre, buses_show):
    """Bus del show al que va una pista según su nombre (None = salida principal)."""
    import re
    n = _normal(nombre)
    for b in buses_show.get("buses", []):
        if re.search(b["patron"], n):
            return b["bus"]
    return None


def _aux_temporal(b):
    return f"trarin {b['aux']}"


def _auxiliares_show(comun, buses_show):
    """Agrega al modelo del show un auxiliar estéreo por bus (con nombre temporal:
    la plantilla puede tener pistas con esos nombres; se renombran al final)."""
    for b in buses_show.get("buses", []):
        comun["pistas"].append({"nombre": _aux_temporal(b), "tipo": "retorno", "canales": 2, "clips": [],
                                "mezcla": {"volumen_db": 0.0, "paneo": [-1.0, 1.0], "mute": False}})


def _crear_buses(d, buses_show):
    from .protools.bloques import arbol
    from .protools.ruteo import _Tabla, crear_bus
    for b in buses_show.get("buses", []):
        if _Tabla(d, arbol(d)).buscar(2, b["bus"]) is None:
            d = crear_bus(d, b["bus"], 2)
    return d


def _ruteo_show(d, buses_show, salidas_show, copias, origen, tmp):
    """Entrada de cada auxiliar del show (su bus) y salida principal de las
    pistas nuevas armadas desde archivos."""
    from .comun import leer_comun
    from .protools.ruteo import copiar_ruteo
    avisos = []
    pl = buses_show["plantilla_aux"]
    entrada_pl = next(p for p in leer_comun(pl["sesion"])["pistas"] if p["nombre"] == pl["pista"])["mezcla"]["entrada"]
    principal = buses_show.get("salida_principal")
    for b in buses_show.get("buses", []):
        d, av = copiar_ruteo(d, _aux_temporal(b), origen(pl["sesion"]), pl["pista"],
                             renombrar={entrada_pl: b["bus"]}, salida=principal, envios=False)
        avisos += av
    for nueva, nombre, sesion, pista, tipo, *_ in copias:
        if nueva and sesion is None and tipo == "audio":
            d, av = copiar_ruteo(d, nombre, d, nombre, salida=salidas_show.get(nombre) or principal, envios=False)
            avisos += av
    return d, avisos


def _quitar_y_renombrar(salida, buses_show):
    """Quita las pistas (y marcadores, {nombre, tick}) de la plantilla que no se
    usan y deja los auxiliares con su nombre final."""
    import os
    from .comun import aplicar, leer_comun
    comun = leer_comun(str(salida))
    quitar = set(buses_show.get("quitar_pistas") or [])
    comun["pistas"] = [p for p in comun["pistas"] if p["nombre"] not in quitar]
    quitar_m = {(m["nombre"], m["tick"]) for m in buses_show.get("quitar_marcadores") or []}
    comun["marcadores"] = [m for m in comun["marcadores"] if (m["nombre"], m["tick"]) not in quitar_m]
    finales = {_aux_temporal(b): b["aux"] for b in buses_show.get("buses", [])}
    for p in comun["pistas"]:
        if p["nombre"] in finales:
            p["nombre"] = finales[p["nombre"]]
            p.setdefault("mezcla", {})["volumen_db"] = 0.0
    tmp = Path(salida).with_name(Path(salida).stem + "_tmp.ptx")
    aplicar(comun, str(salida), str(tmp))
    os.replace(tmp, salida)

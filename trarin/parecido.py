"""Comparar sonidos a partir de las fichas (fase 3): qué pistas se parecen a
una pista o a un WAV dado («busca en el show lo que se parezca a este golpe»),
y buscar pistas por una descripción en palabras («agudas y percusivas en
Aliwen»). Trabaja sobre las fichas ya calculadas (fichas_calcular); no abre los
archivos de audio. Solo lee.

El parecido mira el timbre y la manera de tocar, no el nivel: color (bandas,
brillo, tonal o ruidoso), articulación (golpes por segundo, ataque, caída,
cresta), altura (nota más presente) y estéreo. Cada rasgo está en una escala
donde 1 es «una diferencia que se nota»; la distancia es la raíz de la suma de
cuadrados ponderada. Es orientativo, no exacto."""

import math
import re
from pathlib import Path

from .vistas import _normal

# (nombre, peso): cuánto cuenta cada rasgo en la distancia
RASGOS = (("sub", 1.0), ("graves", 1.0), ("medios", 1.0), ("presencia", 1.0), ("agudos", 1.0),
          ("brillo", 1.5), ("planitud", 1.5), ("ataque", 2.0), ("golpes", 1.0), ("caida", 1.0),
          ("cresta", 0.7), ("tonal", 0.7), ("nota", 0.7), ("canales", 0.5))
_ATAQUE = {"sostenido": 0.0, "mixto": 0.5, "percusivo": 1.0}


def rasgos(f):
    """Vector {rasgo: valor} de una ficha (salida de fichas.resumir). None si
    la ficha no tiene sonido."""
    if not f.get("color"):
        return None
    c, a, h, e = f["color"], f.get("articulacion") or {}, f.get("altura") or {}, f.get("estereo") or {}
    b = c["bandas_pct"]
    v = {k: b.get(k, 0) / 50.0 for k in ("sub", "graves", "medios", "presencia", "agudos")}   # 50 % = 1
    v["brillo"] = math.log2(max(c["centroide_hz"], 50) / 1000)                                 # una octava = 1
    v["planitud"] = math.log10(max(c["planitud"], 1e-6) / 1e-3) / 1.5                           # 1,5 décadas = 1
    v["ataque"] = _ATAQUE.get(a.get("ataque"), 0.0)
    v["golpes"] = math.log2(1 + (a.get("golpes_por_s") or 0))                                   # el doble = 1
    v["caida"] = (a.get("caida_db") or 0) / 6.0
    v["cresta"] = (a.get("cresta_db") or 0) / 6.0
    v["tonal"] = (h.get("tonal_pct") or 0) / 100.0
    v["nota"] = _midi(h["notas"][0]["nota"]) / 12.0 if h.get("notas") else None
    v["canales"] = 1.0 if e.get("canales") == 2 else 0.0
    return v


_NOTAS = ("do", "do#", "re", "re#", "mi", "fa", "fa#", "sol", "sol#", "la", "la#", "si")


def _es_nota(f, n):
    """La nota más presente de la ficha (o una que pese al menos el 25 %) es n
    («la», «do#», «re4»)."""
    for k, x in enumerate((f.get("altura") or {}).get("notas") or []):
        if k and x.get("pct", 0) < 25:
            continue
        nn = _normal(x["nota"])
        if nn == n or (not n[-1].isdigit() and re.fullmatch(re.escape(n) + r"-?\d+", nn)):
            return True
    return False


def _midi(nombre):
    m = re.match(r"([A-Za-z]+#?)(-?\d+)", nombre)
    if not m:
        return 0
    return 12 * (int(m.group(2)) + 1) + _NOTAS.index(m.group(1).lower())


def distancia(va, vb):
    """(distancia, [(rasgo, diferencia)] de mayor a menor)."""
    suma, difs = 0.0, []
    for k, w in RASGOS:
        x, y = va.get(k), vb.get(k)
        if x is None or y is None:
            continue
        d = x - y
        if k == "nota":                       # la misma nota en otra octava se parece
            d = min(abs(d) % 1.0, 1.0 - abs(d) % 1.0) * 2
        suma += w * d * d
        difs.append((k, round(d, 2)))
    difs.sort(key=lambda x: -abs(x[1]))
    return round(math.sqrt(suma), 2), difs


_EXPLICA = {"sub": "sub", "graves": "graves", "medios": "medios", "presencia": "presencia", "agudos": "agudos",
            "brillo": "brillo", "planitud": "ruidoso/tonal", "ataque": "ataque", "golpes": "golpes por segundo",
            "caida": "caída tras el golpe", "cresta": "cresta", "tonal": "cuánto es tonal", "nota": "nota",
            "canales": "mono/estéreo"}


def _por_que(difs):
    return ", ".join(f"{_EXPLICA[k]} {'+' if d > 0 else '−'}" for k, d in difs[:3] if abs(d) >= 0.5) or "casi iguales"


# ------------------------------------------------------------------ candidatos

def _ventanas(ses, cancion, ventana_compases):
    """[(nombre de canción, (a, b), etiqueta del tramo)]: la canción entera o
    partida en ventanas de N compases."""
    from . import fichas as F
    nombre_c, a, b = F._ventana_cancion(ses.comun, cancion)
    if not ventana_compases:
        return [(nombre_c, (a, b), None)]
    r = ses.reloj
    k0, k1 = r.compas_de(a), r.compas_de(min(b, a + 3600))
    out = []
    k = k0
    while k <= k1:
        ta, tb = r.segundo_de_compas(k), r.segundo_de_compas(k + ventana_compases)
        if ta >= b:
            break
        out.append((nombre_c, (max(ta, a), min(tb, b)), f"c. {k - k0 + 1}–{k - k0 + ventana_compases}"))
        k += ventana_compases
    return out


def _fichas_pistas(ses, cancion=None, por_cancion=False, ventana_compases=0):
    """[(etiqueta, pista, canción, ficha, tramo)] de las pistas de audio con sonido.
    ventana_compases: cada pista partida en tramos de N compases de la canción."""
    from . import fichas as F
    out = []
    if por_cancion and not cancion:
        canciones = [n for n, _ in F._canciones_de(ses.comun)]
    else:
        canciones = [cancion]
    for cn in canciones:
        tramos = _ventanas(ses, cn, ventana_compases) if cn else [(None, None, None)]
        for nombre_c, rango, tramo in tramos:
            for p in ses.comun["pistas"]:
                if p["tipo"] != "audio":
                    continue
                j = ses.tramo(p, rango)[0].juntar()
                if j is None:
                    continue
                f = F.resumir(j, ses.reloj)
                if not f.get("color"):
                    continue
                etiqueta = p["nombre"] + (f" ({nombre_c}" + (f", {tramo}" if tramo else "") + ")" if nombre_c else "")
                out.append((etiqueta, p["nombre"], nombre_c, f, tramo))
    return out


def _ficha_tramo(ses, pista, cancion, desde_compas, hasta_compas):
    """Ficha de una pista en los compases desde–hasta de la canción (un golpe,
    una frase), como referencia."""
    from . import fichas as F
    from .escucha import _rango
    p = next((x for x in ses.comun["pistas"] if x["tipo"] == "audio" and x["nombre"] == pista), None) or \
        next((x for x in ses.comun["pistas"] if x["tipo"] == "audio" and _normal(pista) in _normal(x["nombre"])), None)
    if p is None:
        raise F.ErrorFicha(f"no hay una pista de audio que se llame como {pista!r}")
    a, b, nombre = _rango(ses, cancion, desde_compas, hasta_compas, None, None)
    j = ses.tramo(p, (a, b))[0].juntar()
    if j is None:
        raise F.ErrorFicha(f"{p['nombre']} no tiene audio con ficha en {nombre}")
    f = F.resumir(j, ses.reloj)
    if not f.get("color"):
        raise F.ErrorFicha(f"{p['nombre']} no suena en {nombre}")
    return f, f"{p['nombre']} ({nombre})"


def parecidos(sesion, referencia, cancion=None, cuantos=8, por_cancion=False, desde_compas=None, hasta_compas=None,
              ventana_compases=0, sin_misma_pista=False):
    """Las pistas de la sesión más parecidas a 'referencia': el nombre de una
    pista de la misma sesión, o la ruta de un WAV (su ficha se calcula en el
    momento si no está). cancion: comparar lo que suena en esa canción.
    por_cancion: cada pista por separado en cada canción del show.
    desde_compas–hasta_compas: la referencia es solo ese tramo de la pista (un
    golpe, una frase). ventana_compases: las candidatas se parten en tramos de
    N compases de la canción («este golpe del compás 12 contra los demás»)."""
    from . import fichas as F
    ses = F.Sesion(str(sesion))
    if not ses.hay():
        raise F.ErrorFicha("esta sesión todavía no tiene fichas: calcularlas con fichas_calcular")
    if ventana_compases and not cancion:
        raise F.ErrorFicha("ventana_compases necesita una canción")
    cand = _fichas_pistas(ses, cancion, por_cancion, ventana_compases)
    ref = str(referencia)
    if ref.lower().endswith((".wav", ".aif", ".aiff")) and Path(ref).exists():
        fr = F.ficha_archivo(ref)
        etiqueta_ref = Path(ref).name
    elif desde_compas or hasta_compas:
        fr, etiqueta_ref = _ficha_tramo(ses, ref, cancion, desde_compas, hasta_compas)
    else:
        mio = [x for x in cand if x[1] == ref and not x[4]] or [x for x in cand if _normal(ref) in _normal(x[1]) and not x[4]]
        if not mio and ventana_compases:
            fr, etiqueta_ref = _ficha_tramo(ses, ref, cancion, None, None)
        elif not mio:
            raise F.ErrorFicha(f"no hay una pista de audio con sonido que se llame {ref!r}"
                               + (f" en {cancion}" if cancion else ""))
        else:
            fr, etiqueta_ref = mio[0][3], mio[0][0]
    vr = rasgos(fr)
    if vr is None:
        raise F.ErrorFicha("la referencia no tiene sonido")
    lista = []
    pista_ref = etiqueta_ref.split(" (")[0]
    for etiqueta, pista, cn, f, tramo in cand:
        if etiqueta == etiqueta_ref or (sin_misma_pista and pista == pista_ref):
            continue
        v = rasgos(f)
        if v is None:
            continue
        d, difs = distancia(vr, v)
        lista.append({"pista": pista, "cancion": cn, "tramo": tramo, "distancia": d, "difiere_en": _por_que(difs),
                      "palabras": f["palabras"]})
    lista.sort(key=lambda x: x["distancia"])
    return {"referencia": etiqueta_ref, "palabras_referencia": fr["palabras"], "candidatas": len(lista),
            "parecidos": lista[:cuantos]}


# ------------------------------------------------------------------ búsqueda por descripción

# cada entrada: (palabras que la disparan, función(ficha) -> bool, nombre del criterio)
def _c(f):
    return f["color"]["palabras"]


def _a(f):
    return f.get("articulacion") or {}


def _lufs(f):
    n = f.get("nivel") or {}
    return n.get("lufs") if n.get("lufs") is not None else n.get("rms_dbfs")


CRITERIOS = (
    (("agudo", "aguda", "agudos", "agudas"), lambda f: "agudo" in _c(f), "agudo"),
    (("grave", "graves"), lambda f: "grave" in _c(f), "grave"),
    (("muy grave", "subgrave", "sub"), lambda f: "muy grave" in _c(f), "muy grave"),
    (("medio", "medios", "media"), lambda f: "registro medio" in _c(f), "de registro medio"),
    (("brillante", "brillantes"), lambda f: "brillante" in _c(f), "brillante"),
    (("opaco", "opaca", "opacos", "opacas", "apagado", "apagada"), lambda f: "opaco" in _c(f), "opaco"),
    (("tonal", "tonales", "afinado", "afinada", "nota"), lambda f: "tonal" in _c(f) or (f.get("altura") or {}).get("notas"), "tonal"),
    (("ruidoso", "ruidosa", "ruidosos", "ruidosas", "ruido"), lambda f: "ruidoso" in _c(f), "ruidoso"),
    (("percusivo", "percusiva", "percusivos", "percusivas", "golpe", "golpes", "golpeado"), lambda f: _a(f).get("ataque") == "percusivo", "percusivo"),
    (("sostenido", "sostenida", "sostenidos", "sostenidas", "continuo", "continua", "pad", "colchon"), lambda f: _a(f).get("ataque") == "sostenido", "sostenido"),
    (("mixto", "mixta"), lambda f: _a(f).get("ataque") == "mixto", "mixto"),
    (("regular", "regulares", "parejo", "pareja"), lambda f: (_a(f).get("regularidad") or "").endswith("regular") and not _a(f).get("regularidad", "").startswith("ir"), "regular"),
    (("irregular", "irregulares", "libre"), lambda f: _a(f).get("regularidad") == "irregular", "irregular"),
    (("rapido", "rapida", "rapidos", "rapidas", "denso", "densa"), lambda f: (_a(f).get("golpes_por_s") or 0) >= 4, "rápido (≥ 4 golpes/s)"),
    (("lento", "lenta", "lentos", "lentas", "espaciado", "espaciada"), lambda f: _a(f).get("ataque") != "sostenido" and 0 < (_a(f).get("golpes_por_s") or 0) < 1.5, "lento (< 1,5 golpes/s)"),
    (("mono",), lambda f: f["estereo"]["tipo"] in ("mono", "mono duplicado", "casi mono"), "mono"),
    (("estereo", "estereos"), lambda f: f["estereo"]["tipo"].startswith("estéreo"), "estéreo"),
    (("satura", "saturado", "saturada", "saturados", "saturadas", "saturacion", "distorsionado", "distorsionada"), lambda f: "saturacion" in f["problemas"], "satura"),
    (("fuerte", "fuertes"), lambda f: _lufs(f) is not None and _lufs(f) > -18, "fuerte (> −18 LUFS)"),
    (("suave", "suaves", "bajito", "bajita", "debil"), lambda f: _lufs(f) is not None and _lufs(f) < -30, "suave (< −30 LUFS)"),
    (("crescendo", "crece"), lambda f: "crece" in (f.get("forma") or {}).get("palabra", "") or "crescendo" in (f.get("forma") or {}).get("palabra", ""), "crece"),
    (("decrescendo", "se apaga", "baja"), lambda f: "apaga" in (f.get("forma") or {}).get("palabra", "") or "decrescendo" in (f.get("forma") or {}).get("palabra", ""), "se apaga"),
    (("constante", "constantes", "plano", "plana"), lambda f: "constante" in (f.get("forma") or {}).get("palabra", ""), "constante"),
    (("intermitente", "intermitentes", "entrecortado", "entrecortada"), lambda f: "intermitente" in (f.get("forma") or {}).get("palabra", ""), "intermitente"),
    (("problema", "problemas", "clics", "clic", "cortes", "continua"), lambda f: bool(f["problemas"]), "con problemas"),
)
_RELLENO = {"que", "y", "e", "o", "u", "de", "del", "la", "las", "el", "los", "un", "una", "unos", "unas", "pistas",
            "pista", "pistas", "sonidos", "sonido", "son", "suenan", "suena", "sean", "con", "sin", "muy", "mas",
            "bastante", "algo", "cosas", "cosa", "busca", "buscar", "quiero", "dame", "todas", "todos", "lo", "en"}


def interpretar(descripcion):
    """[(criterio, función)] y las palabras que no se entendieron."""
    texto = _normal(descripcion)
    criterios, usadas = [], set()
    # «nota la», «en la nota do#4»: la nota va después de la palabra «nota» (sola, «la» es artículo)
    for m in re.finditer(r"\bnota\s+(do|re|mi|fa|sol|la|si)(#?)(\d?)\b", texto):
        nota = m.group(1) + m.group(2) + m.group(3)
        criterios.append((f"nota {nota}", lambda f, n=nota: _es_nota(f, n)))
        usadas.update({"nota", m.group(1) + m.group(2) + m.group(3)})
        texto = texto.replace(m.group(0), " ")
    palabras = [w for w in re.findall(r"[a-z#0-9]+", texto) if w not in _RELLENO]
    for disparadores, fn, nombre in CRITERIOS:
        for d in disparadores:
            if " " in d:
                if d in texto:
                    criterios.append((nombre, fn)); usadas.update(d.split()); break
            elif d in palabras:
                criterios.append((nombre, fn)); usadas.add(d); break
    # una nota con octava suelta: «do#4», «re4»
    for w in palabras:
        if re.fullmatch(r"(do|re|mi|fa|sol|la|si)#?\d", w) and w not in usadas:
            criterios.append((f"nota {w}", lambda f, n=w: _es_nota(f, n)))
            usadas.add(w)
    no = [w for w in palabras if w not in usadas]
    return criterios, no


def buscar(sesion, descripcion, cancion=None, cuantos=20, por_cancion=False):
    """Pistas de audio cuya ficha cumple TODOS los criterios de la descripción
    (agudo, grave, brillante, opaco, tonal, ruidoso, percusivo, sostenido,
    regular, rápido, lento, mono, estéreo, satura, fuerte, suave, crece, se
    apaga, constante, intermitente, una nota…). cancion: en esa canción."""
    from . import fichas as F
    ses = F.Sesion(str(sesion))
    if not ses.hay():
        raise F.ErrorFicha("esta sesión todavía no tiene fichas: calcularlas con fichas_calcular")
    criterios, no = interpretar(descripcion)
    if not criterios:
        raise F.ErrorFicha(f"no se entendió ningún criterio en {descripcion!r}; se entienden: "
                           + ", ".join(sorted({n for _, _, n in CRITERIOS})))
    out = []
    for etiqueta, pista, cn, f in _fichas_pistas(ses, cancion, por_cancion):
        cumple = [n for n, fn in criterios if fn(f)]
        if len(cumple) == len(criterios):
            out.append({"pista": pista, "cancion": cn, "palabras": f["palabras"]})
    return {"criterios": [n for n, _ in criterios], "no_entendido": no, "encontradas": len(out), "pistas": out[:cuantos]}


def texto_parecidos(r):
    lineas = [f"referencia: {r['referencia']} — {r['palabras_referencia']}"]
    for x in r["parecidos"]:
        lineas.append(f"  {x['distancia']:5.2f}  {x['pista']}"
                      + (f" ({x['cancion']}" + (f", {x['tramo']}" if x.get("tramo") else "") + ")" if x["cancion"] else "")
                      + f"  [{x['difiere_en']}]  {x['palabras']}")
    return "\n".join(lineas)


def texto_busqueda(r):
    lineas = ["criterios: " + ", ".join(r["criterios"]) + (f"  (no entendido: {', '.join(r['no_entendido'])})" if r["no_entendido"] else "")]
    lineas.append(f"{r['encontradas']} pistas")
    for x in r["pistas"]:
        lineas.append(f"  {x['pista']}" + (f" ({x['cancion']})" if x["cancion"] else "") + f"  {x['palabras']}")
    return "\n".join(lineas)

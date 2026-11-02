"""Editar clips por compás de la canción, desde el MCP: «corre el bombo de
Aliwen un compás», «corta la pifilka en el compás 40», «silencia las
kaskawillas entre el 12 y el 20». Capa en compases sobre el modelo común; la
escritura va por `aplicar` (ya verificada en Pro Tools). Siempre plan primero;
con salida se escribe una copia nueva y se revisa.

Acciones:
- mover: los clips de la pista que empiezan en el tramo se corren N compases
  (o segundos); audio y MIDI.
- cortar: los clips de audio que pasan por el compás pedido se parten ahí
  (dos clips del mismo archivo). No se puede partir un clip con fundidos: se
  avisa y se deja.
- silenciar / quitar: los clips de audio dentro del tramo se mutean (o se
  borran); los que cruzan un borde se cortan primero, salvo que tengan
  fundidos (aviso).
Los compases se cuentan desde el marcador de la canción (1 = el primero);
sin canción, son compases de la sesión."""

from pathlib import Path

from .protools import tiempo as T
from .vistas import _normal


class ErrorEdicion(Exception):
    pass


def _reloj(comun):
    from .fichas import Reloj
    return Reloj(comun)


def _ventana(comun, reloj, cancion, desde_compas, hasta_compas):
    """(a_s, b_s, primer compás de la canción, nombre)."""
    if cancion:
        from .fichas import _ventana_cancion
        nombre, a, b = _ventana_cancion(comun, cancion)
        primero = reloj.compas_de(a)
        if b != float("inf"):
            b -= 3.0                    # lo que empieza justo antes de un marcador es de la canción que viene
        a -= 3.0
    else:
        nombre, primero, a, b = None, 1, 0.0, float("inf")
    if desde_compas:
        a = reloj.segundo_de_compas(primero + desde_compas - 1)
    if hasta_compas:
        b = reloj.segundo_de_compas(primero + hasta_compas)
    return a, b, primero, nombre


def _pistas(comun, patron):
    out = [p for p in comun["pistas"] if p["tipo"] in ("audio", "midi") and
           (p["nombre"] == patron or _normal(patron) in _normal(p["nombre"]))]
    if not out:
        raise ErrorEdicion(f"no hay una pista de audio o MIDI que se llame como {patron!r}")
    exactas = [p for p in out if p["nombre"] == patron]
    return exactas or out


def _segundos_de_compases(reloj, desde_s, compases):
    """Cuántos segundos son N compases a partir de desde_s (con el tempo vigente ahí)."""
    k = reloj.compas_de(desde_s)
    return reloj.segundo_de_compas(k + compases) - reloj.segundo_de_compas(k)


def planear(sesion, accion, pista, cancion=None, desde_compas=None, hasta_compas=None, compas=None,
            compases=0.0, segundos=0.0):
    from .comun import leer_comun
    comun = leer_comun(str(sesion))
    fs = comun["unidades"].get("frecuencia_muestreo") or 48000
    reloj = _reloj(comun)
    mapa = reloj.mapa
    a, b, primero, nombre = _ventana(comun, reloj, cancion, desde_compas, hasta_compas)
    pistas = _pistas(comun, pista)
    cambios, avisos = [], []
    if accion == "mover":
        if not compases and not segundos:
            raise ErrorEdicion("mover: indicar compases (puede ser negativo o fraccionario) o segundos")
        for p in pistas:
            for c in p.get("clips") or []:
                ini = c.get("segundos")
                if ini is None or not a <= ini < b:
                    continue
                d = segundos or _segundos_de_compases(reloj, ini, compases)
                if c.get("tipo") in ("audio", "grupo"):
                    nuevo = int(round(c["muestra"] + d * fs))
                    if nuevo < 0:
                        avisos.append(f"{p['nombre']}: un clip quedaría antes del 0, no se mueve")
                        continue
                    cambios.append({"pista": p["nombre"], "clip": c["id"], "que": "mover", "de_s": round(ini, 3),
                                    "a_s": round(nuevo / fs, 3), "muestra": nuevo})
                elif c.get("tipo") == "midi":
                    nuevo = T.tick_de_segundos(ini + d, mapa)
                    cambios.append({"pista": p["nombre"], "clip": c["id"], "que": "mover", "de_s": round(ini, 3),
                                    "a_s": round(ini + d, 3), "tick": nuevo})
    elif accion == "cortar":
        if not compas:
            raise ErrorEdicion("cortar: indicar el compás")
        t = reloj.segundo_de_compas(primero + compas - 1)
        for p in pistas:
            for c in p.get("clips") or []:
                if c.get("tipo") == "grupo":
                    ini, fin = c["muestra"] / fs, (c["muestra"] + c["largo_muestras"]) / fs
                    if ini + 0.001 < t < fin - 0.001:
                        avisos.append(f"{p['nombre']}: el grupo de clips {c['nombre']!r} cruza el corte y no se parte")
                    continue
                if c.get("tipo") != "audio":
                    continue
                ini, fin = c["muestra"] / fs, (c["muestra"] + c["largo_muestras"]) / fs
                if ini + 0.001 < t < fin - 0.001:
                    if c.get("fundidos"):
                        avisos.append(f"{p['nombre']}: el clip en {ini:.2f} s tiene fundidos y no se puede partir")
                        continue
                    cambios.append({"pista": p["nombre"], "clip": c["id"], "que": "cortar", "en_s": round(t, 3),
                                    "muestra": int(round(t * fs))})
    elif accion in ("silenciar", "quitar"):
        if b == float("inf") and not cancion:
            raise ErrorEdicion(f"{accion}: indicar la canción o los compases")
        for p in pistas:
            for c in p.get("clips") or []:
                if c.get("tipo") not in ("audio", "grupo"):
                    continue
                ini, fin = c["muestra"] / fs, (c["muestra"] + c["largo_muestras"]) / fs
                if fin <= a or ini >= b:
                    continue
                cortes = [x for x in (a, b) if ini + 0.001 < x < fin - 0.001]
                if c.get("tipo") == "grupo" and cortes:
                    avisos.append(f"{p['nombre']}: el grupo de clips {c['nombre']!r} cruza el borde del tramo; no se "
                                  f"parte ni se {'mutea' if accion == 'silenciar' else 'quita'}: desagruparlo en Pro Tools")
                    continue
                if cortes and c.get("fundidos"):
                    avisos.append(f"{p['nombre']}: el clip en {ini:.2f} s cruza el borde y tiene fundidos; "
                                  f"no se puede partir, así que se {'mutea' if accion == 'silenciar' else 'deja'} entero")
                    if accion == "quitar":
                        continue
                    cortes = []
                cambios.append({"pista": p["nombre"], "clip": c["id"], "que": accion, "de_s": round(max(ini, a), 3),
                                "a_s": round(min(fin, b), 3), "cortes": [int(round(x * fs)) for x in cortes]})
    else:
        raise ErrorEdicion("accion: mover, cortar, silenciar o quitar")
    return {"sesion": str(sesion), "accion": accion, "pistas": [p["nombre"] for p in pistas], "cancion": nombre,
            "tramo_s": [round(a, 3), None if b == float("inf") else round(b, 3)], "cambios": cambios, "avisos": avisos}


def _partir(c, muestra):
    """Dos clips nuevos (sin id) a partir de c, cortado en la muestra absoluta."""
    k = muestra - c["muestra"]
    base = {x: v for x, v in c.items() if x not in ("id", "segundos", "posicion", "fundidos", "muestra", "desde_muestra",
                                                      "largo_muestras", "nombre")}
    izq = {**base, "muestra": c["muestra"], "desde_muestra": int(c.get("desde_muestra") or 0), "largo_muestras": k}
    der = {**base, "muestra": muestra, "desde_muestra": int(c.get("desde_muestra") or 0) + k,
           "largo_muestras": c["largo_muestras"] - k}
    return izq, der


def escribir(plan, salida):
    """Aplica el plan en una copia nueva y la revisa. Silenciar o quitar con
    cortes va en dos pasadas (primero se corta, después se mutea o borra lo que
    quedó adentro), porque un clip nuevo no puede nacer muteado."""
    import os
    import tempfile
    from .comun import leer_comun, aplicar
    from .vistas import tras_escribir
    if not plan["cambios"]:
        raise ErrorEdicion("el plan no tiene cambios")
    salida = Path(salida)
    comun = leer_comun(plan["sesion"])
    fs = comun["unidades"].get("frecuencia_muestreo") or 48000
    por_pista = {}
    for x in plan["cambios"]:
        por_pista.setdefault(x["pista"], {})[x["clip"]] = x
    segunda = []                       # (pista, de_s, a_s, que) para la segunda pasada
    avisos = list(plan["avisos"])
    for p in comun["pistas"]:
        cambios = por_pista.get(p["nombre"])
        if not cambios:
            continue
        nuevos = []
        for c in p["clips"]:
            x = cambios.get(c.get("id"))
            if not x:
                nuevos.append(c)
                continue
            if x["que"] == "mover":
                if "muestra" in x:
                    c["muestra"] = x["muestra"]
                else:
                    c["tick"] = x["tick"]
                nuevos.append(c)
            elif x["que"] == "cortar":
                nuevos += list(_partir(c, x["muestra"]))
            elif x["cortes"]:                       # silenciar / quitar: cortar ahora, actuar después
                pedazos = [c]
                for m in sorted(x["cortes"]):
                    pedazos += list(_partir(pedazos.pop(), m))
                nuevos += pedazos
                segunda.append((p["nombre"], x["de_s"], x["a_s"], x["que"]))
            else:                                   # entero adentro
                if x["que"] == "quitar" and any(f.get("tipo") == "cruzado" for f in c.get("fundidos") or []):
                    avisos.append(f"{p['nombre']}: el clip en {c['muestra'] / fs:.2f} s tiene un fundido cruzado y no se "
                                  f"puede borrar; queda muteado")
                    nuevos.append(dict(c, mute=True))
                elif x["que"] == "quitar":
                    continue
                else:
                    nuevos.append(dict(c, mute=True))
        p["clips"] = nuevos
    if not segunda:
        aplicar(comun, plan["sesion"], str(salida))
    else:
        fd, tmp = tempfile.mkstemp(suffix=".ptx", dir=str(salida.parent))
        os.close(fd)
        os.unlink(tmp)
        try:
            aplicar(comun, plan["sesion"], tmp)
            c2 = leer_comun(tmp)
            for p in c2["pistas"]:
                tramos = [(a, b, q) for n, a, b, q in segunda if n == p["nombre"]]
                if not tramos:
                    continue
                nuevos = []
                for c in p["clips"]:
                    if c.get("tipo") != "audio":
                        nuevos.append(c)
                        continue
                    ini = c["muestra"] / fs
                    que = next((q for a, b, q in tramos if a - 0.002 <= ini < b - 0.002), None)
                    if que == "quitar" and not any(f.get("tipo") == "cruzado" for f in c.get("fundidos") or []):
                        continue
                    nuevos.append(dict(c, mute=True) if que else c)
                p["clips"] = nuevos
            aplicar(c2, tmp, str(salida))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    n = len(plan["cambios"])
    r = tras_escribir(plan["sesion"], str(salida), f"{plan['accion']} {n} clips de {', '.join(plan['pistas'])}"
                      + (f" en {plan['cancion']}" if plan["cancion"] else ""))
    r["avisos"] = avisos
    return r


def texto(plan):
    L = [f"{plan['accion']} en {', '.join(plan['pistas'])}" + (f" ({plan['cancion']})" if plan["cancion"] else "")
         + f": {len(plan['cambios'])} clips"]
    for x in plan["cambios"][:40]:
        if x["que"] == "mover":
            L.append(f"  {x['pista']}: {x['de_s']} → {x['a_s']} s")
        elif x["que"] == "cortar":
            L.append(f"  {x['pista']}: cortar en {x['en_s']} s")
        else:
            L.append(f"  {x['pista']}: {x['que']} {x['de_s']}–{x['a_s']} s" + (" (se corta en el borde)" if x["cortes"] else ""))
    if len(plan["cambios"]) > 40:
        L.append(f"  … {len(plan['cambios']) - 40} más")
    L += [f"  aviso: {a}" for a in plan["avisos"]]
    return "\n".join(L)

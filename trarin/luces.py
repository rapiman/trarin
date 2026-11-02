"""Luces del show por MIDI: tres pistas MIDI nuevas, una por canal, armadas
desde los golpes de la percusión de cada canción (golpes_a_midi) según la
convención del show:

- canal 1: la luz principal, kultrun o bombo de cada canción;
- canales 2 y 3: una pifilka cada uno, a la contra (L al 2, R al 3; con una
  sola pifilka, sus golpes se reparten alternados); si la canción no tiene
  pifilkas, otra percusión repetida en los dos (o, si se indica, los redobles
  del bombo).

Elige las pistas por nombre y por cuánto audio tienen en la canción; todo se
puede fijar a mano por canción. Plan primero; con salida escribe la sesión
nueva (pistas «Luz 1», «Luz 2», «Luz 3», un clip por canción) y, si puede,
les pone el puerto y el canal de salida."""

import re

from .protools import tiempo as T
from .vistas import _normal

PRINCIPAL = ("kultr", "cultr", "bombo", "kick", "bass drum")
PIFILKA = ("pif", "puf")
OTRA = ("kask", "cascab", "semilla", "uñ", "un", "perc", "caja", "tambor")
NOMBRES = ("Luz 1", "Luz 2", "Luz 3")


def _audio_en(p, fs, a, b):
    return sum(k["largo_muestras"] for k in p.get("clips") or [] if k.get("tipo") == "audio" and not k.get("mute")
               and a <= k["muestra"] / fs < b)


def _elegir(comun, fs, a, b, claves, excluir=(), cuantos=1):
    cand = [(_audio_en(p, fs, a, b), p["nombre"]) for p in comun["pistas"] if p["tipo"] == "audio"
            and p["nombre"] not in excluir and any(k in _normal(p["nombre"]) for k in claves)
            and not (p.get("mezcla") or {}).get("mute")]
    cand = sorted([c for c in cand if c[0] > 0], reverse=True)
    return [n for _, n in cand[:cuantos]]


def planear(sesion, puerto=None, por_cancion=None, grilla="1/16", nota=36, velocidad_minima=1):
    """por_cancion: {canción: {"principal": pista, "pifilkas": [pista, pista] | [pista],
    "otra": pista}} para fijar a mano. Devuelve el plan con las notas por canal."""
    from . import golpes as G
    from .comun import leer_comun
    from .fichas import _ventana_cancion
    from .vistas import _canciones
    sesion = str(sesion)
    comun = leer_comun(sesion)
    fs = comun["unidades"].get("frecuencia_muestreo") or 48000
    fijo = por_cancion or {}
    canales = {1: [], 2: [], 3: []}
    detalle, avisos = [], []
    for nombre, _ in _canciones(comun):
        _, a, b = _ventana_cancion(comun, nombre)
        a -= 3.0
        if b != float("inf"):
            b -= 3.0
        f = next((v for k, v in fijo.items() if _normal(k) in _normal(nombre)), {})
        principal = f.get("principal") or (_elegir(comun, fs, a, b, PRINCIPAL) or [None])[0]
        pif = f.get("pifilkas")
        if pif is None:
            todas = _elegir(comun, fs, a, b, PIFILKA, cuantos=6)
            izq = [x for x in todas if re.search(r"\b(l|izq)\b", _normal(x))]
            der = [x for x in todas if re.search(r"\b(r|der)\b", _normal(x))]
            pif = ([izq[0]] if izq else []) + ([der[0]] if der else [])
            if not pif and todas:
                pif = [todas[0]]
        otra = f.get("otra")
        if not pif and not otra:
            otra = (_elegir(comun, fs, a, b, OTRA, excluir=[principal] if principal else ()) or [None])[0]
        fila = {"cancion": nombre, "principal": principal, "pifilkas": pif, "otra": otra if not pif else None, "notas": {}}
        if principal:
            try:
                pl = G.planear(sesion, principal, cancion=nombre, grilla=grilla, nota=nota)
                canales[1] += [dict(x, cancion=nombre) for x in pl["notas"]]
                fila["notas"][1] = len(pl["notas"])
            except Exception as e:
                avisos.append(f"{nombre}: principal {principal}: {e}")
        else:
            avisos.append(f"{nombre}: no se encontró kultrun ni bombo para la luz principal")
        fuentes = pif or ([otra] if otra else [])
        if not fuentes:
            avisos.append(f"{nombre}: sin pifilkas ni otra percusión para las luces 2 y 3")
        elif len(fuentes) >= 2:
            for canal, pista in zip((2, 3), fuentes[:2]):
                try:
                    pl = G.planear(sesion, pista, cancion=nombre, grilla=grilla, nota=nota)
                    canales[canal] += [dict(x, cancion=nombre) for x in pl["notas"]]
                    fila["notas"][canal] = len(pl["notas"])
                except Exception as e:
                    avisos.append(f"{nombre}: {pista}: {e}")
        else:
            try:
                pl = G.planear(sesion, fuentes[0], cancion=nombre, grilla=grilla, nota=nota)
                for i, x in enumerate(pl["notas"]):          # a la contra: alternados
                    canales[2 if i % 2 == 0 else 3].append(dict(x, cancion=nombre))
                fila["notas"][2] = (len(pl["notas"]) + 1) // 2
                fila["notas"][3] = len(pl["notas"]) // 2
                fila["alternado"] = fuentes[0]
            except Exception as e:
                avisos.append(f"{nombre}: {fuentes[0]}: {e}")
        detalle.append(fila)
    if puerto is None:
        try:
            from .protools.descifrado import leer_descifrado
            from .protools.salidas_midi import leer_salidas
            usados = {}
            for s in leer_salidas(leer_descifrado(sesion)).values():
                if s:
                    usados[s["puerto"]] = usados.get(s["puerto"], 0) + 1
            puerto = max(usados, key=usados.get) if usados else None
        except Exception:
            puerto = None
    return {"sesion": sesion, "puerto": puerto, "canciones": detalle, "avisos": avisos,
            "notas": {c: len(v) for c, v in canales.items()}, "_notas": canales, "grilla": grilla, "nota": nota}


def escribir(plan, salida):
    """Escribe la sesión con las tres pistas de luces; intenta ponerles puerto y
    canal. Devuelve el resultado de tras_escribir más {salidas_midi, avisos}."""
    from pathlib import Path
    from .comun import leer_comun, aplicar
    from .vistas import tras_escribir, _canciones
    from .fichas import _ventana_cancion
    comun = leer_comun(plan["sesion"])
    mapa = T.MapaTempo(comun["tempo"])
    existentes = {p["nombre"] for p in comun["pistas"]}
    avisos = list(plan["avisos"])
    total = 0
    for canal, nombre in zip((1, 2, 3), NOMBRES):
        notas = plan["_notas"][canal]
        if not notas:
            continue
        if nombre in existentes:
            raise ValueError(f"ya existe la pista {nombre!r}; borrarla o renombrarla antes")
        clips = []
        for cn, _ in _canciones(comun):
            mias = sorted((x for x in notas if x["cancion"] == cn), key=lambda x: x["tick"])
            if not mias:
                continue
            inicio = mias[0]["tick"] // T.TICKS_NEGRA * T.TICKS_NEGRA
            fin = max(x["tick"] + x["largo_ticks"] for x in mias)
            clips.append({"tipo": "midi", "nombre": f"{nombre} {cn}", "tick": int(inicio), "largo_ticks": int(fin - inicio),
                          "notas": [{"tick": x["tick"] - inicio, "largo_ticks": x["largo_ticks"], "nota": x["nota"],
                                     "velocidad": x["velocidad"]} for x in mias]})
            total += len(mias)
        comun["pistas"].append({"id": nombre, "nombre": nombre, "tipo": "midi", "clips": clips})
    if not total:
        raise ValueError("no hay golpes para ninguna luz")
    aplicar(comun, plan["sesion"], str(salida))
    salidas = None
    if plan.get("puerto"):
        try:
            from .protools.descifrado import leer_descifrado
            from .protools.reconstruccion import cifrar
            from .protools.salidas_midi import cambiar_salidas, leer_salidas
            d = leer_descifrado(str(salida))
            cambios = {n: {"canal": c, "puerto": plan["puerto"]} for c, n in zip((1, 2, 3), NOMBRES) if plan["_notas"][c]}
            d2 = cambiar_salidas(d, cambios)
            Path(salida).write_bytes(cifrar(d2))
            salidas = {n: leer_salidas(d2).get(n) for n in cambios}
        except Exception as e:
            avisos.append(f"no se pudo poner puerto y canal a las pistas de luces ({e}); asignarlos en Pro Tools: "
                          f"{plan['puerto']} canales 1, 2 y 3")
    r = tras_escribir(plan["sesion"], str(salida), f"luces: {total} notas en {', '.join(n for c, n in zip((1, 2, 3), NOMBRES) if plan['_notas'][c])}")
    r.update({"notas": total, "salidas_midi": salidas, "avisos": avisos})
    return r


def texto(plan):
    L = [f"luces por MIDI (grilla {plan['grilla']}, nota {plan['nota']}" + (f", puerto {plan['puerto']}" if plan.get("puerto") else "") + ")"]
    for c in plan["canciones"]:
        f = c["notas"]
        L.append(f"  {c['cancion']}: canal 1 ← {c['principal'] or '-'} ({f.get(1, 0)} notas); "
                 + (f"canales 2 y 3 ← {c['alternado']} alternado ({f.get(2, 0)}/{f.get(3, 0)})" if c.get("alternado")
                    else f"canal 2 ← {(c['pifilkas'] or [None, None])[0] or c['otra'] or '-'} ({f.get(2, 0)}); canal 3 ← "
                         f"{(c['pifilkas'] or [None, None])[1] if c['pifilkas'] and len(c['pifilkas']) > 1 else c['otra'] or '-'} ({f.get(3, 0)})"))
    L.append("  total: " + ", ".join(f"canal {c} {n} notas" for c, n in plan["notas"].items()))
    L += [f"  aviso: {a}" for a in plan["avisos"]]
    return "\n".join(L)

"""Plantillas de reglas con nombre (fase 5): las categorías, buses, auxiliares,
grupos y reglas MIDI de un show, guardadas para el próximo. Las que vienen con
trarin están en esta carpeta (`rapizuam.json`); las propias van en
`~/.trarin/plantillas/` (o TRARIN_PLANTILLAS). Lo propio de cada show
(canciones, pistas por ruta, qué borrar, carpeta de Live) no va en la
plantilla: se agrega al armar las reglas.

`armar(nombre, extra)` devuelve las reglas listas para consolidar o grupos: la
plantilla con `extra` encima (las listas se suman, lo demás se reemplaza).
En las herramientas MCP, `reglas="plantilla:rapizuam"` (o
`"plantilla:rapizuam+ruta.json"`) usa una plantilla directamente."""

import json
import os
import re
from pathlib import Path

PROPIAS = {"canciones", "live_buscar", "live_carpeta", "rutas", "borrar", "dejar"}     # lo de cada show
_AQUI = Path(__file__).parent


def carpeta_propia():
    return Path(os.environ.get("TRARIN_PLANTILLAS") or Path.home() / ".trarin" / "plantillas")


def _ruta(nombre):
    n = re.sub(r"[^a-z0-9_-]+", "_", nombre.lower().strip())
    propia = carpeta_propia() / f"{n}.json"
    if propia.exists():
        return propia
    incluida = _AQUI / f"{n}.json"
    if incluida.exists():
        return incluida
    return None


def listar():
    out = []
    vistos = set()
    for origen, c in (("propia", carpeta_propia()), ("incluida", _AQUI)):
        if not c.exists():
            continue
        for f in sorted(c.glob("*.json")):
            if f.stem in vistos:
                continue
            vistos.add(f.stem)
            try:
                d = json.loads(f.read_text(encoding="utf-8-sig"))
                desc = d.get("descripcion", "")
            except Exception:
                desc = "(no se pudo leer)"
            out.append({"nombre": f.stem, "origen": origen, "descripcion": desc, "archivo": str(f)})
    return out


def leer(nombre):
    r = _ruta(nombre)
    if r is None:
        raise ValueError(f"no hay una plantilla {nombre!r} (hay: {', '.join(x['nombre'] for x in listar()) or 'ninguna'})")
    return json.loads(r.read_text(encoding="utf-8-sig"))


def guardar(nombre, reglas, descripcion=None, solo_general=True):
    """Guarda unas reglas (dict o ruta a un JSON) como plantilla propia. Con
    solo_general, deja fuera lo propio del show (PROPIAS)."""
    if isinstance(reglas, str):
        reglas = json.loads(Path(reglas).read_text(encoding="utf-8-sig"))
    d = {k: v for k, v in reglas.items() if not (solo_general and k in PROPIAS)}
    if descripcion:
        d = {"descripcion": descripcion, **{k: v for k, v in d.items() if k != "descripcion"}}
    c = carpeta_propia()
    c.mkdir(parents=True, exist_ok=True)
    n = re.sub(r"[^a-z0-9_-]+", "_", nombre.lower().strip())
    f = c / f"{n}.json"
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(f)


def armar(nombre, extra=None):
    """Las reglas de la plantilla con 'extra' (dict o ruta a un JSON) encima:
    las listas se suman (sin repetir), los diccionarios se mezclan y lo demás
    se reemplaza. Quita 'descripcion', 'buses' y 'luces' (informativos)."""
    base = {k: v for k, v in leer(nombre).items() if k not in ("descripcion", "buses", "luces")}
    if isinstance(extra, str):
        extra = json.loads(Path(extra).read_text(encoding="utf-8-sig"))
    for k, v in (extra or {}).items():
        if isinstance(v, list) and isinstance(base.get(k), list):
            base[k] = base[k] + [x for x in v if x not in base[k]]
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def resolver(reglas):
    """Para las herramientas: "plantilla:nombre" o "plantilla:nombre+extra.json"
    → reglas armadas; cualquier otra cosa vuelve tal cual."""
    if isinstance(reglas, str) and reglas.startswith("plantilla:"):
        resto = reglas[len("plantilla:"):]
        nombre, _, extra = resto.partition("+")
        return armar(nombre.strip(), extra.strip() or None)
    return reglas

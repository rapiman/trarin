"""Notas del proyecto: una carpeta `trarin` junto a la sesión, con `notas.md`
(decisiones, lo hecho y lo pendiente). Las herramientas que escriben agregan
una línea en «Hecho»; una conversación nueva parte leyéndolo. Más adelante
la misma carpeta guarda las fichas de sonido."""

from datetime import datetime
from pathlib import Path

_PLANTILLA = """# Notas de {nombre}

Contexto del proyecto para retomarlo después: lo que se decidió, lo que se
hizo y lo que falta. trarin agrega una línea en «Hecho» cada vez que escribe
una sesión de esta carpeta.

## Decisiones

## Hecho

## Pendiente
"""


def carpeta(sesion):
    return Path(sesion).resolve().parent / "trarin"


def archivo(sesion):
    return carpeta(sesion) / "notas.md"


def leer(sesion):
    a = archivo(sesion)
    return a.read_text(encoding="utf-8") if a.exists() else ""


def _asegurar(sesion):
    a = archivo(sesion)
    if not a.exists():
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_text(_PLANTILLA.format(nombre=Path(sesion).resolve().parent.name), encoding="utf-8")
    return a


def anotar(sesion, texto, seccion="Hecho"):
    """Agrega una línea (con fecha) al final de la sección pedida (Decisiones,
    Hecho o Pendiente). Devuelve la ruta del archivo."""
    a = _asegurar(sesion)
    lineas = a.read_text(encoding="utf-8").splitlines()
    titulo = f"## {seccion}"
    if titulo not in lineas:
        lineas += ["", titulo]
    i = lineas.index(titulo) + 1
    while i < len(lineas) and not lineas[i].startswith("## "):
        i += 1
    while i > 0 and lineas[i - 1].strip() == "" and lineas[i - 1] != titulo:
        i -= 1
    linea = f"- {datetime.now():%Y-%m-%d %H:%M} — {texto.strip()}"
    lineas.insert(i, linea)
    a.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return str(a)

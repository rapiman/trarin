"""Trabajar sobre una sola sesión con respaldos: en la carpeta del show hay un
solo archivo de sesión y todo lo demás va a `Session File Backups` (la misma
carpeta que usa Pro Tools).

- `reemplazar(sesion, nueva, motivo)`: respalda la sesión vigente como
  `<nombre>.trarin.<fecha>_<n>.ptx` y pone la nueva en su lugar (las
  herramientas escriben siempre un archivo nuevo; esto lo convierte en «el
  cambio quedó en la sesión»). Anota en las notas.
- `deshacer(sesion, cual=None)`: vuelve al último respaldo de trarin (o al
  indicado); lo que había pasa a `<nombre>.deshecho.<fecha>.ptx`, así no se
  pierde nada y se puede rehacer a mano.
- `listar(sesion)`: los respaldos, del más nuevo al más viejo, con la línea de
  las notas que les corresponde si se encuentra."""

import re
import shutil
from datetime import datetime
from pathlib import Path

from . import notas


class ErrorRespaldo(Exception):
    pass


def carpeta(sesion):
    return Path(sesion).resolve().parent / "Session File Backups"


def _patron(sesion):
    stem = re.escape(Path(sesion).stem)
    return re.compile(rf"^{stem}\.(trarin|deshecho)\.(\d{{8}})(?:_(\d+))?(?:_(\d{{6}}))?\.ptx$")


def listar(sesion):
    s = Path(sesion).resolve()
    c = carpeta(s)
    out = []
    if c.exists():
        pat = _patron(s)
        for f in c.glob(f"{s.stem}.*.ptx"):
            m = pat.match(f.name)
            if not m:
                continue
            st = f.stat()
            out.append({"archivo": str(f), "nombre": f.name, "tipo": m.group(1),
                        "modificado": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "bytes": st.st_size, "_mtime": st.st_mtime})
    out.sort(key=lambda x: -x["_mtime"])
    texto = notas.leer(s)
    for x in out:
        linea = next((l for l in texto.splitlines() if x["nombre"] in l), None)
        if linea:
            x["nota"] = linea.lstrip("- ").strip()
        x.pop("_mtime")
    return out


def _nombre_libre(c, stem, tipo):
    hoy = datetime.now().strftime("%Y%m%d")
    n = 1
    while True:
        f = c / f"{stem}.{tipo}.{hoy}_{n}.ptx"
        if not f.exists():
            return f
        n += 1


def reemplazar(sesion, nueva, motivo=""):
    """Respalda 'sesion' y pone 'nueva' en su lugar (la mueve). Devuelve
    {sesion, respaldo, nota}."""
    s, n = Path(sesion).resolve(), Path(nueva).resolve()
    if not s.exists():
        raise ErrorRespaldo(f"no existe {s}")
    if not n.exists():
        raise ErrorRespaldo(f"no existe {n}")
    if s == n:
        raise ErrorRespaldo("la sesión y la nueva son el mismo archivo")
    c = carpeta(s)
    c.mkdir(parents=True, exist_ok=True)
    respaldo = _nombre_libre(c, s.stem, "trarin")
    shutil.move(str(s), str(respaldo))
    try:
        shutil.move(str(n), str(s))
    except Exception:
        shutil.move(str(respaldo), str(s))       # dejar todo como estaba
        raise
    linea = f"{motivo or 'cambio'}: {n.name} pasa a ser {s.name}; la anterior quedó en {respaldo.name}"
    nota = notas.anotar(s, linea)
    return {"sesion": str(s), "respaldo": str(respaldo), "nota": nota}


def deshacer(sesion, cual=None):
    """Vuelve al último respaldo de trarin (o a 'cual': nombre de archivo o
    parte). Lo que había queda como <nombre>.deshecho.<fecha>_<hora>.ptx."""
    s = Path(sesion).resolve()
    resp = [x for x in listar(s) if x["tipo"] == "trarin"]
    if cual:
        resp = [x for x in resp if cual in x["nombre"]]
    if not resp:
        raise ErrorRespaldo("no hay un respaldo de trarin al que volver" + (f" que contenga {cual!r}" if cual else ""))
    elegido = Path(resp[0]["archivo"])
    c = carpeta(s)
    deshecho = c / f"{s.stem}.deshecho.{datetime.now():%Y%m%d_%H%M%S}.ptx"
    if s.exists():
        shutil.move(str(s), str(deshecho))
    shutil.move(str(elegido), str(s))
    linea = f"deshacer: se volvió a {elegido.name}; lo que había quedó en {deshecho.name}"
    nota = notas.anotar(s, linea)
    return {"sesion": str(s), "volvio_a": elegido.name, "deshecho": str(deshecho), "nota": nota,
            "quedan": sum(1 for x in listar(s) if x["tipo"] == "trarin")}


def texto(lista):
    if not lista:
        return "sin respaldos de trarin"
    return "\n".join(f"{x['modificado']}  {x['tipo']:8}  {x['nombre']}" + (f"  — {x['nota']}" if x.get("nota") else "")
                     for x in lista)

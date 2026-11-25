"""Caché en disco de lo que cuesta calcular a partir de una sesión y no cambia
mientras el archivo no cambie (hoy: el modelo común). Vive en ~/.trarin/cache
(o TRARIN_CACHE); la llave lleva la ruta absoluta, la fecha de modificación y
el tamaño, así una sesión reescrita nunca lee una caché vieja. Se guarda con
pickle, pero al leer solo se aceptan datos (ver _SoloDatos): un archivo que
intente cargar código se descarta. Cada lectura devuelve un objeto nuevo.
TRARIN_SIN_CACHE=1 la apaga.
Se poda sola: más de MAXIMO archivos, se borran los más viejos."""

import hashlib
import os
import pickle
from pathlib import Path

VERSION = 4            # subir cuando cambie la forma del modelo: invalida todo (4: tipo «instrumento»)
MAXIMO = 200


def carpeta():
    return Path(os.environ.get("TRARIN_CACHE") or Path.home() / ".trarin" / "cache")


def activa():
    return os.environ.get("TRARIN_SIN_CACHE", "").strip().lower() not in ("1", "si", "true")


def _llave(ruta, que):
    p = Path(ruta).resolve()
    st = p.stat()
    base = f"{VERSION}|{que}|{p}|{st.st_mtime_ns}|{st.st_size}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def leer(ruta, que):
    if not activa():
        return None
    try:
        f = carpeta() / f"{_llave(ruta, que)}.pkl"
        if not f.exists():
            return None
        with open(f, "rb") as h:
            return _SoloDatos(h).load()
    except Exception:              # una caché rota, ajena o ilegible nunca frena la lectura
        return None


class _SoloDatos(pickle.Unpickler):
    """Lee solo datos (diccionarios, listas, tuplas, textos, números, bytes). El
    modelo común no lleva otra cosa, así que cualquier archivo que pida cargar
    una clase o una función no es una caché de trarin: se rechaza en vez de
    ejecutarlo (un pickle cualquiera puede correr código al abrirse)."""

    def find_class(self, modulo, nombre):
        raise pickle.UnpicklingError(f"la caché solo guarda datos; no se carga {modulo}.{nombre}")


def guardar(ruta, que, objeto):
    if not activa():
        return
    try:
        c = carpeta()
        c.mkdir(parents=True, exist_ok=True)
        f = c / f"{_llave(ruta, que)}.pkl"
        tmp = f.with_suffix(".tmp")
        with open(tmp, "wb") as h:
            pickle.dump(objeto, h, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, f)
        _podar(c)
    except Exception:
        pass


def _podar(c):
    archivos = sorted(c.glob("*.pkl"), key=lambda x: x.stat().st_mtime)
    for x in archivos[:-MAXIMO]:
        try:
            x.unlink()
        except OSError:
            pass


def vaciar():
    c = carpeta()
    n = 0
    if c.exists():
        for x in c.glob("*.pkl"):
            x.unlink()
            n += 1
    return n

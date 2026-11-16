"""Errores de lectura con un mensaje que se entiende.

Los lectores recorren bytes con posiciones y largos sacados del propio
archivo: con una sesión dañada, cortada o de una versión que trarin no conoce,
lo que falla por dentro es un `struct.error`, un índice o una división por
cero. `lectura_clara` los convierte en `ErrorSesion`, que dice qué archivo no
se pudo leer y deja el detalle técnico al final (sirve para reportarlo)."""

import functools
import traceback
from pathlib import Path


class ErrorSesion(Exception):
    """La sesión no se pudo leer: está dañada, incompleta o es de una versión
    que trarin no conoce."""


_RAIZ = Path(__file__).resolve().parent


def _donde(e):
    """«archivo.py:línea» del punto de trarin donde saltó el error."""
    for marco in reversed(traceback.extract_tb(e.__traceback__)):
        try:
            return f"{Path(marco.filename).resolve().relative_to(_RAIZ).as_posix()}:{marco.lineno}"
        except ValueError:
            continue
    return "?"


def lectura_clara(programa):
    """Decorador para `leer(ruta)` de cada programa. Deja pasar los errores del
    sistema (no existe, sin permiso) y los propios de trarin, que ya dicen qué
    pasó; el resto sale como ErrorSesion."""
    def decorar(f):
        @functools.wraps(f)
        def g(ruta, *args, **kwargs):
            try:
                return f(ruta, *args, **kwargs)
            except (OSError, MemoryError, ErrorSesion):
                raise
            except Exception as e:
                if type(e).__module__.split(".")[0] == "trarin":
                    raise
                raise ErrorSesion(
                    f"{Path(str(ruta)).name}: no se pudo leer como sesión de {programa}; está dañada, incompleta o "
                    f"es de una versión que trarin no conoce (detalle: {type(e).__name__}: {e}, en {_donde(e)})") from e
        return g
    return decorar

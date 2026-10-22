"""Servidor MCP de trarin (stdio): leer sesiones, consultar la compatibilidad,
planear y armar un show, y escribir cambios del modelo común.

Requiere el paquete `mcp` (pip install "trarin[mcp]"). Se inicia con
`python -m trarin mcp`. Nunca sobrescribe archivos: las herramientas que
escriben piden una salida nueva y fallan si ya existe.
"""

import json
from pathlib import Path


def _servidor():
    try:
        from mcp.server.mcpserver import MCPServer as Servidor          # mcp 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Servidor          # mcp 1.x
        except ImportError as e:
            raise SystemExit('el servidor MCP requiere el paquete mcp: pip install "trarin[mcp]"') from e
    return Servidor(
        "trarin",
        instructions=("Lee sesiones de Pro Tools (.ptx), Ableton (.als) y FL Studio (.flp) en el modelo "
                      "común trarin/1, consulta qué tan compatibles son varias canciones, planea y arma "
                      "un show que las junta en una sesión de Pro Tools, y escribe cambios del modelo "
                      "común sobre una copia de la sesión. Nunca modifica las sesiones originales."))


def _error_herramienta():
    try:
        from mcp.server.mcpserver.exceptions import ToolError           # mcp 2.x
    except ImportError:
        from mcp.server.fastmcp.exceptions import ToolError             # mcp 1.x
    return ToolError


def _claro(f):
    """Los errores de trarin llegan al cliente con su mensaje."""
    import functools
    ToolError = _error_herramienta()

    @functools.wraps(f)
    def g(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"{type(e).__name__}: {e}") from e
    return g


def _salida_nueva(salida):
    p = Path(salida)
    if p.exists():
        raise ValueError(f"{p} ya existe: elija otro nombre")
    if not p.parent.exists():
        raise ValueError(f"no existe la carpeta {p.parent}")
    return p


def _sin_internos(plan):
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def crear():
    srv = _servidor()

    @srv.tool()
    @_claro
    def leer_sesion(ruta: str, modelo: str = "comun") -> dict:
        """Lee una sesión (.ptx, .als o .flp). modelo: "comun" (trarin/1, igual
        para los tres programas) o "nativo" (todo lo que trarin entiende del
        archivo)."""
        from .comun import leer_comun
        from .__main__ import leer
        if modelo == "comun":
            return leer_comun(ruta)
        if modelo == "nativo":
            return json.loads(json.dumps(leer(ruta), default=str))
        raise ValueError('modelo debe ser "comun" o "nativo"')

    @srv.tool()
    @_claro
    def compatibilidad(sesiones: list[str]) -> dict:
        """Consulta, antes de armar un show, qué tan compatibles son varias
        sesiones de Pro Tools (en el orden del show): frecuencias, tempos,
        pistas que coinciden (propuesta de match), ruteo y plugins. Devuelve el
        informe y su versión en texto."""
        from . import compatibilidad as C
        inf = C.informe(sesiones)
        return {"informe": inf, "texto": C.texto(inf)}

    @srv.tool()
    @_claro
    def planear_show(show: dict) -> dict:
        """Plan de tiempo de un show sin escribir nada: inicio de cada canción,
        mapa de tempo, compases y marcadores. 'show' es el mismo JSON que usa
        `trarin show` ({"canciones": [{"sesion": ...}], "separacion": ...,
        "cambio_tempo": ..., "base": ..., "match": ..., "buses": ...,
        "copiar_solo": ...})."""
        from .show import planear, texto
        plan = planear(show)
        return {"plan": _sin_internos(plan), "texto": texto(plan)}

    @srv.tool()
    @_claro
    def armar_show(show: dict, salida: str) -> dict:
        """Escribe la sesión de Pro Tools del show en 'salida' (un .ptx que no
        exista). Devuelve la ruta y los avisos (lo que no se pudo copiar)."""
        from .show import armar
        ruta, avisos = armar(show, str(_salida_nueva(salida)))
        return {"salida": str(ruta), "avisos": avisos}

    @srv.tool()
    @_claro
    def escribir_sesion(comun: dict, plantilla: str, salida: str) -> dict:
        """Aplica un modelo común editado (leído antes con leer_sesion) sobre una
        copia de 'plantilla' y la guarda en 'salida' (un archivo que no exista,
        con la misma extensión que la plantilla)."""
        from .comun import aplicar
        p = _salida_nueva(salida)
        if p.suffix.lower() != Path(plantilla).suffix.lower():
            raise ValueError("la salida debe tener la misma extensión que la plantilla")
        aplicar(comun, plantilla, str(p))
        return {"salida": str(p)}

    return srv


def main():
    crear().run()

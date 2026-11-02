"""Instalar el servidor MCP de trarin en Claude Desktop y verificar lo que hace
falta: `trarin instalar` escribe (o actualiza) la entrada "trarin" en
`claude_desktop_config.json` apuntando al Python que corre este comando
(`python -m trarin mcp`), revisa las dependencias (mcp, numpy, soxr, lameenc,
pyyaml) y dice qué falta y con qué se instala. Con `--revisar` solo revisa."""

import importlib
import json
import os
import sys
from pathlib import Path

DEPENDENCIAS = (
    ("mcp", "mcp", 'pip install "trarin[mcp]"', "el servidor MCP"),
    ("numpy", "numpy", 'pip install "trarin[audio]"', "fichas, escuchar, bpm, golpes, nivelar"),
    ("soxr", "soxr", 'pip install "trarin[audio]"', "conversión de frecuencia y escuchar"),
    ("lameenc", "lameenc", 'pip install "trarin[mp3]"', "escuchar en MP3"),
    ("yaml", "pyyaml", 'pip install "trarin[yaml]"', "shows descritos en YAML (opcional)"),
)


def rutas_config():
    """Rutas posibles del archivo de configuración de Claude Desktop, la
    primera que exista primero."""
    out = []
    if sys.platform == "win32":
        app = os.environ.get("APPDATA")
        if app:
            out.append(Path(app) / "Claude" / "claude_desktop_config.json")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            for p in (Path(local) / "Packages").glob("Claude_*"):
                out.append(p / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
    elif sys.platform == "darwin":
        out.append(Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    else:
        out.append(Path.home() / ".config" / "Claude" / "claude_desktop_config.json")
    existen = [r for r in out if r.exists()]
    return existen + [r for r in out if r not in existen]


def revisar():
    out = {"python": sys.executable, "version": sys.version.split()[0], "dependencias": [], "falta": []}
    for modulo, paquete, como, para in DEPENDENCIAS:
        try:
            m = importlib.import_module(modulo)
            out["dependencias"].append({"paquete": paquete, "ok": True, "version": getattr(m, "__version__", None), "para": para})
        except Exception:
            out["dependencias"].append({"paquete": paquete, "ok": False, "instalar": como, "para": para})
            out["falta"].append(paquete)
    try:
        import trarin
        out["trarin"] = str(Path(trarin.__file__).parent)
    except Exception:
        out["trarin"] = None
    rutas = rutas_config()
    out["config"] = str(rutas[0]) if rutas else None
    out["config_existe"] = bool(rutas) and rutas[0].exists()
    if out["config_existe"]:
        try:
            d = json.loads(rutas[0].read_text(encoding="utf-8-sig"))
            out["entrada_actual"] = (d.get("mcpServers") or {}).get("trarin")
        except Exception as e:
            out["entrada_actual"] = f"(no se pudo leer: {e})"
    return out


def instalar(config=None, nombre="trarin", python=None):
    """Escribe la entrada en el archivo de configuración (lo crea si falta).
    Devuelve {config, entrada, antes}."""
    ruta = Path(config) if config else rutas_config()[0]
    d = {}
    if ruta.exists():
        d = json.loads(ruta.read_text(encoding="utf-8-sig") or "{}")
    servidores = d.setdefault("mcpServers", {})
    antes = servidores.get(nombre)
    entrada = {"command": python or sys.executable, "args": ["-m", "trarin", "mcp"]}
    servidores[nombre] = entrada
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"config": str(ruta), "entrada": entrada, "antes": antes}


def texto(r):
    L = [f"python {r['version']} en {r['python']}", f"trarin en {r['trarin'] or '(no importable)'}"]
    for x in r["dependencias"]:
        L.append(f"  {'ok   ' if x['ok'] else 'FALTA'} {x['paquete']}" + (f" {x['version']}" if x.get("version") else "")
                 + f" — {x['para']}" + ("" if x["ok"] else f" → {x['instalar']}"))
    L.append(f"configuración de Claude Desktop: {r['config'] or '(no se conoce la ruta en este sistema)'}"
             + (" (existe)" if r["config_existe"] else " (no existe todavía)"))
    if r.get("entrada_actual") is not None:
        L.append(f"  entrada trarin actual: {json.dumps(r['entrada_actual'], ensure_ascii=False)}")
    return "\n".join(L)

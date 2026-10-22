"""Trabajos largos (como en witral): una operación que tarda minutos se lanza
en un proceso aparte y se consulta después, porque el cliente MCP corta las
llamadas largas.

Cada trabajo vive en una carpeta propia (por defecto %LOCALAPPDATA%\\trarin\\
trabajos\\<id>, o ~/.trarin/trabajos/<id>): pedido.json (tarea y argumentos),
salida.log (lo que va registrando), resultado.json o error.txt al terminar, y
pid. Las tareas posibles están en TAREAS (nombre → función que recibe los
argumentos y una función 'registro' para ir contando lo que hace)."""

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path


def carpeta_base():
    base = os.environ.get("TRARIN_TRABAJOS")
    if base:
        return Path(base)
    raiz = os.environ.get("LOCALAPPDATA")
    return (Path(raiz) / "trarin" / "trabajos") if raiz else (Path.home() / ".trarin" / "trabajos")


def _carpeta(ident):
    c = carpeta_base() / ident
    if not c.is_dir():
        raise ValueError(f"no existe el trabajo {ident!r}")
    return c


# ------------------------------------------------------------------ tareas

def _t_armar_show(a, registro):
    from .show import armar
    ruta, avisos = armar(a["show"], a["salida"])
    return {"salida": str(ruta), "avisos": avisos}


def _t_consolidar(a, registro):
    from . import consolidar as K
    plan, avisos, ganancias = K.consolidar(a["sesion"], a["reglas"], a["salida"], registro=registro)
    return {"salida": a["salida"], "plan": K.texto(plan), "avisos": avisos,
            "ganancias": [{"pista": f, "cancion": c, "rms_db": r, "cambio_db": g}
                          for (f, c), (r, g) in ganancias.items()]}


def _t_convertir_audio(a, registro):
    from . import audio
    rutas = audio.convertir(a["entrada"], a["salida"], a.get("frecuencia"), a.get("bits"), a.get("mono", False))
    return {"archivos": rutas}


def _t_bpm(a, registro):
    from . import bpm
    out = []
    for x in a["audios"]:
        registro(f"midiendo {x}")
        extra = {k2: a[k] for k, k2 in (("desde", "desde_s"), ("hasta", "hasta_s"), ("tramo", "tramo_s"))
                 if a.get(k) is not None}
        r = bpm.medir(x, a.get("minimo", 60), a.get("maximo", 200), **extra)
        out.append({"audio": x, "resultado": r, "texto": bpm.texto(r)})
    return {"mediciones": out}


def _t_fichas(a, registro):
    from . import fichas
    return fichas.calcular_sesion(a["sesion"], a.get("recalcular", False), registro)


TAREAS = {"armar_show": _t_armar_show, "consolidar": _t_consolidar,
          "convertir_audio": _t_convertir_audio, "bpm": _t_bpm, "fichas": _t_fichas}


# ------------------------------------------------------------------ lanzar y consultar

def lanzar(tarea, argumentos, despues=None):
    """Lanza la tarea en un proceso aparte. Devuelve el id del trabajo.
    despues: (módulo, función) a llamar con (argumentos, resultado) al terminar
    bien (p. ej. verificar lo escrito y anotarlo en las notas del proyecto)."""
    if tarea not in TAREAS:
        raise ValueError(f"tarea desconocida: {tarea!r}")
    ident = datetime.now().strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()
    c = carpeta_base() / ident
    c.mkdir(parents=True)
    (c / "pedido.json").write_text(json.dumps({"tarea": tarea, "argumentos": argumentos, "despues": despues,
                                               "inicio": time.time()}, ensure_ascii=False, default=str),
                                   encoding="utf-8")
    log = open(c / "salida.log", "w", encoding="utf-8")
    opciones = {}
    if os.name == "nt":
        opciones["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        opciones["start_new_session"] = True
    raiz = str(Path(__file__).resolve().parent.parent)
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=raiz + os.pathsep + os.environ.get("PYTHONPATH", ""))
    p = subprocess.Popen([sys.executable, "-m", "trarin", "_trabajo", str(c)], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, cwd=raiz, env=entorno, **opciones)
    (c / "pid").write_text(str(p.pid))
    return ident


def correr(carpeta):
    """Lo que corre en el proceso del trabajo (python -m trarin _trabajo <carpeta>)."""
    c = Path(carpeta)
    pedido = json.loads((c / "pedido.json").read_text(encoding="utf-8"))

    def registro(*partes):
        print(" ".join(str(x) for x in partes), flush=True)

    try:
        registro(f"inicio: {pedido['tarea']}")
        res = TAREAS[pedido["tarea"]](pedido["argumentos"], registro)
        if pedido.get("despues"):
            import importlib
            mod, fun = pedido["despues"]
            try:
                extra = getattr(importlib.import_module(mod), fun)(pedido["argumentos"], res)
                if extra:
                    res.update(extra)
            except Exception as e:                       # la verificación no tumba el trabajo
                res.setdefault("avisos", []).append(f"no se pudo verificar ni anotar: {e}")
        (c / "resultado.json").write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
        registro("listo")
    except Exception:
        (c / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        registro("error: " + traceback.format_exc().strip().splitlines()[-1])


def _vivo(pid):
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def estado(ident, desde=0, max_lineas=200):
    """{id, tarea, estado (corriendo | terminado | error | interrumpido),
    segundos, lineas (del registro desde la línea 'desde'), siguiente (para
    la próxima consulta), resultado o error}."""
    c = _carpeta(ident)
    pedido = json.loads((c / "pedido.json").read_text(encoding="utf-8"))
    lineas = (c / "salida.log").read_text(encoding="utf-8", errors="replace").splitlines() \
        if (c / "salida.log").exists() else []
    nuevas = lineas[desde:desde + max_lineas]
    out = {"id": ident, "tarea": pedido["tarea"], "segundos": round(time.time() - pedido.get("inicio", time.time())),
           "lineas": nuevas, "siguiente": desde + len(nuevas)}
    if (c / "resultado.json").exists():
        out["estado"] = "terminado"
        out["resultado"] = json.loads((c / "resultado.json").read_text(encoding="utf-8"))
    elif (c / "error.txt").exists():
        out["estado"] = "error"
        out["error"] = (c / "error.txt").read_text(encoding="utf-8")[-3000:]
    else:
        pid = int((c / "pid").read_text()) if (c / "pid").exists() else None
        out["estado"] = "corriendo" if pid and _vivo(pid) else "interrumpido"
    return out


def esperar(ident, segundos=30, desde=0):
    """Espera hasta 'segundos' (tope 40: el cliente corta las llamadas largas)
    a que el trabajo termine; devuelve su estado."""
    tope = time.time() + min(max(segundos, 0), 40)
    while True:
        e = estado(ident, desde)
        if e["estado"] != "corriendo" or time.time() >= tope:
            return e
        time.sleep(1)


def matar(ident):
    c = _carpeta(ident)
    if not (c / "pid").exists():
        return {"id": ident, "matado": False}
    pid = int((c / "pid").read_text())
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass
    (c / "error.txt").write_text("matado a pedido", encoding="utf-8")
    return {"id": ident, "matado": True}


def listar(cuantos=20):
    base = carpeta_base()
    if not base.is_dir():
        return []
    out = []
    for c in sorted((x for x in base.iterdir() if x.is_dir()), reverse=True)[:cuantos]:
        try:
            e = estado(c.name, max_lineas=0)
            out.append({k: e[k] for k in ("id", "tarea", "estado", "segundos")})
        except Exception:
            continue
    return out

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
import re
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


_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")


def _carpeta(ident):
    # el id llega desde el cliente: solo la forma que arma lanzar(), nunca una ruta
    if not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise ValueError(f"id de trabajo no válido: {ident!r}")
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


def _t_escuchar(a, registro):
    from . import escucha
    comunes = dict(registro=registro, formato=a.get("formato") or "wav", mp3_kbps=a.get("mp3_kbps") or 192,
                   auxiliares=a.get("auxiliares", True))
    if a.get("por_pista"):
        return escucha.por_pista(a["sesion"], a.get("cancion"), a.get("desde_compas"), a.get("hasta_compas"),
                                 a.get("desde_s"), a.get("hasta_s"), a.get("pistas"), a.get("frecuencia"),
                                 crudo=a.get("crudo", False), **comunes)
    return escucha.mezclar(a["sesion"], a.get("cancion"), a.get("desde_compas"), a.get("hasta_compas"),
                           a.get("desde_s"), a.get("hasta_s"), a.get("pistas"), a.get("frecuencia"),
                           crudo=a.get("crudo", False), **comunes)


def _t_pizarrita(a, registro):
    from . import pizarrita
    return pizarrita.exportar(a["sesion"], a.get("cancion"), a.get("desde_compas"), a.get("hasta_compas"),
                              a.get("desde_s"), a.get("hasta_s"), a.get("carpeta"), a.get("con_archivos", False),
                              a.get("mezcla", True), a.get("pistas"), a.get("subtitulos_compases", False), registro)


TAREAS = {"armar_show": _t_armar_show, "escuchar": _t_escuchar, "consolidar": _t_consolidar, "pizarrita": _t_pizarrita,
          "convertir_audio": _t_convertir_audio, "bpm": _t_bpm, "fichas": _t_fichas}


def _d_verificar(argumentos, resultado):
    from .vistas import tras_trabajo
    return tras_trabajo(argumentos, resultado)


# lo que se puede pedir para después de un trabajo: por nombre, de esta lista (el pedido
# viaja en un archivo; no dice qué código correr, solo cuál de estos)
DESPUES = {"verificar": _d_verificar}

_LANZADOS = []          # procesos lanzados por este proceso que siguen corriendo


# ------------------------------------------------------------------ lanzar y consultar

def lanzar(tarea, argumentos, despues=None):
    """Lanza la tarea en un proceso aparte. Devuelve el id del trabajo.
    despues: nombre de DESPUES, que se llama con (argumentos, resultado) al
    terminar bien ("verificar": revisa lo escrito y lo anota en las notas del
    proyecto)."""
    if tarea not in TAREAS:
        raise ValueError(f"tarea desconocida: {tarea!r}")
    if despues is not None and despues not in DESPUES:
        raise ValueError(f"'despues' desconocido: {despues!r}")
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
    log.close()                                   # el proceso del trabajo ya tiene el suyo
    _LANZADOS[:] = [x for x in _LANZADOS if x.poll() is None] + [p]     # recoge los que ya terminaron
    # pid + marca de inicio: el sistema reutiliza los números de proceso
    (c / "pid").write_text(f"{p.pid} {_inicio_proceso(p.pid) or ''}".strip())
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
        despues = pedido.get("despues")
        if despues:
            try:
                if not isinstance(despues, str) or despues not in DESPUES:
                    raise ValueError(f"'despues' desconocido: {despues!r}")
                extra = DESPUES[despues](pedido["argumentos"], res)
                if extra:
                    res.update(extra)
            except Exception as e:                       # la verificación no tumba el trabajo
                res.setdefault("avisos", []).append(f"no se pudo verificar ni anotar: {e}")
        (c / "resultado.json").write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
        registro("listo")
    except Exception:
        (c / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        registro("error: " + traceback.format_exc().strip().splitlines()[-1])


def _inicio_proceso(pid):
    """Marca del momento en que partió el proceso 'pid' (texto), o None si no
    existe o ya terminó. Sirve para reconocer un proceso aunque el sistema
    haya entregado su número a otro."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            codigo = wintypes.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(codigo)) or codigo.value != 259:    # STILL_ACTIVE
                return None
            t = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
                return None
            return str((t[0].dwHighDateTime << 32) | t[0].dwLowDateTime)
        finally:
            k.CloseHandle(h)
    try:                                                # Linux: estado y starttime de /proc
        with open(f"/proc/{pid}/stat", "rb") as f:
            campos = f.read().rsplit(b")", 1)[1].split()
        return None if campos[0] == b"Z" else campos[19].decode()
    except (OSError, IndexError):
        pass
    r = subprocess.run(["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    partes = r.stdout.split(None, 1)
    if len(partes) < 2 or partes[0].startswith("Z"):    # no existe, o terminó y nadie lo recogió
        return None
    return partes[1].strip()


def _leer_pid(c):
    """(pid, marca de inicio o None) del archivo pid del trabajo, o (None, None)."""
    try:
        partes = (c / "pid").read_text().split(None, 1)
        return int(partes[0]), (partes[1].strip() if len(partes) > 1 else None)
    except (OSError, ValueError, IndexError):
        return None, None


def _vivo(pid, inicio=None):
    """El proceso del trabajo sigue corriendo: existe y, si se conoce su marca
    de inicio, es el mismo que se lanzó."""
    actual = _inicio_proceso(pid)
    return actual is not None and (inicio is None or actual == inicio)


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
        pid, inicio = _leer_pid(c)
        out["estado"] = "corriendo" if pid and _vivo(pid, inicio) else "interrumpido"
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
    """Detiene el trabajo solo si sigue corriendo y su proceso es el que se
    lanzó; uno que ya terminó no se toca (su número de proceso puede ser hoy
    de otro programa)."""
    c = _carpeta(ident)
    e = estado(ident, max_lineas=0)["estado"]
    pid, inicio = _leer_pid(c)
    if e != "corriendo" or not pid:
        return {"id": ident, "matado": False, "estado": e}
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

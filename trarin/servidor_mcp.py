"""Servidor MCP de trarin (stdio).

Requiere el paquete `mcp` (pip install "trarin[mcp]"). Se inicia con
`python -m trarin mcp`. Nunca sobrescribe archivos: las herramientas que
escriben piden una salida nueva y fallan si ya existe.
"""

import json
from pathlib import Path

INSTRUCCIONES = """\
trarin lee y escribe sesiones de Pro Tools (.ptx), Ableton Live (.als) y FL Studio (.flp).

Reglas:
- Nunca modifica una sesión original: lo que escribe va a un archivo nuevo (salida) que no
  puede existir. Al terminar vuelve a leer lo escrito, lo compara con el origen y lo anota en
  `trarin/notas.md` junto a la sesión.
- Antes de trabajar en un proyecto, leer sus notas (herramienta `notas`): ahí están las
  decisiones (buses, categorías, nombres), lo hecho y lo pendiente. Las decisiones nuevas que
  tome la persona se anotan con `anotar`.
- Para conocer una sesión, partir por `resumen_sesion` (una fila por pista, filtrable por
  nombre o por canción) y `detalle_pista`; `leer_sesion` devuelve el modelo entero y en un
  show grande pesa megas: usarlo solo si hace falta.
- Lo que tarda minutos (armar_show, consolidar, convertir_audio, medir_bpm, fichas_calcular) corre como
  trabajo: devuelve un id; después `trabajo_esperar` (hasta 40 s por llamada; repetir con
  'desde' = 'siguiente' para traer solo lo nuevo) o `trabajo_estado`, y `trabajo_matar`.
- Las que tienen modo plan (plan_consolidar, plan_grupos, planear_show) no escriben nada:
  mostrar el plan a la persona antes de escribir.
- Fichas de sonido: para hablar del sonido sin escucharlo (grave o agudo, brillante u opaco,
  dónde empieza a sonar, nivel en LUFS, saturación, mono o estéreo). Se calculan una vez por
  sesión, solo cuando la persona lo pide (`fichas_calcular`, trabajo); después
  `resumen_sesion` trae una línea de sonido por pista y `ficha` da el detalle de una pista,
  de una canción o de un WAV suelto. Son aproximadas: describen el audio con el clip gain,
  sin fader, plugins ni fundidos. Al conversar, traducirlas a palabras simples para
  alguien que quizás no conoce los términos técnicos.
"""


def _servidor():
    try:
        from mcp.server.mcpserver import MCPServer as Servidor          # mcp 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Servidor          # mcp 1.x
        except ImportError as e:
            raise SystemExit('el servidor MCP requiere el paquete mcp: pip install "trarin[mcp]"') from e
    return Servidor("trarin", instructions=INSTRUCCIONES)


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
    p = Path(salida).resolve()
    if p.exists():
        raise ValueError(f"{p} ya existe: elija otro nombre")
    if not p.parent.exists():
        raise ValueError(f"no existe la carpeta {p.parent}")
    return p


def _existe(ruta):
    p = Path(ruta).resolve()
    if not p.exists():
        raise ValueError(f"no existe {ruta}")
    return str(p)


def _reglas(reglas):
    """Las reglas pueden venir como objeto o como ruta a un JSON."""
    if isinstance(reglas, str):
        return json.loads(Path(reglas).read_text(encoding="utf-8-sig"))
    return reglas


def _sin_internos(plan):
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def crear():
    srv = _servidor()
    herramienta = lambda f: srv.tool()(_claro(f))

    # ------------------------------------------------------------ conocer una sesión

    @herramienta
    def resumen_sesion(ruta: str, patron: str = "", cancion: str = "", formato: str = "texto") -> dict:
        """Una fila por pista: tipo, canales, salida, paneo, volumen, mute, clips
        (cuántos y entre qué segundos), clip gain, automatización y plugins;
        además las canciones (marcadores) con su inicio y, si ya se calcularon
        las fichas de sonido, una línea de sonido por pista. Solo lee.
        patron: expresión regular sobre el nombre (minúsculas, sin tildes), p. ej.
        "pif|puf". cancion: solo las pistas que suenan en esa canción.
        formato: "texto" (una línea por pista, liviano) o "datos" (cada campo)."""
        from .comun import leer_comun
        from .vistas import resumen, texto_resumen
        from . import fichas
        comun = leer_comun(_existe(ruta))
        r = resumen(comun, patron or None, cancion or None)
        sonido = None
        if str(ruta).lower().endswith(".ptx"):
            ses = fichas.Sesion(_existe(ruta), comun)
            if ses.hay():
                sonido = ses.palabras(cancion or None, {f["pista"] for f in r["pistas"]})
        if formato == "datos":
            if sonido is not None:
                r["sonido"] = sonido
            return r
        out = {"total": r["total"], "canciones": r["canciones"], "pistas": texto_resumen(r).splitlines()}
        if sonido is not None:
            out["sonido"] = sonido
        return out

    @herramienta
    def fichas_calcular(sesion: str, recalcular: bool = False) -> dict:
        """Calcula las fichas de sonido de todos los audios de una sesión de Pro
        Tools y las guarda en trarin/fichas junto a ella. Solo cuando la persona
        lo pide. Corre como trabajo (en un show, unos minutos); la segunda vez
        solo calcula lo que falta o cambió, salvo recalcular=True."""
        from . import trabajos
        return {"trabajo": trabajos.lanzar("fichas", {"sesion": _existe(sesion), "recalcular": recalcular})}

    @herramienta
    def ficha(ruta: str, pista: str = "", cancion: str = "", desde_s: float = -1, hasta_s: float = -1) -> dict:
        """Ficha de sonido. Con una sesión (.ptx): la de una pista (entera o en
        una canción) o, solo con 'cancion', una línea por pista que suena en
        ella; necesita fichas_calcular antes. Con un WAV suelto: la del archivo
        (o del tramo desde_s–hasta_s), calculada en el momento si hace falta.
        Trae tiempo (dónde suena de verdad, silencios), nivel (pico, RMS, LUFS,
        rango, momentos más fuertes), problemas (saturación, picos sobre 0 dBFS
        por el clip gain, corriente continua), estéreo, color (bandas, grave o
        agudo, brillante u opaco, tonal o ruidoso) y una línea en palabras.
        Tiempos en m:ss y, en una sesión, también en compás|tiempo."""
        from . import fichas
        r = _existe(ruta)
        if r.lower().endswith(".ptx"):
            ses = fichas.Sesion(r)
            if not ses.hay():
                raise ValueError("esta sesión todavía no tiene fichas: calcularlas con fichas_calcular "
                                 "(solo si la persona lo pide)")
            if pista:
                return ses.ficha(pista, cancion or None)
            if cancion:
                nombre, _, _ = fichas._ventana_cancion(ses.comun, cancion)
                return {"cancion": nombre, "pistas": ses.palabras(cancion)}
            raise ValueError("indicar 'pista', 'cancion' o ambas")
        return fichas.ficha_archivo(r, None if desde_s < 0 else desde_s, None if hasta_s < 0 else hasta_s)

    @herramienta
    def detalle_pista(ruta: str, pista: str, max_clips: int = 60) -> dict:
        """Todo lo de una pista: mezcla, plugins, automatización (puntos) y sus
        clips (posición, largo, archivo, clip gain, fundidos). Solo lee."""
        from .comun import leer_comun
        from .vistas import detalle
        return detalle(leer_comun(_existe(ruta)), pista, max_clips)

    @herramienta
    def rutas_sesion(ruta: str) -> dict:
        """Entradas, salidas y buses que existen en una sesión de Pro Tools
        (para saber a qué se puede mandar una pista). Solo lee."""
        from .vistas import rutas
        return rutas(_existe(ruta))

    @herramienta
    def archivos_audio(ruta: str, solo_faltantes: bool = False) -> dict:
        """Los archivos de audio de una sesión de Pro Tools con su ruta real en
        el disco y si existen. Solo lee."""
        from .vistas import archivos_audio as aa
        return aa(_existe(ruta), solo_faltantes)

    @herramienta
    def comparar_sesiones(antes: str, despues: str) -> dict:
        """Qué cambió entre dos versiones de una sesión: pistas que aparecen o
        desaparecen y, en las demás, salida, paneo, volumen, mute, clips,
        automatización y plugins; además tempo, compases y marcadores. Solo lee."""
        from .vistas import comparar, texto_comparacion
        c = comparar(_existe(antes), _existe(despues))
        return {"pistas": c["pistas"], "aparecen": len(c["aparecen"]), "desaparecen": len(c["desaparecen"]),
                "cambian": len(c["cambian"]), "detalle": texto_comparacion(c).splitlines()}

    @herramienta
    def leer_sesion(ruta: str, modelo: str = "comun") -> dict:
        """El modelo entero de una sesión (.ptx, .als o .flp). Pesa mucho en un
        show grande: preferir resumen_sesion y detalle_pista. modelo: "comun"
        (trarin/1, igual para los tres programas) o "nativo". Solo lee."""
        from .comun import leer_comun
        from .__main__ import leer
        if modelo == "comun":
            return leer_comun(_existe(ruta))
        if modelo == "nativo":
            return json.loads(json.dumps(leer(_existe(ruta)), default=str))
        raise ValueError('modelo debe ser "comun" o "nativo"')

    @herramienta
    def paneo_live(set_live: str) -> dict:
        """Paneo de cada pista de audio de un set de Ableton Live (.als), por
        nombre de pista y de archivo (de −1 izquierda a 1 derecha). Solo lee."""
        from .consolidar import paneos_live
        return paneos_live(_existe(set_live))

    # ------------------------------------------------------------ notas del proyecto

    @herramienta
    def notas(sesion: str) -> dict:
        """Las notas del proyecto (trarin/notas.md junto a la sesión): decisiones,
        lo hecho y lo pendiente. Leerlas al empezar a trabajar en un proyecto."""
        from . import notas as N
        return {"archivo": str(N.archivo(sesion)), "texto": N.leer(sesion) or "(todavía no hay notas)"}

    @herramienta
    def anotar(sesion: str, texto: str, seccion: str = "Decisiones") -> dict:
        """Agrega una línea con fecha a las notas del proyecto. seccion:
        "Decisiones", "Hecho" o "Pendiente". Para dejar registradas las
        decisiones de la persona (p. ej. «bus 7-8: ambientes y sintes»)."""
        from . import notas as N
        if seccion not in ("Decisiones", "Hecho", "Pendiente"):
            raise ValueError('seccion: "Decisiones", "Hecho" o "Pendiente"')
        return {"archivo": N.anotar(sesion, texto, seccion)}

    # ------------------------------------------------------------ shows

    @herramienta
    def compatibilidad(sesiones: list[str]) -> dict:
        """Antes de armar un show: qué tan compatibles son varias sesiones de Pro
        Tools (en el orden del show): frecuencias, tempos, pistas que coinciden
        (propuesta de match), ruteo y plugins. Solo lee."""
        from . import compatibilidad as C
        inf = C.informe(sesiones)
        return {"informe": inf, "texto": C.texto(inf)}

    @herramienta
    def planear_show(show: dict) -> dict:
        """Plan de tiempo de un show sin escribir nada: inicio de cada canción,
        mapa de tempo, compases y marcadores. 'show' es el JSON de `trarin show`
        ({"canciones": [{"sesion": ..., "fin_compas": ...}], "separacion": ...,
        "cambio_tempo": ..., "base": ..., "match": ..., "buses_show": ...})."""
        from .show import planear, texto
        plan = planear(show)
        return {"plan": _sin_internos(plan), "texto": texto(plan)}

    @herramienta
    def armar_show(show: dict, salida: str) -> dict:
        """Escribe la sesión de Pro Tools del show en 'salida' (un .ptx nuevo).
        Corre como trabajo: devuelve su id (ver trabajo_esperar)."""
        from . import trabajos
        ident = trabajos.lanzar("armar_show", {"show": show, "salida": str(_salida_nueva(salida))},
                                despues=("trarin.vistas", "tras_trabajo"))
        return {"trabajo": ident}

    # ------------------------------------------------------------ cambiar una sesión

    @herramienta
    def plan_consolidar(sesion: str, reglas: dict | str) -> dict:
        """Muestra, sin escribir, cómo quedaría consolidar: qué pistas del mismo
        instrumento van a cada pista nueva (L/C/R por paneo, estéreo aparte, una
        más cuando suenan juntas), qué se quita y qué MIDI se junta. 'reglas' es
        el objeto o la ruta de un JSON (ver trarin/consolidar.py: canciones,
        categorias, rutas, auxiliares, borrar, midi, normalizar_db; el paneo de
        Live sale de "live" o de "live_carpeta" + "live_buscar")."""
        from .comun import leer_comun
        from . import consolidar as K
        plan = K.planear(leer_comun(_existe(sesion)), _reglas(reglas))
        return {"texto": K.texto(plan), "nuevas": {n: {"fuentes": x["fuentes"], "paneo": x["paneo"],
                                                      "canales": x["canales"]} for n, x in plan["nuevas"].items()},
                "midi": plan["midi"], "quitar": plan["quitar"] + plan["quitar_midi"] + plan["borrar"],
                "avisos": plan["avisos"]}

    @herramienta
    def consolidar(sesion: str, reglas: dict | str, salida: str) -> dict:
        """Escribe la sesión consolidada en 'salida' (un .ptx nuevo): las pistas
        del mismo instrumento de distintas canciones pasan a pocas pistas, con el
        clip gain normalizado, la automatización por canción, plugins, buses,
        auxiliares y grupos. Mostrar antes plan_consolidar. Corre como trabajo."""
        from . import trabajos
        ident = trabajos.lanzar("consolidar", {"sesion": _existe(sesion), "reglas": _reglas(reglas),
                                               "salida": str(_salida_nueva(salida))},
                                despues=("trarin.vistas", "tras_trabajo"))
        return {"trabajo": ident}

    @herramienta
    def plan_grupos(sesion: str, reglas: dict | str) -> dict:
        """Muestra, sin escribir, qué pistas irían a cada grupo. reglas:
        {"grupos": [{"nombre", "patron", "tipo": "ambos"|"mezcla"}], "grupo_midi": "MIDI"}."""
        from .comun import leer_comun
        from .protools.grupos import grupos_por_nombre
        r = _reglas(reglas)
        pistas = [(x["nombre"], x["tipo"]) for x in leer_comun(_existe(sesion))["pistas"]]
        grupos = grupos_por_nombre(pistas, r["grupos"], r.get("grupo_midi"))
        sueltas = sorted({n for n, t in pistas if t in ("audio", "midi", "0x0007")} - {x for g in grupos for x in g["pistas"]})
        return {"grupos": grupos, "sin_grupo": sueltas}

    @herramienta
    def crear_grupos(sesion: str, reglas: dict | str, salida: str) -> dict:
        """Escribe en 'salida' (un .ptx nuevo) la sesión con los grupos de
        plan_grupos. Verifica lo escrito y lo anota en las notas."""
        from .comun import leer_comun
        from .protools.descifrado import leer_descifrado
        from .protools.grupos import agregar_grupos, grupos_por_nombre
        from .protools.reconstruccion import cifrar
        from .vistas import tras_escribir
        r = _reglas(reglas)
        p = _salida_nueva(salida)
        pistas = [(x["nombre"], x["tipo"]) for x in leer_comun(_existe(sesion))["pistas"]]
        grupos = grupos_por_nombre(pistas, r["grupos"], r.get("grupo_midi"))
        p.write_bytes(cifrar(agregar_grupos(leer_descifrado(sesion), grupos)))
        return {"salida": str(p), "grupos": [g["nombre"] for g in grupos], **tras_escribir(sesion, str(p), "grupos")}

    @herramienta
    def escribir_sesion(comun: dict, plantilla: str, salida: str) -> dict:
        """Aplica un modelo común editado (leído con leer_sesion) sobre una copia
        de 'plantilla' y la guarda en 'salida' (archivo nuevo, misma extensión)."""
        from .comun import aplicar
        p = _salida_nueva(salida)
        if p.suffix.lower() != Path(plantilla).suffix.lower():
            raise ValueError("la salida debe tener la misma extensión que la plantilla")
        aplicar(comun, plantilla, str(p))
        out = {"salida": str(p)}
        if p.suffix.lower() == ".ptx":
            from .vistas import tras_escribir
            out.update(tras_escribir(plantilla, str(p), "escribir"))
        return out

    # ------------------------------------------------------------ audio

    @herramienta
    def medir_bpm(audios: list[str], minimo: float = 60, maximo: float = 200) -> dict:
        """Calcula el tempo de uno o más WAV (mejor una pista de percusión, como
        el bombo): pulso, primer pulso y tempo por tramos. Corre como trabajo."""
        from . import trabajos
        return {"trabajo": trabajos.lanzar("bpm", {"audios": [_existe(a) for a in audios],
                                                   "minimo": minimo, "maximo": maximo})}

    @herramienta
    def convertir_audio(entrada: str, salida: str, frecuencia: int, bits: int = 0, mono: bool = False) -> dict:
        """Cambia la frecuencia de muestreo de un WAV (soxr, calidad muy alta).
        mono=True: un archivo por canal (.L.wav/.R.wav), como los usa Pro Tools.
        Corre como trabajo."""
        from . import trabajos
        _salida_nueva(salida if salida.lower().endswith(".wav") else salida + ".wav")
        salida = str(Path(salida).resolve())
        return {"trabajo": trabajos.lanzar("convertir_audio", {"entrada": _existe(entrada), "salida": salida,
                                                                 "frecuencia": frecuencia, "bits": bits or None,
                                                                 "mono": mono})}

    # ------------------------------------------------------------ trabajos

    @herramienta
    def trabajo_esperar(id: str, segundos: int = 30, desde: int = 0) -> dict:
        """Espera hasta 'segundos' (máx. 40) a que un trabajo termine y devuelve
        su estado, las líneas nuevas del registro (desde la línea 'desde') y, al
        terminar, el resultado (con la verificación de lo escrito). Repetir con
        desde = 'siguiente' mientras siga «corriendo»."""
        from . import trabajos
        return trabajos.esperar(id, segundos, desde)

    @herramienta
    def trabajo_estado(id: str, desde: int = 0) -> dict:
        """Estado de un trabajo sin esperar (ver trabajo_esperar)."""
        from . import trabajos
        return trabajos.estado(id, desde)

    @herramienta
    def trabajo_matar(id: str) -> dict:
        """Detiene un trabajo que sigue corriendo."""
        from . import trabajos
        return trabajos.matar(id)

    @herramienta
    def trabajos_recientes(cuantos: int = 10) -> list:
        """Los últimos trabajos con su estado."""
        from . import trabajos
        return trabajos.listar(cuantos)

    return srv


def main():
    crear().run()

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
- Lo que tarda minutos (armar_show, consolidar, convertir_audio, medir_bpm, fichas_calcular,
  escuchar) corre como
  trabajo: devuelve un id; después `trabajo_esperar` (hasta 40 s por llamada; repetir con
  'desde' = 'siguiente' para traer solo lo nuevo) o `trabajo_estado`, y `trabajo_matar`.
- Las que tienen modo plan (plan_consolidar, plan_grupos, planear_show) no escriben nada:
  mostrar el plan a la persona antes de escribir.
- Si la persona dice que las pistas MIDI aparecen vacías en Pro Tools, `revisar_midi` y
  `reparar_midi`: las notas están, pero la región las deja fuera de su ventana.
- MIDI desde los golpes de una percusión (kultrun, bombo): `golpes_a_midi`; primero el
  plan, y si dice que muchos golpes caen lejos de la grilla, comentarlo antes de escribir.
- Crossfades en clips pegados (una pista, varias o todas): `cruces`; primero el plan
  (por omisión) y mostrarlo antes de escribir.
- Reordenar pistas: `ordenar_pistas` con plan=True muestra el orden (explícito o por
  grupos de la sesión) sin escribir; mostrarlo a la persona antes de escribir.
- Fichas de sonido: para hablar del sonido sin escucharlo (grave o agudo, brillante u opaco,
  percusivo o sostenido, pulso, nota, dónde empieza a sonar, cómo crece, nivel en LUFS,
  saturación, clics, mono o estéreo). Se calculan una vez por sesión, solo cuando la persona
  lo pide (`fichas_calcular`, trabajo); después `resumen_sesion` trae una línea de sonido por
  pista, `ficha` da el detalle de una pista, de una canción o de un WAV suelto,
  `mapa_cancion` muestra qué entra y sale por compases, `que_suena` lo que suena en un
  compás («el compás 40 de Aliwen»), `revisar_niveles` los picos sobre 0 dBFS con fader y
  automatización y `marcar_secciones` escribe los marcadores de sección. Para oír lo que dice
  la ficha, `escuchar` arma una mezcla rápida de un tramo en un WAV. Son aproximadas: describen el audio con el clip gain,
  sin fader, plugins ni fundidos. Al conversar, traducirlas a palabras simples para
  alguien que quizás no conoce los términos técnicos.
- Cómo habla la gente de un sonido y dónde mirarlo en la ficha:
  - grave, agudo, bajo, alto, chillón → color (bandas); brillante, opaco, apagado, oscuro →
    color (centroide); afinado, qué nota → altura (nota más presente; aproximada, no mide
    afinación fina).
  - golpea, pega, suave, ligado, sostenido → articulación (ataque); rápido, lento, cuántos
    golpes → golpes por segundo; a tiempo, atrasado, suelto, cuadrado → en_grilla_pct.
  - fuerte, bajito, se pierde, tapa a otro → nivel (LUFS) y `que_suena`; crece, sube, baja,
    se apaga, entra, sale → forma y `mapa_cancion`.
  - ancho, hueco, raro en mono, se va a un lado → estéreo (tipo, lado, suma_mono_db).
  - chasquido, clic, golpe al cortar, se corta → problemas (clics, cortes, bordes sin
    fundido); satura, revienta, distorsiona → saturación y sobre 0 dBFS; retumba, mueve los
    parlantes, zumbido grave → infrasonido y continua.
  - Todavía sin medida: rajado, áspero, vibrante; redoble, trémolo, tiembla; batimientos,
    pulsa lento; bitonal, multifónico, dos notas a la vez (tarka, pifilka, antara);
    onda asimétrica (pifilka, antara, trompeta, clarinete). Si la persona habla de eso,
    decir que la ficha aún no lo mide, ofrecer lo más cercano (ruidoso, golpes regulares
    rápidos, nota más presente, bandas) y sugerir escucharlo; no inventar el dato.
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
    """Las reglas pueden venir como objeto, como ruta a un JSON o como
    "plantilla:nombre[+extra.json]" (ver trarin.plantillas)."""
    if isinstance(reglas, str) and reglas.startswith("plantilla:"):
        from . import plantillas
        return plantillas.resolver(reglas)
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
        por el clip gain, continua (fija o variable), infrasonido, posibles clics, cortes a cero,
        bordes de clip sin fundido), estéreo (tipo, lado, cuánto pierde en
        mono), color (bandas, grave o agudo, brillante u opaco, tonal o
        ruidoso), altura (nota más presente), articulación (golpes por segundo,
        regularidad y pulso, percusivo o sostenido), forma (curva en 20 tramos,
        constante, crescendo…, cambios de carácter) y una línea en palabras.
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
    def parecidos(ruta: str, referencia: str, cancion: str = "", cuantos: int = 8, por_cancion: bool = False,
                  desde_compas: int = 0, hasta_compas: int = 0, ventana_compases: int = 0,
                  sin_misma_pista: bool = False) -> dict:
        """Las pistas de la sesión que más se parecen a 'referencia' (el nombre
        de una pista de la sesión, o la ruta de un WAV: «busca en el show lo que
        se parezca a este golpe»), por timbre y manera de tocar: color, brillo,
        tonal o ruidoso, ataque, golpes por segundo, caída, nota, mono o
        estéreo (no el nivel). Cada resultado dice en qué difiere. cancion:
        comparar lo que suena en esa canción; por_cancion: cada pista por
        separado en cada canción del show. desde_compas–hasta_compas: la
        referencia es solo ese tramo de la pista en la canción (un golpe, una
        frase); ventana_compases: las candidatas se parten en tramos de N
        compases («este golpe del compás 12 contra los demás»);
        sin_misma_pista: no comparar con la propia pista de la referencia.
        Necesita fichas_calcular. Solo lee."""
        from . import parecido as PR
        r = PR.parecidos(_existe(ruta), referencia, cancion or None, cuantos, por_cancion, desde_compas or None,
                         hasta_compas or None, ventana_compases, sin_misma_pista)
        return {"referencia": r["referencia"], "palabras_referencia": r["palabras_referencia"],
                "candidatas": r["candidatas"],
                "parecidos": [{k: v for k, v in x.items() if k != "palabras"} | {"palabras": x["palabras"]}
                              for x in r["parecidos"]]}

    @herramienta
    def ficha_midi(ruta: str, pista: str, cancion: str = "", audio: str = "") -> dict:
        """Ficha de una pista MIDI (las fichas de sonido son solo de audio):
        notas y densidad (por segundo y por compás), rango y notas más usadas,
        acordes, velocidad, largo de las notas, si está cuantizada a la grilla
        de la sesión, puerto y canal de salida, y si dobla a una pista de audio
        ('audio', o la de nombre más parecido): qué parte de sus notas cae
        sobre un golpe detectado. Con una línea en palabras. Solo lee."""
        from . import ficha_midi as FM
        return FM.ficha(_existe(ruta), pista, cancion or None, audio or None)

    @herramienta
    def buscar_sonidos(ruta: str, descripcion: str, cancion: str = "", cuantos: int = 20,
                       por_cancion: bool = False) -> dict:
        """Pistas de audio cuya ficha cumple una descripción en palabras: «agudas
        y percusivas», «graves sostenidas en estéreo», «ruidoso rápido», «nota
        la», «que saturen». Se entienden: agudo, grave, muy grave, de registro
        medio, brillante, opaco, tonal, ruidoso, percusivo, sostenido, mixto,
        regular, irregular, rápido, lento, mono, estéreo, satura, fuerte, suave,
        crece, se apaga, constante, intermitente, con problemas y «nota X».
        Tienen que cumplirse todos los criterios; las palabras no entendidas se
        devuelven. cancion: solo lo que suena en esa canción. Necesita
        fichas_calcular. Solo lee."""
        from . import parecido as PR
        return PR.buscar(_existe(ruta), descripcion, cancion or None, cuantos, por_cancion)

    @herramienta
    def mapa_cancion(ruta: str, cancion: str, compases_por_tramo: int = 4) -> dict:
        """El mapa de una canción de la sesión, por tramos de N compases: qué
        pistas suenan, cuáles entran y salen y la sonoridad aproximada de la
        suma; los tramos iguales seguidos se juntan en secciones y se proponen
        los compases donde cambia mucho (posibles marcadores de sección).
        Compases de la sesión y contados desde el inicio de la canción.
        Necesita fichas_calcular antes."""
        from . import fichas
        ses = fichas.Sesion(_existe(ruta))
        if not ses.hay():
            raise ValueError("esta sesión todavía no tiene fichas: calcularlas con fichas_calcular "
                             "(solo si la persona lo pide)")
        return ses.mapa(cancion, compases_por_tramo)

    @herramienta
    def que_suena(ruta: str, compas: int = 0, cancion: str = "", segundo: float = -1) -> dict:
        """Qué pistas suenan en un compás, de la más fuerte a la más suave, con
        su color en ese momento. compas: número de compás de la sesión o, si se
        indica 'cancion', contado desde el inicio de esa canción («el compás 40
        de Aliwen»). O bien 'segundo' de la sesión. Necesita fichas_calcular."""
        from . import fichas
        ses = fichas.Sesion(_existe(ruta))
        if not ses.hay():
            raise ValueError("esta sesión todavía no tiene fichas: calcularlas con fichas_calcular "
                             "(solo si la persona lo pide)")
        return ses.que_suena(compas or None, None if segundo < 0 else segundo, cancion or None)

    @herramienta
    def revisar_niveles(ruta: str, cancion: str = "") -> dict:
        """Revisión de nivel de una sesión (o de una canción): pistas cuyo pico
        pasa 0 dBFS con clip gain, fader, su automatización y mute (y si la
        causa es el clip gain, el fader o ambos), y la sonoridad aproximada de
        cada canción con la mezcla. No ve plugins, buses ni el master. Solo lee;
        necesita fichas_calcular."""
        from . import fichas
        ses = fichas.Sesion(_existe(ruta))
        if not ses.hay():
            raise ValueError("esta sesión todavía no tiene fichas: calcularlas con fichas_calcular "
                             "(solo si la persona lo pide)")
        return ses.revisar_niveles(cancion or None)

    @herramienta
    def marcar_secciones(ruta: str, cancion: str, salida: str, compases: list[int] | None = None,
                         compases_por_tramo: int = 4) -> dict:
        """Escribe una sesión nueva (salida) con marcadores de sección en una
        canción: «<canción> B», «<canción> C»… en los compases pedidos (contados
        desde el inicio de la canción) o, si no se indican, en los que propone
        mapa_cancion. Mostrar antes los compases a la persona. Verifica lo
        escrito y lo anota en las notas del proyecto."""
        from . import fichas
        from .vistas import tras_escribir
        origen = _existe(ruta)
        sal = _salida_nueva(salida)
        puestos = fichas.escribir_marcadores(origen, str(sal), cancion, compases or None, compases_por_tramo)
        out = {"salida": str(sal), "marcadores": puestos}
        out.update(tras_escribir(origen, str(sal), f"marcadores de sección en {cancion}"))
        return out

    @herramienta
    def escuchar(ruta: str, cancion: str = "", desde_compas: int = 0, hasta_compas: int = 0,
                 desde_s: float = -1, hasta_s: float = -1, pistas: str = "", frecuencia: int = 0,
                 formato: str = "wav", mp3_kbps: int = 192, auxiliares: bool = True, crudo: bool = False,
                 por_pista: bool = False) -> dict:
        """Mezcla rápida de un tramo de una sesión de Pro Tools en un WAV estéreo
        de 16 bits o un MP3 (formato="mp3", liviano para mandar o escuchar desde
        cualquier lado), en trarin/escuchas junto a la sesión, siempre un
        archivo nuevo, para escuchar sin abrir Pro Tools. Tramo: una canción (y,
        si se indican, sus compases desde_compas–hasta_compas contados desde su
        inicio), compases de la sesión o segundos. pistas: expresión regular para
        escuchar solo algunas («bombo», «pif|kultrun»). frecuencia: p. ej. 22050
        para un WAV más liviano. Suma clip gain, fundidos, paneo, fader, su
        automatización, mute, solo, el fader de los auxiliares por los que pasa
        cada pista (auxiliares=False para no aplicarlo) y el fader del master;
        sin plugins. crudo=True: solo clip gain y fundidos, sin fader, paneo,
        mute, auxiliares ni master («la pista tal cual está grabada»).
        por_pista=True: un archivo por pista («dame las pistas sin efectos»), en
        una carpeta del tramo, cada una con su fader y paneo (o tal cual grabada
        con crudo=True). Corre como trabajo; el resultado trae la ruta."""
        from . import trabajos
        args = {"sesion": _existe(ruta)}
        for k, v in (("cancion", cancion or None), ("desde_compas", desde_compas or None),
                     ("hasta_compas", hasta_compas or None), ("desde_s", None if desde_s < 0 else desde_s),
                     ("hasta_s", None if hasta_s < 0 else hasta_s), ("pistas", pistas or None),
                     ("frecuencia", frecuencia or None), ("formato", formato if formato != "wav" else None),
                     ("mp3_kbps", mp3_kbps if mp3_kbps != 192 else None),
                     ("auxiliares", False if not auxiliares else None),
                     ("crudo", crudo if (crudo or por_pista) else None), ("por_pista", True if por_pista else None)):
            if v is not None:
                args[k] = v
        return {"trabajo": trabajos.lanzar("escuchar", args)}

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
    def comparar_sesiones(antes: str, despues: str, por_cancion: bool = False) -> dict:
        """Qué cambió entre dos versiones de una sesión: pistas que aparecen o
        desaparecen y, en las demás, salida, paneo, volumen, mute, clips,
        automatización y plugins; además tempo, compases y marcadores. Solo lee.
        por_cancion: además, canción por canción, si se corrió el primer audio,
        la primera nota MIDI, la distancia MIDI−audio, el fin del audio o la
        primera automatización respecto del marcador (detecta un MIDI que quedó
        desfasado del audio sin mirar pista por pista)."""
        from .comun import leer_comun
        from .vistas import comparar, texto_comparacion
        ca, cb = leer_comun(_existe(antes)), leer_comun(_existe(despues))
        c = comparar(ca, cb)
        out = {"pistas": c["pistas"], "aparecen": len(c["aparecen"]), "desaparecen": len(c["desaparecen"]),
               "cambian": len(c["cambian"]), "detalle": texto_comparacion(c).splitlines()}
        if por_cancion:
            from . import uniones as U
            out["por_cancion"] = U.texto_comparacion_por_cancion(U.comparar_por_cancion(ca, cb)).splitlines()
        return out

    @herramienta
    def hoja_show(ruta: str, salida: str = "", titulo: str = "", formato: str = "markdown") -> dict:
        """Hoja del show para los músicos: por canción, orden, dónde empieza,
        cuánto dura y cuántos compases, tempo (con sus cambios y rampas
        resumidas), compás, marcas de sección y dónde entra cada pista de audio
        y MIDI (compás de la canción, minuto del show). Sale de los clips, no
        necesita fichas. Escribe un Markdown (por omisión
        trarin/hoja_del_show.md junto a la sesión; 'salida' para otra ruta) y
        devuelve la ruta; formato "datos" devuelve además los datos. Solo lee
        la sesión."""
        from . import hoja as H
        r, h = H.escribir(_existe(ruta), salida or None, titulo or None)
        out = {"escrito": r, "canciones": len(h["canciones"]), "total": h["total"]}
        if formato == "datos":
            out["datos"] = h
        else:
            out["resumen"] = [f"{c['orden']}. {c['nombre']} — {c['inicio']}, {c['duracion']}, {c['compases']} compases, "
                              + " → ".join(H._tempo_corto(t) for t in c["tempo"]) + " bpm, "
                              + " → ".join(x["compas"] for x in c["compas"]) for c in h["canciones"]]
        return out

    @herramienta
    def exportar_pizarrita(ruta: str, cancion: str = "", desde_compas: int = 0, hasta_compas: int = 0,
                           desde_s: float = -1, hasta_s: float = -1, pistas: str = "", carpeta: str = "",
                           con_archivos: bool = False, mezcla: bool = True, subtitulos_compases: bool = False) -> dict:
        """Exporta una canción (o un tramo) del show como montaje de Pizarrita
        Edit Pro, para escucharla y comentarla en el navegador: un JSON con las
        pistas de audio y sus clips (archivo, pedazo, lugar, clip gain,
        fundidos), fader, paneo, mute, automatización y marcas (y los compases
        como subtítulos si se pide), más la mezcla rápida en MP3 para oírla de
        inmediato; con_archivos=True copia los WAV que usa. Va a
        trarin/pizarrita junto a la sesión (o a 'carpeta'). Lo que falta del
        lado de Pizarrita: subir los archivos a una idea y pegar el montaje.
        Corre como trabajo."""
        from . import trabajos
        args = {"sesion": _existe(ruta)}
        for k, v in (("cancion", cancion or None), ("desde_compas", desde_compas or None),
                     ("hasta_compas", hasta_compas or None), ("desde_s", None if desde_s < 0 else desde_s),
                     ("hasta_s", None if hasta_s < 0 else hasta_s), ("pistas", pistas or None),
                     ("carpeta", carpeta or None), ("con_archivos", con_archivos or None),
                     ("mezcla", None if mezcla else False), ("subtitulos_compases", subtitulos_compases or None)):
            if v is not None:
                args[k] = v
        return {"trabajo": trabajos.lanzar("pizarrita", args)}

    @herramienta
    def plantillas(accion: str = "listar", nombre: str = "", reglas: dict | str | None = None,
                   extra: dict | str | None = None, descripcion: str = "") -> dict:
        """Plantillas de reglas con nombre para consolidar, grupos y el armador:
        categorías, buses, auxiliares, grupos y reglas MIDI de un show guardadas
        para el próximo (viene «rapizuam»). accion: "listar"; "ver" (la
        plantilla 'nombre'); "guardar" (unas 'reglas' —objeto o ruta JSON— como
        plantilla propia, sin lo propio del show: canciones, rutas, borrar…);
        "armar" (las reglas listas: la plantilla con 'extra' encima, las listas
        se suman). En consolidar, plan_consolidar, crear_grupos y plan_grupos,
        reglas="plantilla:rapizuam" o "plantilla:rapizuam+extra.json" las usa
        directamente."""
        from . import plantillas as PL
        if accion == "listar":
            return {"plantillas": PL.listar(), "carpeta_propia": str(PL.carpeta_propia())}
        if accion == "ver":
            return PL.leer(nombre)
        if accion == "guardar":
            if reglas is None:
                raise ValueError("guardar: indicar 'reglas'")
            return {"escrito": PL.guardar(nombre, reglas, descripcion or None)}
        if accion == "armar":
            return PL.armar(nombre, extra)
        raise ValueError("accion: listar, ver, guardar o armar")

    @herramienta
    def luces(ruta: str, salida: str = "", puerto: str = "", fijar: dict | None = None, grilla: str = "1/16",
              nota: int = 36) -> dict:
        """Luces del show por MIDI: tres pistas MIDI nuevas («Luz 1», «Luz 2»,
        «Luz 3», un clip por canción) desde los golpes de la percusión de cada
        canción, según la convención: canal 1 el kultrun o bombo (luz
        principal); canales 2 y 3 una pifilka cada uno a la contra (L y R; con
        una sola, golpes alternados); sin pifilkas, otra percusión repetida en
        los dos. Elige las pistas por nombre y por cuánto audio tienen; fijar:
        {canción: {"principal", "pifilkas": [L, R], "otra"}} para decidir a
        mano. Sin salida devuelve el plan (tarda: detecta golpes en todo el
        show); con salida escribe la sesión nueva, les pone puerto (el más usado
        en la sesión, o 'puerto') y canal 1, 2 y 3, la revisa y anota."""
        from . import luces as LU
        plan = LU.planear(_existe(ruta), puerto or None, fijar, grilla, nota)
        out = {"plan": LU.texto(plan).splitlines(), "notas": plan["notas"], "puerto": plan["puerto"], "avisos": plan["avisos"]}
        if salida:
            r = LU.escribir(plan, _salida_nueva(salida))
            out.update({"escrito": str(_existe(salida)), "notas_escritas": r["notas"], "salidas_midi": r["salidas_midi"],
                        "verificacion": r["verificacion"], "avisos": r["avisos"]})
        return out

    @herramienta
    def onda(ruta: str, cancion: str = "", desde_compas: int = 0, hasta_compas: int = 0, desde_s: float = -1,
             hasta_s: float = -1, pistas: str = "", maximo: int = 12) -> dict:
        """Dibuja la onda de una o varias pistas de audio en un tramo: envolvente
        RMS y pico (con el clip gain, bloques de 0,1 s), los golpes detectados,
        los marcadores de canción y la grilla de compases. Escribe un SVG (con
        nombres y etiquetas, para abrir en el navegador) y un PNG en
        trarin/ondas junto a la sesión y devuelve las rutas. Tramo como en
        escuchar; pistas: expresión regular (por defecto las primeras 'maximo'
        pistas de audio con sonido). Necesita fichas_calcular. Solo lee."""
        from . import onda as ON
        return ON.dibujar(_existe(ruta), cancion or None, desde_compas or None, hasta_compas or None,
                          None if desde_s < 0 else desde_s, None if hasta_s < 0 else hasta_s, pistas or None, maximo)

    @herramienta
    def nivelar(ruta: str, objetivo_db: float = -18.0, tope_pico_db: float = -1.0, cancion: str = "",
                pistas: str = "", parejas: bool = False, lufs_objetivo: float = 0.0, salida: str = "") -> dict:
        """Propone el clip gain que deja cada pista de audio a un RMS objetivo
        (por defecto −18 dBFS) en cada canción, sin que el pico pase
        'tope_pico_db'; con parejas=True suma además lo que le falta a cada
        canción para igualar su sonoridad a la más fuerte (o a lufs_objetivo).
        Mide con las fichas (fichas_calcular antes); no toca faders. pistas:
        expresión regular. Sin 'salida' solo propone; con 'salida' escribe una
        copia con el clip gain cambiado en las regiones de esos clips (una curva
        de clip gain se conserva corrida), la revisa y anota. Bajar lo que pasa
        0 dBFS: nivelar con el objetivo actual (−18) y el tope del pico."""
        from . import nivelar as NV
        plan = NV.planear(_existe(ruta), objetivo_db, tope_pico_db, cancion or None, pistas or None, parejas,
                          lufs_objetivo if lufs_objetivo else None)
        out = {"plan": NV.texto(plan).splitlines(), "cambios": plan["cambios"],
               "filas": [{k: v for k, v in f.items() if k != "ids"} for f in plan["filas"] if f["delta_db"]]}
        if salida:
            r = NV.escribir(plan, _salida_nueva(salida))
            out.update({"escrito": str(_existe(salida)), "regiones": r["regiones"], "verificacion": r["verificacion"],
                        "avisos": r["avisos"]})
        return out

    @herramienta
    def editar_clips(ruta: str, accion: str, pista: str, cancion: str = "", desde_compas: int = 0,
                     hasta_compas: int = 0, compas: int = 0, compases: float = 0.0, segundos: float = 0.0,
                     salida: str = "") -> dict:
        """Edita los clips de una pista por compás de la canción. accion:
        "mover" (los clips que empiezan en el tramo se corren 'compases' —puede
        ser negativo o fraccionario— o 'segundos'; audio y MIDI), "cortar" (los
        clips de audio que pasan por 'compas' se parten ahí), "silenciar" o
        "quitar" (los clips de audio dentro de desde_compas–hasta_compas se
        mutean o se borran; los que cruzan un borde se cortan primero). Los
        compases se cuentan desde el marcador de la canción; sin canción, son de
        la sesión. Un clip con fundidos no se puede partir ni, si el fundido es
        cruzado, borrar: se avisa y se mutea entero. Sin 'salida' devuelve el
        plan; con 'salida' escribe una copia nueva, la revisa y anota."""
        from . import edicion as ED
        plan = ED.planear(_existe(ruta), accion, pista, cancion or None, desde_compas or None, hasta_compas or None,
                          compas or None, compases, segundos)
        out = {"plan": ED.texto(plan).splitlines(), "cambios": len(plan["cambios"]), "avisos": plan["avisos"]}
        if salida:
            r = ED.escribir(plan, _salida_nueva(salida))
            out.update({"escrito": str(_existe(salida)), "verificacion": r["verificacion"], "avisos": r["avisos"]})
        return out

    # ------------------------------------------------------------ una sola sesión con respaldos

    @herramienta
    def reemplazar_sesion(sesion: str, nueva: str, motivo: str = "") -> dict:
        """Deja un cambio en la sesión de trabajo: respalda la sesión vigente en
        Session File Backups (<nombre>.trarin.<fecha>_<n>.ptx) y pone en su
        lugar la sesión nueva que escribió otra herramienta (la mueve). Así en
        la carpeta del show hay un solo archivo de sesión. Anota en las notas.
        Antes de reemplazar conviene que 'revisar' de la nueva esté sin
        errores."""
        from . import respaldos
        return respaldos.reemplazar(_existe(sesion), _existe(nueva), motivo)

    @herramienta
    def respaldos_sesion(sesion: str) -> dict:
        """Los respaldos de trarin de una sesión (Session File Backups), del más
        nuevo al más viejo, con la nota de lo que se cambió en cada uno. Solo
        lee."""
        from . import respaldos
        lista = respaldos.listar(_existe(sesion))
        return {"respaldos": lista, "texto": respaldos.texto(lista).splitlines()}

    @herramienta
    def deshacer(sesion: str, cual: str = "") -> dict:
        """Vuelve la sesión de trabajo al último respaldo de trarin (o al que
        contenga 'cual' en el nombre). Lo que había no se pierde: queda como
        <nombre>.deshecho.<fecha>.ptx en Session File Backups. Anota en las
        notas."""
        from . import respaldos
        return respaldos.deshacer(_existe(sesion), cual or None)

    @herramienta
    def revisar(ruta: str) -> dict:
        """Revisión (lint) de una sesión de Pro Tools sin abrirla: errores que
        Pro Tools mostraría rotos (regiones de audio con largo cero o fuera de su
        archivo, listas con cantidad distinta de sus bloques, mapa de tempo que
        no cuadra, clips MIDI con las notas fuera de su ventana, ancla de la
        línea de tiempo distinta de 0 en un show, archivos de audio que faltan)
        y avisos (regiones con la forma antigua de trarin, clips que cruzan un
        marcador, huecos largos). Correr después de cada escritura. Solo lee."""
        from .revision import revisar as rv, texto as tx
        r = rv(_existe(ruta))
        return {"ok": r["ok"], "errores": r["errores"], "avisos": r["avisos"], "revisado": r["revisado"],
                "texto": tx(r).splitlines()}

    @herramienta
    def comparar_con_original(ruta: str, cancion: str, original: str, tolerancia_ms: float = 50.0) -> dict:
        """Compara una canción del show con la sesión de la que salió: cuántos
        clips del original están en el show y en qué pista quedaron, cuáles
        faltan, cuáles están muteados en el show, cuáles quedaron en otro lugar,
        qué hay en el show que no viene del original (grabaciones agregadas),
        qué pistas del original no se llevaron y qué faders cambiaron. Los
        clips se reconocen por archivo, pedazo y largo; la alineación es el
        corrimiento más repetido. Solo lee."""
        from . import origen as O
        r = O.comparar(_existe(ruta), cancion, _existe(original), tolerancia_ms / 1000)
        return {**{k: v for k, v in r.items() if k not in ("faltan", "muteados_en_show", "movidos", "agregados_en_show")},
                "faltan": len(r["faltan"]), "muteados_en_show": len(r["muteados_en_show"]), "movidos": len(r["movidos"]),
                "agregados_en_show": len(r["agregados_en_show"]), "texto": O.texto(r).splitlines()}

    @herramienta
    def uniones(ruta: str, formato: str = "texto") -> dict:
        """Los bordes entre las canciones de un show: por cada par seguido, dónde
        termina el último audio de una (y en qué pistas), el marcador de la
        siguiente, dónde empieza su primer audio, el hueco entre ambos, lo mismo
        para el MIDI, y el tempo y compás a cada lado; los huecos sin ningún clip
        y los clips que cruzan un marcador; y por canción, cómo caen el audio, el
        MIDI y la automatización respecto de su marcador. Es lo que se revisa al
        cambiar el orden o la separación del show. Solo lee.
        formato: "texto" (liviano) o "datos" (cada campo)."""
        from . import uniones as U
        from .comun import leer_comun
        c = leer_comun(_existe(ruta))
        u = U.uniones(c)
        pc = U.por_cancion(c)
        if formato == "datos":
            u["por_cancion"] = pc
            return u
        return {"canciones": u["canciones"], "uniones": U.texto(u).splitlines(),
                "por_cancion": U.texto_por_cancion(pc).splitlines()}

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
    def revisar_midi(sesion: str) -> dict:
        """Clips MIDI que Pro Tools muestra vacíos aunque tengan notas (región y
        notas con distinta convención de posición) y cuántos se pueden reparar.
        Solo lee."""
        from .protools.midi_revision import revisar_midi as revisar
        return revisar(_existe(sesion))

    @herramienta
    def reparar_midi(sesion: str, salida: str) -> dict:
        """Escribe en 'salida' (un .ptx nuevo) la sesión con los clips MIDI vacíos
        reparados (ver revisar_midi). No mueve notas ni clips."""
        from .protools.descifrado import leer_descifrado
        from .protools.midi_revision import reparar_desplazamientos, revisar_midi as revisar
        from .protools.reconstruccion import cifrar
        from .vistas import tras_escribir
        p = _salida_nueva(salida)
        d, n = reparar_desplazamientos(leer_descifrado(_existe(sesion)))
        p.write_bytes(cifrar(d))
        return {"salida": str(p), "regiones_reparadas": n, "despues": revisar(str(p)),
                **tras_escribir(sesion, str(p), f"MIDI: {n} clips vacíos reparados")}

    @herramienta
    def ordenar_pistas(sesion: str, salida: str = "", orden: list[str] | None = None,
                       grupos: list[str] | None = None, plan: bool = True) -> dict:
        """Cambia el orden de las pistas de un .ptx. 'orden': nombres de TODAS las
        pistas en el orden nuevo; o 'grupos': nombres de grupos de la sesión, y el
        orden queda master y auxiliares, audio por grupo (y por su primer clip), MIDI
        por grupo. Con plan=True (por omisión) solo devuelve el orden; con plan=False
        escribe 'salida' (un .ptx nuevo) y verifica que cada pista conserve sus clips,
        notas y mezcla."""
        from .comun import leer_comun
        from .protools.descifrado import leer_descifrado
        from .protools.pistas_orden import orden_por_grupos, reordenar, verificar_orden
        from .protools.reconstruccion import cifrar
        from .vistas import tras_escribir
        c = leer_comun(_existe(sesion))
        if bool(orden) == bool(grupos):
            raise ValueError("indicar 'orden' o 'grupos' (uno de los dos)")
        de = {}
        if grupos:
            orden, de = orden_por_grupos(c, grupos)
        filas = [{"pista": n, "grupo": de.get(n)} for n in orden]
        if plan:
            return {"orden": filas}
        p = _salida_nueva(salida)
        p.write_bytes(cifrar(reordenar(leer_descifrado(sesion), orden)))
        problemas = verificar_orden(c, leer_comun(str(p)), orden)
        if problemas:
            p.unlink()
            raise ValueError(f"la verificación falló, no se escribió: {problemas}")
        return {"salida": str(p), "orden": filas, **tras_escribir(sesion, str(p), "pistas reordenadas")}

    @herramienta
    def salidas_midi(sesion: str, cambios: dict | None = None, salida: str = "") -> dict:
        """Puerto y canal de salida de las pistas MIDI. Sin 'cambios' solo los lee.
        cambios: {pista: {"canal": 1..16, "puerto"?: nombre}} (por omisión el puerto
        que ya tiene; una pista sin salida toma como plantilla otra con ese puerto);
        escribe 'salida' (un .ptx nuevo)."""
        from .protools.descifrado import leer_descifrado
        from .protools.reconstruccion import cifrar
        from .protools import salidas_midi as S
        d = leer_descifrado(_existe(sesion))
        if not cambios:
            return {"salidas": S.leer_salidas(d)}
        p = _salida_nueva(salida)
        p.write_bytes(cifrar(S.cambiar_salidas(d, cambios)))
        despues = S.leer_salidas(leer_descifrado(str(p)))
        mal = [k for k, v in cambios.items() if (despues.get(k) or {}).get("canal") != int(v["canal"])]
        if mal:
            p.unlink()
            raise ValueError(f"la verificación falló, no se escribió: {mal}")
        return {"salida": str(p), "salidas": {k: despues[k] for k in cambios}}

    @herramienta
    def golpes_a_midi(sesion: str, pista: str, cancion: str = "", desde_compas: int = 0, hasta_compas: int = 0,
                      nota: int = 36, grilla: str = "1/16", fase: str = "auto", destino: str = "",
                      salida: str = "", mid: str = "", plan: bool = True) -> dict:
        """Golpes de percusión de una pista de audio (kultrun, bombo: ataques claros)
        → una nota MIDI por golpe, con la fuerza en la velocidad y el largo de lo que
        suena, cuantizada a 'grilla' ('no' = sin cuantizar; fase 'auto' corre la grilla
        a los golpes). Tramo: 'cancion' (marcador) y compases, o toda la pista. Con
        plan=True (por omisión) solo describe lo encontrado; con plan=False escribe
        'salida' (un .ptx nuevo; las notas van a la pista MIDI 'destino', por omisión
        «<pista> MIDI», que se crea si no existe) y/o 'mid' (un .mid nuevo)."""
        from . import golpes as G
        from .comun import leer_comun
        from .vistas import tras_escribir
        pl = G.planear(_existe(sesion), pista, cancion=cancion or None, desde_compas=desde_compas or None,
                       hasta_compas=hasta_compas or None, nota=nota, grilla=grilla, fase=fase)
        res = {**G.publico(pl), "texto": G.texto(pl)}
        if plan:
            return res
        if not salida and not mid:
            raise ValueError("indicar 'salida' (.ptx) o 'mid'")
        if salida:
            p = _salida_nueva(salida)
            res["escrito"] = G.escribir(pl, str(p), destino or None)
            res.update(tras_escribir(sesion, str(p), f"MIDI desde los golpes de {pista}"))
        if mid:
            res["mid"] = G.escribir_mid(pl, str(_salida_nueva(mid)), leer_comun(sesion)["tempo"])
        return res

    @herramienta
    def cruces(sesion: str, salida: str = "", pistas: list[str] | None = None, largo_ms: float = 20.0,
               tolerancia_ms: float = 1.0, curva: str = "potencia", plan: bool = True) -> dict:
        """Crossfades en los clips de audio pegados (o con un hueco o superposición de
        hasta tolerancia_ms) de las pistas indicadas (por omisión, todas las de audio):
        centrados en la unión, curva 'potencia' (Equal Power) o 'lineal' (Equal Gain).
        No toca uniones que ya tienen un fundido; cruza también clips muteados; en
        estéreo con cortes distintos por canal, cruza cada canal. Con plan=True (por
        omisión) solo lista las uniones; con plan=False escribe 'salida' (un .ptx nuevo)
        y verifica cada cruce."""
        from .protools import cruces as X
        from .protools.descifrado import leer_descifrado
        from .vistas import tras_escribir
        pl = X.planear(leer_descifrado(_existe(sesion)), pistas, largo_ms, tolerancia_ms, curva=curva)
        if plan:
            return {**X.publico(pl), "texto": X.texto(pl)}
        p = _salida_nueva(salida)
        n = X.escribir(sesion, str(p), pl)
        faltan = X.verificar(str(p), pl)
        if faltan:
            p.unlink()
            raise ValueError(f"la verificación falló, no se escribió: {faltan[:10]}")
        return {"salida": str(p), "cruces": n, "texto": X.texto(pl),
                **tras_escribir(sesion, str(p), f"{n} crossfades en clips pegados")}

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
    def proponer_tempo(ruta: str, pista: str = "", cancion: str = "", salida: str = "",
                       minimo: float = 40.0, maximo: float = 200.0) -> dict:
        """Propone el tempo de una sesión de Pro Tools midiendo una pista de
        percusión (la indicada o, por defecto, la de kultrun/bombo con más
        audio): bpm, confianza, acento, tempo por tramos, dónde cae el primer
        pulso, a cuántos ms del pulso cae la negra más cercana de la grilla
        actual y si la grilla actual es el mismo pulso contado distinto (95 =
        126,67 × 3/4). En una sesión suelta propone un tempo único con el tick
        0 en un pulso (ancla); en un show o con 'cancion', un cambio de tempo en
        el pulso más cercano de la grilla actual. Solo propone; con 'salida'
        escribe una copia con ese tempo y la revisa."""
        from . import tempo_propuesto as TP
        r = TP.proponer(_existe(ruta), pista or None, cancion or None, None, None, minimo, maximo)
        out = {k: v for k, v in r.items() if k not in ("candidatos",)}
        out["texto"] = TP.texto(r).splitlines()
        if salida:
            out["escrito"] = str(_salida_nueva(salida))
            out["verificacion"] = TP.escribir(r, out["escrito"])["verificacion"]
        return out

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

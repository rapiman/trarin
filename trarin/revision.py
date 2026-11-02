"""Revisión (lint) de una sesión de Pro Tools: invariantes que, cuando fallan,
Pro Tools muestra algo roto aunque trarin lea bien. Para correr después de
cada escritura sin abrir Pro Tools. Solo lee.

Cada regla viene de un error real:
- regiones de audio con largo cero o que se salen de su archivo (regiones con
  desplazamiento mal escritas: rayas negras sin sonido);
- listas cuyo campo «cantidad» no coincide con los hijos que hay (0x262A
  regiones, 0x1004 archivos);
- mapa de tempo cuyo largo o cantidad de eventos no cuadra con el bloque;
- notas MIDI fuera de la ventana de su región (clips vacíos en Pro Tools);
- ancla de la línea de tiempo distinta de 0 en un show (MIDI y marcadores
  corridos respecto del audio);
- archivos de audio que no existen en el disco;
- clips que cruzan un marcador de canción (aviso: puede ser a propósito)."""

import re
import struct
from pathlib import Path

from .protools.bloques import arbol, buscar
from .protools.descifrado import leer_descifrado
from .protools import pistas as P
from .protools.tiempo import _EV_TEMPO


def _cantidad(d, bloque):
    return struct.unpack_from("<I", d, bloque.inicio + 2)[0]


def _tempo(d, b):
    """(largo declarado, largo esperado, eventos declarados, eventos reales) del 0x2028."""
    t = buscar(b, 0x2028)
    if not t:
        return None
    x = t[0]
    datos = d[x.pos:x.fin]
    m = datos.find(b"Tempo\x02\x00")
    if m < 0:
        return None
    largo, n = struct.unpack_from("<II", datos, m + 7)
    reales = len(_EV_TEMPO.findall(datos))
    return largo, 4 + 61 * n, n, reales


def _regiones_mal_formadas(d, b):
    """(inconsistentes, sin_inicio): nombres de regiones de audio con
    desplazamiento > 0. Tras el largo, Pro Tools escribe dos veces el «inicio
    original» con el ancho del 4.º byte (nibble alto y bajo iguales) y 8 bytes
    FF [visto en sesiones de Pro Tools 12: anchos 2, 3, 4 y 8]. Inconsistente:
    el 4.º byte anuncia un ancho y los bytes no siguen esa forma. Sin inicio:
    ancho 0 (la forma que escribía trarin hasta el 2026-11-01; Pro Tools puede
    dejar esos clips con largo cero)."""
    lista = buscar(b, 0x262A)
    if not lista:
        return [], []
    mal, sin_inicio = [], []
    for c in lista[0].hijos:
        if c.tipo_contenido != 0x2629:
            continue
        g = next((h for h in c.hijos if h.tipo_contenido == 0x2628), None)
        if not g:
            continue
        nombre, j = P._cadena(d, g.inicio + 2)
        w_off = (d[j + 1] & 0xF0) >> 4
        w_len = (d[j + 2] & 0xF0) >> 4
        w_ini = (d[j + 3] & 0xF0) >> 4
        if not w_off or int.from_bytes(d[j + 5:j + 5 + w_off], "little") == 0:
            continue
        if not w_ini:
            sin_inicio.append(nombre)
            continue
        p = j + 5 + w_off + w_len
        ini_b = d[p:p + w_ini]
        if (d[j + 3] & 0x0F) != w_ini or d[p + w_ini:p + 2 * w_ini] != ini_b or d[p + 2 * w_ini:p + 2 * w_ini + 8] != b"\xff" * 8:
            mal.append(nombre)
    return mal, sin_inicio


def revisar(ruta, comun=None):
    """Devuelve {"ok", "errores": [...], "avisos": [...], "revisado": {...}}.
    Un error es algo que Pro Tools mostrará roto; un aviso, algo que conviene
    mirar. comun: el modelo ya leído, si se tiene (evita releer)."""
    ruta = str(ruta)
    d = leer_descifrado(ruta)
    b = arbol(d)
    errores, avisos, revisado = [], [], {}

    # --- regiones de audio
    archivos = P.leer_archivos_audio(d, b)
    regiones = P.leer_regiones_audio(d, b)
    revisado["regiones_audio"] = len(regiones)
    revisado["archivos"] = len(archivos)
    largo_cero = [r["nombre"] for r in regiones if r["muestras"] == 0]
    if largo_cero:
        errores.append(f"{len(largo_cero)} regiones de audio con largo cero (en Pro Tools: rayas negras sin sonido): "
                       + ", ".join(largo_cero[:8]))
    fuera = []
    sin_archivo = []
    for r in regiones:
        a = r["archivo"]
        if a is None or a >= len(archivos):
            sin_archivo.append(r["nombre"])
            continue
        total = archivos[a].get("muestras")
        if total and r["desde_muestra"] + r["muestras"] > total:
            fuera.append(f"{r['nombre']} ({r['desde_muestra'] + r['muestras']} > {total})")
    if sin_archivo:
        errores.append(f"{len(sin_archivo)} regiones apuntan a un archivo que no está en la lista: "
                       + ", ".join(sin_archivo[:8]))
    if fuera:
        errores.append(f"{len(fuera)} regiones se salen de su archivo: " + ", ".join(fuera[:8]))
    mal, sin_inicio = _regiones_mal_formadas(d, b)
    if mal:
        errores.append(f"{len(mal)} regiones con desplazamiento tienen el bloque 0x2628 inconsistente "
                       f"(el inicio original no se repite como lo escribe Pro Tools): " + ", ".join(mal[:8]))
    if sin_inicio:
        avisos.append(f"{len(sin_inicio)} regiones con desplazamiento no llevan inicio original (forma de trarin "
                      f"anterior al 2026-11-01); si en Pro Tools aparecen con largo cero, rehacerlas: "
                      + ", ".join(sin_inicio[:5]))

    # --- listas con cantidad
    for tipo, hijo, nombre in ((0x262A, 0x2629, "regiones de audio"), (0x1004, 0x1003, "archivos")):
        lista = buscar(b, tipo)
        if lista:
            x = lista[0]
            n, hijos = _cantidad(d, x), sum(1 for h in x.hijos if h.tipo_contenido == hijo)
            revisado[f"lista_{nombre.replace(' ', '_')}"] = [n, hijos]
            if n != hijos:
                errores.append(f"la lista de {nombre} declara {n} y tiene {hijos} bloques")

    # --- mapa de tempo
    t = _tempo(d, b)
    if t:
        largo, esperado, n, reales = t
        revisado["tempo"] = {"eventos": n, "reales": reales}
        if largo != esperado:
            errores.append(f"mapa de tempo: largo declarado {largo}, esperado {esperado} para {n} eventos")
        if reales != n:
            errores.append(f"mapa de tempo: declara {n} eventos y se leen {reales}")

    # --- MIDI
    from .protools.midi_revision import revisar_midi
    m = revisar_midi(d)
    revisado["regiones_midi"] = m["regiones"]
    if m["vacias_en_pro_tools"]:
        errores.append(f"{m['vacias_en_pro_tools']} clips MIDI con las notas fuera de su ventana (vacíos en Pro Tools; "
                       f"{m['reparables']} reparables con reparar_midi): " + ", ".join(m["ejemplos"][:8]))

    # --- modelo común: ancla, archivos en disco, clips que cruzan marcadores
    if comun is None:
        from .comun import leer_comun
        comun = leer_comun(ruta)
    from .vistas import _canciones
    canciones = _canciones(comun)
    revisado["canciones"] = len(canciones)
    ancla = (comun["tempo"][0].get("inicio_segundos") or 0.0) if comun["tempo"] else 0.0
    if abs(ancla) > 1e-6:
        msg = f"el tick 0 cae en {ancla:.3f} s (ancla de la línea de tiempo): MIDI, marcadores y compases van corridos respecto del audio"
        (errores if len(canciones) > 1 else avisos).append(msg)

    from .vistas import archivos_audio
    faltan = [a["nombre"] for a in archivos_audio(ruta)["archivos"] if not a["existe"]]
    if faltan:
        errores.append(f"{len(faltan)} archivos de audio no están en el disco: " + ", ".join(Path(x).name for x in faltan[:8]))

    if len(canciones) > 1:
        from .uniones import uniones
        u = uniones(comun)
        if u["cruzan"]:
            avisos.append(f"{len(u['cruzan'])} clips cruzan un marcador de canción: "
                          + ", ".join(f"{c['pista']} en {c['marcador']}" for c in u["cruzan"][:8]))
        huecos = [h for h in u["huecos"] if h["largo_s"] > 10]
        if huecos:
            avisos.append(f"{len(huecos)} huecos de más de 10 s sin ningún clip: "
                          + ", ".join(f"{h['desde_s']}–{h['hasta_s']} s" for h in huecos[:8]))

    return {"ok": not errores, "errores": errores, "avisos": avisos, "revisado": revisado}


def texto(r):
    lineas = ["OK: sin errores" if r["ok"] else f"{len(r['errores'])} errores"]
    lineas += [f"  ERROR {x}" for x in r["errores"]]
    lineas += [f"  aviso {x}" for x in r["avisos"]]
    lineas.append("revisado: " + ", ".join(f"{k} {v}" for k, v in r["revisado"].items()))
    return "\n".join(lineas)

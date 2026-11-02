"""Uso:
  python -m trarin leer SESION.(ptx|als|flp) [-o salida.json] [--yaml] [--resumen]
  python -m trarin leer SESION --comun -o comun.json      (modelo común trarin/1)
  python -m trarin escribir EDITADO.json --plantilla ORIGINAL.(als|flp) -o NUEVO.(als|flp)
  python -m trarin compatibilidad CANCION1.ptx CANCION2.ptx ... [--json] [-o informe]
  python -m trarin show plan SHOW.json [--json]
  python -m trarin show armar SHOW.json
  python -m trarin mcp                                   (servidor MCP por stdio)
"""

import argparse
import json
import sys

from pathlib import Path

from .protools import leer as leer_ptx


def leer(ruta):
    """Lee una sesión según su extensión: .ptx (Pro Tools), .als (Ableton), .flp (FL Studio)."""
    ext = Path(ruta).suffix.lower()
    if ext == ".als":
        from .ableton import leer as leer_als
        return leer_als(ruta)
    if ext == ".flp":
        from .flstudio import leer as leer_flp
        return leer_flp(ruta)
    return leer_ptx(ruta)


def _resumen_otro(s):
    o = s["origen"]
    print(f"{o['archivo']}  ({o['producto']} {o.get('version_completa') or ''})")
    print(f"  tempo inicial {s['tempo'][0]['bpm']:g} BPM ({len(s['tempo'])} eventos)")
    for c in s["compases"]:
        print(f"  compás {c['numerador']}/{c['denominador']} desde el tick {c['tick']}")
    for m in s.get("marcadores", []):
        print(f"  marcador {m.get('posicion', m['tick'])}  {m['nombre']}")
    for p in s.get("pistas", []):
        m = p.get("mezcla", {})
        print(f"  pista {p['tipo']:<8} {p['nombre']!r}: {len(p['clips'])} clips ({len(p.get('clips_session', []))} en Session), {m.get('volumen_db')} dB, "
              f"{', '.join(d.get('nombre') or d['tipo'] for d in p.get('dispositivos', []))}")
    for c in s.get("canales", []):
        print(f"  canal {c['indice']:>3} {c['tipo'] or '?':<14} {c['nombre']!r} -> insert {c.get('insert')}")
    for p in s.get("patrones", []):
        print(f"  patrón {p['numero']:>3} {p['nombre'] or '':<20} {len(p['notas'])} notas")
    for a in s.get("arreglos", []):
        print(f"  arreglo {a['nombre']!r}: {len(a['items'])} items en {len(set(i['pista'] for i in a['items']))} pistas")


def _resumen(s):
    o = s["origen"]
    print(f"{o['archivo']}  ({o['producto'] or 'Pro Tools'} {o['version']}, {o['frecuencia_muestreo']} Hz)")
    t = s["tempo"]
    if t:
        bpms = [e["bpm"] for e in t]
        print(f"  tempo: {len(t)} eventos, {min(bpms):g} a {max(bpms):g} BPM, inicial {t[0]['bpm']:g} en {t[0]['posicion']}")
    for c in s["compases"]:
        print(f"  compás {c['posicion']:>12}  {c['numerador']}/{c['denominador']}")
    print(f"  audio: {len(s['audio']['archivos'])} archivos, {len(s['audio']['regiones'])} regiones")
    for p in s["pistas_audio"]:
        n = sum(len(c["regiones"]) for c in p["canales"])
        print(f"  pista audio {p['nombre']!r}: {len(p['canales'])} canal(es), {n} regiones colocadas")
    for p in s.get("playlists_inactivas", []):
        print(f"  playlist inactiva {p['nombre']!r}")
    for g in s.get("grupos", []):
        if g["nombre"] != "<ALL>":
            print(f"  grupo {g['nombre']!r}: {', '.join(map(str, g['pistas']))}")
    for p in s["pistas_midi"]:
        print(f"  pista MIDI  {p['nombre']!r}: {len(p['regiones'])} regiones colocadas")
    for c in s.get("mezcla", []):
        extra = []
        if c.get("mute"): extra.append("mute")
        if c.get("solo"): extra.append("solo")
        extra += [i["plugin"] for i in c.get("inserts", [])]
        extra += ["envío a " + str(e["destino"]) for e in c.get("envios", [])]
        if c.get("color") is not None: extra.append(f"color {c['color']}")
        print(f"  mezcla {c.get('nombre')!r} ({c['tipo']}): {c.get('volumen_db')} dB, paneo {c.get('paneo')} {' · '.join(extra)}")
    for m in s["marcadores"]:
        print(f"  marcador #{m['numero']:<3} {m['posicion']:>12}  {m['nombre']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="trarin")
    sub = p.add_subparsers(dest="orden", required=True)
    l = sub.add_parser("leer", help="vuelca una sesión .ptx, .als o .flp a JSON (o YAML con --yaml)")
    l.add_argument("sesion")
    l.add_argument("-o", "--salida")
    l.add_argument("--yaml", action="store_true", help="YAML en vez de JSON (requiere pyyaml)")
    l.add_argument("--resumen", action="store_true", help="solo un resumen legible")
    l.add_argument("--comun", action="store_true", help="modelo común trarin/1 (mismo JSON para ptx, als y flp)")
    e = sub.add_parser("escribir", help="aplica un JSON editado sobre una copia de la sesión original (.als o .flp)")
    e.add_argument("json")
    e.add_argument("--plantilla", required=True, help="sesión original de la que se leyó el JSON")
    e.add_argument("-o", "--salida", required=True, help="archivo nuevo (nunca se sobrescribe la plantilla)")
    k = sub.add_parser("compatibilidad", help="consulta qué tan compatibles son varias sesiones antes de juntarlas (solo lee)")
    k.add_argument("sesiones", nargs="+", help="sesiones .ptx en el orden del show")
    k.add_argument("--json", action="store_true", help="informe completo en JSON")
    k.add_argument("-o", "--salida")
    sh = sub.add_parser("show", help="armador de shows: 'plan' muestra cómo quedarían las canciones juntas (solo lee); 'armar' escribe la sesión del show")
    sh.add_argument("accion", choices=["plan", "armar"])
    sh.add_argument("archivo", help="descripción del show (.json, o .yaml con pyyaml)")
    sh.add_argument("--json", action="store_true")
    sub.add_parser("mcp", help="servidor MCP por stdio (requiere: pip install \"trarin[mcp]\")")
    bp = sub.add_parser("bpm", help="calcula el tempo de un audio WAV (mejor una pista de percusión; requiere trarin[audio])")
    bp.add_argument("audios", nargs="+")
    bp.add_argument("--desde", type=float, default=0.0, help="segundo desde el que se mide")
    bp.add_argument("--hasta", type=float, help="segundo hasta el que se mide")
    bp.add_argument("--minimo", type=float, default=30.0)
    bp.add_argument("--maximo", type=float, default=240.0)
    bp.add_argument("--tramo", type=float, default=30.0, help="largo de los tramos en segundos (0 = sin tramos)")
    bp.add_argument("--json", action="store_true")
    cv = sub.add_parser("convertir-audio", help="cambia la frecuencia de muestreo de un WAV (requiere trarin[audio])")
    cv.add_argument("entrada")
    cv.add_argument("salida", help="archivo nuevo (con --mono: nombre base para .L.wav y .R.wav)")
    cv.add_argument("--frecuencia", type=int, required=True)
    cv.add_argument("--bits", type=int, help="16, 24, 32 o -32 (flotante); por defecto los del original")
    cv.add_argument("--mono", action="store_true", help="un archivo mono por canal, como los usa Pro Tools")
    gr = sub.add_parser("grupos", help="crea grupos de pistas en una copia de una sesión .ptx según los nombres")
    gr.add_argument("sesion")
    gr.add_argument("reglas", help='JSON: {"grupos": [{"nombre", "patron", "tipo": "ambos"|"mezcla"}], "grupo_midi": "MIDI"}')
    gr.add_argument("-o", "--salida", required=True)
    gr.add_argument("--plan", action="store_true", help="solo muestra qué pistas irían a cada grupo")
    co = sub.add_parser("consolidar", help="junta las pistas del mismo instrumento de un show .ptx en pocas pistas")
    co.add_argument("sesion")
    co.add_argument("reglas", help="JSON con canciones, categorías, rutas, auxiliares… (ver trarin/consolidar.py)")
    co.add_argument("-o", "--salida", required=True)
    co.add_argument("--plan", action="store_true", help="solo muestra qué pistas van a cada pista nueva")
    mi = sub.add_parser("midi", help="revisa los clips MIDI que Pro Tools muestra vacíos; con -o escribe una copia reparada")
    mi.add_argument("sesion")
    mi.add_argument("-o", "--salida", help="sesión .ptx nueva con los clips reparados")
    od = sub.add_parser("ordenar", help="cambia el orden de las pistas de una sesión .ptx (copia nueva)")
    od.add_argument("sesion")
    od.add_argument("-o", "--salida", help="sesión .ptx nueva; sin -o solo muestra el orden")
    qo = od.add_mutually_exclusive_group(required=True)
    qo.add_argument("--lista", help="archivo de texto con los nombres de todas las pistas, uno por línea")
    qo.add_argument("--grupos", help="grupos de la sesión separados por comas: audio y MIDI quedan por grupo")
    cr = sub.add_parser("cruces", help="crossfades en los clips de audio pegados de una sesión .ptx (copia nueva)")
    cr.add_argument("sesion")
    cr.add_argument("-o", "--salida", help="sesión .ptx nueva; sin -o solo muestra las uniones")
    cr.add_argument("--pistas", help="pistas separadas por comas (por defecto todas las de audio)")
    cr.add_argument("--largo-ms", type=float, default=20.0, help="largo del cruce (por defecto 20 ms)")
    cr.add_argument("--tolerancia-ms", type=float, default=1.0,
                    help="hueco o superposición máxima para considerar dos clips pegados (por defecto 1 ms)")
    cr.add_argument("--lineal", action="store_true", help="Equal Gain lineal en vez de Equal Power")
    go = sub.add_parser("golpes", help="golpes de percusión (kultrun, bombo) de una pista de audio → notas MIDI")
    go.add_argument("sesion")
    go.add_argument("pista")
    go.add_argument("--cancion")
    go.add_argument("--desde-compas", type=int)
    go.add_argument("--hasta-compas", type=int)
    go.add_argument("--nota", type=int, default=36)
    go.add_argument("--grilla", default="1/16", help="1/4, 1/8, 1/16, 1/32, 1/8t, 1/16t o 'no' (sin cuantizar)")
    go.add_argument("--fase", default="auto", help="'auto' (grilla corrida a los golpes) o 'sesion'")
    go.add_argument("--destino", help="pista MIDI destino (por defecto «<pista> MIDI», nueva)")
    go.add_argument("-o", "--salida", help="sesión .ptx nueva con las notas; sin -o ni --mid solo muestra el plan")
    go.add_argument("--mid", help="archivo .mid nuevo con las notas")
    ins = sub.add_parser("instalar", help="instala el servidor MCP en Claude Desktop (entrada «trarin» en claude_desktop_config.json con este Python) y revisa las dependencias")
    ins.add_argument("--revisar", action="store_true", help="solo revisar, sin escribir la configuración")
    ins.add_argument("--config", help="ruta del claude_desktop_config.json (por defecto la del sistema)")
    ins.add_argument("--nombre", default="trarin", help="nombre del servidor en la configuración")
    lu = sub.add_parser("luces", help="arma las tres pistas MIDI de luces del show desde la percusión de cada canción (canal 1 kultrun/bombo, 2 y 3 pifilkas a la contra); sin -o solo el plan")
    lu.add_argument("sesion")
    lu.add_argument("--puerto", help="puerto MIDI de salida (por defecto el más usado en la sesión)")
    lu.add_argument("--fijar", help='JSON {"canción": {"principal": pista, "pifilkas": [L, R], "otra": pista}}')
    lu.add_argument("--grilla", default="1/16")
    lu.add_argument("--nota", type=int, default=36)
    lu.add_argument("-o", "--salida")
    on = sub.add_parser("onda", help="dibuja la onda (envolvente RMS y pico, golpes, marcadores y compases) de una o varias pistas en un tramo: SVG y PNG en trarin/ondas. Necesita fichas")
    on.add_argument("sesion")
    on.add_argument("--cancion")
    on.add_argument("--desde-compas", type=int)
    on.add_argument("--hasta-compas", type=int)
    on.add_argument("--desde", type=float, help="segundo de la sesión")
    on.add_argument("--hasta", type=float)
    on.add_argument("--pistas", help="expresión regular sobre el nombre (por defecto hasta 12 pistas de audio)")
    on.add_argument("--maximo", type=int, default=12)
    on.add_argument("-o", "--salida", help="ruta base sin extensión")
    nv = sub.add_parser("nivelar", help="propone el clip gain que deja cada pista a un RMS objetivo por canción (y canciones parejas); con -o lo escribe. Necesita fichas")
    nv.add_argument("sesion")
    nv.add_argument("--objetivo", type=float, default=-18.0, help="RMS objetivo en dBFS (por defecto -18)")
    nv.add_argument("--tope-pico", type=float, default=-1.0, help="el pico no pasa de esto (por defecto -1 dBFS)")
    nv.add_argument("--cancion")
    nv.add_argument("--pistas", help="expresión regular sobre el nombre")
    nv.add_argument("--parejas", action="store_true", help="además, que las canciones queden parejas en sonoridad")
    nv.add_argument("--lufs", type=float, help="con --parejas: sonoridad objetivo (por defecto la de la canción más fuerte)")
    nv.add_argument("--minimo", type=float, default=0.5, help="cambios menores a esto (dB) no se hacen")
    nv.add_argument("-o", "--salida")
    ed = sub.add_parser("editar", help="mover, cortar, silenciar o quitar clips de una pista por compás de la canción; sin -o solo muestra el plan")
    ed.add_argument("sesion")
    ed.add_argument("accion", choices=["mover", "cortar", "silenciar", "quitar"])
    ed.add_argument("pista")
    ed.add_argument("--cancion")
    ed.add_argument("--desde-compas", type=int)
    ed.add_argument("--hasta-compas", type=int)
    ed.add_argument("--compas", type=int, help="cortar: compás de la canción donde cortar")
    ed.add_argument("--compases", type=float, default=0.0, help="mover: cuántos compases (negativo = antes)")
    ed.add_argument("--segundos", type=float, default=0.0, help="mover: cuántos segundos (en vez de compases)")
    ed.add_argument("-o", "--salida", help="sesión .ptx nueva con el cambio")
    og = sub.add_parser("origen", help="compara una canción del show con la sesión de la que salió: clips hallados, faltantes, muteados, movidos, agregados, pistas no llevadas y faders (solo lee)")
    og.add_argument("show")
    og.add_argument("cancion")
    og.add_argument("original", help="sesión .ptx original de la canción")
    og.add_argument("--tolerancia-ms", type=float, default=50.0)
    og.add_argument("--json", action="store_true")
    rs = sub.add_parser("respaldos", help="respaldos de trarin de una sesión (Session File Backups); con --reemplazar NUEVA deja la nueva en su lugar; con --deshacer vuelve al último")
    rs.add_argument("sesion")
    rs.add_argument("--reemplazar", metavar="NUEVA", help="sesión nueva que pasa a ser la de trabajo (la actual se respalda)")
    rs.add_argument("--motivo", default="")
    rs.add_argument("--deshacer", nargs="?", const="", metavar="CUAL", help="volver al último respaldo (o al que contenga CUAL)")
    tp = sub.add_parser("tempo", help="propone el tempo de una sesión .ptx midiendo una pista de percusión; con -o escribe una copia con ese tempo")
    tp.add_argument("sesion")
    tp.add_argument("--pista", help="pista de audio a medir (por defecto, la de percusión con más audio)")
    tp.add_argument("--cancion")
    tp.add_argument("--desde", type=float, help="segundo de la sesión desde el que se busca el clip")
    tp.add_argument("--hasta", type=float)
    tp.add_argument("--minimo", type=float, default=40.0)
    tp.add_argument("--maximo", type=float, default=200.0)
    tp.add_argument("-o", "--salida", help="sesión .ptx nueva con el tempo propuesto")
    tp.add_argument("--json", action="store_true")
    ho = sub.add_parser("hoja", help="hoja del show para los músicos (Markdown): orden, inicio, duración, tempo, compás, secciones y dónde entra cada pista (solo lee)")
    ho.add_argument("sesion")
    ho.add_argument("-o", "--salida", help="archivo .md (por omisión trarin/hoja_del_show.md junto a la sesión)")
    ho.add_argument("--titulo")
    ho.add_argument("--json", action="store_true", help="los datos en JSON por pantalla, sin escribir el .md")
    pa = sub.add_parser("parecidos", help="pistas de la sesión que más se parecen a una pista o a un WAV, según las fichas (solo lee)")
    pa.add_argument("sesion")
    pa.add_argument("referencia", help="nombre de una pista de la sesión o ruta de un WAV")
    pa.add_argument("--cancion")
    pa.add_argument("--por-cancion", action="store_true", help="cada pista por separado en cada canción")
    pa.add_argument("--cuantos", type=int, default=8)
    pa.add_argument("--desde-compas", type=int, help="la referencia es solo este tramo de la pista (con --cancion)")
    pa.add_argument("--hasta-compas", type=int)
    pa.add_argument("--ventana", type=int, default=0, help="partir las candidatas en tramos de N compases")
    pa.add_argument("--otras", action="store_true", help="no comparar con la propia pista de la referencia")
    pa.add_argument("--json", action="store_true")
    fm = sub.add_parser("ficha-midi", help="ficha de una pista MIDI: notas, densidad, rango, velocidad, cuantización, salida y si dobla a una pista de audio")
    fm.add_argument("sesion")
    fm.add_argument("pista")
    fm.add_argument("--cancion")
    fm.add_argument("--audio", help="pista de audio contra la que ver si dobla")
    fm.add_argument("--json", action="store_true")
    bu = sub.add_parser("buscar", help="pistas cuya ficha cumple una descripción («agudas y percusivas», «graves sostenidas en estéreo», «nota la») (solo lee)")
    bu.add_argument("sesion")
    bu.add_argument("descripcion")
    bu.add_argument("--cancion")
    bu.add_argument("--por-cancion", action="store_true")
    bu.add_argument("--cuantos", type=int, default=20)
    bu.add_argument("--json", action="store_true")
    rv = sub.add_parser("revisar", help="revisa una sesión .ptx: lo que Pro Tools mostraría roto (regiones, MIDI, tempo, ancla, archivos) y avisos (solo lee)")
    rv.add_argument("sesion")
    rv.add_argument("--json", action="store_true")
    un = sub.add_parser("uniones", help="bordes entre las canciones de un show .ptx: huecos, clips que cruzan un marcador y, por canción, cómo caen audio y MIDI (solo lee)")
    un.add_argument("sesion")
    un.add_argument("--comparar", metavar="OTRA", help="otra versión de la sesión: qué cambió por canción")
    un.add_argument("--json", action="store_true")
    tj = sub.add_parser("_trabajo", help=argparse.SUPPRESS)
    tj.add_argument("carpeta")
    a = p.parse_args(argv)
    if a.orden == "_trabajo":
        from .trabajos import correr
        correr(a.carpeta)
        return
    if a.orden == "instalar":
        from . import instalar as IN
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        r = IN.revisar()
        print(IN.texto(r))
        if not a.revisar:
            if "mcp" in r["falta"]:
                p.error('falta el paquete mcp: pip install "trarin[mcp]" antes de instalar')
            x = IN.instalar(a.config, a.nombre)
            print(f"escrita la entrada «{a.nombre}» en {x['config']}: {json.dumps(x['entrada'], ensure_ascii=False)}"
                  + (f" (antes: {json.dumps(x['antes'], ensure_ascii=False)})" if x["antes"] else "")
                  + "\nReiniciar Claude Desktop para que la vea.")
        return
    if a.orden == "luces":
        from pathlib import Path
        from . import luces as LU
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        plan = LU.planear(a.sesion, a.puerto, json.loads(a.fijar) if a.fijar else None, a.grilla, a.nota)
        print(LU.texto(plan))
        if a.salida:
            if Path(a.salida).exists():
                p.error(f"{a.salida} ya existe")
            r = LU.escribir(plan, a.salida)
            print(f"escrito {a.salida}: {r['notas']} notas; salidas {r['salidas_midi']}")
            for x in r["avisos"]:
                print(f"  aviso: {x}")
        return
    if a.orden == "onda":
        from . import onda as ON
        from .fichas import ErrorFicha
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            r = ON.dibujar(a.sesion, a.cancion, a.desde_compas, a.hasta_compas, a.desde, a.hasta, a.pistas, a.maximo, a.salida)
        except ErrorFicha as e:
            p.error(str(e))
        print(f"{len(r['pistas'])} pistas, {r['desde']} a {r['hasta']}: " + ", ".join(r["archivos"]))
        return
    if a.orden == "nivelar":
        from pathlib import Path
        from . import nivelar as NV
        from .fichas import ErrorFicha
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            plan = NV.planear(a.sesion, a.objetivo, a.tope_pico, a.cancion, a.pistas, a.parejas, a.lufs, a.minimo)
        except ErrorFicha as e:
            p.error(str(e))
        print(NV.texto(plan))
        if a.salida:
            if Path(a.salida).exists():
                p.error(f"{a.salida} ya existe")
            r = NV.escribir(plan, a.salida)
            print(f"escrito {a.salida}: clip gain de {r['regiones']} regiones; " + (r["verificacion"].get("texto") or ""))
            for x in r["avisos"]:
                print(f"  aviso: {x}")
        return
    if a.orden == "editar":
        from pathlib import Path
        from . import edicion as ED
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            plan = ED.planear(a.sesion, a.accion, a.pista, a.cancion, a.desde_compas, a.hasta_compas, a.compas,
                              a.compases, a.segundos)
            print(ED.texto(plan))
            if a.salida:
                if Path(a.salida).exists():
                    p.error(f"{a.salida} ya existe")
                r = ED.escribir(plan, a.salida)
                print(f"escrito {a.salida}; " + (r["verificacion"].get("texto") or "")
                      + ("" if r["verificacion"].get("revision", {}).get("ok", True) else " — REVISAR"))
                for x in r.get("avisos") or []:
                    print(f"  aviso: {x}")
        except ED.ErrorEdicion as e:
            p.error(str(e))
        return
    if a.orden == "origen":
        from . import origen as O
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            r = O.comparar(a.show, a.cancion, a.original, a.tolerancia_ms / 1000)
        except ValueError as e:
            p.error(str(e))
        print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else O.texto(r))
        return
    if a.orden == "respaldos":
        from . import respaldos as R
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            if a.reemplazar:
                r = R.reemplazar(a.sesion, a.reemplazar, a.motivo)
                print(f"{r['sesion']} reemplazada; respaldo {r['respaldo']}")
            elif a.deshacer is not None:
                r = R.deshacer(a.sesion, a.deshacer or None)
                print(f"se volvió a {r['volvio_a']}; lo que había quedó en {r['deshecho']}; quedan {r['quedan']} respaldos")
            else:
                print(R.texto(R.listar(a.sesion)))
        except R.ErrorRespaldo as e:
            p.error(str(e))
        return
    if a.orden == "tempo":
        from pathlib import Path
        from . import tempo_propuesto as TP
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            r = TP.proponer(a.sesion, a.pista, a.cancion, a.desde, a.hasta, a.minimo, a.maximo)
        except ValueError as e:
            p.error(str(e))
        print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else TP.texto(r))
        if a.salida:
            if Path(a.salida).exists():
                p.error(f"{a.salida} ya existe")
            v = TP.escribir(r, a.salida)
            print(f"escrito {a.salida}; " + (v["verificacion"].get("texto") or ""))
        return
    if a.orden == "hoja":
        from . import hoja as H
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if a.json:
            from .comun import leer_comun
            print(json.dumps(H.hoja(leer_comun(a.sesion)), ensure_ascii=False, indent=2))
            return
        ruta, h = H.escribir(a.sesion, a.salida, a.titulo)
        print(f"{len(h['canciones'])} canciones, {h['total']}; escrito {ruta}")
        return
    if a.orden == "ficha-midi":
        from . import ficha_midi as FM
        from .fichas import ErrorFicha
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            r = FM.ficha(a.sesion, a.pista, a.cancion, a.audio)
        except ErrorFicha as e:
            p.error(str(e))
        print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else r["palabras"])
        return
    if a.orden in ("parecidos", "buscar"):
        from . import parecido as PR
        from .fichas import ErrorFicha
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            if a.orden == "parecidos":
                r = PR.parecidos(a.sesion, a.referencia, a.cancion, a.cuantos, a.por_cancion, a.desde_compas,
                                 a.hasta_compas, a.ventana, a.otras)
                print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else PR.texto_parecidos(r))
            else:
                r = PR.buscar(a.sesion, a.descripcion, a.cancion, a.cuantos, a.por_cancion)
                print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else PR.texto_busqueda(r))
        except ErrorFicha as e:
            p.error(str(e))
        return
    if a.orden == "revisar":
        from .revision import revisar, texto as texto_revision
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        r = revisar(a.sesion)
        print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else texto_revision(r))
        sys.exit(0 if r["ok"] else 1)
    if a.orden == "uniones":
        from . import uniones as U
        from .comun import leer_comun
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        c = leer_comun(a.sesion)
        u = U.uniones(c)
        u["por_cancion"] = U.por_cancion(c)
        if a.comparar:
            u["cambios_por_cancion"] = U.comparar_por_cancion(c, leer_comun(a.comparar))
        if a.json:
            print(json.dumps(u, ensure_ascii=False, indent=2))
            return
        print(U.texto(u))
        print("por canción:")
        print(U.texto_por_cancion(u["por_cancion"]))
        if a.comparar:
            print(f"cambios por canción respecto de {a.comparar}:")
            print(U.texto_comparacion_por_cancion(u["cambios_por_cancion"]))
        return
    if a.orden == "golpes":
        from pathlib import Path
        from . import golpes as G
        from .comun import leer_comun
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        plan = G.planear(a.sesion, a.pista, cancion=a.cancion, desde_compas=a.desde_compas,
                         hasta_compas=a.hasta_compas, nota=a.nota, grilla=a.grilla, fase=a.fase)
        print(G.texto(plan))
        if a.salida:
            if Path(a.salida).exists():
                p.error(f"{a.salida} ya existe")
            r = G.escribir(plan, a.salida, a.destino)
            print(f"{r['notas']} notas en «{r['pista']}»; escrito {a.salida}")
        if a.mid:
            print(G.escribir_mid(plan, a.mid, leer_comun(a.sesion)["tempo"]))
        return
    if a.orden == "cruces":
        from pathlib import Path
        from .protools import cruces as X
        from .protools.descifrado import leer_descifrado
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        pistas = [x.strip() for x in a.pistas.split(",") if x.strip()] if a.pistas else None
        plan = X.planear(leer_descifrado(a.sesion), pistas, a.largo_ms, a.tolerancia_ms,
                         curva="lineal" if a.lineal else "potencia")
        print(X.texto(plan))
        if a.salida:
            if Path(a.salida).exists():
                p.error(f"{a.salida} ya existe")
            n = X.escribir(a.sesion, a.salida, plan)
            faltan = X.verificar(a.salida, plan)
            if faltan:
                Path(a.salida).unlink()
                p.error("la verificación falló, no se escribió: " + "; ".join(faltan[:10]))
            print(f"{n} cruces; escrito {a.salida}")
        return
    if a.orden == "ordenar":
        from pathlib import Path
        from .comun import leer_comun
        from .protools.descifrado import leer_descifrado
        from .protools.pistas_orden import orden_por_grupos, reordenar, verificar_orden
        from .protools.reconstruccion import cifrar
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        c = leer_comun(a.sesion)
        de = {}
        if a.grupos:
            orden, de = orden_por_grupos(c, [g.strip() for g in a.grupos.split(",") if g.strip()])
        else:
            orden = [x for x in Path(a.lista).read_text(encoding="utf-8-sig").splitlines() if x.strip()]
        for n in orden:
            print(f"{n}" + (f"   [{de[n]}]" if n in de else ""))
        if a.salida:
            if Path(a.salida).exists():
                p.error(f"{a.salida} ya existe")
            Path(a.salida).write_bytes(cifrar(reordenar(leer_descifrado(a.sesion), orden)))
            problemas = verificar_orden(c, leer_comun(a.salida), orden)
            if problemas:
                Path(a.salida).unlink()
                p.error("la verificación falló, no se escribió: " + "; ".join(problemas))
            print(f"escrito {a.salida}")
        return
    if a.orden == "midi":
        from pathlib import Path
        from .protools.descifrado import leer_descifrado
        from .protools.midi_revision import reparar_desplazamientos, revisar_midi
        from .protools.reconstruccion import cifrar
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(revisar_midi(a.sesion), ensure_ascii=False, indent=2))
        if a.salida:
            if Path(a.salida).exists():
                p.error(f"{a.salida} ya existe")
            d, n = reparar_desplazamientos(leer_descifrado(a.sesion))
            Path(a.salida).write_bytes(cifrar(d))
            print(f"{n} regiones reparadas; escrito {a.salida}")
        return
    if a.orden == "consolidar":
        from pathlib import Path
        from . import consolidar as K
        from .comun import leer_comun
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        reglas = json.loads(Path(a.reglas).read_text(encoding="utf-8-sig"))
        if a.plan:
            print(K.texto(K.planear(leer_comun(a.sesion), reglas)))
            return
        if Path(a.salida).exists():
            p.error(f"{a.salida} ya existe")
        plan, avisos, _ = K.consolidar(a.sesion, reglas, a.salida)
        print(K.texto(plan))
        for x in avisos:
            print(f"aviso: {x}")
        print(f"escrito {a.salida}")
        return
    if a.orden == "grupos":
        from pathlib import Path
        from .comun import leer_comun
        from .protools.grupos import agregar_grupos, grupos_por_nombre
        from .protools.descifrado import leer_descifrado
        from .protools.reconstruccion import cifrar
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        reglas = json.loads(Path(a.reglas).read_text(encoding="utf-8-sig"))
        pistas = [(x["nombre"], x["tipo"]) for x in leer_comun(a.sesion)["pistas"]]
        grupos = grupos_por_nombre(pistas, reglas["grupos"], reglas.get("grupo_midi"))
        for g in grupos:
            print(f"{g['nombre']} ({len(g['pistas'])}): " + ", ".join(g["pistas"]))
        sueltas = sorted({n for n, t in pistas if t in ("audio", "midi", "0x0007")} - {x for g in grupos for x in g["pistas"]})
        print(f"sin grupo ({len(sueltas)}): " + ", ".join(sueltas))
        if not a.plan:
            if Path(a.salida).exists():
                p.error(f"{a.salida} ya existe")
            Path(a.salida).write_bytes(cifrar(agregar_grupos(leer_descifrado(a.sesion), grupos)))
            print(f"escrito {a.salida}")
        return
    if a.orden in ("bpm", "convertir-audio"):
        from . import audio
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            if a.orden == "bpm":
                from . import bpm
                res = [bpm.medir(x, a.minimo, a.maximo, a.desde, a.hasta, a.tramo) for x in a.audios]
                print(json.dumps(res, ensure_ascii=False, indent=2) if a.json
                      else "\n".join(bpm.texto(r) for r in res))
            else:
                from pathlib import Path
                if Path(a.salida).exists():
                    p.error(f"{a.salida} ya existe")
                for r in audio.convertir(a.entrada, a.salida, a.frecuencia, a.bits, a.mono):
                    print(f"escrito {r}")
        except audio.ErrorAudio as e:
            p.error(str(e))
        return
    if a.orden == "mcp":
        from .servidor_mcp import main as servidor
        servidor()
        return
    if a.orden == "show":
        from .show import cargar, planear, armar, texto as texto_plan, ErrorShow
        if a.accion == "armar":
            try:
                ruta, avisos = armar(cargar(a.archivo))
            except ErrorShow as e:
                p.error(str(e))
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            print(f"escrito {ruta}")
            for x in avisos:
                print(f"  aviso: {x}")
            return
        try:
            plan = planear(cargar(a.archivo))
        except ErrorShow as e:
            p.error(str(e))
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if a.json:
            sys.stdout.write(json.dumps({k: v for k, v in plan.items() if not k.startswith("_")},
                                        ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(texto_plan(plan))
        return
    if a.orden == "compatibilidad":
        from .compatibilidad import informe, texto as texto_informe, ErrorCompatibilidad
        try:
            inf = informe(a.sesiones)
        except ErrorCompatibilidad as e:
            p.error(str(e))
        salida = json.dumps(inf, ensure_ascii=False, indent=2) + "\n" if a.json else texto_informe(inf)
        if a.salida:
            Path(a.salida).write_text(salida, encoding="utf-8")
        else:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            sys.stdout.write(salida)
        return
    if a.orden == "escribir":
        with open(a.json, encoding="utf-8") as f:
            datos = json.load(f)
        ext = Path(a.plantilla).suffix.lower()
        if Path(a.salida).exists():
            p.error(f"{a.salida} ya existe: elija otro nombre")
        if datos.get("formato") == "trarin/1":
            from .comun import aplicar
            aplicar(datos, a.plantilla, a.salida)
            print(f"escrito {a.salida}")
            return
        if ext == ".als":
            from .ableton import escribir
        elif ext == ".flp":
            from .flstudio import escribir
        else:
            p.error("por ahora solo se escriben .als y .flp")
        if Path(a.salida).exists():
            p.error(f"{a.salida} ya existe: elija otro nombre")
        escribir(datos, a.plantilla, a.salida)
        print(f"escrito {a.salida}")
        return
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if a.comun:
        from .comun import leer_comun
        s = leer_comun(a.sesion)
    else:
        s = leer(a.sesion)
    if a.resumen and not a.comun:
        (_resumen if s["origen"]["daw"] == "protools" else _resumen_otro)(s)
        return
    if a.yaml:
        try:
            import yaml
        except ImportError:
            sys.exit("--yaml requiere pyyaml (pip install pyyaml); sin --yaml la salida es JSON")
        texto = yaml.safe_dump(s, allow_unicode=True, sort_keys=False, width=120)
    else:
        texto = json.dumps(s, ensure_ascii=False, indent=2) + "\n"
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as f:
            f.write(texto)
    else:
        sys.stdout.write(texto)


if __name__ == "__main__":
    main()

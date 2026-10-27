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
    tj = sub.add_parser("_trabajo", help=argparse.SUPPRESS)
    tj.add_argument("carpeta")
    a = p.parse_args(argv)
    if a.orden == "_trabajo":
        from .trabajos import correr
        correr(a.carpeta)
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

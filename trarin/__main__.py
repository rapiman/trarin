"""Uso:
  python -m trarin leer SESION.(ptx|als|flp) [-o salida.json] [--yaml] [--resumen]
  python -m trarin leer SESION --comun -o comun.json      (modelo común trarin/1)
  python -m trarin escribir EDITADO.json --plantilla ORIGINAL.(als|flp) -o NUEVO.(als|flp)
  python -m trarin compatibilidad CANCION1.ptx CANCION2.ptx ... [--json] [-o informe]
  python -m trarin show plan SHOW.json [--json]
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
    sh = sub.add_parser("show", help="armador de shows: 'plan' muestra cómo quedarían las canciones juntas (solo lee)")
    sh.add_argument("accion", choices=["plan"])
    sh.add_argument("archivo", help="descripción del show (.json, o .yaml con pyyaml)")
    sh.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.orden == "show":
        from .show import cargar, planear, texto as texto_plan, ErrorShow
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

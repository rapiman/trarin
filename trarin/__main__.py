"""Uso:  python -m trarin leer SESION.ptx [-o salida.json] [--yaml] [--resumen]"""

import argparse
import json
import sys

from .protools import leer


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
    for p in s["pistas_midi"]:
        print(f"  pista MIDI  {p['nombre']!r}: {len(p['regiones'])} regiones colocadas")
    for c in s.get("mezcla", []):
        extra = []
        if c.get("mute"): extra.append("mute")
        if c.get("solo"): extra.append("solo")
        extra += [i["plugin"] for i in c.get("inserts", [])]
        extra += ["envío a " + str(e["destino"]) for e in c.get("envios", [])]
        print(f"  mezcla {c.get('nombre')!r} ({c['tipo']}): {c.get('volumen_db')} dB, paneo {c.get('paneo')} {' · '.join(extra)}")
    for m in s["marcadores"]:
        print(f"  marcador #{m['numero']:<3} {m['posicion']:>12}  {m['nombre']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="trarin")
    sub = p.add_subparsers(dest="orden", required=True)
    l = sub.add_parser("leer", help="vuelca una sesión .ptx a JSON (o YAML con --yaml)")
    l.add_argument("sesion")
    l.add_argument("-o", "--salida")
    l.add_argument("--yaml", action="store_true", help="YAML en vez de JSON (requiere pyyaml)")
    l.add_argument("--resumen", action="store_true", help="solo un resumen legible")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    s = leer(a.sesion)
    if a.resumen:
        _resumen(s)
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

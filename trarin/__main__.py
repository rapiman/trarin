"""Uso:  python -m trarin leer SESION.ptx [-o salida.yaml] [--json] [--resumen]"""

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
    for m in s["marcadores"]:
        print(f"  marcador #{m['numero']:<3} {m['posicion']:>12}  {m['nombre']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="trarin")
    sub = p.add_subparsers(dest="orden", required=True)
    l = sub.add_parser("leer", help="vuelca una sesión .ptx a YAML o JSON")
    l.add_argument("sesion")
    l.add_argument("-o", "--salida")
    l.add_argument("--json", action="store_true")
    l.add_argument("--resumen", action="store_true", help="solo un resumen legible")
    a = p.parse_args(argv)

    s = leer(a.sesion)
    if a.resumen:
        _resumen(s)
        return
    if a.json:
        texto = json.dumps(s, ensure_ascii=False, indent=2)
    else:
        import yaml
        texto = yaml.safe_dump(s, allow_unicode=True, sort_keys=False, width=120)
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as f:
            f.write(texto)
    else:
        sys.stdout.write(texto)


if __name__ == "__main__":
    main()

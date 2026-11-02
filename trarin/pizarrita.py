"""Puente con Pizarrita: exportar una canción del show (o un tramo) como
montaje de Pizarrita Edit Pro, para escucharla y comentarla en el navegador.

El montaje de Edit Pro (`frontend/src/canvas/montajeVideo.ts`, `Proyecto`) ya
es multipista: pistas de audio con clips `{f, a, b, en}` (el pedazo a–b del
archivo f puesto en el segundo en), volumen, paneo, fundidos de entrada y
salida, automatización de volumen y paneo y marcas. Es casi el modelo común,
así que la exportación es una traducción:

- cada pista de audio con sonido en el tramo → una pista de audio del
  montaje (fader en lineal, paneo −1…1, mute, automatización de volumen);
- cada clip → un clip con su archivo (nombre base del WAV: en Pizarrita se
  sube pegado a la idea y queda `<idea>_<nombre>`), su pedazo en segundos, su
  lugar contado desde el inicio del tramo, su clip gain en lineal y sus
  fundidos de entrada y salida (los cruzados se traducen a entrada y salida);
- los marcadores del tramo → marcas; los compases → subtítulos opcionales.

Escribe `<canción>.pizarrita.json`, la mezcla rápida en MP3 (o WAV sin
lameenc) para escuchar de inmediato, y con con_archivos=True copia los WAV que
usa el montaje. Importarlo en Pizarrita (subir los archivos a una idea y pegar
el montaje) es el paso siguiente del lado de Pizarrita."""

import json
import math
import re
import shutil
from pathlib import Path


def _lineal(db):
    return round(10 ** (float(db or 0.0) / 20), 4)


def exportar(sesion, cancion=None, desde_compas=None, hasta_compas=None, desde_s=None, hasta_s=None, carpeta=None,
             con_archivos=False, mezcla=True, pistas=None, subtitulos_compases=False, registro=print):
    from . import fichas as F
    from .escucha import _rango
    from .vistas import _canciones, _normal
    from .protools import tiempo as T
    ses = F.Sesion(str(sesion))
    comun, fs = ses.comun, ses.fs
    a, b, nombre = _rango(ses, cancion, desde_compas, hasta_compas, desde_s, hasta_s)
    m0, m1 = int(round(a * fs)), int(round(b * fs))
    rutas = {}
    for ids, rs in ses.fuentes.items():
        for i, r in zip(ids, rs):
            rutas[i] = r
    proyecto = {"v": 1, "pistas": [], "marcas": [], "nombre": nombre, "origen": {"trarin": "1", "sesion": str(ses.sesion),
                "desde_s": round(a, 3), "hasta_s": round(b, 3), "frecuencia": fs}}
    archivos = {}
    k = 0
    for p in comun["pistas"]:
        if p["tipo"] != "audio" or (pistas and not re.search(pistas, _normal(p["nombre"]))):
            continue
        clips = []
        for c in p.get("clips") or []:
            if c.get("tipo") != "audio" or c.get("archivo") is None or c.get("muestra") is None:
                continue
            x0, x1 = max(c["muestra"], m0), min(c["muestra"] + c["largo_muestras"], m1)
            if x1 <= x0:
                continue
            ids = c.get("archivos_canales") or [c["archivo"]]
            ruta = rutas.get(ids[0])
            if not ruta:
                continue
            archivos[ids[0]] = ruta
            for i in ids[1:]:
                archivos[i] = rutas.get(i)
            off = int(c.get("desde_muestra") or 0) + (x0 - c["muestra"])
            clip = {"id": f"c{k}", "f": Path(ruta).name, "a": round(off / fs, 4), "b": round((off + x1 - x0) / fs, 4),
                    "en": round((x0 - m0) / fs, 4)}
            if len(ids) == 2:
                clip["canales"] = [Path(rutas[i]).name for i in ids if rutas.get(i)]
            g = float(c.get("ganancia_curva")[0]["db"]) if c.get("ganancia_curva") else float(c.get("ganancia_db") or 0.0)
            if abs(g) > 0.01:
                clip["vol"] = _lineal(g)
            if c.get("mute"):
                clip["mudo"] = 1
            for f_ in c.get("fundidos") or []:
                d = round(int(f_.get("muestras", 0)) / fs, 4)
                if d <= 0:
                    continue
                fi = int(f_.get("muestra", 0))
                if f_["tipo"] == "entrada" or (f_["tipo"] == "cruzado" and abs(fi - c["muestra"]) < f_["muestras"]):
                    clip["entra"] = {"d": d}
                else:
                    clip["sale"] = {"d": d}
            clips.append(clip)
            k += 1
        if not clips:
            continue
        mz = p.get("mezcla") or {}
        pista = {"id": f"p{len(proyecto['pistas'])}", "tipo": "audio", "nombre": p["nombre"], "clips": clips,
                 "vol": _lineal(mz.get("volumen_db"))}
        pan = mz.get("paneo")
        if isinstance(pan, (int, float)):
            pista["pan"] = round(float(pan), 3)
        if mz.get("mute"):
            pista["mute"] = 1
        au = p.get("automatizacion") or {}
        auto = {}
        vol = [q for q in au.get("volumen_db") or [] if m0 - fs <= q["muestra"] <= m1 + fs]
        if len({q["valor"] for q in vol}) > 1:
            auto["vol"] = [[round((q["muestra"] - m0) / fs, 3), _lineal(q["valor"])] for q in vol]
        panl = [q for q in au.get("paneo_izq") or [] if m0 - fs <= q["muestra"] <= m1 + fs]
        if len({q["valor"] for q in panl}) > 1 and not isinstance(pan, list):
            auto["pan"] = [[round((q["muestra"] - m0) / fs, 3), round(q["valor"] / 100, 3)] for q in panl]
        if auto:
            pista["auto"] = auto
        proyecto["pistas"].append(pista)
    if not proyecto["pistas"]:
        raise F.ErrorFicha("no hay audio en el tramo pedido")
    mapa = ses.reloj.mapa
    for n, s in _canciones(comun):
        if a <= s < b:
            proyecto["marcas"].append(round(s - a, 3))
    for m in comun.get("marcadores") or []:
        s = T.segundos(m["tick"], mapa)
        if a < s < b and round(s - a, 3) not in proyecto["marcas"]:
            proyecto["marcas"].append(round(s - a, 3))
    proyecto["marcas"].sort()
    if subtitulos_compases:
        r = ses.reloj
        k0, k1 = r.compas_de(a), r.compas_de(b)
        subt = []
        for kk in range(k0, k1 + 1):
            s0, s1 = r.segundo_de_compas(kk) - a, r.segundo_de_compas(kk + 1) - a
            if s1 <= 0 or s0 >= b - a:
                continue
            subt.append({"id": f"s{kk}", "en": round(max(s0, 0), 3), "fin": round(min(s1, b - a), 3), "s": str(kk - k0 + 1)})
        proyecto["subtitulos"] = subt
    carpeta = Path(carpeta) if carpeta else F.carpeta_fichas(ses.sesion).parent / "pizarrita"
    carpeta.mkdir(parents=True, exist_ok=True)
    base = re.sub(r'[<>:"/\\|?*]+', "_", nombre).strip() or "montaje"
    salida = carpeta / f"{base}.pizarrita.json"
    salida.write_text(json.dumps(proyecto, ensure_ascii=False, indent=1), encoding="utf-8")
    out = {"montaje": str(salida), "pistas": len(proyecto["pistas"]), "clips": k, "archivos": len(archivos),
           "desde": ses.reloj(a), "hasta": ses.reloj(b), "avisos": []}
    if mezcla:
        from . import escucha
        try:
            import lameenc  # noqa: F401
            formato = "mp3"
        except ImportError:
            formato = "wav"
            out["avisos"].append("sin lameenc la mezcla va en WAV")
        r = escucha.mezclar(ses, cancion, desde_compas, hasta_compas, desde_s, hasta_s, pistas, None,
                            str(carpeta / f"{base}.{formato}"), registro=lambda *x: None, formato=formato)
        out["mezcla"] = r["salida"]
    if con_archivos:
        destino = carpeta / "audio"
        destino.mkdir(exist_ok=True)
        copiados = 0
        for ruta in archivos.values():
            if ruta and Path(ruta).exists():
                d = destino / Path(ruta).name
                if not d.exists():
                    shutil.copy2(ruta, d)
                    copiados += 1
        out["copiados"] = copiados
        out["carpeta_audio"] = str(destino)
    out["nota"] = ("Para verlo en Pizarrita: subir los archivos de audio a una idea (quedan como <idea>_<nombre>) y pegar "
                   "el montaje en config.video; el campo f de cada clip lleva el nombre base del archivo.")
    return out

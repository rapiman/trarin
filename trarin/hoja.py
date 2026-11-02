"""Hoja del show para los músicos (fase 5): por canción, orden, dónde empieza,
cuánto dura, tempo y compás (y sus cambios), marcas de sección, y dónde entra
cada cosa (pistas de audio y MIDI, por compás de la canción y minuto). Sale
del modelo común: no necesita fichas. Solo lee."""

from pathlib import Path

from .fichas import Reloj, _reloj
from .protools import tiempo as T
from .uniones import _canciones, _fs


def _tempos_en(comun, mapa, a, b):
    """[(segundo, bpm)] de los cambios de tempo dentro de [a, b), más el vigente en a."""
    ev = sorted(comun["tempo"], key=lambda e: e["tick"])
    vig = None
    dentro = []
    for e in ev:
        s = T.segundos(e["tick"], mapa)
        if s <= a + 1e-6:
            vig = e
        elif s < b:
            dentro.append((s, e["bpm"]))
    puntos = [(a, vig["bpm"] if vig else (ev[0]["bpm"] if ev else 120.0))]
    for s, bpm in dentro:
        if abs(bpm - puntos[-1][1]) > 0.05:
            puntos.append((s, bpm))
    # una rampa (muchos eventos chicos y seguidos) se resume en su principio y su fin
    out = []
    i = 0
    while i < len(puntos):
        s, bpm = puntos[i]
        j = i
        signo = 0
        while j + 1 < len(puntos) and puntos[j + 1][0] - puntos[j][0] < 3.0 and abs(puntos[j + 1][1] - puntos[j][1]) < 3.0:
            d = puntos[j + 1][1] - puntos[j][1]
            s_ = (d > 0) - (d < 0)
            if signo and s_ and s_ != signo:            # cambia de sentido: otra rampa
                break
            signo = signo or s_
            j += 1
        if j > i + 1:
            out.append({"en": s, "bpm": bpm, "rampa_hasta": puntos[j][0], "hasta_bpm": puntos[j][1]})
        else:
            out.append({"en": s, "bpm": bpm})
        i = j + 1
    return out


def _compases_en(comun, mapa, a, b):
    ev = sorted(comun.get("compases") or [], key=lambda e: e["tick"])
    vig, dentro = None, []
    for e in ev:
        s = T.segundos(e["tick"], mapa)
        if s <= a + 1e-6:
            vig = e
        elif s < b:
            dentro.append((s, e))
    out = [(a, vig or (ev[0] if ev else {"numerador": 4, "denominador": 4}))]
    for s, e in dentro:
        if (e["numerador"], e["denominador"]) != (out[-1][1]["numerador"], out[-1][1]["denominador"]):
            out.append((s, e))
    return [(s, f"{e['numerador']}/{e['denominador']}") for s, e in out]


def hoja(comun, margen_s=3.0, max_entradas=40):
    """Datos de la hoja: {"canciones": [...], "total_s"}."""
    mapa = T.MapaTempo(comun["tempo"])
    reloj = Reloj(comun)
    fs = _fs(comun)
    canciones = _canciones(comun)
    marcas = sorted(((T.segundos(m["tick"], mapa), m["nombre"]) for m in comun.get("marcadores") or []))
    out = []
    fin_show = 0.0
    for i, (nombre, s0) in enumerate(canciones):
        s1 = canciones[i + 1][1] if i + 1 < len(canciones) else float("inf")
        lo, hi = s0 - margen_s, s1 - margen_s
        entradas, fin = [], s0
        for p in comun["pistas"]:
            if (p.get("mezcla") or {}).get("mute"):
                continue
            primero = None
            for k in p.get("clips") or []:
                if k.get("tipo") in ("audio", "grupo"):
                    if k.get("mute"):
                        continue
                    a, b = k["muestra"] / fs, (k["muestra"] + k["largo_muestras"]) / fs
                elif k.get("tipo") == "midi":
                    if not k.get("notas"):
                        continue
                    a = T.segundos(k["tick"] + min(n["tick"] for n in k["notas"]), mapa)
                    b = T.segundos(k["tick"] + k["largo_ticks"], mapa)
                else:
                    continue
                if lo <= a < hi:
                    primero = a if primero is None else min(primero, a)
                    fin = max(fin, b)
            if primero is not None:
                entradas.append((primero, p["nombre"], p["tipo"]))
        entradas.sort()
        primer_compas = reloj.compas_de(s0)
        dur = (fin if s1 == float("inf") else min(s1, fin)) - s0
        fin_show = max(fin_show, fin)
        c = {"orden": i + 1, "nombre": nombre, "inicio_s": round(s0, 2), "inicio": _reloj(s0),
             "duracion_s": round(dur, 1), "duracion": _reloj(dur), "fin": _reloj(s0 + dur),
             "compases": max(reloj.compas_de(s0 + dur) - primer_compas, 0),
             "tempo": [{"en": _reloj(t["en"]), "bpm": round(t["bpm"], 2)}
                       | ({"rampa_hasta": _reloj(t["rampa_hasta"]), "hasta_bpm": round(t["hasta_bpm"], 2)} if "rampa_hasta" in t else {})
                       for t in _tempos_en(comun, mapa, s0, s1)],
             "compas": [{"en": _reloj(s), "compas": cm} for s, cm in _compases_en(comun, mapa, s0, s1)],
             "secciones": [{"en": _reloj(s), "compas_cancion": reloj.compas_de(s) - primer_compas + 1, "nombre": n}
                           for s, n in marcas if s0 < s < s1 and n != nombre],
             "entradas": [{"pista": n, "tipo": t, "en": _reloj(a), "compas_cancion": reloj.compas_de(a) - primer_compas + 1,
                           "segundos_cancion": round(a - s0, 1)} for a, n, t in entradas[:max_entradas]],
             "pistas": len(entradas)}
        out.append(c)
    return {"canciones": out, "total_s": round(fin_show, 1), "total": _reloj(fin_show)}


def _tempo_corto(t):
    return f"{t['bpm']:g}" + (f"⇢{t['hasta_bpm']:g}" if "hasta_bpm" in t else "")


def _tempo_largo(t):
    if "hasta_bpm" in t:
        return f"rampa de {t['bpm']:g} a {t['hasta_bpm']:g} bpm entre {t['en']} y {t['rampa_hasta']}"
    return f"{t['bpm']:g} bpm desde {t['en']}"


def markdown(h, titulo="Hoja del show"):
    L = [f"# {titulo}", "", f"{len(h['canciones'])} canciones, {h['total']} en total.", ""]
    L.append("| # | Canción | Empieza | Dura | Compases | Tempo | Compás |")
    L.append("|---|---|---|---|---|---|---|")
    for c in h["canciones"]:
        tempo = " → ".join(_tempo_corto(t) for t in c["tempo"])
        comp = " → ".join(x["compas"] for x in c["compas"])
        L.append(f"| {c['orden']} | {c['nombre']} | {c['inicio']} | {c['duracion']} | {c['compases']} | {tempo} | {comp} |")
    L.append("")
    for c in h["canciones"]:
        L.append(f"## {c['orden']}. {c['nombre']}")
        L.append("")
        L.append(f"Empieza en {c['inicio']}, dura {c['duracion']} ({c['compases']} compases), termina en {c['fin']}.")
        if len(c["tempo"]) > 1 or "hasta_bpm" in c["tempo"][0]:
            L.append("Tempo: " + "; ".join(_tempo_largo(t) for t in c["tempo"]) + ".")
        else:
            L.append(f"Tempo {c['tempo'][0]['bpm']:g} bpm, compás {c['compas'][0]['compas']}.")
        if len(c["compas"]) > 1:
            L.append("Compás: " + "; ".join(f"{x['compas']} desde {x['en']}" for x in c["compas"]) + ".")
        if c["secciones"]:
            L.append("")
            L.append("Secciones: " + "; ".join(f"{s['nombre']} (c. {s['compas_cancion']}, {s['en']})" for s in c["secciones"]) + ".")
        L.append("")
        L.append("| Entra | Compás | Minuto | Pista |")
        L.append("|---|---|---|---|")
        for e in c["entradas"]:
            L.append(f"| {e['segundos_cancion']:+g} s | {e['compas_cancion']} | {e['en']} | {e['pista']}{' (MIDI)' if e['tipo'] == 'midi' else ''} |")
        if c["pistas"] > len(c["entradas"]):
            L.append(f"| … | | | {c['pistas'] - len(c['entradas'])} pistas más |")
        L.append("")
    L.append("Tiempos en minuto:segundo del show; compases contados desde el marcador de cada canción.")
    return "\n".join(L) + "\n"


def escribir(sesion, salida=None, titulo=None):
    """Escribe la hoja en Markdown (por omisión en trarin/hoja_del_show.md junto a
    la sesión) y devuelve (ruta, datos)."""
    from .comun import leer_comun
    sesion = Path(sesion)
    c = leer_comun(str(sesion))
    h = hoja(c)
    salida = Path(salida) if salida else sesion.parent / "trarin" / "hoja_del_show.md"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(markdown(h, titulo or f"Hoja del show — {sesion.stem}"), encoding="utf-8")
    return str(salida), h

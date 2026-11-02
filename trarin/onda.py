"""Onda dibujada: la envolvente de una o varias pistas en un tramo, a partir de
las series de las fichas (bloques de 0,1 s: RMS y pico, con el clip gain), con
los marcadores de canción y la grilla de compases. Sin dependencias: SVG con
etiquetas (para abrir en el navegador) y PNG pintado a mano (para que el MCP
devuelva la imagen). La ficha describe; esto ubica de un vistazo."""

import math
import struct
import zlib
from pathlib import Path

from .fichas import BLOQUE_S, SILENCIO_DB

ANCHO = 1200
ALTO_PISTA = 72
MARGEN_IZQ = 150
MARGEN_SUP = 28
PISO_DB = -60.0


def _tramos(ses, cancion, desde_compas, hasta_compas, desde_s, hasta_s, pistas, maximo):
    import re
    from . import fichas as F
    from .escucha import _rango
    from .vistas import _normal
    a, b, nombre = _rango(ses, cancion, desde_compas, hasta_compas, desde_s, hasta_s)
    filas = []
    for p in ses.comun["pistas"]:
        if p["tipo"] != "audio" or (pistas and not re.search(pistas, _normal(p["nombre"]))):
            continue
        j = ses.tramo(p, (a, b))[0].juntar()
        if j is None or not len(j["t"]):
            continue
        filas.append((p["nombre"], j, bool((p.get("mezcla") or {}).get("mute"))))
        if len(filas) >= maximo:
            break
    if not filas:
        raise F.ErrorFicha("no hay pistas de audio con ficha en el tramo pedido")
    return a, b, nombre, filas


def _columnas(j, a, b, ancho):
    """(rms_db, pico_db) por columna de píxel, o nan donde no hay bloques."""
    import numpy as np
    rms = np.full(ancho, np.nan)
    pico = np.full(ancho, np.nan)
    col = ((j["t"] - a) / (b - a) * ancho).astype(int)
    ok = (col >= 0) & (col < ancho)
    for c, r, p in zip(col[ok], j["rms"][ok], j["pico"][ok]):
        rms[c] = r if np.isnan(rms[c]) else max(rms[c], r)
        pico[c] = p if np.isnan(pico[c]) else max(pico[c], p)
    return rms, pico


def _altura(db, alto):
    if db is None or (isinstance(db, float) and math.isnan(db)) or db <= PISO_DB:
        return 0
    return int(round(min(max((db - PISO_DB) / -PISO_DB, 0.0), 1.0) * (alto / 2 - 2)))


def _marcas(ses, a, b):
    """Marcadores en el tramo y compases (cada N, según el largo)."""
    from .protools import tiempo as T
    from .vistas import _canciones
    marc = [(s, n) for n, s in _canciones(ses.comun) if a <= s < b]
    k0, k1 = ses.reloj.compas_de(a), ses.reloj.compas_de(b)
    n = max(1, int(math.ceil((k1 - k0) / 24)))
    comp = []
    k = k0 + (-k0 % n)
    while k <= k1:
        s = ses.reloj.segundo_de_compas(k)
        if a <= s < b:
            comp.append((s, k))
        k += n
    return marc, comp


def svg(ses, a, b, nombre, filas):
    alto = MARGEN_SUP + ALTO_PISTA * len(filas) + 20
    x = lambda s: MARGEN_IZQ + (s - a) / (b - a) * (ANCHO - MARGEN_IZQ - 10)
    L = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{ANCHO}" height="{alto}" font-family="sans-serif" font-size="11">',
         f'<rect width="{ANCHO}" height="{alto}" fill="#1b1b1f"/>',
         f'<text x="8" y="17" fill="#ddd" font-size="13">{_esc(nombre)} — {_mmss(a)} a {_mmss(b)}</text>']
    marc, comp = _marcas(ses, a, b)
    for s, k in comp:
        L.append(f'<line x1="{x(s):.1f}" y1="{MARGEN_SUP}" x2="{x(s):.1f}" y2="{alto - 20}" stroke="#333" stroke-width="1"/>')
        L.append(f'<text x="{x(s) + 2:.1f}" y="{alto - 6}" fill="#888" font-size="9">{k}</text>')
    for i, (n, j, mute) in enumerate(filas):
        y0 = MARGEN_SUP + i * ALTO_PISTA
        ym = y0 + ALTO_PISTA / 2
        L.append(f'<line x1="{MARGEN_IZQ}" y1="{ym}" x2="{ANCHO - 10}" y2="{ym}" stroke="#2a2a30"/>')
        L.append(f'<text x="8" y="{ym + 4:.0f}" fill="{"#777" if mute else "#eee"}">{_esc(n[:24])}{" (mute)" if mute else ""}</text>')
        ancho = ANCHO - MARGEN_IZQ - 10
        rms, pico = _columnas(j, a, b, ancho)
        pts_p, pts_r = [], []
        for c in range(ancho):
            hp, hr = _altura(float(pico[c]), ALTO_PISTA), _altura(float(rms[c]), ALTO_PISTA)
            if hp:
                pts_p.append(f"M{MARGEN_IZQ + c},{ym - hp}v{2 * hp}")
            if hr:
                pts_r.append(f"M{MARGEN_IZQ + c},{ym - hr}v{2 * hr}")
        color = "#4a6a8a" if mute else "#3d8bcd"
        if pts_p:
            L.append(f'<path d="{"".join(pts_p)}" stroke="{color}" stroke-opacity="0.45" stroke-width="1"/>')
        if pts_r:
            L.append(f'<path d="{"".join(pts_r)}" stroke="{color}" stroke-width="1"/>')
        for g in j["golpes"][:400]:
            if a <= g[0] < b and g[1] > SILENCIO_DB + 10:
                L.append(f'<circle cx="{x(g[0]):.1f}" cy="{y0 + 6}" r="1.5" fill="#f0c050"/>')
    for s, n in marc:
        L.append(f'<line x1="{x(s):.1f}" y1="{MARGEN_SUP - 8}" x2="{x(s):.1f}" y2="{alto - 20}" stroke="#e06060" stroke-width="1.5"/>')
        L.append(f'<text x="{x(s) + 3:.1f}" y="{MARGEN_SUP - 10}" fill="#e06060" font-size="10">{_esc(n)}</text>')
    L.append("</svg>")
    return "\n".join(L)


def png(ses, a, b, nombre, filas):
    """PNG RGB pintado a mano: las ondas, los compases y los marcadores (sin texto)."""
    alto = MARGEN_SUP + ALTO_PISTA * len(filas) + 20
    fondo = (27, 27, 31)
    img = [bytearray(bytes(fondo) * ANCHO) for _ in range(alto)]

    def punto(xx, yy, c):
        if 0 <= xx < ANCHO and 0 <= yy < alto:
            img[yy][3 * xx:3 * xx + 3] = bytes(c)

    def vertical(xx, y1, y2, c):
        for yy in range(max(0, y1), min(alto, y2)):
            punto(xx, yy, c)

    x = lambda s: int(MARGEN_IZQ + (s - a) / (b - a) * (ANCHO - MARGEN_IZQ - 10))
    marc, comp = _marcas(ses, a, b)
    for s, _ in comp:
        vertical(x(s), MARGEN_SUP, alto - 20, (51, 51, 51))
    ancho = ANCHO - MARGEN_IZQ - 10
    for i, (n, j, mute) in enumerate(filas):
        y0 = MARGEN_SUP + i * ALTO_PISTA
        ym = y0 + ALTO_PISTA // 2
        for xx in range(MARGEN_IZQ, ANCHO - 10):
            punto(xx, ym, (42, 42, 48))
        rms, pico = _columnas(j, a, b, ancho)
        c1 = (74, 106, 138) if mute else (61, 139, 205)
        c2 = (45, 60, 80) if mute else (35, 75, 110)
        for c in range(ancho):
            hp, hr = _altura(float(pico[c]), ALTO_PISTA), _altura(float(rms[c]), ALTO_PISTA)
            if hp:
                vertical(MARGEN_IZQ + c, ym - hp, ym + hp, c2)
            if hr:
                vertical(MARGEN_IZQ + c, ym - hr, ym + hr, c1)
        for g in j["golpes"][:400]:
            if a <= g[0] < b and g[1] > SILENCIO_DB + 10:
                punto(x(g[0]), y0 + 6, (240, 192, 80))
                punto(x(g[0]), y0 + 7, (240, 192, 80))
        # nombre: una barra de color por pista (el texto va en el SVG)
        for yy in range(y0 + 10, y0 + ALTO_PISTA - 10):
            punto(4, yy, c1)
    for s, _ in marc:
        vertical(x(s), MARGEN_SUP - 8, alto - 20, (224, 96, 96))
        vertical(x(s) + 1, MARGEN_SUP - 8, alto - 20, (224, 96, 96))
    crudo = b"".join(b"\x00" + bytes(f) for f in img)

    def chunk(tipo, datos):
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", ANCHO, alto, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(crudo, 6)) + chunk(b"IEND", b""))


def dibujar(sesion, cancion=None, desde_compas=None, hasta_compas=None, desde_s=None, hasta_s=None, pistas=None,
            maximo=12, salida=None, formatos=("svg", "png")):
    """Escribe la onda en trarin/ondas/ junto a la sesión (o en 'salida', sin
    extensión). Devuelve {archivos, pistas, desde, hasta}."""
    import re
    from . import fichas as F
    ses = sesion if isinstance(sesion, F.Sesion) else F.Sesion(str(sesion))
    if not ses.hay():
        raise F.ErrorFicha("esta sesión todavía no tiene fichas: calcularlas con fichas_calcular")
    a, b, nombre, filas = _tramos(ses, cancion, desde_compas, hasta_compas, desde_s, hasta_s, pistas, maximo)
    if salida:
        base = Path(salida).with_suffix("")
    else:
        carpeta = F.carpeta_fichas(ses.sesion).parent / "ondas"
        carpeta.mkdir(parents=True, exist_ok=True)
        base = carpeta / (re.sub(r'[<>:"/\\|?*]+', "_", nombre + (f" [{pistas}]" if pistas else "")).strip() or "onda")
    base.parent.mkdir(parents=True, exist_ok=True)
    archivos = []
    if "svg" in formatos:
        base.with_suffix(".svg").write_text(svg(ses, a, b, nombre, filas), encoding="utf-8")
        archivos.append(str(base.with_suffix(".svg")))
    if "png" in formatos:
        base.with_suffix(".png").write_bytes(png(ses, a, b, nombre, filas))
        archivos.append(str(base.with_suffix(".png")))
    return {"archivos": archivos, "pistas": [n for n, _, _ in filas], "desde": ses.reloj(a), "hasta": ses.reloj(b)}


def _mmss(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

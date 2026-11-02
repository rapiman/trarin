"""Nivelar: proponer y escribir el clip gain que deja cada pista a un nivel
objetivo, por canción, y que las canciones queden parejas entre sí. Es lo que
se hizo a mano en el show RAPIZUAM (clip gain a −18 dBFS por pista y canción).

Mide con las series de las fichas (fichas_calcular antes): el RMS de los
bloques con sonido de cada pista en cada canción, con su clip gain actual, y
su pico. Propone un delta por pista y canción: objetivo − RMS, acotado para que
el pico no pase 'tope_pico_db'. Con 'parejas', además suma a cada canción lo
que le falta para que su sonoridad (suma de las pistas con fader) llegue a la
canción más fuerte (o a 'lufs_objetivo'). El delta se aplica a todas las
regiones de la pista en esa canción (un clip con curva de clip gain la
conserva corrida). Solo propone; con salida escribe por `clip_gain.cambiar`.

No toca faders: el nivel entre pistas se deja para el fader en Pro Tools."""

import math
from pathlib import Path

from .fichas import ErrorFicha, SILENCIO_DB


def _rms_pico(j):
    import numpy as np
    rms = j["rms"]
    umbral = max(SILENCIO_DB, float(rms.max()) - 45) if len(rms) else SILENCIO_DB
    suena = rms > umbral
    if not suena.any():
        return None, None
    return float(10 * math.log10((10 ** (rms[suena] / 10)).mean())), float(j["pico"].max())


def planear(sesion, objetivo_db=-18.0, tope_pico_db=-1.0, cancion=None, pistas=None, parejas=False,
            lufs_objetivo=None, minimo_cambio_db=0.5):
    from . import fichas as F
    from .vistas import _normal
    from .comun import clips_audio
    import re
    ses = F.Sesion(str(sesion))
    if not ses.hay():
        raise ErrorFicha("esta sesión todavía no tiene fichas: calcularlas con fichas_calcular")
    canciones = [n for n, _ in F._canciones_de(ses.comun)] or [None]
    if cancion:
        canciones = [F._ventana_cancion(ses.comun, cancion)[0]]
    por_cancion = {}
    if parejas:
        niv = ses.revisar_niveles(cancion)["canciones"]
        if niv:
            meta = lufs_objetivo if lufs_objetivo is not None else max(x["lufs"] for x in niv)
            por_cancion = {x["cancion"]: meta - x["lufs"] for x in niv}
    filas, avisos = [], []
    for cn in canciones:
        rango = None
        if cn:
            _, a, b = F._ventana_cancion(ses.comun, cn)
            rango = (a - 3.0, b - 3.0 if b != float("inf") else b)
        for p in ses.comun["pistas"]:
            if p["tipo"] != "audio" or (pistas and not re.search(pistas, _normal(p["nombre"]))):
                continue
            j = ses.tramo(p, rango)[0].juntar()
            if j is None:
                continue
            rms, pico = _rms_pico(j)
            if rms is None:
                continue
            delta = objetivo_db - rms + por_cancion.get(cn, 0.0)
            tope = tope_pico_db - pico
            acotado = False
            if delta > tope:
                delta, acotado = tope, True
            clips = [c for c in clips_audio(p) if c.get("archivo") is not None
                     and (rango is None or rango[0] <= c["segundos"] < rango[1])]
            fila = {"pista": p["nombre"], "cancion": cn, "rms_dbfs": round(rms, 1), "pico_dbfs": round(pico, 1),
                    "delta_db": round(delta, 2), "clips": len(clips), "ids": [c["id"] for c in clips]}
            if acotado:
                fila["acotado_por_pico"] = True
            if abs(delta) < minimo_cambio_db:
                fila["delta_db"] = 0.0
            filas.append(fila)
    return {"sesion": str(Path(sesion).resolve()), "objetivo_db": objetivo_db, "tope_pico_db": tope_pico_db,
            "parejas": {k: round(v, 1) for k, v in por_cancion.items()} if parejas else None,
            "filas": filas, "cambios": sum(1 for f in filas if f["delta_db"]), "avisos": avisos}


def _regiones_de_clips(sesion, ids):
    """{índice de región: [ids de clip]} para los clips (por canal en estéreo)."""
    from .comun import leer_nativo
    nat = leer_nativo(sesion)
    por_pista = {t["nombre"]: t for t in nat["pistas_audio"]}
    grupos = nat["audio"].get("grupos_clips") or []
    out = {}
    for cid in ids:
        pista, j = cid.rsplit("#", 1)
        t = por_pista.get(pista)
        if not t:
            continue
        if "." in j:
            # clip de adentro de un grupo («pista#j.k»): la región k del contenido del grupo
            # de cada canal (cambiarla cambia todos los lugares donde se usa ese contenido)
            j, k = (int(x) for x in j.split(".", 1))
            ref = t["canales"][0]["regiones"][j]
            for canal in t["canales"]:
                r = next((x for x in canal["regiones"] if x["muestra"] == ref["muestra"] and "indice_grupo" in x), None)
                g = grupos[r["indice_grupo"]] if r is not None and r["indice_grupo"] < len(grupos) else None
                cont = (g or {}).get("contenido") or []
                if k < len(cont):
                    out.setdefault(cont[k][1], []).append(cid)
            continue
        j = int(j)
        ref = t["canales"][0]["regiones"][j]
        for canal in t["canales"]:
            r = next((x for x in canal["regiones"] if x["muestra"] == ref["muestra"] and "indice_region" in x), None)
            if r is not None:
                out.setdefault(r["indice_region"], []).append(cid)
    return out


def escribir(plan, salida):
    from .protools.descifrado import leer_descifrado
    from .protools.reconstruccion import cifrar
    from .protools.clip_gain import cambiar
    from .vistas import tras_escribir
    deltas, comparten = {}, []
    for f in plan["filas"]:
        if not f["delta_db"]:
            continue
        for indice, cids in _regiones_de_clips(plan["sesion"], f["ids"]).items():
            if indice in deltas and abs(deltas[indice] - f["delta_db"]) > 0.01:
                comparten.append(f"la región {indice} está en {f['pista']} ({f['cancion']}) y en otro lugar con otro delta: "
                                 f"se usa {deltas[indice]:+.2f} dB")
                continue
            deltas[indice] = f["delta_db"]
    if not deltas:
        raise ValueError("el plan no tiene cambios")
    d = leer_descifrado(plan["sesion"])
    d2, resultado = cambiar(d, deltas)
    Path(salida).write_bytes(cifrar(d2))
    r = tras_escribir(plan["sesion"], str(salida), f"nivelar a {plan['objetivo_db']} dBFS: clip gain de {len(deltas)} regiones")
    r["regiones"] = len(deltas)
    r["avisos"] = plan["avisos"] + comparten
    return r


def texto(plan):
    L = [f"nivelar a {plan['objetivo_db']} dBFS RMS (pico ≤ {plan['tope_pico_db']} dBFS): {plan['cambios']} pistas por cambiar"]
    if plan.get("parejas"):
        L.append("  canciones parejas: " + ", ".join(f"{k} {v:+.1f} dB" for k, v in plan["parejas"].items()))
    for f in plan["filas"]:
        if not f["delta_db"]:
            continue
        L.append(f"  {f['pista']}" + (f" ({f['cancion']})" if f["cancion"] else "") + f": RMS {f['rms_dbfs']}, pico {f['pico_dbfs']} → "
                 f"{f['delta_db']:+.2f} dB en {f['clips']} clips" + (" (acotado por el pico)" if f.get("acotado_por_pico") else ""))
    L += [f"  aviso: {a}" for a in plan["avisos"]]
    return "\n".join(L)

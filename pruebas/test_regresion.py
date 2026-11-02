"""Pruebas de regresión de trarin sobre sesiones chicas guardadas por Pro Tools 12
(`pruebas/sesiones/`): plantillas vacías a 44,1, 48 y 96 kHz, una con una pista
de cada tipo, una con una nota MIDI dibujada en Pro Tools (oráculo) y dos de
orden y canal. Sin dependencias: `python -m unittest discover -s pruebas`.

Lo que protegen:
- descifrar → cifrar devuelve los mismos bytes;
- aplicar sin cambios deja el modelo común igual;
- la revisión (lint) da limpio en lo que guardó Pro Tools y acusa un ancla
  distinta de 0 en un show;
- una nota MIDI escrita por trarin se lee igual que la que dibujó Pro Tools;
- quitar varias pistas en lote da los mismos bytes que de a una;
- reordenar pistas conserva contenido y tamaño;
- el tempo con ancla y el cambio de nombre de pista sobreviven la ida y vuelta.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["TRARIN_SIN_CACHE"] = "1"

SESIONES = RAIZ / "pruebas" / "sesiones"
VACIAS = ["trarin_vacia_44k.ptx", "trarin_vacia_48k.ptx", "trarin_vacia_96k.ptx"]
TODAS = VACIAS + ["trarin_pistas_48k.ptx", "PRUEBA_una_nota_vacia.ptx", "PRUEBA_una_nota.ptx",
                  "PRUEBA_orden_A.ptx", "PRUEBA_canal_A.ptx"]


def _sin_ids(comun):
    """El modelo sin lo que cambia entre lecturas sin significado (ids, nativo)."""
    import json
    c = json.loads(json.dumps(comun, ensure_ascii=False, default=str))
    c.pop("nativo", None)
    c.pop("origen", None)
    return c


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trarin_pruebas_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def salida(self, nombre):
        return str(self.tmp / nombre)


class Cifrado(Base):
    def test_descifrar_cifrar(self):
        from trarin.protools.descifrado import leer_descifrado
        from trarin.protools.reconstruccion import cifrar
        for n in TODAS:
            b = (SESIONES / n).read_bytes()
            self.assertEqual(cifrar(leer_descifrado(str(SESIONES / n))), b, n)


class IdaYVuelta(Base):
    def test_aplicar_sin_cambios(self):
        from trarin.comun import leer_comun, aplicar
        for n in TODAS:
            c = leer_comun(str(SESIONES / n))
            s = self.salida("iv_" + n)
            aplicar(c, str(SESIONES / n), s)
            self.assertEqual(_sin_ids(leer_comun(s)), _sin_ids(c), n)

    def test_tempo_y_ancla(self):
        from trarin.comun import leer_comun, aplicar
        from trarin.protools import tiempo as T
        f = str(SESIONES / "trarin_vacia_48k.ptx")
        c = leer_comun(f)
        c["tempo"] = [{"tick": 0, "bpm": 45.0, "pulso_ticks": T.TICKS_NEGRA, "inicio_segundos": 0.5},
                      {"tick": 8 * T.TICKS_NEGRA, "bpm": 90.0, "pulso_ticks": T.TICKS_NEGRA}]
        s = self.salida("tempo.ptx")
        aplicar(c, f, s)
        t = leer_comun(s)["tempo"]
        self.assertEqual([round(x["bpm"], 3) for x in t], [45.0, 90.0])
        self.assertEqual([x["tick"] for x in t], [0, 8 * T.TICKS_NEGRA])
        self.assertAlmostEqual(t[0].get("inicio_segundos", 0.0), 0.5, places=4)

    def test_renombrar_y_mezcla(self):
        from trarin.comun import leer_comun, aplicar
        f = str(SESIONES / "trarin_pistas_48k.ptx")
        c = leer_comun(f)
        p = next(x for x in c["pistas"] if x["nombre"] == "Audio 1")
        p["nombre"] = "Kultrun"
        p["mezcla"]["volumen_db"] = -6.5
        p["mezcla"]["mute"] = True
        s = self.salida("ren.ptx")
        aplicar(c, f, s)
        q = next(x for x in leer_comun(s)["pistas"] if x["nombre"] == "Kultrun")
        self.assertAlmostEqual(q["mezcla"]["volumen_db"], -6.5, places=1)
        self.assertTrue(q["mezcla"]["mute"])


class Revision(Base):
    def test_lo_de_pro_tools_da_limpio(self):
        from trarin.revision import revisar
        for n in TODAS:
            r = revisar(str(SESIONES / n))
            self.assertTrue(r["ok"], f"{n}: {r['errores']}")

    def test_acusa_ancla_en_show(self):
        from trarin.comun import leer_comun, aplicar
        from trarin.revision import revisar
        from trarin.protools import tiempo as T
        f = str(SESIONES / "trarin_vacia_48k.ptx")
        c = leer_comun(f)
        c["marcadores"] += [{"nombre": "Una", "tick": 0}, {"nombre": "Otra", "tick": 40 * T.TICKS_NEGRA}]
        c["tempo"] = [{"tick": 0, "bpm": 120.0, "pulso_ticks": T.TICKS_NEGRA, "inicio_segundos": 13.09}]
        s = self.salida("ancla.ptx")
        aplicar(c, f, s)
        r = revisar(s)
        self.assertFalse(r["ok"])
        self.assertTrue(any("tick 0 cae en 13.09" in e for e in r["errores"]), r["errores"])


class NotaMidi(Base):
    def test_una_nota_como_pro_tools(self):
        """La nota que dibujó Pro Tools (oráculo) escrita por trarin en la
        sesión vacía se lee igual: mismo tick, nota, largo y velocidad."""
        from trarin.comun import leer_comun, aplicar
        oraculo = leer_comun(str(SESIONES / "PRUEBA_una_nota.ptx"))
        clip = next(k for p in oraculo["pistas"] for k in p["clips"] if k.get("tipo") == "midi" and len(k["notas"]) == 1)
        f = str(SESIONES / "PRUEBA_una_nota_vacia.ptx")
        c = leer_comun(f)
        p = next(x for x in c["pistas"] if x["tipo"] == "midi")
        p["clips"].append({"tipo": "midi", "nombre": "nota", "tick": clip["tick"], "largo_ticks": clip["largo_ticks"],
                           "notas": [dict(clip["notas"][0])]})
        s = self.salida("nota.ptx")
        aplicar(c, f, s)
        q = next(x for x in leer_comun(s)["pistas"] if x["tipo"] == "midi")
        mio = next(k for k in q["clips"] if k.get("tipo") == "midi")
        self.assertEqual(mio["tick"], clip["tick"])
        self.assertEqual(mio["largo_ticks"], clip["largo_ticks"])
        self.assertEqual(mio["notas"], clip["notas"])
        from trarin.protools.midi_revision import revisar_midi
        self.assertEqual(revisar_midi(s)["vacias_en_pro_tools"], 0)


class Lotes(Base):
    def test_quitar_en_lote_igual_que_de_a_una(self):
        from trarin.protools.descifrado import leer_descifrado
        from trarin.protools.pistas_quitar import quitar_pistas
        d = leer_descifrado(str(SESIONES / "trarin_pistas_48k.ptx"))
        nombres = ["Audio 1", "MIDI 1", "Aux 2"]
        self.assertEqual(quitar_pistas(d, nombres), quitar_pistas(d, nombres, de_a_una=True))
        from trarin.comun import leer_comun
        from trarin.protools.reconstruccion import cifrar
        s = self.salida("menos.ptx")
        Path(s).write_bytes(cifrar(quitar_pistas(d, nombres)))
        self.assertEqual([p["nombre"] for p in leer_comun(s)["pistas"]], ["Audio 2", "Aux 1", "Inst 1"])


class Orden(Base):
    def test_reordenar(self):
        from trarin.comun import leer_comun
        from trarin.protools.descifrado import leer_descifrado
        from trarin.protools.pistas_orden import reordenar, verificar_orden
        from trarin.protools.reconstruccion import cifrar
        f = str(SESIONES / "PRUEBA_orden_A.ptx")
        c = leer_comun(f)
        self.assertEqual([p["nombre"] for p in c["pistas"]], ["Audio 2", "Audio 1"])
        d = leer_descifrado(f)
        d2 = reordenar(d, ["Audio 1", "Audio 2"])
        self.assertEqual(len(d2), len(d))
        s = self.salida("orden.ptx")
        Path(s).write_bytes(cifrar(d2))
        c2 = leer_comun(s)
        self.assertEqual([p["nombre"] for p in c2["pistas"]], ["Audio 1", "Audio 2"])
        self.assertEqual(verificar_orden(c, c2, ["Audio 1", "Audio 2"]), [])


class SalidasMidi(Base):
    def test_cambiar_canal(self):
        from trarin.protools.descifrado import leer_descifrado
        from trarin.protools.salidas_midi import leer_salidas, cambiar_salidas
        d = leer_descifrado(str(SESIONES / "PRUEBA_canal_A.ptx"))
        antes = leer_salidas(d)
        pista = next(iter(antes))
        if not antes[pista]:
            self.skipTest("la pista no tiene salida MIDI")
        canal = 4 if antes[pista]["canal"] != 4 else 5
        d2 = cambiar_salidas(d, {pista: {"canal": canal}})
        self.assertEqual(leer_salidas(d2)[pista]["canal"], canal)
        self.assertEqual(leer_salidas(d2)[pista]["puerto"], antes[pista]["puerto"])


if __name__ == "__main__":
    unittest.main()

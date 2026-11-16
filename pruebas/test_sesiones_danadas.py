"""Pruebas con sesiones dañadas a propósito: trarin las lee o falla con un
error propio y claro (ErrorSesion, ErrorDescifrado…), nunca con un error crudo
de Python (struct.error, IndexError, ZeroDivisionError) ni colgándose.
Sin dependencias: `python -m unittest discover -s pruebas`."""

import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["TRARIN_SIN_CACHE"] = "1"

SESIONES = RAIZ / "pruebas" / "sesiones"


def _es_de_trarin(e):
    return type(e).__module__.split(".")[0] == "trarin"


class SesionesDanadas(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trarin_danadas_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _leer(self, ruta):
        """None si se leyó; el error si falló. Falla la prueba si el error no es de trarin."""
        from trarin.comun import leer_comun
        try:
            leer_comun(str(ruta))
            return None
        except Exception as e:
            self.assertTrue(_es_de_trarin(e), f"{ruta.name}: error crudo {type(e).__name__}: {e}")
            return e

    def test_pro_tools_con_bytes_cambiados_cortada_o_con_basura(self):
        from trarin.protools.descifrado import descifrar, leer_descifrado
        azar = random.Random(7)                      # siempre los mismos casos
        fallos = 0
        for nombre in ("PRUEBA_grupos.ptx", "PRUEBA_una_nota.ptx", "trarin_pistas_48k.ptx"):
            d = leer_descifrado(str(SESIONES / nombre))
            for i in range(60):
                m = bytearray(d)
                modo = i % 4
                if modo == 0:                        # unos bytes cambiados
                    for _ in range(azar.randint(1, 8)):
                        m[azar.randrange(0x14, len(m))] = azar.randrange(256)
                elif modo == 1:                      # cortada
                    m = m[:azar.randrange(0x14, len(m))]
                elif modo == 2:                      # un entero enorme
                    k = azar.randrange(0x20, len(m) - 4)
                    m[k:k + 4] = b"\xff\xff\xff\x7f"
                else:                                # bytes de más
                    k = azar.randrange(0x20, len(m))
                    m[k:k] = bytes(azar.randrange(256) for _ in range(azar.randint(1, 64)))
                f = self.tmp / f"{i}_{nombre}"
                f.write_bytes(descifrar(bytes(m)))   # el XOR es simétrico: esto la vuelve a cifrar
                fallos += self._leer(f) is not None
                f.unlink()
        self.assertGreater(fallos, 0)                # entre 180 casos alguno tiene que fallar: si no, la prueba no prueba nada

    def test_archivos_que_no_son_sesiones(self):
        from trarin.errores import ErrorSesion
        for nombre, datos in (("vacia.ptx", b""), ("texto.ptx", b"hola"), ("ruido.ptx", bytes(range(256)) * 20),
                              ("ruido.als", bytes(range(256)) * 20), ("texto.als", b"<esto no cierra"),
                              ("ruido.flp", bytes(range(256)) * 20), ("corta.flp", b"FLhd\x06\x00")):
            f = self.tmp / nombre
            f.write_bytes(datos)
            e = self._leer(f)
            self.assertIsNotNone(e, nombre)
            if isinstance(e, ErrorSesion):
                self.assertIn(nombre, str(e))        # el mensaje dice de qué archivo habla

    def test_revisar_acusa_una_sesion_cortada(self):
        """Una sesión cortada muchas veces se lee sin error (lo que falta son
        bloques del final): la revisión es la que lo dice."""
        from trarin.revision import revisar
        for nombre in ("PRUEBA_una_nota.ptx", "PRUEBA_grupos.ptx", "trarin_vacia_48k.ptx"):
            crudo = (SESIONES / nombre).read_bytes()
            for parte in (0.3, 0.6, 0.9, 0.999):
                f = self.tmp / f"cortada_{nombre}"
                f.write_bytes(crudo[:int(len(crudo) * parte)])
                r = revisar(str(f), archivos_en_disco=False)
                self.assertFalse(r["ok"], f"{nombre} al {parte:.1%}")
                self.assertIn("cortado", r["errores"][0])

    def test_lo_que_no_existe_sigue_siendo_no_existe(self):
        from trarin.comun import leer_comun
        with self.assertRaises(FileNotFoundError):
            leer_comun(str(self.tmp / "no_esta.ptx"))


if __name__ == "__main__":
    unittest.main()

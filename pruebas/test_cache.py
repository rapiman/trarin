"""Pruebas de la caché en disco del modelo común: lo guardado se lee igual, y un
archivo que no sea solo datos (un pickle que intenta cargar código) se descarta
sin ejecutarlo. Sin dependencias: `python -m unittest discover -s pruebas`."""

import os
import pickle
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

SESIONES = RAIZ / "pruebas" / "sesiones"


class _Trampa:
    """Al abrirse con pickle.load llamaría a os.getcwd: inofensivo, pero es código."""
    def __reduce__(self):
        return (os.getcwd, ())


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trarin_cache_"))
        self.antes = {k: os.environ.get(k) for k in ("TRARIN_CACHE", "TRARIN_SIN_CACHE")}
        os.environ["TRARIN_CACHE"] = str(self.tmp)
        os.environ.pop("TRARIN_SIN_CACHE", None)

    def tearDown(self):
        for k, v in self.antes.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lo_guardado_se_lee_igual(self):
        from trarin import cache
        from trarin.comun import leer_comun
        for f in sorted(SESIONES.glob("*.ptx")):
            primero = leer_comun(str(f))                         # calcula y guarda
            guardado = cache.leer(str(f), "comun")
            self.assertIsNotNone(guardado, f"{f.name}: el modelo no quedó en la caché")
            self.assertEqual(guardado, primero, f.name)
            self.assertEqual(leer_comun(str(f)), primero, f.name)  # la segunda lectura sale de la caché
        self.assertEqual(len(list(self.tmp.glob("*.pkl"))), len(list(SESIONES.glob("*.ptx"))))

    def test_un_pickle_con_codigo_se_descarta(self):
        from trarin import cache
        f = str(SESIONES / "trarin_vacia_48k.ptx")
        destino = cache.carpeta() / f"{cache._llave(f, 'comun')}.pkl"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(pickle.dumps(_Trampa()))
        self.assertEqual(pickle.loads(destino.read_bytes()), os.getcwd())   # la trampa funciona con pickle a secas…
        self.assertIsNone(cache.leer(f, "comun"))                           # …y la caché no la abre
        destino.write_bytes(b"esto no es un pickle")
        self.assertIsNone(cache.leer(f, "comun"))


if __name__ == "__main__":
    unittest.main()

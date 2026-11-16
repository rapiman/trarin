"""Pruebas de los trabajos largos (trarin/trabajos.py): lo que llega desde el
cliente (el id, qué correr después) se valida, y un trabajo terminado no se
puede «matar» (su número de proceso puede ser hoy de otro programa).
Sin dependencias: `python -m unittest discover -s pruebas`."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


class Trabajos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trarin_trabajos_")
        self.antes = os.environ.get("TRARIN_TRABAJOS")
        os.environ["TRARIN_TRABAJOS"] = self.tmp

    def tearDown(self):
        if self.antes is None:
            os.environ.pop("TRARIN_TRABAJOS", None)
        else:
            os.environ["TRARIN_TRABAJOS"] = self.antes
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_id_que_no_es_un_id(self):
        from trarin import trabajos
        afuera = Path(self.tmp).parent / "trarin_afuera_de_trabajos"
        afuera.mkdir(exist_ok=True)
        try:
            for malo in ("", "x", "..", "../" + afuera.name, "..\\" + afuera.name, "20260101-000000-zzzz",
                         "20260101-000000-abcd/..", None, 7):
                for f in (trabajos.estado, trabajos.matar):
                    with self.assertRaises(ValueError, msg=repr(malo)):
                        f(malo)
        finally:
            afuera.rmdir()

    def test_despues_solo_de_la_lista(self):
        from trarin import trabajos
        with self.assertRaises(ValueError):
            trabajos.lanzar("bpm", {"audios": []}, despues=("os", "getcwd"))
        with self.assertRaises(ValueError):
            trabajos.lanzar("bpm", {"audios": []}, despues="otra_cosa")
        self.assertEqual(os.listdir(self.tmp), [])           # no alcanzó a crear nada
        self.assertEqual(trabajos.DESPUES["verificar"]({}, {}), {})      # sin salida .ptx no hay nada que verificar

    def test_reconoce_al_proceso_por_su_inicio(self):
        from trarin import trabajos
        yo = os.getpid()
        marca = trabajos._inicio_proceso(yo)
        self.assertTrue(marca)
        self.assertTrue(trabajos._vivo(yo))
        self.assertTrue(trabajos._vivo(yo, marca))
        self.assertFalse(trabajos._vivo(yo, "otra marca"))   # mismo número, otro proceso
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(trabajos._vivo(p.pid))              # ya terminó

    def test_un_trabajo_terminado_no_se_mata(self):
        from trarin import trabajos
        ident = trabajos.lanzar("bpm", {"audios": []})
        self.assertRegex(ident, r"^\d{8}-\d{6}-[0-9a-f]{4}$")
        pid, marca = trabajos._leer_pid(Path(self.tmp) / ident)
        self.assertTrue(pid)
        tope = time.time() + 60
        e = trabajos.estado(ident)
        while e["estado"] == "corriendo" and time.time() < tope:
            time.sleep(0.2)
            e = trabajos.estado(ident)
        self.assertIn(e["estado"], ("terminado", "error"), e)
        r = trabajos.matar(ident)
        self.assertFalse(r["matado"])
        self.assertEqual(trabajos.estado(ident)["estado"], e["estado"])   # matar no cambió nada

    def test_matar_uno_que_corre_y_no_a_un_impostor(self):
        """Con el pid de un proceso vivo: si la marca de inicio es la suya, se
        mata; si es otra (el número pasó a otro programa), no se toca."""
        import json
        from trarin import trabajos
        opciones = {} if os.name == "nt" else {"start_new_session": True}
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **opciones)
        try:
            for ident, marca, debe_matar in (("20260101-000000-aaaa", "otra marca", False),
                                             ("20260101-000000-bbbb", trabajos._inicio_proceso(p.pid), True)):
                c = Path(self.tmp) / ident
                c.mkdir()
                (c / "pedido.json").write_text(json.dumps({"tarea": "bpm", "argumentos": {}, "inicio": time.time()}))
                (c / "pid").write_text(f"{p.pid} {marca}")
                self.assertEqual(trabajos.estado(ident)["estado"], "corriendo" if debe_matar else "interrumpido")
                self.assertEqual(trabajos.matar(ident)["matado"], debe_matar)
                if not debe_matar:
                    self.assertIsNone(p.poll())               # sigue vivo
            p.wait(timeout=15)
            self.assertEqual(trabajos.estado("20260101-000000-bbbb")["estado"], "error")
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()


if __name__ == "__main__":
    unittest.main()

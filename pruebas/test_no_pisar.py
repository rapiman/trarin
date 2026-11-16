"""Pruebas de que trarin no pisa lo que ya existe: la configuración de Claude
Desktop al instalar, la sesión de trabajo al reemplazarla y los archivos de
audio al convertir. Sin dependencias: `python -m unittest discover -s pruebas`."""

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["TRARIN_SIN_CACHE"] = "1"

SESIONES = RAIZ / "pruebas" / "sesiones"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trarin_no_pisar_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Instalar(Base):
    def test_respalda_y_conserva_lo_demas(self):
        from trarin import instalar
        cfg = self.tmp / "claude_desktop_config.json"
        antes = {"otraCosa": 1, "mcpServers": {"otro": {"command": "x"},
                                               "trarin": {"command": "viejo", "env": {"PYTHONPATH": "/algun/lado"}}}}
        cfg.write_text(json.dumps(antes), encoding="utf-8")
        r = instalar.instalar(str(cfg), python="py")
        self.assertEqual(json.loads(Path(r["respaldo"]).read_text(encoding="utf-8")), antes)
        d = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(d["otraCosa"], 1)
        self.assertEqual(d["mcpServers"]["otro"], {"command": "x"})
        self.assertEqual(d["mcpServers"]["trarin"], {"command": "py", "args": ["-m", "trarin", "mcp"],
                                                     "env": {"PYTHONPATH": "/algun/lado", "PYTHONIOENCODING": "utf-8"}})
        self.assertEqual(r["antes"]["command"], "viejo")

    def test_configuracion_nueva(self):
        from trarin import instalar
        cfg = self.tmp / "sub" / "claude_desktop_config.json"
        r = instalar.instalar(str(cfg), python="py")
        self.assertIsNone(r["respaldo"])
        self.assertIn("trarin", json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"])

    def test_no_toca_una_configuracion_que_no_entiende(self):
        from trarin import instalar
        cfg = self.tmp / "claude_desktop_config.json"
        for malo in ('{"mcpServers": {', '[1, 2]', '{"mcpServers": 3}'):
            cfg.write_text(malo, encoding="utf-8")
            with self.assertRaises(ValueError, msg=malo):
                instalar.instalar(str(cfg), python="py")
            self.assertEqual(cfg.read_text(encoding="utf-8"), malo)
        self.assertEqual([x.name for x in self.tmp.iterdir()], [cfg.name])     # ni respaldos ni temporales


class Reemplazar(Base):
    def setUp(self):
        super().setUp()
        self.sesion = self.tmp / "show.ptx"
        shutil.copy(SESIONES / "trarin_vacia_48k.ptx", self.sesion)
        self.original = self.sesion.read_bytes()

    def test_la_nueva_tiene_que_ser_una_sesion(self):
        from trarin import respaldos
        basura = self.tmp / "basura.ptx"
        basura.write_bytes(b"esto no es una sesion de Pro Tools" * 10)
        otra = self.tmp / "nueva.als"
        shutil.copy(SESIONES / "trarin_pistas_48k.ptx", otra)
        for nueva in (basura, otra):
            with self.assertRaises(respaldos.ErrorRespaldo, msg=nueva.name):
                respaldos.reemplazar(self.sesion, nueva)
            self.assertEqual(self.sesion.read_bytes(), self.original)
            self.assertTrue(nueva.exists())
        self.assertEqual(respaldos.listar(self.sesion), [])

    def test_reemplazar_y_deshacer(self):
        from trarin import respaldos
        nueva = self.tmp / "nueva.ptx"
        shutil.copy(SESIONES / "trarin_pistas_48k.ptx", nueva)
        contenido = nueva.read_bytes()
        r = respaldos.reemplazar(self.sesion, nueva, "prueba")
        self.assertEqual(self.sesion.read_bytes(), contenido)
        self.assertEqual(Path(r["respaldo"]).read_bytes(), self.original)
        self.assertFalse(nueva.exists())
        d = respaldos.deshacer(self.sesion)
        self.assertEqual(self.sesion.read_bytes(), self.original)
        self.assertEqual(Path(d["deshecho"]).read_bytes(), contenido)      # lo deshecho tampoco se pierde


def _wav_estereo(ruta, muestras=100):
    datos = struct.pack("<" + "h" * (2 * muestras), *([1000, -1000] * muestras))
    fmt = struct.pack("<HHIIHH", 1, 2, 48000, 48000 * 4, 4, 16)
    with open(ruta, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(datos)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
                + b"data" + struct.pack("<I", len(datos)) + datos)


class ConvertirAudio(Base):
    def test_destinos(self):
        from trarin import audio
        nombres = lambda *a: [x.name for x in audio.destinos(*a)]
        self.assertEqual(nombres(self.tmp / "voz48", 2), ["voz48.wav"])
        self.assertEqual(nombres(self.tmp / "voz48.wav", 2, True), ["voz48.L.wav", "voz48.R.wav"])
        self.assertEqual(nombres(self.tmp / "voz.L_48k", 1, True), ["voz.L_48k.wav"])
        self.assertEqual(nombres(self.tmp / "sala", 3, True), ["sala.1.wav", "sala.2.wav", "sala.3.wav"])

    def test_no_pisa_un_canal_que_ya_existe(self):
        from trarin import audio
        entrada = self.tmp / "voz.wav"
        _wav_estereo(entrada)
        (self.tmp / "voz48.R.wav").write_bytes(b"no me pises")
        with self.assertRaises(audio.ErrorAudio):
            audio.convertir(entrada, self.tmp / "voz48", 48000, mono=True, nuevo=True)
        self.assertEqual((self.tmp / "voz48.R.wav").read_bytes(), b"no me pises")
        self.assertFalse((self.tmp / "voz48.L.wav").exists())               # tampoco escribió el otro canal


if __name__ == "__main__":
    unittest.main()

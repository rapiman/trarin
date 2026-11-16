"""Prueba de humo del servidor MCP: se arma, publica las herramientas esperadas
(todas con descripción), una herramienta que lee responde y un error llega con
un mensaje que se entiende. Se salta si el paquete `mcp` no está instalado
(`pip install "trarin[mcp]"`)."""

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

SESIONES = RAIZ / "pruebas" / "sesiones"

ESPERADAS = {
    "resumen_sesion", "detalle_pista", "rutas_sesion", "archivos_audio", "leer_sesion", "comparar_sesiones",
    "comparar_con_original", "uniones", "revisar", "revisar_midi", "paneo_live", "notas", "anotar",
    "compatibilidad", "planear_show", "armar_show", "hoja_show", "luces", "plan_consolidar", "consolidar",
    "plan_grupos", "crear_grupos", "plantillas", "ordenar_pistas", "editar_clips", "cruces", "marcar_secciones",
    "reparar_midi", "salidas_midi", "golpes_a_midi", "nivelar", "proponer_tempo", "escribir_sesion",
    "reemplazar_sesion", "respaldos_sesion", "deshacer", "fichas_calcular", "ficha", "ficha_midi", "parecidos",
    "buscar_sonidos", "mapa_cancion", "que_suena", "revisar_niveles", "onda", "escuchar", "exportar_pizarrita",
    "medir_bpm", "convertir_audio", "trabajo_esperar", "trabajo_estado", "trabajo_matar", "trabajos_recientes",
}


@unittest.skipUnless(importlib.util.find_spec("mcp"), 'falta el paquete mcp (pip install "trarin[mcp]")')
class Servidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trarin.servidor_mcp import crear
        cls.srv = crear()

    def test_herramientas(self):
        herramientas = asyncio.run(self.srv.list_tools())
        nombres = {h.name for h in herramientas}
        self.assertEqual(nombres - ESPERADAS, set(), "herramientas nuevas: agregarlas aquí y al README")
        self.assertEqual(ESPERADAS - nombres, set(), "herramientas que ya no están")
        self.assertEqual([h.name for h in herramientas if not (h.description or "").strip()], [])

    def test_una_que_lee_responde(self):
        r = asyncio.run(self.srv.call_tool("rutas_sesion", {"ruta": str(SESIONES / "trarin_pistas_48k.ptx")}))
        self.assertIn("Bus 1-2", str(r))

    def _llamar(self, nombre, argumentos):
        """El texto de la respuesta o del error (según la versión de mcp, el error se lanza o viene en el resultado)."""
        try:
            return str(asyncio.run(self.srv.call_tool(nombre, argumentos)))
        except Exception as e:
            return str(e)

    def test_anotaciones(self):
        """Las que solo leen lo dicen (el cliente puede aprobarlas solas); las que mueven
        la sesión o detienen un proceso, también; el resto, ninguna de las dos."""
        def marca(h, camel, snake):
            a = h.annotations
            return None if a is None else (getattr(a, snake, None) if hasattr(a, snake) else getattr(a, camel, None))
        por_nombre = {h.name: h for h in asyncio.run(self.srv.list_tools())}
        if por_nombre["revisar"].annotations is None:
            self.skipTest("esta versión de mcp no tiene anotaciones")
        from trarin.servidor_mcp import MUEVEN_O_DETIENEN, SOLO_LEEN
        self.assertEqual(SOLO_LEEN - ESPERADAS, set())
        self.assertEqual(MUEVEN_O_DETIENEN - ESPERADAS, set())
        for nombre, h in por_nombre.items():
            self.assertEqual(bool(marca(h, "readOnlyHint", "read_only_hint")), nombre in SOLO_LEEN, nombre)
            self.assertEqual(bool(marca(h, "destructiveHint", "destructive_hint")), nombre in MUEVEN_O_DETIENEN, nombre)
        for nombre in ("armar_show", "consolidar", "escribir_sesion", "nivelar", "reemplazar_sesion", "deshacer"):
            self.assertNotIn(nombre, SOLO_LEEN)

    def test_carpetas_permitidas(self):
        """Con TRARIN_RAICES, lo que está afuera no se lee ni se escribe; sin ella, no hay límite."""
        import os
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="trarin_raices_"))
        antes = os.environ.get("TRARIN_RAICES")
        try:
            adentro = tmp / "permitida"
            adentro.mkdir()
            shutil.copy(SESIONES / "trarin_pistas_48k.ptx", adentro / "s.ptx")
            afuera = str(SESIONES / "trarin_pistas_48k.ptx")
            os.environ["TRARIN_RAICES"] = str(adentro)
            self.assertIn("Bus 1-2", self._llamar("rutas_sesion", {"ruta": str(adentro / "s.ptx")}))
            for nombre, argumentos in (
                    ("rutas_sesion", {"ruta": afuera}),
                    ("notas", {"sesion": afuera}),
                    ("compatibilidad", {"sesiones": [str(adentro / "s.ptx"), afuera]}),
                    ("planear_show", {"show": {"canciones": [{"sesion": afuera}]}}),
                    ("reparar_midi", {"sesion": str(adentro / "s.ptx"), "salida": str(tmp / "afuera.ptx")}),
                    ("plan_grupos", {"sesion": str(adentro / "s.ptx"), "reglas": str(tmp / "reglas.json")}),
            ):
                self.assertIn("TRARIN_RAICES", self._llamar(nombre, argumentos), nombre)
            self.assertFalse((tmp / "afuera.ptx").exists())
            os.environ.pop("TRARIN_RAICES")
            self.assertIn("Bus 1-2", self._llamar("rutas_sesion", {"ruta": afuera}))
        finally:
            if antes is None:
                os.environ.pop("TRARIN_RAICES", None)
            else:
                os.environ["TRARIN_RAICES"] = antes
            shutil.rmtree(tmp, ignore_errors=True)

    def test_el_error_llega_con_su_mensaje(self):
        try:
            texto = str(asyncio.run(self.srv.call_tool("resumen_sesion", {"ruta": str(SESIONES / "no_esta.ptx")})))
        except Exception as e:                      # según la versión de mcp, el error se lanza o viene en el resultado
            texto = str(e)
        self.assertIn("no existe", texto)


if __name__ == "__main__":
    unittest.main()

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

    def test_el_error_llega_con_su_mensaje(self):
        try:
            texto = str(asyncio.run(self.srv.call_tool("resumen_sesion", {"ruta": str(SESIONES / "no_esta.ptx")})))
        except Exception as e:                      # según la versión de mcp, el error se lanza o viene en el resultado
            texto = str(e)
        self.assertIn("no existe", texto)


if __name__ == "__main__":
    unittest.main()

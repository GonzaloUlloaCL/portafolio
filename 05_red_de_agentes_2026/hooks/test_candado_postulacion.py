"""Pruebas de candado_postulacion.py con los errores reales del 06-oct-2026."""
import unittest

from candado_postulacion import revisar_accion


def js(codigo):
    return {"tool_name": "mcp__claude-in-chrome__javascript_tool", "tool_input": {"text": codigo}}


def escribir(texto):
    return {"tool_name": "mcp__claude-in-chrome__computer", "tool_input": {"action": "type", "text": texto}}


def formulario(valor):
    return {"tool_name": "mcp__claude-in-chrome__form_input", "tool_input": {"value": valor}}


ENVIO = "const fd=new FormData(f);fd.set('app_letter','Hola');await fetch('/trabajo/postular/send/1',{method:'POST',body:fd})"


class Bloquea(unittest.TestCase):
    def test_envio_masivo_sin_marcador(self):
        self.assertTrue(revisar_accion(js(ENVIO)))

    def test_anos_inflados_tipeados(self):
        self.assertTrue(revisar_accion(escribir("Soy Ingeniero Industrial con más de 10 años en operaciones.")))

    def test_anos_inflados_en_formulario(self):
        self.assertTrue(revisar_accion(formulario("Industrial Engineer with over 10 years in data")))

    def test_genero_vacio(self):
        self.assertTrue(revisar_accion(formulario("I choose not to self-identify")))

    def test_anos_inflados_dentro_de_envio_con_marcador(self):
        codigo = "/*CANDADO_OFERTAS_LEIDAS*/ " + ENVIO + " 'Tengo más de 10 años de experiencia'"
        self.assertTrue(revisar_accion(js(codigo)))


class Pasa(unittest.TestCase):
    def test_envio_con_marcador_y_texto_limpio(self):
        self.assertEqual(revisar_accion(js("/*CANDADO_OFERTAS_LEIDAS*/ " + ENVIO)), [])

    def test_desde_2014(self):
        self.assertEqual(revisar_accion(escribir("Trabajo en operaciones desde 2014, más de 7 años de experiencia.")), [])

    def test_otra_persona(self):
        self.assertEqual(
            revisar_accion(escribir("CANDADO_OTRA_PERSONA Gregorick tiene con más de 12 años de experiencia en logística")),
            [],
        )

    def test_javascript_que_solo_lee(self):
        self.assertEqual(revisar_accion(js("document.title")), [])

    def test_clic_sin_texto(self):
        self.assertEqual(revisar_accion({"tool_name": "mcp__claude-in-chrome__computer", "tool_input": {"action": "left_click"}}), [])


if __name__ == "__main__":
    unittest.main()

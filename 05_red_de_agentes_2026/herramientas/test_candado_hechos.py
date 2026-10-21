"""Tests de candado_hechos.py con frases reales de los casos del 25-sep-2026."""
import unittest

from candado_hechos import revisar

# --- deben RECHAZARSE (frases reales de errores que ocurrieron) ---
MEZCLA_CENCOSUD = (
    "Trabajé en operación de última milla en un darkstore de Cencosud y conozco de cerca "
    "el rechazo en contra entrega."
)
POS_IMPLEMENTE = (
    "Diseñé e implementé un POS completo (Python/FastAPI/SQLite) con más de 4.400 clientes."
)
Q6_INFLADO = (
    "Python (basic-to-intermediate, actively used): I've built a FastAPI backend. "
    "SQL intermedio. Uso Python sobre SAP Business One."
)
BASH_DIARIO = "Uso Bash a diario para automatizar y correr scripts."
CIVIL = "Ingeniero Civil Industrial con 10 años en operaciones."
AGRO_PRESENTE = "Actualmente trabajo en Agrosystems como analista de datos."
CIFRA_PROHIBIDA = "Automaticé el 90% de los procesos del ERP."

AÑOS_INFLADOS = "Soy Ingeniero Industrial con más de 10 años en operaciones y abastecimiento."
AÑOS_INFLADOS_EN = "Industrial Engineer with over 10 years in data."
GENERO_VACIO = "I choose not to self-identify"

# --- deben PASAR (textos ya enviados o aprobados) ---
ISMAEL = (
    "Lo viví en mi propia tienda de dropshipping con pago contra entrega. Lo que aprendí es "
    "que la entrega falla sobre todo por dos cosas: un transportista poco confiable, y un "
    "cliente que no tenía claro que pagaba al recibir."
)
FORMULA_IA = "Diseñé y dirigí la construcción con IA de un POS y un bot de WhatsApp."
SCRAPING = (
    "Comparo una muestra de filas contra su PDF, confirmo que el total de filas coincida "
    "y te entrego una lista de lo que no pude vincular."
)
CENCOSUD_SOLO = "Trabajé en control logístico de última milla para el darkstore SPID de Cencosud."


class Rechazos(unittest.TestCase):
    def rechaza(self, texto, fragmento):
        motivos = " | ".join(revisar(texto)).lower()
        self.assertIn(fragmento, motivos, f"debía rechazar por '{fragmento}': {motivos!r}")

    def test_mezcla_cencosud_contra_entrega(self):
        self.rechaza(MEZCLA_CENCOSUD, "contra entrega")

    def test_autoria_implemente_sin_ia(self):
        self.rechaza(POS_IMPLEMENTE, "autoria")

    def test_nivel_inflado(self):
        self.rechaza(Q6_INFLADO, "nivel")

    def test_bash_diario(self):
        self.rechaza(BASH_DIARIO, "nivel")

    def test_titulo_civil(self):
        self.rechaza(CIVIL, "civil")

    def test_agrosystems_presente(self):
        self.rechaza(AGRO_PRESENTE, "agrosystems")

    def test_años_inflados(self):
        self.rechaza(AÑOS_INFLADOS, "experiencia total")
        self.rechaza(AÑOS_INFLADOS_EN, "experiencia total")

    def test_genero_vacio(self):
        self.rechaza(GENERO_VACIO, "genero")

    def test_frase_prohibida_del_cv(self):
        self.rechaza(CIFRA_PROHIBIDA, "frase prohibida")


class Aprobados(unittest.TestCase):
    def test_pasan(self):
        for nombre, texto in [
            ("ismael", ISMAEL),
            ("formula_ia", FORMULA_IA),
            ("scraping", SCRAPING),
            ("cencosud_solo", CENCOSUD_SOLO),
            ("desde_2014", "Trabajo en operaciones y abastecimiento desde 2014, con más de 7 años de experiencia."),
        ]:
            with self.subTest(nombre):
                self.assertEqual(revisar(texto), [], f"{nombre} no debía rechazarse")


if __name__ == "__main__":
    unittest.main()

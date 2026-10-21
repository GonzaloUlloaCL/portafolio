#!/usr/bin/env python3
"""
Candado de hechos: rechaza un texto (propuesta, post, respuesta de formulario)
que repite un error de contexto que ya ocurrio con Gonza.

Es deterministico: no llama a ningun modelo. Detecta marcas conocidas; NO garantiza
que un texto sea verdadero. Lo demas lo revisa una persona.

Reglas: `nucleo/sobre_mi/hechos_verificados.md`.

Uso:
    python3 candado_hechos.py archivo.txt      # o "-" para leer stdin
    Devuelve 0 si pasa y 1 si lo rechaza (mismo contrato que candado_voz.py).
"""

import ast
import re
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent

FLAGS = re.IGNORECASE

# Verbos de autoria que solo valen si el mismo texto dice que fue con IA.
VERBOS_AUTORIA = (
    r"desarroll(?:é|e|ó)|program(?:é|e)|escrib(?:í|i)|codifiqu(?:é|e)|implement(?:é|e)"
)
COSAS_TECNICAS = r"python|sql|c[oó]digo|script|bot\b|agente|api\b|pos\b|sistema|backend|dashboard"
MARCA_IA = r"con ia|con claude|con alma|dirig|asistid|apoyo de ia|con inteligencia artificial|ayuda de ia"

ULTIMA_MILLA = r"cencosud|darkstore|spid|[uú]ltima milla|rocktruck"
CONTRA_ENTREGA = r"contra ?entrega|contra ?reembolso|contrareembolso|pago al recibir"

REGLAS = [
    (
        "contexto mezclado: la ultima milla de Cencosud no es contra entrega",
        lambda t, o: _cerca(t, ULTIMA_MILLA, CONTRA_ENTREGA, 250),
    ),
    (
        "autoria: 'desarrolle/programe/implemente' sobre trabajo tecnico sin decir que fue "
        "con IA. Usar 'diseñe y dirigi la construccion con IA'",
        lambda t, o: _autoria_sin_ia(t),
    ),
    (
        "vigencia: Agrosystems termino el 21-ago-2026, se habla en pasado",
        lambda t, o: re.search(
            r"(trabajo|laboro|soy .{0,40}|actualmente|hoy)\s+(actualmente\s+)?(en|de)\s+agrosystems"
            r"|uso\s+(hoy\s+)?python\s+sobre\s+sap",
            t,
            FLAGS,
        ),
    ),
    (
        "nivel: no se declara Python o SQL 'intermedio/avanzado' ni shell 'diario' "
        "(nivel decidido: leo y verifico codigo basico; construyo con IA)",
        lambda t, o: re.search(
            r"(python|sql)\s*(\(|:|-|,)?\s*(nivel\s+)?(intermedio|avanzado|experto)"
            r"|(intermedio|avanzado|experto)\s+en\s+(python|sql)"
            r"|(bash|shell)[^.\n]{0,50}(a diario|diari|daily)",
            t,
            FLAGS,
        ),
    ),
    (
        "titulo: es Ingeniero Industrial, nunca Civil",
        lambda t, o: re.search(r"ingenier[oa]?\s+civil|ingenier[ií]a\s+civil|civil engineer", t, FLAGS),
    ),
    (
        "experiencia total: no decir 'mas de 8/10 años' de experiencia (por fechas son ~7 años 9 "
        "meses laborales: 2014-2019 y 2024-2026). Decir 'desde 2014' o 'mas de 7 años'",
        lambda t, o: re.search(
            r"(tengo|cuento con|llevo|poseo|con|de|over|more than|\+)\s*(m[aá]s de\s*|casi\s*|over\s*|more than\s*)?"
            r"(8|9|1\d|2\d)\+?\s*(a[ñn]os|years)\s*(de\s+|of\s+|in\s+|en\s+)?"
            r"(experiencia|experience|operaci|log[ií]stic|datos|data|abastec|supply|proces|inventar)",
            t,
            FLAGS,
        ),
    ),
    (
        "genero: Gonza es hombre; en formularios va Male/Hombre, nunca 'prefiero no responder' ni vacio",
        lambda t, o: re.search(
            r"prefiero no (responder|identificar|decir)|prefer not to (answer|say|disclose)"
            r"|not to self-?identify|choose not to (self-)?identify|no especificad[oa]",
            t,
            FLAGS,
        ),
    ),
]


def _cerca(texto, a, b, distancia):
    """True si un match de `a` y uno de `b` estan a menos de `distancia` caracteres."""
    pos_a = [m.start() for m in re.finditer(a, texto, FLAGS)]
    pos_b = [m.start() for m in re.finditer(b, texto, FLAGS)]
    return any(abs(x - y) <= distancia for x in pos_a for y in pos_b)


def _autoria_sin_ia(texto):
    """True si una oracion tiene verbo de autoria + cosa tecnica y ninguna marca de IA."""
    oraciones = re.split(r"(?<=[.!?\n])\s+", texto)
    for i, oracion in enumerate(oraciones):
        if not re.search(VERBOS_AUTORIA, oracion, FLAGS):
            continue
        if not re.search(COSAS_TECNICAS, oracion, FLAGS):
            continue
        contexto = " ".join(oraciones[max(0, i - 1): i + 2])
        if not re.search(MARCA_IA, contexto, FLAGS):
            return True
    return False


def frases_prohibidas_cv():
    """Lee FRASES_PROHIBIDAS de generar_cv.py sin importarlo (el modulo hace trabajo pesado)."""
    fuente = (AQUI / "generar_cv.py").read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and any(
            getattr(t, "id", "") == "FRASES_PROHIBIDAS" for t in nodo.targets
        ):
            return list(ast.literal_eval(nodo.value))
    return []


def revisar(texto):
    """Devuelve la lista de motivos por los que el texto se rechaza."""
    motivos = []
    for motivo, prueba in REGLAS:
        if prueba(texto, None):
            motivos.append(motivo)
    for patron in frases_prohibidas_cv():
        if re.search(patron, texto, FLAGS):
            motivos.append(f"frase prohibida del CV: /{patron}/")
    return motivos


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    origen = sys.argv[1]
    texto = sys.stdin.read() if origen == "-" else Path(origen).read_text(encoding="utf-8")
    motivos = revisar(texto)
    if motivos:
        print("RECHAZADO:")
        for m in motivos:
            print(f"  - {m}")
        return 1
    print("PASA (recuerda: pasar el candado no prueba que el texto sea verdadero)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

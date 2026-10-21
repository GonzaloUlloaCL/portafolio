#!/usr/bin/env python3
"""Candado de postulaciones (hook PreToolUse sobre las herramientas de Chrome).

Nace el 06-oct-2026: ese día se enviaron ~95 postulaciones con "más de 10 años de
experiencia" (eran ~7 años 9 meses) y ~21 a cargos fuera del perfil, por no validar.

Bloquea (exit 2, el motivo vuelve a Claude) cuando una acción de navegador:
  1. escribe o rellena texto que `candado_hechos.revisar` rechaza (años inflados, género
     vacío, nivel de Python/SQL, título "civil", frases prohibidas del CV...), o
  2. es un envío masivo por código (`postular/send` o `FormData` hacia un formulario) sin el
     marcador `CANDADO_OFERTAS_LEIDAS`. El marcador solo se escribe si CADA oferta se leyó
     completa y se juzgó contra el perfil; elegir por título no vale.

Escape para textos sobre otra persona (CV de un cliente, mensajes a prospectos): incluir
`CANDADO_OTRA_PERSONA` en el texto. Si algo falla al leer la entrada, deja pasar: un
candado roto no debe trabar la sesión.
Pruebas: python3 test_candado_postulacion.py
"""
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "nucleo" / "automatizaciones" / "claude_scripts"))

MARCADOR_LEIDAS = "CANDADO_OFERTAS_LEIDAS"
MARCADOR_OTRA = "CANDADO_OTRA_PERSONA"
ENVIO_MASIVO = re.compile(r"postular/send|/send/|FormData\s*\(", re.IGNORECASE)
ESCRIBE_EN_FORMULARIO = re.compile(
    r"FormData|\.set\(|getOwnPropertyDescriptor|\.value\s*=|dispatchEvent|innerText\s*=",
    re.IGNORECASE,
)


def textos_de(entrada):
    """Junta los textos que la acción escribiría en una página."""
    datos = entrada.get("tool_input", {}) or {}
    nombre = entrada.get("tool_name", "")
    partes = []
    if nombre.endswith("__computer") and datos.get("action") == "type":
        partes.append(str(datos.get("text", "")))
    elif nombre.endswith("__form_input"):
        partes.append(str(datos.get("value", "")))
    elif nombre.endswith("__javascript_tool"):
        codigo = str(datos.get("text", ""))
        if ESCRIBE_EN_FORMULARIO.search(codigo):
            partes.append(codigo)
    return "\n".join(partes)


def revisar_accion(entrada):
    """Devuelve la lista de motivos de bloqueo (vacía si la acción pasa)."""
    texto = textos_de(entrada)
    if not texto or MARCADOR_OTRA in texto:
        return []
    motivos = []
    if ENVIO_MASIVO.search(texto) and MARCADOR_LEIDAS not in texto:
        motivos.append(
            "envío por código sin el marcador CANDADO_OFERTAS_LEIDAS: lee cada oferta completa "
            "y júzgala contra el perfil antes de enviar; elegir por título no alcanza"
        )
    try:
        from candado_hechos import revisar
        motivos.extend(revisar(texto))
    except Exception:
        pass
    return motivos


def main():
    try:
        entrada = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        motivos = revisar_accion(entrada)
    except Exception:
        return 0
    if motivos:
        print("CANDADO DE POSTULACIÓN: acción bloqueada.", file=sys.stderr)
        for m in motivos:
            print(f"  - {m}", file=sys.stderr)
        print(
            "Corrige el texto con los hechos de nucleo/sobre_mi/hechos_verificados.md y reintenta.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Candado de español neutro (hook Stop).

Lee el último mensaje del asistente en el transcript y, si encuentra voseo o jerga
rioplatense, bloquea el cierre del turno y devuelve el motivo para que se reescriba.
Ignora bloques de código, código en línea, palabras entre comillas y citas (`>`), que pueden
traer la voz de otro o mencionar una palabra sin usarla.

Si algo falla al leer el transcript, deja pasar: un candado roto no debe trabar la sesión.
Si `stop_hook_active` viene en true (ya se pidió una reescritura), deja pasar para no
entrar en bucle.

Mantenimiento: giro nuevo que se escapa -> FORMAS_VOSEO; falso positivo -> sacarlo de ahí.
"escribí" y "abrí" se sacaron el 06-oct-2026: son iguales al pretérito de primera persona ("yo escribí").
Pruebas: python3 test_candado_espanol_neutro.py
"""
import json
import re
import sys

FORMAS_VOSEO = [
    # presente / voseo verbal
    "tenés", "podés", "querés", "sabés", "hacés", "decís", "ponés", "venís", "salís",
    "pensás", "necesitás", "buscás", "creés", "vivís", "sos", "vos", "mirás", "usás",
    "estás seguro", "andás", "ves vos",
    # imperativos
    "usá", "mirá", "verificá", "revisá", "probá", "dejá", "tomá", "andá", "vení",
    "poné", "decime", "avisame", "dejame", "contame", "mandame", "pasame", "haceme",
    "fijate", "acordate", "sentate", "corré", "fijá", "pegá",
    "mostrame", "decile", "pedime",
    # jerga rioplatense
    "che", "laburo", "laburar", "quilombo", "capaz que", "bárbaro", "re bien",
    "boludo", "pibe", "guita",
]

_CODIGO = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
# Palabras citadas entre comillas ("decime"): se mencionan, no se usan.
_CITADO = re.compile(r'"[^"\n]{1,80}"|“[^”\n]{1,80}”|«[^»\n]{1,80}»')
# Ambiguas con nombres propios o siglas (Che Guevara, SOS): solo cuenta la minúscula tal cual.
SOLO_MINUSCULA = {"che", "sos", "vos"}


def _patron(formas, flags=0):
    return re.compile(
        r"(?<![\wáéíóúñ])(" + "|".join(re.escape(f) for f in formas) + r")(?![\wáéíóúñ])", flags
    )


_PATRON = _patron([f for f in FORMAS_VOSEO if f not in SOLO_MINUSCULA], re.IGNORECASE)
_PATRON_MINUSCULA = _patron(sorted(SOLO_MINUSCULA))


def limpiar(texto: str) -> str:
    """Quita bloques de código, código en línea, palabras entre comillas y líneas de cita."""
    texto = _CODIGO.sub(" ", texto)
    texto = _CITADO.sub(" ", texto)
    lineas = [l for l in texto.splitlines() if not l.lstrip().startswith(">")]
    return "\n".join(lineas)


def buscar_voseo(texto: str) -> list[str]:
    """Devuelve las formas de voseo halladas (en minúscula, sin repetir, en orden)."""
    limpio = limpiar(texto)
    hallazgos = sorted(
        [(m.start(), m.group(1).lower()) for m in _PATRON.finditer(limpio)]
        + [(m.start(), m.group(1)) for m in _PATRON_MINUSCULA.finditer(limpio)]
    )
    visto = []
    for _, forma in hallazgos:
        if forma not in visto:
            visto.append(forma)
    return visto


def ultimo_mensaje(ruta: str) -> str:
    """Texto del último mensaje del asistente en el transcript JSONL."""
    ultimo = ""
    with open(ruta, "r", encoding="utf-8") as f:
        for linea in f:
            try:
                e = json.loads(linea)
            except ValueError:
                continue
            msg = e.get("message") if isinstance(e, dict) else None
            if e.get("type") != "assistant" or not isinstance(msg, dict):
                continue
            partes = msg.get("content")
            if isinstance(partes, str):
                texto = partes
            else:
                texto = "\n".join(
                    p.get("text", "") for p in (partes or [])
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            if texto.strip():
                ultimo = texto
    return ultimo


def main() -> int:
    try:
        entrada = json.load(sys.stdin)
        if entrada.get("stop_hook_active"):
            return 0
        hallado = buscar_voseo(ultimo_mensaje(entrada["transcript_path"]))
    except Exception:
        return 0
    if not hallado:
        return 0
    print(json.dumps({
        "decision": "block",
        "reason": (
            "Candado de español neutro: tu último mensaje trae voseo o jerga rioplatense ("
            + ", ".join(hallado)
            + "). Reescríbelo en español neutro: tuteo estándar (tienes, puedes, verifica, "
            "avísame), sin voseo."
        ),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

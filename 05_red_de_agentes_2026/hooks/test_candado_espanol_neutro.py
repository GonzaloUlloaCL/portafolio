#!/usr/bin/env python3
"""Pruebas del candado. Positivos: deben detectar. Negativos: no deben."""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from candado_espanol_neutro import buscar_voseo  # noqa: E402

POSITIVOS = [
    ("Tenés que revisar el archivo", ["tenés"]),
    ("Avisame cuando esté listo", ["avisame"]),
    ("Fijate que el servicio no arrancó", ["fijate"]),
    ("Eso es un quilombo, che", ["quilombo", "che"]),
    ("Si querés lo hago ahora", ["querés"]),
    ("Mirá el resultado y usá la ruta nueva", ["mirá", "usá"]),
]

NEGATIVOS = [
    ("Tendrás el resultado mañana; además, está listo y vuelve acá", []),
    ("Usa `tenés` o mira ```\nvos podés\n``` en el código", []),
    ("> Gonza escribió: tenés que avisame\nEso fue la cita.", []),
    ("Tienes que verificar, avísame cuando puedas. Che Guevara, no. SOS no es voseo.", []),
    ("Pasé por la oficina; el vosotros no se usa. Cheque, checo, anchoa.", []),
    ("Ayer escribí el informe y lo envié", []),
    ("No abrí el hilo; reabrí el editor", []),
    ('El candado conserva "decime" y "avisame" como ejemplos', []),
]

fallas = 0
for texto, esperado in POSITIVOS + NEGATIVOS:
    obtenido = buscar_voseo(texto)
    ok = (obtenido != []) if esperado else (obtenido == [])
    if esperado and ok:
        ok = all(e in obtenido for e in esperado)
    print(("OK   " if ok else "FALLA"), repr(texto[:50]), "->", obtenido)
    fallas += 0 if ok else 1

# Extremo a extremo: transcript falso -> el script debe emitir "block" con voseo y callar sin él.
def correr(texto, activo=False):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as t:
        t.write(json.dumps({"type": "user", "message": {"content": "hola"}}) + "\n")
        t.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": texto}]}}) + "\n")
    entrada = json.dumps({"transcript_path": t.name, "stop_hook_active": activo})
    r = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "candado_espanol_neutro.py")],
        input=entrada, capture_output=True, text=True,
    )
    os.unlink(t.name)
    return r.stdout.strip()

for etiqueta, salida, debe_bloquear in [
    ("e2e con voseo", correr("Mirá, tenés que avisame"), True),
    ("e2e neutro", correr("Mira, tienes que avisarme"), False),
    ("e2e voseo con stop_hook_active (no bucle)", correr("Mirá esto", activo=True), False),
]:
    ok = ('"decision": "block"' in salida) == debe_bloquear
    print(("OK   " if ok else "FALLA"), etiqueta)
    fallas += 0 if ok else 1

print(f"\n{'TODO OK' if not fallas else str(fallas) + ' FALLA(S)'}")
sys.exit(1 if fallas else 0)

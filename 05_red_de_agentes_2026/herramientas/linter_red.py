#!/usr/bin/env python3
"""
Linter de la red MERKABA.

Valida las 6 reglas del bloque "El linter (bloquea el cierre)" en ONTOLOGIA.md.
Es la precondición 0 de PROTOCOLO.md: si falla, no hay cierre.

Uso:
    python linter_red.py

Salida:
    ERRORES   -> bloquean el cierre (exit code 1)
    MUERTOS   -> sistema sin artefacto vivo, candidatos a poda (no bloquea)
    otras advertencias informativas (no bloquean)

Decisiones de diseño que Gonza debe confirmar o corregir (ver mensaje al final
de la sesión): el nombre del campo frontmatter para la regla 5 se asumió como
`ruta`, y la regla 6 se audita mostrando el estado real pero sin bloquear el
cierre por archivos ya existentes antes de que la regla existiera.
"""

import re
import sys
from pathlib import Path

import yaml

EXCLUDE_DIRS = {".git", "_fuentes", ".claude", "node_modules", ".venv", "venv"}
VERBOS_VALIDOS = {"SIRVE_A", "DEPENDE_DE", "USA", "GENERÓ", "BLOQUEA"}
TIPOS_VALIDOS = {"proyecto", "sistema", "criterio", "evidencia", "persona"}
# ids que son salidas generadas por script, nunca destino válido de un borde.
ARTEFACTOS_GENERADOS = {"grafo", "mapa"}
FECHA_EN_NOMBRE = re.compile(r"\d{4}[-_]\d{2}(?:[-_]\d{2})?")


def encontrar_raiz(desde: Path) -> Path:
    """Sube desde la ubicación del script hasta encontrar la raíz del repo (donde vive NORTE.md)."""
    actual = desde.resolve()
    while actual != actual.parent:
        if (actual / "NORTE.md").exists():
            return actual
        actual = actual.parent
    raise SystemExit("No se encontró NORTE.md subiendo desde el script — ¿se movió el linter fuera del repo?")


def parsear_frontmatter(texto: str):
    """Devuelve (frontmatter_dict, error) para un archivo. frontmatter_dict es None si no hay bloque ---."""
    if not texto.startswith("---"):
        return None, None
    partes = texto.split("---", 2)
    if len(partes) < 3:
        return None, None
    try:
        fm = yaml.safe_load(partes[1]) or {}
    except yaml.YAMLError as e:
        return None, str(e)
    if not isinstance(fm, dict):
        return None, "el bloque frontmatter no es un mapeo YAML válido"
    return fm, None


def escanear(raiz: Path):
    nodos = {}  # id -> {"path": ruta relativa, "fm": dict}
    sin_frontmatter = []
    frontmatter_malo = []
    con_fecha_en_nombre = []

    for path in sorted(raiz.rglob("*.md")):
        rel = path.relative_to(raiz)
        if any(parte in EXCLUDE_DIRS for parte in rel.parts):
            continue

        if FECHA_EN_NOMBRE.search(path.stem):
            con_fecha_en_nombre.append(str(rel))

        texto = path.read_text(encoding="utf-8")
        fm, error = parsear_frontmatter(texto)

        if error:
            frontmatter_malo.append((str(rel), error))
            continue
        if fm is None:
            sin_frontmatter.append(str(rel))
            continue

        node_id = fm.get("id")
        if not node_id:
            frontmatter_malo.append((str(rel), "frontmatter sin campo 'id'"))
            continue
        if node_id in nodos:
            frontmatter_malo.append((str(rel), f"id duplicado '{node_id}' (ya usado por {nodos[node_id]['path']})"))
            continue

        nodos[node_id] = {"path": str(rel), "fm": fm}

    return nodos, sin_frontmatter, frontmatter_malo, con_fecha_en_nombre


def validar(nodos: dict, raiz: Path):
    errores = []
    muertos = []
    sistema_sin_ruta = []
    tipo_invalido = []

    for node_id, info in nodos.items():
        fm, path = info["fm"], info["path"]
        tipo = fm.get("tipo")

        if tipo not in TIPOS_VALIDOS:
            tipo_invalido.append(f"{path}: tipo={tipo!r} no es uno de los 5 canónicos")

        # Regla 4 — estado/prioridad fuera de proyecto.
        if tipo != "proyecto":
            if "estado" in fm:
                errores.append(f"{path}: campo 'estado' en tipo={tipo!r} (regla 4)")
            if "prioridad" in fm:
                errores.append(f"{path}: campo 'prioridad' en tipo={tipo!r} (regla 4)")

        # Regla 5 — sistema declara la ruta de su artefacto real.
        # Asunción de nombre de campo: 'ruta'. Puede ser una ruta local (relativa a la
        # raíz del repo) o una URL http(s) para un sistema que vive en un repo externo
        # (decisión de arquitectura, sesión 6, 31-ago-2026: proyectos grandes con vida
        # propia -> repo aparte). Una URL no se verifica localmente, solo se registra.
        if tipo == "sistema":
            ruta = fm.get("ruta")
            if not ruta:
                sistema_sin_ruta.append(path)
            elif str(ruta).startswith(("http://", "https://")):
                pass  # ruta externa: no verificable desde este filesystem
            elif not (raiz / ruta).exists():
                muertos.append(f"{path}: declara ruta '{ruta}' y no existe (regla 5)")

        # Reglas 1 y 2 — bordes.
        for borde in fm.get("bordes") or []:
            if not isinstance(borde, dict):
                errores.append(f"{path}: borde mal formado {borde!r} (regla 1)")
                continue
            verbo = borde.get("tipo")
            destino = borde.get("destino")
            if not verbo or verbo not in VERBOS_VALIDOS:
                errores.append(f"{path}: borde hacia {destino!r} sin verbo válido (tipo={verbo!r}) (regla 1)")
            if not destino:
                errores.append(f"{path}: borde sin 'destino' declarado (regla 2)")
            elif destino in ARTEFACTOS_GENERADOS:
                errores.append(f"{path}: borde apunta a artefacto generado '{destino}' (regla 2)")
            elif destino not in nodos:
                errores.append(f"{path}: borde apunta a id inexistente '{destino}' (regla 2)")

    return errores, muertos, sistema_sin_ruta, tipo_invalido


def main():
    raiz = encontrar_raiz(Path(__file__).parent)
    nodos, sin_fm, fm_malo, con_fecha = escanear(raiz)
    errores, muertos, sistema_sin_ruta, tipo_invalido = validar(nodos, raiz)

    for path, err in fm_malo:
        errores.append(f"{path}: frontmatter inválido — {err} (regla 3)")

    print(f"Nodos válidos: {len(nodos)}")
    print(f"Archivos sin frontmatter, excluidos correctamente (regla 3): {len(sin_fm)}")
    print()

    if errores:
        print(f"ERRORES ({len(errores)}) — bloquean el cierre:")
        for e in errores:
            print(f"  - {e}")
    else:
        print("Sin errores duros. El cierre puede continuar.")

    if tipo_invalido:
        print(f"\nTIPO NO CANÓNICO ({len(tipo_invalido)}):")
        for t in tipo_invalido:
            print(f"  - {t}")

    if muertos:
        print(f"\nMUERTOS ({len(muertos)}) — sistema sin artefacto vivo, candidatos a poda (regla 5, no bloquea):")
        for m in muertos:
            print(f"  - {m}")

    if sistema_sin_ruta:
        print(f"\nSISTEMA SIN CAMPO 'ruta' ({len(sistema_sin_ruta)}) — esquema nuevo aún no aplicado, no bloquea:")
        for p in sistema_sin_ruta[:10]:
            print(f"  - {p}")
        if len(sistema_sin_ruta) > 10:
            print(f"  ... y {len(sistema_sin_ruta) - 10} más")

    if con_fecha:
        print(f"\nFECHA EN EL NOMBRE ({len(con_fecha)}) — regla 6, auditado pero no bloquea archivos preexistentes:")
        for f in con_fecha[:10]:
            print(f"  - {f}")
        if len(con_fecha) > 10:
            print(f"  ... y {len(con_fecha) - 10} más")

    sys.exit(1 if errores else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
delivery_zonas.py — Tarifa de delivery por calle (3-ago-2026, planilla de Christian).
=======================================================================================
Verdad: `data/tarifas_delivery.json` (generado por `importar_tarifas_delivery.py` desde
la planilla que entregó Christian). Este módulo solo hace el MATCH en tiempo real:
dada una dirección de texto libre, busca si calza con alguna calle de la tabla.

Por qué el número de casa es OBLIGATORIO (pedido explícito de Gonza, 3-ago-2026): la
planilla no tiene comuna. Hay calles que existen en media Santiago (Recoleta, Pedro de
Valdivia, Lastarria, Prat, Santa Clara...) y la única defensa contra despachar -- o
cobrar mal -- a la comuna equivocada es que el número de casa caiga DENTRO del rango
que Christian definió para SU Recoleta, SU Prat, etc. Si el nombre calza pero el
número no cae en ningún tramo de esa calle, NO hay match: el pedido cae al flujo de
siempre (POS/jefe), nunca se inventa un cobro.

Dónde vive esto en la cadena de precedencia (decidida por Gonza, 3-ago-2026):
    1º esta tabla (planilla)  ->  2º direcciones guardadas en el POS  ->  3º el jefe.
Enganchada en `_continuar_despacho` (webhook.py), ANTES de `_buscar_cobro_direccion`.

Mantenimiento: hoy "Gonza edita y despliega" -- Christian no tiene una pantalla para
tocar esta tabla (eso implicaría agregarla al POS y recompilar el .exe, no se hizo
todavía). Si la planilla cambia: reemplazar el .xlsx en `orientacion final/`, correr
`python bot/importar_tarifas_delivery.py`, y desplegar el JSON nuevo con el resto del
bot -- no hace falta recompilar nada de esto.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

# Mismo criterio que MENU_JSON en prototipo.py: empaquetado como .exe, los datos
# viven junto al ejecutable; en desarrollo, en data/ del proyecto.
if getattr(sys, "frozen", False):
    TARIFAS_JSON = Path(sys.executable).resolve().parent / "tarifas_delivery.json"
else:
    TARIFAS_JSON = Path(__file__).resolve().parent.parent / "data" / "tarifas_delivery.json"

# Palabras que no ayudan a identificar la calle (y que sobrarían para exigir "match
# exacto de token"): articulos/preposiciones comunes en los nombres de la planilla.
_STOPWORDS = {"los", "las", "el", "la", "de", "del", "san", "santa", "calle",
              "pasaje", "avenida", "av"}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.strip().lower())


def _tokens_distintivos(calle_norm):
    """Las palabras de la calle que SI hay que ver en la direccion del cliente (se
    excluyen articulos: 'los'/'las'/'el' aparecen en decenas de calles de la tabla y
    por si solos no identifican nada)."""
    return [t for t in re.findall(r"[a-z]+", calle_norm) if t not in _STOPWORDS and len(t) >= 3]


_TABLA_CACHE = None


def cargar_tabla(ruta=None, forzar_recarga=False):
    """Carga (y cachea) los tramos de `tarifas_delivery.json`, precomputando los
    tokens distintivos de cada calle. `forzar_recarga` es para tests."""
    global _TABLA_CACHE
    if _TABLA_CACHE is not None and not forzar_recarga and ruta is None:
        return _TABLA_CACHE
    ruta = Path(ruta) if ruta else TARIFAS_JSON
    if not ruta.exists():
        resultado = []
    else:
        with open(ruta, encoding="utf-8") as f:
            data = json.load(f)
        resultado = []
        for t in data.get("tramos", []):
            calle_norm = _norm(t["calle"])
            resultado.append({**t, "_calle_norm": calle_norm,
                               "_tokens": _tokens_distintivos(calle_norm)})
    if ruta == TARIFAS_JSON:
        _TABLA_CACHE = resultado
    return resultado


def cobro_por_calle(direccion, tabla=None):
    """Busca la tarifa de delivery para `direccion` contra la tabla de calles.
    Exige: (a) los tokens distintivos del nombre de la calle presentes como palabra
    completa en la direccion, y (b) un numero de casa que caiga DENTRO del rango de
    ese tramo. Devuelve el precio (int) o None si no hay match seguro:
      - ningun numero en la direccion,
      - ninguna calle calza,
      - o dos tramos calzan con PRECIOS DISTINTOS (ambiguo -- mejor no cobrar que
        cobrar mal; cae al flujo de siempre).
    Con precios iguales entre tramos que calzan (ej. limites que se tocan) no es
    ambiguo de verdad: se devuelve ese precio."""
    if tabla is None:
        tabla = cargar_tabla()
    texto = _norm(direccion)
    numeros = [int(n) for n in re.findall(r"\d+", texto)]
    if not numeros or not tabla:
        return None
    precios = set()
    for tramo in tabla:
        tokens = tramo["_tokens"]
        if not tokens or not all(re.search(rf"\b{re.escape(t)}\b", texto) for t in tokens):
            continue
        for n in numeros:
            if tramo["desde"] <= n <= tramo["hasta"]:
                precios.add(tramo["precio"])
                break
    if len(precios) == 1:
        return precios.pop()
    return None


if __name__ == "__main__":
    tabla = cargar_tabla()

    def chk(desc, got, exp):
        ok = got == exp
        print(f"  [{'OK' if ok else 'FALLA'}] {desc}: got={got} exp={exp}")
        return ok

    print(f"== tabla cargada: {len(tabla)} tramos ==")
    todos = [chk("la tabla no esta vacia", len(tabla) > 0, True)]

    print("== match simple (nombre + numero en rango) ==")
    todos.append(chk("Los Cerezos 500 (bajo el corte) -> 2000",
                     cobro_por_calle("Los Cerezos 500, Huechuraba", tabla), 2000))
    todos.append(chk("Los Cerezos 750 (sobre el corte) -> 1500",
                     cobro_por_calle("Los Cerezos 750, Huechuraba", tabla), 1500))
    todos.append(chk("Recoleta 4450 (tramo bajo, precio plano) -> 2000",
                     cobro_por_calle("vivo en Recoleta 4450", tabla), 2000))
    todos.append(chk("Recoleta 5300 (tramo alto, bajo el corte 6000) -> 1500",
                     cobro_por_calle("vivo en Recoleta 5300", tabla), 1500))
    todos.append(chk("Recoleta 6100 (tramo alto, sobre el corte 6000) -> 2000",
                     cobro_por_calle("vivo en Recoleta 6100", tabla), 2000))

    print("== la comuna equivocada NO calza (numero fuera de TODOS los rangos) ==")
    todos.append(chk("Recoleta 200 (la comuna Recoleta, no la calle de Huechuraba) -> None",
                     cobro_por_calle("Recoleta 200", tabla), None))
    todos.append(chk("Pedro de Valdivia 1200 (Providencia, no Huechuraba) -> None",
                     cobro_por_calle("Pedro de Valdivia 1200", tabla), None))

    print("== sin numero, o calle inexistente ==")
    todos.append(chk("sin numero -> None", cobro_por_calle("Los Cerezos, Huechuraba"), None))
    todos.append(chk("calle que no esta en la tabla -> None",
                     cobro_por_calle("Avenida Inventada 500"), None))

    print("== texto conversacional real (no solo 'calle numero') ==")
    todos.append(chk("frase larga con la calle en el medio -> 1500",
                     cobro_por_calle("hola, vivo en Lastarria como en el 700, cerca de la plaza",
                                      tabla), 1500))

    print("== limite exacto de un tramo condicional (no debe quedar afuera) ==")
    todos.append(chk("Los Cerezos 600 (el corte mismo, ya es el precio alto) -> 1500",
                     cobro_por_calle("Los Cerezos 600", tabla), 1500))
    todos.append(chk("Los Cerezos 599 (justo antes del corte) -> 2000",
                     cobro_por_calle("Los Cerezos 599", tabla), 2000))

    print("== calle homonima con dos tramos distintos, sin choque (Recoleta ya probado arriba) ==")
    todos.append(chk("Salvador Allende 400 (tramo 300-600) -> 2000",
                     cobro_por_calle("Salvador Allende 400", tabla), 2000))
    todos.append(chk("Salvador Allende 5650 (el otro tramo, 5600-5700) -> 2000",
                     cobro_por_calle("Salvador Allende 5650", tabla), 2000))

    print("== 'Nave' (rango no numerico) nunca matchea -- se pregunta como siempre ==")
    todos.append(chk("Las Petunias sin ningun numero en la direccion -> None",
                     cobro_por_calle("Las Petunias, nave", tabla), None))
    todos.append(chk("Las Petunias con numero fuera de TODOS sus tramos -> None",
                     cobro_por_calle("Las Petunias 99999", tabla), None))

    print("== ambiguo de verdad (dos tramos calzan con PRECIOS DISTINTOS) -> None, fail-safe ==")
    tabla_ambigua = [
        {"calle": "Calle Prueba", "desde": 100, "hasta": 900, "precio": 1500,
         "_calle_norm": "calle prueba", "_tokens": ["calle", "prueba"]},
        {"calle": "Calle Prueba", "desde": 500, "hasta": 500, "precio": 2000,
         "_calle_norm": "calle prueba", "_tokens": ["calle", "prueba"]},
    ]
    todos.append(chk("numero 500 calza dos tramos con precio distinto -> None (no adivina)",
                     cobro_por_calle("Calle Prueba 500", tabla_ambigua), None))
    todos.append(chk("numero 300 solo calza un tramo -> 1500 (no ambiguo)",
                     cobro_por_calle("Calle Prueba 300", tabla_ambigua), 1500))

    print("\n" + ("TODO OK" if all(todos) else ">>> HAY FALLAS <<<"))

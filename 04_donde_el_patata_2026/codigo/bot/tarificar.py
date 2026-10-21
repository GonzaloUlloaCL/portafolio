#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tarificar.py — Motor de precios de "Donde el Patata" (verdad: data/menu.json).
==============================================================================
Cierra el agujero del prototipo. ANTES: la IA ponia el precio de cada item y el
codigo solo verificaba que los subtotales *sumaran* entre si (si la IA inventaba
un precio, la comanda igual daba "OK"). AHORA: el precio lo calcula Python contra
menu.json. La IA solo dice QUE pidio el cliente (categoria + producto + variante +
cantidad); el subtotal y el total autoritativos los arma este modulo, incluida la
promo 2x.

Sin dependencias externas (solo libreria estandar). Lo usa bot/prototipo.py hoy y
la API real manana.
"""

import json
import re
import unicodedata
from pathlib import Path


# --- normalizacion de texto: casar nombres sin pelear con tildes/mayusculas ---
def _norm(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.strip().lower())


def fmt_pesos(n):
    """4900 -> $4.900"""
    try:
        return "$" + f"{int(round(float(n))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(n)


# alias que la IA podria escribir distinto -> clave canonica del menu.json
_ALIAS_VARIANTE = {
    "churrasco": "churrasco", "mechada": "mechada", "mecha": "mechada",
    "xl": "xl_30cm", "xl 30cm": "xl_30cm", "xl_30": "xl_30cm", "30cm": "xl_30cm",
    "xl_30cm": "xl_30cm", "grande 30cm": "xl_30cm",
    "normal": "normal_22cm", "normal 22cm": "normal_22cm", "22cm": "normal_22cm",
    "normal_22cm": "normal_22cm", "chica": "normal_22cm",
    "individual": "individual", "ind": "individual",
    "mediana": "mediana", "media": "mediana",
    "grande": "grande",
    "6": "6", "6u": "6", "10": "10", "10u": "10",
}


def _canon_variante(v):
    n = _norm(v)
    return _ALIAS_VARIANTE.get(n, n)


# label legible de cada variante canonica (para la UI y la comanda; la clave cruda
# 'xl_30cm' no la debe ver el cliente ni la cocina)
LABEL_VARIANTE = {
    "churrasco": "Churrasco", "mechada": "Mechada",
    "xl_30cm": "XL 30cm", "normal_22cm": "Normal 22cm",
    "individual": "Individual", "mediana": "Mediana", "grande": "Grande",
    "6": "6u (chica)", "10": "10u (grande)",
}


def label_variante(v):
    """Nombre legible de una variante canonica (xl_30cm -> 'XL 30cm'). '' si no hay."""
    return LABEL_VARIANTE.get(v, v) if v else ""


def _variantes_validas(p):
    """Variantes que admite un producto (vacia si no usa variantes)."""
    if "precio_churrasco" in p or "precio_mechada" in p:
        return [v for v in ("churrasco", "mechada") if f"precio_{v}" in p]
    if "xl_30cm" in p or "normal_22cm" in p:
        return [v for v in ("xl_30cm", "normal_22cm") if v in p]
    if any(t in p for t in ("individual", "mediana", "grande")):
        return [t for t in ("individual", "mediana", "grande") if p.get(t) is not None]
    if "precio_6" in p or "precio_10" in p:
        return [v for v in ("6", "10") if f"precio_{v}" in p]
    return []


def _precio_variante(p, var):
    """(precio_unitario, precio_2x|None). var ya canonica, o None si no usa variante."""
    if var in ("churrasco", "mechada"):
        return p[f"precio_{var}"], None
    if var in ("xl_30cm", "normal_22cm"):
        return p[var]["precio"], p[var].get("precio_2x")
    if var in ("individual", "mediana", "grande"):
        return p[var], None
    if var in ("6", "10"):
        return p[f"precio_{var}"], None
    return p.get("precio"), p.get("precio_2x")


def montos_validos_del_menu(menu):
    """Todos los montos en pesos que existen DE VERDAD en el menu -- precio, precio_2x,
    variantes XL/normal, tamanos de papas, porciones 6/10, churrasco/mechada de
    Especiales, y la tarifa minima de delivery. Base del candado de montos (04-ago-2026,
    bot/prototipo.py: sanear_montos_no_autorizados): un monto que el modelo diga y no
    este en este set no puede venir del menu real, sea cual sea la excusa."""
    montos = set()

    def _agregar(v):
        if isinstance(v, (int, float)) and v:
            montos.add(int(v))

    for cat in menu.get("categorias", []):
        for p in cat.get("productos", []):
            _agregar(p.get("precio"))
            _agregar(p.get("precio_2x"))
            _agregar(p.get("precio_churrasco"))
            _agregar(p.get("precio_mechada"))
            _agregar(p.get("individual"))
            _agregar(p.get("mediana"))
            _agregar(p.get("grande"))
            _agregar(p.get("precio_6"))
            _agregar(p.get("precio_10"))
            for variante in ("xl_30cm", "normal_22cm"):
                var = p.get(variante)
                if isinstance(var, dict):
                    _agregar(var.get("precio"))
                    _agregar(var.get("precio_2x"))
    _agregar((menu.get("local") or {}).get("delivery", {}).get("tarifa_minima"))
    return montos


class Tarificador:
    """Pone precio a un pedido leyendo el menu. La IA dice que; esto dice cuanto."""

    def __init__(self, menu):
        self.menu = menu
        self._por_cat_prod = {}   # (cat_norm, prod_norm) -> (cat_real, producto)
        self._por_prod = {}       # prod_norm -> [(cat_real, producto), ...]
        for cat in menu.get("categorias", []):
            cn = _norm(cat["nombre"])
            for p in cat["productos"]:
                pn = _norm(p["nombre"])
                self._por_cat_prod[(cn, pn)] = (cat["nombre"], p)
                self._por_prod.setdefault(pn, []).append((cat["nombre"], p))
        # Validar COMBOS: cada componente debe existir en el menu. Se hace al construir
        # para que un menu.json roto se detecte ANTES de guardarlo (ver api_menu_guardar).
        for cat in menu.get("categorias", []):
            for p in cat["productos"]:
                for comp in p.get("combo", []):
                    _, _, err = self._resolver(comp.get("categoria"), comp.get("producto"))
                    if err:
                        raise ValueError(
                            f"combo '{p['nombre']}': componente '{comp.get('producto')}' invalido: {err}")

    @classmethod
    def desde_json(cls, ruta):
        return cls(json.loads(Path(ruta).read_text(encoding="utf-8")))

    # -- ubicar el producto del menu a partir de lo que dijo la IA --
    def _resolver(self, categoria, producto):
        pn, cn = _norm(producto), _norm(categoria)
        if (cn, pn) in self._por_cat_prod:
            cat_real, p = self._por_cat_prod[(cn, pn)]
            return cat_real, p, None
        # categoria ausente o equivocada: intentar por nombre de producto
        candidatos = self._por_prod.get(pn, [])
        if len(candidatos) == 1:
            cat_real, p = candidatos[0]
            return cat_real, p, None
        if len(candidatos) > 1:
            cats = ", ".join(c for c, _ in candidatos)
            return None, None, f"'{producto}' esta en varias categorias ({cats}); falta categoria"
        return None, None, f"'{producto}' no esta en el menu"

    def _alias_especial(self, producto):
        """'Churrasco Brasileño' / 'Mechada Brasileña' -> ('Especial Brasileno', base).

        25-sep-2026 (caso 69 del curador): el cliente pidio "dos churrascos brasileños";
        en el menu eso es el ESPECIAL Brasileño con base churrasco. El modelo lo escribio
        "Churrasco Brasileno", el motor no lo encontro, la ficha no se armo y el cliente
        quedo 12 minutos esperando. Solo actua cuando el nombre NO existe tal cual (lo
        que ya funcionaba no cambia) y solo si hay UN especial con ese apellido que
        admita esa base. Devuelve ((cat_real, producto), base) o None."""
        pn = _norm(producto)
        for base in ("churrasco", "mechada"):
            for prefijo in (base + "s ", base + " "):
                if pn.startswith(prefijo):
                    resto = pn[len(prefijo):].strip()
                    # "brasilena(s)" / "brasilenos" -> "brasileno": el menu nombra al
                    # especial en masculino y singular
                    singular = re.sub(r"s\b", "", resto)
                    variantes_nombre = {resto, singular, re.sub(r"a\b", "o", singular)}
                    for r in variantes_nombre:
                        cands = self._por_prod.get(f"especial {r}", [])
                        if len(cands) == 1 and base in _variantes_validas(cands[0][1]):
                            return cands[0], base
        return None

    def _resolver_variante(self, p, variante):
        validas = _variantes_validas(p)
        if not validas:
            return None, None  # no usa variante: ok
        if variante in (None, "", "null"):
            return None, f"falta variante (opciones: {', '.join(validas)})"
        v = _canon_variante(variante)
        if v not in validas:
            return None, f"variante '{variante}' invalida (opciones: {', '.join(validas)})"
        return v, None

    def precio_item(self, categoria, producto, variante=None, cantidad=1):
        """Subtotal autoritativo de un item. Devuelve dict con detalle o problema."""
        try:
            cantidad = max(1, int(cantidad))
        except (TypeError, ValueError):
            cantidad = 1
        base = {"cantidad": cantidad, "producto": producto,
                "categoria": categoria, "variante": variante, "subtotal": 0}
        cat_real, p, err = self._resolver(categoria, producto)
        if err:
            alias = self._alias_especial(producto)
            if alias:
                (cat_real, p), base_alias = alias
                err = None
                if variante in (None, "", "null"):
                    variante = base_alias
        if err:
            return {**base, "ok": False, "problema": err}
        var, verr = self._resolver_variante(p, variante)
        if verr:
            return {**base, "ok": False, "problema": verr,
                    "producto": p["nombre"], "categoria": cat_real}
        unit, precio_2x = _precio_variante(p, var)
        if unit is None:
            return {**base, "ok": False, "problema": "sin precio en el menu",
                    "producto": p["nombre"], "categoria": cat_real, "variante": var}
        # promo 2x en productos iguales
        if precio_2x and cantidad >= 2:
            pares, sueltos = divmod(cantidad, 2)
            subtotal = pares * precio_2x + sueltos * unit
        else:
            pares, subtotal = 0, cantidad * unit
        resultado = {"ok": True, "problema": None, "categoria": cat_real, "producto": p["nombre"],
                    "variante": var, "cantidad": cantidad, "precio_unitario": unit,
                    "precio_2x": precio_2x, "pares_promo": pares, "subtotal": subtotal,
                    "grupo": p.get("grupo")}
        if p.get("combo"):
            # COMBO: cobra su precio FIJO (ya calculado arriba como un producto mas, sin
            # sumar componentes); solo se exponen los nombres para que la comanda los liste.
            resultado["componentes"] = [c.get("producto") for c in p["combo"]]
        return resultado

    @staticmethod
    def _aplicar_promo_grupo(items_out):
        """Promo 2x a nivel de GRUPO: productos distintos del mismo 'grupo' (ej. el Italiano
        y sus armados, o Completo+Dinamico) forman pares entre si. Regla de cobro del
        local (Christian, 2026-07-02): un par MIXTO cobra el precio_2x del producto MAS
        CARO del par (Churrasco Completo+Dinamico = 9200, no 9050 ni 8900). Para no
        encarecer de mas, primero se agotan los pares del MISMO producto (a su propio
        precio_2x) y solo las unidades que quedan sueltas se cruzan entre productos.
        La unidad suelta final (si el total del grupo es impar) cobra su precio normal."""
        grupos = {}
        for i, r in enumerate(items_out):
            if r.get("ok") and r.get("grupo") and r.get("precio_2x"):
                # clave (grupo, variante): en As/Vienesas 2 XL hacen par y 2 Normal hacen par,
                # pero XL+Normal (precios distintos) no se cruzan
                grupos.setdefault((r["grupo"], r.get("variante")), []).append(i)
        for idxs in grupos.values():
            if sum(items_out[i]["cantidad"] for i in idxs) < 2:
                continue   # menos de 2 unidades en el grupo: cada una a precio normal
            # 1) pares del MISMO producto: cada linea cobra su propio precio_2x
            sueltas = []   # lineas que aportan 1 unidad sin par propio
            for i in idxs:
                pares, resto = divmod(items_out[i]["cantidad"], 2)
                items_out[i]["subtotal"] = pares * items_out[i]["precio_2x"]
                items_out[i]["pares_promo"] = 1 if pares else 0
                if resto:
                    sueltas.append(i)
            # 2) pares CRUZADOS entre las unidades sueltas (de mas cara a mas barata):
            #    el par cobra el precio_2x del MAS CARO, repartido entre ambas lineas
            sueltas.sort(key=lambda i: (-items_out[i]["precio_unitario"], -items_out[i]["precio_2x"]))
            while len(sueltas) >= 2:
                a, b = sueltas.pop(0), sueltas.pop(0)
                p2x = max(items_out[a]["precio_2x"], items_out[b]["precio_2x"])
                items_out[a]["subtotal"] += p2x - p2x // 2   # sin perder $1 en impares
                items_out[b]["subtotal"] += p2x // 2
                items_out[a]["pares_promo"] = 1
                items_out[b]["pares_promo"] = 1
            # 3) la unidad suelta final cobra su precio normal completo
            if sueltas:
                items_out[sueltas[0]]["subtotal"] += items_out[sueltas[0]]["precio_unitario"]

    @staticmethod
    def _aji_del_chacarero(items_out):
        """El aji verde APARTE no se cobra si el pedido lleva Chacarero (Gonza, 12-ago-2026).

        Regla del local: el aji verde es parte del Chacarero, asi que pedirlo en un pote
        aparte no puede costar plata extra. Sin Chacarero en el pedido, el mismo pote es un
        producto mas y se cobra con el precio del menu, como cualquier salsa.

        Es candado de CODIGO y no solo de prompt porque hay plata de por medio (Arquitectura
        §4): si el modelo lo manda cobrado, aca se pone en 0, y si lo manda gratis sin
        Chacarero, el precio del menu lo cobra igual (eso ya lo hace precio_item).

        DECISION al implementar, pendiente de confirmar con Gonza: el regalo cubre **hasta la
        cantidad de Chacareros** del pedido. 1 Chacarero + 3 potes = 1 gratis y 2 cobrados.
        Si la regla del local fuera "gratis sin limite mientras haya un Chacarero", se cambia
        el min() de abajo por la cantidad entera de la linea.

        El aji DENTRO del sanguche no pasa por aca: eso va en notas y nunca fue un item."""
        chacareros = sum(r["cantidad"] for r in items_out
                         if r.get("ok") and "chacarero" in _norm(r.get("producto")))
        if not chacareros:
            return
        for r in items_out:
            # por palabra y no por substring: "aji verde" y "salsa de aji" si, "ajiaco" no
            if not r.get("ok") or "aji" not in _norm(r.get("producto")).split():
                continue
            gratis = min(r["cantidad"], chacareros)
            if not gratis:
                continue
            chacareros -= gratis
            r["subtotal"] = max(0, r["subtotal"] - gratis * (r.get("precio_unitario") or 0))
            r["aji_de_regalo"] = gratis

    def tarificar_pedido(self, pedido):
        """Recibe el dict del <PEDIDO_JSON> y devuelve precios autoritativos +
        comparacion contra lo que dijo la IA."""
        # consolidar items identicos (misma cat/producto/variante) para que la promo
        # 2x aplique aunque la IA los haya separado en lineas distintas
        agrupados, orden = {}, []
        for it in (pedido.get("items") or []):
            clave = (_norm(it.get("categoria")), _norm(it.get("producto")),
                     _canon_variante(it.get("variante")))
            if clave not in agrupados:
                agrupados[clave] = {"categoria": it.get("categoria"),
                                    "producto": it.get("producto"),
                                    "variante": it.get("variante"),
                                    "cantidad": 0, "notas": [], "subtotal_ia": 0}
                orden.append(clave)
            g = agrupados[clave]
            try:
                g["cantidad"] += int(it.get("cantidad", 1))
            except (TypeError, ValueError):
                g["cantidad"] += 1
            if it.get("notas"):
                g["notas"].append(str(it["notas"]))
            try:
                g["subtotal_ia"] += int(it.get("subtotal", 0) or 0)
            except (TypeError, ValueError):
                pass

        items_out, problemas = [], []
        for clave in orden:
            g = agrupados[clave]
            r = self.precio_item(g["categoria"], g["producto"], g["variante"], g["cantidad"])
            r["notas"] = "; ".join(g["notas"])
            r["subtotal_ia"] = g["subtotal_ia"]
            if not r["ok"]:
                problemas.append(f"{g.get('producto')}: {r['problema']}")
            items_out.append(r)

        # Promo 2x por GRUPO (armados del Italiano y similares) antes de sumar el total.
        self._aplicar_promo_grupo(items_out)
        # El aji verde aparte, gratis si hay Chacarero. Va DESPUES de la promo: descuenta
        # sobre el subtotal ya calculado, nunca antes (si no, la promo lo volveria a sumar).
        self._aji_del_chacarero(items_out)

        total = 0
        for r in items_out:
            if r["ok"]:
                total += r["subtotal"]
                if r["subtotal_ia"] and r["subtotal_ia"] != r["subtotal"]:
                    r["aviso_ia"] = (f"la IA dijo {fmt_pesos(r['subtotal_ia'])}, "
                                     f"menu dice {fmt_pesos(r['subtotal'])}")

        # delivery: lo pone el local (tabla por zona pendiente); la IA NO lo inventa
        try:
            delivery = int(pedido.get("delivery_cobro", 0) or 0)
        except (TypeError, ValueError):
            delivery = 0
        total += delivery
        try:
            total_ia = int(pedido.get("total", 0) or 0)
        except (TypeError, ValueError):
            total_ia = 0
        return {"items": items_out, "delivery_cobro": delivery, "total": total,
                "total_ia": total_ia, "coincide_ia": (total_ia == total),
                "problemas": problemas}


# ===========================================================================
# Auto-test: corre `python tarificar.py` para verificar el motor sin Gemini.
# ===========================================================================
if __name__ == "__main__":
    AQUI = Path(__file__).resolve().parent
    MENU = AQUI.parent / "data" / "menu.json"
    t = Tarificador.desde_json(MENU)

    def chk(desc, got, exp):
        ok = got == exp
        print(f"  [{'OK' if ok else 'FALLA'}] {desc}: got={got} exp={exp}")
        return ok

    print("== precio_item ==")
    todos = []
    todos.append(chk("1 Italiano churrasco",
                     t.precio_item("Churrascos", "Churrasco Italiano", None, 1)["subtotal"], 4900))
    todos.append(chk("2 Italiano churrasco (promo 2x)",
                     t.precio_item("Churrascos", "Churrasco Italiano", None, 2)["subtotal"], 8900))
    todos.append(chk("3 Italiano churrasco (1 par + 1 suelto)",
                     t.precio_item("Churrascos", "Churrasco Italiano", None, 3)["subtotal"], 13800))
    todos.append(chk("Mechada Dinamico (sin 2x)",
                     t.precio_item("Mechadas", "Mechada Dinamico", None, 2)["subtotal"], 11600))
    todos.append(chk("Especial Kayser churrasco",
                     t.precio_item("Especiales", "Especial Kayser", "churrasco", 1)["subtotal"], 5600))
    todos.append(chk("Especial Kayser mechada",
                     t.precio_item("Especiales", "Especial Kayser", "mechada", 1)["subtotal"], 6200))
    todos.append(chk("2 Vienesa Completo XL (promo 2x)",
                     t.precio_item("Vienesas", "Vienesa Completo", "xl_30cm", 2)["subtotal"], 5700))
    todos.append(chk("As Queso normal 22cm",
                     t.precio_item("As", "As Queso", "normal_22cm", 1)["subtotal"], 3900))
    todos.append(chk("Papas mediana",
                     t.precio_item("Papas Fritas", "Papas Fritas", "mediana", 1)["subtotal"], 2500))
    todos.append(chk("Nuggets x6 (viven en Papas Fritas desde 02-jul)",
                     t.precio_item("Papas Fritas", "Nuggets", "6", 1)["subtotal"], 3000))
    todos.append(chk("Empanaditas x10 (viven en Papas Fritas desde 02-jul)",
                     t.precio_item("Papas Fritas", "Empanaditas", "10", 1)["subtotal"], 4000))
    todos.append(chk("Box 1",
                     t.precio_item("Boxes Pollo Crispy", "Box 1", None, 1)["subtotal"], 6500))

    print("== problemas esperados ==")
    todos.append(chk("Italiano sin categoria -> ambiguo",
                     t.precio_item(None, "Italiano", None, 1)["ok"], False))
    todos.append(chk("producto inexistente",
                     t.precio_item("Churrascos", "Pizza", None, 1)["ok"], False))
    todos.append(chk("Especial sin variante",
                     t.precio_item("Especiales", "Especial Kayser", None, 1)["ok"], False))
    todos.append(chk("Papas individual (no disponible)",
                     t.precio_item("Papas Fritas", "Papas Fritas", "individual", 1)["ok"], False))

    print("== tarificar_pedido (con IA equivocada a proposito) ==")
    pedido = {
        "cliente": "Test", "modalidad": "retiro", "items": [
            {"categoria": "Churrascos", "producto": "Churrasco Italiano", "variante": None,
             "cantidad": 2, "subtotal": 9800},   # la IA olvido la promo 2x (deberia ser 8900)
            {"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1, "subtotal": 1500},
        ], "total": 11300,
    }
    res = t.tarificar_pedido(pedido)
    # 8900 (2 Italianos con promo) + 1500 (Lata CCU, precio real vigente en menu.json;
    # subio de 1500 a 2000 el 08-jul-2026 y volvio a bajar a 1500 -- confirmado contra
    # el POS real de Christian el 03-ago-2026). El total autoritativo SIEMPRE sigue al
    # menu real, no a lo que "dijo la IA" en este test (esa es la wrongdoing a proposito).
    todos.append(chk("total autoritativo", res["total"], 10400))
    todos.append(chk("detecta que NO coincide con IA", res["coincide_ia"], False))

    print("== promo 2x entre armados del mismo producto (punto 7) ==")
    # Dos 'Italiano' con ARMADO distinto (el armado viaja en notas, no en la clave)
    # deben consolidarse y aplicar la promo 2x.
    ped_arm = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Italiano", "variante": None, "cantidad": 1, "notas": "Tomate/Palta"},
        {"categoria": "Churrascos", "producto": "Churrasco Italiano", "variante": None, "cantidad": 1, "notas": "Palta/Mayo"},
    ], "total": 0}
    r_arm = t.tarificar_pedido(ped_arm)
    todos.append(chk("2 Italianos con armado distinto -> promo 2x", r_arm["total"], 8900))
    todos.append(chk("se consolidan en 1 linea (cantidad 2)", len(r_arm["items"]), 1))
    todos.append(chk("la linea conserva ambos armados en notas",
                     ("Tomate/Palta" in r_arm["items"][0]["notas"]
                      and "Palta/Mayo" in r_arm["items"][0]["notas"]), True))
    # Italiano + Completo (mismo precio) NO forman par: son productos distintos.
    ped_mix = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Italiano", "variante": None, "cantidad": 1},
        {"categoria": "Churrascos", "producto": "Churrasco Completo", "variante": None, "cantidad": 1},
    ], "total": 0}
    todos.append(chk("Italiano + Completo NO aplican 2x (productos distintos)",
                     t.tarificar_pedido(ped_mix)["total"], 9800))

    print("== promo 2x por GRUPO (armados como modulos independientes, punto 8) ==")
    # 1 Italiano + 1 Palta/Mayo: productos DISTINTOS del mismo grupo -> par 2x
    ped_grp = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1},
        {"categoria": "Churrascos", "producto": "Churrasco Palta/Mayo", "cantidad": 1},
    ], "total": 0}
    r_grp = t.tarificar_pedido(ped_grp)
    todos.append(chk("Italiano + Palta/Mayo (mismo grupo) -> 2x", r_grp["total"], 8900))
    todos.append(chk("cada modulo se imprime como su propio producto",
                     [it["producto"] for it in r_grp["items"]], ["Churrasco Italiano", "Churrasco Palta/Mayo"]))
    # 3 modulos: 1 par + 1 suelto = 8900 + 4900 = 13800
    ped_grp3 = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Palta/Mayo", "cantidad": 1},
        {"categoria": "Churrascos", "producto": "Churrasco Tomate/Mayo", "cantidad": 1},
        {"categoria": "Churrascos", "producto": "Churrasco Solo carne", "cantidad": 1},
    ], "total": 0}
    r_grp3 = t.tarificar_pedido(ped_grp3)
    todos.append(chk("3 modulos del grupo -> 1 par + 1 suelto", r_grp3["total"], 13800))
    todos.append(chk("solo 2 de las 3 lineas marcan promo (la suelta no)",
                     sum(1 for it in r_grp3["items"] if it.get("pares_promo")), 2))
    # Palta/Mayo + Completo: Completo no tiene grupo -> sin promo cruzada
    ped_grp_mix = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Palta/Mayo", "cantidad": 1},
        {"categoria": "Churrascos", "producto": "Churrasco Completo", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("Palta/Mayo + Completo NO forman par (grupos distintos)",
                     t.tarificar_pedido(ped_grp_mix)["total"], 9800))

    print("== promo por grupo + TAMANO (As/Vienesas) ==")
    # 2 del grupo as_italiano en XL -> par a 8900
    ped_as = {"items": [
        {"categoria": "As", "producto": "As Italiano", "variante": "xl_30cm", "cantidad": 1},
        {"categoria": "As", "producto": "As Palta/Mayo", "variante": "xl_30cm", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("As Italiano XL + Palta/Mayo XL -> 2x", t.tarificar_pedido(ped_as)["total"], 8900))
    # XL + Normal NO forman par (tamanos/precios distintos): 4900 + 3500 = 8400
    ped_as_mix = {"items": [
        {"categoria": "As", "producto": "As Italiano", "variante": "xl_30cm", "cantidad": 1},
        {"categoria": "As", "producto": "As Palta/Mayo", "variante": "normal_22cm", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("As XL + Normal NO se cruzan (8400)", t.tarificar_pedido(ped_as_mix)["total"], 8400))
    # Vienesa: 2 Normal del grupo -> par a 5000
    ped_vie = {"items": [
        {"categoria": "Vienesas", "producto": "Vienesa Solo vienesa", "variante": "normal_22cm", "cantidad": 1},
        {"categoria": "Vienesas", "producto": "Vienesa Tomate/Mayo", "variante": "normal_22cm", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("Vienesa Solo vienesa + Tomate/Mayo Normal -> 2x (5000)",
                     t.tarificar_pedido(ped_vie)["total"], 5000))

    print("== promo 2x MIXTA: Completo+Dinamico (el par mixto cobra el 2x del MAS CARO) ==")
    # Regla de Christian (2026-07-02, bug real en caja): Completo (4900/8900) +
    # Dinamico (5200/9200) -> el par cobra 9200 (el 2x del mas caro), no 9050 ni 8900.
    ped_mix_chur = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Completo", "cantidad": 1},
        {"categoria": "Churrascos", "producto": "Churrasco Dinamico", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("Churrasco Completo+Dinamico -> 9200 (2x del mas caro; bug caja 02-jul)",
                     t.tarificar_pedido(ped_mix_chur)["total"], 9200))
    # Los IGUALES se emparejan primero: 2 Completos hacen par propio (8900) y el
    # Dinamico queda suelto a precio normal (5200) -> 14100 (no se cruza de mas).
    ped_2c1d = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Completo", "cantidad": 2},
        {"categoria": "Churrascos", "producto": "Churrasco Dinamico", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("2 Completos + 1 Dinamico -> 8900 + 5200 = 14100 (iguales primero)",
                     t.tarificar_pedido(ped_2c1d)["total"], 14100))
    # Vienesas: mismo tamaño (XL) SI empareja; distinto tamaño NO
    ped_mix_vie_xl = {"items": [
        {"categoria": "Vienesas", "producto": "Vienesa Completo", "variante": "xl_30cm", "cantidad": 1},
        {"categoria": "Vienesas", "producto": "Vienesa Dinamico", "variante": "xl_30cm", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("Vienesa Completo XL + Dinamico XL -> 6600 (2x del mas caro)",
                     t.tarificar_pedido(ped_mix_vie_xl)["total"], 6600))
    ped_mix_vie_talla = {"items": [
        {"categoria": "Vienesas", "producto": "Vienesa Completo", "variante": "normal_22cm", "cantidad": 1},
        {"categoria": "Vienesas", "producto": "Vienesa Dinamico", "variante": "xl_30cm", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("Vienesa Completo NORMAL + Dinamico XL NO empareja (tallas distintas) -> 6300",
                     t.tarificar_pedido(ped_mix_vie_talla)["total"], 2700 + 3600))
    # As: mismo tamaño (XL)
    ped_mix_as = {"items": [
        {"categoria": "As", "producto": "As Completo", "variante": "xl_30cm", "cantidad": 1},
        {"categoria": "As", "producto": "As Dinamico", "variante": "xl_30cm", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("As Completo XL + Dinamico XL -> 9200 (2x del mas caro)",
                     t.tarificar_pedido(ped_mix_as)["total"], 9200))
    # El grupo Completo/Dinamico NO se mezcla con el grupo Italiano
    ped_no_cruza = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1},
        {"categoria": "Churrascos", "producto": "Churrasco Dinamico", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("Italiano + Dinamico NO forman par (grupos distintos) -> 10100",
                     t.tarificar_pedido(ped_no_cruza)["total"], 4900 + 5200))

    print("== combos (punto 10: Box + bebida) ==")
    r_combo = t.precio_item("Boxes Pollo Crispy", "Box 1 + Bebida", None, 1)
    todos.append(chk("combo cobra el precio FIJO (no la suma 6500+1500=8000)",
                     r_combo["subtotal"], 7500))
    todos.append(chk("combo expone sus componentes para la comanda",
                     r_combo.get("componentes"), ["Box 1", "Lata CCU"]))
    todos.append(chk("2 combos -> 15000 (sin promo 2x: el combo no la tiene)",
                     t.precio_item("Boxes Pollo Crispy", "Box 1 + Bebida", None, 2)["subtotal"], 15000))

    print("== validacion de combos rotos (se detecta ANTES de guardar el menu) ==")
    menu_roto = json.loads(json.dumps(t.menu))   # copia profunda via ida-vuelta JSON
    menu_roto["categorias"][0]["productos"].append(
        {"nombre": "Combo Roto", "precio": 1000,
         "combo": [{"categoria": "Churrascos", "producto": "Noexiste"}]})
    try:
        Tarificador(menu_roto)
        rechaza_combo_roto = False
    except ValueError:
        rechaza_combo_roto = True
    todos.append(chk("Tarificador rechaza un combo con componente inexistente",
                     rechaza_combo_roto, True))

    print("== aji verde aparte: gratis con Chacarero, cobrado sin Chacarero (12-ago-2026) ==")
    # El aji verde TODAVIA NO esta en data/menu.json (lo carga Gonza en el POS), asi que el
    # candado se prueba contra una copia del menu con el producto puesto. El dia que exista de
    # verdad, estos tests siguen valiendo igual: el candado no conoce precios, los saca del menu.
    menu_aji = json.loads(json.dumps(t.menu))
    salsas = next(c for c in menu_aji["categorias"] if c["nombre"] == "Salsas")
    salsas["productos"].append({"nombre": "Aji verde", "precio": 500})
    salsas["productos"].append({"nombre": "Ajiaco", "precio": 3000})   # control negativo
    ta = Tarificador(menu_aji)
    solo_aji = {"items": [{"categoria": "Salsas", "producto": "Aji verde", "cantidad": 1}], "total": 0}
    todos.append(chk("aji verde solo (sin Chacarero) -> se cobra 500",
                     ta.tarificar_pedido(solo_aji)["total"], 500))
    aji_regalado_por_la_ia = {"items": [
        {"categoria": "Salsas", "producto": "Aji verde", "cantidad": 1, "subtotal": 0}], "total": 0}
    todos.append(chk("sin Chacarero, la IA NO puede regalarlo: el menu cobra igual -> 500",
                     ta.tarificar_pedido(aji_regalado_por_la_ia)["total"], 500))
    ch_mas_aji = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Chacarero", "cantidad": 1},
        {"categoria": "Salsas", "producto": "Aji verde", "cantidad": 1, "subtotal": 500},
    ], "total": 0}
    r_ch = ta.tarificar_pedido(ch_mas_aji)
    todos.append(chk("Churrasco Chacarero + aji aparte -> el aji va en 0", r_ch["total"], 5200))
    todos.append(chk("y queda marcado como regalo para la comanda",
                     [i.get("aji_de_regalo") for i in r_ch["items"] if i.get("aji_de_regalo")], [1]))
    me_mas_aji = {"items": [
        {"categoria": "Mechadas", "producto": "Mechada Chacarero", "cantidad": 1},
        {"categoria": "Salsas", "producto": "Aji verde", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("Mechada Chacarero + aji aparte -> el aji va en 0",
                     ta.tarificar_pedido(me_mas_aji)["total"], 5800))
    uno_y_tres = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Chacarero", "cantidad": 1},
        {"categoria": "Salsas", "producto": "Aji verde", "cantidad": 3},
    ], "total": 0}
    todos.append(chk("1 Chacarero + 3 ajies -> 1 gratis, 2 cobrados (tope = cant. de Chacareros)",
                     ta.tarificar_pedido(uno_y_tres)["total"], 5200 + 2 * 500))
    dos_ch = ta.precio_item("Churrascos", "Churrasco Chacarero", None, 2)["subtotal"]
    dos_y_tres = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Chacarero", "cantidad": 2},
        {"categoria": "Salsas", "producto": "Aji verde", "cantidad": 3},
    ], "total": 0}
    todos.append(chk("2 Chacareros + 3 ajies -> 2 gratis, 1 cobrado",
                     ta.tarificar_pedido(dos_y_tres)["total"], dos_ch + 500))
    ajiaco = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Chacarero", "cantidad": 1},
        {"categoria": "Salsas", "producto": "Ajiaco", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("control negativo: 'Ajiaco' NO es aji (match por palabra) -> se cobra",
                     ta.tarificar_pedido(ajiaco)["total"], 5200 + 3000))
    otra_salsa = {"items": [
        {"categoria": "Churrascos", "producto": "Churrasco Chacarero", "cantidad": 1},
        {"categoria": "Salsas", "producto": "Ketchup", "cantidad": 1},
    ], "total": 0}
    todos.append(chk("control negativo: el Chacarero no regala CUALQUIER salsa -> ketchup se cobra",
                     ta.tarificar_pedido(otra_salsa)["total"], 5200 + 400))

    print("\n" + ("TODO OK" if all(todos) else ">>> HAY FALLAS <<<"))

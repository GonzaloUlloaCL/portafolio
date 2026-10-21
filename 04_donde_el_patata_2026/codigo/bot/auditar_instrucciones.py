"""Auditoria de las instrucciones del bot -- F1 y F2 del PLAN_AUDITORIA_PROMPT.

Que hace
--------
Mide el prompt REAL que recibe el modelo en cada mensaje (no solo `system_prompt.md`:
tambien el menu y `cierre_pedido.md`, que se pegan en `prototipo.construir_sistema`),
parte las instrucciones en reglas contables y busca las CUATRO clases de defecto del
plan:

    D1 duplicado          la misma regla dos veces
    D2 contradiccion      dos reglas mandan lo opuesto
    D3 huerfana           pide un dato que ningun campo del sistema recibe
    D4 ya defendida       el prompt cuida algo que un candado de codigo ya garantiza

Que NO hace
-----------
No borra ni edita una sola linea del prompt: ESTO SOLO MIDE. El plan es explicito en
que F2 termina en una lista para que Gonza apruebe una por una (y D2, que son
decisiones de negocio, las valida Christian). El script tampoco decide solo: D1 y D4
los detecta con confianza razonable, pero D2 y D3 salen como CANDIDATOS para leer --
"pide un dato que no existe" y "esto contradice a aquello" son juicios de significado,
y un script que los diera por ciertos estaria inventando autoridad que no tiene.

Uso
---
    python bot/auditar_instrucciones.py              # informe a pantalla
    python bot/auditar_instrucciones.py --md ruta.md # ademas escribe el informe

Cada hallazgo sale con archivo, numero de linea y cita textual, para poder ir a
verificarlo sin buscar a mano.
"""
import argparse
import difflib
import re
import sys
import unicodedata
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import prototipo  # noqa: E402

# Aproximacion de tokens: el prompt es espanol con markdown, ~4 caracteres por token.
# No es exacta (no se tokeniza de verdad), sirve para comparar antes/despues.
CARS_POR_TOKEN = 4

# ---------------------------------------------------------------------------
# Los CANDADOS DE CODIGO vigentes (webhook.py). Una regla del prompt cuyo tema ya
# esta en esta lista es un candidato D4: el modelo la lee, la obedece y la arbitra en
# CADA mensaje sin que aporte seguridad, porque el codigo ya no lo deja pasar.
# Si se agrega un candado nuevo al bot, se agrega aca -- si no, la auditoria deja de
# ver esa capa y vuelve a sobrar prompt sin que nadie se entere.
# ---------------------------------------------------------------------------
CANDADOS = {
    "horario / local cerrado": ["cerrado", "horario", "no estamos atendiendo", "23:50",
                                "deja de tomar", "abre a las"],
    "modalidad retiro/delivery": ["modalidad", "retiro con direccion", "sin modalidad",
                                  "delivery o retiro", "modalidad queda sin fijar"],
    "el envio no lo pone el cliente": ["delivery_cobro", "cobro de envio", "valor del envio",
                                       "cuanto sale el envio", "tarifa de envio"],
    "tamano en Churrasco/Mechada": ["xl", "22cm", "30cm", "tamano", "variante"],
    "pedido grande (10+ por linea)": ["pedido grande", "10 unidades", "cantidad grande"],
    "total autoritativo / montos no autorizados": ["total", "precio", "subtotal", "monto",
                                                   "no inventes precios", "$"],
    "pedidos programados": ["hora_programada", "para mas tarde", "programado"],
    "saludo repetido": ["bienvenid", "saludo", "una sola vez"],
    "datos fijos del cliente": ["nombre registrado", "cliente conocido", "no repreguntes"],
    "pedido duplicado": ["dos veces", "pedido duplicado", "ya esta tomado"],
    "nombre en la comanda": ["cliente whatsapp", "nombre del cliente"],
}

# Campos que el sistema REALMENTE recibe (schema de las tools de pedido, prototipo.py).
# Una regla que le pide al cliente algo que no cae en ninguno de estos es candidata a
# D3: gasta un turno del cliente para tirar el dato a la basura.
CAMPOS_SISTEMA = {
    "cliente": ["nombre", "como te llamas", "a nombre de"],
    "modalidad": ["retiro", "delivery", "despacho", "pasar a buscar"],
    "direccion": ["direccion", "calle", "donde", "departamento", "block", "villa"],
    "items": ["producto", "pedido", "que va a querer", "categoria", "cantidad", "agregado",
              "salsa", "bebida", "completo", "churrasco", "papas"],
    "items.variante": ["tamano", "xl", "normal", "individual", "mediana", "grande",
                       "nuggets", "empanaditas"],
    "items.notas": ["sin tomate", "salsa aparte", "modificacion", "sin palta", "notas"],
    "forma_pago": ["efectivo", "tarjeta", "transferencia", "como pagas", "forma de pago"],
    "pago_con / vuelto": ["con cuanto", "paga justo", "vuelto", "billete"],
    "hora_programada": ["para las", "mas tarde", "hora"],
    "comentarios / derivar_humano": ["derivar", "humano", "comentario", "reclamo"],
}

# Verbos con los que una regla le ORDENA al modelo pedirle un dato al cliente. Son los
# unicos que pueden generar una huerfana: una regla que no pide nada no gasta un turno.
VERBOS_PEDIR = ("pregunta", "preguntale", "pregunt", "pide", "pidele", "pidelos",
                "consulta", "consultale", "averigua", "confirma con el cliente")

NEGACIONES = ("no ", "nunca ", "jamas ", "evita ", "sin ")


def _norm(texto):
    """Minusculas, sin tildes y sin adornos de markdown -- para comparar reglas."""
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[*_`#>]", "", t)
    return re.sub(r"\s+", " ", t).strip()


# Una orden de pedirle un dato al cliente: "preguntale X" / "NO preguntes X". Se captura
# el objeto (que se pide) porque es lo unico que permite decidir si dos reglas se
# contradicen o hablan de cosas distintas.
RE_ORDEN = re.compile(
    r"(?:^|[.;:,¿(]\s*|\s)(no|nunca|jamas)?\s*"
    r"(pregunt(?:a|ale|es|arlo|alo|arselo)?|pid(?:e|ele|elos|as)|consult(?:a|ale))\b"
    r"([^.;:!?¿]{0,70})", re.IGNORECASE)
# "pregunta" tambien es sustantivo ("si el mensaje trae una pregunta"): con articulo
# delante NO es una orden. Sin este filtro una sola regla se emparejaba con media docena.
RE_SUSTANTIVO = re.compile(r"\b(una|la|esa|esta|su|tu|alguna|cualquier)\s*$", re.IGNORECASE)
VACIAS = {"cliente", "pedido", "siempre", "cuando", "porque", "entonces", "tambien",
          "corto", "natural", "directo", "solo", "nada", "para", "como", "todo", "eso",
          "ya", "que", "lo", "le", "de", "en", "el", "la", "los", "las", "un", "una"}


class Regla:
    def __init__(self, rid, archivo, linea, bloque, texto, grupo=0):
        self.id = rid
        self.archivo = archivo
        self.linea = linea
        self.bloque = bloque
        self.texto = texto
        self.grupo = grupo      # vinetas hermanas de la misma lista (ver _ambiguas_por_vecindad)
        self.norm = _norm(texto)
        self.candados = []      # D4
        self.campos = []        # a que campo del sistema alimenta
        self.ordenes = self._ordenes()
        self.pide_dato = bool(self.ordenes)

    def _ordenes(self):
        """[(es_negativa, {palabras del objeto})] de cada orden de pedir dato."""
        salida = []
        for m in RE_ORDEN.finditer(self.norm):
            if RE_SUSTANTIVO.search(self.norm[:m.start(2)]):
                continue
            objeto = {p for p in re.findall(r"[a-z]{3,}", m.group(3) or "")} - VACIAS
            salida.append((bool(m.group(1)), objeto))
        return salida

    @property
    def cita(self):
        corto = self.texto if len(self.texto) <= 110 else self.texto[:107] + "..."
        return f"{self.archivo}:{self.linea} · {corto}"


def _reglas_de_archivo(ruta):
    """Parte un .md del prompt en reglas contables.

    Una regla es una vineta (`- `, `* `, `N.`) con sus lineas de continuacion, o un
    parrafo que empieza en negrita (asi se escriben las reglas fuertes de este prompt:
    `**Nunca frenes un pedido por no tener nombre**`). Los titulos y el texto de relleno
    no cuentan: no son ordenes."""
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    reglas, bloque, actual = [], "(sin bloque)", None
    grupo, venia_vineta = 0, False

    def cerrar():
        nonlocal actual
        if actual and len(_norm(actual["texto"])) > 12:
            reglas.append(Regla(f"{ruta.stem[:3].upper()}{len(reglas) + 1:03d}",
                                ruta.name, actual["linea"], actual["bloque"],
                                re.sub(r"\s+", " ", actual["texto"]).strip(),
                                grupo=actual["grupo"]))
        actual = None

    for n, cruda in enumerate(lineas, start=1):
        linea = cruda.rstrip()
        if re.match(r"^#{1,3}\s", linea):
            cerrar()
            bloque = re.sub(r"^#{1,3}\s+", "", linea).strip()
            grupo, venia_vineta = grupo + 1, False
            continue
        arranca = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.*)$", linea)
        if arranca:
            cerrar()
            if not venia_vineta:          # arranca una lista nueva
                grupo += 1
            venia_vineta = True
            actual = {"linea": n, "bloque": bloque, "texto": arranca.group(1),
                      "grupo": grupo}
            continue
        if re.match(r"^\s*(?:⚠️\s*)?\*\*", linea):        # parrafo-regla en negrita
            cerrar()
            grupo, venia_vineta = grupo + 1, False
            actual = {"linea": n, "bloque": bloque, "texto": linea.strip(),
                      "grupo": grupo}
            continue
        if not linea.strip():
            cerrar()
            continue
        if actual:                                       # continuacion de la regla
            actual["texto"] += " " + linea.strip()
        else:
            venia_vineta = False                         # parrafo suelto: corta la lista
    cerrar()
    return reglas


def _clasificar(reglas):
    for r in reglas:
        for nombre, pistas in CANDADOS.items():
            if any(_norm(p) in r.norm for p in pistas):
                r.candados.append(nombre)
        for campo, pistas in CAMPOS_SISTEMA.items():
            if any(_norm(p) in r.norm for p in pistas):
                r.campos.append(campo)


def _duplicados(reglas, corte=0.72):
    """Pares de reglas que dicen casi lo mismo (D1). difflib sobre el texto normalizado:
    no entiende de significado, pero el copy-paste que genera duplicados deja huella
    literal -- que es exactamente el defecto que busca esta clase."""
    pares = []
    for i, a in enumerate(reglas):
        for b in reglas[i + 1:]:
            if abs(len(a.norm) - len(b.norm)) > max(len(a.norm), len(b.norm)) * 0.6:
                continue
            ratio = difflib.SequenceMatcher(None, a.norm, b.norm).ratio()
            if ratio >= corte:
                pares.append((ratio, a, b))
    return sorted(pares, key=lambda p: -p[0])


def _contradicciones(reglas):
    """CANDIDATOS a D2: una regla ordena preguntar algo y otra prohibe preguntar ESO MISMO.

    La primera version de esta funcion comparaba "raices comunes" entre reglas y devolvia
    67 pares, casi todos falsos (una sola regla larga se emparejaba con media docena) y
    sin encontrar el caso que Christian SI habia reportado. Un detector asi no es un
    colador ancho: es ruido que obliga a leer el prompt entero igual, o sea no mide nada.

    Ahora compara el OBJETO de la orden: se extrae (verbo, que se pide) de cada regla y
    solo se emparejan las que piden lo mismo con signo opuesto."""
    pares = []
    for i, a in enumerate(reglas):
        for b in reglas[i + 1:]:
            for neg_a, obj_a in a.ordenes:
                for neg_b, obj_b in b.ordenes:
                    if neg_a == neg_b or not (obj_a and obj_b):
                        continue
                    comunes = obj_a & obj_b
                    if len(comunes) < 2:
                        continue
                    pares.append((sorted(comunes), a, b))
    return pares


def _ambiguas_por_vecindad(reglas):
    """El defecto que reporto Christian y que NO es un duplicado ni una contradiccion:
    una linea sin verbo ("**Tarjeta:** debito o credito.") viviendo en una lista donde
    las hermanas SI son ordenes de preguntar. El modelo la lee como orden de preguntar
    tambien, y gasta un turno del cliente en un dato que el sistema no guarda.

    Se busca por la FORMA, que es lo que el modelo ve: dentro de un mismo grupo de
    vinetas, las que son 'etiqueta: enumeracion' sin verbo, cuando alguna hermana manda
    preguntar."""
    grupos = {}
    for r in reglas:
        grupos.setdefault((r.archivo, r.grupo), []).append(r)
    salida = []
    for hermanas in grupos.values():
        if len(hermanas) < 2:
            continue
        if not any(h.ordenes for h in hermanas):
            continue
        for h in hermanas:
            if h.ordenes:
                continue
            if re.match(r"^\**[A-Za-zÀ-ÿ ]{3,25}\**\s*:", h.texto.strip("*⚠️ ")):
                salida.append((h, [x for x in hermanas if x.ordenes][0]))
    return salida


def _huerfanas(reglas):
    """CANDIDATOS a D3: la regla ORDENA pedir un dato y no calza con ningun campo que el
    sistema reciba. Si la lectura confirma que el dato no existe en el schema, el bot
    esta gastando un turno del cliente para no guardar nada."""
    return [r for r in reglas if r.pide_dato and not r.campos]


# Turnos de una conversacion real, para medir el prompt como sale de verdad desde F5
# (el menu por intencion): el bloque de menu ya no es fijo, asi que un solo numero
# mentiria. Estos son un saludo, un pedido y un turno de cierre.
TURNOS_TIPICOS = [
    "hola, tienen delivery?",
    "quiero 2 churrasco italiano",
    "hola\nquiero 2 churrasco italiano\n¿tu direccion?\nDel Valle 1234\npago con tarjeta",
]


def _medir_prompt():
    """El prompt en su TECHO: el que recibe el modelo cuando el cliente pide la carta
    entera. Desde F5 (06-ago-2026) el menu viaja por intencion, asi que un turno normal
    pesa menos -- ver _medir_tipicos, que es lo que se paga de verdad."""
    completo = prototipo.construir_sistema()
    base = prototipo.SYSTEM_PROMPT.read_text(encoding="utf-8")
    cierre = prototipo.CIERRE_PEDIDO.read_text(encoding="utf-8")
    partes = [("system_prompt.md", len(base)), ("cierre_pedido.md", len(cierre)),
              ("menu + datos del local (se arma en cada mensaje)",
               len(completo) - len(base) - len(cierre))]
    return completo, partes


def _medir_tipicos():
    """(descripcion, caracteres) del prompt en turnos reales, con el foco de F5 puesto."""
    return [(t.splitlines()[-1][:40], len(prototipo.construir_sistema(conversacion=t)))
            for t in TURNOS_TIPICOS]


def _frontmatter():
    """El informe es un NODO de la red, no un archivo suelto: sin frontmatter el grafo
    lo indexa igual pero sin estado ni fecha, y queda como un nodo mudo (verificado el
    06-ago-2026, primera corrida). Se emite en cada generacion con la fecha del dia."""
    from datetime import date
    return ("---\n"
            "id: patata-auditoria-instrucciones\n"
            'titulo: "Donde el Patata — auditoría de instrucciones del bot (F1+F2)"\n'
            "tipo: informe\n"
            "personalidad: emprendedor\n"
            "estado: activo\n"
            "prioridad: media\n"
            f"ultima_actualizacion: {date.today().isoformat()}\n"
            "bordes:\n"
            "  - tipo: DEPENDE_DE\n"
            '    destino: "Proyectos automáticos con Claude y VSC/01_PROYECTOS/'
            'en_curso/Donde el Patata/bot/PLAN_AUDITORIA_PROMPT.md"\n'
            "---\n\n"
            "> ⚠️ **Generado por `auditar_instrucciones.py`. No editar a mano** — se "
            "reescribe entero en cada corrida.\n\n")


def informe():
    completo, partes = _medir_prompt()
    reglas = (_reglas_de_archivo(prototipo.SYSTEM_PROMPT)
              + _reglas_de_archivo(prototipo.CIERRE_PEDIDO))
    _clasificar(reglas)
    L = []
    p = L.append

    p("# Auditoria de instrucciones del bot (F1 + F2)")
    p("")
    p("## F1 · Cuanto pesa el prompt REAL (el que recibe el modelo en cada mensaje)")
    p("")
    p("| Parte | Caracteres | % | tokens aprox |")
    p("|---|---:|---:|---:|")
    for nombre, cars in partes:
        p(f"| {nombre} | {cars:,} | {cars * 100 // max(len(completo), 1)}% | "
          f"{cars // CARS_POR_TOKEN:,} |")
    p(f"| **TECHO por mensaje** | **{len(completo):,}** | 100% | "
      f"**{len(completo) // CARS_POR_TOKEN:,}** |")
    p("")
    p("Desde F5 (06-ago-2026) el menu viaja **por intencion**: el techo de arriba es lo "
      "que se paga cuando el cliente pide la carta entera. Un turno normal pesa menos.")
    p("")
    p("| Turno real | Caracteres | vs. techo |")
    p("|---|---:|---:|")
    for desc, cars in _medir_tipicos():
        p(f"| {desc} | {cars:,} | {cars - len(completo):+,} "
          f"({100 * (cars - len(completo)) // max(len(completo), 1):+d}%) |")
    p("")

    por_bloque = {}
    for r in reglas:
        por_bloque.setdefault(f"{r.archivo} · {r.bloque}", []).append(r)
    p(f"**Reglas contables: {len(reglas)}**, repartidas asi:")
    p("")
    p("| Bloque | Reglas |")
    p("|---|---:|")
    for bloque, rs in sorted(por_bloque.items(), key=lambda kv: -len(kv[1])):
        p(f"| {bloque} | {len(rs)} |")
    p("")

    dups = _duplicados(reglas)
    p(f"## F2 · D1 — duplicados ({len(dups)} pares)")
    p("")
    if not dups:
        p("_Ninguno. El copy-paste que quedaba se limpio en sesiones anteriores._")
    for ratio, a, b in dups[:25]:
        p(f"- **{int(ratio * 100)}% parecidas**")
        p(f"  - {a.cita}")
        p(f"  - {b.cita}")
    p("")

    d4 = [r for r in reglas if r.candados]
    p(f"## F2 · D4 — ya defendida por codigo ({len(d4)} reglas)")
    p("")
    p("_La que mas pesa: el modelo lee, obedece y arbitra estas reglas en CADA mensaje,")
    p("y el candado de codigo las garantiza igual. Ojo: que el codigo lo garantice no")
    p("siempre significa que la regla sobre -- a veces el prompt esta explicando el TONO")
    p("con que se dice, no la garantia. Se aprueban una por una._")
    p("")
    for nombre in CANDADOS:
        suyas = [r for r in d4 if nombre in r.candados]
        if not suyas:
            continue
        p(f"**Candado: {nombre}** — {len(suyas)} reglas")
        for r in suyas[:8]:
            p(f"  - {r.cita}")
        if len(suyas) > 8:
            p(f"  - _(+{len(suyas) - 8} mas)_")
        p("")

    huerfanas = _huerfanas(reglas)
    p(f"## F2 · D3 — candidatas a huerfana ({len(huerfanas)})")
    p("")
    p("_Ordenan pedirle un dato al cliente que no calzo con ningun campo del schema._")
    p("")
    for r in huerfanas:
        p(f"- {r.cita}")
    p("")

    contras = _contradicciones(reglas)
    p(f"## F2 · D2 — contradicciones directas ({len(contras)} pares)")
    p("")
    p("_Una regla manda preguntar algo y otra prohibe preguntar ESO MISMO (se compara el")
    p("objeto de la orden, no palabras sueltas en comun)._")
    p("")
    if not contras:
        p("_Ninguna._")
    for comunes, a, b in contras[:25]:
        p(f"- **objeto en comun: {', '.join(comunes)}**")
        p(f"  - {a.cita}")
        p(f"  - {b.cita}")
    p("")

    vecinas = _ambiguas_por_vecindad(reglas)
    p(f"## F2 · D2b — ambiguas por vecindad ({len(vecinas)})")
    p("")
    p("_La clase que reporto Christian y que no es duplicado ni contradiccion: una linea")
    p("SIN verbo ('**Tarjeta:** debito o credito') en una lista donde las hermanas SI son")
    p("ordenes de preguntar. El modelo la lee como orden de preguntar tambien._")
    p("")
    if not vecinas:
        p("_Ninguna._")
    for sospechosa, hermana in vecinas:
        p(f"- {sospechosa.cita}")
        p(f"  - _hermana que SI ordena preguntar:_ {hermana.cita}")
    p("")
    p("---")
    p("")
    p("_Generado por `bot/auditar_instrucciones.py`. NADA se borro: esto es la lista")
    p("para aprobar una por una (F2 del plan). La poda es F4, y va despues del banco")
    p("de regresion (F3) -- sin banco, cada corte es a ciegas._")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Audita las instrucciones del bot (F1+F2).")
    ap.add_argument("--md", help="ademas de imprimir, escribe el informe en este archivo")
    args = ap.parse_args()
    texto = informe()
    print(texto)
    if args.md:
        # El frontmatter va SOLO en el archivo (es lo que lo hace nodo del grafo), no en
        # la salida por pantalla.
        Path(args.md).write_text(_frontmatter() + texto + "\n", encoding="utf-8")
        print(f"\n[escrito] {args.md}")

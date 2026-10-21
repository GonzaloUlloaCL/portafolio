#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
impresora.py — Impresión directa ESC/POS a la X-Printer (térmica 80 mm) por Windows.
====================================================================================
Manda los bytes ESC/POS a la impresora a través del **spooler de Windows** (la X-Printer
ya está instalada con su driver — "XPrinter v7.77"). No necesita Zadig ni libusb: usa
`win32print` (pywin32) con datatype RAW. Si pywin32 no está (o no es Windows), las
funciones de impresión avisan con un error claro y el resto del POS sigue funcionando.

Config en `pos/config.json`: { "impresora": "<nombre Windows>", "ancho": 48 }.
Si no hay nombre guardado, autodetecta una que parezca térmica o usa la predeterminada.

Probar sin imprimir:  python pos/impresora.py   (lista impresoras + muestra la comanda)
"""

import json
import sys
import unicodedata
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from rutas import CONFIG_IMPRESORA as CONFIG, BOT  # noqa: E402  (rutas .py vs .exe)
sys.path.insert(0, str(BOT))
from tarificar import fmt_pesos, label_variante  # noqa: E402

ANCHO_80MM = 48   # caracteres por línea a 80 mm, fuente A

# --- comandos ESC/POS ---
ESC, GS = b"\x1b", b"\x1d"
INIT = ESC + b"@"
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
ALIGN_L, ALIGN_C = ESC + b"a\x00", ESC + b"a\x01"
DOBLE_ON, DOBLE_OFF = GS + b"!\x11", GS + b"!\x00"     # doble alto+ancho
DOBLE_ALTO = GS + b"!\x01"                             # solo doble alto (no rompe las 48 col)
CORTE = GS + b"V\x42\x00"                              # avanza y corta (parcial)


def qr_bytes(contenido, escala=6, quiet=3):
    """QR como IMAGEN raster (GS v 0), NO como comando QR nativo (GS ( k).

    07-jul-2026 (bug real en la termica de Christian): la XPrinter NO interpreta el
    comando QR nativo GS ( k -> lo imprimia como texto crudo ('1A2 1C1 ... 1Q0' + el
    link). El comando GS ( k funciona en muchas Epson pero no en esta impresora. La
    solucion que anda en CUALQUIER termica: generar la matriz del QR y mandarla como
    bitmap raster (GS v 0), que toda impresora sabe imprimir como imagen.

    'escala' = pixeles por modulo (6 -> ~186px, escaneable de un celular en 80mm);
    'quiet' = margen en modulos alrededor (zona de silencio que el QR necesita para
    escanearse). Usa la lib 'qrcode' solo para la matriz (sin PIL); si faltara, cae al
    link en texto plano para no romper la comanda."""
    try:
        import qrcode
    except Exception:
        return _enc("(sin QR) " + contenido) + b"\n"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=quiet)
    qr.add_data(contenido)
    qr.make(fit=True)
    m = qr.get_matrix()
    n = len(m)
    ancho_bytes = (n * escala + 7) // 8
    alto_px = n * escala
    o = bytearray()
    o += GS + b"v0\x00"                                          # GS v 0, modo normal
    o += bytes([ancho_bytes & 0xFF, (ancho_bytes >> 8) & 0xFF])  # ancho en BYTES (xL,xH)
    o += bytes([alto_px & 0xFF, (alto_px >> 8) & 0xFF])          # alto en puntos (yL,yH)
    for fila in m:
        linea = bytearray(ancho_bytes)
        for x, negro in enumerate(fila):
            if negro:
                base = x * escala
                for sx in range(escala):
                    px = base + sx
                    linea[px >> 3] |= 0x80 >> (px & 7)
        for _ in range(escala):        # escalar tambien en vertical
            o += linea
    return bytes(o)


def _norm(s):
    """A ASCII: las térmicas no imprimen bien tildes/ñ."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c))


def _enc(s, mayus=True):
    # 02-jul-2026 (regla C1): por defecto TODO el texto va en MAYUSCULAS. Desde el
    # editor de comanda (07-jul) el tema puede apagarlo (mayus=False) para toda la
    # comanda; cierre_bytes/test_bytes siguen en mayusculas (llaman sin el parametro).
    s = _norm(s)
    if mayus:
        s = s.upper()
    return s.encode("cp437", "replace")


def _fila(izq, der, ancho):
    """izq a la izquierda, der a la derecha, dentro de 'ancho'."""
    izq, der = str(izq), str(der)
    if len(izq) + len(der) + 1 > ancho:
        izq = izq[: max(0, ancho - len(der) - 1)]
    return izq + " " * max(1, ancho - len(izq) - len(der)) + der


def _fecha_hora(iso):
    """'2026-06-28T14:30:00' -> '28-06-2026 14:30' (timestamp de la comanda)."""
    if not iso or len(iso) < 16:
        return iso or ""
    y, m, d = iso[:10].split("-")
    return f"{d}-{m}-{y} {iso[11:16]}"


def _lineas_pago_txt(pedido):
    """Lineas del/los metodo(s) de pago bajo el total. Uno: 'Pago: X'. Varios:
    'Pago (mixto):' + cada metodo con su monto, de mayor a menor."""
    pagos = pedido.get("pagos")
    if pagos and len(pagos) > 1:
        out = ["Pago (mixto):"]
        for pg in sorted(pagos, key=lambda x: -(x.get("monto") or 0)):
            out.append(f"  - {pg.get('metodo')}: {fmt_pesos(pg.get('monto') or 0)}")
        return out
    if pagos and len(pagos) == 1:
        return [f"Pago: {pagos[0].get('metodo')}"]
    fp = pedido.get("forma_pago")
    return [f"Pago: {fp}"] if fp else []


# ---------------------------------------------------------------------------
# Config / selección de impresora
# ---------------------------------------------------------------------------
def _cfg():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def guardar_config(impresora=None, ancho=None):
    c = _cfg()
    if impresora is not None:
        c["impresora"] = impresora
    if ancho is not None:
        c["ancho"] = int(ancho)
    CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    return c


def listar_impresoras():
    try:
        import win32print
    except ImportError:
        return []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def impresora_actual():
    """La impresora configurada; si no hay, autodetecta una térmica o la predeterminada."""
    c = _cfg()
    if c.get("impresora"):
        return c["impresora"]
    nombres = listar_impresoras()
    for n in nombres:
        ln = n.lower()
        if any(k in ln for k in ("xprinter", "xp-", "xp ", "pos", "80", "thermal", "term", "ticket")):
            return n
    try:
        import win32print
        return win32print.GetDefaultPrinter()
    except Exception:
        return nombres[0] if nombres else None


# ---------------------------------------------------------------------------
# Comanda desde un TEMA editable (07-jul-2026)
# ---------------------------------------------------------------------------
# La comanda ya no tiene el orden ni los tamanos fijos: se arma desde un "tema" que
# el editor del POS (Admin -> Diseno de comanda) guarda en config.json. Cada bloque
# genera SU contenido con la logica de siempre (telefono solo si hay, direccion grande
# en delivery, total alineado, etc.); el TEMA decide el ORDEN, la VISIBILIDAD y el
# ESTILO (tamano/negrita/alineacion) de cada bloque, mas ajustes globales (mayusculas,
# separador, interlineado, espacio entre letras). El default reproduce el diseno limpio
# validado con Gonza/Christian: encabezado y total grandes, telefono/direccion/items en
# doble alto para la cocina y el repartidor, QR de WhatsApp al pie.

TEMA_COMANDA_DEFAULT = {
    "mayus": True,
    "separador": "guiones",   # entre bloques: 'guiones' | 'blanco' | 'nada'
    "sep": 0,                 # lineas en blanco EXTRA entre bloques
    "inter": 1.0,             # interlineado (1.0 = normal); ESC 3
    "track": 0,               # espacio entre caracteres (dots); ESC SP
    "bloques": [
        {"id": "header",  "on": True, "tam": "dd", "bold": True,  "al": "c"},
        {"id": "tel",     "on": True, "tam": "da", "bold": True,  "al": "l"},
        {"id": "cliente", "on": True, "tam": "n",  "bold": False, "al": "l"},
        {"id": "modo",    "on": True, "tam": "da", "bold": True,  "al": "l"},
        {"id": "items",   "on": True, "tam": "da", "bold": True,  "al": "l"},
        {"id": "total",   "on": True, "tam": "dd", "bold": True,  "al": "l"},
        {"id": "pago",    "on": True, "tam": "n",  "bold": False, "al": "l"},
        {"id": "vuelto",  "on": True, "tam": "dd", "bold": True,  "al": "c"},
        {"id": "qr",      "on": True, "tam": "n",  "bold": False, "al": "c"},
    ],
}

ETIQUETA_BLOQUE = {
    "header": "Encabezado (local + N + fecha)", "tel": "Teléfono", "cliente": "Cliente",
    "modo": "Modalidad / dirección", "items": "Ítems del pedido", "total": "Total",
    "pago": "Pago", "vuelto": "Vuelto (grande)", "qr": "QR WhatsApp",
}

_TAM_ESC = {"n": (b"", b""), "da": (DOBLE_ALTO, DOBLE_OFF), "dd": (DOBLE_ON, DOBLE_OFF)}
_PROPS_BLOQUE = ("on", "tam", "bold", "al")


def tema_comanda():
    """El tema guardado (config.json) fusionado sobre el default; garantiza que esten
    TODOS los bloques (respeta el orden guardado y agrega al final los que falten)."""
    t = {k: v for k, v in TEMA_COMANDA_DEFAULT.items() if k != "bloques"}
    guardado = _cfg().get("tema_comanda") or {}
    for k in list(t):
        if k in guardado:
            t[k] = guardado[k]
    base = {b["id"]: b for b in TEMA_COMANDA_DEFAULT["bloques"]}
    guard_bloques = guardado.get("bloques") or TEMA_COMANDA_DEFAULT["bloques"]
    orden = [b["id"] for b in guard_bloques if b.get("id") in base]
    orden += [bid for bid in base if bid not in orden]
    by_id = {b.get("id"): b for b in guard_bloques}
    t["bloques"] = [{**base[bid], **{k: v for k, v in by_id.get(bid, {}).items() if k in _PROPS_BLOQUE}} for bid in orden]
    return t


def guardar_tema_comanda(tema):
    c = _cfg()
    c["tema_comanda"] = tema
    CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    return c


def _bloque_lineas(bid, p, ancho):
    """Lineas de texto de un bloque (sin estilo). 'ancho' es el ancho EFECTIVO en
    columnas (ya descontado si el bloque va en doble ancho), para alinear el total."""
    if bid == "header":
        return [p.get("_local", "Donde el Patata"),
                f"#{p.get('id', '?')} [{p.get('canal', '')}]  {_fecha_hora(p.get('creado_en'))}"]
    if bid == "tel":
        return [f"TEL: {p['telefono']}"] if p.get("telefono") else []
    if bid == "cliente":
        return [f"Cliente: {p.get('cliente') or '-'}"]
    if bid == "modo":
        modo = p.get("modalidad", "")
        if modo == "delivery":
            return ["DELIVERY:", p.get("direccion") or "(sin direccion)"]
        if modo == "local":
            return ["LOCAL" + (f"  Mesa {p['mesa']}" if p.get("mesa") else "")]
        return ["RETIRO"]
    if bid == "items":
        out = []
        for it in p.get("items", []):
            nombre = it.get("nombre_comanda") or it.get("producto", "?")
            base = f"{it.get('cantidad', 1)}x {nombre}"
            if it.get("variante"):
                base += f" {label_variante(it['variante'])}"
            if not it.get("ok"):
                base += "  /!\\ REVISAR"
            nota = it.get("notas", "")
            una = f"{base} ({nota})" if nota else base
            if nota and len(una) > ancho:
                out.append(base)
                out.append(f"  ({nota})")
            else:
                out.append(una)
            if it.get("componentes"):
                out.append("  incluye: " + ", ".join(it["componentes"]))
        if p.get("delivery_cobro"):
            out.append(_fila("+ delivery", fmt_pesos(p["delivery_cobro"]), ancho))
        if p.get("notas"):
            out.append(f"NOTA: {p['notas']}")
        return out
    if bid == "total":
        return [_fila("TOTAL", fmt_pesos(p.get("total", 0)), ancho)]
    if bid == "pago":
        out = list(_lineas_pago_txt(p))
        if p.get("pago_con") is not None:
            out.append(f"Paga con {fmt_pesos(p['pago_con'])}  Vuelto {fmt_pesos(p.get('vuelto', 0))}")
        return out
    if bid == "vuelto":
        if p.get("modalidad") == "delivery" and (p.get("vuelto") or 0) > 0:
            return [f"VUELTO {fmt_pesos(p['vuelto'])}"]
        return []
    return []


def _sep_bytes(tema, ancho, enc):
    o = bytearray()
    if tema.get("separador") == "guiones":
        o += enc("-" * ancho) + b"\n"
    elif tema.get("separador") == "blanco":
        o += b"\n"
    o += b"\n" * int(tema.get("sep", 0) or 0)
    return bytes(o)


def comanda_bytes(p, local="Donde el Patata", ancho=ANCHO_80MM, tema=None):
    """Comanda ESC/POS armada desde el TEMA (ver tema_comanda()). Si no se pasa tema,
    usa el guardado. Cada bloque emite su contenido con el estilo que el tema le da:
    tamano (n/doble-alto/doble-alto+ancho), negrita y alineacion, mas los globales."""
    tema = tema or tema_comanda()
    mayus = bool(tema.get("mayus", True))
    p = dict(p)
    p.setdefault("_local", local)

    def enc(s):
        return _enc(s, mayus)

    o = bytearray()
    o += INIT
    if tema.get("track"):
        o += ESC + b"\x20" + bytes([max(0, min(255, int(tema["track"])))])
    if tema.get("inter") and float(tema["inter"]) != 1.0:
        o += ESC + b"3" + bytes([max(0, min(255, round(30 * float(tema["inter"]))))])

    primero = True
    for b in tema.get("bloques", []):
        if not b.get("on"):
            continue
        bid = b.get("id")
        tam = b.get("tam", "n")
        on_, off_ = _TAM_ESC.get(tam, (b"", b""))
        ancho_ef = ancho // 2 if tam == "dd" else ancho

        if bid == "qr":
            if not p.get("telefono"):
                continue
            if not primero:
                o += _sep_bytes(tema, ancho, enc)
            primero = False
            o += ALIGN_C + enc("WHATSAPP DEL CLIENTE:") + b"\n"
            o += qr_bytes(f"https://wa.me/56{p['telefono']}") + b"\n" + ALIGN_L
            continue

        lineas = _bloque_lineas(bid, p, ancho_ef)
        if not lineas:
            continue
        if not primero:
            o += _sep_bytes(tema, ancho, enc)
        primero = False
        o += (ALIGN_C if b.get("al") == "c" else ALIGN_L)
        if b.get("bold"):
            o += BOLD_ON
        for ln in lineas:
            o += on_ + enc(ln) + b"\n" + off_
        if b.get("bold"):
            o += BOLD_OFF
        o += ALIGN_L

    o += b"\n" * 3 + CORTE
    return bytes(o)


def cierre_bytes(resumen, local="Donde el Patata", ancho=ANCHO_80MM):
    """Ticket del CIERRE DE CAJA (punto 12): fondo, ventas por metodo, esperado,
    contado y descuadre. El descuadre va bien grande (es lo primero que Christian
    necesita leer al cerrar el local)."""
    o = bytearray()
    o += INIT + ALIGN_C
    o += DOBLE_ON + BOLD_ON + _enc(local) + b"\n" + DOBLE_OFF + BOLD_OFF
    # Numero de caja + fecha/hora en UNA linea (mismo ahorro de espacio que en la comanda)
    fecha = _fecha_hora(resumen.get("cerrada_en") or resumen.get("abierta_en"))
    o += _enc(f"CIERRE DE CAJA #{resumen.get('caja_id', '?')}  {fecha}") + b"\n"
    o += ALIGN_L + _enc("-" * ancho) + b"\n"

    if resumen.get("cajero"):
        o += _enc(f"Cajero: {resumen['cajero']}") + b"\n"
    o += _enc(_fila("Pedidos", str(resumen.get("n_pedidos", 0)), ancho)) + b"\n"
    o += _enc("-" * ancho) + b"\n"
    o += BOLD_ON + _enc(_fila("TOTAL VENTAS", fmt_pesos(resumen.get("total_ventas", 0)), ancho)) + b"\n" + BOLD_OFF
    o += _enc("-" * ancho) + b"\n"

    # Cuadre por metodo de pago (regla E1, 02-jul): reemplaza el listado plano
    # "Ventas por metodo de pago" por un bloque propio de cada metodo. Efectivo es
    # el unico que se cuenta fisico (fondo + ventas = lo que deberia haber); tarjeta
    # y transferencia se concilian contra el resumen del posnet / la cartola del
    # banco, por eso alcanza con mostrar su venta del dia.
    por_metodo = resumen.get("por_metodo") or {}
    o += BOLD_ON + _enc("Cuadre efectivo") + b"\n" + BOLD_OFF
    o += _enc(_fila("  Fondo inicial", fmt_pesos(resumen.get("fondo_inicial", 0)), ancho)) + b"\n"
    o += _enc(_fila("  + Ventas efvo.", fmt_pesos(por_metodo.get("efectivo", 0)), ancho)) + b"\n"
    o += _enc(_fila("  = Deberia haber", fmt_pesos(resumen.get("esperado_efectivo", 0)), ancho)) + b"\n"
    contado_otros = resumen.get("contado_otros") or {}
    descuadre_otros = resumen.get("descuadre_otros") or {}
    for metodo, monto in por_metodo.items():
        if metodo == "efectivo":
            continue
        o += BOLD_ON + _enc(f"Cuadre {metodo}") + b"\n" + BOLD_OFF
        o += _enc(_fila(f"  Ventas {metodo}", fmt_pesos(monto), ancho)) + b"\n"
        # Ajuste por metodo (punto E2, 03-jul): si el cajero confirmo el monto contra
        # el posnet/la cartola al cerrar, se imprime junto con la diferencia.
        if metodo in contado_otros:
            o += _enc(_fila("  Confirmado", fmt_pesos(contado_otros[metodo]), ancho)) + b"\n"
            dif_m = descuadre_otros.get(metodo, 0)
            if dif_m == 0:
                o += _enc("  Cuadra") + b"\n"
            elif dif_m > 0:
                o += _enc(_fila("  Sobran", fmt_pesos(dif_m), ancho)) + b"\n"
            else:
                o += _enc(_fila("  Faltan", fmt_pesos(-dif_m), ancho)) + b"\n"
    o += _enc("-" * ancho) + b"\n"

    if resumen.get("contado_cierre") is not None:
        o += _enc(_fila("Efectivo contado", fmt_pesos(resumen["contado_cierre"]), ancho)) + b"\n"
        o += _enc("-" * ancho) + b"\n"
        descuadre = resumen.get("descuadre") or 0
        if descuadre == 0:
            texto_dif = "CAJA CUADRADA"
        elif descuadre > 0:
            texto_dif = f"SOBRAN {fmt_pesos(descuadre)}"
        else:
            texto_dif = f"FALTAN {fmt_pesos(-descuadre)}"
        o += ALIGN_C + BOLD_ON + DOBLE_ON + _enc(texto_dif) + b"\n" + DOBLE_OFF + BOLD_OFF + ALIGN_L
    o += b"\n" * 3 + CORTE
    return bytes(o)


def test_bytes(local="Donde el Patata", ancho=ANCHO_80MM):
    o = bytearray()
    o += INIT + ALIGN_C
    o += DOBLE_ON + BOLD_ON + _enc(local.upper()) + b"\n" + DOBLE_OFF + BOLD_OFF
    o += _enc("PRUEBA DE IMPRESION") + b"\n"
    o += _enc("La impresora esta lista") + b"\n"
    o += ALIGN_L + _enc("-" * ancho) + b"\n"
    o += _enc(_fila("Ejemplo item", fmt_pesos(8900), ancho)) + b"\n"
    o += b"\n" * 4 + CORTE
    return bytes(o)


# ---------------------------------------------------------------------------
# Envío RAW al spooler de Windows
# ---------------------------------------------------------------------------
def imprimir_raw(nombre, data):
    try:
        import win32print
    except ImportError:
        raise RuntimeError("Falta pywin32 (o no es Windows): no puedo imprimir directo.")
    if not nombre:
        raise RuntimeError("No hay impresora configurada.")
    h = win32print.OpenPrinter(nombre)
    try:
        win32print.StartDocPrinter(h, 1, ("Comanda POS Patata", None, "RAW"))
        win32print.StartPagePrinter(h)
        win32print.WritePrinter(h, data)
        win32print.EndPagePrinter(h)
        win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)
    return nombre


def imprimir_pedido(pedido, local="Donde el Patata"):
    nombre = impresora_actual()
    ancho = _cfg().get("ancho", ANCHO_80MM)
    return imprimir_raw(nombre, comanda_bytes(pedido, local, ancho))


def imprimir_test(local="Donde el Patata"):
    nombre = impresora_actual()
    ancho = _cfg().get("ancho", ANCHO_80MM)
    return imprimir_raw(nombre, test_bytes(local, ancho))


def imprimir_cierre(resumen, local="Donde el Patata"):
    nombre = impresora_actual()
    ancho = _cfg().get("ancho", ANCHO_80MM)
    return imprimir_raw(nombre, cierre_bytes(resumen, local, ancho))


# ---------------------------------------------------------------------------
# Probar sin impresora: python pos/impresora.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Impresoras detectadas:", listar_impresoras() or "(ninguna / sin pywin32)")
    print("Seleccionada:", impresora_actual())
    demo = {
        "id": 7, "canal": "cajero", "cliente": "Tamara Bravo", "telefono": "988378723",
        "modalidad": "delivery", "direccion": "Los Eucaliptus 5771",
        "creado_en": "2026-06-28T20:15:00",
        "total": 14400, "delivery_cobro": 1500,
        "pagos": [{"metodo": "efectivo", "monto": 9400}, {"metodo": "transferencia", "monto": 5000}],
        "notas": "casa esquina, cuidado con el perro",
        "items": [
            {"cantidad": 2, "producto": "Italiano", "nombre_comanda": "Churrasco Italiano",
             "subtotal": 8900, "ok": True, "notas": "Tomate/Palta; 1 con pan frio"},
            {"cantidad": 1, "producto": "Papas Fritas", "nombre_comanda": "Papas Fritas",
             "variante": "grande", "subtotal": 4000, "ok": True},
            {"cantidad": 1, "producto": "Lata CCU", "nombre_comanda": "Lata CCU",
             "subtotal": 1500, "ok": True, "notas": "Bilz"},
        ],
    }
    print("\n--- vista de la comanda (lo que recibe la térmica, sin comandos) ---")
    crudo = comanda_bytes(demo)
    texto = crudo.decode("cp437", "replace")
    visible = "".join(ch for ch in texto if ch == "\n" or ch.isprintable())
    print(visible)
    print(f"[{len(crudo)} bytes ESC/POS, corte incluido]")

    print("\n--- vista del CIERRE DE CAJA (punto 12) ---")
    resumen_demo = {
        "caja_id": 12, "cajero": "Christian", "abierta_en": "2026-06-30T12:00:00",
        "cerrada_en": "2026-06-30T23:45:00", "n_pedidos": 18, "total_ventas": 142300,
        "por_metodo": {"efectivo": 61000, "tarjeta debito": 55300, "transferencia": 26000},
        "fondo_inicial": 20000, "esperado_efectivo": 81000, "contado_cierre": 80500,
        "descuadre": -500,
        # Ajuste por metodo (punto E2, 03-jul): tarjeta confirmada exacta, transferencia con
        # $500 de menos (ej: una transferencia que no llego a la cuenta).
        "contado_otros": {"tarjeta debito": 55300, "transferencia": 25500},
        "descuadre_otros": {"tarjeta debito": 0, "transferencia": -500},
    }
    crudo_cierre = cierre_bytes(resumen_demo)
    texto_cierre = crudo_cierre.decode("cp437", "replace")
    print("".join(ch for ch in texto_cierre if ch == "\n" or ch.isprintable()))
    print(f"[{len(crudo_cierre)} bytes ESC/POS, corte incluido]")

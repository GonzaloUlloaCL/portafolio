#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sii.py — Lectura del Timbre Electrónico (TED) del SII de Chile + códigos de barra.
==================================================================================
Dos cosas que la recepción de mercadería necesita leer con la cámara:

1) EL TIMBRE SII (TED). Las Facturas y Guías de Despacho chilenas llevan un
   "timbre electrónico": un código PDF417 que, decodificado, es un XML `<TED>`
   con los datos oficiales del documento. Este módulo PARSEA ese string y
   devuelve lo que sirve para ingresar mercadería:
       RUT del proveedor, tipo y folio del documento, fecha, receptor y monto.

   HONESTIDAD TÉCNICA (importante):
   - El TED trae el MONTO TOTAL (`MNT`), NO el neto ni el IVA por separado. El
     neto/IVA se DERIVAN asumiendo IVA 19% para documentos afectos (factura 33,
     guía 52). Para exentos (34) no se calcula IVA. Es una aproximación honesta:
     el total es exacto; el desglose es estimado hasta cruzarlo con la factura.
   - El timbre es PDF417, no QR. La cámara del navegador (html5-qrcode) lee QR y
     códigos de barra 1D con soltura, pero PDF417 es poco confiable ahí. Por eso
     este parser acepta el string venga de donde venga (cámara, foto, pegado a
     mano) y no depende de una librería de escaneo en particular.

2) CÓDIGOS DE BARRA DE INSUMOS (EAN-13). Para identificar un insumo por su código
   de barra al recibirlo. Se valida el dígito verificador.

Sin dependencias externas: solo librería estándar.
Auto-test: `python wms/sii.py`
"""

import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

# Tipos de Documento Tributario Electrónico del SII (los que llegan a un local).
TIPOS_DTE = {
    "33": "Factura Electrónica",
    "34": "Factura Exenta Electrónica",
    "39": "Boleta Electrónica",
    "41": "Boleta Exenta Electrónica",
    "46": "Factura de Compra",
    "52": "Guía de Despacho Electrónica",
    "56": "Nota de Débito Electrónica",
    "61": "Nota de Crédito Electrónica",
}
# Documentos EXENTOS de IVA (no se les deriva IVA 19%).
TIPOS_EXENTOS = {"34", "41"}
IVA_CHILE = 0.19


# ----------------------------------------------------------------------
# RUT chileno
# ----------------------------------------------------------------------
def _dv_rut(cuerpo):
    """Dígito verificador de un RUT (módulo 11)."""
    s, mult = 0, 2
    for c in reversed(str(cuerpo)):
        s += int(c) * mult
        mult = 2 if mult == 7 else mult + 1
    resto = 11 - (s % 11)
    return "0" if resto == 11 else "K" if resto == 10 else str(resto)


def formatear_rut(rut):
    """'761920839' o '76192083-9' -> '76.192.083-9'. Devuelve el original si no parece RUT."""
    if not rut:
        return rut
    limpio = re.sub(r"[^0-9kK]", "", str(rut))
    if len(limpio) < 2:
        return rut
    cuerpo, dv = limpio[:-1], limpio[-1].upper()
    try:
        cuerpo_fmt = f"{int(cuerpo):,}".replace(",", ".")
    except ValueError:
        return rut
    return f"{cuerpo_fmt}-{dv}"


def rut_valido(rut):
    """True si el dígito verificador cuadra."""
    limpio = re.sub(r"[^0-9kK]", "", str(rut or ""))
    if len(limpio) < 2:
        return False
    cuerpo, dv = limpio[:-1], limpio[-1].upper()
    if not cuerpo.isdigit():
        return False
    return _dv_rut(cuerpo) == dv


# ----------------------------------------------------------------------
# EAN-13 (código de barra de insumos)
# ----------------------------------------------------------------------
def ean13_valido(codigo):
    """Valida un EAN-13 por su dígito verificador."""
    c = re.sub(r"\D", "", str(codigo or ""))
    if len(c) != 13:
        return False
    d = [int(x) for x in c]
    s = sum(v * (3 if i % 2 else 1) for i, v in enumerate(d[:12]))
    return (10 - s % 10) % 10 == d[12]


# ----------------------------------------------------------------------
# Timbre SII (TED)
# ----------------------------------------------------------------------
def _tags_locales(texto):
    """Parsea el XML y devuelve {tag_local: primer_texto} ignorando namespaces.
    Robusto: el TED puede venir suelto o dentro de un <DTE> con namespace del SII."""
    out = {}
    try:
        root = ET.fromstring(texto.strip())
    except ET.ParseError:
        return None
    for elem in root.iter():
        local = elem.tag.split("}")[-1]        # descarta el namespace {..}
        if local not in out and elem.text and elem.text.strip():
            out[local] = elem.text.strip()
    return out


def _campo_regex(texto, tag):
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", texto, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def parsear_ted(texto):
    """Lee el string del timbre SII y devuelve un dict listo para la recepción.

    Devuelve: {ok, rut_emisor, tipo_doc, tipo_doc_nombre, folio, fecha_doc,
               rut_receptor, receptor, total, neto, iva, exento, ted_raw, aviso}.
    Si no logra leer nada útil, ok=False con un 'motivo'."""
    if not texto or not str(texto).strip():
        return {"ok": False, "motivo": "vacío"}
    texto = str(texto).strip()

    campos = _tags_locales(texto) or {}

    def leer(tag):
        if campos.get(tag):
            return campos[tag]
        return _campo_regex(texto, tag)        # fallback si el XML venía roto

    rut_emisor = leer("RE")
    td = leer("TD")
    folio = leer("F")
    fecha = leer("FE")
    rut_receptor = leer("RR")
    receptor = leer("RSR")
    mnt = leer("MNT")

    if not any([rut_emisor, td, folio, mnt]):
        return {"ok": False, "motivo": "no_es_timbre_sii", "ted_raw": texto[:2000]}

    try:
        total = int(re.sub(r"\D", "", mnt)) if mnt else None
    except (TypeError, ValueError):
        total = None

    td = (td or "").strip()
    exento = td in TIPOS_EXENTOS
    neto = iva = None
    aviso = None
    if total is not None:
        if exento:
            neto, iva = total, 0
        else:
            neto = round(total / (1 + IVA_CHILE))
            iva = total - neto
            aviso = "neto/IVA derivados asumiendo IVA 19% (el TED solo trae el total)"

    return {
        "ok": True,
        "rut_emisor": formatear_rut(rut_emisor) if rut_emisor else None,
        "rut_emisor_valido": rut_valido(rut_emisor) if rut_emisor else None,
        "tipo_doc": td or None,
        "tipo_doc_nombre": TIPOS_DTE.get(td, f"Documento tipo {td}" if td else "Desconocido"),
        "folio": folio,
        "fecha_doc": fecha,
        "rut_receptor": formatear_rut(rut_receptor) if rut_receptor else None,
        "receptor": receptor,
        "total": total,
        "neto": neto,
        "iva": iva,
        "exento": exento,
        "ted_raw": texto[:2000],
        "aviso": aviso,
    }


def leer_captura(texto):
    """Punto de entrada único para la cámara: decide si lo escaneado es un timbre
    SII o un código de barra de insumo, y responde en consecuencia."""
    t = (texto or "").strip()
    if "<TED" in t or "<DD>" in t or "<DTE" in t:
        return {"clase": "sii", **parsear_ted(t)}
    solo_num = re.sub(r"\D", "", t)
    if len(solo_num) == 13:
        return {"clase": "ean13", "codigo": solo_num, "valido": ean13_valido(solo_num)}
    return {"clase": "desconocido", "texto": t[:200]}


def muestra_ted(rut_emisor="76192083-9", tipo="52", folio="4587", total=119000,
                fecha=None, receptor="Ulloa Y Silva Spa", rut_receptor="77441532-7"):
    """Genera un timbre SII de ejemplo (para demo/pruebas de la recepción)."""
    fecha = fecha or datetime.now().strftime("%Y-%m-%d")
    return (
        '<TED version="1.0"><DD>'
        f"<RE>{rut_emisor}</RE><TD>{tipo}</TD><F>{folio}</F><FE>{fecha}</FE>"
        f"<RR>{rut_receptor}</RR><RSR>{receptor}</RSR><MNT>{total}</MNT>"
        "<IT1>Mercaderia varios</IT1><CAF version=\"1.0\"><DA><RS>Proveedor Demo</RS>"
        "</DA></CAF><TSTED>2026-07-01T10:00:00</TSTED></DD>"
        "<FRMT algoritmo=\"SHA1withRSA\">ZmlybWFfZGVtbw==</FRMT></TED>"
    )


# ======================================================================
# Auto-test
# ======================================================================
def _auto_test():
    fallos = []

    def chk(nombre, cond):
        print(f"  [{'OK  ' if cond else 'FALLA'}] {nombre}")
        if not cond:
            fallos.append(nombre)

    print("- RUT chileno")
    chk("formatea RUT sin puntos", formatear_rut("761920839") == "76.192.083-9")
    chk("RUT valido detectado", rut_valido("76192083-9") is True)
    chk("RUT invalido detectado", rut_valido("76192083-5") is False)
    chk("DV con K", _dv_rut("6318909") == "K" or rut_valido("6318909-K"))

    print("- EAN-13")
    chk("EAN-13 valido", ean13_valido("4006381333931") is True)
    chk("EAN-13 invalido (checksum)", ean13_valido("4006381333930") is False)
    chk("EAN-13 largo incorrecto", ean13_valido("12345") is False)

    print("- Timbre SII: Guia de Despacho afecta (deriva IVA 19%)")
    ted = muestra_ted(tipo="52", folio="4587", total=119000)
    r = parsear_ted(ted)
    chk("parseo ok", r["ok"] is True)
    chk("RUT emisor formateado", r["rut_emisor"] == "76.192.083-9")
    chk("tipo doc nombre", r["tipo_doc_nombre"] == "Guía de Despacho Electrónica")
    chk("folio leido", r["folio"] == "4587")
    chk("total exacto", r["total"] == 119000)
    chk("neto derivado (119000/1.19=100000)", r["neto"] == 100000)
    chk("IVA derivado (19000)", r["iva"] == 19000)
    chk("aviso de derivacion presente", bool(r["aviso"]))

    print("- Timbre SII: Factura EXENTA (sin IVA)")
    r2 = parsear_ted(muestra_ted(tipo="34", total=50000))
    chk("marcado exento", r2["exento"] is True)
    chk("neto = total en exento", r2["neto"] == 50000)
    chk("IVA = 0 en exento", r2["iva"] == 0)

    print("- Robustez del parser")
    chk("string vacio", parsear_ted("")["ok"] is False)
    chk("string basura no es timbre", parsear_ted("hola mundo")["ok"] is False)
    # XML roto pero con los tags -> fallback regex
    roto = "<TED><DD><RE>76192083-9</RE><TD>33</TD><F>99</F><MNT>11900</MNT>"
    rr = parsear_ted(roto)
    chk("fallback regex sobre XML roto", rr["ok"] is True and rr["folio"] == "99")

    print("- Router de captura (SII vs EAN-13 vs desconocido)")
    chk("detecta timbre SII", leer_captura(ted)["clase"] == "sii")
    chk("detecta EAN-13", leer_captura("4006381333931")["clase"] == "ean13")
    chk("detecta desconocido", leer_captura("cualquier cosa")["clase"] == "desconocido")

    print()
    print(f"RESULTADO: {'TODO OK' if not fallos else str(len(fallos)) + ' FALLA(S) -> ' + str(fallos)}")
    return not fallos


if __name__ == "__main__":
    sys.exit(0 if _auto_test() else 1)

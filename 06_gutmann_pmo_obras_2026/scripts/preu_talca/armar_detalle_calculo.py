"""PDF con el cálculo del presupuesto de Talca recinto por recinto: cada partida con su fórmula escrita con las cotas,
y el recorte del plano con lo que se midió marcado encima (tabiques que se anotan en el recinto, ventanas, puertas y
la superficie).

Uso: python -I armar_detalle_calculo.py <plano.pdf> <Presupuesto_Talca.xlsx> <carpeta_trabajo> <salida.pdf>
Las cantidades y los P.U. se leen del Excel (son fórmulas aritméticas con las cotas); la geometría de sitio.py se
verificó encima del plano y contra las cadenas de cotas. Recortes a 110-150 dpi por zona, nunca la lámina entera.
"""
import os
import re
import sys

import openpyxl
import pymupdf
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sitio as S  # noqa: E402

PLANO, EXCEL, TRABAJO, SALIDA = sys.argv[1:5]
os.makedirs(TRABAJO, exist_ok=True)
pdfmetrics.registerFont(TTFont("Dv", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DvB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("Dv", normal="Dv", bold="DvB", italic="Dv", boldItalic="DvB")

ANCHO, ALTO, MARGEN = 842, 595, 28
AZUL, GRIS, ROJO = HexColor("#1F3A5F"), HexColor("#6B6B6B"), HexColor("#C62828")
FONDO = HexColor("#EAF1F8")
C_TAB, C_VEN, C_PTA, C_SUP = Color(0.12, 0.45, 0.85, 0.55), Color(0.1, 0.65, 0.3, 0.75), Color(0.95, 0.55, 0.0, 0.8), Color(0.85, 0.15, 0.15, 1)
CUERPO = ParagraphStyle("c", fontName="Dv", fontSize=8.4, leading=10.8)
CH = ParagraphStyle("ch", fontName="Dv", fontSize=7.4, leading=9.2)
CHB = ParagraphStyle("chb", parent=CH, fontName="DvB")
SUBT = ParagraphStyle("s", fontName="DvB", fontSize=10.5, leading=13, textColor=AZUL)
K = 56.69   # pt del plano por metro
plano = pymupdf.open(PLANO)[0]


def pesos(v):
    return "$" + f"{round(v):,}".replace(",", ".")


def n2(v, d=2):
    return f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def ev(v):
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).lstrip("=")
    if not re.fullmatch(r"[0-9.+\-*/() ]+", s):
        raise SystemExit(f"Fórmula no aritmética: {v!r}")
    return eval(s, {"__builtins__": {}})


def legible(v):
    """«=(3.89+3+4.24)*3» → «(3,89 + 3,00 + 4,24) × 3,00»."""
    if not isinstance(v, str):
        return n2(v) if isinstance(v, float) else str(v)
    s = v.lstrip("=")
    s = re.sub(r"\d+(?:\.\d+)?", lambda m: n2(float(m.group())), s)
    return s.replace("*", " × ").replace("+", " + ").replace("-", " − ")


# ------------------------------------------------------------------ datos del Excel
ws = openpyxl.load_workbook(EXCEL)["Presupuesto"]
SECT, PART = {}, []
for r in ws.iter_rows(min_row=8, values_only=True):
    a, b = r[0], str(r[1] or "")
    m = re.match(r"^([A-E]\.\d+) (.+)$", b)
    if not a and m:
        SECT[m.group(1)] = m.group(2)
    if isinstance(a, str) and a.count(".") == 2:
        q, pu = ev(r[3]), ev(r[4])
        PART.append(dict(c=a, d=b, u=r[2], f=r[3], q=q, pu=pu, t=(q or 0) * (pu or 0), o=r[6] or ""))
POR = {}
for p in PART:
    POR.setdefault(p["c"].rsplit(".", 1)[0], []).append(p)
CD = sum(p["t"] for p in PART)
ZONAS = {"A": "Obras preliminares", "B": "Oficinas y Guardar", "C": "Salas de capacitación", "D": "Recepción y hall",
         "E": "Generales"}
RECINTO = {r[6]: r for r in S.RECINTOS}

c = canvas.Canvas(SALIDA, pagesize=(ANCHO, ALTO))
c.setTitle("Presupuesto Talca: cálculo recinto por recinto")
c.setAuthor("Alma, para Gonza")
pag = [0]


def nueva(titulo, sub):
    if pag[0]:
        c.showPage()
    pag[0] += 1
    c.setFillColor(AZUL); c.setFont("DvB", 16); c.drawString(MARGEN, ALTO - MARGEN - 12, titulo)
    c.setFillColor(GRIS); c.setFont("Dv", 9); c.drawString(MARGEN, ALTO - MARGEN - 27, sub)
    c.setFont("Dv", 7.5)
    c.drawString(MARGEN, 14, "Presupuesto Habilitación Preuniversitario Talca · cálculo recinto por recinto · plano v3, "
                 "1:50 · Excel Presupuesto_Talca.xlsx (17-10-2026)")
    c.drawRightString(ANCHO - MARGEN, 14, f"página {pag[0]}")
    c.setFillColor(HexColor("#000000"))


def tabla(filas, anchos, x, ytop, cabeza=True, total=None):
    st = [("FONT", (0, 0), (-1, -1), "Dv", 7.4), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LINEBELOW", (0, 0), (-1, -1), 0.3, HexColor("#D0D0D0")), ("LEFTPADDING", (0, 0), (-1, -1), 3),
          ("RIGHTPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if cabeza:
        st += [("BACKGROUND", (0, 0), (-1, 0), AZUL), ("TEXTCOLOR", (0, 0), (-1, 0), white), ("FONT", (0, 0), (-1, 0), "DvB", 7.4)]
    if total:
        st += [("FONT", (0, -1), (-1, -1), "DvB", 7.6), ("BACKGROUND", (0, -1), (-1, -1), FONDO)]
    t = Table(filas, colWidths=anchos, repeatRows=1 if cabeza else 0)
    t.setStyle(TableStyle(st))
    _, h = t.wrap(sum(anchos), 2000)
    t.drawOn(c, x, ytop - h)
    return h


def recorte(nombre, caja_m, dpi):
    x0, y0, x1, y1 = caja_m
    ruta = os.path.join(TRABAJO, nombre + ".png")
    plano.get_pixmap(dpi=dpi, clip=pymupdf.Rect(x0 * K, y0 * K, x1 * K, y1 * K)).save(ruta)
    return ruta


def dibujar_plano(sec, caja_m, x, y, w, h, dpi=130):
    """Recorte del plano con lo medido para el sector: superficie (rojo), tabiques anotados aquí (azul), ventanas (verde)
    y puertas descontadas (naranjo)."""
    x0, y0, x1, y1 = caja_m
    s = min(w / (x1 - x0), h / (y1 - y0))
    iw, ih = (x1 - x0) * s, (y1 - y0) * s
    ox, oy = x + (w - iw) / 2, y + (h - ih) / 2
    c.drawImage(recorte("rec_" + sec.replace(".", "_"), caja_m, dpi), ox, oy, iw, ih)
    c.setStrokeColor(HexColor("#B0B0B0")); c.setLineWidth(0.5); c.rect(ox, oy, iw, ih)
    P = lambda mx, my: (ox + (mx - x0) * s, oy + ih - (my - y0) * s)
    c.saveState()
    pth = c.beginPath(); pth.rect(ox, oy, iw, ih); c.clipPath(pth, stroke=0)

    def rect(a, b, d, e, col, fill=True):
        (px, py), (qx, qy) = P(a, e), P(b, d)
        if fill:
            c.setFillColor(col); c.rect(px, py, max(1.2, qx - px), max(1.2, qy - py), stroke=0, fill=1)
        else:
            c.setStrokeColor(col); c.setLineWidth(1.4); c.setDash(4, 2); c.rect(px, py, qx - px, qy - py, stroke=1, fill=0); c.setDash()
    for m in S.MUROS:
        if m[4] == sec:
            rect(*m[:4], C_TAB)
    if sec.startswith("B"):
        (a, b), = S.MURO_INCLINADO
        cortes = S.CORTES_INCLINADO + [(b[1], None)]
        for i in range(len(cortes) - 1):
            if cortes[i][1] == sec:
                ya, yb = cortes[i][0], cortes[i + 1][0]
                xa = a[0] + (b[0] - a[0]) * (ya - a[1]) / (b[1] - a[1]); xb = a[0] + (b[0] - a[0]) * (yb - a[1]) / (b[1] - a[1])
                c.setStrokeColor(C_TAB); c.setLineWidth(max(1.5, 0.12 * s)); c.line(*P(xa, ya), *P(xb, yb))
    for v in S.VENTANAS:
        if v[4] == sec:
            rect(*v[:4], C_VEN)
    for p in S.PUERTAS:
        if p[4] == sec:
            rect(*p[:4], C_PTA)
    if sec in RECINTO:
        n, sup, a, b, d, e, _ = RECINTO[sec]
        rect(a, min(b, S.x_fachada((d + e) / 2)), d, e, C_SUP, fill=False)
        if sec == "C.1":
            rect(*S.SALA01_ALTA, C_SUP, fill=False)
    c.restoreState()
    return ih


def leyenda(x, y):
    items = [(C_TAB, "Tabique que se anota en este recinto"), (C_VEN, "Ventana: suma al tabique y va en «Ventanas»"),
             (C_PTA, "Puerta: se descuenta del tabique"), (C_SUP, "Superficie del recinto (rótulo)")]
    for i, (col, t) in enumerate(items):
        yy = y - i * 11
        if col is C_SUP:
            c.setStrokeColor(col); c.setDash(3, 2); c.rect(x, yy, 14, 7, stroke=1, fill=0); c.setDash()
        else:
            c.setFillColor(col); c.rect(x, yy, 14, 7, stroke=0, fill=1)
        c.setFillColor(HexColor("#000000")); c.setFont("Dv", 7.2); c.drawString(x + 19, yy + 1, t)


# ------------------------------------------------------------------ 1. resumen
nueva("Presupuesto Talca: el cálculo recinto por recinto",
      "Cada partida con su fórmula escrita con las cotas del plano, y el plano marcado con lo que se midió · preparado por "
      "Alma para Gonza, 17-10-2026")
tot_z = {}
for p in PART:
    tot_z[p["c"][0]] = tot_z.get(p["c"][0], 0) + p["t"]
filas = [["Zona", "Costo directo", "%"]] + [[f"{k} {ZONAS[k]}", pesos(v), n2(v / CD * 100, 1) + " %"] for k, v in tot_z.items()]
filas += [["Costo directo", pesos(CD), "100 %"], ["Gastos generales 5 %", pesos(CD * 0.05), ""], ["Utilidades 15 %", pesos(CD * 0.15), ""],
          ["Subtotal neto", pesos(CD * 1.2), ""], ["IVA 19 %", pesos(CD * 1.2 * 0.19), ""], ["TOTAL", pesos(CD * 1.2 * 1.19), ""]]
tabla(filas, [190, 100, 50], MARGEN, ALTO - MARGEN - 46, total=True)
reglas = [
    "<b>Fuente.</b> Plano «Habilitacion - Preuniversitario Talca v3», escala 1:50. Los tabiques se leyeron de los vectores "
    "del plano, se dibujaron encima y se cuadraron contra las cadenas de cotas (tramos + vanos = total).",
    "<b>Tabiques.</b> Todos nuevos, «Tabiquería interior», a 3,00 m de alto (criterio de Luciano). Se recorren los "
    "recintos desde arriba a la izquierda hacia abajo a la derecha: el tabique que comparten dos recintos se anota en el "
    "primero. Se descuentan las puertas y no las ventanas. Donde un pilar corta el tabique, el tramo del pilar no se cuenta.",
    "<b>Pisos, cielos y pintura de cielo.</b> La superficie útil rotulada en el plano. El hall no tiene rótulo: 216,39 m² "
    "a escala (planta 680,20 m² menos los 13 recintos, los tabiques y los pilares), con la Recepción adentro.",
    "<b>Guardapolvo.</b> Perímetro interior menos la puerta; en los recintos con un lado inclinado se suman los dos anchos. "
    "Un pilar que entra al recinto por un lado suma sus dos caras. <b>Pintura de muros:</b> ese perímetro × 3,00.",
    "<b>Ventanas.</b> Ancho del plano × 1,70 (alto de Luciano). <b>P.U.</b> De San Miguel por nombre de partida; la puerta "
    "doble de la entrada, de San Bernardo (750.000).",
    "<b>Fuera del total</b> (hoja «Para Luciano»): baños (el plano no trae), mesones de la Recepción y del pilar del hall, "
    "marcos de dos pilares y techumbre.",
]
y = ALTO - MARGEN - 46
for t in reglas:
    p = Paragraph(t, CUERPO); _, h = p.wrap(410, 400); p.drawOn(c, 404, y - h); y -= h + 6
leyenda(404, y - 14)
c.setFillColor(AZUL); c.setFont("DvB", 10.5); c.drawString(MARGEN, 196, "Recintos y páginas")
idx = [["Página", "Sector", "Recinto", "Costo directo"]]
orden = ["A.1"] + [k for k in POR if k[0] in "BCD"] + ["E.1"]
for i, k in enumerate(orden):
    nm = SECT.get(k, "").replace("Sector ", ""); idx.append([str(i + 2), k, nm[:1].upper() + nm[1:40], pesos(sum(p["t"] for p in POR[k]))])
tabla(idx[:10], [36, 34, 160, 70], MARGEN, 188)
tabla([idx[0]] + idx[10:], [36, 34, 190, 70], MARGEN + 320, 188) if len(idx) > 10 else None

# ------------------------------------------------------------------ 2. una página por sector
CAJA = {"A.1": (8.8, 1.2, 40.6, 28.8), "E.1": (8.8, 1.2, 40.6, 28.8), "D.1": (13.0, 7.0, 30.6, 28.8)}


def caja_de(sec):
    if sec in CAJA:
        return CAJA[sec]
    n, sup, a, b, d, e, _ = RECINTO[sec]
    if sec == "C.1":
        d = S.SALA01_ALTA[2]
    b = min(b, S.x_fachada(d)) + 0.6
    return (a - 1.4, d - 1.4, b + 1.0, e + 1.4)


for k in orden:
    ps = POR[k]
    nombre = SECT.get(k, k).replace("Sector ", "")
    sub = sum(p["t"] for p in ps)
    extra = ""
    if k in RECINTO:
        extra = f" · superficie útil {RECINTO[k][1]} m²"
    nueva(f"{k} · {nombre[0].upper() + nombre[1:]}", f"{ZONAS[k[0]]}{extra} · subtotal {pesos(sub)}")
    # plano a la izquierda
    pw, ph = 330, 440
    dibujar_plano(k, caja_de(k), MARGEN, ALTO - MARGEN - 40 - ph, pw, ph, dpi=110 if k in ("A.1", "E.1", "D.1") else 150)
    leyenda(MARGEN, ALTO - MARGEN - 40 - ph - 16)
    # tabla de cálculo a la derecha
    filas = [["Código", "Partida", "Cálculo", "Cantidad", "P.U.", "Total"]]
    for p in ps:
        filas.append([p["c"], Paragraph(p["d"], CH), Paragraph(legible(p["f"]) + (" " + p["u"] if p["u"] else ""), CH),
                      n2(p["q"]) if isinstance(p["q"], float) else str(p["q"]), pesos(p["pu"]), pesos(p["t"])])
    filas.append(["", Paragraph("<b>Subtotal</b>", CH), "", "", "", pesos(sub)])
    x = MARGEN + pw + 14
    anchos = [34, 92, 158, 44, 52, 62]
    h = tabla(filas, anchos, x, ALTO - MARGEN - 40, total=True)
    # observaciones (cómo se midió)
    y = ALTO - MARGEN - 40 - h - 10
    c.setFillColor(AZUL); c.setFont("DvB", 9); c.drawString(x, y, "Cómo se midió"); y -= 4
    for p in ps:
        if not p["o"] or p["o"].startswith("Igual que"):
            continue
        par = Paragraph(f"<b>{p['c']}</b> {p['o']}", CH)
        _, hh = par.wrap(sum(anchos), 400)
        if y - hh < 26:
            break
        par.drawOn(c, x, y - hh); y -= hh + 3

c.save()
print(f"{SALIDA}: {pag[0]} páginas · costo directo {pesos(CD)}")

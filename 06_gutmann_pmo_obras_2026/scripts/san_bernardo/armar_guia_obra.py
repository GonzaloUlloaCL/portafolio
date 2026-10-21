"""Guía en PDF para entender la ampliación San Bernardo: cómo se construye y cómo leer su plano, zona por zona.

Uso: python -I armar_guia_obra.py <plano.pdf> <carpeta_trabajo> <salida.pdf>
Las coordenadas de recortes y números se escriben en píxeles de la vista del plano de 1932 px de lado (la misma
con que se revisó el plano) y se pasan a puntos de la página rotada con P = 0,8835 (1701 pt / 1932 px · 2599/2599).
Memoria: los recortes van a 150-300 dpi sobre zonas chicas; nunca rasterizar la página entera a 300 dpi (el
14-oct una imagen de 7.088 px de lado tumbó la sesión).
"""
import os
import sys

import pymupdf
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable, Paragraph, Table, TableStyle

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from partidas import GG, IVA, UTILIDAD, totales  # noqa: E402

PLANO, TRABAJO, SALIDA = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(TRABAJO, exist_ok=True)

pdfmetrics.registerFont(TTFont("Dv", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DvB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("Dv", normal="Dv", bold="DvB", italic="Dv", boldItalic="DvB")

ANCHO, ALTO = 842, 595  # A4 apaisado
MARGEN = 28
ROJO, AZUL, GRIS = HexColor("#C62828"), HexColor("#1F3A5F"), HexColor("#6B6B6B")
FONDO_CUENTA, FONDO_PRUEBA, FONDO_OJO = HexColor("#EAF1F8"), HexColor("#FFF4CC"), HexColor("#FBE3E3")

CUERPO = ParagraphStyle("cuerpo", fontName="Dv", fontSize=8.6, leading=11.2, alignment=TA_LEFT)
CUERPO_CH = ParagraphStyle("cuerpo_ch", parent=CUERPO, fontSize=8, leading=10.2)
SUBT = ParagraphStyle("subt", fontName="DvB", fontSize=10.5, leading=13, textColor=AZUL, spaceAfter=2)
TIT_CAJA = ParagraphStyle("tit_caja", fontName="DvB", fontSize=9, leading=11.5, textColor=AZUL)

P = 0.8835  # px de la vista de 1932 -> pt del plano
plano = pymupdf.open(PLANO)
pagina_plano = plano[0]


def pesos(v):
    return f"${v:,.0f}".replace(",", ".")


def pt(*v):
    return tuple(round(x * P, 1) for x in v)


def recorte(nombre, clip, dpi):
    ruta = os.path.join(TRABAJO, f"{nombre}.png")
    pix = pagina_plano.get_pixmap(dpi=dpi, clip=pymupdf.Rect(*clip))
    pix.save(ruta)
    return ruta


def num(n):
    return f'<font backColor="#C62828" color="#FFFFFF"><b>&nbsp;{n}&nbsp;</b></font>'


def tabla_caja(titulo, texto, fondo, ancho):
    t = Table([[Paragraph(titulo, TIT_CAJA)], [Paragraph(texto, CUERPO)]], colWidths=[ancho])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fondo), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (0, 0), 5),
                           ("BOTTOMPADDING", (0, -1), (-1, -1), 6), ("TOPPADDING", (0, 1), (-1, 1), 1)]))
    return t


class caja(Flowable):
    """Caja de color que toma el ancho de la columna donde cae."""

    def __init__(self, titulo, texto, fondo):
        super().__init__()
        self.titulo, self.texto, self.fondo, self._t = titulo, texto, fondo, None

    def wrap(self, w, h):
        self._t = tabla_caja(self.titulo, self.texto, self.fondo, w)
        self.width, self.height = self._t.wrap(w, h)
        return self.width, self.height

    def draw(self):
        self._t.drawOn(self.canv, 0, 0)


def pie(c, n):
    c.setFont("Dv", 7.5)
    c.setFillColor(GRIS)
    c.drawString(MARGEN, 14, "Guía de la obra · Ampliación San Bernardo, Covadonga 139 · plano «Ampliacion Obra Menor 2 - "
                 "Covadonga 139» v3, planta 1:50")
    c.drawRightString(ANCHO - MARGEN, 14, f"página {n}")
    c.setFillColor(HexColor("#000000"))


def marcador(c, x, y, n, r=8):
    c.setFillColor(ROJO)
    c.setStrokeColor(white)
    c.setLineWidth(1)
    c.circle(x, y, r, stroke=1, fill=1)
    c.setFillColor(white)
    c.setFont("DvB", 9 if n < 10 else 8)
    c.drawCentredString(x, y - 3.2, str(n))


def imagen_con_numeros(c, png, clip, x, y, w, h, marcas, r=8):
    """Dibuja el recorte y encima los números. Cada marca es (n, x, y) o (n, x, y, x_objetivo, y_objetivo), en px."""
    c.drawImage(png, x, y, w, h)
    c.setStrokeColor(HexColor("#B0B0B0"))
    c.setLineWidth(0.6)
    c.rect(x, y, w, h, stroke=1, fill=0)
    s = w / (clip[2] - clip[0])

    def aca(px, py):
        return x + (px * P - clip[0]) * s, y + h - (py * P - clip[1]) * s

    for m in marcas:
        mx, my = aca(m[1], m[2])
        if len(m) == 5:
            tx, ty = aca(m[3], m[4])
            c.setStrokeColor(ROJO)
            c.setLineWidth(1.1)
            c.line(mx, my, tx, ty)
            c.setFillColor(ROJO)
            c.circle(tx, ty, 1.8, stroke=0, fill=1)
        marcador(c, mx, my, m[0], r)


def empacar(unidades, columnas):
    """Reparte flowables en columnas [(x, y_arriba, ancho, alto)]. Devuelve posiciones o None si no cabe."""
    pos, k = [], 0
    if not columnas:
        return None
    x, ytop, w, h = columnas[0]
    usado = 0
    for u in unidades:
        _, uh = u.wrap(w, 10_000)
        sep = 5 if usado else 0
        while usado + sep + uh > h:
            k += 1
            if k >= len(columnas):
                return None
            x, ytop, w, h = columnas[k]
            usado, sep = 0, 0
            _, uh = u.wrap(w, 10_000)
        pos.append((u, x, ytop - usado - sep - uh, w))
        usado += sep + uh
    return pos


def dibujar(ubic):
    for u, ux, uy, uw in ubic:
        u.wrap(uw, 10_000)
        u.drawOn(c, ux, uy)


# ------------------------------------------------------------------------------------- contenido
ZONAS = [
    dict(
        n=1, titulo="Salas de Capacitación 02 y 03", donde="Abajo del plano: el bloque sur.",
        clip=pt(290, 1365, 1480, 1860), dpi=170, disposicion="arriba",
        marcas=[(1, 355, 1800), (2, 380, 1826), (3, 1080, 1625, 1040, 1585), (4, 1010, 1700, 1010, 1746),
                (5, 820, 1445, 820, 1405), (6, 912, 1455, 912, 1412), (7, 800, 1680, 773, 1680),
                (8, 450, 1790, 450, 1768)],
        items=[
            ("Cadena de recintos", "0,12 · 6,00 · 0,12 · 8,48 · 0,12: muro, Sala 03 (6,00 m), muro, Sala 02 (8,48 m), "
             "muro. Los números chicos son espesores de muro (12 cm de tabique)."),
            ("Cadena de vanos", "1,10 · 1,80 · 0,30 · 1,80 · 4,00 · 2,00 · 0,60 · 2,00 · 1,40: tramos de muro y ventanas. "
             "La Sala 03 tiene 2 ventanas de 1,80 y la Sala 02, 2 de 2,00."),
            ("Rótulo", "Nombre y «Sup. útil»: la superficie interior. Sala 02: 42,40 m²."),
            ("Ventana", "Donde el achurado rojo se corta y queda una línea doble delgada. El alto (0,60) lo da el correo: "
             "horizontales, proyectantes, de aluminio anodizado."),
            ("Puerta doble", "Dos arcos = dos hojas. El vano mide 1,40 (cota de arriba). Vale 750.000."),
            ("Vidrio fijo", "El 0,60 junto a cada puerta doble: un vidrio fijo de 1,50 de alto para ver la sala desde el "
             "corredor."),
            ("Muro entre salas", "Es tabique interior: metalcon con yeso cartón por las dos caras, sin el revestimiento "
             "metálico de los muros de afuera."),
            ("Proyección del alero", "La línea segmentada es el borde del techo visto desde arriba: sobresale 0,40 m del "
             "muro. Va en techumbre, no en el piso."),
        ],
        cuenta="Sala 03: 6,00 × 5,00 = 30,00 m² ✓ (rótulo 30,00).<br/>Sala 02: 8,48 × 5,00 = 42,40 m² ✓.<br/>"
               "Ventanas Sala 02: 2 × 2,00 × 0,60 + 0,60 × 1,50 = 3,30 m².<br/>Pintura de muros Sala 02: perímetro "
               "2 × (8,48 + 5,00) = 26,96 m × 2,40 de alto = 64,70 m².",
        prueba="¿Cuántos m² de ventana tiene la Sala 03?",
        respuesta="3,06 m²: 2 × 1,80 × 0,60 + el vidrio fijo de 0,60 × 1,50.",
    ),
    dict(
        n=2, titulo="Sala de Capacitación 01 y Guardar", donde="A la derecha del plano: el bloque oriente.",
        clip=pt(950, 430, 1480, 1420), dpi=170, disposicion="lado", ancho_img=330,
        marcas=[(1, 1160, 1165, 1160, 1098), (2, 1440, 1110, 1395, 1085), (3, 1270, 940, 1330, 940),
                (4, 1060, 810, 1000, 810), (5, 1060, 905, 995, 905), (6, 1235, 715, 1190, 690),
                (7, 1240, 810, 1190, 775), (8, 1450, 740, 1395, 700)],
        items=[
            ("Rótulo", "Sala 01, Sup. útil 50,00 m²."),
            ("Cotas de la sala", "10,00 de largo (cota vertical de la derecha) y 5,00 de ancho (abajo)."),
            ("Ventanas", "La cadena 2,00 · 0,60 · 2,00 · 0,60 · 2,00 del muro oriente: 3 ventanas de 2,00."),
            ("Puerta doble", "Vano de 1,40, abre hacia el corredor."),
            ("Vidrio fijo", "0,60 × 1,50 junto a la puerta."),
            ("Guardar", "Bodega rectangular, Sup. útil 9,80 m². Al norte tiene los baños."),
            ("Equipo AC", "Rectángulo segmentado: aire acondicionado. Hay 3 en el plano y no están en el presupuesto."),
            ("Cadena de la derecha", "2,48 · 0,12 · 1,96: baños, muro y Guardar; 20,00 es el largo total del bloque "
             "oriente, por fuera."),
        ],
        cuenta="Sala 01: 5,00 × 10,00 = 50,00 m² ✓.<br/>Guardar: 5,00 × 1,96 = 9,80 m² ✓.<br/>Perímetro del "
               "Guardar: 2 × (5,00 + 1,96) = 13,92 m.",
        prueba="¿Cuántos m² de ventana tiene la Sala 01?",
        respuesta="4,50 m²: 3 × 2,00 × 0,60 + 0,60 × 1,50.",
    ),
    dict(
        n=3, titulo="Baños 01 y 02", donde="Arriba a la derecha, entre el corredor norte y el Guardar.",
        clip=pt(945, 412, 1420, 700), dpi=300, disposicion="lado", ancho_img=None,
        marcas=[(1, 1265, 690, 1230, 648), (2, 1060, 470, 1035, 512), (3, 1100, 625, 1117, 590),
                (4, 1250, 470, 1290, 530), (5, 1165, 450, 1135, 482), (6, 985, 610, 1045, 590),
                (7, 960, 445, 1018, 493), (8, 1390, 455, 1335, 505)],
        items=[
            ("Cadena de abajo", "0,20 · 1,50 · 0,12 · 3,38: muro, Baño 02 (1,50 de ancho), muro y Baño 01 (3,38)."),
            ("Cadena del Baño 02", "0,20 · 0,75 · 0,75: muro, puerta de 0,75 y muro. Separa el Baño 02 del vestíbulo."),
            ("Lavamanos", "Los 3 semicírculos del Baño 01, contra su muro poniente."),
            ("WC y cubículos", "Los 3 óvalos del Baño 01, cada uno en su cubículo. El plano no da la altura de las "
             "divisiones: se supuso 2,00."),
            ("Puerta del Baño 01", "Arco de 0,75 desde el vestíbulo."),
            ("Baño 02", "1 WC y 1 lavamanos. Su fondo (~1,30) no tiene cota: sale de 2,48 − 1,06 − 0,12."),
            ("Vestíbulo", "La entrada de 0,80 desde el corredor, sin puerta dibujada. De ahí se pasa a los dos baños."),
            ("Ventanas", "0,11 · 0,60 · 0,23 · 0,60: dos ventanas de 0,60 en el muro oriente del Baño 01. El Baño 02 "
             "tiene otra de 0,60 hacia el corredor."),
        ],
        cuenta="Baño 01: 3,38 × 2,48 = 8,38 m². Baño 02: 1,50 × 1,30 = 1,95 m². Vestíbulo: 1,50 × 1,06 = 1,59 m². "
               "Total 11,92 m² (no traen rótulo).<br/>Porcelanato de muro: perímetros 2 × (3,38 + 2,48) + 2 × (1,50 + "
               "1,30) = 17,32 m × 2,40 = 41,57 m².<br/>Artefactos: 4 WC + 4 lavamanos = 8 unidades, más 4 griferías y "
               "4 espejos.",
        prueba="¿Cuántos artefactos sanitarios hay que comprar para los dos baños?",
        respuesta="8: 4 WC y 4 lavamanos (3 + 3 en el Baño 01 y 1 + 1 en el Baño 02). Las griferías y los espejos van "
                  "en partidas aparte.",
    ),
    dict(
        n=4, titulo="Corredor cubierto, pilares y patio", donde="Al centro y a la izquierda del plano.",
        clip=pt(240, 590, 1010, 1420), dpi=170, disposicion="lado", ancho_img=440,
        marcas=[(1, 900, 905, 852, 940), (2, 790, 1030, 836, 1030), (3, 352, 1185, 365, 1210),
                (4, 310, 860, 282, 860), (5, 560, 870, 560, 905), (6, 945, 1050, 910, 965),
                (7, 262, 1300, 280, 1330), (8, 470, 1360, 470, 1320)],
        items=[
            ("Pilar de acero", "Cada cuadradito rojo de 0,10 es un pilar de 100 × 100 × 3. Son 10, cada uno sobre un dado "
             "de hormigón de 50 × 50 × 50."),
            ("Perfil de acero", "«proy. perfil acero 150*50*3 rect.»: la viga que une los pilares y recibe las cerchas. "
             "El correo dice 100 × 50 × 3: hay que confirmarlo con Luciano."),
            ("Cadena de pilares", "0,10 · 2,40 · 0,10 · 2,40 · 0,10 · 2,40 · 0,10 · 2,00: pilar, luz, pilar... El último "
             "2,00 es el ancho del corredor."),
            ("Cadena del patio", "2,30 es el corredor norte; luego 0,10 · 2,45 cuatro veces (pilares y luces). La cota "
             "total 10,10 no cuadra con la suma (10,30): se usó 10,10."),
            ("Patio descubierto", "7,50 × 10,10, sin techo. Piso antideslizante."),
            ("Corredor cubierto", "Techado pero abierto, para circular con lluvia y dar sombra en la tarde. N.T.N. +0,05 "
             "es el nivel del terreno hoy."),
            ("Ancho del corredor sur", "2,00."),
            ("Círculo Ø1,50", "Giro de una silla de ruedas: accesibilidad universal. No se construye; se respeta."),
        ],
        cuenta="Corredor: norte 6,28 × 2,30 + oriente 2,10 × 10,10 + sur 9,60 × 2,00 = 54,85 m².<br/>Patio: 7,50 × 10,10 "
               "= 75,75 m².<br/>Vigas: ejes entre pilares 4,18 + 10,21 + 7,50 + 2,10 = 24,0 m × 6,78 kg/m = 163 kg.<br/>"
               "Pilares: 10 × 2,40 m × 9,14 kg/m = 219 kg.",
        prueba="Si el patio midiera 10,30 en vez de 10,10, ¿cuántos m² más de piso habría?",
        respuesta="1,5 m²: 7,50 × 0,20.",
    ),
    dict(
        n=5, titulo="Araucaria y unión con el edificio existente", donde="Arriba a la izquierda del plano.",
        clip=pt(300, 280, 1000, 700), dpi=190, disposicion="lado", ancho_img=470,
        marcas=[(1, 470, 520, 465, 560), (2, 700, 425, 700, 452), (3, 600, 540, 615, 500),
                (4, 455, 330, 455, 350), (5, 600, 412, 638, 372), (6, 790, 560, 768, 550), (7, 860, 545, 830, 530),
                (8, 650, 690, 650, 668)],
        items=[
            ("Árbol araucaria", "El círculo grande es la copa. La araucaria no se tala; el árbol que se tala es otro "
             "(A.1.6)."),
            ("Borde de lo existente", "La línea gris es el edificio que ya está. Todo lo gris se conserva."),
            ("Puerta a lo existente", "Une el corredor norte con el pasillo de la Kitchenette. Ya existe: no se "
             "presupuesta."),
            ("Kitchenette", "Recinto existente, en gris: no se toca."),
            ("N.P.T. +0,10", "Nivel de piso terminado del edificio existente. El piso nuevo debe quedar igual, sin "
             "rampas."),
            ("N.T.N. +0,05", "Nivel del terreno natural hoy: 5 cm más abajo que el piso existente."),
            ("Corredor norte", "2,30 de ancho entre el edificio existente y la viga."),
            ("Cadena del corredor norte", "0,10 · 1,99 · 0,10 · 1,99 · 0,10 · 2,00: pilares y luces del borde norte."),
        ],
        cuenta="Rebaje: el piso queda a +0,10 y debajo van 0,36 m de capas, así que el fondo queda a +0,10 − 0,36 = "
               "−0,26. El terreno está a +0,05: se rebajan 0,05 + 0,26 = 0,31 m.<br/>Volumen: 285,71 m² × 0,31 = "
               "88,57 m³.",
        prueba="¿Por qué no se puede dejar el piso nuevo 5 cm más bajo, al nivel del terreno?",
        respuesta="Porque el correo pide que calce con el piso existente (+0,10) por accesibilidad universal: no hay "
                  "espacio para rampas.",
    ),
]

SIMBOLOS = [
    ("Muro nuevo", pt(680, 1730, 960, 1762), "Achurado rojo: se construye completo.",
     "Tabique metalcon: m² = largo × 2,40."),
    ("Existente", pt(360, 130, 720, 450), "Línea gris sin achurado: lo que ya está.", "No se presupuesta."),
    ("Cadena de cotas", pt(360, 1790, 790, 1832), "Medidas en metros, una tras otra.",
     "De aquí salen largos y anchos."),
    ("Rótulo de recinto", pt(975, 1543, 1115, 1585), "Nombre y «Sup. útil»: la superficie interior.",
     "Va directo a piso, cielo y techumbre."),
    ("Puerta doble", pt(770, 1360, 885, 1420), "Dos arcos: dos hojas.", "Se cuenta: 750.000 cada una."),
    ("Ventana", pt(940, 1736, 1085, 1760), "El achurado se corta y queda una línea doble.",
     "Ancho del plano × alto del correo (0,60)."),
    ("Pilar y viga de acero", pt(680, 1245, 870, 1282), "Cuadradito de 0,10 y la nota del perfil.",
     "Pilares por unidad; vigas en kg."),
    ("Proyección del alero", pt(470, 1220, 670, 1250), "Línea segmentada: el borde del techo.",
     "Va en aleros (m²), no en el piso."),
    ("N.T.N. / N.P.T.", pt(588, 1312, 650, 1345), "Nivel del terreno / del piso terminado, en metros.",
     "Las diferencias piden rebaje o relleno."),
    ("Círculo Ø1,50", pt(852, 1265, 975, 1385), "Giro de silla de ruedas.", "No se construye ni se cubica."),
    ("Equipo AC", pt(1290, 1500, 1345, 1600), "Rectángulo segmentado con su nota.",
     "Aire acondicionado: fuera de este presupuesto."),
    ("Artefactos", None, "WC (óvalo) y lavamanos (semicírculo).", "Se cuentan uno por uno."),
    ("Árbol", pt(340, 440, 590, 690), "Círculo grande con nombre: la copa.", "Si se tala, va por unidad."),
]
ARTEFACTOS = [("WC", pt(1043, 578, 1088, 616)), ("lavamanos", pt(1103, 506, 1142, 552))]

# ------------------------------------------------------------------------------------- páginas
c = canvas.Canvas(SALIDA, pagesize=(ANCHO, ALTO))
c.setTitle("Guía de la obra · Ampliación San Bernardo")
c.setAuthor("Alma, para Gonza")
pagina = 0
avisos = []


def nueva_pagina():
    global pagina
    if pagina:
        c.showPage()
    pagina += 1


def titulo_pagina(t, sub=None):
    c.setFillColor(AZUL)
    c.setFont("DvB", 17)
    c.drawString(MARGEN, ALTO - MARGEN - 14, t)
    if sub:
        c.setFillColor(GRIS)
        c.setFont("Dv", 9.5)
        c.drawString(MARGEN, ALTO - MARGEN - 30, sub)
    c.setFillColor(HexColor("#000000"))


CLIP_MAPA = pt(240, 30, 1500, 1880)
mapa_png = recorte("mapa", CLIP_MAPA, 50)


def mapa(x, y, w, destacar=None):
    h = w * (CLIP_MAPA[3] - CLIP_MAPA[1]) / (CLIP_MAPA[2] - CLIP_MAPA[0])
    c.drawImage(mapa_png, x, y, w, h)
    c.setStrokeColor(HexColor("#B0B0B0"))
    c.setLineWidth(0.5)
    c.rect(x, y, w, h)
    s = w / (CLIP_MAPA[2] - CLIP_MAPA[0])
    for z in ZONAS:
        if destacar is not None and z["n"] != destacar:
            continue
        x0, y0, x1, y1 = z["clip"]
        x0, x1 = max(x0, CLIP_MAPA[0]), min(x1, CLIP_MAPA[2])
        c.setStrokeColor(ROJO)
        c.setLineWidth(1.4 if destacar else 1.1)
        c.rect(x + (x0 - CLIP_MAPA[0]) * s, y + h - (y1 - CLIP_MAPA[1]) * s, (x1 - x0) * s, (y1 - y0) * s,
               stroke=1, fill=0)
        if destacar is None:
            marcador(c, x + (x0 - CLIP_MAPA[0]) * s + 9, y + h - (y0 - CLIP_MAPA[1]) * s - 9, z["n"], r=7.5)
    return h


# 1. Portada
nueva_pagina()
titulo_pagina("La ampliación San Bernardo, explicada",
              "Qué se construye, cómo se construye y cómo leer su plano · preparada por Alma para Gonza, 14-10-2026")
col_w = 395
_, cd, neto, iva, total = totales()
bloques = [
    Paragraph("Qué es la obra", SUBT),
    Paragraph("Una ampliación de un piso pegada a un edificio existente: <b>3 salas de capacitación</b> (50,00, 42,40 y "
              "30,00 m²), un <b>Guardar</b> (bodega, 9,80 m²) y <b>dos baños</b> (uno con 3 WC y otro con 1), unidos por un "
              "<b>corredor cubierto</b> de estructura de acero que rodea un <b>patio descubierto</b>. Se tala un árbol; "
              "la araucaria no se tala.", CUERPO),
    Paragraph("Con qué se construye (correo del arquitecto)", SUBT),
    Paragraph("• Piso: radier de hormigón H25 de 10 cm con malla, sobre gravilla y base estabilizada, al mismo nivel "
              "del piso existente.<br/>• Muros: tabiques de metalcon (acero liviano) de 2,40 de alto, con yeso cartón "
              "por dentro y plancha metálica por fuera.<br/>• Techo: cerchas de metalcon cada 0,80 y cielo de yeso "
              "cartón.<br/>• Corredor: pilares y vigas de acero sobre dados de hormigón.<br/>• Electricidad: la ve un "
              "especialista (no está en este presupuesto).", CUERPO),
    Paragraph("Tres reglas de este plano", SUBT),
    Paragraph("1. Las medidas están en metros y usan punto: <b>8.48</b> son 8,48 m.<br/>2. <b>Rojo achurado = obra "
              "nueva. Gris = existente</b>, no se toca.<br/>3. La <b>Sup. útil</b> rotulada es la superficie interior: "
              "sirve para comprobar tus cuentas.", CUERPO),
    caja("El número de hoy", f"Costo directo {pesos(cd)} · con gastos generales {GG:.0%}, utilidades {UTILIDAD:.0%} e "
         f"IVA {IVA:.0%}: <b>{pesos(total)}</b>. El detalle está en Presupuesto_San_Bernardo.xlsx y lo que falta "
         "confirmar, en su hoja «Para Luciano».", FONDO_CUENTA),
    Paragraph("Cómo usar esta guía", SUBT),
    Paragraph("Página 2: cómo se construye, capa por capa. Páginas 3 y 4: los símbolos. Páginas 5 a 9: el plano zona "
              "por zona, con la cuenta y una pregunta para practicar. Página 10: del plano al presupuesto y las "
              "respuestas.", CUERPO),
]
ubic = empacar(bloques, [(MARGEN, ALTO - MARGEN - 48, col_w, 500)])
if ubic:
    dibujar(ubic)
else:
    avisos.append("la portada no cabe")
mx = MARGEN + col_w + 22
alto_mapa = ALTO - MARGEN - 52 - 40
mw = alto_mapa * (CLIP_MAPA[2] - CLIP_MAPA[0]) / (CLIP_MAPA[3] - CLIP_MAPA[1])
mx += (ANCHO - MARGEN - mx - mw) / 2
mapa(mx, ALTO - MARGEN - 48 - alto_mapa, mw)
c.setFont("Dv", 8)
c.setFillColor(GRIS)
c.drawCentredString(mx + mw / 2, ALTO - MARGEN - 60 - alto_mapa, "Las 5 zonas de la guía sobre el plano.")
y_ley = ubic[-1][2] - 26 if ubic else 200
c.setFillColor(AZUL)
c.setFont("DvB", 10.5)
c.drawString(MARGEN, y_ley, "Las 5 zonas")
for k, z in enumerate(ZONAS):
    ly = y_ley - 18 - k * 16
    marcador(c, MARGEN + 6, ly + 3, z["n"], r=6.5)
    c.setFillColor(HexColor("#000000"))
    c.setFont("Dv", 8.6)
    c.drawString(MARGEN + 17, ly, f"{z['titulo']} (página {k + 5})")
pie(c, pagina)

# 2. Cómo se construye
nueva_pagina()
titulo_pagina("Cómo se construye, capa por capa", "Dibujos esquemáticos, sin escala. Las medidas salen del correo del "
              "arquitecto y del plano.")


def capa(x, y, w, h, color, texto, derecha=None):
    c.setFillColor(HexColor(color))
    c.setStrokeColor(HexColor("#555555"))
    c.setLineWidth(0.5)
    c.rect(x, y, w, h, stroke=1, fill=1)
    c.setFillColor(HexColor("#000000"))
    c.setFont("Dv", 8)
    c.drawString(x + w + 8, y + h / 2 - 3, texto)
    if derecha:
        c.setFillColor(AZUL)
        c.setFont("DvB", 8)
        c.drawRightString(x - 8, y + h / 2 - 3, derecha)
        c.setFillColor(HexColor("#000000"))


# 2a. Corte del piso
x0, ytop = MARGEN + 70, ALTO - MARGEN - 70
c.setFont("DvB", 10.5)
c.setFillColor(AZUL)
c.drawString(MARGEN, ytop + 8, "1. El piso: se rebaja el terreno y se arma un paquete de 0,36 m")
esc = 300  # pt por metro (vertical)
w = 170
niveles = [("+0,10", 0.01, "#D9C7A7", "Porcelanato (N.P.T. +0,10)"),
           ("", 0.10, "#BDBDBD", "Radier H25, 10 cm, malla C-92"),
           ("", 0.10, "#E0E0E0", "Gravilla, 10 cm"),
           ("", 0.15, "#C9B48A", "Base estabilizada, 15 cm"),
           ("−0,26", 0, None, "")]
y = ytop - 14
for etq, e, color, texto in niveles:
    if e:
        capa(x0, y - e * esc, w, max(e * esc, 3), color, texto, etq or None)
        y -= max(e * esc, 3)
    else:
        c.setFillColor(AZUL)
        c.setFont("DvB", 8)
        c.drawRightString(x0 - 8, y - 3, etq)
# terreno natural y rebaje
y_ntn = ytop - 14 - (0.10 - 0.05) * esc
c.setStrokeColor(HexColor("#8D6E63"))
c.setDash(4, 3)
c.line(x0 - 50, y_ntn, x0 + w + 20, y_ntn)
c.setDash()
c.setFillColor(HexColor("#8D6E63"))
c.setFont("Dv", 7.5)
c.drawRightString(x0 - 8, y_ntn - 10, "terreno hoy +0,05")
c.setStrokeColor(ROJO)
c.setLineWidth(1)
xr = x0 + w - 18
c.line(xr, y_ntn, xr, y)
c.line(xr - 3, y_ntn, xr + 3, y_ntn)
c.line(xr - 3, y, xr + 3, y)
c.setFillColor(ROJO)
c.setFont("DvB", 8)
c.drawRightString(xr - 4, (y_ntn + y) / 2 - 3, "rebaje 0,31 m")
c.setFillColor(HexColor("#000000"))

c.setFont("Dv", 7.5)
c.setFillColor(GRIS)
c.drawString(x0, y - 14, "El radier lleva 1% de pendiente hacia el poniente para evacuar las aguas lluvia.")
c.setFillColor(HexColor("#000000"))

# 2b. Muro perimetral (vista en planta del espesor)
yb = y - 48
c.setFont("DvB", 10.5)
c.setFillColor(AZUL)
c.drawString(MARGEN, yb, "2. El muro de afuera, de adentro hacia afuera")
capas_muro = [("Yeso cartón ST 15 mm", "#F5F5F5", 10), ("Metalcon 90 mm (montantes)", "#90A4AE", 40),
              ("OSB o terciado 15 mm", "#D7B98E", 10), ("Omega de metalcon (solera)", "#B0BEC5", 10),
              ("Plancha metálica, igual a lo existente", "#78909C", 6)]
xm, ym = MARGEN + 10, yb - 30
for i, (t, col, ww) in enumerate(capas_muro):
    c.setFillColor(HexColor(col))
    c.setStrokeColor(HexColor("#555555"))
    c.rect(xm, ym - 60, ww, 60, stroke=1, fill=1)
    c.setFillColor(HexColor("#000000"))
    c.setFont("DvB", 7)
    c.drawCentredString(xm + ww / 2, ym + 4, str(i + 1))
    xm += ww
lx = MARGEN + 110
for i, (t, _, _) in enumerate(capas_muro):
    c.setFillColor(HexColor("#000000"))
    c.setFont("Dv", 8)
    c.drawString(lx, ym - 8 - i * 12, f"{i + 1}. {t}")
c.setFont("Dv", 7.5)
c.setFillColor(GRIS)
c.drawString(MARGEN + 10, ym - 72, "adentro ←   → afuera")
c.drawString(MARGEN, ym - 88, "Los tabiques interiores llevan solo 1 y 2, con yeso cartón por las dos caras.")

# columna derecha: techo, corredor y orden de obra
xd = ANCHO / 2 + 10
c.setFont("DvB", 10.5)
c.setFillColor(AZUL)
c.drawString(xd, ytop + 8, "3. El techo: cerchas de metalcon cada 0,80")
bx, by, bw = xd + 20, ytop - 95, 230
c.setStrokeColor(HexColor("#455A64"))
c.setLineWidth(1.4)
alto_alto, alto_bajo = 1.0 * 70, 0.2 * 70
c.line(bx, by, bx + bw, by)                       # cuerda inferior (cielo)
c.line(bx, by, bx, by + alto_alto)                # lado alto
c.line(bx + bw, by, bx + bw, by + alto_bajo)      # lado bajo
c.line(bx, by + alto_alto, bx + bw, by + alto_bajo)  # cuerda superior (caída)
c.setLineWidth(0.7)
for k in range(1, 6):
    xx = bx + bw * k / 6
    yy = by + alto_alto - (alto_alto - alto_bajo) * k / 6
    c.line(xx, by, xx, yy)
    c.line(bx + bw * (k - 1) / 6, by + alto_alto - (alto_alto - alto_bajo) * (k - 1) / 6, xx, by)
c.setFont("Dv", 8)
c.setFillColor(HexColor("#000000"))
c.drawRightString(bx - 5, by + alto_alto / 2, "1,00 m")
c.drawString(bx + bw + 5, by + 3, "0,20 m (tapacán y canal)")
c.drawString(bx + 30, by - 12, "cielo de yeso cartón 10 mm")
c.setFillColor(ROJO)
c.drawString(bx + bw - 70, by + alto_alto - 10, "→ el agua cae hacia afuera")
c.setFillColor(HexColor("#000000"))

orden = [
    ("A", "Preliminares", "faena, empalmes, tala de un árbol, trazado y rebaje de 0,31 m."),
    ("D.1", "Base", "estabilizado, gravilla y radier de toda la superficie (285,71 m²) con bomba."),
    ("C.1", "Acero", "dados de hormigón, 10 pilares y vigas del corredor."),
    ("B", "Recintos", "tabiques, techumbre, cielo, ventanas, puertas, piso y pintura, sala por sala."),
    ("C.2-C.3", "Corredor y patio", "techumbre, aleros y piso antideslizante."),
    ("D.2-D.3", "Cierre", "aguas lluvia, agua potable, alcantarillado y aseo."),
]
caja_orden = [Paragraph("4. En qué orden se construye (y dónde está en el Excel)", SUBT)]
caja_orden += [Paragraph(f"{num(i)}&nbsp; <b>{t}</b> ({cod}): {d}", CUERPO) for i, (cod, t, d) in enumerate(orden, 1)]
caja_orden.append(caja("La araucaria", "No se tala. El correo pide no pasar instalaciones bajo tierra cerca de "
                       "ella (D.2).", FONDO_OJO))
ubic = empacar(caja_orden, [(xd, by - 30, ANCHO - MARGEN - xd, by - 30 - 30)])
if ubic:
    dibujar(ubic)
else:
    avisos.append("página 2: el orden de obra no cabe")
pie(c, pagina)

# 3-4. Símbolos
TIT_CARTA = ParagraphStyle("tit_carta", fontName="DvB", fontSize=10, leading=12, textColor=AZUL)
for parte in range(2):
    nueva_pagina()
    titulo_pagina("Los símbolos del plano" + (" (sigue)" if parte else ""),
                  "Cada ejemplo está recortado de este mismo plano. «Para el presupuesto» dice qué hacer con lo que ves.")
    cw, gap = (ANCHO - 2 * MARGEN - 3 * 12) / 4, 12
    top = ALTO - MARGEN - 46
    chh = (top - 34 - gap) / 2
    for i, (nombre, clip, que, presu) in enumerate(SIMBOLOS[parte * 8:(parte + 1) * 8]):
        fila, col = divmod(i, 4)
        x = MARGEN + col * (cw + gap)
        ytop_ = top - fila * (chh + gap)
        c.setStrokeColor(HexColor("#D0D0D0"))
        c.setLineWidth(0.7)
        c.roundRect(x, ytop_ - chh, cw, chh, 5, stroke=1, fill=0)
        p = Paragraph(nombre, TIT_CARTA)
        p.wrap(cw - 16, 50)
        p.drawOn(c, x + 8, ytop_ - 20)
        caja_w, caja_h = cw - 16, 128
        caja_y = ytop_ - 26 - caja_h
        c.setFillColor(HexColor("#F6F6F6"))
        c.rect(x + 8, caja_y, caja_w, caja_h, stroke=0, fill=1)
        piezas = [(None, clip)] if clip else ARTEFACTOS
        sub_w = (caja_w - 4) / len(piezas)
        for k, (etq, aclip) in enumerate(piezas):
            png = recorte(f"simbolo_{parte}_{i}_{k}", aclip, 300)
            cwp, chp = aclip[2] - aclip[0], aclip[3] - aclip[1]
            alto_disp = caja_h - (22 if etq else 8)
            e = min((sub_w - 8) / cwp, alto_disp / chp)
            iw, ih = cwp * e, chp * e
            bx_ = x + 8 + 2 + k * sub_w
            c.drawImage(png, bx_ + (sub_w - iw) / 2, caja_y + (16 if etq else 4) + (alto_disp - ih) / 2, iw, ih)
            if etq:
                c.setFont("Dv", 7.5)
                c.setFillColor(GRIS)
                c.drawCentredString(bx_ + sub_w / 2, caja_y + 5, etq)
        textos = [Paragraph(f"<b>Qué es:</b> {que}", CUERPO_CH),
                  Paragraph(f"<font color='#1F3A5F'><b>Para el presupuesto:</b> {presu}</font>", CUERPO_CH)]
        ubic = empacar(textos, [(x + 8, caja_y - 6, cw - 16, caja_y - 6 - (ytop_ - chh) - 4)])
        if not ubic:
            avisos.append(f"símbolo «{nombre}»: el texto no cabe en la tarjeta")
            continue
        dibujar(ubic)
    pie(c, pagina)

# 5-9. Zonas
for z in ZONAS:
    nueva_pagina()
    titulo_pagina(f"Zona {z['n']} · {z['titulo']}", z["donde"])
    loc_w = 80
    mapa_h = loc_w * (CLIP_MAPA[3] - CLIP_MAPA[1]) / (CLIP_MAPA[2] - CLIP_MAPA[0])
    mapa(ANCHO - MARGEN - loc_w, ALTO - MARGEN - mapa_h, loc_w, destacar=z["n"])
    png = recorte(f"zona{z['n']}", z["clip"], z["dpi"])
    cw_, ch_ = z["clip"][2] - z["clip"][0], z["clip"][3] - z["clip"][1]
    arriba = ALTO - MARGEN - 44
    abajo = 30
    alto_util = arriba - abajo
    ancho_util = ANCHO - 2 * MARGEN - loc_w - 10

    def unidades(ancho_caja):
        u = [Paragraph("Qué estás viendo", SUBT)]
        u += [Paragraph(f"{num(k)}&nbsp; <b>{t}.</b> {d}", CUERPO) for k, (t, d) in enumerate(z["items"], 1)]
        u += [caja("La cuenta", z["cuenta"], FONDO_CUENTA),
              caja("Prueba tú", z["prueba"] + " <font color='#6B6B6B'>(Respuesta al final de la guía.)</font>",
                   FONDO_PRUEBA)]
        return u

    colocado = None
    for intento in range(30):
        reduccion = 0.95 ** intento
        if z["disposicion"] == "arriba":
            iw = (ANCHO - 2 * MARGEN - loc_w - 10) * reduccion
            ih = iw * ch_ / cw_
            ix, iy = MARGEN, arriba - ih
            col_w3 = (ANCHO - 2 * MARGEN - 2 * 14) / 3
            alto_txt = iy - 12 - abajo
            colocado = empacar(unidades(col_w3),
                               [(MARGEN + k * (col_w3 + 14), iy - 12, col_w3, alto_txt) for k in range(3)])
        else:
            ih = alto_util * reduccion
            iw = ih * cw_ / ch_
            if z.get("ancho_img") and iw > z["ancho_img"] * reduccion:
                iw = z["ancho_img"] * reduccion
                ih = iw * ch_ / cw_
            ix, iy = MARGEN, arriba - ih
            x_txt = ix + iw + 16
            w_txt = ANCHO - MARGEN - x_txt - loc_w - 10
            opciones = [[(x_txt, arriba, w_txt, alto_util)]]
            if w_txt > 400:
                cw2 = (w_txt - 14) / 2
                opciones.append([(x_txt + k * (cw2 + 14), arriba, cw2, alto_util) for k in range(2)])
            for cols in opciones:
                colocado = empacar(unidades(cols[0][2]), cols)
                if colocado:
                    break
        if colocado:
            break
    if not colocado:
        avisos.append(f"zona {z['n']}: el texto no cabe ni achicando la imagen")
        continue
    imagen_con_numeros(c, png, z["clip"], ix, iy, iw, ih, z["marcas"])
    dibujar(colocado)
    z["escala"] = iw / cw_
    pie(c, pagina)

# 10. Del plano al presupuesto
nueva_pagina()
titulo_pagina("Del plano al presupuesto", "Cuánto pesa cada capítulo, la receta que se usó y las respuestas de «Prueba tú».")
por_sec, cd, neto, iva, total = totales()
nombres = {"A": "Obras preliminares", "B": "Recintos (3 salas, Guardar y baños)", "C": "Corredor cubierto y patio",
           "D": "Base, radier, instalaciones y aseo"}


filas = [[Paragraph("<b>Capítulo</b>", CUERPO), Paragraph("<b>Monto</b>", CUERPO), Paragraph("<b>%</b>", CUERPO)]]
filas += [[Paragraph(f"{k}. {nombres[k]}", CUERPO), Paragraph(pesos(v), CUERPO), Paragraph(f"{v / cd:.0%}", CUERPO)]
          for k, v in por_sec.items()]
filas += [[Paragraph("<b>Costo directo</b>", CUERPO), Paragraph(f"<b>{pesos(cd)}</b>", CUERPO), ""],
          [Paragraph(f"Gastos generales {GG:.0%} + utilidades {UTILIDAD:.0%}", CUERPO), Paragraph(pesos(neto - cd), CUERPO), ""],
          [Paragraph(f"IVA {IVA:.0%}", CUERPO), Paragraph(pesos(iva), CUERPO), ""],
          [Paragraph("<b>Total</b>", CUERPO), Paragraph(f"<b>{pesos(total)}</b>", CUERPO), ""]]
colw = (ANCHO - 2 * MARGEN - 24) / 2
tabla_m = Table(filas, colWidths=[colw - 140, 95, 45])
tabla_m.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, HexColor("#C8C8C8")),
                             ("BACKGROUND", (0, 0), (-1, 0), HexColor("#EDEDED")),
                             ("BACKGROUND", (0, 5), (-1, 5), FONDO_CUENTA),
                             ("BACKGROUND", (0, 8), (-1, 8), FONDO_CUENTA)]))
pasos = [
    ("Ubica el recinto", "por su rótulo: nombre y Sup. útil."),
    ("Busca sus cotas:", "la cadena que lo cruza de lado a lado y la de arriba abajo."),
    ("Multiplica y compara", "con la Sup. útil. Aquí las 3 salas y el Guardar calzan exacto."),
    ("Saca el perímetro", "para guardapolvo y pintura; los muros de afuera y de adentro van por separado."),
    ("Cuenta", "puertas, ventanas, artefactos y pilares, uno por uno."),
    ("Lee el correo:", "alturas y materiales que la planta no trae (2,40 de alto, capas del piso, perfiles)."),
]
izq = [Paragraph("Cuánto pesa cada capítulo", SUBT), tabla_m,
       Paragraph("Lo que más pesa son los recintos (B): tabiques, techumbre y piso de 144,12 m² interiores.", CUERPO),
       Paragraph("La receta en 6 pasos", SUBT)]
izq += [Paragraph(f"{num(k)}&nbsp; <b>{t}</b> {d}", CUERPO) for k, (t, d) in enumerate(pasos, 1)]
der = [Paragraph("Respuestas de «Prueba tú»", SUBT)]
der += [Paragraph(f"{num(z['n'])}&nbsp; <b>Zona {z['n']}.</b> {z['prueba']}<br/>"
                  f"<font color='#1F3A5F'>→ {z['respuesta']}</font>", CUERPO) for z in ZONAS]
der.append(caja("Antes de ofertar", "La hoja «Para Luciano» del Excel tiene las preguntas vigentes. Las que más mueven "
                "el número: las partidas fuera del presupuesto (electricidad y aire acondicionado), el valor de aguas "
                "lluvia y el perfil de las vigas.", FONDO_OJO))
for lista, x in ((izq, MARGEN), (der, MARGEN + colw + 24)):
    ubic = empacar(lista, [(x, ALTO - MARGEN - 46, colw, ALTO - MARGEN - 46 - 30)])
    if not ubic:
        avisos.append("la página final no cabe")
        continue
    dibujar(ubic)
pie(c, pagina)
c.save()

print("guardado:", SALIDA, "· páginas:", pagina)
for z in ZONAS:
    print(f"  zona {z['n']}: escala de la imagen {z.get('escala', 0):.2f}")
for a in avisos:
    print("  AVISO:", a)
sys.exit(1 if avisos else 0)

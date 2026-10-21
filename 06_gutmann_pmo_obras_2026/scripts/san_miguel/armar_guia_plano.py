"""Guía en PDF para aprender a leer el plano San Miguel v5, zona por zona.

Uso: python -I armar_guia_plano.py <plano.pdf> <carpeta_trabajo> <salida.pdf>
Las coordenadas de los recortes y de los números están en puntos del plano tal como se ve
(página rotada), las mismas que usa get_pixmap(clip=...).
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

PLANO, TRABAJO, SALIDA = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(TRABAJO, exist_ok=True)

pdfmetrics.registerFont(TTFont("Dv", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DvB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("Dv", normal="Dv", bold="DvB", italic="Dv", boldItalic="DvB")

ANCHO, ALTO = 842, 595  # A4 apaisado
MARGEN = 28
ROJO, AZUL, GRIS = HexColor("#C62828"), HexColor("#1F3A5F"), HexColor("#6B6B6B")
FONDO_CUENTA, FONDO_PRUEBA = HexColor("#EAF1F8"), HexColor("#FFF4CC")

CUERPO = ParagraphStyle("cuerpo", fontName="Dv", fontSize=8.6, leading=11.2, alignment=TA_LEFT)
CUERPO_CH = ParagraphStyle("cuerpo_ch", parent=CUERPO, fontSize=8, leading=10.2)
SUBT = ParagraphStyle("subt", fontName="DvB", fontSize=10.5, leading=13, textColor=AZUL, spaceAfter=2)
TIT_CAJA = ParagraphStyle("tit_caja", fontName="DvB", fontSize=9, leading=11.5, textColor=AZUL)

plano = pymupdf.open(PLANO)
pagina_plano = plano[0]


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

    def __init__(self, titulo, texto, fondo, _ancho=None):
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
    c.drawString(MARGEN, 14, "Guía de lectura del plano · Sede Preuniversitario San Miguel · plano San Miguel v5, piso 1")
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
    """Dibuja el recorte en (x, y, w, h) y encima los números, en puntos del plano.

    Cada marca es (n, ax, ay[, tx, ty]) en posición fija, o un dict que la pega a un rótulo sin taparlo:
    {"n": 1, "izq": (x0, yc)} · {"der": (x1, yc)} · {"arriba": (xc, y0)} · {"abajo": (xc, y1)}.
    """
    c.drawImage(png, x, y, w, h)
    c.setStrokeColor(HexColor("#B0B0B0"))
    c.setLineWidth(0.6)
    c.rect(x, y, w, h, stroke=1, fill=0)
    s = w / (clip[2] - clip[0])
    hueco = (r + 2.5) / s  # en puntos del plano, para que el número no tape el rótulo

    def aca(px, py):
        return x + (px - clip[0]) * s, y + h - (py - clip[1]) * s

    for m in marcas:
        if isinstance(m, dict):
            n, objetivo = m["n"], m.get("a")
            if "izq" in m:
                ax, ay = m["izq"][0] - hueco, m["izq"][1]
            elif "der" in m:
                ax, ay = m["der"][0] + hueco, m["der"][1]
            elif "arriba" in m:
                ax, ay = m["arriba"][0], m["arriba"][1] - hueco
            else:
                ax, ay = m["abajo"][0], m["abajo"][1] + hueco
            m = (n, ax, ay, *objetivo) if objetivo else (n, ax, ay)
        n, ax, ay = m[:3]
        mx, my = aca(ax, ay)
        if len(m) == 5:
            tx, ty = aca(m[3], m[4])
            c.setStrokeColor(ROJO)
            c.setLineWidth(1.1)
            c.line(mx, my, tx, ty)
            c.setFillColor(ROJO)
            c.circle(tx, ty, 1.8, stroke=0, fill=1)
        marcador(c, mx, my, n)


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


# ------------------------------------------------------------------------------------- contenido
ZONAS = [
    dict(
        n=1, titulo="Sala 01, B. Mujeres 2 y B. Universal", donde="Arriba a la izquierda del plano.",
        clip=(520, 150, 1560, 460), dpi=220, disposicion="arriba",
        marcas=[{"n": 1, "izq": (866, 192.5)}, {"n": 2, "izq": (822, 212)}, (3, 700, 262, 700, 231),
                {"n": 4, "izq": (834, 314)}, (5, 612, 349, 575, 349), (6, 1118, 425, 1169, 425),
                {"n": 7, "izq": (1337, 358)}, {"n": 8, "izq": (1475, 433)}],
        items=[
            ("Cadena de cotas", "Se lee de izquierda a derecha: 0,15 · 10,50 · 0,15 · 4,45 · 0,15 · 1,60 · 0,12. Los "
             "números chicos son espesores de muro; los grandes, el ancho de cada recinto. La cota de más arriba (15,40) "
             "suma varios tramos."),
            ("Nota escrita", "«muro de albañilería - h = 3,50 m»: el muro del fondo es de albañilería y mide 3,50 m de "
             "alto. Es de las pocas alturas que trae esta planta."),
            ("Muro rosado", "El achurado rosado es obra nueva: la Sala 01 y los dos baños se construyen completos."),
            ("Rótulo del recinto", "Nombre y superficie útil, que es la superficie interior: 40,16 m². Ese número va "
             "directo a pavimento."),
            ("Ventana", "En el muro izquierdo el achurado se corta y queda una línea doble delgada: es la ventana. La "
             "cadena vertical 0,75 · 2,68 · 0,60 dice muro, ventana de 2,68 m, muro. El 3,88 es el fondo interior."),
            ("Puerta", "El arco muestra hacia dónde abre la hoja. Su ancho, 0,95, está en la cota vertical junto al muro."),
            ("Artefactos", "En el B. Mujeres 2 se cuentan 5 WC (óvalos dentro de los cubículos) y 5 lavamanos (contra "
             "el muro de abajo)."),
            ("Círculo Ø1,50", "El círculo punteado del B. Universal es el espacio de giro de una silla de ruedas. No se "
             "construye: se respeta."),
        ],
        cuenta="Sala 01: 10,50 × 3,88 = 40,74 m². El rótulo dice 40,16 (+1,4%): la diferencia es el saliente de la "
               "chimenea de la casona que entra en la sala.<br/>Perímetro: 2 × (10,50 + 3,88) = 28,76 m. Guardapolvo: "
               "28,76 − 0,95 de la puerta = 27,81 ml.<br/>B. Mujeres 2: 4,45 × 2,78 = 12,37 m².",
        prueba="¿Cuánto mide de ancho el B. Universal y en qué cadena lo encontraste?",
        respuesta="1,60 m, en la cadena de cotas de arriba, entre un muro de 0,15 y uno de 0,12.",
    ),
    dict(
        n=2, titulo="Salas 02 y 03 y patio central", donde="Arriba a la derecha del plano.",
        clip=(1460, 150, 2440, 860), dpi=200, disposicion="lado", ancho_img=500,
        marcas=[{"n": 1, "izq": (1773, 192.5)}, {"n": 2, "arriba": (2039.5, 432)}, (3, 1628, 604, 1582, 562),
                {"n": 4, "izq": (1730, 556.5)}, (5, 1950, 604, 1995, 612), (6, 1640, 750),
                {"n": 7, "izq": (1755, 622)}, {"n": 8, "izq": (1669, 282)}],
        items=[
            ("Cadena de arriba", "1,60 · 0,12 · 8,46 · 0,12 · 4,60 · 0,12: el B. Universal, la Sala 02 (8,46 m de "
             "ancho) y la Sala 03 (4,60 m)."),
            ("Cotas verticales", "La 4,60 junto al muro entre las salas es el fondo de la Sala 02. La 9,00, a la "
             "derecha, es el largo de la Sala 03."),
            ("Rebaje de la puerta", "El muro se mete 0,85 m y deja un nicho de 1,07 m (0,95 de puerta + 0,12 de muro). "
             "Ese rectángulo queda fuera de la sala."),
            ("Ventanas", "1,20 · 2,40 · 0,60 · 2,40 · 0,79 = muro, ventana, muro, ventana, muro. Donde el muro es línea "
             "doble delgada hay ventana: 2 ventanas de 2,40 m."),
            ("Puerta y ventana de la Sala 03", "El arco es la puerta. En ese muro, la cadena 0,95 · 0,70 · 2,30 marca "
             "puerta, muro y una ventana de 2,30 m hacia el patio."),
            ("Patio y baranda", "Rectángulo rodeado por «baranda h = 0,95 m». Los tramos de 2,03 son paños de baranda "
             "entre postes de 0,10. Mide 8,50 × 2,70 m."),
            ("N.P.T.", "Nivel de piso terminado, en metros. El pasillo está a −0,05 y el patio a −0,20: el patio queda "
             "15 cm más abajo."),
            ("Proyección del alero", "La línea segmentada «proy. alero techumbre» marca hasta dónde llega el techo. "
             "Está arriba, no en el piso."),
        ],
        cuenta="Sala 02: 8,46 × 4,60 = 38,92 m², menos el rebaje 1,07 × 0,85 = 0,91 → 38,01 m² (rótulo 38,00 ✓).<br/>"
               "Sala 03: 4,60 × 9,00 = 41,40 m², exacto.<br/>Patio: 8,50 × 2,70 = 22,95 m². Baranda del patio: "
               "8,50 + 8,50 + 2,70 = 19,70 ml.",
        prueba="¿Cuántas ventanas tiene la Sala 02 y de qué ancho son?",
        respuesta="2 ventanas de 2,40 m, en el muro de abajo.",
    ),
    dict(
        n=3, titulo="La casona, sus baños y el hall", donde="Al centro del plano.",
        clip=(330, 330, 1560, 980), dpi=180, disposicion="lado", ancho_img=520,
        marcas=[(1, 930, 428, 930, 462), {"n": 2, "izq": (624, 585)}, {"n": 3, "izq": (1001, 585)},
                {"n": 4, "arriba": (1128, 561)}, {"n": 5, "izq": (745, 900.5)}, {"n": 6, "izq": (889, 844.5)},
                {"n": 7, "izq": (1265, 863)}, {"n": 8, "izq": (839, 947)}, (9, 700, 760)],
        items=[
            ("Muro negro = existente", "Los muros negros y gruesos son de la casona, que se conserva. Lo rosado dentro "
             "de ellos es nuevo: tabiques, cierres de vanos y shafts."),
            ("Recepción", "2,81 × 3,00 m más el bow window (la ventana poligonal de la izquierda) = 13,08 m² rotulados."),
            ("Estar de Alumnos", "22,96 m² rotulados. Los 10 círculos no tienen nota: no se presupuestan hasta saber "
             "qué son."),
            ("Refuerzo de acero", "Nota escrita junto a la cota 1,46: se abre un vano nuevo en el muro derecho, con "
             "2 pilares de acero de 0,10 y una viga encima."),
            ("Escalera de la casona", "Los peldaños numerados 01 a 07 suben al piso 2. Está dentro de la casona, así que "
             "se asume existente."),
            ("Baños 1", "B. Hombres 1 y B. Mujeres 1: 1 WC y 1 lavamanos cada uno. No tienen superficie escrita: se "
             "midieron a escala."),
            ("B. Hombres 2", "2 WC en cubículos, 2 urinarios y 3 lavamanos."),
            ("Shafts", "Cuadritos rosados en el muro de abajo con la nota «Shaft ext. forzada»: ductos de extracción de "
             "los baños (son 3). «Vent. Sanit.» y «Descarga Sanit.» marcan lo existente."),
            ("Hall sin rótulo", "El espacio abierto entre la Recepción, la escalera y los baños no tiene superficie "
             "escrita. Se midió a escala (unos 32 m²) y hay que confirmarlo en terreno."),
        ],
        cuenta="<b>Medir a escala:</b> cuando falta una cota, se usa una cota conocida como regla. Por ejemplo, si la "
               "cota 1,50 mide 135 puntos en la pantalla, cada punto vale 1,50 ÷ 135 = 0,011 m. Lo que se mide así "
               "queda como «Supuesto» hasta confirmarlo en terreno.",
        prueba="¿Cuántos artefactos tiene en total el B. Hombres 2?",
        respuesta="7: 2 WC, 2 urinarios y 3 lavamanos.",
    ),
    dict(
        n=4, titulo="Salas 07 y 06", donde="Abajo a la izquierda del plano.",
        clip=(120, 880, 1180, 1490), dpi=200, disposicion="lado", ancho_img=540,
        marcas=[{"n": 1, "izq": (476, 1411.5)}, {"n": 2, "izq": (855, 1383.5)}, (3, 300, 1200, 345, 1143),
                {"n": 4, "arriba": (251.5, 1036)}, (5, 486, 940, 486, 912), {"n": 6, "izq": (1069, 1141.5)},
                {"n": 7, "der": (469, 1189.5)}, (8, 700, 955, 700, 922)],
        items=[
            ("Cadena exterior", "3,10 · 1,01 · 0,77 · 2,40 · 1,60 · 2,40 · 1,20 · 2,40 · 1,85: mide por fuera el muro "
             "de la calle, tramo lleno y ventana. Cada 2,40 es una ventana."),
            ("Cadena interior", "0,12 · 4,38 · 0,12 · 10,00: la Sala 07 mide 4,38 m de ancho y la Sala 06, 10,00 m."),
            ("Ochavo", "La esquina cortada de la Sala 07 (1,43 en diagonal) le quita un triángulo de 1,01 × 1,01 m."),
            ("Fondo de la Sala 07", "La cota vertical 4,30 es el fondo interior; la 4,70 incluye los muros."),
            ("Puerta de la Sala 07", "Está en el muro de arriba (0,95) y abre hacia el acceso techado de la casona."),
            ("Rebaje de la Sala 06", "Como en la Sala 02: un nicho de 0,85 de fondo donde está la puerta, abajo a la "
             "derecha."),
            ("L.O.E. y antejardín", "La L.O.E. (línea oficial de edificación) marca hasta dónde se puede construir "
             "hacia la calle. Hasta la reja quedan 3,00 m de antejardín."),
            ("Muro de la casona", "El muro de arriba de la Sala 06 es negro: es de la casona. Por eso esta sala solo "
             "tiene tres muros nuevos."),
        ],
        cuenta="Sala 07: 4,38 × 4,30 = 18,83, menos el ochavo 1,01 × 1,01 ÷ 2 = 0,51 → 18,32 m² (rótulo 18,39; −0,4%)."
               "<br/>Sala 06: su fondo no está acotado aquí. La Sala 04, en la misma fila, mide 4,20: 10,00 × 4,20 = "
               "42,00, menos el rebaje 0,91 = 41,09 m², igual al rótulo. Así se confirma una cota que falta.",
        prueba="¿Cuántas ventanas tiene la Sala 06 y cómo lo supiste?",
        respuesta="2 de 2,40 m: en la cadena exterior, dentro del tramo de la Sala 06, aparecen dos 2,40.",
    ),
    dict(
        n=5, titulo="Salas 05 y 04 y pasillo frontal", donde="Abajo a la derecha del plano.",
        clip=(1140, 880, 2440, 1490), dpi=180, disposicion="lado", ancho_img=540,
        marcas=[{"n": 1, "izq": (1428, 1383.5)}, {"n": 2, "abajo": (2407.5, 1054)}, (3, 2335, 990, 2392, 990),
                {"n": 4, "der": (1232, 1141.5)}, {"n": 5, "izq": (2111, 1222.5)}, {"n": 6, "izq": (1610, 1256)},
                {"n": 7, "izq": (2242, 1254)}, {"n": 8, "izq": (1363, 1458.5)}],
        items=[
            ("Cadena de anchos", "0,12 · 10,00 · 0,12 · 1,50 · 0,12 · 9,45 · 0,14: la Sala 05, el pasillo entre "
             "salas y la Sala 04."),
            ("Fondo 4,20", "La cota de la derecha (0,20 · 4,20 · 0,20) da el fondo interior de la Sala 04, con muros "
             "de 0,20."),
            ("Muro de deslinde", "La nota vertical «muro de albañilería - h = 3,50 m» dice que el muro derecho de la "
             "Sala 04 también es el límite del terreno, de 3,50 m de alto."),
            ("Rebajes de puerta", "Las dos salas tienen el nicho de 0,85 con su puerta, abajo a la izquierda."),
            ("Pasillo frontal", "Mide 1,60 de ancho (cota vertical) y corre frente a las salas 06, 05 y 04."),
            ("Baranda y alero", "En el borde del pasillo va la «baranda h = 0,95 m». La línea segmentada de abajo es "
             "el alero que lo techa."),
            ("Zona baja", "Fuera de la baranda, frente a la Sala 04, hay una franja de 4,41 m a N.P.T. −0,20, más "
             "baja que el pasillo (−0,05)."),
            ("Cota total", "35,30 es el largo de todo el frente construido, desde el ochavo de la Sala 07 hasta el muro "
             "de la Sala 04."),
        ],
        cuenta="Sala 05: 10,00 × 4,20 = 42,00, menos el rebaje 0,91 = 41,09 m², igual al rótulo.<br/>Sala 04: 9,45 × "
               "4,20 = 39,69, menos 0,91 = 38,78 m², igual al rótulo.<br/>Pasillo entre salas: 1,50 × 4,44 = 6,66 m².",
        prueba="El rebaje de la puerta mide 1,07 × 0,85 m. ¿Cuánto le resta a la superficie de la sala?",
        respuesta="0,91 m² (1,07 × 0,85 = 0,9095).",
    ),
    dict(
        n=6, titulo="Acceso por el costado izquierdo", donde="Borde izquierdo del plano.",
        clip=(60, 150, 600, 960), dpi=220, disposicion="lado", ancho_img=None,
        marcas=[(1, 182, 620, 148, 620), (2, 250, 330, 205, 330), (3, 232, 790, 175, 812),
                {"n": 4, "izq": (217, 444.5)}, {"n": 5, "arriba": (417.5, 675)}, (6, 322, 745, 360, 800),
                {"n": 7, "der": (489, 788)}],
        items=[
            ("Deslinde", "La línea larga del borde, segmentada con puntos, es el límite del terreno."),
            ("Portón", "Dos arcos grandes que abren hacia adentro: un portón de dos hojas de unos 2,60 m (medido a "
             "escala)."),
            ("Puerta peatonal y N.T.N.", "Un arco chico en el deslinde es una puerta peatonal. Al lado, «N.T.N. ±0,00» "
             "es el nivel del terreno: el cero desde donde se miden todos los N.P.T."),
            ("Franja de 3,00", "Entre el deslinde y la línea segmentada interior hay 3,00 m: es el acceso lateral."),
            ("Alero de acceso", "Rectángulo segmentado «proy. alero techumbre» con 2 pilares (los cuadrados negros): un "
             "techo sobre la entrada de la casona."),
            ("Símbolo de rampa", "Rectángulo de 1,55 × 2,15 con un triángulo. La entrada está a +0,10 y el terreno a "
             "±0,00, así que se interpretó como rampa. Hay que confirmarlo con Luciano."),
            ("N.P.T. de la entrada", "+0,10: el piso del acceso está 10 cm sobre el terreno."),
        ],
        cuenta="Alero de acceso, medido a escala: 2,59 × 4,59 = 11,89 m² (Supuesto).<br/>Desnivel de la entrada: "
               "+0,10 − 0,00 = 10 cm.",
        prueba="¿Por qué la entrada de la casona necesita una rampa?",
        respuesta="Porque el piso del acceso está 10 cm más alto que el terreno (N.P.T. +0,10 contra N.T.N. ±0,00).",
    ),
    dict(
        n=7, titulo="Detalle: Oficina 01 y escalera", donde="Al centro, a la derecha de la casona.",
        clip=(1170, 470, 1470, 850), dpi=320, disposicion="lado", ancho_img=None,
        marcas=[(1, 1207, 683), (2, 1322, 640, 1288, 640), (3, 1262, 528, 1288, 545), (4, 1432, 720),
                (5, 1281, 756), (6, 1282, 836, 1300, 800), (7, 1352, 700, 1373, 700)],
        items=[
            ("Cadena de la puerta", "0,12 · 0,95 · 0,12 · 1,34: muro, puerta de 0,95, muro y el tramo que sigue."),
            ("Tabiques nuevos", "Las cotas 1,05 y 0,75 miden los tabiques rosados junto a la puerta."),
            ("Otra rampa", "Rectángulo de 1,25 × 1,66 con triángulo, entre N.P.T. +0,10 y −0,05: salva 15 cm."),
            ("Escalera exterior", "Peldaños con «baranda h = 0,95 m»: sube a un segundo piso, probablemente el de la "
             "casona. Falta saber si existe o es nueva."),
            ("Rótulo de la Oficina 01", "10,99 m². Tiene forma de L y no todas sus medidas están acotadas: el "
             "perímetro se mide en terreno."),
            ("Muro con el B. Hombres 2", "Muro rosado (nuevo) con los 3 lavamanos del baño; a la izquierda, un "
             "urinario."),
            ("Muro negro", "El muro grueso de la derecha es de la casona: la oficina queda dentro de ella."),
        ],
        cuenta="Desnivel de la rampa: +0,10 − (−0,05) = 0,15 m. Por eso hace falta una rampa entre los dos pisos.",
        prueba="¿Cuánto mide la puerta de la Oficina 01?",
        respuesta="0,95 m: es la cota del medio en la cadena 0,12 · 0,95 · 0,12.",
    ),
]

SIMBOLOS = [
    ("Muro nuevo", (1525, 290, 1645, 350), "Achurado rosado.", "Se construye: fundaciones, muro y terminaciones."),
    ("Muro existente", (1290, 895, 1440, 960), "Contorno negro sin achurado: la casona. A la derecha nace un muro "
     "rosado nuevo.", "Se conserva; solo se interviene donde el plano lo indica."),
    ("Cadena de cotas", (1425, 180, 1560, 204), "Medidas en metros, una tras otra. Las chicas son muros; las "
     "grandes, recintos.", "De aquí salen largos y anchos."),
    ("Rótulo de recinto", (1722, 426, 1833, 463), "Nombre y «Sup. útil»: la superficie interior.",
     "Va directo a pavimento y sirve para comprobar tus cuentas."),
    ("Puerta", (1962, 576, 2032, 640), "Hoja y arco que muestra hacia dónde abre.",
     "Se cuenta y se descuenta del guardapolvo."),
    ("Ventana", (1630, 547, 1760, 586), "El achurado se corta y queda una línea doble delgada.",
     "Se cuenta con su ancho; el alto sale de las elevaciones."),
    ("N.P.T.", (1750, 612, 1795, 641), "Nivel de piso terminado, en metros sobre o bajo el terreno (±0,00).",
     "Las diferencias de nivel piden rellenos, gradas o rampas."),
    ("Círculo Ø1,50", (1440, 325, 1535, 420), "Espacio de giro de una silla de ruedas (accesibilidad universal).",
     "No se construye ni se cubica."),
    ("Proyección del alero", (1600, 270, 1760, 296), "Línea segmentada: el borde del techo visto desde arriba.",
     "Va en techumbre y aleros, no en el piso."),
    ("Baranda", (1515, 643, 1690, 676), "Línea con postes y la nota de su altura.", "Se mide en metros lineales (ml)."),
    ("Artefactos", None, "WC (óvalo), lavamanos (semicírculo) y urinario (óvalo chico contra el muro).",
     "Se cuentan uno por uno."),
    ("Nota escrita", (815, 203, 950, 221), "Texto con datos que el dibujo no muestra, como alturas o materiales.",
     "Manda sobre lo que uno supone."),
    ("Shaft", (835, 912, 905, 954), "Ducto vertical: un cuadrito con su nota.", "Se cuenta por unidad."),
    ("Escalera", (740, 838, 852, 910), "Peldaños dibujados y numerados.", "Si es nueva, se presupuesta completa."),
    ("Símbolo de rampa", (350, 722, 450, 880), "Rectángulo con un triángulo. Así se interpretó en este plano.",
     "Confirmar con Luciano antes de cubicar."),
    ("L.O.E.", (400, 1170, 520, 1200), "Línea oficial de edificación: hasta dónde se construye hacia la calle.",
     "Entre ella y la reja queda el antejardín."),
]
ARTEFACTOS = [("WC", (1190, 233, 1224, 273)), ("lavamanos", (1186, 364, 1222, 393)), ("urinario", (1208, 790, 1237, 820))]

# ------------------------------------------------------------------------------------- páginas
c = canvas.Canvas(SALIDA, pagesize=(ANCHO, ALTO))
c.setTitle("Cómo leer el plano · Sede San Miguel")
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


mapa_png = recorte("mapa", (0, 0, ANCHO_PLANO := pagina_plano.rect.width, pagina_plano.rect.height), 60)
CLIP_MAPA = (0, 0, pagina_plano.rect.width, pagina_plano.rect.height)


def mapa(x, y, w, zonas_dibujar, destacar=None):
    h = w * CLIP_MAPA[3] / CLIP_MAPA[2]
    c.drawImage(mapa_png, x, y, w, h)
    c.setStrokeColor(HexColor("#B0B0B0"))
    c.setLineWidth(0.5)
    c.rect(x, y, w, h)
    s = w / CLIP_MAPA[2]
    for z in zonas_dibujar:
        x0, y0, x1, y1 = z["clip"]
        if destacar is not None and z["n"] != destacar:
            continue
        c.setStrokeColor(ROJO)
        c.setLineWidth(1.4 if destacar else 1.1)
        c.rect(x + x0 * s, y + h - y1 * s, (x1 - x0) * s, (y1 - y0) * s, stroke=1, fill=0)
        if destacar is None:
            marcador(c, x + x0 * s + 9, y + h - y0 * s - 9, z["n"], r=7.5)
    return h


# 1. Portada y cómo leer una planta
nueva_pagina()
titulo_pagina("Cómo leer el plano de la Sede San Miguel",
              "Guía zona por zona sobre el plano San Miguel v5 (planta del piso 1) · preparada por Alma para Gonza, 08-10-2026")
col_w = 300
bloques = [
    Paragraph("Qué es una planta", SUBT),
    Paragraph("Es el edificio cortado a la altura de la cintura y visto desde arriba.", CUERPO),
    Paragraph("• Lo cortado, los muros, se dibuja grueso.<br/>• Lo que queda más abajo (piso, artefactos) va en línea "
              "fina.<br/>• Lo que está arriba (aleros, techo) va en línea segmentada.", CUERPO),
    Paragraph("Tres reglas de este plano", SUBT),
    Paragraph("1. Las medidas están en metros y usan punto: <b>10.50</b> son 10,50 m.<br/>2. <b>Rosado = obra nueva. "
              "Negro = existente</b> (la casona, que se conserva).<br/>3. La <b>Sup. útil</b> de cada recinto es la "
              "superficie interior: sirve para comprobar tus cuentas.", CUERPO),
    Paragraph("Cómo usar esta guía", SUBT),
    Paragraph("1. El mapa de la derecha muestra las 7 zonas que se revisaron.<br/>2. En las páginas 2 y 3 están los "
              "símbolos, con ejemplos reales del plano.<br/>3. Cada zona tiene una página con el recorte y números "
              "rojos, qué significa cada número, la cuenta y una pregunta para practicar.<br/>4. Al final están la "
              "receta para pasar del plano al presupuesto y las respuestas.", CUERPO),
    caja("Consejo", "Amplía los recortes en la pantalla: están en alta resolución y así se leen bien las cotas chicas.",
         FONDO_PRUEBA, col_w),
]
ubic = empacar(bloques, [(MARGEN, ALTO - MARGEN - 48, col_w, 470)])
assert ubic, "la portada no cabe"
for u, ux, uy, uw in ubic:
    u.wrap(uw, 10_000)
    u.drawOn(c, ux, uy)
mx = MARGEN + col_w + 22
mw = ANCHO - MARGEN - mx
mh = mw * CLIP_MAPA[3] / CLIP_MAPA[2]
mapa(mx, ALTO - MARGEN - 52 - mh, mw, ZONAS)
c.setFont("Dv", 8)
c.setFillColor(GRIS)
c.drawString(mx, ALTO - MARGEN - 60 - mh, "Las 7 zonas de la guía sobre el plano completo. La zona 7 es un detalle dentro "
             "de la 3.")
for k, z in enumerate(ZONAS):
    columna, fila = divmod(k, 4)
    lx = mx + columna * (mw / 2)
    ly = ALTO - MARGEN - 82 - mh - fila * 17
    marcador(c, lx + 6, ly + 3, z["n"], r=6.5)
    c.setFillColor(HexColor("#000000"))
    c.setFont("Dv", 8.6)
    c.drawString(lx + 17, ly, f"{z['titulo']} (página {k + 4})")
pie(c, pagina)

# 2-3. Símbolos
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
        ytop = top - fila * (chh + gap)
        c.setStrokeColor(HexColor("#D0D0D0"))
        c.setLineWidth(0.7)
        c.roundRect(x, ytop - chh, cw, chh, 5, stroke=1, fill=0)
        p = Paragraph(nombre, TIT_CARTA)
        p.wrap(cw - 16, 50)
        p.drawOn(c, x + 8, ytop - 20)
        caja_w, caja_h = cw - 16, 128
        caja_y = ytop - 26 - caja_h
        c.setFillColor(HexColor("#F6F6F6"))
        c.rect(x + 8, caja_y, caja_w, caja_h, stroke=0, fill=1)
        if clip:
            png = recorte(f"simbolo_{parte}_{i}", clip, 400)
            cwp, chp = clip[2] - clip[0], clip[3] - clip[1]
            esc = min((caja_w - 8) / cwp, (caja_h - 8) / chp)
            iw, ih = cwp * esc, chp * esc
            c.drawImage(png, x + 8 + (caja_w - iw) / 2, caja_y + (caja_h - ih) / 2, iw, ih)
        else:
            sub_w = (caja_w - 16) / 3
            for k, (etq, aclip) in enumerate(ARTEFACTOS):
                png = recorte(f"artefacto_{k}", aclip, 400)
                cwp, chp = aclip[2] - aclip[0], aclip[3] - aclip[1]
                esc = min((sub_w - 6) / cwp, (caja_h - 22) / chp)
                iw, ih = cwp * esc, chp * esc
                bx = x + 8 + 4 + k * (sub_w + 4)
                c.drawImage(png, bx + (sub_w - iw) / 2, caja_y + 16 + (caja_h - 22 - ih) / 2, iw, ih)
                c.setFont("Dv", 7.5)
                c.setFillColor(GRIS)
                c.drawCentredString(bx + sub_w / 2, caja_y + 5, etq)
        textos = [Paragraph(f"<b>Qué es:</b> {que}", CUERPO_CH),
                  Paragraph(f"<font color='#1F3A5F'><b>Para el presupuesto:</b> {presu}</font>", CUERPO_CH)]
        ubic = empacar(textos, [(x + 8, caja_y - 6, cw - 16, caja_y - 6 - (ytop - chh) - 4)])
        if not ubic:
            avisos.append(f"símbolo «{nombre}»: el texto no cabe en la tarjeta")
            continue
        for u, ux, uy, uw in ubic:
            u.wrap(uw, 10_000)
            u.drawOn(c, ux, uy)
    pie(c, pagina)

# 4-10. Zonas
for z in ZONAS:
    nueva_pagina()
    titulo_pagina(f"Zona {z['n']} · {z['titulo']}", z["donde"])
    loc_w = 112
    mapa_h = loc_w * CLIP_MAPA[3] / CLIP_MAPA[2]
    mapa(ANCHO - MARGEN - loc_w, ALTO - MARGEN - mapa_h, loc_w, ZONAS, destacar=z["n"])
    png = recorte(f"zona{z['n']}", z["clip"], z["dpi"])
    cw_, ch_ = z["clip"][2] - z["clip"][0], z["clip"][3] - z["clip"][1]
    arriba = ALTO - MARGEN - mapa_h - 10
    abajo = 30
    alto_util = arriba - abajo
    ancho_util = ANCHO - 2 * MARGEN

    def unidades(ancho_caja):
        u = [Paragraph("Qué estás viendo", SUBT)]
        u += [Paragraph(f"{num(k)}&nbsp; <b>{t if t.endswith('.') else t + '.'}</b> {d}", CUERPO)
              for k, (t, d) in enumerate(z["items"], 1)]
        u += [caja("La cuenta", z["cuenta"], FONDO_CUENTA, ancho_caja),
              caja("Prueba tú", z["prueba"] + " <font color='#6B6B6B'>(Respuesta al final de la guía.)</font>",
                   FONDO_PRUEBA, ancho_caja)]
        return u

    colocado = None
    for intento in range(30):
        reduccion = 0.95 ** intento
        if z["disposicion"] == "arriba":
            iw = ancho_util * reduccion
            ih = iw * ch_ / cw_
            ix, iy = MARGEN, arriba - ih
            alto_txt = iy - 12 - abajo
            col_w3 = (ancho_util - 2 * 14) / 3
            cols = [(MARGEN + k * (col_w3 + 14), iy - 12, col_w3, alto_txt) for k in range(3)]
            ancho_caja = col_w3
        else:
            ih = alto_util * reduccion
            iw = ih * cw_ / ch_
            if z.get("ancho_img") and iw > z["ancho_img"] * reduccion:
                iw = z["ancho_img"] * reduccion
                ih = iw * ch_ / cw_
            ix, iy = MARGEN, arriba - ih
            resto = ancho_util - iw - 16
            bajo_imagen = [(MARGEN, iy - 10, iw, iy - 10 - abajo)] if iy - abajo > 70 else []
            # primero una sola columna (de lectura cómoda); si no cabe y hay espacio, dos
            opciones = [[(ix + iw + 16, arriba, min(resto, 540), alto_util)] + bajo_imagen]
            if resto > 430:
                col_w = (resto - 14) / 2
                opciones.append([(ix + iw + 16 + k * (col_w + 14), arriba, col_w, alto_util) for k in range(2)]
                                + bajo_imagen)
            colocado = None
            for cols in opciones:
                colocado = empacar(unidades(cols[0][2]), cols)
                if colocado:
                    break
            if colocado:
                break
            continue
        u = unidades(ancho_caja)
        colocado = empacar(u, cols)
        if colocado:
            break
    if not colocado:
        avisos.append(f"zona {z['n']}: el texto no cabe ni achicando la imagen")
        continue
    imagen_con_numeros(c, png, z["clip"], ix, iy, iw, ih, z["marcas"])
    for u_, ux, uy, uw in colocado:
        u_.wrap(uw, 10_000)
        u_.drawOn(c, ux, uy)
    z["escala"] = iw / cw_
    pie(c, pagina)

# 11. Receta y respuestas
nueva_pagina()
titulo_pagina("Del plano al presupuesto", "La receta que se usó en esta obra y las respuestas de «Prueba tú».")
pasos = [
    ("Ubica el recinto", "por su rótulo: nombre y Sup. útil."),
    ("Busca sus cotas:", "la cadena que lo cruza de lado a lado y la que lo cruza de arriba abajo."),
    ("Multiplica y compara:", "largo × ancho, menos rebajes u ochavos, contra la Sup. útil. Si difiere más de 3%, "
     "algo se leyó mal."),
    ("Saca el perímetro:", "2 × (largo + ancho). El guardapolvo descuenta el ancho de las puertas."),
    ("Cuenta", "puertas (arcos), ventanas (líneas dobles) y artefactos, uno por uno."),
    ("Marca lo que falta:", "lo que la planta no dice (alturas, materiales) queda «Pendiente»; lo medido a escala, "
     "«Supuesto»."),
]
izq = [Paragraph("La receta en 6 pasos", SUBT)]
izq += [Paragraph(f"{num(k)}&nbsp; <b>{t}</b> {d}", CUERPO) for k, (t, d) in enumerate(pasos, 1)]
izq.append(Paragraph("Lo que una planta no dice", SUBT))
tabla = Table([[Paragraph("<b>Falta</b>", CUERPO), Paragraph("<b>Dónde se encuentra</b>", CUERPO)],
               [Paragraph("Altura de muros y cielos", CUERPO), Paragraph("Cortes", CUERPO)],
               [Paragraph("Alto de ventanas y fachadas", CUERPO), Paragraph("Elevaciones", CUERPO)],
               [Paragraph("Materiales y terminaciones", CUERPO), Paragraph("Especificaciones técnicas (EETT)", CUERPO)],
               [Paragraph("Electricidad, agua y alcantarillado", CUERPO), Paragraph("Planos de especialidades", CUERPO)]],
              colWidths=[180, 175])
tabla.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, HexColor("#C8C8C8")),
                           ("BACKGROUND", (0, 0), (-1, 0), HexColor("#EDEDED")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
izq.append(tabla)
izq.append(Paragraph("Por eso, en el Excel de la obra hay partidas «Pendientes»: no son un error de lectura, son "
                     "datos que esta planta no trae.", CUERPO))
der = [Paragraph("Respuestas de «Prueba tú»", SUBT)]
der += [Paragraph(f"{num(z['n'])}&nbsp; <b>Zona {z['n']}.</b> {z['prueba']}<br/>"
                  f"<font color='#1F3A5F'>→ {z['respuesta']}</font>", CUERPO) for z in ZONAS]
colw = (ANCHO - 2 * MARGEN - 24) / 2
for lista, x in ((izq, MARGEN), (der, MARGEN + colw + 24)):
    ubic = empacar(lista, [(x, ALTO - MARGEN - 46, colw, ALTO - MARGEN - 46 - 30)])
    if not ubic:
        avisos.append("la página final no cabe")
        continue
    for u_, ux, uy, uw in ubic:
        u_.wrap(uw, 10_000)
        u_.drawOn(c, ux, uy)
pie(c, pagina)
c.save()

print("guardado:", SALIDA, "· páginas:", pagina)
for z in ZONAS:
    print(f"  zona {z['n']}: escala de la imagen {z.get('escala', 0):.2f}")
for a in avisos:
    print("  AVISO:", a)
sys.exit(1 if avisos else 0)

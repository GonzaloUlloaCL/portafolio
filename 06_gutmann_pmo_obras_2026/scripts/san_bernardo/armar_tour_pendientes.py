"""Tour visual de partidas pendientes, San Bernardo (plano v3). Uso: tour.py <plano.pdf> <trabajo> <salida.pdf>"""
import os, sys
import pymupdf
from reportlab.lib.colors import HexColor, Color, white, black
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

PLANO, TRAB, SALIDA = sys.argv[1:4]
os.makedirs(TRAB, exist_ok=True)
pdfmetrics.registerFont(TTFont("Dv", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DvB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("Dv", normal="Dv", bold="DvB", italic="Dv", boldItalic="DvB")
W, H = 842, 595
MG = 28
AZUL, ROJO, GRIS = HexColor("#1F3A5F"), HexColor("#C62828"), HexColor("#6B6B6B")
NARANJA, VERDE, CELESTE, MORADO = HexColor("#E67E22"), HexColor("#2E7D32"), HexColor("#1976D2"), HexColor("#7B1FA2")
F_CUENTA, F_OJO, F_PRUEBA = HexColor("#EAF1F8"), HexColor("#FBE3E3"), HexColor("#FFF4CC")
CU = ParagraphStyle("cu", fontName="Dv", fontSize=8.4, leading=10.8)
CUc = ParagraphStyle("cuc", parent=CU, fontSize=7.6, leading=9.4)
SUB = ParagraphStyle("sub", fontName="DvB", fontSize=10.5, leading=13, textColor=AZUL)

pag = pymupdf.open(PLANO)[0]
K, P = 56.6929, 0.8835
X0, Y0 = 240 * P, 250 * P            # origen (m) de las coordenadas medidas en el plano


def recorte(nombre, m, dpi):
    r = pymupdf.Rect(X0 + m[0] * K, Y0 + m[1] * K, X0 + m[2] * K, Y0 + m[3] * K)
    ruta = os.path.join(TRAB, nombre + ".png")
    pag.get_pixmap(dpi=dpi, clip=r).save(ruta)
    return ruta


class Vista:
    """Imagen del plano en el PDF; convierte metros del plano a puntos de la página."""
    def __init__(self, c, png, m, x, ytop, w):
        self.c, self.m, self.x, self.w = c, m, x, w
        self.h = w * (m[3] - m[1]) / (m[2] - m[0])
        self.y = ytop - self.h
        c.drawImage(png, x, self.y, w, self.h)
        c.setStrokeColor(HexColor("#B0B0B0")); c.setLineWidth(0.6); c.rect(x, self.y, w, self.h)

    def p(self, mx, my):
        return (self.x + (mx - self.m[0]) / (self.m[2] - self.m[0]) * self.w,
                self.y + self.h - (my - self.m[1]) / (self.m[3] - self.m[1]) * self.h)

    def linea(self, pts, color, ancho=2.4, dash=None, alfa=0.9):
        c = self.c
        c.saveState(); c.setStrokeColor(color); c.setStrokeAlpha(alfa); c.setLineWidth(ancho)
        if dash: c.setDash(*dash)
        q = [self.p(*pt) for pt in pts]
        for a, b in zip(q, q[1:]):
            c.line(*a, *b)
        c.restoreState()

    def poligono(self, pts, color, alfa=0.18):
        c = self.c
        c.saveState(); c.setFillColor(color); c.setFillAlpha(alfa); c.setStrokeAlpha(0)
        path = c.beginPath(); q = [self.p(*pt) for pt in pts]
        path.moveTo(*q[0])
        for t in q[1:]:
            path.lineTo(*t)
        path.close(); c.drawPath(path, fill=1, stroke=0); c.restoreState()

    def etiqueta(self, mx, my, texto, color, tam=8.5):
        c = self.c
        x, y = self.p(mx, my)
        tw = pdfmetrics.stringWidth(texto, "DvB", tam)
        c.saveState(); c.setFillColor(white); c.setFillAlpha(0.88)
        c.roundRect(x - tw / 2 - 3, y - 4, tw + 6, tam + 5, 2, stroke=0, fill=1); c.restoreState()
        c.setFillColor(color); c.setFont("DvB", tam); c.drawCentredString(x, y, texto)

    def flecha(self, a, b, color):
        c = self.c
        (x1, y1), (x2, y2) = self.p(*a), self.p(*b)
        c.saveState(); c.setStrokeColor(color); c.setFillColor(color); c.setLineWidth(2)
        c.line(x1, y1, x2, y2)
        import math
        ang = math.atan2(y2 - y1, x2 - x1)
        pts = [(x2, y2), (x2 - 8 * math.cos(ang - 0.45), y2 - 8 * math.sin(ang - 0.45)),
               (x2 - 8 * math.cos(ang + 0.45), y2 - 8 * math.sin(ang + 0.45))]
        path = c.beginPath(); path.moveTo(*pts[0]); path.lineTo(*pts[1]); path.lineTo(*pts[2]); path.close()
        c.drawPath(path, fill=1, stroke=0); c.restoreState()

    def marca(self, n, mx, my, color=ROJO, r=7.5):
        c = self.c
        x, y = self.p(mx, my)
        c.setFillColor(color); c.setStrokeColor(white); c.setLineWidth(1); c.circle(x, y, r, stroke=1, fill=1)
        c.setFillColor(white); c.setFont("DvB", 8.5 if len(str(n)) < 2 else 7.5); c.drawCentredString(x, y - 3, str(n))


def titulo(c, t, sub):
    c.setFillColor(AZUL); c.setFont("DvB", 16); c.drawString(MG, H - MG - 14, t)
    c.setFillColor(GRIS); c.setFont("Dv", 9); c.drawString(MG, H - MG - 29, sub)


def pie(c, n):
    c.setFont("Dv", 7.2); c.setFillColor(GRIS)
    c.drawString(MG, 14, "Tour de partidas pendientes · Ampliación San Bernardo, Covadonga 139 · plano v3, 1:50 · "
                 "medidas «a escala» salen del dibujo vectorial; las cantidades del Excel las pone Gonza")
    c.drawRightString(W - MG, 14, f"página {n}")


def columna(c, items, x, ytop, w):
    y = ytop
    for it in items:
        if isinstance(it, tuple):
            titulo_c, texto, fondo = it
            t = Table([[Paragraph(titulo_c, ParagraphStyle("tc", fontName="DvB", fontSize=9, leading=11, textColor=AZUL))],
                       [Paragraph(texto, CU)]], colWidths=[w])
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fondo), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
            _, h = t.wrap(w, 1000); t.drawOn(c, x, y - h); y -= h + 6
        else:
            _, h = it.wrap(w, 1000); it.drawOn(c, x, y - h); y -= h + 4
    return y


def leyenda(c, x, y, filas):
    for color, texto, dash in filas:
        c.saveState(); c.setStrokeColor(color); c.setLineWidth(3)
        if dash: c.setDash(*dash)
        c.line(x, y + 3, x + 22, y + 3); c.restoreState()
        c.setFillColor(black); c.setFont("Dv", 8); c.drawString(x + 28, y, texto); y -= 13
    return y


c = canvas.Canvas(SALIDA, pagesize=(W, H))
c.setTitle("Tour de partidas pendientes · San Bernardo")
c.setAuthor("Alma, para Gonza")
M_TODO = (0.6, 2.4, 18.6, 24.9)
png_todo = recorte("todo", M_TODO, 85)

# Geometría leída en el plano (metros desde el origen de la zona de obra)
TECHO = [(5.17, 3.29), (17.39, 3.29), (17.39, 23.69), (1.59, 23.69), (1.59, 15.39), (9.09, 15.39), (9.09, 6.09),
         (5.17, 6.09)]
LIMATESAS = [((9.09, 6.09), (17.39, 3.29)), ((9.09, 15.39), (17.39, 23.69))]

# ---------------------------------------------------------------- 1. Resumen
titulo(c, "Partidas pendientes: qué falta y dónde está en el plano",
       "Revisión del Excel del Drive (14-10, 13:15). Rebaje y retiro de escombros quedan en pausa: método de Luciano.")
v = Vista(c, png_todo, M_TODO, W - MG - 300, H - MG - 42, 300)
v.poligono(TECHO, CELESTE, 0.15)
for a, b in LIMATESAS:
    v.linea([a, b], MORADO, 1.6, (4, 3))
v.marca(1, 13.3, 13.0); v.marca(2, 5.2, 19.4); v.marca(3, 14.2, 5.3); v.marca(4, 6.4, 23.69, NARANJA)
filas = [[Paragraph("<b>#</b>", CU), Paragraph("<b>Partida</b>", CU), Paragraph("<b>Qué falta</b>", CU)],
         ["1", Paragraph("D.3.2 Techumbre (general del sector)", CU),
          Paragraph("Hoy dice «del corredor», GL 1 × 1.700.000, con la observación de agua potable copiada. "
                    "Las techumbres de salas, Guardar y baños se sacaron de B: van aquí. Página 2.", CU)],
         ["2", Paragraph("D.3.3 Aleros (0,40 m)", CU),
          Paragraph("GL 1 × 1.500.000 con la observación «Igual que D.2.2». Medir el largo de alero. Página 3.", CU)],
         ["3", Paragraph("D.3.1 Aguas lluvia: canales y hojalatería", CU),
          Paragraph("GL 1 × 1.500.000. Medir el largo de canal y ver el valor de San Miguel. Página 3.", CU)],
         ["4", Paragraph("B.5 Baños 01 y 02", CU),
          Paragraph("Tabiques (un muro contado dos veces y tres sin contar), cielo y piso (5 × 3,6). Página 4.", CU)],
         ["5", Paragraph("Salas y textos", CU), Paragraph("Cantidades listas. Solo observaciones viejas. Página 5.", CU)]]
t = Table(filas, colWidths=[18, 150, 290])
t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, HexColor("#C8C8C8")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                       ("BACKGROUND", (0, 0), (-1, 0), HexColor("#EDEDED")), ("FONT", (0, 1), (0, -1), "DvB")]))
_, th = t.wrap(460, 1000); t.drawOn(c, MG, H - MG - 48 - th)
columna(c, [("Cómo leer el techo de este plano",
             "El plano dibuja con línea segmentada el borde del techo («proy. alero techumbre») y dos diagonales "
             "(moradas): van de la esquina interior del patio a la esquina exterior de la obra. Son <b>limatesas</b>: "
             "la línea donde se juntan dos aguas. Así, cada ala tiene una sola agua: <b>alta hacia el patio</b> (con "
             "alero y tapacán) y <b>baja hacia afuera</b> (con canal), como pide el correo: «caída de aguas lluvias "
             "hacia afuera o hacia el perímetro exterior de esta ampliación». Cerchas de 1,00 en el punto alto y 0,20 "
             "en el bajo, «para tapacán y canal».", F_CUENTA),
            ("Lo que no hice", "No moví ninguna cantidad. Ventanas a 1,70 y muros a 3,00 quedan como los dejaste "
             "(criterio de Luciano). Las medidas de este tour son la «luz» para que tú completes.", F_PRUEBA)],
        MG, H - MG - 58 - th, 460)
pie(c, 1); c.showPage()

# ---------------------------------------------------------------- 2. Techumbre general
titulo(c, "1 · Techumbre general del sector (D.3.2)",
       "Todo lo techado: recintos y corredor, hasta la línea «proy. alero techumbre». En celeste, la superficie.")
v = Vista(c, png_todo, M_TODO, MG, H - MG - 40, 330)
v.poligono(TECHO, CELESTE, 0.22)
v.linea(TECHO + [TECHO[0]], CELESTE, 1.8)
for a, b in LIMATESAS:
    v.linea([a, b], MORADO, 2, (5, 3))
v.flecha((5.5, 16.6), (5.5, 22.6), AZUL); v.flecha((10.4, 12.0), (16.4, 12.0), AZUL); v.flecha((7.2, 5.6), (7.2, 3.8), AZUL)
v.etiqueta(9.49, 2.85, "15,80 (0,40 + 15,00 + 0,40)", CELESTE); v.etiqueta(18.05, 13.5, "20,40", CELESTE)
v.etiqueta(5.3, 10.6, "hueco del patio", GRIS); v.etiqueta(5.3, 11.4, "7,50 × 12,10", GRIS)
v.etiqueta(7.1, 6.8, "+ 3,92 × 2,80", GRIS)
columna(c, [
    Paragraph("Cómo lo mediría", SUB),
    Paragraph("1. El <b>contorno exterior</b> es un rectángulo: 15,80 de ancho (los 15,00 de la cota de abajo más "
              "los aleros de 0,40 a cada lado) por 20,40 de largo (la cota de 20,00 más el alero sur de 0,40; al "
              "norte el techo llega al edificio existente).", CU),
    Paragraph("2. Se le resta el <b>hueco sin techo</b>: el patio y la zona de la araucaria, de 7,50 × 12,10 "
              "(del borde poniente hasta el alero del corredor oriente, y del edificio existente hasta el alero del "
              "corredor sur).", CU),
    Paragraph("3. Se le suma de vuelta el <b>corredor norte</b> que queda dentro de ese hueco: 3,92 × 2,80.", CU),
    ("La cuenta (a escala, en planta)",
     "15,80 × 20,40 − 7,50 × 12,10 + 3,92 × 2,80 = 322,32 − 90,75 + 10,98 = <b>242,55 m²</b>.<br/>"
     "Control por partes: recintos 155,10 (huella por fuera de muros) + corredor 54,85 + aleros ≈ 26 = 236 m². "
     "La diferencia (≈ 6 m²) es el corredor norte, que en «Cubicación base» se midió desde el pilar.<br/>"
     "La pendiente no cambia la cuenta: 0,80 m de alto en ~8 m de ancho da +0,5%.", F_CUENTA),
    ("Decisión tuya antes de poner la cantidad",
     "<b>¿El general incluye los aleros?</b> Si D.3.2 se mide hasta la línea de alero (242,55), D.3.3 Aleros "
     "contaría dos veces los ≈ 26 m². Opciones: (a) D.3.2 hasta muros y vigas (≈ 216) y D.3.3 aparte; o (b) "
     "D.3.2 con aleros y D.3.3 se elimina. Así lo hizo Luciano en San Miguel: techumbre de cada sala en m² "
     "(92.000) y «Techo alero» como GL aparte (F.2.1).", F_OJO),
    ("Nombre", "Hoy dice «Techumbre del corredor», pero ahora cubre todo el sector. La observación copiada "
     "(«mismos materiales que lo existente… araucaria») es la de agua potable.", F_PRUEBA),
], MG + 345, H - MG - 40, W - 2 * MG - 345)
leyenda(c, MG, H - MG - 50 - v.h, [(CELESTE, "Superficie de techo (proyección horizontal)", None),
                                    (MORADO, "Limatesa (encuentro de dos aguas)", (5, 3)),
                                    (AZUL, "Flecha: hacia dónde cae el agua", None)])
pie(c, 2); c.showPage()

# ---------------------------------------------------------------- 3. Aleros y canales
titulo(c, "2 · Aleros de los corredores (D.3.3) y canales (D.3.1)",
       "El alero es la franja de 0,40 que sobresale; el canal va en el borde bajo, donde cae el agua.")
v = Vista(c, png_todo, M_TODO, MG, H - MG - 40, 330)
v.poligono(TECHO, CELESTE, 0.08)
ALERO_PATIO = [(5.17, 6.09), (9.09, 6.09), (9.09, 15.39), (1.59, 15.39)]
v.linea(ALERO_PATIO, NARANJA, 3.4)
v.linea([(1.59, 15.39), (1.59, 23.69)], GRIS, 3.4, (6, 3))
v.linea([(5.17, 3.29), (5.17, 6.09)], GRIS, 3.4, (6, 3))
v.linea([(1.59, 23.69), (17.39, 23.69), (17.39, 3.29), (5.17, 3.29)], VERDE, 3.4)
for a, b in LIMATESAS:
    v.linea([a, b], MORADO, 1.6, (5, 3))
v.etiqueta(7.1, 6.55, "3,92", NARANJA); v.etiqueta(9.6, 10.7, "9,30", NARANJA); v.etiqueta(5.3, 15.85, "7,50", NARANJA)
v.etiqueta(9.5, 24.25, "canal sur 15,80", VERDE); v.etiqueta(17.6, 14.0, "20,40", VERDE)
v.etiqueta(11.3, 2.85, "canal norte 12,22", VERDE); v.etiqueta(1.25, 19.6, "8,30", GRIS)
columna(c, [
    Paragraph("Aleros: dónde están", SUB),
    Paragraph("<font color='#E67E22'><b>Naranjo, lado patio (alero de los corredores):</b></font> 0,40 más allá de "
              "las vigas, en el borde alto. Norte 3,92 + oriente 9,30 + sur 7,50 = <b>20,72 ml</b>.", CU),
    Paragraph("<b>Lado exterior (sobre los canales):</b> sur 15,80 + oriente 20,40 + poniente de la Sala 03 8,30 = "
              "<b>44,50 ml</b>. Cotas de 0,40 en las esquinas SE y en el poniente de la Sala 03.", CU),
    ("La cuenta del alero",
     "Solo corredores: 20,72 × 0,40 = <b>8,29 m²</b>.<br/>Todo el contorno: (20,72 + 44,50) × 0,40 = "
     "65,22 × 0,40 = <b>26,09 m²</b>.<br/>Si va en GL, como el «Techo alero» de San Miguel (F.2.1), estos m² son "
     "la base para el precio. Ojo con el doble conteo de la página 2.", F_CUENTA),
    Paragraph("Canales: dónde van", SUB),
    Paragraph("<font color='#2E7D32'><b>Verde, borde bajo:</b></font> sur 15,80 + oriente 20,40 + norte 12,22 (contra "
              "el edificio existente, de la esquina poniente del corredor norte hasta la esquina NE) = "
              "<b>48,42 ml</b>. Los bordes grises (poniente de la Sala 03 y del corredor norte) son "
              "<b>tapacán lateral</b>, sin canal. El lado patio es borde alto: tapacán, sin canal.", CU),
    ("Para conversar con Luciano",
     "• El canal norte queda en el encuentro con el techo existente («proy. alero techumbre» del edificio): es un "
     "canal de encuentro, más trabajo de hojalatería.<br/>• Bajadas de agua: el plano no las dibuja.<br/>"
     "• Valor de referencia: en San Miguel, F.2.3 «Aguas lluvia: canales (primer piso) + hojalatería salas» es "
     "GL 1 × 1.500.000 <b>+ 2.000.000</b> dentro de la fórmula del total (= 3.500.000). Para 71,91 ml de canal.",
     F_OJO),
], MG + 345, H - MG - 40, W - 2 * MG - 345)
leyenda(c, MG, H - MG - 50 - v.h, [(NARANJA, "Alero lado patio (corredores), 0,40", None),
                                    (VERDE, "Canal (borde bajo)", None),
                                    (GRIS, "Tapacán lateral, sin canal", (6, 3))])
pie(c, 3); c.showPage()

# ---------------------------------------------------------------- 4. Baños
titulo(c, "3 · Baños 01 y 02 (B.5): muros, cielo y piso",
       "Cada muro numerado con su largo y con qué colinda. Regla de Luciano: hacia afuera → exterior; si toca un baño → baño; entre recintos secos → interior.")
M_B = (10.9, 2.7, 18.2, 9.45)
png_b = recorte("banos", M_B, 220)
v = Vista(c, png_b, M_B, MG, H - MG - 46, 345)
MUROS = [  # n, puntos (m), color
    (1, [(11.75, 3.39), (11.75, 4.50)], CELESTE), (2, [(11.75, 4.50), (11.75, 6.05)], VERDE),
    (3, [(16.93, 3.39), (16.93, 6.05)], VERDE), (4, [(12.32, 3.19), (15.42, 3.19)], VERDE),
    (5, [(11.85, 6.05), (16.93, 6.05)], VERDE), (6, [(13.55, 3.39), (13.55, 6.05)], VERDE),
    (7, [(11.85, 4.38), (13.55, 4.38)], VERDE), (8, [(11.75, 6.05), (11.75, 8.20)], CELESTE),
    (9, [(16.93, 6.05), (16.93, 8.20)], CELESTE), (10, [(11.85, 7.93), (16.93, 7.93)], NARANJA)]
for n, pts, col in MUROS:
    v.linea(pts, col, 4.5, alfa=0.55)
POS = {1: (11.35, 3.95), 2: (11.35, 5.3), 3: (17.35, 4.6), 4: (13.9, 2.9), 5: (15.6, 6.3), 6: (13.85, 3.75),
       7: (12.5, 4.05), 8: (11.35, 7.1), 9: (17.35, 7.1), 10: (14.4, 8.3)}
for n, (mx, my) in POS.items():
    v.marca(n, mx, my, AZUL)
filas = [["#", "Muro", "Largo", "Colinda con", "Regla", "Hoy en el Excel"],
         ["1", "Poniente del vestíbulo", "0,80 + 0,26 = 1,06", "corredor / vestíbulo", "exterior", "0,26 en B.5.2"],
         ["2", "Poniente del Baño 02", "0,47 + 0,60 + 0,47 = 1,54", "corredor / baño", "baño", "B.5.1 <b>y</b> B.5.2"],
         ["3", "Oriente del Baño 01", "2,48 (+0,12)", "exterior / baño", "baño", "B.5.1"],
         ["4", "Norte achurado", "≈ 3,24 (a escala)", "existente / baño", "baño", "—"],
         ["5", "Baños / Guardar", "1,50 + 0,12 + 3,38 = 5,00", "Guardar / baños", "baño", "—"],
         ["6", "Baño 01 / vestíbulo y Baño 02", "2,48 (con puerta 0,75)", "baño / baño", "baño", "—"],
         ["7", "Vestíbulo / Baño 02", "0,75 + puerta 0,75 = 1,50", "vestíbulo / baño", "baño", "0,75 en B.5.2"],
         ["8", "Poniente del Guardar", "0,80 + 1,28 = 2,08", "corredor", "exterior", "B.4.1 (4,2)"],
         ["9", "Oriente del Guardar", "1,96 + 0,12 = 2,08", "exterior", "exterior", "B.4.1 (4,2)"],
         ["10", "Guardar / Sala 01", "5,00", "Sala 01", "interior", "B.4.2 (5)"]]
filas = [[Paragraph(str(x), CUc) for x in f] for f in filas]
t = Table(filas, colWidths=[16, 100, 100, 72, 52, 78])
t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, HexColor("#C8C8C8")), ("BACKGROUND", (0, 0), (-1, 0), HexColor("#EDEDED")),
                       ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
xr = MG + 360
_, th = t.wrap(W - MG - xr, 1000); t.drawOn(c, xr, H - MG - 46 - th)
columna(c, [
    ("Lo que encontré en B.5.1 y B.5.2",
     "• El muro 2 (1,54) está en las dos partidas: B.5.1 = 1,54 + 2,48 y B.5.2 = 0,75 + 0,26 + <b>1,54</b>.<br/>"
     "• Los muros 4, 5 y 6 no están en ninguna partida (≈ 10,7 ml).<br/>"
     "• Con la regla, 2, 3 y 4 serían «baño» aunque den hacia afuera (en San Miguel, E.1.1: todos los muros del "
     "baño, a 73.000). Si Luciano los quiere como exterior, se queda como lo tienes.", F_OJO),
    ("Cielo y piso (B.5.4 y B.5.5 = 5 × 3,6)",
     "El plano no trae 3,60 en este bloque: el fondo de los baños es <b>2,48</b>. Interior: 5,00 × 2,48 = 12,40 m² "
     "con los tabiques; por recinto, Baño 01 3,38 × 2,48 = 8,38 + Baño 02 1,50 × 1,30 (a escala) = 1,95 + "
     "vestíbulo 1,50 × 1,06 = 1,59 = 11,92 m². En el porcelanato de muro usaste 1,54 para el Baño 02 (6,08): es "
     "la cadena exterior, con el muro.", F_CUENTA),
], xr, H - MG - 52 - th, W - MG - xr)
y = leyenda(c, MG, H - MG - 56 - v.h, [(CELESTE, "exterior (85.000)", None), (VERDE, "baño (73.000)", None),
                                        (NARANJA, "interior (76.000)", None)])
pie(c, 4); c.showPage()

# ---------------------------------------------------------------- 5. Salas y textos
titulo(c, "4 · Salas y Guardar: listas · textos por ordenar",
       "Las cantidades de B.1 a B.4 están completas con tu criterio (muros h = 3,00, ventanas × 1,70). Solo quedan textos.")
columna(c, [
    Paragraph("Observaciones que ya no dicen lo que hace la fórmula", SUB),
    Paragraph("• B.1.1, B.2.1, B.3.1: dicen «por 2,40 de alto»; la fórmula usa 3.<br/>"
              "• B.3.8: dice «2 ventanas de 1,80 × 0,60 + vidrio fijo»; la fórmula es 4,2 × 1,7 (2 × 1,80 + 0,60 = "
              "4,20 ml). B.1.9 y B.2.9 ya no tienen observación: siguen la misma regla (6,60 y 4,60 ml).<br/>"
              "• B.1.2: dice «el muro del baño se agrega en la partida del baño», pero en el plano v3 la Sala 01 "
              "no toca baños: al norte tiene el Guardar, y ese muro está en B.4.2 (muro 10 de la página 4).<br/>"
              "• B.5.4 y B.5.5: dicen «Igual a B.5.3», que ya no existe.<br/>"
              "• C.3.1: dice «Igual a C.2.1», que ya no existe (la techumbre del corredor pasó a D.3).<br/>"
              "• D.2.2: dice «Igual que D.2.2» (debería ser D.2.1). D.3.2 y D.3.3 traen observaciones copiadas.", CU),
    Paragraph("Rótulos de subtotales", SUB),
    Paragraph("• Fila 106 dice «Subtotal D.2» y es el de D.3. Fila 110 dice «Subtotal D.3» y es el de D.4. Las sumas "
              "están bien (F111 toma las cuatro).", CU),
    Paragraph("Para Luciano", SUB),
    Paragraph("• La pregunta 13 (aguas lluvia 1,5 o 3,5 M) se responde con su propio San Miguel: F.2.3 suma "
              "1.500.000 + 2.000.000 de hojalatería de salas.", CU),
    ("Si quieres, lo ordeno yo", "Puedo corregir solo estos textos (observaciones y rótulos), sin tocar ninguna "
     "cantidad. O los dejas para cuando cierres las partidas.", F_PRUEBA),
], MG, H - MG - 46, W - 2 * MG)
pie(c, 5)
c.save()
print("ok", SALIDA)

"""Plano del piso 2 de la casona, a escala, con las medidas interiores que tomó Gonza el 08-oct-2026. Las ventanas,
las puertas y la escalera salen del video de terreno del mismo día.

Uso: python -I armar_croquis_piso2.py <video_piso2.mp4> <carpeta_trabajo> <salida.pdf>
Necesita imageio-ffmpeg (trae su propio ffmpeg) para sacar los cuadros de la página de fotos.
Coordenadas en metros, a caras interiores: x crece del frente (Gran Avenida) hacia el fondo; y crece del lado del
pasillo lateral al de la calle lateral, igual que el plano San Miguel v5. Los muros interiores se dibujan de 15 cm
(supuesto). El plano no trae norte: los lados se nombran por lo que tienen al frente.
"""
import os
import subprocess
import sys

import imageio_ffmpeg
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

VIDEO, TRABAJO, SALIDA = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(TRABAJO, exist_ok=True)

pdfmetrics.registerFont(TTFont("Dv", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DvB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("Dv", normal="Dv", bold="DvB", italic="Dv", boldItalic="DvB")

ANCHO, ALTO = 842, 595  # A4 apaisado
MARGEN = 28
NEGRO, ROJO, AZUL, GRIS = HexColor("#000000"), HexColor("#C62828"), HexColor("#1F3A5F"), HexColor("#6B6B6B")
CELESTE, AMARILLO, MADERA = HexColor("#9CC3E6"), HexColor("#FFF4CC"), HexColor("#8D5524")
CUERPO = ParagraphStyle("cuerpo", fontName="Dv", fontSize=8.4, leading=10.8)
CUERPO_CH = ParagraphStyle("cuerpo_ch", parent=CUERPO, fontSize=7.8, leading=9.8)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def area(pts):
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2


def n(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


# ---------------------------------------------------------------- planta (medidas de Gonza, 08-oct)
M = 0.15                              # muro interior (supuesto)
FONDO = 4.1 + M + 3.3                 # pieza 1 + muro + pieza 2
X3 = 5.1 + M                          # comienzo de la pieza 3
X4 = X3 + 1.5 + M                     # comienzo de la pieza 4
X4F = X4 + 3.3                        # fin de la pieza 4: muro de fondo de la fila del pasillo lateral
X5 = X4F + M                          # comienzo de la pieza 5
LARGO = X5 + 3.0
Y5 = FONDO - 4.9                      # la pieza 5 llega hasta la calle lateral
Y5N = Y5 + 2.5                        # a los 2,5 m se angosta a 2,0 m por el baño
XB1 = LARGO - 2.0 - M                 # muro derecho del baño
XB0 = XB1 - 3.2
YB0 = FONDO - 2.2
YP = 4.1 + M                          # comienzo del pasillo, bajo la fila del pasillo lateral
PASILLO_A = rect(X3, YP, X3 + 1.4, YP + 2.0)          # 2,00 × 1,40, junto a la escalera
PASILLO_B = rect(X3 + 1.4, YP, X4F, YP + 1.0)         # 1,00 × 3,60, hacia el baño
XE1 = XB0 - M                                          # la escalera llega hasta el muro del baño
HUECO = [(X3, YP + 2.0), (X3 + 1.4, YP + 2.0), (X3 + 1.4, YP + 1.0), (XE1, YP + 1.0), (XE1, FONDO), (X3, FONDO)]
TRAMO1 = (X3, FONDO - 0.9, XE1 - 1.0, FONDO)           # tramo largo: viene de abajo de la pieza 2
DESCANSO = (XE1 - 1.0, FONDO - 1.1, XE1, FONDO)
TRAMO2 = (XE1 - 1.0, YP + 1.0, XE1, FONDO - 1.1)       # gira a la izquierda y llega al pasillo
CONTORNO = [(0, 0), (X4F, 0), (X4F, Y5), (LARGO, Y5), (LARGO, FONDO), (0, FONDO)]
TECHO_AGREGADO = rect(X4F, 0, LARGO, Y5)
BALCON = [(0, 0.56), (-0.93, 0.56), (-1.70, 1.03), (-1.70, 3.20), (-0.93, 3.66), (0, 3.66)]  # forma del bow window

# id, nombre, polígono, medidas, cómo se reconoce, punto del rótulo (None = centro)
PIEZAS = [
    ("1", "Pieza 1", rect(0, 0, 5.1, 4.1), "5,10 × 4,10", "muros oscuros y un muro entero de espejos", None),
    ("2", "Pieza 2", rect(0, YP, 5.1, FONDO), "5,10 × 3,30", "rosada, esquina sobre el pórtico", None),
    ("3", "Pieza 3", rect(X3, 0, X3 + 1.5, 4.1), "1,50 × 4,10", "blanca, cielo oscuro", None),
    ("4", "Pieza 4", rect(X4, 0, X4F, 4.1), "3,30 × 4,10", "antes rotulada «baño»", None),
    ("5", "Pieza 5", [(X5, Y5), (LARGO, Y5), (LARGO, FONDO), (XB1 + M, FONDO), (XB1 + M, Y5N), (X5, Y5N)],
     "3,00 × 4,90; 2,00 en el tramo junto al baño", "antes piezas 5 y 6", ((X5 + LARGO) / 2, (Y5 + Y5N) / 2)),
    ("B", "Baño", rect(XB0, YB0, XB1, FONDO), "3,20 × 2,20", "antes rotulado «pieza 4»", ((XB0 + XB1) / 2 + 0.4, None)),
]
SUP_PASILLO = 2.0 * 1.4 + 1.0 * 3.6  # medidas de Gonza
SUP_TOTAL = sum(area(p[2]) for p in PIEZAS) + SUP_PASILLO

# ventanas: id, extremos sobre el muro exterior, lado del rótulo, certeza, qué es
VENTANAS = [
    ("V1", (0, 1.8), (0, 2.8), "O", "segura", "puerta-ventana al balcón"),
    ("V2", (1.2, 0), (1.9, 0), "N", "segura", "ventana alta"),
    ("V3", (3.3, 0), (4.0, 0), "N", "segura", "ventana alta"),
    ("V4", (0, 5.3), (0, 6.6), "O", "segura", "ventana doble, con reja"),
    ("V5", (1.0, FONDO), (1.7, FONDO), "S", "segura", "ventana angosta, con reja"),
    ("V6", (X3 + 0.35, 0), (X3 + 1.15, 0), "N", "segura", "ventana con montante"),
    ("V7", (XB0 + 1.0, FONDO), (XB0 + 1.8, FONDO), "S", "probable", "ventana alta"),
    ("V8", (X4 + 1.3, 0), (X4 + 2.1, 0), "N", "por confirmar", "ventana con reja"),
    ("V9", (X4F, 0.9), (X4F, 1.7), "E", "por confirmar", "ventana alta con reja"),
    ("V10", (LARGO, Y5 + 0.65), (LARGO, Y5 + 1.45), "E", "por confirmar", "ventana alta con reja"),
    ("V11", (LARGO, FONDO - 1.55), (LARGO, FONDO - 0.75), "E", "por confirmar", "ventana"),
    ("V12", (X3 + 0.35, FONDO), (X3 + 0.95, FONDO), "S", "probable", "ventana de la escalera"),
]
# puertas: id, muro horizontal ("h", y) o vertical ("v", x), desde, hasta (la bisagra va en «desde» si es "h" y en
# «hasta» si es "v"), hacia dónde abre, certeza, une
PUERTAS = [
    ("P1", "v", 5.1 + M / 2, 3.0, 3.8, "der", "por confirmar", "pieza 1 y pieza 3"),
    ("P2", "v", 5.1 + M / 2, YP + 0.25, YP + 1.05, "izq", "segura", "pieza 2 y pasillo"),
    ("P3", "h", 4.1 + M / 2, 2.0, 2.8, "abajo", "segura", "pieza 1 y pieza 2"),
    ("P4", "h", 4.1 + M / 2, X3 + 0.35, X3 + 1.15, "arriba", "segura", "pieza 3 y pasillo"),
    ("P5", "h", YB0 - 0.05, XB0 + 0.4, XB0 + 1.2, "abajo", "probable", "baño y pasillo"),
    ("P6", "h", 4.1 + M / 2, X4 + 1.3, X4 + 2.1, "arriba", "probable", "pieza 4 y pasillo"),
    ("P7", "v", X4F + M / 2, YP + 0.1, YP + 0.8, "der", "probable", "pieza 5 y pasillo"),
]

# cuadros del video para la página de fotos: segundo, rótulo
CUADROS = [
    (36.0, "Pieza 1: puerta-ventana al balcón (V1) y el muro de espejos"),
    (38.5, "Pieza 1: ventana alta hacia el pasillo lateral (V2 o V3), vidrio roto"),
    (54.0, "Balcón: piso de baldosa y reja de la puerta-ventana"),
    (23.5, "Pieza 2: ventana angosta (V5) y ventana doble (V4)"),
    (19.3, "Pieza 3, vista desde el pasillo: ventana con montante (V6)"),
    (70.5, "Pieza 3: cielo oscuro con cables cortados"),
    (79.0, "Cubículos y piso cerámico: ¿baño o pieza 4?"),
    (82.5, "Lavamanos y ventana con reja: ¿baño o pieza 4?"),
    (92.5, "Pieza 5: ventana alta con reja"),
    (12.5, "Escalera: descanso y giro a la izquierda"),
    (97.6, "Pasillo hacia el frente; al fondo, la pieza 2"),
]


def sacar_cuadros():
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    rutas = []
    for t, _ in CUADROS:
        ruta = os.path.join(TRABAJO, f"cuadro_{t:05.1f}.jpg")
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", VIDEO, "-frames:v", "1",
                        "-q:v", "3", "-y", ruta], check=True)
        rutas.append(ruta)
    return rutas


c = canvas.Canvas(SALIDA, pagesize=(ANCHO, ALTO))
c.setTitle("Casona San Miguel — piso 2 a escala")


def pie(k):
    c.setFont("Dv", 7)
    c.setFillColor(GRIS)
    c.drawString(MARGEN, 14, "Obra San Miguel · piso 2 de la casona: medidas de Gonza y video de terreno del 08-10-2026")
    c.drawRightString(ANCHO - MARGEN, 14, f"página {k} de 3")
    c.setFillColor(NEGRO)


def titulo(t, sub):
    c.setFont("DvB", 15)
    c.setFillColor(AZUL)
    c.drawString(MARGEN, ALTO - 40, t)
    c.setFont("Dv", 9)
    c.setFillColor(GRIS)
    c.drawString(MARGEN, ALTO - 55, sub)
    c.setFillColor(NEGRO)


# ---------------------------------------------------------------- página 1: la planta
titulo("Casona — piso 2 a escala", "Con tus medidas interiores del 08-oct. Orientado igual que el plano San Miguel v5 "
       "(frente a la izquierda, pasillo lateral arriba).")
ESC = 32.0  # puntos por metro
OX, OY = MARGEN + 20 + 1.9 * ESC, 190


def P(x, y):
    return OX + x * ESC, OY + (FONDO - y) * ESC


def poligono(pts, grosor=1.0, relleno=None, color=NEGRO, punteada=False, trazo=True):
    cam = c.beginPath()
    cam.moveTo(*P(*pts[0]))
    for pt in pts[1:]:
        cam.lineTo(*P(*pt))
    cam.close()
    c.setLineWidth(grosor)
    c.setStrokeColor(color)
    c.setDash(3, 2.5) if punteada else c.setDash()
    if relleno is not None:
        c.setFillColor(relleno)
    c.drawPath(cam, stroke=1 if trazo else 0, fill=0 if relleno is None else 1)
    c.setDash()
    return cam


def linea(x0, y0, x1, y1, grosor, color=NEGRO):
    c.setStrokeColor(color)
    c.setLineWidth(grosor)
    c.line(*P(x0, y0), *P(x1, y1))


# techo del agregado de 1 piso, al fondo: achurado
c.saveState()
cam = poligono(TECHO_AGREGADO, 0.6, relleno=HexColor("#F2F2F2"), color=GRIS, punteada=True)
c.clipPath(cam, stroke=0, fill=0)
c.setStrokeColor(HexColor("#C8C8C8"))
c.setLineWidth(0.5)
x0, y0 = P(X4F, Y5)
for k in range(0, 260, 9):
    c.line(x0 + k - 120, y0, x0 + k, y0 + 120)
c.restoreState()
c.setFillColor(GRIS)
c.setFont("Dv", 6.2)
for k, txt in enumerate(("Techo del agregado de 1 piso", "(abajo, la sala de 2,50 m)")):
    c.drawCentredString(*P((X4F + LARGO) / 2 + 0.15, Y5 - 0.75 + 0.3 * k), txt)

# pasillo y hueco de la escalera
poligono(PASILLO_A, 0.8, relleno=AMARILLO)
poligono(PASILLO_B, 0.8, relleno=AMARILLO)
poligono(HUECO, 0.8, relleno=HexColor("#F3E6DA"))
# piezas: cada una con su línea; entre dos piezas vecinas queda el muro de 15 cm
for _, _, pts, _, _, _ in PIEZAS:
    poligono(pts, 1.3, relleno=white)
# muro exterior
poligono(CONTORNO, 3.2)
# balcón
poligono(BALCON, 1.2, relleno=HexColor("#E6E6E6"))

# escalera: tramos con sus peldaños y el sentido de subida
for tramo, eje in ((TRAMO1, "x"), (TRAMO2, "y")):
    poligono(rect(*tramo), 0.9, relleno=white)
    x0, y0, x1, y1 = tramo
    paso = 0.26
    if eje == "x":
        x = x0 + paso
        while x < x1 - 0.01:
            linea(x, y0, x, y1, 0.5)
            x += paso
    else:
        y = y1 - paso
        while y > y0 + 0.01:
            linea(x0, y, x1, y, 0.5)
            y -= paso
poligono(rect(*DESCANSO), 0.9, relleno=white)
ym, xm = (TRAMO1[1] + TRAMO1[3]) / 2, (TRAMO2[0] + TRAMO2[2]) / 2
linea(X3 + 0.15, ym, xm, ym, 1.1, ROJO)
linea(xm, ym, xm, YP + 1.2, 1.1, ROJO)
fx, fy = P(xm, YP + 1.2)
c.setFillColor(ROJO)
flecha = c.beginPath()
flecha.moveTo(fx, fy + 5)
flecha.lineTo(fx - 3.5, fy - 3)
flecha.lineTo(fx + 3.5, fy - 3)
flecha.close()
c.drawPath(flecha, stroke=0, fill=1)
c.setFont("DvB", 6.4)
c.drawString(*P(X3 + 0.1, YP + 2.3), "sube")
# baranda del hueco, salvo donde llega la escalera
linea(X3, YP + 2.0, X3 + 1.4, YP + 2.0, 2.2, MADERA)
linea(X3 + 1.4, YP + 1.0, X3 + 1.4, YP + 2.0, 2.2, MADERA)

# ventanas
for vid, (ax, ay), (bx, by), lado, certeza, _ in VENTANAS:
    p0, p1 = P(ax, ay), P(bx, by)
    color = CELESTE if certeza != "por confirmar" else GRIS
    c.setFillColor(white)
    c.setStrokeColor(color)
    c.setLineWidth(1.3)
    c.setDash() if certeza != "por confirmar" else c.setDash(2, 1.5)
    if ay == by:  # muro horizontal
        c.rect(p0[0], p0[1] - 2.2, p1[0] - p0[0], 4.4, stroke=0, fill=1)
        c.line(p0[0], p0[1] - 1.4, p1[0], p1[1] - 1.4)
        c.line(p0[0], p0[1] + 1.4, p1[0], p1[1] + 1.4)
        tx, ty = (p0[0] + p1[0]) / 2, p0[1] + (9 if lado == "N" else -14)
    else:
        c.rect(p0[0] - 2.2, p1[1], 4.4, p0[1] - p1[1], stroke=0, fill=1)
        c.line(p0[0] - 1.4, p0[1], p1[0] - 1.4, p1[1])
        c.line(p0[0] + 1.4, p0[1], p1[0] + 1.4, p1[1])
        # a la derecha del muro: al frente queda por dentro, para no taparse con el balcón
        tx, ty = p0[0] + 14, (p0[1] + p1[1]) / 2 - 3
    c.setDash()
    c.setFillColor(HexColor("#1F6FB2") if certeza != "por confirmar" else GRIS)
    c.setFont("DvB", 7)
    c.drawCentredString(tx, ty, vid)

# puertas: hueco en el muro, hoja y arco de apertura
for pid, sentido, eje, a, b, abre, certeza, _ in PUERTAS:
    color = NEGRO if certeza != "por confirmar" else GRIS
    c.setStrokeColor(color)
    c.setLineWidth(0.7)
    c.setFillColor(white)
    if sentido == "h":
        h, q = P(a, eje), P(b, eje)
        r = q[0] - h[0]
        c.rect(h[0], h[1] - 2.4, r, 4.8, stroke=0, fill=1)
        s = 1 if abre == "arriba" else -1
        c.setDash(2, 1.5) if certeza == "por confirmar" else c.setDash()
        c.line(h[0], h[1], h[0], h[1] + s * r)
        c.arc(h[0] - r, h[1] - r, h[0] + r, h[1] + r, 0, 90 * s)
        tx, ty = h[0] + r / 2 + 1, h[1] + (-10 if s == 1 else 5)
    else:
        h, q = P(eje, b), P(eje, a)
        r = q[1] - h[1]
        c.rect(h[0] - 2.4, h[1], 4.8, r, stroke=0, fill=1)
        c.setDash(2, 1.5) if certeza == "por confirmar" else c.setDash()
        if abre == "der":
            c.line(h[0], h[1], h[0] + r, h[1])
            c.arc(h[0] - r, h[1] - r, h[0] + r, h[1] + r, 0, 90)
            tx = h[0] - 9
        else:
            c.line(h[0], h[1], h[0] - r, h[1])
            c.arc(h[0] - r, h[1] - r, h[0] + r, h[1] + r, 90, 90)
            tx = h[0] + 9
        ty = h[1] + r / 2 - 3
    c.setDash()
    c.setFillColor(color)
    c.setFont("DvB", 6.5)
    c.drawCentredString(tx, ty, pid)


def renglones(texto, ancho, fuente, tam):
    salida, actual = [], ""
    for w in texto.split():
        if c.stringWidth((actual + " " + w).strip(), fuente, tam) > ancho:
            salida.append(actual)
            actual = w
        else:
            actual = (actual + " " + w).strip()
    return salida + [actual]


# rótulos de las piezas: número, nombre, medidas y superficie
for pid, nombre, pts, medidas, _, punto in PIEZAS:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    lx = punto[0] if punto and punto[0] is not None else (min(xs) + max(xs)) / 2
    ly = punto[1] if punto and punto[1] is not None else (min(ys) + max(ys)) / 2
    cx, cy = P(lx, ly)
    c.setFillColor(AZUL)
    c.circle(cx, cy + 12, 7.5, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("DvB", 8)
    c.drawCentredString(cx, cy + 9.2, pid)
    c.setFillColor(NEGRO)
    c.setFont("DvB", 7.2)
    c.drawCentredString(cx, cy - 4, nombre)
    ancho = (max(xs) - min(xs)) * ESC - 6
    lineas = renglones(medidas, ancho, "Dv", 6.4) + [f"{n(area(pts), 1)} m²"]
    c.setFont("Dv", 6.4)
    for k, txt in enumerate(lineas):
        c.drawCentredString(cx, cy - 12 - 7.6 * k, txt)
# pasillo
c.setFont("DvB", 7.2)
c.drawCentredString(*P(X3 + 1.4 + 0.85, YP + 0.45), "Pasillo")
c.setFont("Dv", 6.2)
c.drawCentredString(*P(X3 + 1.4 + 0.85, YP + 0.75), "1,00 × 3,60")
c.drawCentredString(*P(X3 + 0.7, YP + 1.65), "2,00 × 1,40")
bx, by = P(-0.85, 2.1)
c.saveState()
c.translate(bx, by)
c.rotate(90)
c.setFont("DvB", 6.4)
c.drawCentredString(0, -2, "Balcón")
c.restoreState()

# entorno (sin flecha de norte: el plano no lo trae)
c.setFont("Dv", 7.5)
c.setFillColor(GRIS)
gx, gy = P(-2.25, FONDO / 2)
c.saveState()
c.translate(gx, gy)
c.rotate(90)
c.drawCentredString(0, 0, "FRENTE · Gran Avenida")
c.restoreState()
c.drawCentredString(P(X4F / 2, 0)[0], P(X4F / 2, 0)[1] + 20, "Lado del pasillo lateral (futura Sala 01)")
c.drawCentredString(P(LARGO / 2, FONDO)[0], P(LARGO / 2, FONDO)[1] - 26,
                    "Lado de la calle lateral: patio pavimentado (futuras salas 05 a 07)")
ex, ey = P(LARGO, FONDO / 2)
c.saveState()
c.translate(ex + 26, ey)
c.rotate(-90)
c.drawCentredString(0, 0, "Fondo: agregado de 1 piso y patio")
c.restoreState()

# escala gráfica
sx, sy = P(0, FONDO)[0], OY - 62
c.setLineWidth(0.6)
c.setStrokeColor(NEGRO)
for k in range(5):
    c.setFillColor(NEGRO if k % 2 == 0 else white)
    c.rect(sx + k * ESC, sy, ESC, 4, stroke=1, fill=1)
c.setFillColor(NEGRO)
c.setFont("Dv", 6.5)
for k in range(6):
    c.drawCentredString(sx + k * ESC, sy - 9, str(k))
c.drawString(sx + 5 * ESC + 8, sy - 9, "m")

# columna derecha
xd, ancho_d = 590, ANCHO - MARGEN - 590
bloques = [
    ("Medidas", f"Interiores, en metros: las que tomaste el 08-oct. Los muros interiores van de 15 cm (supuesto). "
     f"Superficie útil: {n(SUP_TOTAL, 1)} m², con el baño y el pasillo."),
    ("Lo que acomodé yo", "Tus medidas no dicen dónde va cada cosa. El pasillo, la escalera y el baño en el rincón de "
     "la pieza 5 los ubiqué yo, para que todo calce. Ventanas y puertas salen del video: las grises están por "
     "confirmar."),
    ("Calza con el piso 1", f"Así dibujado, el piso 2 mide {n(LARGO)} × {n(FONDO)} m por dentro, y el piso 1, "
     "13,65 × 7,70 m: la diferencia son los muros. El rincón del fondo sin piso 2 queda sobre la sala de 2,50 m del "
     "piso 1."),
    ("Para confirmar", "<b>1.</b> En el video vi una puerta de la pieza 1 al pasillo, pero con estas medidas no lo "
     "toca. ¿Se entra por la pieza 2 o por la 3?<br/><b>2.</b> ¿El tramo angosto de la pieza 5 queda junto al baño, "
     "del lado de la calle lateral?<br/><b>3.</b> Las fotos de cubículos y lavamanos (página 3), ¿son del baño o de "
     "la pieza 4?"),
    ("La escalera", "Sube desde el hall del piso 1: el tramo largo empieza bajo la pieza 2, descansa junto al baño y "
     "gira a la izquierda hasta el pasillo."),
]
yd = ALTO - 82
for t, txt in bloques:
    p = Paragraph(f"<b>{t}</b><br/>{txt}", CUERPO_CH)
    w, h = p.wrap(ancho_d, 400)
    c.setFillColor(HexColor("#EAF1F8"))
    c.rect(xd - 5, yd - h - 5, ancho_d + 10, h + 9, stroke=0, fill=1)
    p.drawOn(c, xd, yd - h)
    yd -= h + 16
pie(1)
c.showPage()

# ---------------------------------------------------------------- página 2: medidas hechas y por hacer
titulo("Medidas del piso 2: lo que ya está y lo que falta", "Medidas interiores, en metros. Las marcadas con ✓ me "
       "las pasaste el 08-oct; el resto falta medirlo.")
filas = [["#", "Qué", "Qué medir", "Medida"]]
filas.append(["G1", "Planta completa", "Largo (del frente al fondo) y ancho, por dentro",
              f"{n(LARGO)} × {n(FONDO)}: sale de tus medidas con muros de 15 cm. Confírmalo si puedes"])
filas.append(["G2", "Alto", "Del piso al cielo, de cada pieza", ""])
for pid, nombre, _, medidas, como, _ in PIEZAS:
    filas.append([pid, f"{nombre} ({como})", "Largo × ancho", f"{medidas} ✓"])
filas.append(["Pa", "Pasillo", "Largo × ancho de cada tramo", "2,00 × 1,40 y 1,00 × 3,60 ✓"])
filas.append(["E", "Escalera", "Ancho; peldaños de cada tramo; largo × ancho del descanso", ""])
filas.append(["Bc", "Balcón", "Frente, fondo y alto del antepecho", ""])
for vid, _, _, _, _, que in VENTANAS:
    filas.append([vid, que[0].upper() + que[1:], "Ancho × alto", ""])
for pid, _, _, _, _, _, _, une in PUERTAS:
    filas.append([pid, f"Puerta entre {une}", "Ancho × alto · ¿tiene hoja o falta?", ""])
filas.append(["Ba", "Baño (artefactos)", "Cuántos WC, lavamanos, urinarios y cubículos", ""])
filas.append(["T", "Techo y canal", "Largo del canal (todo el perímetro) y largo × ancho del techo con aleros", ""])
filas.append(["M", "Materiales", "Piso hoy: tablas de madera. Cielo hoy: ¿qué es? ¿Qué va en cada uno?", ""])
estilo = ParagraphStyle("t", parent=CUERPO_CH, fontSize=7.4, leading=8.8)
datos = [[Paragraph(str(x), estilo) for x in f] for f in filas]
mitad = (len(datos) + 1) // 2
for k, parte in enumerate((datos[:mitad], [datos[0]] + datos[mitad:])):
    t = Table(parte, colWidths=[28, 118, 128, 100], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, HexColor("#C8C8C8")),
                           ("BACKGROUND", (0, 0), (-1, 0), HexColor("#EDEDED")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2)]))
    w, h = t.wrap(380, 520)
    t.drawOn(c, MARGEN + k * 400, ALTO - 72 - h)
pie(2)
c.showPage()

# ---------------------------------------------------------------- página 3: fotos para reconocer las piezas
titulo("Cómo reconocer cada pieza", "Cuadros del video del piso 2 (08-oct). El número entre paréntesis es el segundo "
       "del video.")
rutas = sacar_cuadros()
cols, w_img = 4, 186
h_img = w_img * 480 / 848
x0, y0 = MARGEN, ALTO - 78
for k, (ruta, (t, rot)) in enumerate(zip(rutas, CUADROS)):
    col, fila = k % cols, k // cols
    x = x0 + col * (w_img + 10)
    y = y0 - fila * (h_img + 36) - h_img
    c.drawImage(ruta, x, y, w_img, h_img)
    p = Paragraph(f"{rot} <font color='#6B6B6B'>({int(t // 60)}:{t % 60:04.1f})</font>", CUERPO_CH)
    _, hp = p.wrap(w_img, 40)
    p.drawOn(c, x, y - hp - 2)
pie(3)
c.save()
print("guardado:", SALIDA)
print(f"planta {n(LARGO)} × {n(FONDO)} m · superficie útil {n(SUP_TOTAL)} m²")
for pid, nombre, pts, medidas, _, _ in PIEZAS:
    print(f"  {nombre}: {medidas} → {n(area(pts))} m²")

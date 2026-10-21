"""Guía en PDF para leer los 5 planos de la obra Daniel Barrera, con las láminas de instalaciones
superpuestas sobre la de arquitectura y las inconsistencias entre láminas.

Uso: python -I armar_guia_planos.py <carpeta_planos> <carpeta_trabajo> <salida.pdf>
Las coordenadas de recortes y números están en puntos de cada lámina tal como se ve (página rotada),
las mismas que usa get_pixmap(clip=...). En las superposiciones, las coordenadas son las de arquitectura.
"""
import os
import sys

import numpy as np
import pymupdf
from PIL import Image
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable, Paragraph, Table, TableStyle

PLANOS, TRABAJO, SALIDA = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(TRABAJO, exist_ok=True)

pdfmetrics.registerFont(TTFont("Dv", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DvB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("Dv", normal="Dv", bold="DvB", italic="Dv", boldItalic="DvB")

ANCHO, ALTO = 842, 595  # A4 apaisado
MARGEN = 28
ROJO, AZUL, GRIS = HexColor("#C62828"), HexColor("#1F3A5F"), HexColor("#6B6B6B")
MAGENTA = HexColor("#D91A73")
FONDO_CUENTA, FONDO_PRUEBA, FONDO_OJO = HexColor("#EAF1F8"), HexColor("#FFF4CC"), HexColor("#FCE4EC")

CUERPO = ParagraphStyle("cuerpo", fontName="Dv", fontSize=8.4, leading=10.9, alignment=TA_LEFT)
CUERPO_CH = ParagraphStyle("cuerpo_ch", parent=CUERPO, fontSize=7.8, leading=9.8)
SUBT = ParagraphStyle("subt", fontName="DvB", fontSize=10.5, leading=13, textColor=AZUL, spaceAfter=2)
TIT_CAJA = ParagraphStyle("tit_caja", fontName="DvB", fontSize=9, leading=11.5, textColor=AZUL)

LAMINAS = {n: pymupdf.open(os.path.join(PLANOS, f"{n}.pdf"))[0]
           for n in ("arquitectura", "agua_potable", "alcantarillado", "electrico", "gas")}

# Calce de cada planta de instalaciones sobre la de arquitectura: punto_instalación = s · punto_arq + (tx, ty).
# Salió de registrar.py (correlación de los muros) y se revisó a ojo: los muros exteriores coinciden.
# El detalle del baño de visita (1:25) se calzó a mano con los ejes F y 7, que están en la planta y en el detalle.
CALCE = {
    "AF1": ("agua_potable", 0.93333, -51.67, 105.13), "AF2": ("agua_potable", 0.93500, -147.47, 104.67),
    "AC1": ("agua_potable", 0.93781, 1092.58, 59.13), "AC2": ("agua_potable", 0.93625, 992.35, 60.32),
    "AL1": ("alcantarillado", 0.97490, -20.81, 266.56), "AL2": ("alcantarillado", 0.97750, -66.29, 264.87),
    "EA1": ("electrico", 0.95146, 8.79, -166.39), "EA2": ("electrico", 0.95250, -33.91, -166.70),
    "EE1": ("electrico", 0.95198, 8.19, 576.31), "EE2": ("electrico", 0.95250, -33.91, 575.68),
    "GA1": ("gas", 1.48073, -31.30, 27.65),
    "BV": ("arquitectura", 2.0, 3237.3 - 2.0 * 699.0, 1380.6 - 2.0 * 797.85),
}


def recorte(nombre, lamina, clip, dpi):
    ruta = os.path.join(TRABAJO, f"{nombre}.png")
    LAMINAS[lamina].get_pixmap(dpi=dpi, clip=pymupdf.Rect(*clip)).save(ruta)
    return ruta


def gris(pagina, clip, zoom):
    pix = pagina.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(clip), colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(np.float32) / 255


def superposicion(nombre, clave, clip, dpi):
    """Arquitectura en gris y la otra lámina en magenta encima, en la grilla de arquitectura."""
    lamina, s, tx, ty = CALCE[clave]
    z = dpi / 72
    a = gris(LAMINAS["arquitectura"], clip, z)
    e = gris(LAMINAS[lamina], (s * clip[0] + tx, s * clip[1] + ty, s * clip[2] + tx, s * clip[3] + ty), z / s)
    h, w = min(a.shape[0], e.shape[0]), min(a.shape[1], e.shape[1])
    tinta_a, tinta_e = 1 - a[:h, :w], 1 - e[:h, :w]
    img = np.ones((h, w, 3), np.float32) * (1 - tinta_a[..., None] * 0.7)
    img = img * (1 - tinta_e[..., None]) + tinta_e[..., None] * np.array([0.85, 0.10, 0.45])
    ruta = os.path.join(TRABAJO, f"{nombre}.png")
    Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(ruta)
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
    c.drawString(MARGEN, 14, "Guía de lectura de los planos · Obra Daniel Barrera (calle Flamenco N° 23) · "
                             "arquitectura, agua potable, alcantarillado, electricidad y gas")
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
    """Dibuja el recorte en (x, y, w, h) y encima los números, en puntos de la lámina.

    Cada marca es (n, ax, ay[, tx, ty]) en posición fija, o un dict que la pega a un rótulo sin taparlo:
    {"n": 1, "izq": (x0, yc)} · {"der": (x1, yc)} · {"arriba": (xc, y0)} · {"abajo": (xc, y1)}.
    """
    c.drawImage(png, x, y, w, h)
    c.setStrokeColor(HexColor("#B0B0B0"))
    c.setLineWidth(0.6)
    c.rect(x, y, w, h, stroke=1, fill=0)
    s = w / (clip[2] - clip[0])
    hueco = (r + 2.5) / s

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
        mx = min(max(mx, x + r), x + w - r)
        my = min(max(my, y + r), y + h - r)
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


def img(lamina=None, clip=None, dpi=150, marcas=(), sup=None, pie_img=None):
    """Una imagen de una página: recorte de una lámina, o superposición (sup = clave de CALCE) en coords de arquitectura."""
    return dict(lamina=lamina or "arquitectura", clip=clip, dpi=dpi, marcas=list(marcas), sup=sup, pie=pie_img)


LEYENDA = "Gris: arquitectura · magenta: {}"

# ------------------------------------------------------------------------------------- contenido
ARQ = [
    dict(
        n=1, titulo="Planta del 1er nivel", donde="Lámina de arquitectura, arriba a la izquierda.",
        imagenes=[img(clip=(180, 180, 800, 880), dpi=210, marcas=[
            (1, 205, 600, 256, 600), {"n": 2, "izq": (470, 529)}, (3, 418, 600, 446, 600),
            {"n": 4, "izq": (451, 480)}, (5, 525, 520, 500, 503), (6, 395, 380, 378, 347),
            (7, 655, 640, 688, 640), (8, 528, 760, 566, 795), (9, 455, 848, 458, 812),
            {"n": 10, "izq": (341, 716)}])],
        disposicion="lado", ancho_img=420,
        items=[
            ("Muro cortafuego", "Los dos muros laterales dicen «muro cortafuego ladrillo princesa RF-150»: albañilería "
             "que separa de los vecinos y aguanta 150 minutos de fuego. Los otros dos muros también son de albañilería "
             "(así los nombran los detalles)."),
            ("Cotas en centímetros", "Aquí las medidas van en <b>cm</b>, no en metros como en San Miguel: 775 son "
             "7,75 m. Es el ancho interior de la casa."),
            ("Fondo del living", "580 de la ventana del fondo a la barra de la cocina, y 227 de la barra al muro de la "
             "calle: 8,07 m de fondo interior."),
            ("Rótulo del recinto", "Nombre y superficie útil: el living-comedor tiene 48,70 m²."),
            ("Rombo con +2,45", "Aparece en cada recinto. La planta no dice qué es; parece la altura del cielo, pero el "
             "corte da otra (ver la página de inconsistencias)."),
            ("Ventana V2", "El código va en un círculo y el rótulo dice 220x300. Hay dos, en el muro del fondo. El cuadro "
             "de ventanas está en la misma lámina."),
            ("Escalera", "Peldaños numerados del 1 al 16. Parte con 4 peldaños en abanico junto a la puerta y sube "
             "pegada al muro derecho."),
            ("Puerta de acceso", "Rotulada «100x200 V1». V1 es el código de una ventana: es un error de rótulo."),
            ("Ventana V3", "Tres ranuras de 30 cm entre los pilares del frente, aunque el código V3 aparece una vez. "
             "La elevación muestra las tres."),
            ("Cocina", "4,27 × 2,27, con lavaplatos (Lp), encimera de 4 platos y barra con 3 pisos."),
        ],
        cuenta="Interior: 7,75 × 8,07 = 62,54 m².<br/>Cocina: 4,27 × 2,27 = 9,69 m² (rótulo 9,72 ✓).<br/>Living: "
               "62,54 − 9,69 = 52,85, pero el rótulo dice 48,70. Los 4,15 m² de diferencia son la franja de la escalera, "
               "que el rótulo no cuenta como living (Supuesto).",
        prueba="¿Cuántas ventanas V3 hay en el frente y cuánto mide cada una?",
        respuesta="3, de 0,30 × 1,70 m. En la planta se ven tres ranuras entre pilares y la elevación lo confirma.",
    ),
    dict(
        n=2, titulo="Planta del 2do nivel", donde="Lámina de arquitectura, arriba al centro.",
        imagenes=[img(clip=(840, 200, 1425, 860), dpi=210, marcas=[
            (1, 1050, 230, 1050, 252), {"n": 2, "izq": (976, 366)}, {"n": 3, "izq": (1224, 356)},
            {"n": 4, "izq": (1192, 507)}, {"n": 5, "izq": (1113, 330)}, {"n": 6, "izq": (988, 496)},
            {"n": 7, "izq": (1195, 716)}, (8, 1310, 640, 1330, 640), {"n": 9, "izq": (1003, 814)},
            {"n": 10, "izq": (1170, 404)}])],
        disposicion="lado", ancho_img=410,
        items=[
            ("Cadena de vanos", "Arriba: 90 · 140 · 329 · 140 · 99 = 798. Muro, ventana V4, muro (con la V5 de 50 al "
             "medio), V4 y muro."),
            ("Dormitorio de arriba a la izquierda", "Rótulo «13,60 m²», pero sus cotas son 288 × 300."),
            ("Dormitorio de arriba a la derecha", "También «13,60 m²», con cotas 310 × 250."),
            ("Estar", "Otra vez «13,60 m²», con cotas 470 × 387. Los tres rótulos son una copia (ver la cuenta)."),
            ("«W.C 1» y «W.C 2»", "No tienen artefactos y se abren desde un dormitorio: son <b>walk-in closets</b>, no "
             "baños. En las especialidades, «W.C» es el inodoro."),
            ("Baño 1", "Tina (Bll), inodoro y lavamanos. 4,10 m²."),
            ("Baño 2", "Tina esquinera, inodoro y doble lavamanos. 6,30 m². Ahí mismo, agua potable dibuja un termo "
             "de 80 litros."),
            ("Llegada de la escalera", "Peldaños 5 a 16 contra el muro derecho. Por encima de la escalera hay un vacío "
             "sin piso."),
            ("Balcón", "Puerta-ventana V6 de 200 × 200 hacia un balcón de 3,00 × 0,60."),
            ("Puertas", "P2 de 75 (dormitorios) y P3 de 70 (baños y closet). El arco indica hacia dónde abre la hoja."),
        ],
        cuenta="Dormitorio sup. izq.: 2,88 × 3,00 = <b>8,64</b> m² (rótulo 13,60 ✗).<br/>Dormitorio sup. der.: 3,10 × "
               "2,50 = <b>7,75</b> m² (13,60 ✗).<br/>Estar: 4,70 × 3,87 ≈ <b>18,19</b> m² (13,60 ✗).<br/>Baño 1: 2,88 × "
               "1,41 = 4,06 (4,10 ✓). Baño 2: 2,53 × 2,54 = 6,43 (6,30 ✓). W.C 2: 1,52 × 1,40 = 2,13 (2,12 ✓).",
        prueba="¿Qué hay en el recinto «W.C 1» y qué se presupuesta en él?",
        respuesta="Nada de sanitario: es un walk-in closet. Lleva piso, muros, cielo y puerta, sin artefactos.",
    ),
    dict(
        n=3, titulo="Escalera, baño de visita y caseta de lavadora", donde="Lámina de arquitectura, arriba a la derecha "
                                                                           "(detalles a escala 1:25).",
        imagenes=[img(clip=(2450, 170, 2960, 690), dpi=190, marcas=[
            {"n": 1, "izq": (2572, 347)}, {"n": 2, "izq": (2937, 258)}, {"n": 3, "izq": (2517, 500)},
            {"n": 4, "der": (2634, 505)}, {"n": 5, "arriba": (2712, 641)}, {"n": 6, "izq": (2582, 625)},
            {"n": 7, "izq": (2560, 416)}], pie_img="Perfil de la escalera"),
                  img(clip=(2740, 960, 3300, 1530), dpi=170, marcas=[
            {"n": 8, "izq": (3143, 1014)}, (9, 3080, 1050, 3112, 1050), (10, 3270, 1250, 3249, 1250),
            {"n": 11, "izq": (2802, 1190)}, {"n": 12, "izq": (2833, 1400)}],
                      pie_img="Planta de la escalera y del baño de visita")],
        disposicion="arriba",
        items=[
            ("Peldaños", "Del 1 al 16. El 16 es el piso del 2do nivel."),
            ("Huella y contrahuella", "Lupa de detalle: huella de 28 (30 con la nariz) y contrahuella de 18,85 cm."),
            ("Altura del muro alto", "284 cm, bajo la llegada de la escalera."),
            ("Altura del baño", "Los muros del baño miden 240 cm."),
            ("Largo del baño", "266 cm bajo la escalera."),
            ("Piso rebajado", "El baño está 28 cm más abajo que el living, para ganar altura bajo los peldaños."),
            ("Caseta de lavadora", "Nicho de 77 × 189 para la lavadora (Lv), bajo los últimos peldaños."),
            ("Tabiques", "Metalcon 60CA85: perfiles de acero liviano con placas. Dos tabiques cortos separan el baño "
             "de la caseta."),
            ("Puertas", "80x200 a la caseta y 70x200 al baño. <b>Ninguna está en el cuadro de puertas.</b>"),
            ("Muro perimetral", "El baño se apoya en el muro de albañilería de la medianera."),
            ("Tramo recto", "10 huellas de 28 = 280 cm."),
            ("Ancho total", "28 + 28 + 51 + 39 = 146 cm en el abanico de abajo."),
        ],
        cuenta="Altura que sube la escalera: 16 contrahuellas × 18,85 = <b>3,02 m</b>.<br/>El corte pone el piso del 2do "
               "nivel en +3,24 (obra gruesa). Faltan 22 cm: o la contrahuella real es 3,24 ÷ 16 = 20,25 cm, o hay "
               "otra cota que no calza. Es una pregunta para Luciano.",
        prueba="¿Por qué el piso del baño de visita está 28 cm más abajo que el living?",
        respuesta="Porque el baño queda bajo la escalera: al bajar el piso, gana altura para estar de pie bajo los peldaños.",
    ),
    dict(
        n=4, titulo="Cortes: alturas y materiales", donde="Lámina de arquitectura, al centro.",
        imagenes=[img(clip=(1100, 1040, 2620, 1500), dpi=120, marcas=[
            {"n": 1, "izq": (1737, 1108)}, {"n": 2, "izq": (1734, 1170)}, {"n": 3, "izq": (1732, 1305)},
            {"n": 4, "izq": (1733, 1332)}, {"n": 5, "izq": (1737, 1487)}, {"n": 6, "izq": (1236, 1238)},
            {"n": 7, "izq": (1128, 1235)}, {"n": 8, "izq": (1473, 1284)}, {"n": 9, "izq": (1309, 1339)},
            {"n": 10, "izq": (1277, 1170)}])],
        disposicion="arriba",
        items=[
            ("Frontón", "+6,75 y +6,25: el remate de la fachada sobre el techo."),
            ("+5,66", "Rotulado «Piso Obra Gru. 2do Nivel», pero está a la altura del cielo del 2do nivel. Es un error "
             "de rótulo."),
            ("+3,24", "Piso del 2do nivel en obra gruesa."),
            ("+2,76", "Cielo del 1er nivel en obra gruesa."),
            ("±0,00", "Piso terminado del 1er nivel. El terreno natural (N.T.N.) está 9 cm más abajo."),
            ("Panel SIP", "Muros del 2do nivel: panel SIP 2×3\" con OSB de 9,5 mm por ambas caras."),
            ("Cortafuego", "En el 2do nivel, el corte pone 2 planchas de volcanita RF de 15 mm sobre el SIP. La planta, "
             "en cambio, dice ladrillo."),
            ("Pisos", "Flotante de 8 mm en dormitorios y estar; cerámico de 30 × 30 en baños. Todo sobre terciado "
             "estructural."),
            ("Perfil metálico", "Vigas de 150 × 50 × 4 mm bajo el piso del 2do nivel (ver la estructura del piso)."),
            ("Cerchas", "Metalcon 90CA085 y 150CA085 sobre el 2do nivel; el cielo es volcanita (RH en baños)."),
        ],
        cuenta="Alto libre del 1er nivel: 2,76 m. Entrepiso: 3,24 − 2,76 = 0,48 m. Alto del 2do nivel: 5,66 − 3,24 = "
               "2,42 m (el corte acota 238 y 242).<br/>Muros SIP del 2do nivel, brutos: perímetro 2 × (7,98 + 9,35) = "
               "34,66 m × 2,42 = 83,9 m², antes de descontar ventanas.",
        prueba="¿Qué alto tiene el 2do nivel según el corte?",
        respuesta="2,42 m: de +3,24 (piso) a +5,66 (cielo).",
    ),
    dict(
        n=5, titulo="Elevaciones, puertas y ventanas", donde="Lámina de arquitectura: elevaciones a la izquierda y "
                                                             "cuadro de puertas y ventanas abajo a la derecha.",
        imagenes=[img(clip=(50, 1080, 1120, 1520), dpi=110, marcas=[
            (1, 234, 1255, 234, 1270), (2, 75, 1236, 86, 1236), (3, 185, 1450), (4, 306, 1395, 306, 1430),
            (5, 424, 1395, 424, 1430), (6, 446, 1350, 446, 1366), (7, 752, 1420), (8, 731, 1215, 731, 1236)],
                      pie_img="Elevación de la calle (izq.) y del fondo (der.)"),
                  img(clip=(2160, 1560, 3000, 2330), dpi=110, marcas=[
            (9, 2314, 1620, 2314, 1650), (10, 2568, 1805, 2568, 1790), (11, 2563, 1935), (12, 2558, 2160)],
                      pie_img="Detalle de puertas y ventanas")],
        disposicion="arriba",
        items=[
            ("Balcón", "Baranda metálica del balcón del 2do nivel, frente a la puerta-ventana V6."),
            ("Altura del 2do nivel", "304 en fachada, más 30 de losa."),
            ("Revestimiento de piedra", "El 1er nivel de la fachada a la calle va enchapado en piedra. Es una partida "
             "propia, por m²."),
            ("V3", "Las tres ranuras verticales de 30 × 170."),
            ("Puerta de acceso", "Con dos paños laterales de vidrio y un alero de 172 encima."),
            ("Alero de acceso", "172 de largo y 15 de espesor."),
            ("V2 en el fondo", "Dos ventanales de dos hojas en el 1er nivel. Medidos a escala: unos 3,00 de ancho por "
             "2,20 de alto cada uno."),
            ("V4 y V5", "Dos V4 de los dormitorios y la V5 chica del W.C 2."),
            ("P1", "Hoja de 103 más dos laterales de 20: 143 × 202."),
            ("P3 «cantidad 1»", "El cuadro pide una sola P3, pero en la planta hay tres."),
            ("V2 en el detalle", "Un solo paño de 450 × 220, distinto de la planta (220x300) y de la elevación."),
            ("V6", "Puerta-ventana de 200 × 200 al balcón."),
        ],
        cuenta="Cuadro de ventanas (ancho × alto, cm): V1 150×50 · V2 450×220 (?) · V3 30×170 · V4 140×100 · V5 50×40 · "
               "V6 200×200 · V7 150×50.<br/>Puertas: P1 143×202 · P2 75×200 (3) · P3 70×200 (1 en el cuadro, 3 en la "
               "planta).",
        prueba="¿Cuánto mide de ancho la puerta de acceso completa, con sus laterales?",
        respuesta="1,43 m: hoja de 1,03 más dos paños de 0,20 (detalle P1).",
    ),
    dict(
        n=6, titulo="Estructura metálica del piso del 2do nivel", donde="Lámina de arquitectura, arriba a la derecha "
                                                                         "de las plantas.",
        imagenes=[img(clip=(1460, 170, 2140, 930), dpi=170, marcas=[
            {"n": 1, "izq": (1720, 219)}, {"n": 2, "izq": (1567, 248)}, (3, 1745, 520, 1771, 520),
            {"n": 4, "izq": (1628, 393)}, (5, 1800, 650, 1835, 621), (6, 2015, 640, 1972, 693),
            (7, 1580, 785, 1604, 805), {"n": 8, "izq": (1568, 274)}])],
        disposicion="lado", ancho_img=400,
        items=[
            ("Cadeneta", "Perfil «cos 150/50/15/3» cada 80 cm: arriostra las vigas entre sí."),
            ("Modulación", "Las vigas van cada 80 cm (la cadena de arriba repite 80)."),
            ("Viga IPE 450", "Viga de acero laminado al centro, de lado a lado. Es la pieza más pesada de la obra."),
            ("Vigas de 3,91 m", "Perfil 150/50/4 mm, de 3,91 m. Cada rótulo es una viga."),
            ("Vigas de 2,89 m", "Las más cortas, junto al vano de la escalera."),
            ("Vano de la escalera", "Rectángulo sin vigas de 1,04 × 3,84: ahí no hay piso."),
            ("Balcón", "Cinco vigas cortas de 1,325 m que vuelan hacia la calle."),
            ("Perimetrales", "Perfil 150/50/3 mm de 6,00 m que cierra el borde."),
        ],
        cuenta="Vigas 150/50/4 mm: 14 de 3,91 m + 4 de 2,89 + 10 de 2,075 + 5 de 1,325 + 1 de 3,925 = 34 vigas, "
               "<b>101,1 m</b> lineales.<br/>Superficie del piso: 7,98 × 9,35 = 74,6 m², menos el vano de la escalera "
               "1,04 × 3,84 = 4,0 m² → 70,6 m² de terciado.",
        prueba="¿Cada cuánto van las vigas del piso?",
        respuesta="Cada 80 cm.",
    ),
    dict(
        n=7, titulo="Techumbre: cerchas y frontón", donde="Lámina de arquitectura, franja de abajo.",
        imagenes=[img(clip=(200, 1590, 2150, 2384), dpi=95, marcas=[
            (1, 358, 1790, 358, 1806), (2, 546, 1870, 546, 1890), (3, 330, 1966), (4, 330, 2315), (5, 1208, 2240),
            {"n": 6, "izq": (1757, 1737)}, {"n": 7, "izq": (1678, 1808)}, (8, 1789, 1680, 1789, 1700)])],
        disposicion="arriba",
        items=[
            ("Costaneras", "Omega 40 085 sobre las cerchas: ahí se fija la cubierta."),
            ("Perfiles de la cercha", "Cordones de 90CA085 y 150CA085, con puntales y montantes de 60CA085."),
            ("Cercha 1", "Mide 7,98 de largo y tiene dos aguas desiguales (2,30 y 5,53)."),
            ("Frontón 1", "El remate de las fachadas: 2,23 + 5,60, de 0,60 y 1,12 de alto."),
            ("Cruz de San Andrés", "Dos diagonales que arriostran las cerchas, con 2 autoperforantes por unión."),
            ("Planta de cerchas", "12 cerchas C1 cada 80 cm, más un frontón F1 en cada extremo."),
            ("C.S.A.", "4 cruces de San Andrés, en dos paños."),
            ("Medidas", "Techo de 7,98 × 9,35."),
        ],
        cuenta="Superficie en planta del techo: 7,98 × 9,35 = 74,6 m². La cubierta inclinada mide algo más. La pendiente "
               "no está acotada: queda «Pendiente» hasta tener la EETT o preguntarle a Luciano.",
        prueba="¿Cuántas cerchas lleva el techo?",
        respuesta="12 cerchas C1 y 2 frontones F1.",
    ),
]

ESP = [
    dict(
        n=8, titulo="Agua potable superpuesta", donde="Lámina de agua potable (red fría) sobre la de arquitectura.",
        imagenes=[img(clip=(200, 280, 780, 860), dpi=190, sup="AF1", marcas=[
            (1, 436, 845, 436, 818), (2, 547, 735, 547, 750), (3, 318, 495, 318, 512), (4, 290, 300, 270, 305),
            (5, 640, 600, 672, 620), (6, 348, 740, 348, 758)], pie_img=LEYENDA.format("agua fría, 1er nivel")),
                  img(clip=(870, 250, 1400, 880), dpi=190, sup="AF2", marcas=[
            (7, 950, 470, 928, 482), (8, 1250, 680, 1252, 696), (9, 1130, 730, 1158, 718),
            (10, 1360, 570, 1367, 594), (11, 1040, 850, 1057, 828)], pie_img=LEYENDA.format("agua fría, 2do nivel"))],
        disposicion="arriba",
        items=[
            ("Arranque", "«red existente» al frente: la casa se conecta al medidor que ya hay."),
            ("PP-R de 32 mm", "Polipropileno, el tubo más grueso: 6,27 m por el frente y 3,33 m por el costado."),
            ("PP-R de 25 mm", "Corre por el muro izquierdo (8,58 m) hasta el fondo."),
            ("Calefón «C»", "Al fondo, por fuera, junto al muro izquierdo. Lo dibujan agua potable y gas."),
            ("Baño de visita", "Inodoro, lavamanos y lavadora (Mlav) <b>sobre la escalera de arquitectura</b>: es el "
             "baño que la planta no dibuja."),
            ("Lavaplatos", "En la cocina, contra el muro de la calle."),
            ("Baño 1", "Tina (Bll), inodoro (W.C) y lavamanos (Lo). Calza con arquitectura."),
            ("Baño 2", "Tina esquinera, inodoro y dos lavamanos. Calza con arquitectura."),
            ("Termo de 80 litros", "Dibujado en el Baño 2, aparte del calefón."),
            ("Bajada", "Baja la red por el muro derecho hacia el 1er nivel."),
            ("Subida", "Sube la red desde el 1er nivel junto al balcón."),
        ],
        cuenta="PP-R rotulado en la red fría: 32 mm = 6,27 + 3,33 = <b>9,60 m</b> · 25 mm = 8,58 (1er nivel) + 2,20 + "
               "3,33 + 1,40 + 2,65 + 0,88 + 1,20 (2do) = <b>20,24 m</b> · 20 mm = 0,82 + 0,42 = <b>1,24 m</b>.<br/>"
               "Red caliente: 20 mm = 8,50 + 0,82 + 1,50 = 10,82 m · 25 mm = 2,20 + 0,88 + 2,65 + 1,20 = 6,93 m.<br/>"
               "Las subidas y bajadas entre niveles no traen largo: unos 3,2 m cada una (Supuesto).",
        prueba="¿Qué artefactos de agua tiene el baño de visita?",
        respuesta="Inodoro (W.C), lavamanos (Lo) y la lavadora (Mlav).",
    ),
    dict(
        n=9, titulo="Alcantarillado superpuesto", donde="Lámina de alcantarillado sobre la de arquitectura.",
        imagenes=[img(clip=(200, 280, 780, 880), dpi=190, sup="AL1", marcas=[
            (1, 328, 520, 328, 541), (2, 371, 620, 371, 637), (3, 640, 640, 666, 660), (4, 600, 790, 652, 804),
            (5, 690, 860, 681, 832), (6, 250, 600, 234, 583), (7, 342, 860, 342, 832)],
                      pie_img=LEYENDA.format("alcantarillado, 1er nivel")),
                  img(clip=(870, 250, 1400, 880), dpi=190, sup="AL2", marcas=[
            (8, 978, 395, 978, 412), (9, 1064, 395, 1064, 412), (10, 960, 500, 989, 498),
            (11, 1240, 740, 1216, 718), (12, 1380, 640, 1374, 614)], pie_img=LEYENDA.format("alcantarillado, 2do nivel"))],
        disposicion="arriba",
        items=[
            ("C.C.I. existente", "Cámara de inspección que ya existe, en el living."),
            ("PVC 110 al 2,32 %", "Colector bajo el piso: 4,28 m. «p» es la pendiente."),
            ("Baño de visita", "Descarga del inodoro y del lavamanos. Otra vez, sobre la escalera."),
            ("PVC 110 al 3 %", "Sale de la casa hacia la cámara del frente."),
            ("C.I. nuevas", "Cámaras de inspección «ver detalle»: la lámina trae el detalle constructivo."),
            ("Bajada 110", "Tubo vertical que trae las aguas del 2do nivel."),
            ("Hacia la calle", "Unión domiciliaria por calle Flamenco: Ø 110, 5,90 m al 10 %."),
            ("PVC 110 al 1,5 %", "Descarga del inodoro del Baño 1."),
            ("PVC 50", "Descargas de lavamanos y tina."),
            ("Pileta", "Pileta de piso en cada baño (rejilla de desagüe)."),
            ("Baño 2", "Tres descargas que bajan juntas por el rincón."),
            ("Bajada derecha", "Baja por el muro derecho junto a la escalera."),
        ],
        cuenta="PVC 110 rotulado en el 1er nivel: 4,28 + 1,59 + 0,88 + 1,57 + 4,92 = <b>13,24 m</b>. En el 2do: 0,73 + "
               "0,66 + 0,98 + 2,00 + 0,52 + 0,52 = <b>5,41 m</b>.<br/>PVC 50: 0,88 + 0,52 + 1,09 + 1,09 + 2,27 + 0,77 + "
               "0,77 + 2,27 = <b>9,66 m</b> (Supuesto: algunos rótulos se repiten en los dos niveles).",
        prueba="¿Qué pendiente tiene la unión domiciliaria hacia la calle?",
        respuesta="10 %, en 5,90 m de PVC de 110 mm (cuadro «Datos de la U.D. proyectada»).",
    ),
    dict(
        n=10, titulo="Electricidad: alumbrado y tablero", donde="Lámina eléctrica (plantas de alumbrado) sobre la de "
                                                                "arquitectura, y el esquema unilineal.",
        imagenes=[img(clip=(200, 280, 790, 880), dpi=170, sup="EA1", marcas=[
            (1, 375, 395, 375, 412), (2, 346, 615, 346, 633), (3, 512, 770, 512, 790), (4, 465, 575, 465, 593),
            (5, 630, 653, 652, 653)], pie_img=LEYENDA.format("alumbrado, 1er nivel")),
                  img(clip=(870, 250, 1400, 880), dpi=170, sup="EA2", marcas=[
            (6, 1180, 500, 1180, 520), (7, 1300, 682, 1316, 682)], pie_img=LEYENDA.format("alumbrado, 2do nivel")),
                  img(lamina="electrico", clip=(1530, 90, 2150, 680), dpi=150, marcas=[
            (8, 1866, 170, 1866, 189), (9, 1900, 440, 1920, 435), (10, 1872, 590, 1872, 567),
            (11, 1608, 580, 1608, 558)], pie_img="Esquema unilineal")],
        disposicion="arriba",
        items=[
            ("Centro de luz", "Círculo con una cruz. La letra (a) dice qué interruptor lo enciende."),
            ("Centros «b»", "Dos centros con la misma letra: se encienden juntos desde el interruptor rotulado «b»."),
            ("T.D.A.", "Tablero de distribución de alumbrado, en el muro de la calle junto al acceso."),
            ("Equipo de emergencia", "Rectángulo con una cruz en el living."),
            ("Baño de visita", "Centro de luz bajo la escalera: la electricidad también cuenta con ese baño."),
            ("Estar", "Dos centros «g»."),
            ("Escalera", "Un centro de luz sobre el vacío de la escalera."),
            ("Empalme", "A-6 de 25 A. El unilineal dice «proyectado» y la planta, «existente»."),
            ("Diferencial", "2 × 25 A, 30 mA: protege los dos circuitos de enchufes."),
            ("Circuitos", "1 de alumbrado (10 A) y 2 de enchufes (16 A)."),
            ("Tierra", "Barra Cooperweld de 2 × 3 m y conductor de 5,27 mm. Valor medido promedio: 31,3 Ω."),
        ],
        cuenta="Lo que se cuenta en planta: centros, interruptores y enchufes, uno por uno. El <b>cuadro de cargas no "
               "sirve</b>: habla de una «sede» con «bodega» y de 12 fluorescentes de 2×40 W, que esta casa no tiene (ver "
               "inconsistencias).<br/>Alimentador subterráneo: Superflex 10 AWG en conduit de 20 mm, "
               "<b>22 m</b>.",
        prueba="¿Cuántos circuitos tiene el tablero y cuáles pasan por el diferencial?",
        respuesta="3 circuitos. Los 2 de enchufes (16 A) pasan por el diferencial; el de alumbrado (10 A), no.",
    ),
    dict(
        n=11, titulo="Electricidad: enchufes", donde="Lámina eléctrica (plantas de enchufes) sobre la de arquitectura.",
        imagenes=[img(clip=(200, 280, 790, 880), dpi=170, sup="EE1", marcas=[(1, 300, 427, 268, 427), (2, 242, 610, 242, 568), (3, 650, 720, 688, 693), (4, 442, 735, 442, 768)], pie_img=LEYENDA.format("enchufes, "
                                                                                                        "1er nivel")),
                  img(clip=(870, 250, 1400, 880), dpi=170, sup="EE2", marcas=[(2, 1290, 440, 1312, 470), (1, 1300, 560, 1334, 526)], pie_img=LEYENDA.format("enchufes, "
                                                                                                       "2do nivel"))],
        disposicion="lado", ancho_img=520,
        items=[
            ("Enchufe", "Medio círculo negro contra el muro. El de 300 W es el normal; el de 800 W, para artefactos "
             "grandes."),
            ("Número en un círculo", "② o ③ es el circuito al que pertenece ese tramo."),
            ("Baño de visita", "También tiene enchufe en la planta eléctrica."),
            ("Cocina", "Enchufes sobre la encimera, para artefactos de cocina."),
            ("Cómo se presupuesta", "Cada enchufe es una unidad (caja, módulo y tapa) más los metros de canalización "
             "y cable hasta el tablero."),
        ],
        cuenta="El cuadro de cargas dice 10 enchufes de 300 W y 2 de 800 W. Como el cuadro no es de esta casa, los "
               "enchufes se cuentan en la planta (fase de itemizado) y no se copian del cuadro.",
        prueba="¿De dónde se saca la cantidad de enchufes para el presupuesto?",
        respuesta="Contándolos en las plantas de enchufes, no del cuadro de cargas.",
    ),
    dict(
        n=12, titulo="Gas superpuesto", donde="Lámina de gas sobre la de arquitectura, con su isométrica y detalles.",
        imagenes=[img(clip=(100, 20, 790, 860), dpi=160, sup="GA1", marcas=[
            (1, 240, 75, 292, 99), (2, 205, 250, 262, 297), (3, 200, 470, 260, 470), (4, 290, 840, 285, 812),
            (5, 380, 740, 355, 752)], pie_img=LEYENDA.format("gas")),
                  img(lamina="gas", clip=(1380, 40, 2000, 860), dpi=120, marcas=[
            (6, 1600, 160), (7, 1620, 600, 1620, 615), (8, 1470, 760, 1480, 745)], pie_img="Isométrica y detalles")],
        disposicion="lado", ancho_img=470,
        items=[
            ("Cilindros", "Dos de 45 kg al fondo del terreno, con techo de protección."),
            ("Calefón «C»", "Contra el muro del fondo, por fuera."),
            ("Cañería por el muro izquierdo", "Cobre de 1/2\": 4,36 m por el patio y 8,10 m por el muro."),
            ("Tramo del frente", "3,10 m hasta la cocina."),
            ("Futura cocina", "Solo queda el arranque: llave de paso de 1/4 de vuelta y tapagorro. La cocina no se "
             "conecta ahora."),
            ("Isométrica", "Muestra la red en 3D con el largo de cada tramo."),
            ("Detalle de cilindros", "Flexible, regulador y tee de prueba."),
            ("Arranque futuro", "Detalle de cómo queda tapado el punto de la cocina."),
        ],
        cuenta="Cobre 1/2\" rotulado en planta: 0,50 + 4,36 + 8,10 + 3,10 = <b>16,06 m</b>. La isométrica agrega tramos "
               "verticales (0,90 y 0,80): unos 17,8 m en total (Supuesto; confirmar en la isométrica).",
        prueba="¿La cocina queda conectada al gas?",
        respuesta="No: queda solo el arranque tapado con tapagorro, para una conexión futura.",
    ),
]

INC = [
    dict(
        n=13, titulo="Lo que no cuadra (1): el baño de visita", donde="Superposiciones sobre la planta del 1er nivel.",
        imagenes=[img(clip=(560, 470, 780, 840), dpi=260, sup="AF1", marcas=[
            (1, 640, 620, 672, 690), (2, 640, 560, 675, 575), (3, 740, 700, 700, 700)],
                      pie_img=LEYENDA.format("agua potable")),
                  img(clip=(590, 470, 760, 830), dpi=260, sup="BV", marcas=[
            (4, 630, 715, 657, 718), (5, 640, 640, 680, 637), (6, 740, 770, 700, 790)],
                      pie_img=LEYENDA.format("detalle 1:25 del baño, llevado a 1:50"))],
        disposicion="lado", ancho_img=380,
        items=[
            ("Inodoro de la especialidad", "Agua potable, alcantarillado, electricidad y gas ponen un baño completo "
             "donde la planta de arquitectura dibuja la escalera."),
            ("Lavadora", "Mlav en el mismo lugar."),
            ("Escalera libre", "La planta de arquitectura solo dibuja los peldaños, sin tabiques ni puertas debajo."),
            ("Detalle sobre la planta", "Si el detalle 1:25 de la misma lámina se reduce a 1:50 y se calza con los ejes "
             "F y 7, el baño cae justo bajo los peldaños 6 a 13."),
            ("Caseta de lavadora", "Queda bajo los peldaños 11 a 13."),
            ("Abanico", "El arranque de la escalera calza con una diferencia de unos 10 cm."),
        ],
        cuenta="<b>Conclusión:</b> el baño de visita y la caseta sí existen (los traen el detalle y las 4 "
               "especialidades). Lo que falta es dibujarlos en la planta del 1er nivel.<br/><b>Para el presupuesto:</b> "
               "va completo: tabiques metalcon 60CA85, 2 puertas (80×200 y 70×200, fuera del cuadro), inodoro, "
               "lavamanos, llave de lavadora, piso rebajado 28 cm y cerámica.",
        prueba="¿Bajo qué peldaños queda el inodoro del baño de visita?",
        respuesta="Bajo los peldaños 6 a 8, donde el piso rebajado da más altura.",
        ojo=True,
    ),
    dict(
        n=14, titulo="Lo que no cuadra (2): rótulos, nombres y cotas del 2do nivel",
        donde="Planta del 2do nivel de arquitectura.",
        imagenes=[img(clip=(840, 200, 1425, 860), dpi=210, marcas=[
            {"n": 1, "izq": (976, 366)}, {"n": 2, "izq": (1224, 356)}, {"n": 3, "izq": (1192, 507)},
            {"n": 4, "izq": (1028, 696)}, {"n": 5, "izq": (938, 571)}, (6, 860, 560, 857, 520)])],
        disposicion="lado", ancho_img=410,
        items=[
            ("13,60 copiado (1)", "Dormitorio de 2,88 × 3,00 = 8,64 m²."),
            ("13,60 copiado (2)", "Dormitorio de 3,10 × 2,50 = 7,75 m²."),
            ("13,60 copiado (3)", "Estar de 4,70 × 3,87 ≈ 18,19 m²."),
            ("Tres «Dormitorio 1»", "Los tres dormitorios se llaman igual. Para el presupuesto se renombran 1, 2 y 3 "
             "(Supuesto)."),
            ("«W.C» = closet", "W.C 1 y W.C 2 son walk-in closets. En las especialidades «W.C» es el inodoro, y "
             "alcantarillado y gas llaman «Baño 2» al de visita, mientras arquitectura llama así al del 2do nivel."),
            ("Cadena izquierda", "300 + 8 + 149 + 8 + 100 + 8 + 342 = 915 cm interiores. La cadena derecha (250 + 8 + "
             "649 = 907) y la medida a escala dan 907: sobran 8 cm en alguna cota."),
        ],
        cuenta="Los tres rótulos de 13,60 suman 40,80 m², contra 34,58 m² medidos con las cotas (−6,22 m²).<br/>"
               "<b>Para el presupuesto:</b> la superficie de pisos, cielos y pintura se calcula con las cotas, no con "
               "los rótulos.",
        prueba="Si el dormitorio de arriba a la derecha mide 3,10 × 2,50, ¿cuántos m² de piso flotante lleva?",
        respuesta="7,75 m², no 13,60.",
        ojo=True,
    ),
    dict(
        n=15, titulo="Lo que no cuadra (3): ventanas, puertas, alturas y muros",
        donde="Cuadro de puertas y ventanas, elevación del fondo y cortes.",
        imagenes=[img(clip=(2160, 1560, 3000, 2330), dpi=120, marcas=[
            (1, 2563, 1935), (2, 2568, 1805, 2568, 1790), (3, 2314, 1620, 2314, 1650)],
                      pie_img="Detalle de puertas y ventanas"),
                  img(clip=(600, 1080, 1120, 1520), dpi=150, marcas=[(4, 752, 1420)], pie_img="Elevación del fondo")],
        disposicion="arriba",
        items=[
            ("V2: tres medidas", "Planta: dos de «220x300». Detalle: un solo paño de 450 × 220. Elevación del fondo: "
             "dos ventanales de unos 3,00 × 2,20 (medido a escala). Lo más probable es que sean dos de 3,00 × 2,20 "
             "(Supuesto)."),
            ("P3", "El cuadro dice cantidad 1; la planta tiene 3 (W.C 2, Baño 1 y Baño 2)."),
            ("Puerta de acceso", "La planta dice «100x200 V1»; el detalle P1 mide 143 × 202 con laterales."),
            ("Elevación", "Los dos ventanales del fondo, de dos hojas cada uno."),
            ("Rombos de altura", "La planta pone +2,45 (living, cocina, dormitorios y estar) y +2,85 (baños y "
             "closets). Los cortes dan 2,76 de alto en el 1er nivel y 2,42 en el 2do. Ninguno calza."),
            ("+5,66", "El corte lo rotula «piso del 2do nivel», pero es el cielo."),
            ("Cortafuego del 2do nivel", "La planta dice «ladrillo princesa RF-150»; el corte dibuja panel SIP con 2 "
             "planchas de volcanita RF de 15 mm. Cambia mucho el costo."),
            ("Escalera", "16 contrahuellas de 18,85 = 3,02 m, contra +3,24 del piso del 2do nivel."),
        ],
        cuenta="<b>Para el presupuesto:</b> las ventanas se cuentan en la planta y se dimensionan con la elevación. Las "
               "P3 van como 3. El cortafuego del 2do nivel queda «Pendiente» hasta que Luciano diga si es ladrillo o "
               "SIP con volcanita RF.",
        prueba="¿Cuántas puertas P3 se presupuestan y por qué?",
        respuesta="3, porque la planta las dibuja en W.C 2, Baño 1 y Baño 2; el «cantidad 1» del cuadro está mal.",
        ojo=True,
    ),
    dict(
        n=16, titulo="Lo que no cuadra (4): el cuadro de UEH y los rótulos",
        donde="Cuadro de UEH de la lámina de alcantarillado y rótulo de la lámina eléctrica.",
        imagenes=[img(lamina="alcantarillado", clip=(1340, 730, 1600, 1060), dpi=220, marcas=[
            (1, 1588, 825, 1562, 825), (2, 1588, 870, 1564, 870), (3, 1545, 1023, 1518, 1023),
            (4, 1395, 839, 1435, 839)], pie_img="Alcantarillado: UEH"),
                  img(lamina="electrico", clip=(1900, 1010, 2300, 1240), dpi=200, marcas=[(5, 2285, 1035)],
                      pie_img="Rótulo de la lámina eléctrica")],
        disposicion="lado", ancho_img=430,
        items=[
            ("Multiplicación mal hecha", "WC: 3 × 6 = 18, no 15."),
            ("Total", "El cuadro suma 27, con el 15 equivocado. Con 18 da 30."),
            ("Otro total", "«Datos de la U.D.» dice 24 UEH. Hay tres totales distintos: 24, 27 y 30."),
            ("Artefactos", "Cuenta 3 lavamanos y la planta tiene 4 (el Baño 2 tiene dos). No incluye tinas, lavadora ni "
             "piletas."),
            ("Rótulo cambiado", "Las láminas eléctrica y de alcantarillado llevan el rótulo «Proyecto de instalación de "
             "agua potable domiciliario». Las cuatro de instalaciones citan un certificado de factibilidad del "
             "19/06/2009, y ninguna lámina trae propietario ni dirección completa."),
        ],
        cuenta="<b>Para el presupuesto:</b> los artefactos se cuentan en las plantas; el cuadro de UEH es un dato del "
               "trámite sanitario y no cambia las cantidades.<br/>Conviene preguntar si hay una versión más nueva de las "
               "láminas de instalaciones.",
        prueba="Con 3 inodoros de 6 UEH, 3 lavamanos de 2 y 1 artefacto «LC» de 6, ¿cuántas UEH da el cuadro bien sumado?",
        respuesta="30 (18 + 6 + 6).",
        ojo=True,
    ),
    dict(
        n=17, titulo="Lo que no cuadra (5): el cuadro de cargas y el agua caliente",
        donde="Cuadro de cargas y esquema unilineal de la lámina eléctrica; agua potable y gas.",
        imagenes=[img(lamina="electrico", clip=(810, 1430, 1820, 1610), dpi=170, marcas=[
            (1, 1760, 1552, 1700, 1500), (2, 1034, 1552, 1034, 1500), (3, 1193, 1552, 1193, 1512)],
                      pie_img="Eléctrico: cuadro de cargas de alumbrado")],
        disposicion="arriba",
        items=[
            ("Ubicación", "«Sede, cocina, bodega, baño 1-2-3, ext.»: el cuadro habla de una sede con bodega."),
            ("Fluorescentes", "12 equipos de 2×40 W y 3 de 2×20 W, que las plantas de esta casa no muestran."),
            ("Enchufes", "10 de 300 W y 2 de 800 W. Tampoco calza con lo que se ve en las plantas."),
            ("Empalme", "La planta dice «empalme existente A-6 (25 A)» y el unilineal, «empalme proyectado». Si hay que "
             "pedirlo, es una partida y un trámite aparte."),
            ("Agua caliente", "Agua potable y gas dibujan un calefón «C» afuera, al fondo. Agua potable dibuja además un "
             "termo de 80 L en el Baño 2, y el cuadro eléctrico no tiene circuito para un termo."),
        ],
        cuenta="<b>Para el presupuesto:</b> centros, interruptores y enchufes se cuentan en las plantas; el cuadro de "
               "cargas no se usa.<br/>El empalme y el agua caliente quedan «Pendientes» hasta que Luciano conteste: "
               "¿el empalme existe? ¿calefón, termo o los dos?",
        prueba="¿Por qué no se usa el cuadro de cargas para contar los centros de luz?",
        respuesta="Porque es de otra obra: habla de una sede con bodega y de fluorescentes que esta casa no tiene.",
        ojo=True,
    ),
]

ZONAS = ARQ + ESP + INC

# Mapa de la lámina de arquitectura con las zonas de la guía (recuadros de cada página de arquitectura)
MAPA_ZONAS = [(1, (180, 180, 800, 880)), (2, (840, 200, 1425, 860)), (3, (2450, 150, 3300, 1530)),
              (4, (1100, 1040, 2620, 1500)), (5, (50, 1080, 1120, 1520)), (5, (2160, 1560, 3000, 2330)),
              (6, (1460, 170, 2140, 930)), (7, (200, 1590, 2150, 2384))]

INCONSISTENCIAS = [
    ("Baño de visita y caseta de lavadora", "La planta del 1er nivel no los dibuja; están en el detalle 1:25 y en las 4 "
     "especialidades.", "Alto: es un baño completo.", "¿Se construye el baño de visita con la caseta de lavadora, "
     "como en el detalle?", 13),
    ("Escalera", "16 × 18,85 = 3,02 m contra +3,24 del piso del 2do nivel.", "Medio.",
     "¿Cuánto mide la contrahuella real?", 3),
    ("Superficies del 2do nivel", "Tres rótulos de 13,60 m² copiados; las cotas dan 8,64, 7,75 y ≈18,19.", "Medio: "
     "pisos, cielos y pintura.", "Ninguna: se usan las cotas.", 14),
    ("Cadena izquierda del 2do nivel", "Suma 9,15 m interiores contra 9,07 de la derecha y de la escala.", "Bajo.",
     "Ninguna: se usa 9,07.", 14),
    ("Ventana V2", "Planta 220x300 (2), detalle 450×220 (1), elevación ≈3,00×2,20 (2).", "Medio: son los ventanales "
     "más grandes.", "¿Medida y cantidad de la V2?", 15),
    ("Puertas", "P3: cuadro 1, planta 3. Acceso rotulado «100x200 V1»; el detalle P1 mide 143×202. Las puertas del baño "
     "de visita no están en el cuadro.", "Bajo.", "Confirmar la P1.", 15),
    ("Alturas", "Rombos +2,45 / +2,85 en planta contra 2,76 y 2,42 en los cortes. +5,66 rotulado como piso.", "Medio: "
     "muros y pintura.", "¿Qué significan los rombos?", 15),
    ("Cortafuego del 2do nivel", "Planta: ladrillo princesa RF-150. Corte: SIP + 2 volcanitas RF 15 mm.", "Alto.",
     "¿Ladrillo o SIP con volcanita RF?", 15),
    ("Cuadro de UEH", "3 × 6 = 15 (debe ser 18). Totales de 24, 27 y 30. Faltan tinas, lavadora y piletas.", "Bajo "
     "(es trámite).", "Ninguna para el presupuesto.", 16),
    ("Cuadro de cargas", "Es de otra obra (sede, bodega, 12 fluorescentes). Empalme «existente» en planta y «proyectado» "
     "en el unilineal.", "Medio: no sirve para contar.", "¿El empalme existe o hay que pedirlo?", 17),
    ("Rótulos de lámina", "Eléctrico y alcantarillado dicen «agua potable». Factibilidad de 2009, sin propietario ni "
     "dirección.", "Ninguno.", "¿Hay versión más nueva de las láminas?", 16),
    ("Agua caliente", "Calefón a gas afuera (agua potable y gas) más termo de 80 L en el Baño 2.", "Medio.",
     "¿Calefón, termo o los dos?", 17),
    ("Nombres", "Tres «Dormitorio 1»; «Baño 2» para dos baños distintos; «W.C» = closet en arquitectura.", "Ninguno "
     "(solo confunde).", "Ninguna.", 14),
    ("Escala del dibujo", "El dibujo es 1,1 % más chico que sus cotas (798 acotado contra 7,90 medido).", "Bajo.",
     "Ninguna: al medir a escala, se multiplica por 1,011.", 1),
]

SIMBOLOS = [
    ("Cota (cm)", (425, 512, 535, 545), "Número sobre una línea con trazos en las puntas. Aquí va en centímetros.",
     "De aquí salen largos y anchos."),
    ("Muro cortafuego", (205, 400, 270, 700), "Muro grueso en el deslinde, con su nota escrita al lado.",
     "Se construye completo; su material manda en el costo."),
    ("Rótulo y rombo", (440, 470, 520, 510), "Nombre, superficie útil y un rombo con un número (+2,45).",
     "La superficie se comprueba con las cotas."),
    ("Código de vano", (350, 334, 412, 358), "Medida y código en un círculo: V = ventana, P = puerta.",
     "Se busca en el cuadro de puertas y ventanas."),
    ("Puerta", (540, 755, 600, 810), "Hoja y arco que muestra hacia dónde abre.", "Se cuenta por unidad."),
    ("Peldaños", (640, 520, 720, 700), "Escalones numerados desde abajo.", "Se cuentan y se mide el alto total."),
    ("Ejes", (685, 180, 715, 205), "Círculo con letra o número: líneas de referencia de la estructura.",
     "Sirven para calzar detalles y plantas."),
    ("Artefactos", (320, 752, 470, 802), "Lavaplatos, inodoro, lavamanos y tina, dibujados en planta.",
     "Se cuentan uno por uno."),
]

# ------------------------------------------------------------------------------------- páginas
c = canvas.Canvas(SALIDA, pagesize=(ANCHO, ALTO))
c.setTitle("Cómo leer los planos · Obra Daniel Barrera")
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


def dibujar(lista):
    for u, ux, uy, uw in lista:
        u.wrap(uw, 10_000)
        u.drawOn(c, ux, uy)


ARQ_RECT = LAMINAS["arquitectura"].rect
mapa_png = recorte("mapa", "arquitectura", (0, 0, ARQ_RECT.width, ARQ_RECT.height), 50)


def mapa(x, y, w, destacar=None):
    h = w * ARQ_RECT.height / ARQ_RECT.width
    c.drawImage(mapa_png, x, y, w, h)
    c.setStrokeColor(HexColor("#B0B0B0"))
    c.setLineWidth(0.5)
    c.rect(x, y, w, h)
    s = w / ARQ_RECT.width
    for n, (x0, y0, x1, y1) in MAPA_ZONAS:
        if destacar is not None and n != destacar:
            continue
        c.setStrokeColor(ROJO)
        c.setLineWidth(1.4 if destacar else 1.1)
        c.rect(x + x0 * s, y + h - y1 * s, (x1 - x0) * s, (y1 - y0) * s, stroke=1, fill=0)
        if destacar is None:
            marcador(c, x + x0 * s + 9, y + h - y0 * s - 9, n, r=7.5)
    return h


# 1. Portada
nueva_pagina()
titulo_pagina("Cómo leer los planos de la obra Daniel Barrera",
              "Guía lámina por lámina, con las instalaciones superpuestas sobre la arquitectura · preparada por Alma "
              "para Gonza, 12-10-2026")
col_w = 300
bloques = [
    Paragraph("Qué es esta obra", SUBT),
    Paragraph("Una vivienda de dos niveles en calle Flamenco N° 23, presentada como <b>Regularización Ley 20.898</b> "
              "(profesional: Felipe Potthoff M.). El 1er nivel es de albañilería; el 2do nivel, de paneles SIP sobre "
              "una estructura de piso de acero; el techo, de cerchas metalcon.", CUERPO),
    Paragraph("Las 5 láminas", SUBT),
    Paragraph("• <b>Arquitectura</b>: plantas, cortes, elevaciones, estructura del piso, techumbre, escalera y cuadro "
              "de puertas y ventanas (mapa de la derecha).<br/>• <b>Agua potable</b>, <b>alcantarillado</b>, "
              "<b>eléctrico</b> y <b>gas</b>: cada una repite las plantas con su red encima.", CUERPO),
    Paragraph("Tres reglas de estos planos", SUBT),
    Paragraph("1. Las cotas van en <b>centímetros</b>: 775 son 7,75 m (en San Miguel iban en metros).<br/>2. Puertas y "
              "ventanas llevan un <b>código</b> (P1, V2…) que se busca en el cuadro de la lámina de arquitectura.<br/>"
              "3. Las especialidades se dibujaron sobre la arquitectura: si se superponen, deben calzar. Donde no "
              "calzan, hay algo que preguntar.", CUERPO),
    Paragraph("Cómo usar esta guía", SUBT),
    Paragraph("Páginas 3–9: arquitectura, zona por zona. Páginas 10–14: cada especialidad superpuesta (gris = "
              "arquitectura, <font color='#D91A73'><b>magenta</b></font> = instalación). Páginas 15–19: lo que no "
              "cuadra, con superposiciones. Página 20: tabla de inconsistencias y preguntas para Luciano. Página 21: "
              "receta y respuestas.", CUERPO),
    caja("Consejo", "Amplía los recortes en la pantalla: están en alta resolución.", FONDO_PRUEBA),
]
ubic = empacar(bloques, [(MARGEN, ALTO - MARGEN - 48, col_w, 500)])
assert ubic, "la portada no cabe"
dibujar(ubic)
mx = MARGEN + col_w + 22
mw = ANCHO - MARGEN - mx
mh = mapa(mx, ALTO - MARGEN - 52 - mw * ARQ_RECT.height / ARQ_RECT.width, mw)
c.setFont("Dv", 8)
c.setFillColor(GRIS)
c.drawString(mx, ALTO - MARGEN - 62 - mh, "Lámina de arquitectura completa, con las zonas de la guía.")
for k, z in enumerate(ARQ):
    lx = mx
    ly = ALTO - MARGEN - 82 - mh - k * 15
    marcador(c, lx + 6, ly + 3, z["n"], r=6.5)
    c.setFillColor(HexColor("#000000"))
    c.setFont("Dv", 8.4)
    c.drawString(lx + 17, ly, f"{z['titulo']} (pág. {k + 3})")
pie(c, pagina)

# 2. Símbolos
nueva_pagina()
titulo_pagina("Los símbolos de estos planos",
              "Cada ejemplo está recortado de la lámina de arquitectura. «Para el presupuesto» dice qué hacer con lo que "
              "ves.")
TIT_CARTA = ParagraphStyle("tit_carta", fontName="DvB", fontSize=10, leading=12, textColor=AZUL)
cw, gap = (ANCHO - 2 * MARGEN - 3 * 12) / 4, 12
top = ALTO - MARGEN - 46
chh = (top - 34 - gap) / 2
for i, (nombre, clip, que, presu) in enumerate(SIMBOLOS):
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
    png = recorte(f"simbolo_{i}", "arquitectura", clip, 400)
    cwp, chp = clip[2] - clip[0], clip[3] - clip[1]
    esc = min((caja_w - 8) / cwp, (caja_h - 8) / chp)
    c.drawImage(png, x + 8 + (caja_w - cwp * esc) / 2, caja_y + (caja_h - chp * esc) / 2, cwp * esc, chp * esc)
    textos = [Paragraph(f"<b>Qué es:</b> {que}", CUERPO_CH),
              Paragraph(f"<font color='#1F3A5F'><b>Para el presupuesto:</b> {presu}</font>", CUERPO_CH)]
    ubic = empacar(textos, [(x + 8, caja_y - 6, cw - 16, caja_y - 6 - (ytop - chh) - 4)])
    if not ubic:
        avisos.append(f"símbolo «{nombre}»: el texto no cabe en la tarjeta")
        continue
    dibujar(ubic)
pie(c, pagina)


# 3-18. Zonas
def png_de(z, k, im):
    nombre = f"z{z['n']}_{k}"
    if im["sup"]:
        return superposicion(nombre, im["sup"], im["clip"], im["dpi"])
    return recorte(nombre, im["lamina"], im["clip"], im["dpi"])


PIE_IMG = 11  # alto reservado bajo cada imagen para su leyenda

for z in ZONAS:
    nueva_pagina()
    titulo_pagina(f"{z['n']} · {z['titulo']}", z["donde"])
    es_arq = z in ARQ
    loc_w = 112 if es_arq else 0
    mapa_h = loc_w * ARQ_RECT.height / ARQ_RECT.width if es_arq else 0
    if es_arq:
        mapa(ANCHO - MARGEN - loc_w, ALTO - MARGEN - mapa_h, loc_w, destacar=z["n"])
    pngs = [png_de(z, k, im) for k, im in enumerate(z["imagenes"])]
    proporciones = [(im["clip"][2] - im["clip"][0]) / (im["clip"][3] - im["clip"][1]) for im in z["imagenes"]]
    arriba = ALTO - MARGEN - max(mapa_h, 34) - 10
    abajo = 30
    ancho_util = ANCHO - 2 * MARGEN
    sep_img = 10

    def unidades():
        u = [Paragraph("Qué estás viendo", SUBT)]
        u += [Paragraph(f"{num(k)}&nbsp; <b>{t if t.endswith('.') else t + '.'}</b> {d}", CUERPO)
              for k, (t, d) in enumerate(z["items"], 1)]
        u += [caja("La cuenta" if not z.get("ojo") else "Qué hacer", z["cuenta"],
                   FONDO_CUENTA if not z.get("ojo") else FONDO_OJO),
              caja("Prueba tú", z["prueba"] + " <font color='#6B6B6B'>(Respuesta al final de la guía.)</font>",
                   FONDO_PRUEBA)]
        return u

    colocado = None
    for intento in range(40):
        red = 0.95 ** intento
        if z["disposicion"] == "arriba":
            # fila de imágenes de igual alto a todo el ancho; texto en 3 columnas debajo
            alto_fila = (ancho_util - sep_img * (len(pngs) - 1)) / sum(proporciones) * red
            alto_fila = min(alto_fila, (arriba - abajo) * 0.62 * red)
            iy = arriba - alto_fila
            col_w3 = (ancho_util - 2 * 14) / 3
            alto_txt = iy - PIE_IMG - 8 - abajo
            cols = [(MARGEN + k * (col_w3 + 14), iy - PIE_IMG - 8, col_w3, alto_txt) for k in range(3)]
        else:
            alto_fila = (arriba - abajo - PIE_IMG) * red
            ancho_fila = alto_fila * sum(proporciones) + sep_img * (len(pngs) - 1)
            if ancho_fila > z["ancho_img"] * red:
                alto_fila = (z["ancho_img"] * red - sep_img * (len(pngs) - 1)) / sum(proporciones)
                ancho_fila = z["ancho_img"] * red
            iy = arriba - alto_fila
            resto = ancho_util - ancho_fila - 16
            bajo = [(MARGEN, iy - PIE_IMG - 8, ancho_fila, iy - PIE_IMG - 8 - abajo)] if iy - abajo > 90 else []
            xt = MARGEN + ancho_fila + 16
            opciones = [[(xt, arriba, resto, arriba - abajo)] + bajo]
            if resto > 430:
                cw2 = (resto - 14) / 2
                opciones.append([(xt + k * (cw2 + 14), arriba, cw2, arriba - abajo) for k in range(2)] + bajo)
            cols = None
            for op in opciones:
                if empacar(unidades(), op):
                    cols = op
                    break
            if cols is None:
                continue
        colocado = empacar(unidades(), cols)
        if colocado:
            break
    if not colocado:
        avisos.append(f"página {z['n']}: el texto no cabe ni achicando las imágenes")
        continue
    ix = MARGEN
    for png, prop, im in zip(pngs, proporciones, z["imagenes"]):
        iw = alto_fila * prop
        imagen_con_numeros(c, png, im["clip"], ix, iy, iw, alto_fila, im["marcas"])
        if im["pie"]:
            c.setFont("Dv", 7.2)
            c.setFillColor(MAGENTA if im["sup"] else GRIS)
            c.drawString(ix, iy - 9, im["pie"])
            c.setFillColor(HexColor("#000000"))
        ix += iw + sep_img
    dibujar(colocado)
    z["escala"] = alto_fila / (z["imagenes"][0]["clip"][3] - z["imagenes"][0]["clip"][1])
    pie(c, pagina)

# 19. Tabla de inconsistencias
nueva_pagina()
titulo_pagina("Inconsistencias y preguntas para Luciano",
              "Todo lo que no calza entre láminas, cuánto pesa en el presupuesto y qué hay que preguntar.")
CH = ParagraphStyle("ch", parent=CUERPO, fontSize=7.4, leading=9)
CHB = ParagraphStyle("chb", parent=CH, fontName="DvB")
filas = [[Paragraph(t, CHB) for t in ("#", "Qué", "Qué no calza", "Peso en el presupuesto", "Pregunta para Luciano",
                                      "Pág.")]]
for k, (que, detalle, peso, pregunta, pag) in enumerate(INCONSISTENCIAS, 1):
    filas.append([Paragraph(str(k), CH), Paragraph(f"<b>{que}</b>", CH), Paragraph(detalle, CH), Paragraph(peso, CH),
                  Paragraph(pregunta, CH), Paragraph(str(pag + 2), CH)])
anchos = [20, 105, 282, 110, 235, 34]
tabla = Table(filas, colWidths=anchos, repeatRows=1)
tabla.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, HexColor("#C8C8C8")),
                           ("BACKGROUND", (0, 0), (-1, 0), HexColor("#EDEDED")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
tw, th = tabla.wrap(sum(anchos), 10_000)
if th > ALTO - MARGEN - 46 - 30:
    avisos.append("la tabla de inconsistencias no cabe")
tabla.drawOn(c, MARGEN, ALTO - MARGEN - 46 - th)
pie(c, pagina)

# 20. Receta y respuestas
nueva_pagina()
titulo_pagina("De los planos al presupuesto", "La receta para esta obra y las respuestas de «Prueba tú».")
pasos = [
    ("Arquitectura primero:", "recinto por recinto, con las cotas (en cm), no con los rótulos de superficie."),
    ("Alturas desde los cortes:", "2,76 en el 1er nivel y 2,42 en el 2do, hasta que Luciano aclare los rombos."),
    ("Vanos desde la planta:", "se cuentan en la planta y se dimensionan con la elevación y el cuadro."),
    ("Estructura y techo:", "vigas por largo desde la estructura del piso; cerchas desde la planta de cerchas."),
    ("Cada especialidad superpuesta:", "artefactos, puntos y metros de cañería por diámetro desde la planta; los "
     "cuadros no se usan."),
    ("Marca lo que falta:", "lo que no calza queda «Pendiente» con su pregunta; lo medido a escala, «Supuesto» (× 1,011)."),
]
izq = [Paragraph("La receta en 6 pasos", SUBT)]
izq += [Paragraph(f"{num(k)}&nbsp; <b>{t}</b> {d}", CUERPO) for k, (t, d) in enumerate(pasos, 1)]
izq.append(Paragraph("Lo que estas láminas no dicen", SUBT))
falta = Table([[Paragraph("<b>Falta</b>", CUERPO), Paragraph("<b>Dónde buscarlo</b>", CUERPO)],
               [Paragraph("Terminaciones y calidades", CUERPO), Paragraph("Especificaciones técnicas (EETT)", CUERPO)],
               [Paragraph("Pendiente y material de la cubierta", CUERPO), Paragraph("EETT o Luciano", CUERPO)],
               [Paragraph("Fundaciones del 1er nivel", CUERPO), Paragraph("Plano de cálculo (no vino)", CUERPO)],
               [Paragraph("Qué existe y qué es nuevo", CUERPO), Paragraph("Terreno: es una regularización", CUERPO)]],
              colWidths=[180, 175])
falta.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, HexColor("#C8C8C8")),
                           ("BACKGROUND", (0, 0), (-1, 0), HexColor("#EDEDED")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
izq.append(falta)
izq.append(Paragraph("Al ser una <b>regularización</b>, parte de la casa puede estar construida. La visita a terreno "
                     "dice qué se presupuesta como obra nueva y qué solo se regulariza.", CUERPO))
der = [Paragraph("Respuestas de «Prueba tú»", SUBT)]
der += [Paragraph(f"{num(z['n'])}&nbsp; {z['prueba']}<br/><font color='#1F3A5F'>→ {z['respuesta']}</font>", CUERPO_CH)
        for z in ZONAS]
colw = (ANCHO - 2 * MARGEN - 24) / 2
for lista, x in ((izq, MARGEN), (der, MARGEN + colw + 24)):
    ubic = empacar(lista, [(x, ALTO - MARGEN - 46, colw, ALTO - MARGEN - 46 - 30)])
    if not ubic:
        avisos.append("la página final no cabe")
        continue
    dibujar(ubic)
pie(c, pagina)
c.save()

print("guardado:", SALIDA, "· páginas:", pagina)
for a in avisos:
    print("  AVISO:", a)
sys.exit(1 if avisos else 0)

"""Geometría de la habilitación Preuniversitario Talca para el tour 3D, en metros del plano v3.

Ejes: x hacia la derecha e y hacia abajo, como se lee la lámina; origen en su esquina superior izquierda. Escala 1:50
(56,69 pt por metro). Los tabiques salen de los vectores del plano (pares de líneas paralelas a 0,12) y se completaron a
mano los que el detector no tomó (la Sala 01, la L de la Sala 03, el muro de la entrada y el muro inclinado de las
oficinas). Cada tabique va con el sector del Excel donde se anota, según la convención de recorrido de Gonza.
"""
import math

ANG = 4.03                       # giro de los ejes de pilares y de la fachada oriente (grados)
TAN = math.tan(math.radians(ANG))
ALTO_MURO = 3.00                 # alto de tabique de Luciano
ALTO_PUERTA = 2.10
ALFEIZAR, DINTEL = 0.90, 2.60    # ventanas de 1,70 de alto (alto de Luciano); el alféizar es supuesto
CIELO = 3.00

# Tabiques llenos: (x0, x1, y0, y1, sector)
MUROS = [
    # B Oficinas y Guardar
    (10.05, 14.07, 8.21, 8.33, "B.1"), (10.07, 14.32, 11.32, 11.44, "B.1"), (14.32, 14.44, 8.98, 10.52, "B.1"),
    (9.99, 14.44, 14.44, 14.56, "B.2"), (14.32, 14.44, 11.44, 11.84, "B.2"), (14.32, 14.44, 13.04, 13.64, "B.2"),
    (9.93, 13.64, 16.64, 16.76, "B.3"), (14.32, 14.44, 15.36, 15.96, "B.3"),
    (9.87, 14.44, 18.74, 18.86, "B.4"), (14.32, 14.44, 16.75, 17.94, "B.4"),
    (9.77, 11.96, 21.86, 21.98, "B.5"), (11.96, 12.07, 19.66, 21.98, "B.5"),
    (12.07, 12.28, 21.86, 21.98, "B.6"), (13.07, 13.20, 20.06, 23.96, "B.6"), (13.07, 13.20, 24.76, 28.06, "B.6"),
    # C Salas (orden del Excel: 01, 02, 03, 07, 04, 06, 05)
    (20.67, 20.79, 3.18, 8.76, "C.1"), (20.67, 23.71, 3.18, 3.30, "C.1"), (23.59, 23.71, 1.93, 3.30, "C.1"),
    (30.24, 30.36, 1.93, 7.97, "C.1"), (21.74, 22.23, 8.64, 8.76, "C.1"), (23.02, 23.42, 8.64, 8.76, "C.1"),
    (25.42, 26.02, 8.64, 8.76, "C.1"), (28.02, 30.24, 8.64, 8.76, "C.1"),
    (31.03, 31.58, 8.30, 8.42, "C.2"), (32.53, 38.27, 8.30, 8.42, "C.2"), (38.20, 39.62, 1.81, 1.93, "C.2"),
    (32.54, 32.66, 8.42, 10.38, "C.3"), (30.24, 32.66, 10.26, 10.38, "C.3"), (30.24, 30.36, 10.38, 12.21, "C.3"),
    (30.24, 30.36, 14.21, 14.61, "C.3"), (30.24, 30.36, 15.56, 15.96, "C.3"), (30.47, 37.70, 16.30, 16.42, "C.3"),
    (15.64, 21.48, 15.51, 15.63, "C.4"), (15.64, 15.76, 15.63, 20.06, "C.4"), (21.36, 21.48, 15.63, 17.46, "C.4"),
    (21.36, 21.48, 18.71, 19.11, "C.4"), (15.64, 21.90, 20.06, 20.18, "C.4"),
    (29.68, 29.80, 16.76, 17.61, "C.5"), (29.68, 29.80, 19.61, 20.21, "C.5"), (29.68, 29.80, 22.21, 22.61, "C.5"),
    (29.68, 29.80, 23.56, 23.96, "C.5"), (29.91, 37.13, 24.30, 24.42, "C.5"),
    (13.20, 15.64, 20.06, 20.18, "C.6"), (21.77, 21.90, 20.18, 21.07, "C.6"), (21.77, 21.90, 23.07, 23.96, "C.6"),
    (21.77, 21.90, 24.76, 25.16, "C.6"), (21.77, 21.90, 26.11, 26.51, "C.6"), (21.77, 21.90, 27.71, 28.06, "C.6"),
    (29.11, 29.23, 24.76, 25.16, "C.7"), (29.11, 29.23, 26.11, 26.51, "C.7"), (29.11, 29.23, 27.71, 28.06, "C.7"),
    # D Hall: muro de la entrada
    (15.38, 16.00, 8.21, 8.33, "D.1"), (20.10, 20.67, 8.21, 8.33, "D.1"),
]
# Muro de fondo de las oficinas, a 1,7°: de (10,05; 8,21) a (9,46; 28,06), por tramos según el recinto que cierra
MURO_INCLINADO = [((10.05, 8.21), (9.46, 28.06))]
CORTES_INCLINADO = [(8.21, "B.1"), (11.38, "B.2"), (14.50, "B.3"), (16.70, "B.4"), (18.80, "B.5"), (21.92, "B.6")]

# Vanos dentro de una línea de tabique: (x0, x1, y0, y1, sector, nombre). Puertas de 2,10; ventanas de 0,90 a 2,60
PUERTAS = [
    (17.05, 19.05, 8.21, 8.33, "D.1", "Puerta doble de la entrada, 2,00"),
    (14.32, 14.44, 10.52, 11.32, "B.1", "Puerta Oficina 05"), (14.32, 14.44, 13.64, 14.44, "B.2", "Puerta Oficina 04"),
    (14.32, 14.44, 14.56, 15.36, "B.3", "Puerta Oficina 03"), (14.32, 14.44, 17.94, 18.74, "B.4", "Puerta Oficina 02"),
    (11.96, 12.07, 18.86, 19.66, "B.5", "Puerta Guardar"), (12.28, 13.07, 21.86, 21.98, "B.6", "Puerta Oficina 01"),
    (20.79, 21.74, 8.64, 8.76, "C.1", "Puerta Sala 01"), (31.58, 32.53, 8.30, 8.42, "C.2", "Puerta Sala 02"),
    (30.24, 30.36, 14.61, 15.56, "C.3", "Puerta Sala 03"), (21.36, 21.48, 19.11, 20.06, "C.4", "Puerta Sala 07"),
    (29.68, 29.80, 22.61, 23.56, "C.5", "Puerta Sala 04"), (21.77, 21.90, 25.16, 26.11, "C.6", "Puerta Sala 06"),
    (29.11, 29.23, 25.16, 26.11, "C.7", "Puerta Sala 05"),
]
VENTANAS = [
    (16.00, 17.05, 8.21, 8.33, "D.1"), (19.05, 20.10, 8.21, 8.33, "D.1"),
    (14.32, 14.44, 11.84, 13.04, "B.2"),
    (23.42, 25.42, 8.64, 8.76, "C.1"), (26.02, 28.02, 8.64, 8.76, "C.1"),
    (30.24, 30.36, 12.21, 14.21, "C.3"), (21.36, 21.48, 17.46, 18.71, "C.4"),
    (29.68, 29.80, 17.61, 19.61, "C.5"), (29.68, 29.80, 20.21, 22.21, "C.5"),
    (21.77, 21.90, 21.07, 23.07, "C.6"), (21.77, 21.90, 26.51, 27.71, "C.6"),
    (29.11, 29.23, 26.51, 27.71, "C.7"),
]

# 15 pilares de 0,80 (esquina superior izquierda), leídos de los rectángulos grises del plano
PILARES = [(38.27, 7.97), (30.24, 7.97), (22.23, 7.97), (14.20, 7.97), (6.19, 7.97),
           (37.69, 15.96), (29.68, 15.96), (21.67, 15.96), (13.64, 15.96), (5.63, 15.96),
           (37.13, 23.96), (29.12, 23.96), (21.11, 23.96), (13.07, 23.96), (5.06, 23.96)]
LADO_PILAR = 0.80
FILAS_EJES = [8.37, 16.36, 24.36]   # ejes horizontales por el centro de los pilares

# Muros perimetrales existentes (opacos, Gonza 17-oct): oriente inclinado y sur continuo
FACHADA_ORIENTE = ((39.626, 0.758), (37.704, 28.058))   # cara interior
FACHADA_SUR = [(4.82, 37.86)]   # continuo: el vano dibujado bajo la línea gruesa es muro (Gonza, 17-oct)
Y_SUR = 28.06
LIMITE_NORTE = ((23.71, 1.93), (39.70, 1.93))   # borde segmentado: por confirmar

# Recintos: (nombre, sup. útil, x0, x1, y0, y1, sector). Los que tocan la fachada llegan hasta ella
RECINTOS = [
    ("Oficina 05", "12,38", 10.0, 14.32, 8.33, 11.32, "B.1"), ("Oficina 04", "12,82", 9.9, 14.32, 11.44, 14.44, "B.2"),
    ("Oficina 03", "8,61", 9.85, 14.32, 14.56, 16.64, "B.3"), ("Oficina 02", "8,74", 9.8, 14.32, 16.76, 18.74, "B.4"),
    ("Guardar", "6,41", 9.7, 11.96, 18.86, 21.86, "B.5"), ("Oficina 01", "20,64", 9.6, 13.07, 21.98, 28.06, "B.6"),
    ("Sala de Capacitación 01", "58,88", 20.79, 30.24, 3.30, 8.64, "C.1"),
    ("Sala de Capacitación 02", "55,81", 30.36, 39.6, 1.93, 8.30, "C.2"),
    ("Sala de Capacitación 03", "61,24", 30.36, 39.1, 8.42, 16.30, "C.3"),
    ("Sala de Capacitación 07", "24,80", 15.76, 21.36, 15.63, 20.06, "C.4"),
    ("Sala de Capacitación 04", "64,93", 29.80, 38.6, 16.42, 24.30, "C.5"),
    ("Sala de Capacitación 06", "66,52", 13.20, 21.77, 20.18, 28.06, "C.6"),
    ("Sala de Capacitación 05", "30,41", 29.23, 38.0, 24.42, 28.06, "C.7"),
]
SALA01_ALTA = (23.71, 30.24, 1.93, 3.30)   # la parte alta de la L de la Sala 01
RECEPCION = (15.64, 21.49, 12.49, 15.53)    # zona segmentada de 5,84 × 3,00 dentro del hall

# Mobiliario de la Recepción (a escala) y los dos pilares con marco (por confirmar)
MESONES = [(15.70, 18.50, 13.15, 13.95), (18.60, 21.40, 13.15, 13.95), (17.68, 18.50, 13.95, 15.40),
           (18.60, 19.40, 13.95, 15.40)]
SILLAS = [(16.36, 13.0), (17.50, 13.0), (19.67, 13.0), (20.72, 13.0), (17.06, 14.6), (20.03, 14.6)]
MARCOS = [("Marco del pilar de la entrada: ¿forro, mueble o shaft? (por confirmar)", (14.07, 15.38, 7.80, 8.99)),
          ("Marco del pilar del hall con mesón: ¿mueble de cafetería? (por confirmar)", (21.48, 22.81, 15.52, 17.12))]
MESON_PILAR = (21.48, 22.81, 15.52, 15.96)

# Zona de recepción existente del edificio (gris, fuera de la obra)
EXISTENTE = (3.7, 20.67, 0.3, 8.21)

ENTRADA = (18.05, 9.4)     # donde parte la caminata: recién pasada la puerta doble, mirando al sur


def x_fachada(y):
    (xa, ya), (xb, yb) = FACHADA_ORIENTE
    return xa + (xb - xa) * (y - ya) / (yb - ya)

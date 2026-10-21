"""Partidas de la ampliación San Bernardo: cantidades, precios unitarios y observaciones.

Lo usan armar_presupuesto.py (Excel) y armar_guia_obra.py (PDF), para que los dos digan lo mismo.
Cantidades: texto de fórmula Excel sin el «=» (se ve la cuenta) o número. Precios unitarios: los de
San Miguel v2.0 (Drive, 13-oct-2026), salvo las puertas, que dio Gonza (doble 750.000, simple 400.000).
Plano: «Ampliacion Obra Menor 2 - Covadonga 139 - Preu UC-A_01 (2) v3.pdf» (14-oct-2026). Las secciones A y C
siguen el Excel que Gonza editó en Drive el 14-oct (sin cierre perimetral ni puerta al edificio existente).
"""

H = 2.40  # altura interior de los recintos (correo del arquitecto)

# Superficies generales (m²), de las cotas del plano
HUELLA = "14.84*5.24+5.24*14.76"            # bloque sur (salas 02 y 03) + bloque oriente (Sala 01, Guardar, baños)
CORREDOR = "6.28*2.3+2.1*10.1+9.6*2"       # norte + oriente + sur
PATIO = "7.5*10.1"
TOTAL = f"{HUELLA}+{CORREDOR}+{PATIO}"     # 285,71 m²

PU_SM = "P.U. de San Miguel."

SECCIONES = [
    ("A", "OBRAS PRELIMINARES", [
        ("A.1 Instalación de faenas y trabajos previos", [
            ("A.1.1", "Instalación de faena (oficina, bodega, pañol, iluminación)", "GL", 1, 2200000, ""),
            ("A.1.3", "Empalmes provisorios de luz y agua", "GL", 1, 550000, "Partida global. " + PU_SM),
            ("A.1.4", "Servicios higiénicos provisorios", "GL", 3, 220000, ""),
            ("A.1.5", "Letrero de obra y señalética", "UN", 1, 550000, "Valor de San Miguel."),
            ("A.1.6", "Tala y retiro de arbol", "UN", 1, 766667, ""),
            ("A.1.7", "Trazado, replanteo y niveles", "GL", 1, 350000,
             "Valor de San Miguel. Niveles críticos: el piso nuevo queda a +0,10, igual al existente (el correo pide "
             "que calce perfecto por accesibilidad universal, sin rampas), y el radier con pendiente de 1% hacia el "
             "poniente."),
            ("A.1.8", "Rebaje de terreno (e = 0,31 m)", "m³", f"({TOTAL})*0.31", 50000,
             "Superficie: recintos 155,10 + corredor 54,85 + patio 75,75 = 285,71 m² (ver «Cubicación base»). "
             "Profundidad: el terreno está a +0,05 (N.T.N. del plano) y el piso terminado debe quedar a +0,10. Bajo el "
             "piso van 0,36 m (estabilizado 0,15 + gravilla 0,10 + radier 0,10 + porcelanato ~0,01): el fondo queda "
             "a +0,10 − 0,36 = −0,26, y de +0,05 a −0,26 hay 0,31 m."),
            # El Excel vigente (editado por Gonza el 14-oct) trae además una fila «Retiro de escombros» (95.000/m³)
            # sin cantidad y fuera del subtotal A.1, y la nota de Luciano para el rebaje en la columna H. No suman.
        ]),
    ]),
    ("B", "RECINTOS", [
        ("B.1 Sala de Capacitación 01 (50,00 m²)", [
            ("B.1.1", "Tabiquería perimetral metalcon (h = 2,40)", "m²", f"(10+10)*{H}", 85000,
             "Muros oriente (10,00) y poniente, hacia el corredor (10,00), por 2,40 de alto (correo). Sin descontar "
             "vanos, como en San Miguel. P.U. de tabiquería exteriores de San Miguel: incluye las capas de las EETT "
             "(criterio de Luciano)."),
            ("B.1.2", "Tabiquería interior metalcon (h = 2,40)", "m²", f"(5+5)*{H}", 76000,
             "Muros con el Guardar (5,00) y con la Sala 02 (5,00). Yeso cartón ST 15 mm por ambas caras (correo)."),
            ("B.1.3", "Techumbre", "m²", 50.00, 92000,
             "Sup. útil rotulada (5,00 × 10,00). Cerchas de metalcon 90 mm cada 0,80, de 1,00 m de alto en el punto "
             "alto y 0,20 en el bajo, con caída hacia afuera (correo). Los aleros van en C.2.2."),
            ("B.1.4", "Estructura de cielo", "m²", 50.00, 32000, "Cielo de yeso cartón 10 mm ST (correo). Sup. útil."),
            ("B.1.5", "Porcelanato de piso", "m²", 50.00, 95000,
             "Sup. útil. El correo pide «el mismo tipo de piso existente»: se usó el porcelanato de San Miguel porque "
             "no sabemos qué piso tienen hoy las salas. Luciano: confirmar."),
            ("B.1.6", "Guardapolvo", "ml", "2*(5+10)", 25000, "Perímetro 2 × (5,00 + 10,00), sin descontar puertas."),
            ("B.1.7", "Pintura de muros", "m²", f"2*(5+10)*{H}", 7500, "Perímetro 30,00 × 2,40 de alto."),
            ("B.1.8", "Pintura de cielo", "m²", 50.00, 7500, "Sup. útil."),
            ("B.1.9", "Ventanas y vidrios", "m²", "3*2*0.6+0.6*1.5", 230000,
             "3 ventanas horizontales de 2,00 × 0,60 en el muro oriente (proyectantes, aluminio anodizado) + 1 vidrio "
             "fijo de 0,60 × 1,50 junto a la puerta (correo: «solo como visual»)."),
            ("B.1.10", "Puerta doble", "UN", 1, 750000,
             "Puerta de dos hojas, vano 1,40, hacia el corredor. Precio dado por Gonza. Correo: «del mismo tipo de las "
             "existentes»."),
        ]),
        ("B.2 Sala de Capacitación 02 (42,40 m²)", [
            ("B.2.1", "Tabiquería perimetral metalcon (h = 2,40)", "m²", f"(8.48+5+3.48)*{H}", 85000,
             "Muro sur (8,48), oriente (5,00) y el tramo norte que da al corredor (8,48 − 5,00 = 3,48; el resto del "
             "muro norte es el de la Sala 01). Mismo criterio que B.1.1."),
            ("B.2.2", "Tabiquería interior metalcon (h = 2,40)", "m²", f"5*{H}", 76000,
             "Muro con la Sala 03 (5,00). El muro con la Sala 01 ya está en B.1.2."),
            ("B.2.3", "Techumbre", "m²", 42.40, 92000, "Sup. útil rotulada (8,48 × 5,00). Mismo sistema que B.1.3."),
            ("B.2.4", "Estructura de cielo", "m²", 42.40, 32000, "Sup. útil."),
            ("B.2.5", "Porcelanato de piso", "m²", 42.40, 95000, "Sup. útil. Ver B.1.5."),
            ("B.2.6", "Guardapolvo", "ml", "2*(8.48+5)", 25000, "Perímetro 2 × (8,48 + 5,00)."),
            ("B.2.7", "Pintura de muros", "m²", f"2*(8.48+5)*{H}", 7500, "Perímetro 26,96 × 2,40."),
            ("B.2.8", "Pintura de cielo", "m²", 42.40, 7500, "Sup. útil."),
            ("B.2.9", "Ventanas y vidrios", "m²", "2*2*0.6+0.6*1.5", 230000,
             "2 ventanas de 2,00 × 0,60 en el muro sur + vidrio fijo de 0,60 × 1,50 junto a la puerta."),
            ("B.2.10", "Puerta doble", "UN", 1, 750000, "Vano 1,40, hacia el corredor sur. Precio dado por Gonza."),
        ]),
        ("B.3 Sala de Capacitación 03 (30,00 m²)", [
            ("B.3.1", "Tabiquería perimetral metalcon (h = 2,40)", "m²", f"(6+5+6)*{H}", 85000,
             "Muros sur (6,00), poniente (5,00) y norte, hacia el corredor (6,00). El muro con la Sala 02 está en B.2.2."),
            ("B.3.2", "Techumbre", "m²", 30.00, 92000, "Sup. útil rotulada (6,00 × 5,00)."),
            ("B.3.3", "Estructura de cielo", "m²", 30.00, 32000, "Sup. útil."),
            ("B.3.4", "Porcelanato de piso", "m²", 30.00, 95000, "Sup. útil. Ver B.1.5."),
            ("B.3.5", "Guardapolvo", "ml", "2*(6+5)", 25000, "Perímetro 2 × (6,00 + 5,00)."),
            ("B.3.6", "Pintura de muros", "m²", f"2*(6+5)*{H}", 7500, "Perímetro 22,00 × 2,40."),
            ("B.3.7", "Pintura de cielo", "m²", 30.00, 7500, "Sup. útil."),
            ("B.3.8", "Ventanas y vidrios", "m²", "2*1.8*0.6+0.6*1.5", 230000,
             "2 ventanas de 1,80 × 0,60 en el muro sur + vidrio fijo de 0,60 × 1,50 junto a la puerta."),
            ("B.3.9", "Puerta doble", "UN", 1, 750000, "Vano 1,40, hacia el corredor sur. Precio dado por Gonza."),
        ]),
        ("B.4 Guardar (9,80 m²)", [
            ("B.4.1", "Tabiquería perimetral metalcon (h = 2,40)", "m²", f"(2.08+2.08)*{H}", 85000,
             "Muro poniente, hacia el corredor: puerta 0,80 + 1,28 = 2,08 ml. Muro oriente: 1,96 + 0,12 = 2,08 ml. Al "
             "sur está el muro de la Sala 01 (B.1.2) y al norte el de los baños (B.5.2)."),
            ("B.4.2", "Techumbre", "m²", 9.80, 92000, "Sup. útil rotulada: 5,00 × 1,96 = 9,80 ✓ (plano v3)."),
            ("B.4.3", "Estructura de cielo", "m²", 9.80, 32000, "Sup. útil."),
            ("B.4.4", "Porcelanato de piso", "m²", 9.80, 95000, "Sup. útil. Ver B.1.5."),
            ("B.4.5", "Guardapolvo", "ml", "2*(5+1.96)", 25000, "Perímetro 2 × (5,00 + 1,96) = 13,92."),
            ("B.4.6", "Pintura de muros", "m²", f"2*(5+1.96)*{H}", 7500, "Perímetro 13,92 × 2,40."),
            ("B.4.7", "Pintura de cielo", "m²", 9.80, 7500, "Sup. útil."),
            ("B.4.8", "Puerta simple", "UN", 1, 400000, "Puerta de 0,80 desde el corredor. Precio dado por Gonza."),
        ]),
        ("B.5 Baños 01 y 02 (8,38 + 1,95 m², más vestíbulo de 1,59 m²)", [
            ("B.5.1", "Tabiquería exteriores (h = 2,40)", "m²", f"1.06*{H}", 85000,
             "Solo el muro poniente del vestíbulo, hacia el corredor: entrada 0,80 + 0,26 = 1,06 ml (sin descontar "
             "vanos, como en San Miguel). Los demás muros del bloque colindan con un baño y van en B.5.2."),
            ("B.5.2", "Tabiquería baño (h = 2,40)", "m²", f"(1.54+2.6+3.24+5+2.48+1.5)*{H}", 73000,
             "Todo muro colindante a un baño es tabiquería baño, también hacia afuera (criterio de Luciano, como E.1.1 "
             "de San Miguel). Exteriores: poniente del Baño 02 0,47 + 0,60 + 0,47 = 1,54; oriente 2,48 + 0,12 = 2,60; "
             "norte achurado 3,24 a escala (Supuesto: no tiene cota propia). Interiores: con el Guardar 5,00; entre el "
             "Baño 01 y el Baño 02 con el vestíbulo 2,48; entre el vestíbulo y el Baño 02 0,75 + puerta 0,75 = 1,50."),
            ("B.5.3", "Techumbre", "m²", "3.38*2.48+1.5*1.3+1.5*1.06", 92000,
             "Baño 01 3,38 × 2,48 + Baño 02 1,50 × 1,30 + vestíbulo 1,50 × 1,06 = 11,92 m². Los baños no traen rótulo. "
             "El fondo del Baño 02 (1,30) sale de 2,48 − 1,06 − 0,12 (Supuesto: el muro del vestíbulo no tiene cota "
             "propia)."),
            ("B.5.4", "Estructura de cielo", "m²", "3.38*2.48+1.5*1.3+1.5*1.06", 32000, "Igual a B.5.3."),
            ("B.5.5", "Porcelanato de piso", "m²", "3.38*2.48+1.5*1.3+1.5*1.06", 95000, "Igual a B.5.3."),
            ("B.5.6", "Porcelanato de muro", "m²", f"(2*(3.38+2.48)+2*(1.5+1.3))*{H}", 95000,
             "Perímetros: Baño 01 11,72 + Baño 02 5,60 = 17,32 × 2,40, de piso a cielo (Supuesto: el plano no dice "
             "hasta dónde va)."),
            ("B.5.7", "Pintura de cielo y de muros del vestíbulo", "m²",
             f"3.38*2.48+1.5*1.3+1.5*1.06+2*(1.5+1.06)*{H}", 7500,
             "Cielo 11,92 (igual a B.5.3) + muros del vestíbulo 2 × (1,50 + 1,06) × 2,40 = 12,29."),
            ("B.5.8", "Tabiquería de cubículos", "m²", "(2*1.18+2.48)*2", 150000,
             "Baño 01: 2 divisiones de 1,18 de fondo (medido a escala) + frente de 2,48, de 2,00 de alto (Supuesto: el "
             "plano no da la altura)."),
            ("B.5.9", "Artefactos sanitarios (4 WC y 4 lavamanos)", "UN", 8, 240000,
             "Contados en el plano: Baño 01 con 3 WC y 3 lavamanos, Baño 02 con 1 WC y 1 lavamanos. Correo: mismo tipo "
             "y materiales que lo existente."),
            ("B.5.10", "Grifería de agua potable", "UN", 4, 650000, "Una por lavamanos. " + PU_SM),
            ("B.5.11", "Espejos y accesorios", "UN", 4, 90000, "Uno por lavamanos. " + PU_SM),
            ("B.5.12", "Ventana sanitaria", "UN", 3, 90000,
             "Dos de 0,60 en el muro oriente del Baño 01 (cadena 0,11 · 0,60 · 0,23 · 0,60) y una de 0,60 en el muro "
             "poniente del Baño 02."),
            ("B.5.13", "Puerta simple", "UN", 2, 400000,
             "Una por baño, de 0,75, desde el vestíbulo. Precio dado por Gonza."),
        ]),
    ]),
    ("C", "CORREDOR CUBIERTO Y PATIO", [
        ("C.1 Estructura de acero del corredor", [
            ("C.1.1", "Excavación de fundaciones aisladas (50 × 50 × 50)", "m³", "10*0.5*0.5*0.5", 50000,
             "10 pilares contados en el plano: 3 en el borde norte, 4 en el borde oriente del patio y 3 en el borde "
             "sur. Dado de 50 × 50 × 50 cm por pilar (correo)."),
            ("C.1.2", "Fundaciones aisladas de hormigón", "m³", "10*0.5*0.5*0.5", 220000, PU_SM),
            ("C.1.3", "Pilares de acero 100 × 100 × 3", "kg", "10*2.4*9.14", 8000,
             "10 pilares × 2,40 m × 9,14 kg/m (tubo cuadrado 100 × 100 × 3). Altura 2,40, igual al interior de las "
             "salas (Supuesto: el plano no da la altura del corredor)."),
            ("C.1.4", "Vigas de acero rectangulares 100 × 50 × 3", "kg", "(4.18+10.21+7.5+2.1)*6.78", 8000,
             "Ejes medidos entre pilares en el plano: norte 4,18 + oriente 10,21 + sur 7,50 + remate al muro de la "
             "Sala 03 2,10 = 23,99 ml, × 6,78 kg/m. El correo dice 100 × 50 × 3, pero el plano rotula «150*50*3»: "
             "con 150 × 50 × 3 (9,14 kg/m) son 219 kg en vez de 163 (+$0,45 M). Luciano: confirmar el perfil."),
        ]),
        ("C.2 Techumbre del corredor", [
            ("C.2.1", "Techumbre del corredor", "m²", CORREDOR, 92000,
             "Corredor norte 6,28 × 2,30 + corredor oriente 2,10 × 10,10 + corredor sur 9,60 × 2,00 = 54,85 m². "
             "Cerchas de metalcon apoyadas en las vigas de C.1.4 (correo)."),
            ("C.2.2", "Aleros (0,40 m)", "m²", "(15.64+20.4+7.34+22.18)*0.4", 92000,
             "Largo medido sobre la línea «proy. alero techumbre»: sur 15,64 + oriente 20,40 + poniente de la Sala 03 "
             "y del corredor 7,34 + borde hacia el patio 22,18 = 65,56 ml × 0,40 (Supuesto: medido a escala). Mismo "
             "P.U. que la techumbre."),
        ]),
        ("C.3 Pisos exteriores", [
            ("C.3.1", "Piso antideslizante del corredor", "m²", CORREDOR, 95000,
             "Igual a C.2.1. Correo: modelo distinto al interior y antideslizante. P.U. del porcelanato de San Miguel "
             "(Supuesto)."),
            ("C.3.2", "Piso antideslizante del patio", "m²", PATIO, 95000,
             "Patio descubierto 7,50 × 10,10 (cotas). La cadena de la izquierda suma 10,30 (4 × 2,45 + 5 × 0,10) y no "
             "10,10: si vale 10,30 son 1,5 m² más (+$0,14 M). Se usó la cota total."),
        ]),
    ]),
    ("D", "OBRAS GENERALES", [
        ("D.1 Base y radier (bajo recintos, corredor y patio: 285,71 m²)", [
            ("D.1.1", "Base estabilizada (e = 0,15)", "m³", f"({TOTAL})*0.15", 53000,
             "285,71 m² × 0,15. Correo: «de al menos 15 cm». " + PU_SM),
            ("D.1.2", "Cama de gravilla (e = 0,10)", "m³", f"({TOTAL})*0.10", 53000, "285,71 m² × 0,10. " + PU_SM),
            ("D.1.3", "Radier H25 e = 10 cm con malla C-92", "m²", TOTAL, 36000,
             "285,71 m². Correo: H25, 10 cm, malla Acma C-92 o superior, pendiente de 1% hacia el poniente. La malla "
             "se dio por incluida en el P.U., como en San Miguel."),
            ("D.1.4", "Servicio de bomba de hormigón", "UN", 2, 600000,
             "Hormigón: 285,71 × 0,10 = 28,6 m³. En San Miguel un servicio rindió ~17 m³, así que son 2."),
        ]),
        ("D.2 Instalaciones generales", [
            ("D.2.1", "Aguas lluvia: canales y hojalatería", "GL", 1, 1500000,
             "Valor de San Miguel. Ojo: allá F.2.3 trae 1 × 1.500.000 pero el total dice 3.500.000; aquí se usó "
             "1.500.000. Correo: caída hacia afuera de la ampliación."),
            ("D.2.2", "Red de agua potable", "GL", 1, 1700000,
             "Correo: mismos materiales que lo existente. No pasar instalaciones bajo tierra cerca de la araucaria."),
            ("D.2.3", "Red de alcantarillado", "GL", 1, 1500000, "Igual que D.2.2."),
        ]),
        ("D.3 Aseo", [
            ("D.3.1", "Aseo durante la obra", "m²", TOTAL, 2500, "Superficie total intervenida: 285,71 m²."),
            ("D.3.2", "Aseo final", "m²", TOTAL, 2500, "Superficie total intervenida: 285,71 m²."),
        ]),
    ]),
]

GG, UTILIDAD, IVA = 0.05, 0.15, 0.19


def valor(q):
    return eval(q) if isinstance(q, str) else q


def totales():
    """Costo directo por sección y resumen, con las cantidades sin redondear (igual que Excel)."""
    por_sec = {}
    for letra, _, sectores in SECCIONES:
        por_sec[letra] = sum(valor(q) * pu for _, partidas in sectores for _, _, _, q, pu, _ in partidas)
    cd = sum(por_sec.values())
    neto = cd * (1 + GG + UTILIDAD)
    return por_sec, cd, neto, neto * IVA, neto * (1 + IVA)

"""Geometría de San Miguel para el tour, en metros de la lámina A04 (x a la derecha, y hacia abajo). El piso 2 (A05) se
corre +0,60 en x y −0,03 en y: así calzan las marcas de los cortes A, B, C, D, E y F y el muro sur de la casona.
Muros: caras gruesas (1,56) con su interior delgado = existentes; achurado rojo = nuevos. Recintos: relleno del espacio
libre (a 10 cm) con puertas, ventanas y pasos cerrados, desde cada rótulo del plano."""
import json, re, sys
import numpy as np
import pymupdf
from scipy import ndimage as nd
AQUI = sys.argv[1]; sys.path.insert(0, AQUI)
from puertas import puertas as leer_puertas
from artefactos import artefactos as leer_artefactos
sys.path.insert(0, AQUI + '/..')
PL = '<ruta-local> Miguel-A_%s.pdf'
K = 56.69; DX2, DY2 = 0.60, -0.03
def mueve(r, dx, dy): return [round(r[0]+dx, 2), round(r[1]+dx, 2), round(r[2]+dy, 2), round(r[3]+dy, 2)] + list(r[4:])
def rotulos(p):
    M = p.rotation_matrix; out = []
    for b in p.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            t = ''.join(s['text'] for s in l['spans']).strip(); r = pymupdf.Rect(l['bbox'])*M
            out.append((t, (r.x0+r.x1)/2/K, (r.y0+r.y1)/2/K))
    return out
NOMBRES = r'^(Oficina \d+|Sala de Capacitación \d+|Sala de Profesores|Estar (de )?Alumnos \d+|Recepción|Archivo|B\. (Hombres|Mujeres) \d|B\. Universal|\(corredor cubierto\)|\(patio interior descubierto\))$'
def corridas(m, eje):
    # largo de la corrida continua de cada píxel a lo largo del eje
    a = m if eje == 1 else m.T; out = np.zeros(a.shape, int)
    for i, row in enumerate(a):
        d = np.diff(np.concatenate(([0], row.astype(int), [0]))); s0 = np.where(d == 1)[0]; e0 = np.where(d == -1)[0]
        for x, y in zip(s0, e0): out[i, x:y] = y - x
    return out if eje == 1 else out.T
def cerrar(g, G, maxgap=3.3):
    # une extremos de muro alineados: en cada fila (o columna), huecos de hasta 3,3 m entre píxeles de muro orientado
    # a lo largo de ese eje (corrida ≥ 0,3 m en el eje y ≤ 0,6 m en el otro)
    out = g.copy()
    for eje in (1, 0):
        ca = corridas(g, eje); cb = corridas(g, 1 - eje)
        orient = g & (ca >= 0.3*G) & (cb <= 0.6*G)
        a = orient if eje == 1 else orient.T; o = out if eje == 1 else out.T
        for i, row in enumerate(a):
            idx = np.where(row)[0]
            if len(idx) < 2: continue
            salto = np.diff(idx)
            for k in np.where((salto > 1) & (salto <= maxgap*G))[0]:
                o[i, idx[k]:idx[k+1]] = True
    return out
# vanos que el detector leyó como ventana y son corredor abierto (Gonza, 20-oct): el paso con rampa entre las Salas 02 y 03
SIN_VIDRIO = {'04': [[32.41, 33.91, 16.56, 16.72]]}
# el hall del piso 2 no tiene rótulo en la A05 (coordenadas de la A05)
ROTULOS_A_MANO = {'05': [('Hall', '', 18.5, 13.7)]}
# tabique existente del pasillo de los baños 1 (A04): dibujado con línea fina, el detector de caras gruesas no lo toma (Gonza, 20-oct)
MUROS_A_MANO = {'04': [[20.27, 20.39, 12.99, 14.19], [16.82, 16.95, 15.42, 16.44]]}   # + muro bajo la escalera entre el tramo 1 y los baños 1 (Gonza: «ahí va muro»)
# Muro sur de la casona hacia la Sala de Profesores (A04): las ventanas de los baños quedan cerradas por el tabique nuevo
# y salen 5 shafts de extracción forzada; el detector mezclaba ventanas, pasos y una baranda falsos. B. Universal: el
# «paso» que lo partía no existe y la esquina noroeste es muro (Gonza, 20-oct: «baño universal descuadrado»)
LIMPIAR = {'04': [([28.7, 37.4, 12.08, 12.32], {}), ([28.7, 37.4, 14.88, 15.12], {}), ([28.7, 28.95, 12.2, 15.0], {}),
                  ([35.1, 35.32, 12.25, 14.95], {}), ([30.85, 31.05, 12.25, 14.95], {}), ([32.95, 33.2, 12.25, 14.95], {}),
                  ([34.24, 36.7, 10.4, 10.72], {'vanos': [[34.27, 36.66, 10.46, 10.64, 'ventana', 2.39]]}),
                  ([9.9, 11.9, 16.4, 16.7], {'nuevo': [[10.88, 11.87, 16.44, 16.64]]}),   # muro de 1,00 de la Oficina 01; la puerta de 0,95 queda libre (A04)
                  ([23.6, 23.95, 14.8, 16.1], {}),   # B. Hombres 2: el vano que une sus dos mitades no parte el recinto
([35.2, 37.35, 14.9, 15.1], {}), ([35.2, 37.35, 12.1, 12.3], {}), ([37.3, 37.65, 12.25, 14.7], {'vanos': [[37.37, 37.59, 12.3, 14.6, 'ventana', 2.3]]}),   # ventana de 2,30 del Estar Alumnos 02 hacia el corredor (A04; Gonza 20-oct: «falta la ventana»)
                  # tabique con ventana de baño (0,60 × 0,50) del B. Mujeres 2 hacia el patio de servicio (A04; Gonza 20-oct)
                  ([27.1, 27.3, 4.55, 5.39], {'nuevo': [[27.15, 27.25, 4.36, 4.67], [27.15, 27.25, 5.27, 5.58]], 'vanos': [[27.15, 27.25, 4.67, 5.27, 'ventana', 0.6]]}), ([11.8, 23.1, 16.4, 17.0], {'exist': [[11.87, 23.07, 16.44, 16.62]], 'nuevo': [[11.87, 23.07, 16.62, 16.76],
                    [17.97, 18.19, 16.76, 16.96], [19.35, 19.59, 16.76, 16.96], [20.21, 20.53, 16.76, 16.96], [21.17, 21.37, 16.76, 16.96],
                    [22.11, 22.41, 16.76, 16.96]]}),
                  ([27.3, 27.95, 5.9, 8.1], {}), ([27.25, 27.7, 5.6, 5.9], {'nuevo': [[27.25, 27.69, 5.64, 5.84]]})]}
# tabique delgado (5 cm) de la salida lateral, del pasillo de 3,00 entre la Oficina 01 y el cierro de Carmen Mena (A04,
# línea fina): tramo fijo hasta el cierro; la puerta de 0,90 (21,49 a 22,39) la dibuja el tour. No está en el presupuesto
NUEVOS_A_MANO = {'04': [[9.62, 9.67, 22.39, 24.06]]}
# los dos lavamanos del B. Hombres 3 (A05) quedan pegados al muro y el detector no los separa: se leen a escala
ART_A_MANO = {'05': [[19.92, 20.34, 15.42, 15.84, 'lavamanos'], [19.92, 20.34, 15.94, 16.36, 'lavamanos']]}
# recintos cuyo relleno se escapa al exterior (coordenadas de la lámina propia)
# últimos tramos del patio junto al muro de la Sala 03: barandas, no ventanas (Gonza: «ahí no van ventanas»)
BARANDAS_A_MANO = {'04': [[28.87, 30.91, 12.14, 12.26], [31.01, 33.03, 12.14, 12.26], [33.15, 35.17, 12.14, 12.26], [35.25, 37.37, 12.14, 12.26],
                          [28.87, 30.91, 14.94, 15.06], [31.01, 33.03, 14.94, 15.06], [33.15, 35.17, 14.94, 15.06], [35.25, 37.37, 14.94, 15.06],
                          [28.77, 28.89, 12.26, 13.54], [28.77, 28.89, 13.66, 14.94]]}
# postes de 0,10 de la baranda del patio (A04)
POSTES_PATIO = [[x, x + 0.1, y, y + 0.12] for x in (28.77, 30.91, 33.03, 35.17) for y in (12.14, 14.94)] + [[28.77, 28.89, 13.54, 13.66]]
CAJA_RECINTO = {'04': {'B. Mujeres 2': [22.6, 27.13, 4.36, 7.32]}}
CORTES = {'04': [('Sala de Capacitación 01', 'y', 10.45, '>', 'Corredor cubierto'),
                 ('Recepción', 'y', 12.7, '>', 'Hall de acceso')],
          '05': [('Oficina 03', 'x', 22.3, '<', 'Hall')]}
def piso(n, js, dx, dy):
    p = pymupdf.open(PL % n)[0]; d = json.load(open(js))
    for z, nuevos in LIMPIAR.get(n, []):     # franjas que se rehacen a mano (lectura confusa del plano)
        dentro = lambda r: z[0] <= (r[0]+r[1])/2 <= z[1] and z[2] <= (r[2]+r[3])/2 <= z[3]
        for k in ('exist', 'nuevo', 'vanos'): d[k] = [r for r in d[k] if not dentro(r)]
        for k, rs in nuevos.items(): d[k] = d[k] + rs
    d['exist'] = d['exist'] + MUROS_A_MANO.get(n, [])
    d['nuevo'] = d['nuevo'] + NUEVOS_A_MANO.get(n, []) + (POSTES_PATIO if n == '04' else [])
    R = d['exist'] + d['nuevo']
    pu = leer_puertas(p, R)
    va = [v for v in d['vanos'] if min(v[1]-v[0], v[3]-v[2]) <= 0.5]
    rot = rotulos(p); recs = []
    for i, (t, x, y) in enumerate(rot):
        if re.match(NOMBRES, t):
            sup = next((re.sub(r'Sup\. útil\s*', '', u).replace('m²', '') for u, a, b in rot if u.startswith('Sup. útil') and abs(a-x) < 0.4 and 0 < b-y < 0.5), '')
            recs.append([t.strip('()').capitalize() if t.startswith('(') else t, sup, x, y])
    recs += [list(r) for r in ROTULOS_A_MANO.get(n, [])]   # espacios sin rótulo en la lámina
    # grilla de recintos a 10 cm
    G = 10; X0, Y0, X1, Y1 = -6.0, 3.5, 46.0, 31.0     # con la vereda y la calzada de las dos calles
    W, H = int((X1-X0)*G), int((Y1-Y0)*G); g = np.zeros((H, W), bool)
    def pinta(r):
        a, b, c, e = r[:4]; g[max(0, int((c-Y0)*G)):int(np.ceil((e-Y0)*G)), max(0, int((a-X0)*G)):int(np.ceil((b-X0)*G))] = True
    for r in R + va + pu + BARANDAS_A_MANO.get(n, []): pinta(r)   # las barandas también cierran (el patio queda aparte)   # (las barandas también cierran: el patio queda aparte)
    g = cerrar(g, G)
    lab, _ = nd.label(~g)
    ids = np.zeros((H, W), np.int16); salas = []
    for nom, sup, x, y in recs:
        k = lab[int((y-Y0)*G), int((x-X0)*G)]
        if k == 0: continue
        if any(s[2] == k for s in salas):   # el mismo espacio con dos rótulos (corredor): un solo recinto
            continue
        salas.append([nom, sup, int(k)])
    # quitar regiones gigantes (exterior) y numerar 1..n
    for i, s in enumerate(salas, 1):
        m = lab == s[2]
        caja = CAJA_RECINTO.get(n, {}).get(s[0])
        if caja:   # el relleno se escapa por un vano sin cerrar: se acota a la caja del recinto
            yy, xx = np.mgrid[0:H, 0:W]; x_, y_ = xx/G + X0, yy/G + Y0
            m = m & (x_ > caja[0]) & (x_ < caja[1]) & (y_ > caja[2]) & (y_ < caja[3])
        elif m.sum() > 0.9*W*H*0.5: s.append(False); continue
        ids[m] = i; s.append(True)
    # cortes a mano donde el relleno junta dos espacios (puerta sin arco o vano ancho), en metros de la lámina propia
    for nom, eje, corte, lado, nuevo in CORTES.get(n, []):
        i = next((j for j, s in enumerate(salas, 1) if s[0] == nom), None)
        if not i: continue
        yy, xx = np.mgrid[0:H, 0:W]; v = (xx/G + X0) if eje == 'x' else (yy/G + Y0)
        m = (ids == i) & ((v > corte) if lado == '>' else (v < corte))
        if m.any(): salas.append([nuevo, '', 0, True]); ids[m] = len(salas)
    # cerrar el grosor de muro dentro del recinto vecino no: celdas de muro quedan en 0
    fila = lambda r: ''.join(chr(48+int(v)) for v in r)   # 0 = nada, 1.. = recinto (chr desde '1')
    grilla = [fila(r) for r in ids]
    mv = lambda L: [mueve(r, dx, dy) for r in L]
    # barandas: «ventanas» entre dos pilarcitos (≤ 0,3 × 0,3), como el borde del patio (baranda h = 0,95 en la A04)
    poste = [r for r in R if r[1]-r[0] <= 0.3 and r[3]-r[2] <= 0.3]
    def toca(x, y): return any(a-0.06 <= x <= b+0.06 and c-0.06 <= y <= e+0.06 for a, b, c, e in poste)
    def es_baranda(v):
        a, b, c, e = v[:4]
        if b-a > e-c: return toca(a, (c+e)/2) and toca(b, (c+e)/2)
        return toca((a+b)/2, c) and toca((a+b)/2, e)
    def entre_postes(v):    # «paso» que cruza el patio de un pilarcito a otro: no existe
        a, b, c, e = v[:4]
        return (toca(a, c) or toca(a, e) or toca((a+b)/2, c)) and (toca(b, e) or toca(b, c) or toca((a+b)/2, e)) and max(b-a, e-c) > 1.5
    va = [v for v in va if not (v[4] == 'paso' and entre_postes(v))]
    en_casona = lambda v: 10.0 < (v[0]+v[1])/2 < 26.7 and 8.1 < (v[2]+v[3])/2 < 17.0
    ven = [v for v in va if v[4] == 'ventana' and (not es_baranda(v) or en_casona(v)) and not any(abs(v[0]-q[0]) < 0.05 and abs(v[2]-q[2]) < 0.05 for q in SIN_VIDRIO.get(n, []))]
    bar = [v for v in va if v[4] == 'ventana' and es_baranda(v) and not en_casona(v)]   # dentro de la casona no hay barandas (bow window)
    # artefactos sanitarios (símbolos del plano), sin los círculos de las mesas del Estar de Alumnos 01
    art = [a for a in leer_artefactos(p, dx, dy) if not (17.0 < (a[0]+a[1])/2 < 22.5 and 8.8 < (a[2]+a[3])/2 < 12.6)]
    art += [[round(a[0]+dx, 2), round(a[1]+dx, 2), round(a[2]+dy, 2), round(a[3]+dy, 2), a[4]] for a in ART_A_MANO.get(n, [])]
    return dict(artefactos=art, exist=mv(d['exist']), nuevo=mv(d['nuevo']), barandas=mv([v[:4] for v in bar] + BARANDAS_A_MANO.get(n, [])),
                ventanas=mv([v[:4] for v in ven]),
                pasos=mv([v[:4] for v in va if v[4] == 'paso'] + [q for q in SIN_VIDRIO.get(n, [])]),
                puertas=[mueve(q[:4], dx, dy) + [q[4], [round(q[5][0]+dx, 2), round(q[5][1]+dy, 2)], [round(q[6][0]+dx, 2), round(q[6][1]+dy, 2)]] for q in pu],
                recintos=[[s[0], s[1]] for s in salas], grilla=grilla, origen=[X0+dx, Y0+dy], G=G)
P1 = piso('04', AQUI + '/../m04b.json', 0, 0)
P2 = piso('05', AQUI + '/../m05.json', DX2, DY2)
json.dump(dict(P1=P1, P2=P2), open(sys.argv[2], 'w'), ensure_ascii=False)
for k, P in (('P1', P1), ('P2', P2)):
    print(k, {c: len(v) for c, v in P.items() if isinstance(v, list)}, [r[0] for r in P['recintos']])

# ---------------------------------------------------------------- escalera, zonas caminables, pisos, equipos de AC
NPT1, NPT2 = 0.10, 3.70
SUBE = (NPT2 - NPT1) / 22          # 22 contrahuellas (peldaños 01 a 21 y la llegada 22)
ESC = []                           # (x0, x1, y0, y1, n.º de peldaño) en metros de la A04
for k in range(1, 8):  ESC.append([round(14.87 + (k-1)*0.279, 3), round(14.87 + k*0.279, 3), 15.49, 16.41, k])
for k in range(8, 17): ESC.append([round(16.82 + (k-8)*0.279, 3), round(16.82 + (k-7)*0.279, 3), 15.49, 16.41, k])
ESC.append([19.33, 20.25, 15.49, 16.41, 17])                   # descanso en la esquina
for i, k in enumerate(range(18, 22)): ESC.append([19.33, 20.25, round(15.49 - (i+1)*0.275, 3), round(15.49 - i*0.275, 3), k])
LOTE = [[4.26, 4.26], [43.73, 4.26], [43.73, 24.26], [7.09, 24.26], [4.26, 21.43]]
CASONA = [11.4, 26.7, 8.1, 17.0]   # caja de la casona en planta (A04)
BANOS_NUEVOS = [21.9, 29.0, 4.2, 8.8]
# Lo que se demuele (A02 «superficies que se demuelen», corrida +1,70 / +1,70 para calzar la casona con la A04), con su
# superficie rotulada; las fotos del 08-oct muestran construcciones de un piso con techo de zinc y zócalo azul
DEMOLER = [["B", "Restaurante (cocina)", "16,91", [22.7, 27.3, 7.2, 11.3]],
           ["C", "Construcción del fondo", "45,32", [30.6, 43.7, 19.8, 24.1]],
           ["E", "Ampliación baños", "8,21", [24.3, 27.8, 20.1, 22.6]]]
# Cierre exterior hacia las dos calles (A04 y F.3.3): muro bajo de 0,90 × 0,37 con reja, 9 pilares de 0,45 que suben a
# 2,60, portón del frente, acceso peatonal (0,87, A04), portón de la calle lateral (Carmen Mena) y el tramo final ciego de
# 3,37 hasta la esquina oriente. Tramos: (punto a, punto b, espesor, alto, tipo)
CIERRE = [[[4.43, 7.89], [4.43, 14.33], 0.37, 0.9, "reja"], [[4.43, 15.2], [4.43, 21.2], 0.37, 0.9, "reja"],
          [[4.43, 21.2], [7.3, 23.97], 0.37, 0.9, "reja"], [[7.3, 23.97], [27.5, 23.97], 0.37, 0.9, "reja"],
          [[30.72, 24.07], [43.73, 24.07], 0.21, 3.37, "ciego"]]
PILARES_CIERRE = [[4.43, 7.89], [4.43, 14.1], [4.43, 15.43], [4.43, 21.2], [7.3, 23.97], [12.39, 23.97], [17.44, 23.97],
                  [22.49, 23.97], [27.5, 23.97]]
PORTONES = [[[4.43, 4.4], [4.43, 7.67], "Portón del frente"], [[27.73, 24.0], [30.72, 24.0], "Portón de la calle lateral (F.3.2)"]]
# Corredores cubiertos (F.2.1 y F.2.2, A04): alrededor del patio, pasillos de 1,50 con vuelo de 0,40 hacia el hueco de
# 8,10 × 1,90 que queda descubierto; al frente de las salas, alero de 1,60 con baranda de 0,95 y sus retornos
CORREDORES = dict(patio=[27.3, 37.4, 10.64, 16.56], hueco=[29.27, 37.37, 12.64, 14.54], patio_npt=-0.20, frente=[18.6, 39.32, 21.0, 22.6],
                  baranda_frente=[[[18.6, 22.54], [39.32, 22.54]], [[18.6, 21.1], [18.6, 22.54]], [[39.32, 21.1], [39.32, 22.54]]])
CALLE = dict(vereda_oriente=[-0.6, 4.26], vereda_sur=[24.26, 27.2], calzada_x=-6.0, calzada_y=31.0)
# Pórtico de acceso de la casona (A04 y fotos): dos pilares, rampa i = 6,45 % de 1,55 × 2,15 y techo bajo el alero
PORTICO = dict(pilares=[[9.61, 9.93, 12.78, 13.11], [9.61, 9.93, 16.43, 16.74]], techo=[9.45, 11.85, 12.6, 16.9],
               rampa=[8.06, 9.61, 13.6, 15.75], antepecho=[9.93, 11.85, 12.85, 13.05])

def en_poligono(x, y, poli):
    c = np.zeros(x.shape, bool)
    for (xi, yi), (xj, yj) in zip(poli, poli[-1:] + poli[:-1]):
        cruza = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi)
        c ^= cruza
    return c
def rle(m):
    # una fila por línea: largos alternados empezando por «no»
    filas = []
    for r in m:
        d = np.flatnonzero(np.diff(np.concatenate(([0], r.astype(int), [0]))))
        largos = np.diff(np.concatenate(([0], d, [len(r)])))
        filas.append(",".join(map(str, largos[:-1] if len(largos) and largos[-1] == 0 else largos)))
    return filas
def caminable(P, semilla):
    """Celdas donde puede estar el centro de la persona (radio 0,2): libres de muros y ventanas, dentro del lote y unidas
    a la semilla (así el techo del piso 1 no cuenta como piso 2)."""
    G = P['G']; X0, Y0 = P['origen']; H = len(P['grilla']); W = len(P['grilla'][0])
    g = np.zeros((H, W), bool)
    for k in ('exist', 'nuevo', 'ventanas', 'barandas'):
        for a, b, c, e in (r[:4] for r in P[k]):
            g[max(0, int((c-Y0)*G)):int(np.ceil((e-Y0)*G)), max(0, int((a-X0)*G)):int(np.ceil((b-X0)*G))] = True
    yy, xx = np.mgrid[0:H, 0:W]; x = xx/G + X0 + 0.05; y = yy/G + Y0 + 0.05
    if P is P1:   # el cierre exterior también se choca (salvo el acceso peatonal y el portón lateral, abiertos)
        for (ax, ay), (bx, by), e, h, t in CIERRE:
            L = max(abs(bx-ax), abs(by-ay)); n = int(L*G*2) + 2
            for f in np.linspace(min(0.5, e/2/L), max(0.5, 1-e/2/L), n):   # sin pasarse del extremo (deja libre el acceso)
                cx, cy = ax + (bx-ax)*f, ay + (by-ay)*f
                g[max(0, int((cy-e/2-Y0)*G)):int((cy+e/2-Y0)*G)+1, max(0, int((cx-e/2-X0)*G)):int((cx+e/2-X0)*G)+1] = True
        for (ax, ay), (bx, by), t in PORTONES:   # los dos portones quedan cerrados (Gonza, 20-oct)
            g[int((min(ay, by)-0.1-Y0)*G):int((max(ay, by)+0.1-Y0)*G)+1, int((min(ax, bx)-0.1-X0)*G):int((max(ax, bx)+0.1-X0)*G)+1] = True
        zona = (x > -5.5) & (y > 4.26) & (x < 43.73) & (y < 30.5)
    else:
        zona = en_poligono(x, y, LOTE)
    # holgura de 0,16 m desde muros y ventanas (antes 0,20): las puertas de 0,80 dejan pasar con comodidad
    libre = (nd.distance_transform_edt(~g) >= 1.6) & zona
    lab, _ = nd.label(libre); k = lab[int((semilla[1]-Y0)*G), int((semilla[0]-X0)*G)]
    return lab == k
def pisos(P):
    """Recintos como rectángulos (corridas por fila unidas hacia abajo), para pisos y cielos."""
    G = P['G']; X0, Y0 = P['origen']; ids = np.array([[ord(c)-48 for c in r] for r in P['grilla']])
    out = []; abiertos = {}
    for i, row in enumerate(ids):
        d = np.flatnonzero(np.diff(np.concatenate(([0], row, [0])))) ; corr = {}
        j = 0
        while j < len(row):
            if row[j] == 0: j += 1; continue
            k = j
            while k < len(row) and row[k] == row[j]: k += 1
            corr[(j, k, int(row[j]))] = True; j = k
        nuevos = {}
        for key in corr:
            nuevos[key] = abiertos.pop(key, i)
        for (a, b, idr), i0 in abiertos.items(): out.append([round(X0+a/G, 2), round(X0+b/G, 2), round(Y0+i0/G, 2), round(Y0+i/G, 2), idr])
        abiertos = nuevos
    for (a, b, idr), i0 in abiertos.items(): out.append([round(X0+a/G, 2), round(X0+b/G, 2), round(Y0+i0/G, 2), round(Y0+len(ids)/G, 2), idr])
    return out
M1 = caminable(P1, (1.5, 14.75)); M2 = caminable(P2, (20.4, 13.6))
ids2 = np.array([[ord(c)-48 for c in r] for r in P2['grilla']]) > 0
M2 &= nd.binary_dilation(ids2, np.ones((7, 7)))      # el piso 2 son sus recintos: no se camina por el techo
P1['camina'] = rle(M1); P2['camina'] = rle(M2)
P1['pisos'] = pisos(P1); P2['pisos'] = pisos(P2)
SITIO = dict(CORREDORES=CORREDORES, CIERRE=CIERRE, PILARES_CIERRE=PILARES_CIERRE, PORTONES=PORTONES, CALLE=CALLE, DEMOLER=DEMOLER, PORTICO=PORTICO, P1=P1, P2=P2, ESC=ESC, NPT1=NPT1, NPT2=NPT2, SUBE=SUBE, LOTE=LOTE, CASONA=CASONA, BANOS_NUEVOS=BANOS_NUEVOS)
for P in (P1, P2): P.pop('grilla_txt', None)
json.dump(SITIO, open(sys.argv[2], 'w'), ensure_ascii=False, separators=(',', ':'))
print('caminable m²: P1', M1.sum()/100, 'P2', M2.sum()/100, '· pisos', len(P1['pisos']), len(P2['pisos']))

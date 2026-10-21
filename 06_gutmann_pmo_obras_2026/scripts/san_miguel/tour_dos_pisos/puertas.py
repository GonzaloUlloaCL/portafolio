"""Puertas desde los arcos de abatimiento: centro (bisagra), radio y el lado cerrado (el que sigue la línea del muro)."""
import pymupdf, math
K = 56.69
def centro(p0, p1, p2, p3):
    # rectas por p0 ⟂ (p1−p0) y por p3 ⟂ (p3−p2)
    d0 = (p1[0]-p0[0], p1[1]-p0[1]); d3 = (p3[0]-p2[0], p3[1]-p2[1])
    n0 = (-d0[1], d0[0]); n3 = (-d3[1], d3[0])
    det = n0[0]*(-n3[1]) - n0[1]*(-n3[0])
    if abs(det) < 1e-9: return None
    t = ((p3[0]-p0[0])*(-n3[1]) - (p3[1]-p0[1])*(-n3[0])) / det
    return (p0[0]+t*n0[0], p0[1]+t*n0[1])
def arcos(p):
    M = p.rotation_matrix; out = []
    for d in p.get_drawings():
        col = tuple(round(x, 2) for x in (d.get('color') or ()))
        for it in d['items']:
            if it[0] != 'c': continue
            q = [((v*M).x/K, (v*M).y/K) for v in it[1:5]]
            c = centro(*q)
            if not c: continue
            r0 = math.dist(c, q[0]); r3 = math.dist(c, q[3])
            if abs(r0-r3) > 0.03 or not (0.5 <= r0 <= 1.3): continue
            ang = abs(math.degrees(math.atan2(q[0][1]-c[1], q[0][0]-c[0]) - math.atan2(q[3][1]-c[1], q[3][0]-c[0]))) % 360
            if ang > 180: ang = 360 - ang
            if not (70 <= ang <= 110): continue
            out.append((c, q[0], q[3], r0))
    return out
def en(R, x, y, m=0.02):
    return any(a-m <= x <= b+m and c-m <= y <= d+m for a, b, c, d in R)
def puertas(p, R):
    """Devuelve [x0,x1,y0,y1, ancho, bisagra, tip_abierto]: el vano de la hoja cerrada, con el espesor del muro vecino."""
    out = []
    for c, a, b, r in arcos(p):
        mejor = None; puntaje = 0
        for cerr, abie in ((a, b), (b, a)):
            ux, uy = (cerr[0]-c[0])/r, (cerr[1]-c[1])/r
            if abs(ux) > 0.15 and abs(uy) > 0.15: continue
            # la hoja cerrada sigue la línea del muro: hay muro detrás de la bisagra y pasado el extremo cerrado (en una
            # puerta doble, el otro lado es la otra hoja). Gana el lado con más muro alrededor
            p = en(R, c[0]-ux*0.12, c[1]-uy*0.12) + en(R, cerr[0]+ux*0.12, cerr[1]+uy*0.12) + \
                0.5 * (en(R, c[0]-ux*0.4, c[1]-uy*0.4) + en(R, cerr[0]+ux*0.4, cerr[1]+uy*0.4))
            if p > puntaje: mejor = (cerr, abie, ux, uy); puntaje = p
        if not mejor: continue
        cerr, abie, ux, uy = mejor
        # espesor: muro vecino que toca la bisagra
        e = 0.15; cx = cy = None
        for x0, x1, y0, y1 in R:
            if abs(ux) > abs(uy) and x0-0.15 <= c[0] <= x1+0.15 and y0-0.05 <= c[1] <= y1+0.05 and y1-y0 < 0.5: e = y1-y0; cy = (y0+y1)/2
            if abs(uy) > abs(ux) and y0-0.15 <= c[1] <= y1+0.15 and x0-0.05 <= c[0] <= x1+0.05 and x1-x0 < 0.5: e = x1-x0; cx = (x0+x1)/2
        if abs(ux) > abs(uy):
            ym = cy if cy is not None else c[1]
            v = [min(c[0], cerr[0]), max(c[0], cerr[0]), ym-e/2, ym+e/2]
        else:
            xm = cx if cx is not None else c[0]
            v = [xm-e/2, xm+e/2, min(c[1], cerr[1]), max(c[1], cerr[1])]
        k = [round(z, 2) for z in v] + [round(r, 2), [round(c[0], 2), round(c[1], 2)], [round(abie[0], 2), round(abie[1], 2)]]
        if not any(abs(k[0]-o[0]) < 0.05 and abs(k[2]-o[2]) < 0.05 for o in out): out.append(k)
    return out

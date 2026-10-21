"""Artefactos sanitarios desde los símbolos del plano: los trazos curvos finos (0,48) forman figuras cerradas de 0,25 a
0,85 m. Se rasterizan a 2 cm, se rellenan y cada figura cerrada es un artefacto; su forma dice cuál es."""
import pymupdf, sys, json
import numpy as np
from scipy import ndimage as nd
K = 56.69; R = 50
def artefactos(p, dx=0.0, dy=0.0):
    M = p.rotation_matrix; seg = []
    for d in p.get_drawings():
        if tuple(round(x, 2) for x in (d.get('color') or ())) != (0, 0, 0) or round(d.get('width') or 0, 2) != 0.48: continue
        r = pymupdf.Rect(d['rect'] * M).normalize()
        if r.width/K > 0.9 or r.height/K > 0.9: continue
        for it in d['items']:
            pts = [it[1], it[2]] if it[0] == 'l' else list(it[1:5]) if it[0] == 'c' else []
            pts = [(q*M) for q in pts]
            for a, b in zip(pts, pts[1:]): seg.append((a.x/K, a.y/K, b.x/K, b.y/K))
    X0, Y0 = 0, 0; W, H = int(56*R), int(31*R); g = np.zeros((H, W), bool)
    for x0, y0, x1, y1 in seg:
        n = int(max(abs(x1-x0), abs(y1-y0))*R*2) + 2
        xs = (np.linspace(x0, x1, n)*R).astype(int).clip(0, W-1); ys = (np.linspace(y0, y1, n)*R).astype(int).clip(0, H-1)
        g[ys, xs] = True
    g = nd.binary_dilation(g, np.ones((2, 2)))
    f = nd.binary_fill_holes(g); lab, n = nd.label(f); out = []
    for i, sl in enumerate(nd.find_objects(lab), 1):
        h, w = (sl[0].stop-sl[0].start)/R, (sl[1].stop-sl[1].start)/R
        area = (lab[sl] == i).sum()/R/R
        if not (0.25 <= max(w, h) <= 0.85 and min(w, h) >= 0.2): continue
        if area < 0.45*w*h: continue          # figura cerrada (no un arco de puerta)
        x0, x1, y0, y1 = sl[1].start/R, sl[1].stop/R, sl[0].start/R, sl[0].stop/R
        largo = max(w, h)/min(w, h)
        tipo = "WC" if largo >= 1.3 else "urinario" if max(w, h) < 0.42 else "lavamanos"
        out.append([round(x0+dx, 2), round(x1+dx, 2), round(y0+dy, 2), round(y1+dy, 2), tipo])
    return out
if __name__ == '__main__':
    p = pymupdf.open(sys.argv[1])[0]
    A = artefactos(p)
    from collections import Counter; print(Counter(a[4] for a in A))
    for a in A: print(a)

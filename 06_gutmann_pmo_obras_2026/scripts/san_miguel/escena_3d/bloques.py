"""Grilla de muros -> rectángulos en metros + vanos (puertas/ventanas) entre tramos colineales."""
import sys, json, numpy as np
D = sys.argv[1]; RES = 0.05
def rects(m):
    m = m.copy(); out = []
    H, W = m.shape
    for y in range(H):
        x = 0
        while x < W:
            if not m[y, x]: x += 1; continue
            x1 = x
            while x1 < W and m[y, x1]: x1 += 1
            y1 = y + 1
            while y1 < H and m[y1, x:x1].all() and (x1 == W or not m[y1, x1]) and (x == 0 or not m[y1, x-1]): y1 += 1
            m[y:y1, x:x1] = False
            out.append([x*RES, y*RES, (x1-x)*RES, (y1-y)*RES]); x = x1
    return out
red = np.load(D+'/red.npy'); ex = np.load(D+'/ex.npy') & ~red
R = [r for r in rects(red) if r[2]*r[3] > 0.004]
E = [r for r in rects(ex) if r[2]*r[3] > 0.004]
def vanos(rs, maxg=3.2):
    out = []
    for horiz in (True, False):
        segs = [r for r in rs if (r[3] <= 0.35 if horiz else r[2] <= 0.35) and (r[2] if horiz else r[3]) >= 0.3]
        for a in segs:
            for b in segs:
                if a is b: continue
                if horiz:
                    if abs(a[1]-b[1]) > 0.06 or abs(a[3]-b[3]) > 0.06: continue
                    g0, g1 = a[0]+a[2], b[0]
                else:
                    if abs(a[0]-b[0]) > 0.06 or abs(a[2]-b[2]) > 0.06: continue
                    g0, g1 = a[1]+a[3], b[1]
                g = g1 - g0
                if 0.5 <= g <= maxg and not 1.45 <= g <= 1.55:
                    # que no haya otro tramo entre medio
                    if horiz: r = [g0, a[1], g, a[3]]
                    else: r = [a[0], g0, a[2], g]
                    out.append(r + ['puerta' if g < 1.1 else 'ventana'])
    # quitar duplicados y vanos que contienen otro más chico
    out = sorted({tuple(round(v, 2) if isinstance(v, float) else v for v in o) for o in out}, key=lambda o: (o[2]*o[3], o[0], o[1]))  # orden fijo: el set no garantiza orden
    keep = []
    for o in out:
        if any(k[0] >= o[0]-.01 and k[1] >= o[1]-.01 and k[0]+k[2] <= o[0]+o[2]+.01 and k[1]+k[3] <= o[1]+o[3]+.01 for k in keep): continue
        keep.append(o)
    return keep
VR = vanos(R, 2.8); VE = [v for v in vanos(E, 1.1) if v[4] == 'puerta']
xs = [r[0] for r in R+E] + [r[0]+r[2] for r in R+E]; ys = [r[1] for r in R+E] + [r[1]+r[3] for r in R+E]
print(len(R), len(E), len(VR), len(VE), min(xs), max(xs), min(ys), max(ys))
from collections import Counter
print(Counter(v[4] for v in VR), Counter(v[4] for v in VE))
json.dump({'nuevos': R, 'existentes': E, 'vanos_nuevos': VR, 'vanos_existentes': VE}, open(D+'/muros.json', 'w'))

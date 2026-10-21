"""Uso: python -I texto.py <plano.pdf> [x0 y0 x1 y1] -> bloques con coords de la vista"""
import sys, pymupdf
p = pymupdf.open(sys.argv[1])[0]; m = p.rotation_matrix
caja = pymupdf.Rect(*map(float, sys.argv[2:6])) if len(sys.argv) > 2 else None
f = []
for b in p.get_text("blocks"):
    r = pymupdf.Rect(b[:4]) * m; t = b[4].strip().replace("\n", " | ")
    if t and (caja is None or caja.intersects(r)): f.append((round(r.y0), round(r.x0), t))
for y, x, t in sorted(f): print(f"{x:5d} {y:5d}  {t}")

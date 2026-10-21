"""Extrae muros nuevos (achurado rojo) y existentes (relleno negro) del plano a una grilla de 5 cm."""
import sys, json
import numpy as np, pymupdf
from PIL import Image
PT_M = 72/0.0254/50  # 1:50 -> pt por metro
RES = 0.05
d = pymupdf.open(sys.argv[1]); p = d[0]; M = p.rotation_matrix
W, H = p.rect.width, p.rect.height
nx, ny = int(W/PT_M/RES)+1, int(H/PT_M/RES)+1
red = np.zeros((ny, nx), bool); blk = np.zeros((ny, nx), bool)
def marcar(g, r):
    r = r * M
    x0, y0, x1, y1 = [int(v/PT_M/RES) for v in (r.x0, r.y0, r.x1, r.y1)]
    g[min(y0,y1):max(y0,y1)+1, min(x0,x1):max(x0,x1)+1] = True
for x in p.get_drawings():
    col = x.get('color'); fill = x.get('fill')
    if x['type'] == 's' and col and col[0] > .9 and col[1] < .1:
        for it in x['items']:
            if it[0] == 'l': marcar(red, pymupdf.Rect(it[1], it[2]).normalize())
    elif x['type'] == 's' and col == (0.0, 0.0, 0.0) and (x.get('width') or 0) >= float(sys.argv[3]):
        for it in x['items']:
            if it[0] == 'l': marcar(blk, pymupdf.Rect(it[1], it[2]).normalize())
            elif it[0] == 're': marcar(blk, it[1])
np.save(sys.argv[2]+'/red.npy', red); np.save(sys.argv[2]+'/blk.npy', blk)
img = np.full((ny, nx, 3), 255, np.uint8); img[blk] = (0,0,0); img[red] = (220,40,40)
Image.fromarray(img).save(sys.argv[2]+'/grid.png'); print(nx, ny, red.sum(), blk.sum())

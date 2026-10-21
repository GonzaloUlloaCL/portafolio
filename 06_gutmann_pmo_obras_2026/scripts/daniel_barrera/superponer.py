"""Uso: python -I superponer.py <arq.pdf> x0 y0 x1 y1 <esp.pdf> s tx ty dpi <salida.png>
Arquitectura en gris, instalaciones en color encima, en la grilla de la arquitectura."""
import sys, numpy as np, pymupdf
from PIL import Image
arq = sys.argv[1]; ac = list(map(float, sys.argv[2:6])); esp = sys.argv[6]
s, tx, ty, dpi = map(float, sys.argv[7:11]); z = dpi / 72
def gris(pdf, clip, zoom):
    p = pymupdf.open(pdf)[0]
    pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(clip), colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(np.float32) / 255
A = gris(arq, ac, z)
ec = [s*ac[0]+tx, s*ac[1]+ty, s*ac[2]+tx, s*ac[3]+ty]
E = gris(esp, ec, z / s)
h = min(A.shape[0], E.shape[0]); w = min(A.shape[1], E.shape[1]); A = A[:h, :w]; E = E[:h, :w]
ia = 1 - A; ie = 1 - E                       # tinta 0..1
img = np.ones((h, w, 3), np.float32)
gris_c = np.array([0.55, 0.55, 0.55]); col = np.array([0.85, 0.10, 0.45])
img = img * (1 - ia[..., None] * 0.75) + 0                       # arquitectura gris
img = img * (1 - ie[..., None]) + ie[..., None] * col              # instalaciones en magenta encima
Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(sys.argv[11])

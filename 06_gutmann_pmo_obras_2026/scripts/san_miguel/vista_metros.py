"""Recorte del plano en metros con grilla de 1 m (rótulos) y 0,5 m, para leer coordenadas.

Uso: python -I vista_metros.py <plano.pdf> x0 y0 x1 y1 px_por_m <salida.png>
"""
import sys

import pymupdf
from PIL import Image, ImageDraw, ImageFont

PT_M = 72 / 0.0254 / 50  # 1:50
pdf, x0, y0, x1, y1, k, out = sys.argv[1], *map(float, sys.argv[2:7]), sys.argv[7]
p = pymupdf.open(pdf)[0]
pix = p.get_pixmap(dpi=round(k / PT_M * 72), clip=pymupdf.Rect(x0 * PT_M, y0 * PT_M, x1 * PT_M, y1 * PT_M))
img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
esc = pix.width / (x1 - x0)  # px por metro real del render
d = ImageDraw.Draw(img, "RGBA")
f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
m = int(x0 * 2) / 2
while m <= x1:
    x = (m - x0) * esc
    entero = abs(m - round(m)) < 1e-9
    d.line([(x, 0), (x, img.height)], fill=(0, 120, 255, 110 if entero else 45), width=1)
    if entero:
        d.text((x + 2, 2), f"{m:.0f}", fill=(0, 90, 220), font=f)
    m += 0.5
m = int(y0 * 2) / 2
while m <= y1:
    y = (m - y0) * esc
    entero = abs(m - round(m)) < 1e-9
    d.line([(0, y), (img.width, y)], fill=(0, 160, 60, 110 if entero else 45), width=1)
    if entero:
        d.text((2, y + 2), f"{m:.0f}", fill=(0, 130, 40), font=f)
    m += 0.5
img.save(out)
print(out, img.width, "x", img.height, f"{esc:.1f} px/m")

"""Uso: python -I recortar.py <plano.pdf> <salida.png> x0 y0 x1 y1 dpi  (coords de la vista rotada)"""
import sys, pymupdf
p = pymupdf.open(sys.argv[1])[0]
x0, y0, x1, y1, dpi = map(float, sys.argv[3:8])
r = pymupdf.Rect(x0, y0, x1, y1)
p.get_pixmap(dpi=int(dpi), clip=r).save(sys.argv[2])

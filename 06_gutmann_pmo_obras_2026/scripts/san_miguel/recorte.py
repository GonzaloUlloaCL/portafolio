import sys, pymupdf
pdf, x0, y0, x1, y1, dpi, out = sys.argv[1], *map(float, sys.argv[2:7]), sys.argv[7]
p = pymupdf.open(pdf)[0]
pix = p.get_pixmap(dpi=int(dpi), clip=pymupdf.Rect(x0, y0, x1, y1))
pix.save(out); print(out, pix.width, "x", pix.height)

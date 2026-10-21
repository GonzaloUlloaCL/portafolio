"""Rellena el interior de los muros existentes (contornos) sin rellenar recintos."""
import sys, numpy as np
from scipy import ndimage as nd
from PIL import Image
D = sys.argv[1]
red = np.load(D+'/red.npy'); blk = np.load(D+'/blk.npy')
holes = nd.binary_fill_holes(blk) & ~blk
lab, n = nd.label(holes)
fill = np.zeros_like(blk)
for i in range(1, n+1):
    h = lab == i
    if not nd.binary_erosion(h, iterations=8).any():  # más delgado que ~50 cm: es muro
        fill |= h
ex = blk | fill
np.save(D+'/ex.npy', ex)
img = np.full(red.shape+(3,), 255, np.uint8); img[ex] = (0,0,0); img[red] = (220,40,40)
Image.fromarray(img[50:450, 90:860]).resize((1540, 800), Image.NEAREST).save(D+'/grid2.png')
print(n, fill.sum())

"""Calza una planta de instalaciones sobre la de arquitectura.
Uso: python -I registrar.py <arq.pdf> ax0 ay0 ax1 ay1 <esp.pdf> ex0 ey0 ex1 ey1 smin smax
Devuelve s, tx, ty tales que punto_esp = s * punto_arq + (tx, ty)   (coords de la vista, en pt)."""
import sys, numpy as np, pymupdf

def mascara(pdf, clip, zoom):
    p = pymupdf.open(pdf)[0]
    pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(clip), colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(np.float32)
    return (a < 140).astype(np.float32)

def suavizar(m, r=2):
    out = m.copy()
    for _ in range(r):
        out = np.maximum.reduce([out, np.roll(out, 1, 0), np.roll(out, -1, 0), np.roll(out, 1, 1), np.roll(out, -1, 1)])
    return out

def mejor(arq, ac, esp, ec, s, z):
    A = suavizar(mascara(arq, ac, z))
    E = suavizar(mascara(esp, ec, z / s))  # la planta de instalaciones llevada a la escala de la de arquitectura
    H = max(A.shape[0], E.shape[0]) * 2; W = max(A.shape[1], E.shape[1]) * 2
    F = np.fft.irfft2(np.fft.rfft2(E, (H, W)) * np.conj(np.fft.rfft2(A, (H, W))), (H, W))
    i, j = np.unravel_index(np.argmax(F), F.shape)
    dy = i if i < H // 2 else i - H; dx = j if j < W // 2 else j - W
    score = F[i, j] / np.sqrt(A.sum() * E.sum())
    # pixel (u,v) de A corresponde a pixel (u+dx, v+dy) de E
    # A: x_arq = ac0 + u/z ; E: x_esp = ec0 + (u+dx)*s/z
    tx = ec[0] + dx * s / z - s * ac[0]; ty = ec[1] + dy * s / z - s * ac[1]
    return score, tx, ty

arq, esp = sys.argv[1], sys.argv[6]
ac = tuple(map(float, sys.argv[2:6])); ec = tuple(map(float, sys.argv[7:11]))
smin, smax = float(sys.argv[11]), float(sys.argv[12])
res = []
for s in np.linspace(smin, smax, 25):
    res.append((*mejor(arq, ac, esp, ec, s, 1.0), s))
sc, tx, ty, s = max(res)
fino = [(*mejor(arq, ac, esp, ec, s2, 2.0), s2) for s2 in np.linspace(s - (smax-smin)/24, s + (smax-smin)/24, 9)]
sc, tx, ty, s = max(fino)
print(f"s={s:.5f} tx={tx:.2f} ty={ty:.2f} score={sc:.3f}")

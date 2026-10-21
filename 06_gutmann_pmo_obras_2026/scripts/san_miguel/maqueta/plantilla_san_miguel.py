"""Convierte la extracción del plano San Miguel v5 al modelo editable de la maqueta (muros como tramos)."""
import sys, json
D = sys.argv[1]
m = json.load(open(D + '/muros.json')); rc = json.load(open(D + '/recintos.json'))
LOTE = [2.42, 3.86, 41.85, 23.79]
r2 = lambda v: round(v, 3)
muros, vanos, objetos = [], [], []
def tramo(r, estado):
    x, y, w, d = r
    if w >= d: return dict(x1=r2(x), y1=r2(y + d / 2), x2=r2(x + w), y2=r2(y + d / 2), esp=r2(d))
    return dict(x1=r2(x + w / 2), y1=r2(y), x2=r2(x + w / 2), y2=r2(y + d), esp=r2(w))
def alto(r, estado):
    if estado == 'existente': return 2.7 if r[0] < 10 else 3.1
    return 3.5 if (r[1] + r[3] < 4.4 or r[0] > 41.4) else 2.6
n = 0
def nuevo_id(p):
    global n; n += 1; return f'{p}{n}'
for clave, estado in (('nuevos', 'nuevo'), ('existentes', 'existente')):
    for r in m[clave]:
        if r[2] < 0.3 and r[3] < 0.3:  # pilar
            objetos.append(dict(id=nuevo_id('o'), tipo='pilar', x=r2(r[0] + r[2] / 2), y=r2(r[1] + r[3] / 2), rot=0))
            continue
        t = tramo(r, estado)
        muros.append(dict(id=nuevo_id('m'), **t, alto=alto(r, estado), mat='albanileria', estado=estado))
for clave, estado in (('vanos_nuevos', 'nuevo'), ('vanos_existentes', 'existente')):
    for v in m[clave]:
        r = v[:4]; t = tramo(r, estado)
        mid = nuevo_id('m')
        muros.append(dict(id=mid, **t, alto=alto(r, estado), mat='albanileria', estado=estado))
        L = max(r[2], r[3])
        if v[4] == 'ventana':
            vanos.append(dict(id=nuevo_id('v'), muro=mid, tipo='ventana', s=r2(L / 2), ancho=r2(L - 0.02), alto=1.2, antepecho=0.9))
        else:
            vanos.append(dict(id=nuevo_id('v'), muro=mid, tipo='puerta', s=r2(L / 2), ancho=r2(L - 0.02), alto=2.1, antepecho=0))
USO = lambda nm: 'bano' if 'Baño' in nm else 'sala' if 'Sala' in nm else 'oficina' if 'Oficina' in nm else 'pasillo' if 'Patio' in nm or 'Hall' in nm else 'estar'
recintos = []
for k, v in rc.items():
    recintos.append(dict(id=nuevo_id('r'), nombre=v['nombre'], x=v['rect'][0], y=v['rect'][1], w=v['rect'][2], d=v['rect'][3],
                         uso=USO(v['nombre']), tipo='remodelacion' if v['tipo'] == 'casona' else 'nueva',
                         piso='Por confirmar', cielo='Por confirmar', retiroPiso=v['tipo'] == 'casona',
                         techo=v['tipo'] == 'nueva' and 'Patio' not in v['nombre'], supRotulada=v['sup']))
# Artefactos contados en el plano, alineados a los muros largos de cada baño
ART = {'Baño Mujeres 1': (1, 1, 0), 'Baño Hombres 1': (1, 1, 0), 'Baño Hombres 2': (2, 3, 2), 'Baño Mujeres 2': (5, 5, 0), 'Baño Universal': (1, 1, 0)}
for rr in recintos:
    if rr['nombre'] not in ART: continue
    wc, lav, uri = ART[rr['nombre']]
    x, y, w, d = rr['x'], rr['y'], rr['w'], rr['d']; lx = w >= d; L = w if lx else d
    for i in range(wc + uri):
        t = (i + .5) / (wc + uri) * L
        objetos.append(dict(id=nuevo_id('o'), tipo='urinario' if i >= wc else 'wc', x=r2(x + t if lx else x + .35), y=r2(y + .35 if lx else y + t), rot=0 if lx else 270))
    for i in range(lav):
        t = (i + .5) / lav * L
        objetos.append(dict(id=nuevo_id('o'), tipo='lavamanos', x=r2(x + t if lx else x + w - .3), y=r2(y + d - .3 if lx else y + t), rot=180 if lx else 90))
proy = dict(version=1, nombre='Sede San Miguel', direccion='Gran Avda. José Miguel Carrera 5670, San Miguel',
            lote=dict(x0=LOTE[0], y0=LOTE[1], x1=LOTE[2], y1=LOTE[3]), muros=muros, vanos=vanos, recintos=recintos, objetos=objetos,
            precios={}, param=dict(gg=5, util=15, iva=19, relleno=None, alero=0.3, alturaMuro=2.6), seq=n)
json.dump(proy, open(sys.argv[2], 'w'), ensure_ascii=False, separators=(',', ':'))
print(len(muros), len(vanos), len(recintos), len(objetos), len(json.dumps(proy)))

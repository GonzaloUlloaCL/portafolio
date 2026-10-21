"""Incrusta muros, recintos, partidas y artefactos en la plantilla y escribe la página."""
import sys, json
D, PLANT, SAL = sys.argv[1], sys.argv[2], sys.argv[3]
datos = {
    'muros': json.load(open(D + '/muros.json')),
    'recintos': json.load(open(D + '/recintos.json')),
    'partidas': json.load(open(D + '/partidas.json')),
    # WC, lavamanos y urinarios contados en el plano (hoja «Cubicación base»)
    'artefactos': {'N': {'wc': 1, 'lav': 1, 'uri': 0}, 'O': {'wc': 1, 'lav': 1, 'uri': 0}, 'P': {'wc': 2, 'lav': 3, 'uri': 2},
                   'Q': {'wc': 5, 'lav': 5, 'uri': 0}, 'R': {'wc': 1, 'lav': 1, 'uri': 0}},
}
def red(o):
    if isinstance(o, float): return round(o, 3)
    if isinstance(o, list): return [red(x) for x in o]
    if isinstance(o, dict): return {k: red(v) for k, v in o.items()}
    return o
txt = open(PLANT).read().replace('/*DATOS*/', json.dumps(red(datos), ensure_ascii=False, separators=(',', ':')))
open(SAL, 'w').write(txt)
print(len(txt))

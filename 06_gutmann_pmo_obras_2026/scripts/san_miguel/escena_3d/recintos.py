"""Rectángulo de cada recinto (m), leído del plano renderizado (1 px = 0,02244 m). Se contrasta con la sup. útil."""
import sys, json
K = 0.02244
PX = {  # letra: (nombre, x0, y0, x1, y1 en px del render, sup. útil rotulada, tipo)
 'C': ('Sala de Capacitación 01', 450, 178, 918, 355, 40.16, 'nueva'),
 'D': ('Sala de Capacitación 02', 1205, 240, 1585, 445, 38.00, 'nueva'),
 'E': ('Sala de Capacitación 03', 1600, 240, 1795, 640, 41.40, 'nueva'),
 'F': ('Sala de Capacitación 04', 1433, 728, 1855, 915, 38.78, 'nueva'),
 'G': ('Sala de Capacitación 05', 905, 728, 1353, 915, 41.09, 'nueva'),
 'H': ('Sala de Capacitación 06', 452, 728, 900, 915, 41.09, 'nueva'),
 'I': ('Sala de Capacitación 07', 250, 718, 445, 915, 18.39, 'nueva'),
 'J': ('Recepción', 460, 368, 590, 520, 13.08, 'casona'),
 'K': ('Hall y circulación', 460, 520, 672, 712, None, 'casona'),
 'L': ('Estar de Alumnos', 678, 372, 935, 550, 22.96, 'casona'),
 'M': ('Oficina 01', 940, 520, 1070, 612, 10.99, 'casona'),
 'N': ('Baño Mujeres 1', 752, 615, 826, 712, None, 'casona'),
 'O': ('Baño Hombres 1', 680, 615, 746, 712, None, 'casona'),
 'P': ('Baño Hombres 2', 830, 615, 1070, 712, None, 'casona'),
 'Q': ('Baño Mujeres 2', 925, 178, 1130, 310, None, 'nueva'),
 'R': ('Baño Universal', 1130, 240, 1200, 345, None, 'nueva'),
 'S': ('Patio y pasillos', 1205, 525, 1585, 650, None, 'exterior'),
}
out = {}
for k, (n, x0, y0, x1, y1, sup, tipo) in PX.items():
    r = [round(x0*K, 2), round(y0*K, 2), round((x1-x0)*K, 2), round((y1-y0)*K, 2)]
    out[k] = {'nombre': n, 'rect': r, 'sup': sup, 'tipo': tipo}
    print(k, n, r, round(r[2]*r[3], 1), sup)
json.dump(out, open(sys.argv[1]+'/recintos.json', 'w'), ensure_ascii=False)

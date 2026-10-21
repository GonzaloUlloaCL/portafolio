import copy, re, sys, openpyxl
src, dst = sys.argv[1], sys.argv[2]
wb = openpyxl.load_workbook(src); ws = wb["Presupuesto"]
REF = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")
def fila(t, col=2): return next(r for r in range(1, ws.max_row + 1) if ws.cell(r, col).value == t)
tot = fila("TOTAL C"); ult = tot - 1; sub = fila("C.7 Sector pasillo")
assert ws.cell(ult, 1).value == "C.7.9"
N = 2
for row in ws.iter_rows():
    for c in row:
        if isinstance(c.value, str) and c.value.startswith("="):
            partes = re.split(r"('[^']*'![$A-Z0-9:]+)", c.value)
            c.value = "".join(p if p.startswith("'") else REF.sub(
                lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{int(m.group(4)) + N if int(m.group(4)) >= tot else m.group(4)}", p) for p in partes)
alturas = {r: d.height for r, d in ws.row_dimensions.items()}
ws.insert_rows(tot, N)
for r in sorted(alturas, reverse=True):
    if r >= tot: ws.row_dimensions[r + N].height = alturas[r]
cf = ws.conditional_formatting; nuevas = []
for rng in list(cf):
    m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", str(rng.sqref))
    if m and int(m.group(4)) >= tot:
        nuevas.append((f"{m.group(1)}{m.group(2)}:{m.group(3)}{int(m.group(4)) + N}", rng.rules)); del cf._cf_rules[rng]
for s, rules in nuevas:
    for rule in rules: cf.add(s, rule)
for k in range(1, 8):
    ws.cell(tot, k)._style = copy.copy(ws.cell(sub, k)._style)
    ws.cell(tot + 1, k)._style = copy.copy(ws.cell(ult, k)._style)
ws.row_dimensions[tot].height = alturas.get(sub); ws.row_dimensions[tot + 1].height = alturas.get(ult)
ws.cell(tot, 2).value = "C.8 Sector techumbre casona"
r = tot + 1
ws.cell(r, 1).value, ws.cell(r, 2).value, ws.cell(r, 3).value, ws.cell(r, 4).value = "C.8.1", "Techumbre casona (global)", "m²", "=14.2*8.3"
ws.cell(r, 6).value = f"=D{r}*E{r}"
ws.cell(r, 7).value = ("Partida global: a diferencia del resto del presupuesto, que va por sector, la techumbre de la casona es una sola "
    "cubierta sobre todos los recintos de los pisos 1 y 2 (zonas B y C), por eso se agrupa en una sola partida. Superficie en planta "
    "de la casona, la misma usada para la fachada del piso 2 (F.2.3): 14,20 × 8,30 = 117,86 m². No incluye alero (no figura en el plano) "
    "ni pendiente; la losa del bow window va aparte en B.2.5. Alcance (reparación o reemplazo) por confirmar.")
t = tot + 2
assert ws.cell(t, 2).value == "TOTAL C"
ws.cell(t, 6).value = re.sub(r"F(\d+)\)$", f"F{t-1})", ws.cell(t, 6).value)
wb.save(dst); print("TOTAL C", ws.cell(t, 6).value, [str(x.sqref) for x in ws.conditional_formatting])

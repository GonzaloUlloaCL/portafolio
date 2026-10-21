import sys, openpyxl
ruta = sys.argv[1]
maxfilas = int(sys.argv[2]) if len(sys.argv) > 2 else 400
wb = openpyxl.load_workbook(ruta, data_only=False)
for ws in wb.worksheets:
    print(f"=== Hoja: {ws.title}  dims={ws.dimensions}  max_row={ws.max_row} max_col={ws.max_column}")
    if ws.merged_cells.ranges:
        print("   combinadas:", ", ".join(str(r) for r in list(ws.merged_cells.ranges)[:30]))
    n = 0
    for fila in ws.iter_rows():
        vals = [(c.coordinate, c.value) for c in fila if c.value not in (None, "")]
        if vals:
            print("  ", " | ".join(f"{k}={v!r}" for k, v in vals))
            n += 1
            if n >= maxfilas:
                print("   ... (corte)")
                break

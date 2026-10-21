import re, openpyxl, sys
wb = openpyxl.load_workbook(sys.argv[1]); ws = wb["Presupuesto"]; cb = wb["Cubicación base"]
def cbv(r):
    v = cb.cell(r, 2).value
    if isinstance(v, str) and v.startswith("=SUM"):
        a, b = map(int, re.findall(r"B(\d+)", v)); return sum(cbv(i) for i in range(a, b + 1))
    return float(v)
def ev(v, depth=0):
    if v is None or v == "": return None
    if isinstance(v, (int, float)): return float(v)
    if isinstance(v, str) and v.startswith("="):
        e = v[1:]
        e = re.sub(r"'Cubicación base'!\$B\$(\d+)", lambda m: repr(cbv(int(m[1]))), e)
        e = re.sub(r"\bD(\d+)\b", lambda m: repr(ev(ws.cell(int(m[1]), 4).value)), e)
        return eval(e)
    try: return float(v)
    except: return v
if __name__ == "__main__":
    blk = None
    for r in range(1, 400):
        a, b = ws.cell(r, 1).value, ws.cell(r, 2).value
        if a is None and isinstance(b, str) and re.match(r"^[A-C]\.\d+\.\d+ ", b): blk = b
        if isinstance(a, str) and re.match(r"^[A-C]\.\d+\.\d+\.\d+$", a):
            v = ev(ws.cell(r, 4).value)
            print(f"{r}|{a}|{b}|{ws.cell(r,3).value}|{round(v,2) if isinstance(v,float) else v}|{str(ws.cell(r,4).value)[:50]}|{(ws.cell(r,7).value or '')[:140]}")

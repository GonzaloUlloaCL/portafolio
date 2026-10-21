"""Exporta las partidas del presupuesto vigente (hoja «Presupuesto», sectores A-F) a JSON, con cantidades evaluadas."""
import re, sys, json, openpyxl
wb = openpyxl.load_workbook(sys.argv[1]); ws = wb["Presupuesto"]; cb = wb["Cubicación base"]
def cbv(r):
    v = cb.cell(r, 2).value
    if isinstance(v, str) and v.startswith("=SUM"):
        a, b = map(int, re.findall(r"B(\d+)", v)); return sum(cbv(i) for i in range(a, b + 1))
    if isinstance(v, str) and v.startswith("="): return ev(v, hoja=cb)
    return float(v)
def ev(v, hoja=None):
    if v is None or v == "": return None
    if isinstance(v, (int, float)): return float(v)
    if isinstance(v, str) and v.startswith("="):
        e = v[1:]
        e = re.sub(r"'Cubicación base'!\$?B\$?(\d+)", lambda m: repr(cbv(int(m[1]))), e)
        h = hoja or ws; col = 2 if h is cb else 4
        e = re.sub(r"\b(?:D|B)\$?(\d+)\b", lambda m: repr(ev(h.cell(int(m[1]), col).value, h) or 0.0), e)
        e = e.replace("ROUND(", "round(").replace("SUM(", "sum_(")
        return eval(e, {"round": round, "sum_": lambda *a: sum(a), "ROUNDUP": lambda x, n=0: -(-x // 1)})
    try: return float(v)
    except: return None
out = []; sector = None; sub = None; fallas = 0
for r in range(1, ws.max_row + 1):
    a, b, c, d, g = (ws.cell(r, i).value for i in (1, 2, 3, 4, 7))
    if isinstance(a, str) and re.match(r"^[A-F]\. ", a): sector = {"cod": a[0], "titulo": a[3:].strip(), "subs": []}; out.append(sector); continue
    if a is None and isinstance(b, str) and re.match(r"^[A-F]\.\d+ ", b): sub = {"cod": b.split()[0], "titulo": b.split(" ", 1)[1], "items": []}; sector["subs"].append(sub); continue
    if isinstance(a, str) and re.match(r"^[A-F](\.\d+)+$", a):
        try: q = ev(d)
        except Exception as ex: q = None; fallas += 1; print("falla", a, d, ex, file=sys.stderr)
        sub["items"].append([a, b, c or "", None if q is None else round(q, 2), g or ""])
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False)
n = sum(len(s["items"]) for x in out for s in x["subs"])
print("partidas", n, "fallas", fallas)
for x in out: print(x["cod"], x["titulo"], [ (s["cod"], s["titulo"], len(s["items"])) for s in x["subs"]])

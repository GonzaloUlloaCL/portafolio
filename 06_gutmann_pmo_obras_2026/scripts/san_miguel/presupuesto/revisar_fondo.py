import re, openpyxl, sys
wb = openpyxl.load_workbook(sys.argv[1]); ws = wb["Presupuesto"]; cb = wb["Cubicación base"]
def cbv(r):
    v = cb.cell(r, 2).value
    if isinstance(v, str) and v.startswith("=SUM"):
        a, b = map(int, re.findall(r"B(\d+)", v)); return sum(cbv(i) for i in range(a, b + 1))
    return float(v)
def ev(v, fila=None):
    if v is None or v == "": return None
    if isinstance(v, (int, float)): return float(v)
    if isinstance(v, str) and v.startswith("="):
        e = v[1:]
        e = re.sub(r"'Cubicación base'!\$?B\$?(\d+)", lambda m: repr(cbv(int(m[1]))), e)
        e = re.sub(r"\$?D\$?(\d+)\b", lambda m: repr(ev(ws.cell(int(m[1]), 4).value)), e)
        try: return eval(e)
        except Exception as x: return f"ERR {x}"
    try: return float(v)
    except: return v
items=[]; sec=None; zona=None; prob=[]
for r in range(1, ws.max_row+1):
    a,b,c,d,e,f,g=[ws.cell(r,k).value for k in range(1,8)]
    if a and re.match(r"^[A-F]\. ",str(a)): zona=str(a)[0]; continue
    if a is None and isinstance(b,str) and re.match(r"^[A-F]\.\d+ ",b): sec=b.split()[0]; continue
    if a and re.match(r"^[A-F]\.\d+\.\d+$",str(a)):
        items.append(dict(r=r,cod=a,des=b,uni=c,d=d,v=ev(d),f=f,g=g,sec=sec))
print("items",len(items))
# 1 código vs sector y correlativo
from collections import defaultdict
porsec=defaultdict(list)
for it in items:
    if not it["cod"].startswith(it["sec"]+"."): prob.append(("codigo fuera de sector",it["cod"],it["sec"]))
    porsec[it["sec"]].append(int(it["cod"].split(".")[-1]))
for s,l in porsec.items():
    if l!=list(range(1,len(l)+1)): prob.append(("correlativo",s,l))
cods=[it["cod"] for it in items]
for c in set(cods):
    if cods.count(c)>1: prob.append(("codigo repetido",c))
for it in items:
    r=it["r"]
    if it["f"]!=f"=D{r}*E{r}": prob.append(("F",it["cod"],it["f"]))
    if not it["uni"]: prob.append(("sin unidad",it["cod"]))
    if it["v"] in (None,0) or isinstance(it["v"],str): prob.append(("cantidad",it["cod"],it["d"],it["v"]))
    if not it["g"]: prob.append(("sin observacion",it["cod"],it["des"]))
    if ws.cell(r,5).value not in (None,""): prob.append(("precio cargado",it["cod"],ws.cell(r,5).value))
    # número final de la observación vs cantidad
    g=it["g"]
    if isinstance(g,str) and not g.startswith("=") and isinstance(it["v"],float):
        nums=re.findall(r"=\s*([\d\.]*\d,\d+)\s*(m²|m³|ml|m\b)",g)
        tot=re.findall(r"Total\s*([\d\.]*\d,\d+)",g)
        cand=(tot or [n for n,_ in nums])
        if cand:
            x=float(cand[-1].replace(".","").replace(",","."))
            if abs(x-it["v"])>0.015: prob.append(("obs≠cantidad",it["cod"],round(it["v"],2),x))
        un=re.findall(r"(m²|m³|\bml\b)",g)
        if un and it["uni"] in("m²","m³","ml") and un[-1]!=it["uni"] and "Igual" not in g[:6]:
            prob.append(("unidad obs",it["cod"],it["uni"],un[-1]))
    if isinstance(g,str) and g.startswith("="):
        prob.append(("obs formula",it["cod"],it["uni"],it["d"],g, round(ev(g),2) if not isinstance(ev(g),str) else ev(g)))
    if isinstance(g,str):
        for ref in re.findall(r"\b([A-F]\.\d+\.\d+)\b",g):
            if ref not in cods: prob.append(("ref a codigo inexistente",it["cod"],ref))
            else:
                o=next(x for x in items if x["cod"]==ref)
for p in prob: print(p)
# totales
tots=[(r,ws.cell(r,2).value,ws.cell(r,6).value) for r in range(1,ws.max_row+1) if str(ws.cell(r,2).value).startswith("TOTAL")]
print(tots)
for r in range(1,ws.max_row+1):
    if ws.cell(r,2).value in ("COSTO DIRECTO","GASTOS GENERALES","UTILIDADES","SUBTOTAL NETO","IVA","TOTAL"): print(r,ws.cell(r,2).value,ws.cell(r,5).value,ws.cell(r,6).value)
print([str(x.sqref) for x in ws.conditional_formatting], ws.max_row, ws.freeze_panes, ws.print_area if hasattr(ws,'print_area') else None)
import pickle; pickle.dump(items,open("items.pkl","wb"))

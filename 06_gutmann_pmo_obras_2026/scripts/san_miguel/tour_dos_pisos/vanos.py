"""Vanos entre muros colineales: puerta (hay un arco de abatimiento cerca), ventana (líneas finas paralelas cruzan el
vano) o paso libre."""
import pymupdf,sys,json
K=56.69
def arcos(p):
    M=p.rotation_matrix; A=[]
    for d in p.get_drawings():
        for it in d['items']:
            if it[0]=='c':
                pts=[q*M for q in it[1:5]]; A.append([(q.x/K,q.y/K) for q in pts])
    return A
def finas(p):
    M=p.rotation_matrix; L=[]
    for d in p.get_drawings():
        w=round(d.get('width') or 0,2); col=tuple(round(x,2) for x in (d.get('color') or ()))
        if col!=(0,0,0) or w>=1.5: continue
        for it in d['items']:
            if it[0]=='l':
                a=it[1]*M;b=it[2]*M; L.append((a.x/K,a.y/K,b.x/K,b.y/K))
    return L
def vanos(R,p,dx=0,dy=0):
    A=arcos(p); L=finas(p); out=[]
    R=[tuple(r) for r in R]
    for i,a in enumerate(R):
        for b in R:
            if a==b: continue
            for eje in (0,1):
                if eje==0:  # horizontal: misma banda y
                    if a[1]>=b[0] or abs(a[2]-b[2])>0.06 or abs(a[3]-b[3])>0.06: continue
                    g0,g1=a[1],b[0]; c0,c1=max(a[2],b[2]),min(a[3],b[3])
                    if not (0.5<=g1-g0<=3.2): continue
                    if any(r!=a and r!=b and r[0]<g1-0.02 and r[1]>g0+0.02 and r[2]<c1 and r[3]>c0 for r in R): continue
                    rect=(g0,g1,c0,c1); larg=g1-g0; cx,cy=(g0+g1)/2,(c0+c1)/2
                    cruza=sum(1 for x0,y0,x1,y1 in L if abs(y0-y1)<0.01 and c0-0.02<=y0<=c1+0.02 and min(x0,x1)<=g0+0.2*larg and max(x0,x1)>=g1-0.2*larg)
                else:
                    if a[3]>=b[2] or abs(a[0]-b[0])>0.06 or abs(a[1]-b[1])>0.06: continue
                    g0,g1=a[3],b[2]; c0,c1=max(a[0],b[0]),min(a[1],b[1])
                    if not (0.5<=g1-g0<=3.2): continue
                    if any(r!=a and r!=b and r[2]<g1-0.02 and r[3]>g0+0.02 and r[0]<c1 and r[1]>c0 for r in R): continue
                    rect=(c0,c1,g0,g1); larg=g1-g0; cx,cy=(c0+c1)/2,(g0+g1)/2
                    cruza=sum(1 for x0,y0,x1,y1 in L if abs(x0-x1)<0.01 and c0-0.02<=x0<=c1+0.02 and min(y0,y1)<=g0+0.2*larg and max(y0,y1)>=g1-0.2*larg)
                X0,X1,Y0,Y1=rect
                dentro=lambda q:X0-0.06<=q[0]<=X1+0.06 and Y0-0.06<=q[1]<=Y1+0.06
                arco=any((dentro(arc[0]) or dentro(arc[3])) and max(abs(arc[0][0]-arc[3][0]),abs(arc[0][1]-arc[3][1]))>0.25 for arc in A)
                tipo='puerta' if arco else 'ventana' if cruza>=1 else 'paso'
                out.append([round(rect[0]+dx,2),round(rect[1]+dx,2),round(rect[2]+dy,2),round(rect[3]+dy,2),tipo,round(larg,2)])
    # quitar duplicados
    u=[];[u.append(o) for o in out if o not in u]
    return u
if __name__=='__main__':
    p=pymupdf.open(sys.argv[1])[0]; d=json.load(open(sys.argv[2]))
    d['vanos']=vanos(d['exist']+d['nuevo'],p)
    json.dump(d,open(sys.argv[2],'w'))
    from collections import Counter; print(Counter(v[4] for v in d['vanos']))

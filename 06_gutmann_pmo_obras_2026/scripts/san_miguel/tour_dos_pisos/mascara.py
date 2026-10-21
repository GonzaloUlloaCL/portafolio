"""Muros de una planta como máscara a 2 cm: el interior de las caras gruesas (1,56) que es delgado, más el achurado rojo
(muro nuevo). Después se parte en rectángulos."""
import pymupdf,sys,json
import numpy as np
from scipy import ndimage as nd
K=56.69; R=50   # 2 cm
def lineas(p):
    M=p.rotation_matrix; neg=[]; roj=[]
    for d in p.get_drawings():
        w=round(d.get('width') or 0,2); col=tuple(round(x,2) for x in (d.get('color') or ()))
        dest = neg if (col==(0,0,0) and w==1.56) else roj if col==(1,0,0.02) else None
        if dest is None: continue
        for it in d['items']:
            if it[0]=='l': dest.append(((it[1]*M).x/K,(it[1]*M).y/K,(it[2]*M).x/K,(it[2]*M).y/K))
            elif it[0]=='re':
                r=pymupdf.Rect(it[1]*M).normalize(); a=[(r.x0,r.y0),(r.x1,r.y0),(r.x1,r.y1),(r.x0,r.y1)]
                for i in range(4): dest.append((a[i][0]/K,a[i][1]/K,a[(i+1)%4][0]/K,a[(i+1)%4][1]/K))
    return neg,roj
def raster(L,shape,org):
    g=np.zeros(shape,bool); X0,Y0=org
    for x0,y0,x1,y1 in L:
        n=int(max(abs(x1-x0),abs(y1-y0))*R*2)+2
        xs=((np.linspace(x0,x1,n)-X0)*R).round().astype(int); ys=((np.linspace(y0,y1,n)-Y0)*R).round().astype(int)
        g[ys.clip(0,shape[0]-1),xs.clip(0,shape[1]-1)]=True
    return g
def mascara(p):
    neg,roj=lineas(p)
    allL=neg+roj
    X0=min(min(l[0],l[2]) for l in allL)-1; Y0=min(min(l[1],l[3]) for l in allL)-1
    W=int((max(max(l[0],l[2]) for l in allL)-X0+1)*R); H=int((max(max(l[1],l[3]) for l in allL)-Y0+1)*R)
    gn=raster(neg,(H,W),(X0,Y0))
    # interiores delgados encerrados por caras negras
    lab,n=nd.label(~gn)
    fino=np.zeros_like(gn)
    dist=nd.distance_transform_edt(~gn)
    mx=nd.maximum(dist,lab,index=np.arange(1,n+1))
    for i,m in enumerate(mx,1):
        if m<=0.36*R:
            fino|=lab==i
    ex=fino|gn
    ex=nd.binary_opening(ex,np.ones((3,3)))  # saca líneas sueltas
    # rojo: bloques del achurado, cerrando la trama
    gr=raster(roj,(H,W),(X0,Y0)); gr=nd.binary_closing(gr,np.ones((5,5)))
    gr=nd.binary_fill_holes(gr)&~nd.binary_opening(np.zeros_like(gr))
    gr=nd.binary_opening(gr,np.ones((3,3)))
    return ex,gr,(X0,Y0)
def a_rects(g,org,minlen=0.12):
    X0,Y0=org; out=[]
    lab,n=nd.label(g)
    for sl in nd.find_objects(lab):
        sub=(lab[sl]>0)
        rem=sub.copy()
        while rem.any():
            # rectángulo máximo greedy: toma el punto más arriba-izquierda y crece
            ys,xs=np.nonzero(rem); i=np.lexsort((xs,ys))[0]; y,x=ys[i],xs[i]
            # opción horizontal y vertical, elegir la de mayor área
            best=None
            for mode in (0,1):
                if mode==0:
                    x1=x
                    while x1+1<sub.shape[1] and sub[y,x1+1]: x1+=1
                    y1=y
                    while y1+1<sub.shape[0] and sub[y1+1,x:x1+1].all(): y1+=1
                else:
                    y1=y
                    while y1+1<sub.shape[0] and sub[y1+1,x]: y1+=1
                    x1=x
                    while x1+1<sub.shape[1] and sub[y:y1+1,x1+1].all(): x1+=1
                a=(x1-x+1)*(y1-y+1)
                if best is None or a>best[0]: best=(a,x,x1,y,y1)
            a,x,x1,y,y1=best
            rem[y:y1+1,x:x1+1]=False
            # bajar pixeles restantes del mismo rect sólo marca; ignorar rect diminuto
            if max(x1-x+1,y1-y+1)/R>=minlen:
                oy,ox=sl[0].start,sl[1].start
                out.append((round(X0+(ox+x)/R,2),round(X0+(ox+x1+1)/R,2),round(Y0+(oy+y)/R,2),round(Y0+(oy+y1+1)/R,2)))
    return out
if __name__=='__main__':
    p=pymupdf.open(sys.argv[1])[0]
    ex,gr,org=mascara(p)
    ex=ex&~gr
    d={'exist':a_rects(ex,org),'nuevo':a_rects(gr,org)}
    json.dump(d,open(sys.argv[2],'w')); print({k:len(v) for k,v in d.items()})

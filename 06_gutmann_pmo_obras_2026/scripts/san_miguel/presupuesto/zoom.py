import sys, math
from PIL import Image, ImageDraw, ImageFont
Image.MAX_IMAGE_PIXELS=None
x0,y0,x1,y1=map(float,sys.argv[1:5]); out=sys.argv[5]; z=float(sys.argv[6]) if len(sys.argv)>6 else 1.6
im=Image.open("plano144.png").convert("RGB"); k0=56.69*2
c=im.crop((int(x0*k0),int(y0*k0),int(x1*k0),int(y1*k0))); c=c.resize((int(c.width*z),int(c.height*z)),Image.LANCZOS); k=k0*z
d=ImageDraw.Draw(c,"RGBA"); f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",16)
m=math.ceil(x0*4)/4
while m<=x1:
    x=(m-x0)*k; e=abs(m-round(m))<1e-9
    d.line([(x,0),(x,c.height)],fill=(0,120,255,110 if e else 35))
    if e: d.text((x+2,2),str(round(m)),fill=(0,90,220),font=f)
    m+=0.25
m=math.ceil(y0*4)/4
while m<=y1:
    y=(m-y0)*k; e=abs(m-round(m))<1e-9
    d.line([(0,y),(c.width,y)],fill=(0,160,60,110 if e else 35))
    if e: d.text((2,y+2),str(round(m)),fill=(0,130,40),font=f)
    m+=0.25
c.save(out); print(c.size)

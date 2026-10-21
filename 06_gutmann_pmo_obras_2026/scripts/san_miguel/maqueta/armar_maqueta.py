"""Incrusta la plantilla San Miguel en la maqueta y escribe la página."""
import sys
plant, sm, sal = sys.argv[1], sys.argv[2], sys.argv[3]
txt = open(plant).read().replace('/*SAN_MIGUEL*/', open(sm).read())
open(sal, 'w').write(txt); print(len(txt))

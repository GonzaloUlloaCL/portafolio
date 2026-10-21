# Scripts de la obra Daniel Barrera

Arman la guía de lectura de los 5 planos. Los escribió Alma el 12-oct-2026. Se corren con `python -I` y un venv
fuera de esta carpeta (`pip install -r requirements.txt`, igual que en San Miguel).

| Script | Qué hace | Argumentos |
|---|---|---|
| `armar_guia_planos.py` | Arma la guía: arquitectura zona por zona, cada especialidad superpuesta y las inconsistencias | `<carpeta_planos> <carpeta_trabajo> <salida.pdf>` |
| `registrar.py` | Calcula el calce de una planta de instalaciones sobre la de arquitectura (escala y desplazamiento, por correlación de muros) | `<arq.pdf> x0 y0 x1 y1 <esp.pdf> x0 y0 x1 y1 s_min s_max` |
| `superponer.py` | Dibuja una superposición: arquitectura en gris y la otra lámina en magenta | `<arq.pdf> x0 y0 x1 y1 <esp.pdf> s tx ty dpi <salida.png>` |
| `recortar.py` | Recorta una zona de una lámina a PNG (coordenadas de la página rotada) | `<plano.pdf> <salida.png> x0 y0 x1 y1 dpi` |
| `texto.py` | Lista los textos de una lámina con sus coordenadas | `<plano.pdf> [x0 y0 x1 y1]` |

Los calces que usa la guía están en `CALCE`, dentro de `armar_guia_planos.py`. El del detalle 1:25 del baño de
visita se hizo a mano con los ejes F y 7 (escala 2,0), porque la correlación no sirve entre escalas distintas.

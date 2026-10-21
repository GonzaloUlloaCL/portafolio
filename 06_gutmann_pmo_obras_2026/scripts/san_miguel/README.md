# Scripts de la obra San Miguel

Arman y verifican los entregables de la carpeta de arriba. Los escribió Alma el 08-oct-2026. Desde esta carpeta
reproducen el Excel y la guía vigentes tal cual (comprobado el 08-oct: 0 diferencias).

## Preparar el entorno (una vez por sesión)

    python3 -m venv /ruta/al/venv
    /ruta/al/venv/bin/pip install -r requirements.txt

El venv va fuera de esta carpeta, para no subirlo a Drive. La guía usa las fuentes DejaVu del sistema
(`/usr/share/fonts/truetype/dejavu/`).

## Scripts

Se corren con `python -I` (modo aislado).

| Script | Qué hace | Argumentos |
|---|---|---|
| `armar_sala_por_sala.py` | Arma el presupuesto recinto por recinto sobre el formato de Luciano. Los precios quedan vacíos | `<formato_luciano.xlsx> <salida.xlsx>` |
| `verificar.py` | Revisa el Excel terminado: sumas, fórmulas, rótulos contra el plano y trazabilidad con el borrador del 7-oct. Solo lee; sale con código 1 si algo falla | `<presupuesto.xlsx> <plano.pdf> <borrador_7oct.xlsx>` |
| `armar_guia_plano.py` | Arma la guía de lectura del plano | `<plano.pdf> <carpeta_para_recortes> <salida.pdf>` |
| `recorte.py` | Recorta una zona del plano a PNG, con coordenadas de la página rotada | `<plano.pdf> x0 y0 x1 y1 dpi <salida.png>` |
| `revisar.py` | Muestra las partidas de las secciones pedidas, con sus cantidades calculadas | `<presupuesto.xlsx> verificar.py <letras, ej. C,D>` |
| `volcar_xlsx.py` | Vuelca un Excel a texto, hoja por hoja | `<archivo.xlsx> [máx. filas]` |
| `armar_croquis_piso2.py` | Arma el plano del piso 2 de la casona a escala con las medidas interiores de Gonza (08-oct); ventanas, puertas y escalera salen del video de terreno. Trae la lista de lo que falta medir y fotos de cada pieza. Usa el ffmpeg de imageio-ffmpeg | `<video_piso2.mp4> <carpeta_trabajo> <salida.pdf>` |
| `vista_metros.py` | Recorta el plano en metros, con grilla de 1 m y 0,5 m, para leer coordenadas | `<plano.pdf> x0 y0 x1 y1 px_por_m <salida.png>` |
| `tramos_muro.py` | Lista los tramos de muro existente (E) y nuevo (N) a lo largo de líneas horizontales o verticales, en metros. Usa la grilla de 5 cm que dejan `escena_3d/extraer.py` y `rellenar.py`. Con él se midieron los muros de la casona de «Cubicación base» | `<carpeta_grilla> h\|v <coord> <desde> <hasta> [...]` |

## Antes de regenerar el Excel

El generador lo escribe desde cero. **Nunca uses como salida el Excel vigente** si alguien ya lo editó (por ejemplo,
Luciano con sus precios): genera a otro nombre y traspasa esos cambios.

## Escena 3D (`escena_3d/`)

Arma la página 3D de la obra (escrita por Alma el 08-oct-2026): cinco etapas, rayos X de instalaciones, sol por
hora, corte, recorrido y la ficha de partidas de cada recinto. Los muros y vanos salen de los vectores del plano
(escala 1:50); los rectángulos de los recintos se leyeron a mano del plano y están en `recintos.py`. Publicada en
https://claude.ai/artifact/EKEddZENDxzz7wnQnMvas4. Comprobado el 08-oct: desde aquí se reproduce byte a byte.

Se corre desde esta carpeta (`scripts/`), con una carpeta de trabajo vacía `<tmp>`:

    python -I escena_3d/extraer.py "../San Miguel- v5.pdf" <tmp> 0.96      # muros nuevos y existentes a grilla de 5 cm
    python -I escena_3d/rellenar.py <tmp>                                  # rellena los muros huecos de la casona
    python -I escena_3d/bloques.py <tmp>                                   # rectángulos en metros + puertas y ventanas
    python -I escena_3d/recintos.py <tmp>                                  # rectángulo de cada recinto
    python -I escena_3d/partidas.py ../Presupuesto_San_Miguel_sala_por_sala.xlsx verificar.py <tmp>/partidas.json
    python -I escena_3d/armar.py <tmp> escena_3d/plantilla.html <salida.html>

`plantilla.html` tiene la escena (Three.js r128). Los supuestos van rotulados en la página: altura de muros nuevos
2,60 m, instalaciones ilustrativas, piso 2 de la casona como volumen y norte sin confirmar.

## Maqueta de obra (`maqueta/`)

Herramienta para construir la obra en 3D (muros, vanos, recintos, objetos) con el presupuesto recinto por recinto
armándose solo y exportación a Excel. Publicada en https://claude.ai/artifact/2ZeST3jSUnwTMv5f8j3N9c (guarda en la
página con `db`; quien no puede escribir guarda en su navegador y pasa el proyecto en .json). Usa la carpeta de
trabajo `<tmp>` de la escena 3D (pasos extraer → recintos):

    python -I maqueta/plantilla_san_miguel.py <tmp> <tmp>/sm.json     # San Miguel como proyecto editable
    python -I maqueta/armar_maqueta.py maqueta/plantilla_maqueta.html <tmp>/sm.json <salida.html>

Comprobado el 08-oct: reproduce byte a byte la versión publicada.

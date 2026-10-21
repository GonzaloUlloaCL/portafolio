# Scripts de la ampliación San Bernardo

Arman el presupuesto y la guía de la obra. Los escribió Alma el 14-oct-2026, sobre el método de San Miguel.

    python3 -m venv /ruta/al/venv && /ruta/al/venv/bin/pip install openpyxl==3.1.5 pymupdf==1.28.2 reportlab==5.0.1

| Script | Qué hace | Argumentos |
|---|---|---|
| `partidas.py` | Datos: cada partida con su cantidad (fórmula), precio unitario y observación. Lo usan los otros dos | — |
| `armar_presupuesto.py` | Excel con el formato de San Miguel v2.0: Léeme, Para Luciano, Presupuesto, Cubicación base | `<san_miguel_v2.0.xlsx> <salida.xlsx>` |
| `armar_guia_obra.py` | Guía en PDF: cómo se construye, símbolos y el plano en 5 zonas | `<plano.pdf> <carpeta_recortes> <salida.pdf>` |

Se corren con `python -I`. No rasterizar el plano entero a 300 dpi: la página mide 60 × 60 cm y la imagen
pasa de 1 GB (el 14-oct eso tumbó la sesión). Los recortes de la guía van por zonas.
Nunca uses como salida el Excel vigente si Gonza o Luciano ya lo editaron: genera a otro nombre y traspasa.

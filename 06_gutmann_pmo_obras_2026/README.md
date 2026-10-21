# 06 · PMO de obras — Gutmann Construcciones (oct 2026–actualidad)

**Coordinador de Proyectos y Presupuestos de Obra (PMO), independiente.** Llevo la oficina de proyectos de una
constructora: recibo cada obra (planos, correos del mandante, audios del dueño), la estudio y la dejo lista para cotizar,
con el mismo método para toda la cartera. Los precios unitarios y las decisiones de obra son del dueño. Publicado con su
autorización.

## Resultado

**5 presupuestos de obra cerrados en dos semanas (7 al 19 de octubre de 2026), por $853 millones en total.**

| Obra | Total | Entregables |
|---|---|---|
| Sede preuniversitario, San Miguel (casona existente + ampliación, dos pisos) | $443,6 M | [Presupuesto](san_miguel/presupuesto_san_miguel.pdf) · [Guía de lectura del plano](san_miguel/guia_lectura_plano_san_miguel.pdf) · [Recorrido 3D](https://gonzaloulloacl.github.io/portafolio/06_gutmann_pmo_obras_2026/san_miguel/tour_san_miguel.html) |
| Preuniversitario, Talca | $222,9 M | [Presupuesto](talca/presupuesto_talca.pdf) · [Cálculo recinto por recinto](talca/calculo_presupuesto_talca.pdf) · [Guía](talca/guia_lectura_plano_talca.pdf) · [Recorrido 3D](https://gonzaloulloacl.github.io/portafolio/06_gutmann_pmo_obras_2026/talca/tour_talca.html) |
| Ampliación, San Bernardo | $173,1 M | [Presupuesto](san_bernardo/presupuesto_san_bernardo.pdf) · [Guía de obra](san_bernardo/guia_obra_san_bernardo.pdf) · [Recorrido 3D](https://gonzaloulloacl.github.io/portafolio/06_gutmann_pmo_obras_2026/san_bernardo/tour_san_bernardo.html) |
| Dren francés en recinto aeroportuario | $12,7 M | [Presupuesto](dren/presupuesto_dren.pdf) · [Cálculo de cantidades](dren/calculo_cantidades_dren.pdf) · [Recorrido 3D](https://gonzaloulloacl.github.io/portafolio/06_gutmann_pmo_obras_2026/dren/tour_dren.html) |
| Mantención de oficinas y camarines | $1,07 M | [Presupuesto](gruas/presupuesto_gruas.pdf) |
| Vivienda con 4 especialidades (anulada por el cliente) | — | [Guía de lectura de 5 planos](daniel_barrera/guia_lectura_planos.pdf) |

Los recorridos 3D se abren en el navegador, también en celular: una película por etapas de obra y una caminata libre por
los recintos.

## El método

1. **Estudiar** el plano por zonas, más las indicaciones del mandante y del dueño.
2. **Itemizar** por sector y recinto, en orden de obra, solo con partidas de un catálogo común (hasta 325 partidas en una obra).
3. **Cubicar** cada partida con su fórmula y la cota del plano de donde sale, para que se pueda revisar con calculadora.
4. **Dejar a la vista** solo lo que decide el dueño: materiales, precios y alcance.

[Guía didáctica de itemizado y cubicación](metodo/guia_didactica_itemizado_y_cubicacion.pdf) · [Scripts por obra](scripts/)

## Herramientas

Los presupuestos (Excel y PDF), las guías y los recorridos se generan con scripts de Python (openpyxl, reportlab,
PyMuPDF) y Three.js, que diseñé y dirigí con IA. La geometría de los recorridos se lee de los vectores del plano y se
verifica contra sus cotas antes de entregar.

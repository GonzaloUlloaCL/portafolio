---
name: tour-obra-3d
description: Arma o corrige el tour 3D de una obra de Luciano (película por etapas de obra + caminata) leyendo los planos PDF, y audita el modelo antes de entregarlo (huecos sin techo, parches, ventanas o barandas mal leídas, alturas que asoman). Usar cuando Gonza pida un tour de obra, mande capturas o notas de WhatsApp con detalles del tour, o diga «revisa / cubre esos detalles».
---

# Tour 3D de obra (base aceptada: San Miguel, 20-oct-2026)

Código base: `~/intercambio/Luciano_Obra/San_Miguel/scripts/tour_dos_pisos/` (fuera de git). Copiarlo a la carpeta de la obra
nueva y cambiar solo la geometría y los capítulos.

- `mascara.py` (muros: caras 1,56 + achurado rojo), `vanos.py`, `puertas.py` (arcos), `artefactos.py` (símbolos sanitarios).
- `armar_sitio.py`: junta todo por piso, recintos por relleno desde los rótulos, zona caminable, y las correcciones a mano
  `LIMPIAR`, `MUROS_A_MANO`, `NUEVOS_A_MANO`, `ROTULOS_A_MANO`, `ART_A_MANO`, `BARANDAS_A_MANO`, `CAJA_RECINTO`, `CORTES`.
- `parteA.js` escena · `parteE.js` texturas, fusión de mallas, sombras, azulejos y `auditarHuecos()` · `parteB.js` capítulos ·
  `parteC.js` reproductor · `parteD.js` caminata y corte. `armar_tour.py` arma el HTML con las partidas del presupuesto.

## Flujo

1. Leer TODAS las láminas: plantas, techumbre, elevaciones y cortes. Las alturas, techos (dos aguas, frontones, cornisas),
   balcones y muretes salen de las elevaciones y cortes, no se inventan. Lo que el plano no dice (material, alto) se
   pregunta y se anota como pendiente.
2. Armar `sitio.json` y superponer muros, vanos y puertas al plano por zonas antes de diseñar.
3. Película: vistas de 3 s + 2 s de lectura, en orden lógico de obra, un ángulo distinto por capítulo.
4. Publicar en el mismo artifact y anotar en `luciano_registro`.

## Auditoría antes de entregar (los errores que Gonza ya marcó)

- `auditarHuecos()` en la consola debe dar 0: cada punto pisable de un recinto tiene techo o cielo encima.
- Recintos con 0 m² o que se escapan al exterior (el relleno se filtra por un vano): acotar con `CAJA_RECINTO`.
- El detector confunde: tabiques finos (línea 0,84 o 0,72) que no toma; ventanas tapiadas por shafts; barandas leídas como
  ventana (y al revés); «pasos» falsos que parten recintos; hojas de puerta sin muro.
- Alturas: nada asoma (pilares a la altura de su techo; la casona sube a la losa solo bajo el piso 2).
- Nada se ve entre pisos: cantos de losa en los huecos de escalera; el bajo de la escalera es cielo inclinado, no macizo.
- Azulejos solo en el tramo de muro que da a un baño; cubículos solo dentro del baño; el baño universal no lleva cubículos.
- Escalas: la planta se verifica contra cotas (ej. rampa 1,55 × 2,15, frente del pórtico 3,96), pero los ALTOS de puertas,
  ventanas, cornisas y techos se leen de elevaciones y cortes (px por metro desde dos niveles acotados), nunca por defecto:
  un alto por defecto (2,10) dejó la salida al balcón desproporcionada frente a la entrada.
- Barandas y patios: los tramos entre pilarcitos son baranda, no ventana ni paso; un «paso» que cruza un patio es falso.
- Revisar cada nota con una captura a la altura de los ojos desde el mismo punto que la de Gonza.
- Lo que está en el plano y no en el presupuesto se anota en la nota de la obra como adicional o pregunta, sin tocar el
  presupuesto entregado.

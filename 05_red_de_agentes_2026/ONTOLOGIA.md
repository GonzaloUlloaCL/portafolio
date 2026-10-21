---
id: ontologia_red_nueva
titulo: "Ontología de la red nueva — 5 tipos, 5 verbos, linter"
tipo: criterio
vigencia: permanente
ultima_actualizacion: 2026-08-18
bordes:
  - tipo: SIRVE_A
    destino: norte
---

# Ontología de la red nueva

Derivada el 18-ago-2026 (sesión 2 del rediseño) **del material real que migra**, no
elegida de un catálogo. Ese fue el error que produjo 45 tipos de nodo y que `APLICA_A`
llegara al 56% de las aristas.

## El eje: caducidad, no tema

El tema (Patata, CoVisionario, Agrosystems) es **dónde vive** el archivo — la carpeta.
El tipo responde otra pregunta: **qué lo hace caducar**. Son ejes independientes, y un
mismo tópico se reparte en varios tipos, como un cuerpo se reparte en varios planos.

Hoy el `tipo` intenta ser tema (`documento`, `readme`, `nota_cliente`), así que la red
guarda dos veces de qué habla y **ninguna vez lo que se necesita para borrar**. Por eso
el 88% figura "activo": para saber si algo murió hay que abrirlo y juzgarlo.

## Los 5 tipos

| `tipo:` (canónico) | Cuerpo | Qué es | Muere cuando |
|---|---|---|---|
| `proyecto` | físico | La cosa viva con estado | El proyecto muere o se pausa |
| `sistema` | etérico | Cómo funciona algo que **corre** | Cambia el artefacto que describe |
| `criterio` | causal | Regla o método reutilizable | No se invoca en N cierres |
| `evidencia` | akáshico | Lo que pasó, fechado, inmutable | Nunca — no pretende estar vigente |
| `persona` | vínculo | Alguien del mundo de Gonza | Se acaba la relación |

- **Solo `tipo:` va en el frontmatter.** El nombre del cuerpo vive **únicamente en esta
  tabla**; el linter lo imprime en sus mensajes. Escribirlo en cada nodo lo
  desincronizaría, que es el mecanismo exacto por el que hoy hay `estado: activo` en
  archivos que no son proyectos.
- **`estado` y `prioridad` solo existen en `proyecto`.** Un campo que está en todo no
  informa nada.
- **`sistema` declara la ruta del artefacto real.** Si el archivo no existe, el nodo
  muere solo: candado de código, no alerta.
- **`NORTE.md` es `criterio` con `inmutable: true`** — la mónada. Un tipo con una sola
  instancia es justo lo que se criticó de los otros 20.
- **Los pendientes no son un tipo.** El estado de trabajo vive *dentro* del nodo
  `proyecto`. Ahí mueren en el origen los diarios y los archivos con fecha en el nombre.

## Los 5 verbos

**`SIRVE_A` · `DEPENDE_DE` · `USA` · `GENERÓ` · `BLOQUEA`**

- `SIRVE_A` funde `ALIMENTA` (101 declaradas) e `IMPULSA` (20): ambos apuntan casi
  siempre a `NORTE.md` y dicen lo mismo.
- **`APLICA_A` muere.** No colapsó por mal uso: es el `return` por defecto de
  `clasificar_borde()`, cuyas reglas previas están cableadas a proyectos muertos
  (`cobertores`, `maquilapp`). Sus usos declarados apuntan sobre todo a `GRAFO.md`,
  que es una salida generada.
- `BLOQUEA` se conserva pese a tener 3 usos: es el único verbo que produce una acción.

## El linter (bloquea el cierre)

Mismo mecanismo que el candado de español neutro, que ya demostró funcionar:

1. **Sin verbo declarado no hay arista. No hay valor por defecto.**
2. El destino debe existir y **no puede ser un artefacto generado** (`GRAFO.md`, `MAPA.md`).
3. **Sin frontmatter no es nodo** (hoy entran 92 así).
4. `estado`/`prioridad` fuera de `proyecto` = error.
5. `sistema` sin artefacto vivo = marcado muerto.
6. Fecha en el nombre del archivo = rechazado en creación.

## Lista blanca (310 archivos escaneados)

**Migran 156 · se descartan 154.**

| Tipo | Migran |
|---|---|
| sistema | 36 |
| proyecto | 32 |
| criterio | 28 |
| evidencia | 27 |
| persona | 21 |
| por decidir | 12 |

Ningún cajón de sastre: el tipo mayor es el 23%, contra el 44% que hoy se lleva
`documento`.

**Descartes:** 92 sin frontmatter · 38 diarios (se funden en el destilado) · 8 de
`alma_agent` (decisión 1: muere) · 7 de Cobertores (operación cerrada) · 7 de estado de
trabajo (van dentro del proyecto) · 2 artefactos generados.

Detalle archivo por archivo: `lista_blanca_red_nueva.md` (mismo directorio).

### Los 12 por decidir

`organizaciones.md` · `Constitucion_proyecto.md` · `MaquilApp_Etiquetas_POV_Usuario.md` ·
`MaquilApp_Humanizacion_Interfaz.md` · `carrousel.md` · `marca/destacadas/LEEME.md` ·
`reset_your_life.md` · `ventre_leger.md` · 2 × `README.md` · `empieza_aqui.md` ·
`roadmap.md` (24 KB)

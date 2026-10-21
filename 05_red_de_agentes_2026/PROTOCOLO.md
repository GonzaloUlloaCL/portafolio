---
id: protocolo
titulo: "Protocolo de despertar y cierre"
tipo: criterio
vigencia: permanente
ultima_actualizacion: 2026-09-18
bordes:
  - tipo: SIRVE_A
    destino: norte
---

# Protocolo

Reemplaza al handshake `salir` de la red vieja. Ese protocolo **se cumplió**: 97
commits `auto-salir`, 61 de los últimos 105 commits fueron cierres. No falló por
indisciplina. Falló porque **sus 6 pasos eran todos aditivos** — actualizar nodos,
regenerar grafo, escribir el diario, commitear, sembrar curiosidad. Ninguno restaba.

Resultado medido: el `CLAUDE.md` de Patata se podó de emergencia el 04-ago (79 KB) y
**volvió a 82 KB en 7 días**. La regla de higiene existía desde el 21-jul. Escribirla
no alcanzó.

## Despertar

Se leen **cuatro archivos y ninguno más**:

1. `NORTE.md` — para qué existe todo esto.
2. `ONTOLOGIA.md` — cómo está hecha la red.
3. `BITACORA.md` — dónde quedó la sesión anterior y cuál es la próxima tarea.
4. `Alma.md` — principios del asistente (≈4 KB orientativo). **Nunca se poda ni se recorta**: se suma sin cortar (Gonza, 09-oct).

Todo lo demás se busca cuando hace falta, por `id` o por grep. **Nunca se lee la red
entera para arrancar** — así se llegó a sesiones de 150k tokens antes del primer trabajo
útil.

> Esto nace de un fallo real del 18-ago-2026: la sesión 2 arrancó ciega porque la
> sesión 1 no dejó rastro en disco, y reconstruyó el contexto equivocado. El paso 4 del
> cierre existe para que no vuelva a pasar.

## Guardar trabajo vs. cerrar: dos operaciones distintas

**Mal en los dos sentidos** (17-sep: dos sesiones en paralelo reescribieron
`BITACORA.md` casi al mismo segundo, se fusionó a mano; 18-sep: el error opuesto,
tratar un commit normal como si necesitara "permiso de cierre"). Commitear lo propio
nunca depende de que alguien pida cerrar.

### A. Higiene git — trabajo normal, no un cierre

Cualquier sesión commitea/trackea lo que ella misma produjo, sin pedir turno. Gonza
puede además pedir un barrido del repo entero (**"cerrá todo"**, sin importar qué
sesión tocó qué) — corre igual en paralelo con sesiones activas, por diseño.

1. `git status --short` (repo + submódulos, ver paso 5 de B) y commitear/trackear lo
   que aparezca. Puntero de submódulo en su propio commit mecánico ("actualiza puntero
   de X a `sha`"), separado del commit narrativo del cambio real.
2. **Nunca toca `BITACORA.md` ni corre el linter.** Eso es B, y es justo lo que no hay
   que disparar como efecto secundario de la higiene.

### B. "El cierre de Alma" — el ritual narrativo de esta sesión

**Le pertenece a la conversación que lo pide.** Nunca se dispara solo porque alguien
pidió A en otra sesión, ni porque A ya corrió — si Gonza no lo pidió acá, acá no corre.

**Orden invertido a propósito: se resta antes de sumar.** La poda diferida es poda que
no ocurre — la red vieja la difería a "la sesión siguiente", y en 97 cierres esa sesión
nunca llegó.

**Si otra sesión ya reescribió `BITACORA.md` mientras ésta trabajaba** (se detecta
mirando `git log -1 BITACORA.md` contra cuándo arrancó esta sesión): el paso 4 funde la
narrativa propia con la que ya está, no la reemplaza entera. "Se reescribe cada cierre"
pasa a significar *se funde* cuando hay más de una sesión viva, no *se pisa*.

### 0. Precondición

Corre el linter. **Si falla, no hay cierre.** No es una alerta: es un bloqueo, igual que
el candado de español neutro, que es la única regla de este ecosistema que nunca se
incumplió — porque es la única que puede decir que no.

### 1. Medir

Presupuesto por contenedor. Si algo se pasó, se destila **ahora**, no después:

| Contenedor | Tope |
|---|---|
| `BITACORA.md` | 4 KB |
| `ONTOLOGIA.md` | 8 KB |
| `PROTOCOLO.md` | 6 KB |
| Cualquier nodo | 15 KB |
| `NORTE.md` | inmutable |

### 2. Restar

Se poda lo que el linter marcó muerto:

- `sistema` cuyo artefacto ya no existe → muere solo, sin que nadie opine.
- `criterio` que no se invocó en 5 cierres → se gradúa o se mata.
- `proyecto` cerrado → pasa a `evidencia`, que es donde vive lo que ya pasó.

**Podar es el precio de cerrar.** Una sesión que no restó nada no cierra: destila algo,
aunque sea un párrafo.

### 3. Graduar

Si algo aprendido en un proyecto sirve a otro, sube a `criterio` y deja de pertenecer al
proyecto que lo generó. Así "el LLM propone, el código aplica" dejó de estar enterrado en
la carpeta del local de Christian.

### 4. Anotar

Se reescribe `BITACORA.md` (o se funde con lo que ya haya, ver arriba). **Un solo
archivo, sin fecha en el nombre.** Lleva tres cosas: qué se hizo, dónde quedó, cuál es
la próxima tarea.

El historial no se guarda en archivos: **lo guarda git**. `git log -p BITACORA.md` lo
devuelve entero. Los 38 diarios de la red vieja (745 KB) existían porque nadie confiaba
en que el historial ya estaba versionado.

### 5. Commitear y subir

Un commit por cierre, que describe **qué cambió de estado**, no qué se conversó. Con linter
verde y `git status` vacío, se hace `git push` a `origin` **sin preguntar** (Gonza, 06-oct).

**Antes de terminar, `git status --short` tiene que salir vacío.** Si no sale vacío, no
hay cierre: o se commitea esa línea, o se justifica por nombre de archivo por qué queda
afuera. Hay que barrer el árbol entero, incluidos
submódulos con `git status` propio (ej. `despliegue_railway` en Donde el Patata) — no
alcanza con commitear solo lo que se conversó (17-sep: `nucleo/00_NUCLEO/` quedó 9 días
sin trackear porque cada cierre commiteaba solo lo suyo).

## Curiosidad

La red vieja sembraba una pregunta por cierre y no cerraba ninguna: llegó a **70
sembradas y ~20 activas contra un tope de 5**, y sus últimos registros son siete actas
que dicen que no se puede sembrar más. El mecanismo dejó de producir preguntas y pasó a
producir actas de que no puede producir preguntas.

Regla nueva: **el cierre responde o mata una curiosidad. No siembra.** Solo cuando
queden menos de 3 activas se vuelve a sembrar. Un mecanismo que solo suma es el mismo
error del protocolo viejo, en miniatura.

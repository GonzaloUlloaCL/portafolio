---
id: wms_inventario
titulo: "Donde el Patata — WMS (inventario integrado al POS)"
tipo: arquitectura
personalidad: red_neuronal
estado: activo
prioridad: alta
actualizado: 2026-07-20
bordes:
  - tipo: ALIMENTA
    destino: "Proyectos automáticos con Claude y VSC/01_PROYECTOS/en_curso/Donde el Patata/CLAUDE.md"
  - tipo: ALIMENTA
    destino: "Proyectos automáticos con Claude y VSC/01_PROYECTOS/en_curso/Donde el Patata/caso_exito_covisionarios.md"
  - tipo: ALIMENTA
    destino: "NORTE.md"
ultima_actualizacion: 2026-07-22
---

# Donde el Patata — WMS (inventario integrado al POS)

> **Estado (2026-07-20):** **EN PRODUCCIÓN**. Capas 1-4 + capa 5 (montado sobre el POS real:
> descuento de stock en la venta + WMS/finanzas en `/wms/`) hechas y verificadas. El `.exe`
> del POS se entrega con el WMS adentro (`dist/POS Patata (update 20260720).zip`).
> **Reparado el 20-jul:** la creación de recetas fallaba porque en producción el WMS arranca
> sin insumos y la pestaña mostraba un desplegable vacío que parecía roto → ahora hay estado
> vacío guiado + botón "🥔 Cargar ejemplo Patata" (siembra insumos+receta de ejemplo,
> idempotente). Verificado en las 3 capas (tests, navegador, binario). Es el ítem 3 del
> roadmap del POS ("Contable/Financiero"), ya operativo.
> **Pendiente:** cargar datos reales de insumos/recetas (Christian) y validar el escaneo del
> timbre SII (PDF417) con cámara real (hardware).

## Insight central: el WMS es un MÓDULO del POS, no una app aparte

El escenario "teórico de Huechuraba" que se le planteó a Atlas **era este negocio real**.
El POS ya resuelve productos, medios de pago, canales, usuarios/roles/permisos (validados
en backend), caja y auditoría. El WMS **no reconstruye** nada de eso: comparte la misma base
SQLite, el mismo login y el mismo motor de precios, y agrega solo lo que faltaba.

```
   POS (nucleo.py, tarificar.py, menu.json, pedidos.db, usuarios/permisos)
        ▲            │
        │ ALIMENTA   │ comparte base + login + máquina de estados
        │            ▼
   WMS (wms/): insumos · proveedores · ubicaciones · recetas(BOM) · movimientos · recepciones · terminados
```

**La costura (una sola):** cada línea de pedido del POS se identifica por `(categoria,
producto, variante)`; una receta se define contra ESA misma identidad — el WMS "lee" la
estructura del POS, no una copia. Y `sincronizar_estado(pedido_id, nuevo_estado)` engancha
en `transicionar()`: en **"A cocina" (en_preparacion)** descuenta stock; al **cancelar** un
confeccionado, lo manda al limbo de terminados. Tiempo real, mismo proceso, sin batch (a
escala de un local no hay saturación que justifique una cola).

## Piezas (todas con auto-test propio)

- **`wms/inventario.py`** — núcleo del WMS. Maestros, motor de explosión de recetas (BOM con
  factor de conversión y merma), movimientos/stock, semáforo dinámico y productos terminados.
  Auto-test 26/26 (`python wms/inventario.py`).
- **`wms/sii.py`** — parser del Timbre Electrónico (TED) del SII de Chile + EAN-13 + RUT.
  Honesto: el TED trae el TOTAL, el neto/IVA se derivan asumiendo IVA 19%. Auto-test 26/26.
- **`wms/servidor.py`** — FastAPI que reúsa el login/permisos del POS (header `X-Usuario-Id`,
  fail-closed). Smoke test 13/13 (`python wms/servidor.py --test`).
- **`wms/wms.html`** — UI móvil: panel con semáforo + gráficos (ECharts vendorizado local,
  offline), recepción SII con cámara/pegado, recetas, terminados. Verificada en navegador.
- **Capa de ayuda (10-jul):** botón `📖 Manual` en la UI → panel con el paso a paso por cada
  pantalla + conceptos (factor de conversión, merma, semáforo, terminados), MÁS un **tour de
  vitrina** hecho a mano (`▶ Hacer el recorrido guiado`) que oscurece la pantalla y resalta cada
  sección en orden con un globo explicativo. Sin dependencias nuevas (offline). Verificado en
  navegador: el spotlight se posiciona exacto sobre cada objetivo, el globo actualiza título/
  contador y queda en pantalla, y cambia de pestaña solo.
- **`wms/static/`** — ECharts y html5-qrcode vendorizados (funcionan dentro del `.exe`, sin CDN).

## Dos reglas de negocio que fijó Gonza (2026-07-10)

1. **Cancelar un confeccionado NO devuelve los insumos.** El churrasco ya está armado: el
   insumo se consumió. Lo confeccionado pasa a **`productos_terminados`** ('disponible') para
   **revender / vender más barato / regalar / destruir**; la pérdida se contabiliza. Solo el
   caso "no alcanzó a cocinarse" tiene la salida `devolver_insumos` (repone el stock).
2. **El semáforo es dinámico y vivo.** El umbral no es un número fijo: lee la historia real de
   ventas del POS → consumo diario, **días de cobertura**, punto de reorden móvil = consumo ×
   (lead time + días de seguridad) y **tendencia** (subiendo/bajando). El admin puede **pisarlo
   a manual** cuando quiera. Sin historia, cae honestamente al umbral manual.

## Permisos nuevos (fail-closed, se suman a los del POS)

`gestionar_insumos`, `gestionar_recetas`, `recibir_mercaderia`, `ajustar_inventario`,
`ver_reportes_wms`. Todo perfil admin los hereda automáticamente; ningún perfil no-admin los
tiene salvo asignación explícita.

## Capa 5 — enganche a producción (pendiente, decide Gonza)

1. Un hook opcional en `nucleo.transicionar()` que llame a `inv.sincronizar_estado(...)` para
   que la venta REAL descuente stock (hoy solo lo hace la simulación).
2. Montar el router del WMS en `cajero.py` (mismo puerto, misma sesión) apuntando al
   `pedidos.db` real.
3. Re-empaquetar el `.exe`.

## Límites honestos

- El timbre SII es **PDF417**, no QR; leerlo con cámara en el navegador es poco confiable. El
  parser acepta el string venga de donde venga (cámara, foto, pegado); el EAN-13 del insumo sí
  se escanea bien. La lectura con hardware real la valida Christian, como toda la parte física.
- El neto/IVA del TED son **derivados** (el timbre solo trae el total). Exactos al cruzarlos
  con la factura.

## Cuando este nodo cambia

- Si se ejecuta la capa 5, actualizar el ítem 3 del roadmap en el `CLAUDE.md` del proyecto y
  registrar el borde real POS↔WMS (el descuento pasa a ser producción, no simulación).
- Si el menú del POS cambia mucho, re-mapear las recetas (la costura es por nombre de producto).
- Si la UI se mueve (sobre todo con la capa 5), revisar que los pasos del **tour de vitrina**
  (`TOUR` en `wms.html`) sigan apuntando a los elementos correctos. El manual de texto no se rompe.

---
id: plantilla-contable-generica
titulo: "Plantilla contable genérica (base reutilizable para clientes CoVisionarios)"
tipo: plantilla
personalidad: emprendedor
estado: activo
prioridad: media
ultima_actualizacion: 2026-07-13
---

# Plantilla contable / libro de caja — diseño genérico reutilizable

> **Para qué es este archivo.** El diseño contable "completo/ambicioso" (multi-medio
> de pago, M:N proveedor↔insumo, base tributaria Real/Teórico, IVA neto, consolidado
> al contador) se conserva **aquí como base** para futuros clientes de CoVisionarios.
> Para **Donde el Patata** se implementó una **versión lean** ajustada a la planilla
> real de Christian (ver `wms/finanzas.py`). No confundir las dos: esta es la teoría
> reusable; `finanzas.py` es la instancia concreta y probada.

## Principio rector (aprendido con Patata)

El control de **inventario físico** (insumos, recetas/BOM, stock, semáforo — el WMS
`inventario.py`) y el **libro de caja / contabilidad** (flujo por medio de pago, gasto
por categoría, base tributaria) son **capas distintas**. Modelar la contabilidad
reusando el modelo de insumos/recetas es sobre-ingeniería ("inflar"). La capa contable
debe ser **ligera** y tocar el resto del sistema en **dos costuras**:

1. **Ingresos ← POS**: las ventas por medio de pago se leen del POS, no se re-digitan.
2. **Compras con factura ← recepciones SII**: un escaneo alimenta stock **y**
   contabilidad (IVA crédito real).

## Modelo de datos genérico

- **`categorias_gasto`** — catálogo maestro de líneas de egreso, **tipadas**
  (`insumo_variable` | `costo_fijo` | `otro_egreso` | `pago_delivery`) y con
  frecuencia (`diaria` | `mensual` | `eventual`). Es lo que hace el sistema
  adaptable a cualquier rubro: se resiembra con las líneas del cliente.
- **`egresos`** — tabla de **hechos** (fecha, categoría, proveedor OPCIONAL, monto,
  con/ sin factura, IVA, `recepcion_id`, nota, origen). La relación **M:N
  proveedor↔insumo emerge de los hechos**: cada gasto liga 1 proveedor (o ninguno =
  comodín) con 1 categoría. **No** hay columna fija por proveedor ni tabla puente
  rígida. Resuelve los proveedores comodín ("Otro", "oferta", sin dato) con
  `proveedor_id NULL`.
- **`ingresos_manuales`** — lo único que el POS no da (p. ej. boleta manual del SII).
- **`ventas_dia`** — snapshot de ventas por medio de pago para días sin POS
  (histórico importado). El mes en curso se lee del POS en vivo.

## Reglas de cálculo genéricas (tributarias)

```
Boleta Efectiva = Boleta Manual − Transferencia        # (regla del cliente Patata)
Ingreso Real     = Efectivo + Transferencia + POS       # toda la caja que entra
Ingreso Teórico  = POS + Transferencia + Boleta Efectiva# lo declarable/documentado
Total Egresos    = insumos variables + costos fijos
Flujo Real       = Ingreso Real    − Total Egresos − Otros Egresos − Pago Delivery
Flujo Teórico    = Ingreso Teórico − Total Egresos − Otros Egresos − Pago Delivery
Caja no documentada = Real − Teórico
IVA débito ≈ Ingreso Teórico × 19/119 (precio con IVA)  # estimación informativa
IVA crédito = Σ IVA de compras con factura (real desde recepciones SII)
IVA neto    = débito − crédito
```
> **Ojo al adaptar:** las fórmulas Real/Teórico y "Boleta Efectiva" son las de la
> planilla de Patata. Otro cliente puede documentar distinto — parametrizar antes de
> reusar. `Pago al delivery` en Patata es un **egreso** (pago al repartidor), con
> monto auto-sugerido desde `delivery_cobro` del POS.

## Captura de datos (3 caminos, del más automático al manual)

1. **Ventas** → automáticas del POS (`ventas_periodo`).
2. **Compras con factura** → recepción SII (`egreso_desde_recepcion`): un escaneo =
   stock + IVA crédito real. Idempotente por recepción.
3. **Gastos varios** → carga manual (UI) o **texto libre por WhatsApp** (parser
   `parsear_gasto`: "Ariztía 39300 vienesas s/factura" → monto/categoría/proveedor/
   factura), sobre el canal del jefe (autenticado por número).

## Entrega al contador

`consolidado_contador(anio, mes)` → `.xlsx` con el **mismo layout de la planilla del
cliente** (una columna = total del mes), para que el contador reciba el formato de
siempre. El detalle diario vive en el sistema; se le entrega el agregado.

## Preguntas a resolver con CADA cliente nuevo (antes de instanciar)

- ¿Qué medios de pago maneja y cuáles se documentan solos vs. solo con boleta?
- ¿Cuál es su lista real de categorías de gasto (insumos / fijos / otros)?
- ¿"Pago al delivery"/comisiones son ingreso o egreso en su lógica?
- ¿Emite boleta/factura manual? ¿Cómo calcula lo declarable (Teórico)?
- ¿Tiene planilla histórica para importar? ¿Con qué layout?

---

*Instancia concreta y probada de esta plantilla: `wms/finanzas.py` (Donde el Patata),*
*con `importar_finanzas.py` (histórico) y `exportar_contador.py` (consolidado).*

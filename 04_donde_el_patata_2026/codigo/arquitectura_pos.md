---
id: arquitectura_pos
titulo: "Donde el Patata — Bosquejo del POS"
tipo: arquitectura
personalidad: red_neuronal
estado: activo
prioridad: media
actualizado: 2026-06-24
bordes:
  - tipo: DEPENDE_DE
    destino: "Proyectos automáticos con Claude y VSC/01_PROYECTOS/en_curso/Donde el Patata/CLAUDE.md"
ultima_actualizacion: 2026-06-25
---

# Donde el Patata — Bosquejo del POS

> **Estado:** bosquejo inicial 2026-06-24, **pendiente de validar con Gonza**. No es diseño cerrado.
> **Decisión previa (CLAUDE.md):** POS **nuevo desde cero** (no integrar el actual); dos caras
> (cliente web + cajero local) con comanda impresa.

## Insight central: el POS es el HUB de pedidos

Hoy el bot ya toma pedidos por WhatsApp. El POS no es "otra cosa aparte": es **donde convergen los
pedidos de los tres canales**, en una sola cola hacia la cocina.

```
   Bot WhatsApp ─┐
   Web cliente  ─┼──►  NÚCLEO POS (cola de pedidos + estados)  ──►  Comanda a cocina
   Cajero local ─┘
```

El bot pasa a ser, en la práctica, **"otro cajero"** que atiende por WhatsApp.

## Reutilización (clave para no trabajar doble)

- **`tarificar.py`** (motor de precios) y **`data/menu.json`** (carta) son **compartidos** entre bot y POS.
  Una sola verdad de precios y de carta para todos los canales. Esto ya está construido y probado.

## Componentes (bosquejo)

1. **Núcleo de pedidos** — modelo de pedido + estados (recibido → en preparación → listo → entregado/retirado).
   Persistencia simple (SQLite). Es el corazón; todos los canales escriben acá.
2. **Precios y carta** — `tarificar.py` + `menu.json`. Ya existen.
3. **Cara CAJERO (local)** ✅ **CONSTRUIDA (2026-06-25)** — `pos/cajero.py` + `pos/cajero.html`:
   pantalla web local para tomar pedidos presenciales + digitación manual, con precio en vivo, cola y
   comanda imprimible. La llave para el bot quedó abierta (`canal` en `POST /api/pedidos`).
4. **Cara CLIENTE (web)** — carta online + carrito; el pedido cae a la misma cola.
5. **Integración con el bot** — el pedido que arma el bot entra a la misma cola (unifica WhatsApp + web + mesón).
6. **Impresión de comanda** — impresora térmica (ESC/POS) o, para arrancar, PDF / pantalla.
7. **Pagos** — registrar la forma de pago; pasarela online (Transbank / MercadoPago) = fase posterior.

## Primer ladrillo: el NÚCLEO (elegido por Gonza, 2026-06-24) — ANDAMIADO ✅

Antes que cualquier UI, se construye el **corazón**: la lógica de pedidos sin interfaz, para que
bot, cajero y web se enchufen después sobre la misma base. Ya está el esqueleto funcional y probado:
→ Código: `pos/nucleo.py` · Diseño y cómo enchufar cada canal: [pos/README.md](pos/README.md)

Hace: recibe un pedido crudo de cualquier canal, lo **tarifica** con `bot/tarificar.py`, lo guarda en
SQLite, lo mueve por la **máquina de estados** (recibido→confirmado→en_preparacion→listo→entregado) y
arma la **comanda**. El estado "listo" es el gancho que disparará el aviso al cliente (loop del bot).

## Decisiones abiertas (a validar con Gonza / Christian)

- ✅ **¿Primer ladrillo?** RESUELTO: el núcleo común (2026-06-24). **Cara cajero CONSTRUIDA y verificada (2026-06-25).** Siguiente UI sugerida: web del cliente.
- ¿El POS **reemplaza ya** al actual o convive un tiempo?
- **Hardware:** ¿hay impresora térmica? ¿en qué corre el cajero (PC del local, tablet)?
- ¿La web-cliente es la misma carta del bot, o algo más visual/foto?
- **Stock/inventario:** ¿entra al POS, o sigue siendo el canal del jefe del bot?

## Relación con el resto

- **Bot:** comparte núcleo, menú y precios; es un canal de entrada más.
- **Fase contable/stock (roadmap):** el POS genera los datos (ventas, caja) que alimentan esa fase.

*Bosquejo: 2026-06-24. Cara cajero construida 2026-06-25.*

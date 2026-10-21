---
id: pos_readme
titulo: "POS Patata — Núcleo común de pedidos"
tipo: readme
personalidad: red_neuronal
estado: activo
prioridad: media
actualizado: 2026-06-25
bordes:
  - tipo: USA
    destino: "Proyectos automáticos con Claude y VSC/01_PROYECTOS/en_curso/Donde el Patata/bot/tarificar.py"
  - tipo: APLICA_A
    destino: "Proyectos automáticos con Claude y VSC/01_PROYECTOS/en_curso/Donde el Patata/arquitectura_pos.md"
ultima_actualizacion: 2026-06-27
---

# POS Patata — Núcleo común de pedidos

Primer ladrillo del POS (elegido 2026-06-24): el **núcleo**, antes que cualquier UI.
Es el corazón donde convergen los 3 canales; el cajero y la web del cliente se enchufan después.

## Qué es (y qué NO es todavía)

`nucleo.py` es la lógica de pedidos **sin interfaz**. Hace:
- **Recibe** un pedido crudo (qué pidió el cliente) desde cualquier canal.
- **Tarifica** con el motor compartido `bot/tarificar.py` → precio y total autoritativos (una sola verdad).
- **Guarda** en SQLite (`pos/pedidos.db`) con id + estado + timestamps.
- **Mueve** el pedido por la máquina de estados hacia la cocina.
- Expone la **cola** de pedidos activos y arma la **comanda** en texto.

**No** incluye todavía: web del cliente, impresión a térmica (hoy se imprime desde el navegador),
pagos online, ni el enganche real con el bot. Eso son capas encima del núcleo.
La **cara cajero ya existe** (`pos/cajero.py` + `pos/cajero.html`, 2026-06-25) — ver sección abajo.

## Modelo de pedido (normalizado, igual para todos los canales)

```
canal        : "whatsapp" | "web" | "cajero"
cliente      : nombre
modalidad    : "retiro" | "delivery"   (+ direccion si es delivery)
items[]      : { categoria, producto, variante, cantidad, notas }  ← lo "crudo"; el núcleo le pone precio
forma_pago   : "efectivo" | "tarjeta" | "transferencia"
→ tras tarificar: cada item lleva subtotal; el pedido lleva total, problemas, estado, id, historial.
```
El canal solo dice **qué** pidió el cliente; el **precio lo pone el núcleo** (mismo principio que el bot).

## Máquina de estados

```
recibido ──► confirmado ──► en_preparacion ──► listo ──► entregado
   │              │                │
   └──────────────┴── cancelado ◄──┘
```
- **recibido:** entró (de cualquier canal), aún sin aceptar.
- **confirmado:** el local lo aceptó.
- **en_preparacion → listo:** en cocina / listo. "listo" es lo que dispara el aviso al cliente
  (retiro: "ya puedes pasar"; delivery: "va en ruta") — gancho para el loop de los dos números del bot.
- **entregado / cancelado:** finales. Las transiciones inválidas se rechazan.

## Cómo se enchufa cada canal (futuro)

| Canal | Cómo entra al núcleo |
|-------|----------------------|
| **Bot WhatsApp** | cuando el bot cierra un pedido, llama a `nucleo.crear(canal="whatsapp", ...)` con los items que ya extrajo (o `POST /api/pedidos` con `canal:"whatsapp"`). **La llave ya está abierta**: el endpoint del cajero acepta `canal`. El "listo" del núcleo dispara el aviso al cliente. |
| **Cajero (local)** ✅ | **LISTO (2026-06-25):** `pos/cajero.py` + `pos/cajero.html`. La pantalla local arma el pedido y llama a `nucleo.crear(canal="cajero", ...)` vía `POST /api/pedidos`. |
| **Web cliente** | la carta online arma el carrito y llama a `nucleo.crear(canal="web", ...)`. |

Los tres comparten **un solo `menu.json` y un solo `tarificar.py`** → no hay precios duplicados.

## Probarlo

```
python pos/nucleo.py
```
Corre el auto-test (crea pedidos, los tarifica, los mueve de estado, valida la cola e imprime
una comanda). No necesita internet ni Gemini.

## Cara cajero (UI) — `pos/cajero.py` + `pos/cajero.html`

Primera UI sobre el núcleo (2026-06-25). Pantalla local para **atención presencial y
digitación manual**: Christian toca productos del menú (con sus variantes y sabores), ve el
**precio en vivo**, manda el pedido a la cola y lo mueve por la cocina hasta entregarlo.

```
python pos/cajero.py            # levanta el servidor -> http://127.0.0.1:8000
python pos/cajero.py --port 9000
python pos/cajero.py --test     # auto-test sin navegador (no necesita Gemini)
```
- Es un servidor **FastAPI** (ya en el venv). Se abre en el navegador del PC del local.
- Variable opcional `POS_DB` para usar una base aparte (dev/pruebas); por defecto usa `pos/pedidos.db`.
- Endpoints: `GET /api/menu`, `POST /api/preview` (precio en vivo, no guarda),
  `POST /api/pedidos` (crea; acepta `canal`), `GET /api/cola`,
  `POST /api/pedidos/{id}/estado`, `GET /api/pedidos/{id}/comanda`.
- **La llave para el bot:** `POST /api/pedidos` acepta el campo `canal`. El bot, cuando esté,
  manda `"whatsapp"` por la misma puerta (o `nucleo.crear(canal="whatsapp", ...)` en proceso).
  Misma cola, misma cocina, mismos precios — no hay que duplicar nada.
- Precios: el cajero **nunca** calcula precios; el total lo pone el tarificador (una sola verdad).
- Impresión: **ESC/POS directo a la X-Printer** (`pos/impresora.py`, botón "Imprimir (térmica)"), con respaldo por navegador.

## Estado (2026-06-25)

Hecho y probado: ✅ cara cajero · ✅ **caja** (apertura/cierre + vuelto + cuadre del día) ·
✅ **clientes** (base por teléfono + búsqueda por nombre, 4.421 importados) · ✅ modalidad **Local** (con mesa) ·
✅ **editar pedido** (reusa la pantalla; solo con la caja abierta) · ✅ **impresión térmica ESC/POS** ·
✅ **`.exe`** empaquetado para Christian (`dist/POS Patata.zip`).

Siguiente / pendiente:
1. **Validar la impresión** con la X-Printer real (Christian, botón Test).
2. **Enganche con el bot** — `nucleo.crear(canal="whatsapp", ...)` / `POST /api/pedidos` (la llave ya está abierta).
3. **Web cliente** (carta + carrito), sobre el mismo núcleo.
4. **Pagos online** (Transbank / MercadoPago).

## Deuda técnica anotada

- `tarificar.py` y `menu.json` viven en `bot/` pero son **compartidos**. Refactor futuro: moverlos a un
  `comun/` para que bot y POS los importen sin truco de `sys.path`.

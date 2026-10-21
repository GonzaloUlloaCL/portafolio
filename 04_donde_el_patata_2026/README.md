# 04 · Donde el Patata — POS, bot de WhatsApp y finanzas (2026)

Donde el Patata es un local de comida rápida en Huechuraba, Santiago. Atendía todo a mano por WhatsApp, teléfono y
mostrador, sin sistema ni datos ordenados. Diseñé y dirigí con IA la construcción de su sistema, que hoy opera con
clientes reales. Publicado con autorización del dueño.

## Qué hay en producción

| Pieza | Qué hace | Código |
|---|---|---|
| **POS del cajero** | Pedidos de mostrador y delivery, pago combinado y vuelto, caja con apertura y cierre, impresión térmica ESC/POS, usuarios con PIN (pbkdf2) y auditoría de quién hizo qué. Base de **más de 4.400 clientes** importados y depurados. En uso desde el 27-jun-2026 | [`codigo/pos/`](codigo/pos/) |
| **Bot de pedidos por WhatsApp** | Corre 24/7 en Railway sobre la API de WhatsApp Cloud. El modelo de lenguaje entiende el pedido; **el precio, las promociones y el total los calcula el código** contra el menú: cero precios inventados | [`codigo/bot/`](codigo/bot/) |
| **Motor de precios** | Única fuente de verdad del precio, compartida por el POS y el bot | [`codigo/bot/tarificar.py`](codigo/bot/tarificar.py) |
| **Banco de pruebas** | Conversaciones de prueba que se corren antes de cada cambio del bot; un candado impide subir si empeora | [`banco_pruebas.py`](codigo/bot/banco_pruebas.py), [`candado_banco.py`](codigo/bot/candado_banco.py) |
| **Curador** | El dueño marca respuestas malas del bot y yo las corrijo en las reglas: el bot aprende de la operación | [`codigo/bot/curador/`](codigo/bot/curador/) · [cómo aprende](codigo/bot/COMO_APRENDE_EL_BOT.pdf) |
| **Finanzas e inventario** | Libro de caja con 43 categorías, ingresos leídos del POS sin doble digitación, egresos con IVA crédito y exportación al contador | [`codigo/wms/`](codigo/wms/) |

## Un resultado concreto

Al importar al libro de caja la planilla histórica del local, todo lo numérico cuadró exacto y además aparecieron
**$51.000 que la planilla perdía**: montos escritos como texto ("deli 25500") que la suma de Excel ignoraba.

## Decisiones de diseño

- **SQLite y no MySQL:** el POS corre en el PC del local; un servicio más es algo más que se puede caer un sábado en la
  noche.
- **Quién es el dueño lo decide el número que escribe, no el texto del mensaje:** nadie puede hacerse pasar por él en el chat.
- **El bot y el POS van separados:** el túnel público solo expone lo que necesita el bot.

Arquitectura: [bot](codigo/arquitectura_bot.md) · [POS](codigo/arquitectura_pos.md).

> Copia curada del repositorio privado: sin bases de datos, conversaciones de clientes ni credenciales. Los teléfonos y la
> cuenta bancaria están reemplazados por marcadores y el PIN sembrado es de ejemplo.

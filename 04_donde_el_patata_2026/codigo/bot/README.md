---
id: patata-bot-readme
titulo: "Bot Patata — Prototipo local (Fase 1)"
tipo: readme
personalidad: red_neuronal
estado: activo
prioridad: media
actualizado: 2026-07-02
conectado_a:
  - Proyectos automáticos con Claude y VSC/01_PROYECTOS/en_curso/Donde el Patata/arquitectura_bot.md
bordes:
  - tipo: APLICA_A
    destino: Proyectos automáticos con Claude y VSC/01_PROYECTOS/en_curso/Donde el Patata/arquitectura_bot.md
ultima_actualizacion: 2026-07-04
---
# Bot Patata — Prototipo local (Fase 1)

Prueba el "cerebro" del bot en tu PC, **sin WhatsApp**, para afinar el tono y la toma de pedidos antes de conectarlo a Meta. Ver diseño completo en [../arquitectura_bot.md](../arquitectura_bot.md).

## Cómo correrlo

1. Tener la **API key de OpenAI** (https://platform.openai.com/api-keys). Cambiado
   de Gemini a OpenAI el 09-jul-2026 (la key que consiguió Christian fue de OpenAI).
   El script la busca en: variable `OPENAI_API_KEY_BOT` (o `OPENAI_API_KEY`), o el
   archivo `openai_key_bot.txt` en esta carpeta.
2. Abrir una terminal en esta carpeta y correr:

   ```
   python prototipo.py
   ```

3. Escribir como si fueras un cliente. Comandos: `salir`, `reset`.

No necesita instalar nada (usa solo la librería estándar de Python).

**Modo jefe** (2026-07-02): `python prototipo.py --jefe` simula que escribe
Christian desde su número (+56 9 XXXX XXXX; en el webhook real el modo se activa
comparando el `from` con `JEFE_NUM` en código, nunca por el texto del chat).

## Qué hace

- Responde dudas (horario, delivery, precios, pago) con el tono de Patata.
- Toma el pedido conversando y, cuando está completo, imprime la **COMANDA PARA CHRISTIAN** con el total recalculado.
- Nunca revela que el delivery es propio (dato interno).
- **En modo jefe** (`--jefe`): recibe instrucciones del día ("hoy solo retiro",
  "se agotó el chacarero") y las persiste en `instrucciones_local.txt` — que el
  bot de clientes obedece; registra feedback/ideas en `mejoras_jefe.md` con tono
  receptivo (nunca se defiende); y arma contenido de marketing con el formato del
  motor Prasad (medio sugerido + 3 ángulos ranqueados + copy listo para pegar con
  precios reales del menú). Probado en vivo contra Gemini el 02-jul. Los flyers /
  imágenes son v2 (requieren `patata.yaml` con el ADN visual + media upload de la
  Cloud API).

## Piezas (qué tocar para mejorarlo)

- **`system_prompt.md`** — la personalidad y las reglas del bot. Edítalo para cambiar el tono o agregar reglas.
- **`patrones_reales.md`** — tono, mensajes tipo y vocabulario destilados de chats reales. Es la fuente de lo que va al system_prompt.
- **`../data/menu.json`** — los precios y datos del local (fuente de verdad). Si cambia la carta, se actualiza acá.
- **`tarificar.py`** — el motor de precios. Calcula cada subtotal, la promo 2x y el total contra `menu.json`. Trae auto-test: `python tarificar.py` (no necesita OpenAI).
- **`system_prompt_jefe.md`** — la personalidad del **modo jefe** (receptivo, registra instrucciones/mejoras, marketing estilo Prasad). Solo se activa por número, en código.
- **`instrucciones_local.txt`** — el "canal del jefe": cada línea es una instrucción temporal vigente (ej. `solo retiro hoy`, `se agotaron las bilz`). Si el archivo existe, el bot de clientes la obedece. **Desde el 02-jul lo escribe el propio modo jefe** (bloque `<INSTRUCCIONES_LOCAL>` interceptado por el código); también se puede editar a mano para pruebas.
- **`mejoras_jefe.md`** — bitácora de feedback e ideas que dicta el jefe (bloques `<MEJORA>`, anexados con fecha, nunca se reemplaza).
- **`prototipo.py`** — el código que conecta todo con OpenAI (cambiado de Gemini el 09-jul-2026; la clase `CerebroGemini` sigue en el archivo por si hiciera falta volver).

## Cómo se asegura que los precios sean correctos (2026-06-24)

La IA **no pone precios**: solo identifica qué pidió el cliente (categoría, producto, variante, cantidad). El **código** (`tarificar.py`) calcula el subtotal, aplica la promo 2x y arma el total leyendo `menu.json`. La comanda muestra el precio del menú, marca si la IA se había equivocado en una cifra y avisa si un producto no está en la carta. Así la IA no puede inventar ni errar un precio.

## Limitaciones de esta versión (a endurecer después)

- La conversación vive en memoria mientras el script corre (no se guarda historial entre ejecuciones). En la versión real se usará una base simple (SQLite).
- Aún no está conectado a WhatsApp: eso es la siguiente etapa (WhatsApp Cloud API + hosting).

## Entregables Tangibles

- **Compilaciones .exe:** Se han generado ejecutables (`.exe`) del prototipo del bot para pruebas locales, facilitando la distribución y validación por parte del equipo. Estos ejecutables permiten probar el "cerebro" del bot sin necesidad de un entorno Python configurado.

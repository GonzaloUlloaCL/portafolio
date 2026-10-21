---
id: readme
titulo: "Curador de prompts — Donde el Patata"
tipo: readme
personalidad: red_neuronal
estado: activo
prioridad: media
actualizado: 2026-08-05
---
# Curador de prompts — Donde el Patata

Herramienta LOCAL, no se despliega a Railway. Cura conversaciones REALES de
producción contra el `system_prompt.md` vigente, con un botón "¿sigue fallando?"
que le pega una vez más al modelo real para comprobar si un ajuste al prompt
arregló el caso.

**04-ago-2026:** además de correr como `python curador.py` (uso de Gonza, este
mismo directorio), existe compilada como `.exe` (`build_pyi/Curador Patata.spec`)
para que **Christian también pueda correrla desde su PC personal** — ver
`dist/Curador Patata/LEEME_CHRISTIAN.md` para el paquete que se le entregó.
Mismo código, dos formas de correrlo.

⚠️ **Estado real al 05-ago-2026: el paquete se envió, nada más.** Nadie confirmó que
Christian lo haya instalado, abierto ni usado. Entregado ≠ instalado ≠ en uso — es el
mismo patrón de "cargar ≠ usar" que ya se vio con los paneles y reportes anteriores.
El único uso verificado sigue siendo el de Gonza desde este directorio.

## Primera vez

1. Copiá `config_curador.ejemplo.json` a `config_curador.json` (mismo directorio) y
   completá:
   - `bot_url`: público, ya viene con el valor correcto (`https://bot.dondeelpatata.cl`).
   - `bot_panel_token`: el mismo valor que `bot_panel_token` en el `config.json` del
     POS de Christian (o la variable `BOT_PANEL_TOKEN` en Railway, servicio
     `bot-patata`). **Nunca se sube a git** — ver `.gitignore` raíz del workspace.
2. `python curador.py` (necesita el mismo venv que el resto del proyecto: fastapi,
   uvicorn). Levanta en http://127.0.0.1:8002

## Flujo de curación

1. **Sincronizar conversaciones** — trae lo nuevo desde el panel del bot (mismo
   endpoint `/conversaciones` que usa la pantalla en vivo del POS).
2. Abrís una conversación de la pestaña **Pendientes**. Si algo del BOT está mal,
   click en esa burbuja → escribís qué falló → **Guardar marca**. Si está todo bien,
   **"Está todo bien, no hay nada que curar"**.
3. Las marcadas quedan en **Casos abiertos**. Después de ajustar `system_prompt.md`
   (acá mismo, con **Editar system_prompt.md**, o a mano en el archivo), volvés al
   caso y apretás **¿Sigue fallando?** — llama al modelo real con el prompt VIGENTE
   y el mismo contexto de esa conversación, y muestra la respuesta vieja vs la nueva
   lado a lado. La decisión de si ya está arreglado es tuya (curación manual).
4. **Guardar y pushear** en el editor del prompt: escribe `bot/system_prompt.md`
   (con backup `.bak_<fecha>` al lado, mismo patrón que el resto del proyecto),
   copia el archivo a `despliegue_railway/bot/system_prompt.md` (CRLF→LF) y hace
   `git push` a `christian-ulloa/bot-patata`. **El deploy en Railway sigue siendo
   SIEMPRE manual** desde su dashboard — esta app nunca lo aprueba ni lo dispara.
   Si el remoto tiene commits que este repo local no tiene (alguien editó directo
   en GitHub), el push se aborta solo y avisa en vez de pisar nada.

## Qué toca y qué no toca

- Lee/escribe **solo** `bot/system_prompt.md`. Nunca `webhook.py`, nunca otro
  archivo del bot.
- Su propia base (`curaciones.db`) es una CACHE local de curación, separada de
  `conversaciones_bot.db` (esa vive en el volumen de Railway y es del bot real).
  Borrar `curaciones.db` no afecta al bot ni a Christian — solo pierde el estado
  de curación ya hecho.

## Límite conocido, no corregido a propósito

`/conversaciones` (el endpoint del bot que esta app consume) devuelve como máximo
los 300 turnos MÁS RECIENTES posteriores a `desde_id`, no los 300 siguientes. Si
pasan varios días sin sincronizar y se acumulan más de 300 turnos nuevos, los del
medio se pierden para siempre. Arreglarlo implica tocar `webhook.py`, que está
fuera del alcance acordado para esta app. Mientras se sincronice con regularidad
(el uso normal de curar seguido), no debería pasar.

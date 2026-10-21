#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webhook.py — Bot de "Donde el Patata" conectado a WhatsApp Cloud API (09-jul-2026).
=====================================================================================
Servicio APARTE del POS (puerto propio, distinto de pos/cajero.py) que recibe los
mensajes reales de WhatsApp vía Meta, los procesa con el mismo cerebro ya probado en
`prototipo.py` (Gemini + system_prompt.md + tarificar.py), y:
  - responde al cliente por la WhatsApp Cloud API,
  - cuando el pedido queda completo, se lo manda al CAJERO CORRIENDO por HTTP
    (POST /api/pedidos, canal="whatsapp") -- el cajero es el UNICO que escribe su
    pedidos.db, asi el pedido aparece en la cola de Christian sin lios de base
    compartida (funciona igual con el .exe o con el codigo fuente). Si el cajero no
    responde, lo guarda en una base local de respaldo y avisa,
  - si escribe el JEFE (Christian, JEFE_NUM), activa el modo jefe (instrucciones del
    día / feedback / marketing) ya diseñado en prototipo.py.

Por qué es un servicio APARTE del POS: el túnel público (ngrok/cloudflared) solo debe
exponer ESTE puerto, nunca el POS completo (que tiene la caja, los clientes, el admin).

Config: bot/config_webhook.json (créalo la primera vez con `python webhook.py --init`,
o copia el ejemplo de bot/GUIA_WEBHOOK.md). Contiene SECRETOS (token de Meta) -- no lo
compartas ni lo subas a ningún lado.

Cerebro: usa la key DEDICADA del bot (OPENAI_API_KEY_BOT o bot/openai_key_bot.txt),
NUNCA compartida con otro uso (ver _buscar_key_openai en prototipo.py). Modelo
primario: gpt-4.1-mini desde el 28-jul-2026 (~1s igual que el anterior, pero sigue
instrucciones bastante mejor -- ver el porque en CerebroOpenAI), con caida a
gpt-4o-mini y por ultimo gpt-5-mini -- este ultimo existe en la cuenta pero es un modelo
"razonador" que tardo 5s en la prueba real, demasiado lento para un chat. (09-jul-
2026: cambio de plan de Gemini a OpenAI porque la key que consiguio Christian fue
de OpenAI, no de Gemini; el resto del bot -- system_prompt, tarificador, cola del
POS -- no cambio nada.)

Uso:
  python webhook.py            # levanta el servidor (puerto de config, default 8001)
  python webhook.py --test     # auto-test SIN necesitar credenciales de Meta ni de
                                # OpenAI (usa un cerebro simulado) -- verifica que el
                                # parseo, la seguridad y el enganche al POS funcionan
  python webhook.py --init     # crea bot/config_webhook.json con placeholders
"""
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

if getattr(sys, "frozen", False):
    # Empaquetado como .exe (Bot Patata): config, conversaciones_bot.db y el respaldo
    # local viven JUNTO al ejecutable (escribibles) -- ver pos/rutas.py del POS.
    AQUI = Path(sys.executable).resolve().parent
    PROYECTO = AQUI
    POS = AQUI
else:
    AQUI = Path(__file__).resolve().parent          # bot/
    PROYECTO = AQUI.parent
    POS = PROYECTO / "pos"
sys.path.insert(0, str(AQUI))                    # para importar prototipo.py (mismo folder)
sys.path.insert(0, str(POS))                      # para importar nucleo.py

import prototipo                                 # noqa: E402  (acceso a MENU_CACHE SIEMPRE
# via el modulo -- no "from prototipo import MENU_CACHE": eso copia el valor de arranque,
# y _refrescar_menu_pos necesita ver el valor VIVO, ej. si un test lo reemplaza)
from prototipo import (  # noqa: E402
    CerebroOpenAI, CONFIRMACION_CIERRE_RESPALDO, CORTE_PEDIDOS, DATOS_DIR, JEFE_NUM,
    MENU_JSON, TOOL_EMITIR_PEDIDO, TOOL_MARCAR_INCOMPLETO, TOOL_PREVISUALIZAR_PEDIDO,
    TZ_CHILE,
    candado_instrucciones, candado_modalidad, candado_pedido_grande,
    construir_sistema, estado_horario, interpretar_instrucciones_estructuradas,
    mensaje_cerrado, MENSAJE_CERRADO_POR_JEFE,
    mensaje_corte_pedidos, montos_dichos_por_el_cliente, procesar_bloques_jefe,
    corregir_link_menu, sanear_montos_no_autorizados, sanear_saludo_repetido, sanear_tamano_sandwich,
)
from tarificar import Tarificador, montos_validos_del_menu, label_variante   # noqa: E402
from delivery_zonas import cobro_por_calle         # noqa: E402
# nucleo.py/rutas.py (pos/) NO se importan aca arriba a proposito (16-jul-2026,
# migracion a la nube): en produccion el bot SIEMPRE le habla al POS por HTTP
# (ver _entregar_payload/_crear_pedido_en_pos), nunca escribe directo a su
# pedidos.db -- ese import solo hace falta para el --test local (que SI corre
# un NucleoPedidos real en memoria) y para simular el fallback viejo. Importarlo
# aca arriba obligaba a que el repo del bot cargara con todo pos/ para poder
# arrancar -- inviable en un contenedor en la nube que NO tiene esa carpeta.
# Se importa DIFERIDO donde realmente se usa (igual criterio que win32print en
# pos/impresora.py: el programa arranca aunque la dependencia no este).

CONFIG = AQUI / "config_webhook.json"
# CONV_DB/RESPALDO_DB viven en DATOS_DIR (ver prototipo.py): en un PC local es AQUI
# de siempre; en Railway, si BOT_DATA_DIR apunta a un volumen persistente, sobreviven
# a un redeploy -- si no, un simple "git push" borraria toda conversacion en curso y
# la cola de reintento de pedidos.
CONV_DB = DATOS_DIR / "conversaciones_bot.db"
# Cola de pedidos que NO llegaron al POS al momento (POS cerrado/caido). Reemplaza el
# fallback viejo (escribir directo a DB_DEFAULT): ese fallback, corriendo como .exe
# aparte del POS, escribia en una base FANTASMA junto al bot -- invisible para
# Christian para siempre (bug real detectado 14-jul-2026, primer uso real: un pedido
# nunca aparecio en la cola ni se pudo imprimir). Ahora se guarda aca + se avisa por
# WhatsApp al jefe de inmediato, y un hilo en background reintenta solo cada 30s.
RESPALDO_DB = DATOS_DIR / "respaldo_pedidos.db"
REINTENTO_SEGUNDOS = 30
# Sincronizacion del menu con el POS (27-jul-2026, S4 del plan: "dos verdades del
# menu"). 5 min: suficiente para que un cambio de precio de Christian en el Admin
# del POS llegue al bot sin demora molesta, sin exigirle mucho a su tunel gratis.
MENU_SYNC_SEGUNDOS = 300
# Sincronizacion de la cache de clientes con el POS (04-ago-2026, "nombre registrado").
# Mismo intervalo que el menu: no hace falta que sea mas seguido -- un cliente que recien
# se registro (compro por cajero o WhatsApp) tarda como mucho esto en aparecer con
# "Nombre registrado" en su proxima conversacion nueva.
CLIENTES_SYNC_SEGUNDOS = 300
# Recordatorio de pedidos PROGRAMADOS (04-ago-2026, P29): avisa al jefe esta cantidad
# de minutos ANTES de la hora que pidio el cliente; si en ESCALA_PROGRAMADO_MINUTOS
# nadie lo movio a cocina, avisa TAMBIEN al respaldo (regla general de delegacion a
# humano/jefe, igual que cobro/transferencia). Nunca fuerza el pedido a cocina solo --
# a diferencia de cobro/transferencia, no hay un "por defecto seguro" para arrancar a
# cocinar sin que un humano lo decida.
RECORDATORIO_PROGRAMADO_MINUTOS = 15
ESCALA_PROGRAMADO_MINUTOS = 5
PROGRAMADOS_POLL_SEGUNDOS = 60

# Seguimiento de saludo sin respuesta (13-sep-2026, pedido de Gonza): si el bot habló
# ultimo en una conversacion VIVA (cerrado_en IS NULL -- no llego a pedido, si hubiera
# cerrado limpiar_historial() ya la archivo) y pasaron estos minutos sin que el cliente
# conteste, se manda UN mensaje de seguimiento, nunca mas de uno por conversacion.
# Dentro de la ventana de 24h de WhatsApp (el cliente escribio primero): no hace falta
# plantilla aprobada por Meta. Recontactar DIAS despues, o sin que el cliente haya
# escrito primero, es otro proyecto (plantilla de marketing + opt-in) -- no esto.
#
# BUG real (encontrado en el curador el 28-sep-2026, casos 77/81/83): "cerrado_en IS
# NULL -> no llego a pedido" es FALSO. limpiar_historial() archiva TODO lo viejo en el
# instante del despacho, pero un "Ok"/"gracias" del cliente DESPUES de esa confirmacion
# (dentro de MINUTOS_VENTANA_POSCIERRE) crea un turno nuevo con cerrado_en NULL -- y ESE
# turno es el que este ciclo ve como "conversacion viva abandonada". El cliente ya se
# habia despedido; el bot le insistia igual, hasta 3 veces seguidas en el caso 83. Ver
# el chequeo de _ultimo_pedido_despachado() mas abajo.
MINUTOS_SEGUIMIENTO_SALUDO = 10
SEGUIMIENTO_SALUDO_POLL_SEGUNDOS = 60
# Registro neutro (BLOQUE 2 del prompt): sin emoji propio, sin preguntar como esta.
# 07-oct-2026 (Christian, caso 127 del curador): el "¿Sigues ahi? Cuentame que te gustaria
# pedir y seguimos" presionaba a quien solo habia preguntado el horario; queda
# simplemente "Quedamos atentos."
MENSAJE_SEGUIMIENTO_SALUDO = "Quedamos atentos."

# --- Monitor de la ventana pico (29-sep-2026, pedido de Gonza: visibilidad real sobre
# lo que le llega a Christian, no otra promesa) ---
# Medido sobre 21 dias de conversaciones reales (turnos_cache del curador, 934 turnos):
# el pico duro es 20h-23h (22h la hora mas cargada), con un pico secundario a
# almuerzo (12h-13h) que se deja afuera a proposito -- concentrar el monitor en UNA
# ventana, la de mayor riesgo real, en vez de correr parejo las 24h.
HORA_INICIO_MONITOR_PICO = 20
HORA_FIN_MONITOR_PICO = 23    # inclusive
MONITOR_PICO_POLL_SEGUNDOS = 300    # 5 min
# A partir de cuantos minutos sin cerrar un caso humano (derivar_humano, pedido
# grande, falla del cerebro, etc.) se considera que nadie lo esta mirando.
MINUTOS_CASO_HUMANO_SIN_ATENDER = 15
NTFY_URL = "https://ntfy.sh/"
# Tema de ntfy.sh (largo y secreto: quien lo sepa puede leer/enviar), mismo patron que
# ALERTA_TOPIC de proyectos/busqueda_laboral/monitor/monitor.py -- variable de entorno,
# nunca a git. Sin ella el monitor sigue corriendo pero no manda nada (ver
# _revisar_monitor_pico).
NTFY_TOPIC_PATATA = os.environ.get("NTFY_TOPIC_PATATA", "").strip()

# --- Roles de jefe (13-sep-2026, pedido de Christian/Gonza: rol por numero, con
# jefe1/jefe2 simetricos y todo lo demas sin rol -- reemplaza el diseño del 15-jul-2026
# de JEFE_NUM/JEFE_PERSONAL/JEFE_BACKUP con atribuciones distintas cada uno) ---
# ROLES_JEFE es el UNICO lugar donde se decide quien es jefe: agregar, sacar o
# reasignar un numero es cambiar este dict, nada mas. Un numero que no esta ahi es
# 'sin rol' (cliente normal) -- ROLES_JEFE.get(...) devuelve None por defecto, nunca
# hay que acordarse de excluirlo a mano en otro lado.
# jefe1 = JEFE_NUM (+56 9 XXXX XXXX, el manual del mostrador del local).
# jefe2 = JEFE_BACKUP (56984695292, celular de Yoci -- esposa de Christian, socia y
# duena del local igual que el, mismos tratos y privilegios, pedido de Gonza 30-jul-2026).
# Los dos tienen EXACTAMENTE las mismas atribuciones: mandan comandos (envio <monto> /
# confirmado / en camino / instrucciones del dia / gastos) y reciben las alertas del
# sistema (pendientes, rechazados, cobros de envio).
# El celular personal de Christian (56986923342, antes JEFE_PERSONAL: partia en modo
# PRUEBA y alternaba a modo JEFE escribiendo texto) pierde el rol por completo el
# 13-sep-2026 -- pedido explicito de Gonza. Es un cliente normal: no manda comandos, no
# recibe alertas, y el toggle de modo prueba/jefe (que solo existia para este numero)
# se elimina junto con el resto de este bloque.
# 10-ago-2026 -> revertido 13-sep-2026: JEFE_NUM habia salido de las alertas por ser un
# telefono fijo de mostrador que nadie mira ("una alerta perdida"). El pedido explicito
# de Gonza el 13-sep-2026 fue simetria total entre jefe1 y jefe2: vuelve a recibirlas.
JEFE_BACKUP = "56984695292"
ROLES_JEFE = {
    JEFE_NUM: "jefe1",
    JEFE_BACKUP: "jefe2",
}


def _rol_de(telefono):
    """'jefe1' / 'jefe2' / None (cliente normal, sin rol) segun el numero normalizado."""
    tel_norm = _norm_telefono(telefono)
    return next((rol for num, rol in ROLES_JEFE.items() if _norm_telefono(num) == tel_norm),
                None)


NUMS_ALERTAS_JEFE = (JEFE_NUM, JEFE_BACKUP)
# Minutos EXTRA que se le dan al backup una vez que la espera del jefe ya vencio.
ESPERA_BACKUP_MINUTOS = 5
# PLANTILLA DE META PARA EL RESPALDO (18-ago-2026). Creada a mano en el Administrador de
# WhatsApp de la cuenta "Donde el Patata Huechuraba" -- OJO, no en la de prueba, que es
# donde cae el administrador por defecto y donde una plantilla no le sirve al bot.
# Cuerpo aprobado: "Patata: nadie respondio {{1}}. Detalle: {{2}}. Responde en este chat
# para destrabar el pedido." Si se cambia el nombre o el idioma alla, hay que cambiarlo
# ACA tambien: Meta exige que coincidan exacto o rechaza el envio con un 4xx.
PLANTILLA_RESPALDO = "aviso_respaldo_patata"
PLANTILLA_IDIOMA = "es"
# Tope por variable. No es un limite de Meta (el cuerpo entero admite ~1024), es para que
# un resumen largo no se coma el mensaje y quede ilegible en la pantalla del telefono.
MAX_VARIABLE_PLANTILLA = 300
# Pedido delivery sin cobro de envio conocido: se retiene esperando que el jefe
# responda 'envio <monto>'. Salvaguarda anti-limbo en DOS escalones (30-jul-2026): a los
# ESPERA_COBRO_MINUTOS se escala al backup, y recien ESPERA_BACKUP_MINUTOS despues el
# pedido entra IGUAL a cocina con 'ENVIO POR DEFINIR' (leccion del caso Matias: un
# pedido nunca puede quedar esperando invisible para siempre).
# 10 -> 5 min por respuesta de Christian (P11, 30-jul-2026): "dejemos en 5 minutos yo,
# 5 minutos el respaldo". Techo total de espera del cliente: 10 min, no 15.
ESPERA_COBRO_MINUTOS = 5
# Pedido por TRANSFERENCIA (27-jul-2026, S2.5 punto 7 del plan): el bot no puede ver
# el pantallazo, asi que antes se mandaba derecho a cocina "por confirmar" y a veces
# se preparaba sin que la plata hubiera llegado de verdad (bug real, conv. 27: "funciono
# de milagro"). Ahora se retiene igual que el cobro de envio -- mismo patron, mismo
# timeout anti-limbo -- hasta que el jefe responda 'confirmado'.
# 15 -> 5 min por respuesta de Christian (P14, 30-jul-2026): "tiempo de espera 5 minutos
# jefe y 5 minutos respaldo, luego entra igual marcado 'por confirmar'".
ESPERA_COMPROBANTE_MINUTOS = 5
# Lo que el cliente ve cuando su pedido queda RETENIDO por transferencia (29-jul-2026,
# bug real de la prueba de Gonza): el texto que escribe el modelo cierra con "listo,
# aviso al local", que en esta rama es FALSO -- el pedido no entra a cocina hasta que
# alguien mire el pantallazo. El cliente se quedaba esperando 30 min algo que nunca
# arranco. La rama de cobro de envio ya avisaba lo suyo (ver _continuar_despacho);
# esta no avisaba nada. Va al FINAL del mensaje: es lo ultimo que lee el cliente.
AVISO_ESPERANDO_COMPROBANTE = (
    "💳 Ojo: todavía NO entra a cocina. Primero el local tiene que ver tu "
    "transferencia — mándame el pantallazo por acá y apenas la confirmen te aviso "
    "y arrancamos 🙌")
# EL ENVIO NO LO PONE EL CLIENTE (30-jul-2026, bug real de la prueba de Gonza: el
# cliente escribio cuanto "le sale" el delivery y el modelo lo sumo al total del chat).
# El sistema ya ignoraba ese monto para la comanda (_payload_pedido manda siempre
# delivery_cobro=0), pero el cliente igual leia -- y podia transferir -- un total que
# el local nunca cobro. Ahora, cuando el modelo acepta un envio dictado, el codigo lo
# borra Y le desmiente el total al cliente en el mismo mensaje.
# Redaccion a prueba de los DOS caminos: la direccion puede tener tarifa guardada (el
# envio real ya se le dijo en el mensaje anterior) o no (se le dice despues). Por eso no
# promete "te lo confirmo en un momento": seria falso en el primer caso.
AVISO_ENVIO_NO_LO_PONE_EL_CLIENTE = (
    "🛵 Ojo con el valor del envío: lo fija el local según tu dirección, así que no "
    "puedo tomarlo de un monto dado por acá. El total que vale es el que te confirmo "
    "yo, no el que aparece más arriba.")
# El numero que el local le da al cliente cuando hay que hablar con una persona. Vive
# aca una sola vez para no tenerlo escrito a mano en cada mensaje (tambien esta en
# system_prompt.md 3.8 y en data/menu.md, que son documentos, no codigo).
TELEFONO_LOCAL_VISIBLE = "+56 9 XXXX XXXX"
# PEDIDO DUPLICADO (05-ago-2026, pendiente #9 -- visto probando contra el modelo real):
# despues de cerrar un pedido, el cliente escribio "si, confirmo" otra vez y el modelo
# volvio a llamar a emitir_pedido. Eso es un SEGUNDO ticket a cocina: comida preparada
# dos veces, plata perdida de verdad. Lo que existia (_nota_pedido_reciente) es una NOTA
# para el modelo, o sea depende de que obedezca; esto es codigo.
# Dice "tomado" y NO "en preparacion" a proposito: un pedido por transferencia queda
# RETENIDO hasta que el jefe vea el pantallazo, y ahi "en preparacion" seria mentira --
# el cliente podria dejar de mandar su comprobante creyendo que ya esta cocinandose.
MENSAJE_PEDIDO_YA_TOMADO = (
    "¡Ese pedido ya lo tengo tomado! 🙌 No te lo mando dos veces. "
    f"Si necesitas cambiar o agregar algo, llama al local al {TELEFONO_LOCAL_VISIBLE}.")
# CIERRE DE CORTESIA tras un pedido YA despachado (02-oct-2026, casos 109/110 del
# curador): el modelo a veces vuelve a llamar a emitir_pedido con el MISMO pedido ante
# un simple "yap, no hay problema" / "dale" -- no porque el cliente pida reenviarlo, sino
# porque el chat sigue abierto (regla 3.10a). Responderle con MENSAJE_PEDIDO_YA_TOMADO ahi
# no tiene sentido: el cliente no pidio nada, solo se estaba despidiendo (caso real
# 109: "Yap, no hay problema" -> el bot contesto "Ese pedido ya lo tengo tomado", que
# no es respuesta a nada que haya dicho). MENSAJE_PEDIDO_YA_TOMADO se mantiene para el
# caso que SI amerita la aclaracion (ej. "si, confirmo" repetido, caso original del 9).
MENSAJE_CIERRE_CORTESIA_DUPLICADO = "¡De nada, gracias a ti! 🙌"
# Caso 170 (15-oct-2026): a un "ok"/"dale" sin gracias, sin emoji (pedido de Gonza).
MENSAJE_ACUSE_DUPLICADO = "Perfecto"
MENSAJE_SEGUNDO_PEDIDO_A_HUMANO = (
    "Tu pedido anterior ya quedó confirmado. Lo que acabas de agregar/cambiar se lo "
    "paso a una persona del local para sumarlo a ESE MISMO pedido -- no te llega "
    "aparte. Ya les avisé y te escriben en un ratito — si es urgente, llámalos al "
    f"{TELEFONO_LOCAL_VISIBLE}.")
CONFIG_DEFAULT = {
    "verify_token": "cambiame_por_un_texto_secreto",
    "phone_number_id": "",
    "access_token": "",
    "app_secret": "",
    "puerto": 8001,
    "pos_api_url": "http://127.0.0.1:8000",   # el cajero corriendo, al que el bot le manda el pedido
}
# Variables de entorno para la nube (Railway): tienen prioridad sobre config_webhook.json
# (que en la nube no existe -- las credenciales viajan como variables, nunca como
# archivo). En un PC local, sin estas variables definidas, se sigue leyendo el archivo
# igual que siempre -- no rompe el flujo de desarrollo/instalacion actual.
_ENV_CONFIG = {
    "verify_token": "META_VERIFY_TOKEN",
    "phone_number_id": "META_PHONE_NUMBER_ID",
    "access_token": "META_ACCESS_TOKEN",
    "app_secret": "META_APP_SECRET",
    "pos_api_url": "POS_API_URL",
}
MAX_TURNOS_HISTORIAL = 20   # tope defensivo: no dejar crecer el contexto sin limite
# Caducidad de una conversacion sin cerrar (15-jul-2026): un cliente saludo 2 horas
# despues de una conversacion inconclusa y el bot le retomo el hilo viejo ("¿listo
# para tu segundo pedido?"). Si la ultima actividad es mas vieja que esto, la
# conversacion arranca fresca. 6h cubre un turno completo de servicio (un reclamo
# de "¿y mi pedido?" 1-2h despues SI conserva el contexto, que es lo deseable).
HORAS_CADUCIDAD_HISTORIAL = 6
# LA VENTANA DE DESPUES DEL CIERRE (18-ago-2026). Cerrado el pedido, la conversacion
# sigue entrando al prompt este rato mas. Es lo que le permite al modelo aplicar la
# seccion 3.10 del prompt ("se esta despidiendo" vs "quiere tocar el pedido") en vez de
# saludar de cero a alguien que acaba de confirmar (chat del 15-ago: "A eso me referia"
# a los 10 s del cierre y el bot contesto con la bienvenida entera).
#
# MAS CORTA que MINUTOS_MEMORIA_PEDIDO_RECIENTE a proposito, y hay que mantenerlo asi:
# la nota de abajo solo se consulta cuando el historial ya esta vacio (ver
# `abre_conversacion`), o sea que si las dos duraran lo mismo la nota no se leeria NUNCA
# -- quedaria viva en el codigo y muerta en la practica. Las dos capas se turnan: los
# primeros 15 min manda el hilo completo, despues toma el relevo la nota.
MINUTOS_VENTANA_POSCIERRE = 15
# Memoria de pedido reciente (27-jul-2026, S2.5 punto 3): al despachar, la conversacion
# se archiva (correcto -- que el proximo saludo arranque fresco), y eso dejaba al bot SIN
# memoria si el mismo cliente escribia de nuevo 5-20 min despues para agregar/cambiar
# algo -- lo trataba como cliente nuevo. Con esta ventana, el bot reconoce que hubo un
# pedido reciente y lo dice en vez de ignorarlo.
# Subido de 30 a 60 (18-sep-2026, caso #24 del curador): el bot le promete al cliente
# "30-40 min aprox" de demora (ver _MINUTOS_DEMORA en prototipo.py, hasta 40 en dia
# cargado), y con 30 min de memoria un cliente que preguntaba "ya viene?" justo dentro de
# ESE mismo rango prometido (a los 52 min, en el caso real) ya habia perdido la memoria
# -- el bot le contestaba con el saludo de bienvenida entero, como a un desconocido. La
# ventana tiene que cubrir la demora maxima prometida MAS el margen de que el cliente
# recien pregunte, no calzar justo con el piso de la demora.
MINUTOS_MEMORIA_PEDIDO_RECIENTE = 60
# Ventana del candado de pedido duplicado (05-ago-2026, pendiente #9). Mismo valor que
# la memoria de arriba y a proposito en una constante APARTE: una dice "contale al bot
# que hubo un pedido" y la otra dice "no despaches otro", y son decisiones distintas --
# si algun dia hay que alargar el bloqueo sin alargar la memoria (o al reves), se mueve
# una sola. 30 min cubre de sobra la preparacion de un pedido de comida rapida.
MINUTOS_BLOQUEO_PEDIDO_DUPLICADO = 30
# REAPERTURA (05-ago-2026, bug real de Gonza probando en produccion): un cliente
# cancelo su pedido ("no voy a ordenar nada por hoy") y volvio a escribir "hola" 74
# minutos despues. La conversacion seguia VIVA (no caduco -- faltaban 4h45 para las
# 6h -- y nadie la cerro, porque cancelar no despacha ningun pedido), asi que el bot
# lo trato como si siguiera a mitad del pedido anterior: le contesto una ficha pelada,
# sin saludo y sin su nombre registrado. Las dos cosas venian de la misma raiz --
# "conversacion nueva" se decidia SOLO con el historial vacio.
# Con esta ventana, un silencio largo cuenta como conversacion nueva a los dos efectos
# (volver a saludar y volver a traer los datos del cliente conocido), sin borrar el
# hilo: el contexto sigue ahi si el cliente retoma el tema.
# 30 min: mas que suficiente para un pedido que se esta armando (los mensajes reales
# van de 1 a 3 min), y bien por debajo de la caducidad dura de 6h.
MINUTOS_REAPERTURA_CONVERSACION = 30
# Limite anti-abuso de mensajes entrantes (17-ago-2026, pendiente #34, umbral
# confirmado por Gonza). Hoy no hay ningun tope por remitente: el debounce de 8s
# junta las rafagas rapidas, pero a alguien que escribe cada 10s no lo agrupa nada
# -- son llamadas pagadas a OpenAI, una por mensaje. Cuenta CLIENTE + BOT, no solo
# cliente: un pedido normal completo ya son 10-15 turnos contando las respuestas,
# asi que el limite tiene que medir lo mismo que compara. No banea -- es una
# ventana movil sobre `turnos`, se destraba sola si el numero deja de escribir.
# 26-sep-2026 (Gonza): 20 -> 40. El 25-sep a las 12:06 el bot dejo de contestarle a una
# clienta REAL a mitad de su pedido (56992218372, 9 productos): 20 turnos en 10 min se
# alcanzan en un pedido grande, sobre todo si el bot contesta por partes. Ventana
# 10 -> 20 min, tambien decision de Gonza.
LIMITE_MENSAJES_ANTIABUSO = 40
VENTANA_ANTIABUSO_MINUTOS = 20
# Dedupe de reintentos de Meta (27-jul-2026, bug real: si el bot tarda en responder
# o hay un corte de red, Meta reenvia el MISMO mensaje -- antes eso duplicaba la
# comanda entera). Se guarda el msg["id"] de cada mensaje visto ANTES de procesarlo;
# un id repetido se descarta en silencio. 7 dias de retencion es de sobra (Meta deja
# de reintentar mucho antes) y mantiene la tabla chica.
DIAS_RETENCION_VISTOS = 7
# Retencion de conversaciones ya CERRADAS (29-jul-2026). Desde esta fecha cerrar un
# pedido no borra la conversacion: la ARCHIVA (marca `cerrado_en`), porque la misma
# tabla `turnos` alimenta la pantalla en vivo del POS y el panel de pendientes -- ver
# limpiar_historial(). Como ya no se borra nada, hace falta un tope o la base del
# volumen crece para siempre: pasado esto, lo archivado se elimina de verdad. 30 dias
# cubre de sobra cualquier reclamo ("el pedido del finde pasado") sin engordar.
DIAS_RETENCION_TURNOS = 30


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _cfg_webhook():
    """default < config_webhook.json (si existe, uso local) < variables de entorno
    (mayor prioridad -- asi corre en la nube sin el archivo, que jamas se sube a git
    porque lleva secretos). PORT la fija la plataforma de hosting (Railway); 'puerto'
    del json/default es el fallback para correr suelto en un PC."""
    c = dict(CONFIG_DEFAULT)
    if CONFIG.exists():
        try:
            c.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except Exception:
            pass
    for campo, var in _ENV_CONFIG.items():
        valor = os.environ.get(var, "").strip()
        if valor:
            c[campo] = valor
    c["puerto"] = int(os.environ.get("PORT", c.get("puerto", 8001)))
    return c


def _norm_telefono(tel):
    """Solo digitos; si trae +56/56 adelante, toma los ultimos 9 (movil chileno).
    Copia deliberada de NucleoPedidos.norm_telefono (pos/nucleo.py): es pura
    formateo de texto, sin estado, y el bot no debe depender de todo pos/ solo
    por esta funcion (ver nota junto a los imports, arriba)."""
    d = "".join(ch for ch in str(tel or "") if ch.isdigit())
    return d[-9:] if len(d) > 9 else d


def _init_config():
    if CONFIG.exists():
        print(f"Ya existe {CONFIG}. No lo piso (borralo a mano si queres reiniciarlo).")
        return
    CONFIG.write_text(json.dumps(CONFIG_DEFAULT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Creado {CONFIG} con placeholders. Completa verify_token/phone_number_id/"
          "access_token/app_secret siguiendo bot/GUIA_WEBHOOK.md.")


# ---------------------------------------------------------------------------
# Conversación por teléfono (persistida en SQLite propia del bot -- no toca pedidos.db)
# ---------------------------------------------------------------------------
def _migrar_turnos(con):
    """Agrega `cerrado_en` a una tabla `turnos` vieja que no lo tenga (29-jul-2026).
    Mismo patron que la migracion suave de `caja_id` en pos/nucleo.py: la base del
    volumen de Railway ya existe y tiene conversaciones dentro -- no se puede recrear.
    NULL = conversacion viva (la ve el cerebro); con fecha = archivada (solo la ven
    las pantallas del POS)."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(turnos)").fetchall()]
    if "cerrado_en" not in cols:
        con.execute("ALTER TABLE turnos ADD COLUMN cerrado_en TEXT")


def _con_conv():
    import sqlite3
    con = sqlite3.connect(CONV_DB, check_same_thread=False)
    con.execute("""CREATE TABLE IF NOT EXISTS turnos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telefono TEXT NOT NULL,
        rol TEXT NOT NULL,
        texto TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        cerrado_en TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS mensajes_vistos (
        msg_id TEXT PRIMARY KEY,
        visto_en TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS seguimientos_saludo (
        telefono TEXT NOT NULL,
        turno_id INTEGER NOT NULL,
        enviado_en TEXT NOT NULL,
        PRIMARY KEY (telefono, turno_id)
    )""")
    _migrar_turnos(con)
    con.commit()
    return con


def historial(telefono, db_path=None):
    """El contexto VIVO de un cliente, para el cerebro: los turnos sin `cerrado_en`, MAS
    los que cerraron hace menos de MINUTOS_VENTANA_POSCIERRE.
    29-jul-2026: la caducidad ya no BORRA la conversacion vencida, la archiva -- borrar
    destruia el hilo que las pantallas del POS todavia necesitan mostrar.

    18-ago-2026 -- LA VENTANA DE DESPUES DEL CIERRE. Antes el filtro era solo
    `cerrado_en IS NULL`, asi que al confirmar un pedido el modelo perdia la conversacion
    entera de un saque. Efecto real (chat del 15-ago, 23:16): el cliente escribio "A eso
    me referia" DIEZ SEGUNDOS despues de la confirmacion y el bot le contesto con la
    bienvenida completa, como a un desconocido. No fue el modelo desobedeciendo: la
    seccion 3.10 del prompt ("se esta despidiendo -> una linea corta y nada mas") le
    hablaba de una conversacion que el ya no podia ver, y el candado del saludo se apoya
    en la MISMA señal (historial vacio = conversacion nueva), asi que tampoco lo tapaba.
    Con la ventana, la 3.10 por fin tiene sobre que operar y `abre_conversacion` deja de
    mentir. Vencida la ventana el hilo desaparece solo -- no hay que archivarlo de nuevo,
    ya lo esta -- y ahi toma el relevo la nota de _nota_pedido_reciente."""
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        corte_cierre = (datetime.now() - timedelta(minutes=MINUTOS_VENTANA_POSCIERRE)
                        ).isoformat(timespec="seconds")
        filas = con.execute(
            "SELECT rol, texto, creado_en FROM turnos "
            "WHERE telefono=? AND (cerrado_en IS NULL OR cerrado_en >= ?) ORDER BY id ASC",
            (telefono, corte_cierre)).fetchall()
        if filas:
            try:
                ultimo = datetime.fromisoformat(filas[-1][2])
                if (datetime.now() - ultimo).total_seconds() > HORAS_CADUCIDAD_HISTORIAL * 3600:
                    # Se archiva con la fecha del ULTIMO TURNO, no con la de ahora: si se
                    # usara 'ahora', un hilo muerto hace 8 h entraria a la ventana de
                    # poscierre y volveria al prompt en el mensaje siguiente -- justo lo
                    # contrario de lo que la caducidad viene a hacer.
                    _archivar_turnos(con, telefono, cuando=filas[-1][2])
                    con.commit()
                    return []
            except (TypeError, ValueError):
                pass   # timestamp ilegible: mejor conservar el historial que romperlo
        return [{"rol": r, "texto": t} for r, t, _ in filas[-MAX_TURNOS_HISTORIAL:]]
    finally:
        con.close()


def minutos_desde_ultimo_turno(telefono, db_path=None):
    """Minutos transcurridos desde el ultimo turno VIVO de este cliente, o None si no
    hay ninguno (o si el timestamp es ilegible). Lo usa procesar_mensaje para decidir
    si el cliente esta RETOMANDO una conversacion vieja -- ver
    MINUTOS_REAPERTURA_CONVERSACION."""
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        fila = con.execute(
            "SELECT creado_en FROM turnos WHERE telefono=? AND cerrado_en IS NULL "
            "ORDER BY id DESC LIMIT 1", (telefono,)).fetchone()
        if not fila:
            return None
        try:
            return (datetime.now() - datetime.fromisoformat(fila[0])).total_seconds() / 60
        except (TypeError, ValueError):
            return None   # timestamp ilegible: que decida el resto de la logica
    finally:
        con.close()


def _mensajes_recientes(telefono, minutos=VENTANA_ANTIABUSO_MINUTOS, db_path=None):
    """Cuantos turnos (cliente + bot, cualquier estado) dejo este telefono en los
    ultimos `minutos`. Es el conteo del candado anti-abuso (#34): a proposito NO
    filtra `cerrado_en` -- una racha de mensajes cuenta igual aunque el pedido ya
    haya cerrado en el medio."""
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        corte = (datetime.now() - timedelta(minutes=minutos)).isoformat(timespec="seconds")
        fila = con.execute(
            "SELECT COUNT(*) FROM turnos WHERE telefono=? AND creado_en >= ?",
            (telefono, corte)).fetchone()
        return fila[0] if fila else 0
    finally:
        con.close()


def guardar_turno(telefono, rol, texto, db_path=None):
    from datetime import datetime
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        cur = con.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                          (telefono, rol, texto, datetime.now().isoformat(timespec="seconds")))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


# Caso 167 del curador (14-oct-2026): el curador y la pantalla del POS mostraban
# mensajes INCOMPLETOS. El aviso de transferencia ("todavia NO entra a cocina", el total
# y la cuenta) se pegaba al cierre DESPUES de guardar el turno, y los mensajes que salen
# fuera de la conversacion (cobro del envio, "recibimos tu transferencia", "va en
# camino"...) no se guardaban nunca. Christian curaba viendo menos de lo que leyo el
# cliente. Regla: todo lo que se le manda a un cliente queda en `turnos`, tal cual.
def registrar_enviado(telefono, texto, db_path=None):
    """Guarda un mensaje que el bot le mando al cliente por fuera de procesar_mensaje.
    Entra ARCHIVADO (cerrado_en = ahora): queda a la vista del curador, del POS y del
    contexto reciente del modelo (historial() incluye lo archivado hace poco), pero no
    pasa a ser el "ultimo turno vivo" que miran el seguimiento y los candados."""
    from datetime import datetime
    ahora = datetime.now().isoformat(timespec="seconds")
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        con.execute("INSERT INTO turnos (telefono, rol, texto, creado_en, cerrado_en) "
                    "VALUES (?,?,?,?,?)", (telefono, "asistente", texto, ahora, ahora))
        con.commit()
    finally:
        con.close()


def enviar_al_cliente(telefono, texto, cfg, db_path=None):
    """enviar_whatsapp + registrar_enviado: lo que sale, queda guardado."""
    r = enviar_whatsapp(telefono, texto, cfg)
    registrar_enviado(telefono, texto, db_path=db_path)
    return r


def _fijar_texto_turno(turno_id, texto, db_path=None):
    """Deja en el turno `turno_id` el texto que de verdad se envio, este archivado o no."""
    if not turno_id:
        return
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        con.execute("UPDATE turnos SET texto=? WHERE id=? AND texto IS NOT ?",
                    (texto, turno_id, texto))
        con.commit()
    finally:
        con.close()


def reescribir_ultimo_turno_asistente(telefono, texto, db_path=None):
    """Deja en el historial el texto que el cliente REALMENTE recibio.

    28-jul-2026: cuando un candado de codigo bloquea un pedido, al cliente le llega un
    mensaje fijo, pero en el historial quedaba guardada la respuesta ORIGINAL del modelo
    (la que confirmaba el pedido). En el turno siguiente el modelo leia su propia
    confirmacion como si hubiera salido, no la pregunta del candado -- y volvia a cerrar
    igual. Con esto, lo que el modelo lee es lo mismo que vio el cliente."""
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        con.execute(
            "UPDATE turnos SET texto=? WHERE id=("
            "  SELECT MAX(id) FROM turnos "
            "  WHERE telefono=? AND rol='asistente' AND cerrado_en IS NULL)",
            (texto, telefono))
        con.commit()
    finally:
        con.close()


def _archivar_turnos(con, telefono, cuando=None):
    """Marca como cerrada la conversacion VIVA de un telefono (no toca lo ya archivado,
    para no re-fechar un hilo antiguo).

    `cuando` es CUANDO se dio por terminada esa conversacion, y no siempre es 'ahora':
    un hilo que caduco por silencio (HORAS_CADUCIDAD_HISTORIAL) termino en su ultimo
    turno, hace horas, no en el instante en que alguien vino a mirarlo. La diferencia
    importa desde el 18-ago-2026, porque historial() usa esa fecha para decidir si la
    conversacion sigue en la ventana de despues del cierre."""
    con.execute("UPDATE turnos SET cerrado_en=? WHERE telefono=? AND cerrado_en IS NULL",
                (cuando or datetime.now().isoformat(timespec="seconds"), telefono))


def limpiar_historial(telefono, db_path=None):
    """Cierra la conversacion de un cliente: el cerebro arranca fresco la proxima vez.

    29-jul-2026 -- ARCHIVA, ya no borra. Antes hacia `DELETE FROM turnos`, y esa es la
    MISMA tabla que leen la pantalla en vivo del POS (`todos_los_turnos`) y el panel de
    pendientes (`turnos_de`): al cerrar un pedido, la conversacion entera desaparecia de
    las dos. Christian abria el chat del bot y solo veia lo que el cliente hubiera escrito
    DESPUES del cierre (bug real, prueba de Gonza 29-jul), y el boton "Ver conversacion"
    de un pendiente mostraba vacio -- justo el caso para el que se construyo el 29-jul.
    El efecto buscado (que el proximo saludo arranque sin contexto viejo) lo da igual el
    filtro `cerrado_en IS NULL` de historial(); lo unico que se pierde es la destruccion.
    """
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        _archivar_turnos(con, telefono)
        # Poda: sin DELETE en ningun lado, la base del volumen creceria sin techo.
        corte = (datetime.now() - timedelta(days=DIAS_RETENCION_TURNOS)).isoformat(timespec="seconds")
        con.execute("DELETE FROM turnos WHERE cerrado_en IS NOT NULL AND cerrado_en < ?", (corte,))
        con.commit()
    finally:
        con.close()


def todos_los_turnos(desde_id=0, limite=300, db_path=None):
    """Conversacion COMPLETA (todos los telefonos), para la pantalla en vivo del POS
    (14-jul-2026: Christian pidio poder ver que conversa el bot -- hoy no tenia
    ninguna visibilidad y le daba ansiedad). `desde_id` para pedir solo lo nuevo
    (polling incremental); id=0 trae el historial completo (recortado a `limite`)."""
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        filas = con.execute(
            "SELECT id, telefono, rol, texto, creado_en FROM turnos "
            "WHERE id > ? ORDER BY id DESC LIMIT ?", (desde_id, limite)).fetchall()
        turnos = [{"id": f[0], "telefono": f[1], "rol": f[2], "texto": f[3], "creado_en": f[4]}
                  for f in filas]
        turnos.reverse()   # de vuelta a orden cronologico ascendente
        return turnos
    finally:
        con.close()


def turnos_de(telefono, limite=60, db_path=None):
    """La conversacion de UN cliente, para el panel de pendientes del POS
    (29-jul-2026): cuando un pedido no llega a la cola, Christian necesita leer QUE
    hablo ese cliente sin cazarlo entre los mensajes de todos los demas.

    SOLO LEE -- y esa es la razon de que exista, en vez de reusar `historial()`.
    `historial()` es el lector del CEREBRO: solo devuelve la conversacion VIVA (filtra
    `cerrado_en IS NULL`), archiva la vencida y recorta a MAX_TURNOS_HISTORIAL. Usarla
    aca devolveria vacio justo en el caso que importa, porque un pedido pendiente de
    anoche siempre esta vencido o cerrado. Nunca cambiar esto por `historial()`.
    (Hasta el 29-jul-2026 el riesgo era peor: `historial()` BORRABA, asi que abrir el
    panel destruia la conversacion. Hoy solo la esconde; el candado sigue valiendo.)"""
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        filas = con.execute(
            "SELECT rol, texto, creado_en FROM turnos WHERE telefono=? "
            "ORDER BY id DESC LIMIT ?", (telefono, limite)).fetchall()
        turnos = [{"rol": r, "texto": t, "creado_en": c} for r, t, c in filas]
        turnos.reverse()
        return turnos
    finally:
        con.close()


def _con_conv_en(db_path):
    import sqlite3
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.execute("""CREATE TABLE IF NOT EXISTS turnos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telefono TEXT NOT NULL, rol TEXT NOT NULL, texto TEXT NOT NULL,
        creado_en TEXT NOT NULL, cerrado_en TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS mensajes_vistos (
        msg_id TEXT PRIMARY KEY, visto_en TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS seguimientos_saludo (
        telefono TEXT NOT NULL, turno_id INTEGER NOT NULL, enviado_en TEXT NOT NULL,
        PRIMARY KEY (telefono, turno_id)
    )""")
    _migrar_turnos(con)
    con.commit()
    return con


# ---------------------------------------------------------------------------
# Dedupe de reintentos de Meta + lock por telefono (27-jul-2026, sesion S1 del
# plan) -- ver DIAS_RETENCION_VISTOS arriba.
# ---------------------------------------------------------------------------
def _ids_del_payload(payload):
    """Todos los msg['id'] de un payload de Meta (cualquier tipo, no solo texto --
    un reintento tambien puede ser de un mensaje que igual se ignora, y da lo mismo
    marcarlo como visto)."""
    ids = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                mid = msg.get("id")
                if mid:
                    ids.append(mid)
    return ids


def _nombres_de_perfil(valor):
    """{telefono normalizado: nombre de perfil de WhatsApp} de un 'value' del webhook de
    Meta. Meta manda esto en `contacts[]` junto a cada mensaje entrante -- es el nombre
    que el propio cliente puso en su WhatsApp, o sea el mismo que Christian veria si
    abriera el chat. Hasta el 06-ago-2026 se ignoraba, y por eso las comandas de clientes
    nuevos salian sin nadie adentro (ver _nombre_para_comanda)."""
    salida = {}
    for c in valor.get("contacts", []) or []:
        tel = _norm_telefono(c.get("wa_id") or "")
        nombre = ((c.get("profile") or {}).get("name") or "").strip()
        if tel and nombre:
            salida[tel] = nombre
    return salida


def _marcar_vistos_y_filtrar(msg_ids, db_path=None):
    """Devuelve el subconjunto de msg_ids que NO se habian visto antes, dejandolos
    marcados en el mismo paso -- asi un reintento que llega mientras el primero
    todavia se esta procesando (en otro hilo) tambien se descarta, no solo los que
    llegan despues. Sin id (payload raro/de prueba) se deja pasar siempre."""
    import sqlite3
    if not msg_ids:
        return set()
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        ahora = datetime.now().isoformat(timespec="seconds")
        nuevos = set()
        for mid in msg_ids:
            try:
                con.execute("INSERT INTO mensajes_vistos (msg_id, visto_en) VALUES (?,?)",
                            (mid, ahora))
                nuevos.add(mid)
            except sqlite3.IntegrityError:
                pass   # ya visto -> reintento de Meta, se descarta en silencio
        corte = (datetime.now() - timedelta(days=DIAS_RETENCION_VISTOS)).isoformat(timespec="seconds")
        con.execute("DELETE FROM mensajes_vistos WHERE visto_en < ?", (corte,))
        con.commit()
        return nuevos
    finally:
        con.close()


_LOCKS_TELEFONO = {}
_LOCKS_TELEFONO_GUARD = threading.Lock()


def _lock_de(telefono):
    """Un lock por telefono (creado perezoso): mensajes del MISMO cliente que
    llegan casi juntos se procesan en orden, uno a la vez (si no, dos hilos
    leyendo/escribiendo el historial a la vez podrian pisarse o llamar al LLM dos
    veces con el mismo contexto viejo). Clientes DISTINTOS no se esperan entre si
    -- cada uno tiene su propio lock, y por eso el bot no se congela con trafico
    de un sabado."""
    with _LOCKS_TELEFONO_GUARD:
        return _LOCKS_TELEFONO.setdefault(telefono, threading.Lock())


# ---------------------------------------------------------------------------
# Seguridad: verificación del handshake de Meta + firma de cada POST (fail-closed)
# ---------------------------------------------------------------------------
def verificar_token(hub_mode, hub_verify_token, hub_challenge, cfg):
    """Handshake GET /webhook que exige Meta al configurar el webhook. Devuelve
    (status_code, cuerpo). Fail-closed: cualquier cosa que no calce exactamente -> 403."""
    if hub_mode == "subscribe" and hub_verify_token and hub_verify_token == cfg.get("verify_token"):
        return 200, hub_challenge or ""
    return 403, "verify_token invalido"


def firma_valida(cuerpo_bytes, firma_header, app_secret):
    """Valida X-Hub-Signature-256 (HMAC-SHA256 del cuerpo crudo con el App Secret de
    Meta) -- evita que cualquiera le mande POSTs falsos al webhook simulando ser Meta.
    Si NO hay app_secret configurado (etapa inicial de pruebas, antes de tener el
    panel de Meta completo) deja pasar pero es un hueco de seguridad temporal:
    documentado en GUIA_WEBHOOK.md, hay que completarlo antes de ir a producción real."""
    if not app_secret:
        return True
    if not firma_header or not firma_header.startswith("sha256="):
        return False
    esperada = hmac.new(app_secret.encode("utf-8"), cuerpo_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma_header.split("=", 1)[1], esperada)


# ---------------------------------------------------------------------------
# Envío a WhatsApp Cloud API
# ---------------------------------------------------------------------------
def enviar_whatsapp(telefono, texto, cfg, intentos=2):
    """Manda el mensaje por la Cloud API. Si faltan credenciales (etapa de pruebas
    locales, sin cuenta de Meta enchufada todavia), NO revienta: loguea 'simulado' y
    sigue -- asi el resto del webhook se puede verificar sin credenciales reales.

    27-jul-2026 (S2 del plan): antes un fallo de RED o un 5xx de Meta (caida
    transitoria) se tragaba en silencio -- un solo intento, best-effort. Ahora
    reintenta hasta 'intentos' veces ante fallas de conexion o 5xx (transitorios).
    Un 4xx (numero invalido, mensaje fuera de la ventana de 24h, texto rechazado) es
    definitivo -- repetir el mismo POST fallaria igual -- asi que NO se reintenta."""
    token, phone_id = cfg.get("access_token"), cfg.get("phone_number_id")
    if not token or not phone_id:
        print(f"[SIMULADO -- sin credenciales de Meta configuradas] -> {telefono}: {texto[:120]}")
        return {"simulado": True}
    return _post_meta({"messaging_product": "whatsapp", "to": telefono,
                       "type": "text", "text": {"body": texto}}, cfg, intentos)


def _post_meta(cuerpo, cfg, intentos=2, que="mensaje"):
    """El POST a la Cloud API, con la politica de reintentos de S2 (27-jul-2026): una
    falla de RED o un 5xx (caida transitoria) se reintenta; un 4xx (numero invalido,
    fuera de la ventana de 24h, plantilla inexistente) es definitivo y repetir el mismo
    POST fallaria igual.

    Lo comparten enviar_whatsapp y enviar_plantilla_whatsapp a proposito: son el MISMO
    POST con distinto cuerpo, y dos copias de esta politica se habrian separado con el
    tiempo -- justo lo que paso con los avisos al respaldo, que nadie volvio a mirar."""
    import urllib.request
    import urllib.error

    token, phone_id = cfg.get("access_token"), cfg.get("phone_number_id")
    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    datos = json.dumps(cuerpo).encode("utf-8")
    ultimo_error = {"error": "sin detalle"}
    for intento in range(1, max(1, intentos) + 1):
        peticion = urllib.request.Request(url, data=datos, method="POST", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(peticion, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", errors="replace")
            print(f"[ERROR enviando {que} a WhatsApp] intento {intento}/{intentos} "
                  f"{e.code}: {detalle[:300]}")
            ultimo_error = {"error": detalle, "http_status": e.code}
            if not (500 <= e.code < 600):
                return ultimo_error   # 4xx: definitivo, reintentar no sirve
        except urllib.error.URLError as e:
            print(f"[ERROR de conexion a Meta] intento {intento}/{intentos} {e.reason}")
            ultimo_error = {"error": str(e.reason)}
        if intento < intentos:
            time.sleep(0.5)
    return ultimo_error


def enviar_plantilla_whatsapp(telefono, plantilla, variables, cfg,
                              idioma=PLANTILLA_IDIOMA, intentos=2):
    """Manda una PLANTILLA aprobada por Meta en vez de un mensaje libre.

    POR QUE EXISTE (18-ago-2026, medido en /salud, no supuesto): WhatsApp solo entrega
    texto libre a quien le escribio al bot en las ultimas 24 h; fuera de esa ventana solo
    pasan plantillas aprobadas. El jefe de RESPALDO es, por definicion, el que NO escribe
    -- existe solo para cuando el titular no contesta -- asi que su ventana esta siempre
    cerrada. Medicion de los 7 dias previos: respaldo 3 enviados / 3 fallidos / 0
    entregados (codigo 131047), jefe 2 de 2 leidos. O sea: el segundo escalon de la
    salvaguarda anti-limbo (ESPERA_BACKUP_MINUTOS, nacido del caso Matias) nunca
    funciono en produccion, y no habia forma de notarlo desde el POST -- Meta acepta el
    mensaje con 200 y recien avisa del rechazo por el webhook de estados.

    OJO con las variables: van SIEMPRE por _var_plantilla, nunca crudas."""
    token, phone_id = cfg.get("access_token"), cfg.get("phone_number_id")
    if not token or not phone_id:
        print(f"[SIMULADO -- sin credenciales de Meta] plantilla {plantilla} -> {telefono}")
        return {"simulado": True}
    return _post_meta({
        "messaging_product": "whatsapp", "to": telefono, "type": "template",
        "template": {"name": plantilla, "language": {"code": idioma},
                     "components": [{"type": "body", "parameters": [
                         {"type": "text", "text": v} for v in variables]}]},
    }, cfg, intentos, que=f"plantilla {plantilla}")


def _var_plantilla(texto):
    """Aplana un texto para que pueda viajar como parametro de una plantilla.

    Meta rechaza el mensaje ENTERO si un parametro trae saltos de linea, tabs o mas de 4
    espacios seguidos -- y _resumen_pedido_para_jefe devuelve exactamente eso: cuatro
    lineas. Sin este aplanado la plantilla fallaria siempre y el arreglo no serviria de
    nada; el aviso volveria a perderse, ahora por otro motivo."""
    limpio = " · ".join(t.strip() for t in str(texto or "").splitlines() if t.strip())
    limpio = re.sub(r"\s{2,}", " ", limpio).strip()
    if len(limpio) > MAX_VARIABLE_PLANTILLA:
        limpio = limpio[:MAX_VARIABLE_PLANTILLA - 1].rstrip() + "…"
    return limpio or "-"


# ---------------------------------------------------------------------------
# El "cerebro real" se crea UNA sola vez, PEREZOSO (recien al primer mensaje real) --
# asi --test nunca necesita una API key para correr.
# ---------------------------------------------------------------------------
_CEREBRO_SINGLETON = None


def _cerebro_real():
    global _CEREBRO_SINGLETON
    if _CEREBRO_SINGLETON is None:
        _CEREBRO_SINGLETON = CerebroOpenAI(dedicado=True)
    return _CEREBRO_SINGLETON


# ---------------------------------------------------------------------------
# EL NOMBRE CON QUE SALE LA COMANDA (06-ago-2026, pedido de Gonza: "repara lo del
# nombre").
#
# "Cliente WhatsApp" nacio como ULTIMO recurso y se habia vuelto el caso normal: desde
# que el nombre dejo de ser dato obligatorio (05-ago, bloque de salida de 2 pasos), toda
# comanda de cliente nuevo salia con ese texto. Y no quedaba ahi -- `nucleo.crear` guarda
# el nombre del pedido en la base de CLIENTES del POS (guardar_cliente), que despues se
# exporta a la cache que lee el bot: el placeholder volvia disfrazado de "nombre
# registrado" y, peor, PISABA el nombre real de un cliente conocido cada vez que el
# modelo se olvidaba de incluirlo.
#
# Ahora hay una cadena de confianza (ver _nombre_para_comanda) y un dato que ya llegaba
# gratis y se tiraba a la basura: el nombre de perfil de WhatsApp que Meta manda en CADA
# mensaje entrante. Nada de esto le pregunta nada al cliente ni al modelo.
# ---------------------------------------------------------------------------
NOMBRE_COMANDA_FALLBACK = "Cliente WhatsApp"
# Un nombre de perfil mas largo que esto desarma la linea de la comanda impresa.
LARGO_MAX_NOMBRE_PERFIL = 40


def _es_nombre_placeholder(nombre):
    """True si `nombre` no identifica a NADIE: vacio, el fallback generico, o el propio
    telefono escrito como nombre. Vive en una sola funcion porque la pregunta se hace en
    tres lugares distintos (que sale en la comanda, que se muestra en la ficha, y que se
    acepta como 'cliente registrado' del POS); si cada uno lo decidiera por su cuenta, el
    dia que cambie el texto del fallback quedaria alguno mirando el string viejo."""
    n = re.sub(r"\s+", " ", str(nombre or "")).strip()
    if not n or n.lower() == NOMBRE_COMANDA_FALLBACK.lower():
        return True
    return bool(re.fullmatch(r"[\d+()\-\s]+", n))   # el telefono no es un nombre


def _limpiar_nombre_perfil(nombre):
    """Deja el nombre de perfil de WhatsApp en algo IMPRIMIBLE en una comanda termica, o
    None si no sirve. La gente pone de todo en su perfil: emojis, corazones, el nombre
    del negocio, su propio numero. Se queda solo con letras y numeros latinos (un emoji
    en el ticket sale como basura o rompe la codificacion de la impresora) y corta a
    LARGO_MAX_NOMBRE_PERFIL."""
    limpio = re.sub(r"[^A-Za-zÀ-ÿ0-9' .-]", " ", str(nombre or ""))
    limpio = re.sub(r"\s+", " ", limpio).strip(" .-'")
    if len(limpio) < 2 or _es_nombre_placeholder(limpio):
        return None
    return limpio[:LARGO_MAX_NOMBRE_PERFIL].strip()


def _nombre_para_comanda(pedido, telefono, db_path=None):
    """Con que nombre sale la comanda a la cocina, en orden de confianza:

      1. el que el modelo puso en el pedido -- el cliente lo dijo en ESTA conversacion;
      2. el que el local ya tiene guardado para ese telefono (cache de clientes del POS);
      3. el nombre de perfil de WhatsApp (lo manda Meta en cada mensaje, ver
         _nombres_de_perfil);
      4. NOMBRE_COMANDA_FALLBACK, recien cuando no hay ninguno de los tres.

    El paso 2 no es comodidad: es el que evita que el placeholder PISE en la base del POS
    el nombre real de un cliente conocido (nucleo.crear -> guardar_cliente escribe este
    mismo campo)."""
    del_modelo = (pedido.get("cliente") or "").strip()
    if not _es_nombre_placeholder(del_modelo):
        return del_modelo
    registrado = _datos_cliente_registrado(telefono) or {}
    if not _es_nombre_placeholder(registrado.get("nombre")):
        return registrado["nombre"].strip()
    return _nombre_perfil(telefono, db_path=db_path) or NOMBRE_COMANDA_FALLBACK


def _payload_pedido(pedido, telefono, db_path=None):
    """Traduce el PEDIDO_JSON del bot al cuerpo que espera POST /api/pedidos (PedidoIn).
    delivery_cobro=0: el bot NO conoce la tarifa por zona (regla del system_prompt: 'el
    valor exacto se confirma segun direccion'); Christian la ajusta al ver el pedido.
    15-jul-2026: NORMALIZA lo que el modelo puede escribir 'a su manera' (modalidad o
    forma de pago con mayusculas, cantidad como texto) -- un valor fuera de norma hacia
    que el POS rechazara el pedido con 400 y quedara atascado en la cola de reintento."""
    def _cantidad(v):
        try:
            return max(1, int(str(v).strip()))
        except (TypeError, ValueError):
            return 1

    items = [{"categoria": it.get("categoria"), "producto": it.get("producto"),
              "variante": it.get("variante"), "cantidad": _cantidad(it.get("cantidad", 1)),
              "notas": it.get("notas", "") or ""} for it in pedido.get("items", [])]
    modalidad = str(pedido.get("modalidad") or "retiro").strip().lower()
    if modalidad not in ("retiro", "delivery", "local"):
        modalidad = "retiro"
    forma_pago = pedido.get("forma_pago")
    if forma_pago is not None:
        forma_pago = str(forma_pago).strip().lower() or None
    # 04-ago-2026 (P29): hora que el CLIENTE pidio para su pedido ("quiero mi pedido
    # para las 21:00"). Fail-safe: si el modelo escribio algo que no es HH:MM, se
    # ignora (el pedido entra normal, sin programar) en vez de que el POS lo rechace
    # con un 400 y quede atascado en la cola de reintento.
    hora_programada = pedido.get("hora_programada")
    if hora_programada is not None:
        hora_programada = str(hora_programada).strip()
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", hora_programada):
            hora_programada = None
    return {
        "canal": "whatsapp", "cliente": _nombre_para_comanda(pedido, telefono, db_path=db_path),
        "modalidad": modalidad, "items": items,
        "forma_pago": forma_pago, "direccion": pedido.get("direccion"),
        "delivery_cobro": 0, "notas": pedido.get("comentarios", "") or "", "telefono": telefono,
        "hora_programada": hora_programada,
    }


def _sanear_envio_dictado(pedido):
    """Borra del PEDIDO_JSON el cobro de envio que el modelo haya aceptado del CLIENTE y
    devuelve el monto que traia (0 si no traia nada). El envio lo fija SOLO el local: la
    tarifa guardada de esa direccion en el POS (_buscar_cobro_direccion) o la respuesta
    'envio <monto>' de un jefe -- nunca un numero dicho en el chat.

    30-jul-2026, bug real de la prueba de Gonza: el cliente escribio cuanto "le sale" el
    delivery y el modelo lo sumo al total. A la comanda ya no llegaba (_payload_pedido
    manda delivery_cobro=0 desde siempre), pero el cliente leia ese total inflado y por
    ese monto transferia. Esto es la mitad de codigo del candado; la otra mitad es
    avisarle al cliente que ignore ese total (AVISO_ENVIO_NO_LO_PONE_EL_CLIENTE).

    LIMITE HONESTO: solo ve el campo 'delivery_cobro'. Si el modelo mete el envio
    directo en 'total' sin declararlo, aca no se detecta -- lo tapa el flujo, que le
    manda al cliente el total definitivo recien cuando el envio esta resuelto
    (_aviso_transferencia, siempre tarificado por codigo)."""
    try:
        cobro = int(re.sub(r"[^\d]", "", str(pedido.get("delivery_cobro") or 0)) or 0)
    except (TypeError, ValueError):
        cobro = 0
    if cobro > 0:
        pedido["delivery_cobro"] = 0
        print(f"[envio][CANDADO] el modelo acepto delivery_cobro={cobro} dictado en el "
              "chat; lo ignoro (el envio lo pone el local).")
    return max(0, cobro)


def _headers_pos(extra=None):
    """Headers para cualquier llamada al POS. Hoy (LAN local) no manda nada extra --
    la 'llave' es que el pedido solo puede venir de dentro de la red. Cuando el bot
    viva en Railway y el POS se exponga por tunel a internet, sin candado CUALQUIERA
    podria crear pedidos falsos con solo adivinar la URL. Dos candados posibles, no
    excluyentes; cada uno se activa solo si su variable esta definida (sin ellas, el
    setup local actual funciona igual que siempre):
      - POS_BOT_TOKEN -> header X-Bot-Token (Opcion B, candado en el codigo del POS,
        sin depender de Cloudflare ni tarjeta). Es el que usamos.
      - POS_ACCESS_CLIENT_ID/SECRET -> service token de Cloudflare Access (Opcion A;
        queda soportado por si algun dia se activa Zero Trust)."""
    h = dict(extra or {})
    # 20-jul-2026 (bug real en produccion): Cloudflare rechazaba TODAS las llamadas del
    # bot al POS con 403 "error code: 1010" (Browser Integrity Check). No era Access ni
    # el candado del POS: urllib se identifica como 'Python-urllib/3.x', firma que la
    # proteccion anti-bots de Cloudflare tiene en lista negra. Un User-Agent propio y
    # descriptivo evita el bloqueo SIN debilitar la seguridad de la zona.
    h.setdefault("User-Agent", "BotPatata/1.0 (+https://dondeelpatata.cl)")
    bot_token = os.environ.get("POS_BOT_TOKEN", "").strip()
    if bot_token:
        h["X-Bot-Token"] = bot_token
    cid = os.environ.get("POS_ACCESS_CLIENT_ID", "").strip()
    secreto = os.environ.get("POS_ACCESS_CLIENT_SECRET", "").strip()
    if cid and secreto:
        h["CF-Access-Client-Id"] = cid
        h["CF-Access-Client-Secret"] = secreto
    return h


def _post_al_cajero(payload, pos_api_url):
    """POST /api/pedidos al cajero que YA esta corriendo (sin auth propia: hoy solo
    alcanzable en la LAN local; con Cloudflare Access -- ver _headers_pos -- protegido
    aun expuesto a internet). Asi el cajero es el UNICO proceso que escribe su
    pedidos.db -> el pedido aparece en la cola de Christian sin importar si corre el .exe
    o el codigo, y sin choques de SQLite entre dos procesos."""
    import urllib.request
    body = json.dumps(payload).encode("utf-8")
    url = pos_api_url.rstrip("/") + "/api/pedidos"
    peticion = urllib.request.Request(url, data=body, method="POST",
                                      headers=_headers_pos({"Content-Type": "application/json"}))
    with urllib.request.urlopen(peticion, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _refrescar_menu_pos(cfg):
    """Trae el menu.json REAL del POS (GET /api/menu-edit, la misma estructura cruda
    que usa la pantalla de Admin -- ya validada, sin cambios del lado del POS) y lo
    cachea en MENU_CACHE (27-jul-2026, S4 del plan: G, "dos verdades del menu").
    Se llama desde un hilo de fondo (ver _hilo_sincronizar_menu): NUNCA en el camino
    de una respuesta a un cliente -- la leccion de S1 sigue aplicando, nada externo
    puede demorar un mensaje. Fail-safe total: si el POS no contesta o lo que trae
    no valida como menu real (Tarificador lo revisa, incluidos combos, igual que
    hace el propio POS antes de guardar uno nuevo), se descarta y se sigue con lo
    que ya habia -- cache vieja o el menu.json empaquetado. Devuelve True/False."""
    import urllib.request
    url = (cfg.get("pos_api_url") or "http://127.0.0.1:8000").rstrip("/") + "/api/menu-edit"
    peticion = urllib.request.Request(url, headers=_headers_pos())
    try:
        with urllib.request.urlopen(peticion, timeout=8) as r:
            nuevo = json.loads(r.read().decode("utf-8"))
        Tarificador(nuevo)   # valida ANTES de aceptarlo -- un menu roto no debe pisar el bueno
    except Exception as e:
        print(f"[menu][AVISO] no pude traer el menu del POS ({e}); sigo con el que tengo.")
        return False
    tmp = prototipo.MENU_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(nuevo, ensure_ascii=False), encoding="utf-8")
    tmp.replace(prototipo.MENU_CACHE)   # atomico: nunca deja el cache a medio escribir
    return True


def _hilo_sincronizar_menu():
    """Background: refresca el menu desde el POS cada MENU_SYNC_SEGUNDOS (mas un
    primer intento apenas arranca, para no operar con el menu.json viejo del deploy
    mas tiempo del necesario si el tunel ya esta arriba). Mismo criterio que
    _hilo_reintento_pendientes: nunca debe tumbar el webhook por esto."""
    while True:
        try:
            _refrescar_menu_pos(_cfg_webhook())
        except Exception as e:
            print(f"[menu][AVISO] el hilo de sincronizacion fallo esta vuelta: {e}")
        time.sleep(MENU_SYNC_SEGUNDOS)


def _refrescar_clientes_pos(cfg):
    """Trae TODOS los clientes con telefono del POS (GET /api/clientes-export) y los
    cachea en CLIENTES_CACHE, keyed por telefono normalizado -> {nombre, direccion}
    (04-ago-2026, diseno acordado con Gonza para 'nombre registrado': cache local con
    refresco periodico, MISMO patron que _refrescar_menu_pos -- nunca una consulta en
    vivo en el camino de un mensaje). Se llama desde un hilo de fondo (ver
    _hilo_sincronizar_clientes). Fail-safe total: si el POS no contesta o la respuesta
    no calza con la forma esperada, se descarta y se sigue con la cache que ya habia.
    Devuelve True/False."""
    import urllib.request
    url = (cfg.get("pos_api_url") or "http://127.0.0.1:8000").rstrip("/") + "/api/clientes-export"
    peticion = urllib.request.Request(url, headers=_headers_pos())
    try:
        with urllib.request.urlopen(peticion, timeout=15) as r:
            crudo = json.loads(r.read().decode("utf-8"))
        cache = {}
        for c in crudo["clientes"]:
            tel = _norm_telefono(c.get("telefono"))
            nombre = (c.get("nombre") or "").strip()
            # 06-ago-2026: el POS puede tener guardado "Cliente WhatsApp" como nombre de
            # un telefono (lo escribio este mismo bot antes del fix del nombre, via
            # nucleo.crear -> guardar_cliente). Si entrara a la cache, el bot le diria al
            # modelo "este cliente ya se llama Cliente WhatsApp" y lo pondria en la ficha
            # como si fuera una persona. La cache no lo ingiere: para el bot ese telefono
            # simplemente no tiene nombre registrado.
            if not tel or _es_nombre_placeholder(nombre):
                continue
            cache[tel] = {"nombre": nombre, "direccion": (c.get("direccion") or "").strip()}
    except Exception as e:
        print(f"[clientes][AVISO] no pude traer los clientes del POS ({e}); sigo con la cache que tengo.")
        return False
    tmp = prototipo.CLIENTES_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(prototipo.CLIENTES_CACHE)   # atomico: nunca deja la cache a medio escribir
    return True


def _hilo_sincronizar_clientes():
    """Background: refresca la cache de clientes desde el POS cada CLIENTES_SYNC_SEGUNDOS.
    Mismo criterio que _hilo_sincronizar_menu: nunca debe tumbar el webhook por esto."""
    while True:
        try:
            _refrescar_clientes_pos(_cfg_webhook())
        except Exception as e:
            print(f"[clientes][AVISO] el hilo de sincronizacion fallo esta vuelta: {e}")
        time.sleep(CLIENTES_SYNC_SEGUNDOS)


def _pedidos_programados_pendientes(cfg):
    """Trae del POS los pedidos activos con hora_programada (GET /api/pedidos-programados,
    misma direccion bot->POS que _refrescar_menu_pos). Fail-safe: si el POS no responde,
    lista vacia -- no revienta el hilo."""
    import urllib.request
    url = (cfg.get("pos_api_url") or "http://127.0.0.1:8000").rstrip("/") + "/api/pedidos-programados"
    try:
        peticion = urllib.request.Request(url, headers=_headers_pos())
        with urllib.request.urlopen(peticion, timeout=8) as r:
            return json.loads(r.read().decode("utf-8")).get("pedidos", [])
    except Exception as e:
        print(f"[programados][AVISO] no pude consultar pedidos programados del POS ({e}).")
        return []


def _minutos_para_hora(hora_programada, ahora=None):
    """'21:00' -> minutos desde AHORA (Chile) hasta esa hora de HOY. Negativo si ya paso."""
    ahora = ahora or datetime.now(TZ_CHILE)
    h, m = (int(x) for x in hora_programada.split(":"))
    objetivo = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    return (objetivo - ahora).total_seconds() / 60


def _avisar_jefe_programado(pedido, hora, cfg, backup):
    # 13-sep-2026: el primer aviso va a jefe1 (JEFE_NUM) y, si nadie lo confirma, escala
    # a jefe2 (JEFE_BACKUP) -- mismo orden que el resto de las alertas del sistema ahora
    # que jefe1 volvio a recibirlas (ver el bloque de ROLES_JEFE).
    destinatarios = [JEFE_BACKUP] if backup else [JEFE_NUM]
    prefijo = "🆘 (nadie confirmó el primer aviso) " if backup else ""
    texto = (f"{prefijo}🟣 PEDIDO PROGRAMADO para las {hora} — hora de empezar a prepararlo:\n\n"
             f"{_resumen_pedido_para_jefe(pedido)}")
    for dest in destinatarios:
        enviar_whatsapp(dest, texto, cfg)


def _revisar_programados(cfg, db_path=None):
    """Un ciclo del recordatorio de pedidos programados (P29, 04-ago-2026): a
    RECORDATORIO_PROGRAMADO_MINUTOS de la hora pedida avisa al jefe; si en
    ESCALA_PROGRAMADO_MINUTOS nadie lo movio a cocina, avisa TAMBIEN al respaldo -- y
    ahi se detiene (nunca fuerza el pedido solo, a diferencia de cobro/transferencia:
    no hay un 'por defecto seguro' para arrancar a cocinar sin que un humano decida).
    Separado en funcion aparte (no adentro del hilo) para poder probarlo directo."""
    pedidos = _pedidos_programados_pendientes(cfg)
    con = _con_respaldo(db_path)
    try:
        for p in pedidos:
            if p.get("estado") not in ("recibido", "confirmado"):
                continue   # ya lo movieron a cocina -- nada que recordar
            hora = p.get("hora_programada")
            if not hora:
                continue
            try:
                # _ahora_chile() (no datetime.now directo): mockeable en --test, igual
                # que ya se hace para el corte de pedidos de las 23:50.
                faltan = _minutos_para_hora(hora, _ahora_chile())
            except Exception:
                continue
            if faltan > RECORDATORIO_PROGRAMADO_MINUTOS:
                continue   # todavia falta mucho
            fila = con.execute(
                "SELECT recordado_en, escalado_en FROM recordatorios_programados WHERE pedido_id=?",
                (p["id"],)).fetchone()
            ahora_iso = datetime.now().isoformat(timespec="seconds")
            if fila is None:
                _avisar_jefe_programado(p, hora, cfg, backup=False)
                con.execute(
                    "INSERT INTO recordatorios_programados (pedido_id, recordado_en) VALUES (?,?)",
                    (p["id"], ahora_iso))
                con.commit()
                continue
            if fila[1]:   # escalado_en
                continue   # ya se escalo -- de aca en mas es decision humana
            recordado = datetime.fromisoformat(fila[0])   # recordado_en
            if (datetime.now() - recordado).total_seconds() / 60 >= ESCALA_PROGRAMADO_MINUTOS:
                _avisar_jefe_programado(p, hora, cfg, backup=True)
                con.execute(
                    "UPDATE recordatorios_programados SET escalado_en=? WHERE pedido_id=?",
                    (ahora_iso, p["id"]))
                con.commit()
    finally:
        con.close()


def _hilo_recordar_programados():
    """Background: revisa cada PROGRAMADOS_POLL_SEGUNDOS si hay pedidos programados a
    los que avisar/escalar. Nunca debe tumbar el webhook por esto."""
    while True:
        try:
            _revisar_programados(_cfg_webhook())
        except Exception as e:
            print(f"[programados][AVISO] el hilo de recordatorio fallo esta vuelta: {e}")
        time.sleep(PROGRAMADOS_POLL_SEGUNDOS)


def _revisar_seguimientos_saludo(cfg, db_path=None, respaldo_db_path=None):
    """Un ciclo de seguimiento de saludo sin respuesta (13-sep-2026, pedido de Gonza):
    si el bot hablo ultimo en una conversacion VIVA y el cliente no contesto en
    MINUTOS_SEGUIMIENTO_SALUDO, manda UN mensaje corto de seguimiento -- nunca mas de
    uno por conversacion (candado: tabla seguimientos_saludo, PK telefono+turno_id).
    Separado en funcion aparte (no adentro del hilo) para poder probarlo directo, mismo
    criterio que _revisar_programados. Los numeros de jefe/staff (_JEFES_SIN_BUFFER)
    quedan afuera a proposito: un 'modo jefe' de Christian no es un cliente al que
    venderle. Tampoco manda nada si el local no esta tomando pedidos ahora mismo
    (cerrado o pasado el corte, mismo candado que usa el mensaje fijo de horario) --
    sería contradictorio avisar 'cerramos' y minutos despues insistir con el pedido.

    28-sep-2026 (bug real visto en el curador, casos 77/81/83): tampoco manda nada si
    el telefono tiene un pedido despachado hace menos de MINUTOS_MEMORIA_PEDIDO_RECIENTE
    -- el mismo plazo que ya usa _nota_pedido_reciente para decidir si el MODELO todavia
    recuerda ese pedido. Si el modelo lo recuerda, este seguimiento tambien tiene que
    saberlo: el turno 'vivo' que ve mas abajo no es un cliente a medias, es la cola de
    cortesia ('Ok', 'gracias') que deja la ventana de poscierre. `respaldo_db_path` es
    aparte de `db_path` (turnos vs. RESPALDO_DB) para poder probar los dos por separado,
    igual que ya hace el resto de este archivo.

    28-sep-2026 (bug real visto en el curador, caso 86): tampoco manda nada si el turno
    'vivo' es mas viejo que HORAS_CADUCIDAD_HISTORIAL -- mismo criterio que ya aplica
    historial() para dar por muerto un hilo. Sin esto, un mensaje como el fijo de
    'estamos cerrados' (que nunca se archiva, porque no hay despacho que dispare
    limpiar_historial) se queda 'vivo' para siempre; si el local estaba cerrado cuando
    llego, esta funcion ni se ejecuta (toma_pedidos() da False), pero apenas reabre
    -- HORAS despues -- ese turno viejisimo sigue siendo el 'ultimo turno vivo' del
    telefono y dispara un 'sigues ahi' sobre un saludo que ya se contesto del todo.
    Se archiva en el momento (misma fecha del turno, no la de ahora, mismo motivo que
    historial()) en vez de solo ignorarlo, para que no se vuelva a evaluar cada ciclo."""
    menu_actual = json.loads(prototipo.menu_json_activo().read_text(encoding="utf-8"))
    toma, _motivo = _toma_pedidos(menu_actual)
    if not toma:
        return
    con = _con_conv() if db_path is None else _con_conv_en(db_path)
    try:
        corte = (datetime.now() - timedelta(minutes=MINUTOS_SEGUIMIENTO_SALUDO)
                 ).isoformat(timespec="seconds")
        corte_caducado = (datetime.now() - timedelta(hours=HORAS_CADUCIDAD_HISTORIAL)
                          ).isoformat(timespec="seconds")
        # El ultimo turno VIVO de cada telefono (cerrado_en IS NULL): si el pedido ya
        # se confirmo, limpiar_historial() ya archivo el hilo y esta consulta no lo ve.
        filas = con.execute(
            "SELECT t.telefono, t.id, t.rol, t.creado_en FROM turnos t "
            "WHERE t.cerrado_en IS NULL AND t.id = ("
            "  SELECT MAX(id) FROM turnos t2 "
            "  WHERE t2.telefono = t.telefono AND t2.cerrado_en IS NULL)").fetchall()
        for telefono, turno_id, rol, creado_en in filas:
            if rol != "asistente" or creado_en > corte:
                continue
            if creado_en < corte_caducado:
                # Hilo caducado (mismo criterio que historial()): no es un cliente
                # esperando, es un turno viejo (ej. el fijo de 'estamos cerrados') que
                # nunca se archivo porque no hubo despacho. Se archiva y se sigue, sin
                # mandar nada. Ver docstring, bug del 28-sep (caso 86).
                _archivar_turnos(con, telefono, cuando=creado_en)
                con.commit()
                continue
            # El ultimo turno ya ES el recordatorio: no se insiste de nuevo (ver abajo).
            _ultimo = con.execute("SELECT texto FROM turnos WHERE id=?", (turno_id,)).fetchone()
            if _ultimo and _ultimo[0] == MENSAJE_SEGUIMIENTO_SALUDO:
                continue
            # 07-oct-2026 (Christian, caso 125): el cliente dijo "Buuu yaaa / Gracias" (ya
            # no queria pedir), el bot le contesto "¡Gracias a ti!" y 10 min despues le
            # llego el seguimiento. Una despedida del cliente no es un pedido a medias.
            if _ultimo and _es_despedida_del_bot(_ultimo[0]):
                continue
            # 01-oct-2026 (caso real visto en el curador): el cliente dijo "No gracias"
            # al PRIMER "sigues ahi" -- declino, no se quedo a medias -- y 10 min
            # despues, con un turno_id NUEVO (el de su propia despedida: "Para la otra
            # pedire, Gracias!" -> "¡Gracias a ti!"), le llego un SEGUNDO "sigues ahi".
            # El chequeo de arriba solo mira si el ULTIMO turno ES el recordatorio;
            # cualquier respuesta del cliente -- aunque sea un "no gracias" -- corre
            # ese turno y vuelve a abrir la puerta. El docstring de esta funcion
            # promete UN mensaje, nunca mas de uno por conversacion: eso hay que
            # mirarlo en todo el tramo vivo, no solo en su ultimo turno.
            _ya_avisado = con.execute(
                "SELECT 1 FROM turnos WHERE telefono=? AND cerrado_en IS NULL "
                "AND texto=? LIMIT 1", (telefono, MENSAJE_SEGUIMIENTO_SALUDO)).fetchone()
            if _ya_avisado:
                continue
            if _norm_telefono(telefono) in _JEFES_SIN_BUFFER:
                continue
            # 29-sep-2026 (bug real del curador, conversacion 94): un pedido RETENIDO
            # esperando que el jefe confirme la transferencia es el mismo caso que uno ya
            # despachado -- el cliente no esta "a medias", esta esperando a un HUMANO, no
            # al bot. Caso real: cliente transfirio y mando el comprobante a las 20:07, y
            # una hora despues (21:07, sin que nadie del local hubiera confirmado
            # todavia) igual le llego "sigues ahi? que te gustaria pedir" -- un mensaje
            # que no tiene sentido si el pedido ya esta en camino a cocina, solo
            # esperando que alguien mire el pantallazo. No depende de ninguna ventana de
            # tiempo (a diferencia del despacho reciente): mientras siga retenido, no se
            # insiste, sin importar cuanto lleve esperando.
            if _tiene_comprobante_pendiente(telefono, db_path=respaldo_db_path):
                continue
            # El pedido ya se despacho hace poco (turno 'vivo' = cortesia post-cierre,
            # no cliente a medias): no insistir. Ver docstring, bug del 28-sep.
            #
            # 29-sep-2026 (bug real del curador, conversacion 93): esto se media desde
            # que se DESPACHO el pedido, no desde que el cliente escribio por ultima
            # vez. Caso real: pedido despachado 17:45 ("10 min aprox"), el cliente
            # pregunto "esta listo?" a las 17:57 (el bot contesto bien: "el local te
            # avisa"), y a las 18:45 -- justo el borde de MINUTOS_MEMORIA_PEDIDO_RECIENTE
            # (60 min) medidos desde el DESPACHO, no desde su propio mensaje -- el bot
            # igual le pregunto "sigues ahi? cuentame que te gustaria pedir" a alguien
            # que solo estaba esperando que le avisaran. Ahora la ventana se mide desde
            # el ULTIMO MENSAJE DEL CLIENTE: mientras siga escribiendo sobre su pedido
            # (aunque sea solo "esta listo?"), la espera se sigue considerando fresca. Si
            # no hay turno de cliente que leer (no deberia pasar), se cae al criterio
            # viejo para no dejar el candado sin efecto.
            _reciente = _ultimo_pedido_despachado(telefono, db_path=respaldo_db_path)
            # Caso 157 (curador, 10-oct-2026): _ultimo_pedido_despachado no tiene ventana,
            # asi que un pedido de HACE UNA SEMANA entraba igual a esta rama y el
            # recordatorio esperaba 60 min desde el ultimo mensaje del cliente en vez de
            # 10. Le pasaba a todo cliente que ya habia pedido alguna vez. El pedido solo
            # cuenta si esta conversacion viva arranco dentro de
            # MINUTOS_MEMORIA_PEDIDO_RECIENTE despues de despacharlo (el caso 93: pidio y
            # 12 min despues pregunto "esta listo?").
            if _reciente:
                _inicio_vivo = con.execute(
                    "SELECT MIN(creado_en) FROM turnos WHERE telefono=? AND cerrado_en IS NULL",
                    (telefono,)).fetchone()
                if _inicio_vivo and _inicio_vivo[0]:
                    _desde_despacho = (datetime.fromisoformat(_inicio_vivo[0])
                                       - (datetime.now() - timedelta(minutes=_reciente[1]))
                                       ).total_seconds() / 60
                    if _desde_despacho > MINUTOS_MEMORIA_PEDIDO_RECIENTE:
                        _reciente = None
            if _reciente:
                _edad_relevante = _reciente[1]
                _ultimo_cliente = con.execute(
                    "SELECT creado_en FROM turnos WHERE telefono=? AND rol='usuario' "
                    "AND cerrado_en IS NULL ORDER BY id DESC LIMIT 1", (telefono,)).fetchone()
                if _ultimo_cliente:
                    _edad_relevante = (datetime.now()
                                       - datetime.fromisoformat(_ultimo_cliente[0])
                                       ).total_seconds() / 60
                # 01-oct-2026 (caso real 97 del curador: "hizo una pausa de 1 hora, es
                # demasiado"). Si el tramo 'vivo' que se esta mirando ARRANCO dentro de
                # MINUTOS_VENTANA_POSCIERRE del despacho (ej. el cliente solo agrego "a
                # nombre de Javiera" 9 min despues de confirmar), es cola de cortesia de
                # principio a fin -- igual que el "gracias" de los casos 77/81/83, solo
                # que este alcanzo a crear un turno mas. Ahi NO hay ventana que valga la
                # pena esperar a que expire: nunca es un pedido a medias, y
                # MINUTOS_MEMORIA_PEDIDO_RECIENTE de por si solo retrasaba el "sigues
                # ahi" en vez de evitarlo. Se detecta mirando cuando empezo el tramo vivo
                # (su primer turno sin cerrar), no el ultimo.
                _primer_vivo = con.execute(
                    "SELECT MIN(creado_en) FROM turnos WHERE telefono=? AND cerrado_en IS NULL",
                    (telefono,)).fetchone()
                if _primer_vivo and _primer_vivo[0]:
                    _despacho_en = datetime.now() - timedelta(minutes=_reciente[1])
                    _primer_vivo_dt = datetime.fromisoformat(_primer_vivo[0])
                    if (_despacho_en <= _primer_vivo_dt
                            <= _despacho_en + timedelta(minutes=MINUTOS_VENTANA_POSCIERRE)):
                        continue
                if _edad_relevante <= MINUTOS_MEMORIA_PEDIDO_RECIENTE:
                    continue
            ya = con.execute(
                "SELECT 1 FROM seguimientos_saludo WHERE telefono=? AND turno_id=?",
                (telefono, turno_id)).fetchone()
            if ya:
                continue
            enviar_whatsapp(telefono, MENSAJE_SEGUIMIENTO_SALUDO, cfg)
            con.execute(
                "INSERT INTO seguimientos_saludo (telefono, turno_id, enviado_en) "
                "VALUES (?,?,?)",
                (telefono, turno_id, datetime.now().isoformat(timespec="seconds")))
            # 25-sep-2026 (caso 59): ahora SI queda como turno, para que el curador (y el
            # propio modelo) vean que se le insistio. No se repite: el chequeo de arriba
            # salta cuando el ultimo turno ya es este mismo recordatorio.
            con.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                        (telefono, "asistente", MENSAJE_SEGUIMIENTO_SALUDO,
                         datetime.now().isoformat(timespec="seconds")))
            con.commit()
            # Hasta el 25-sep NO se guardaba como turno (por miedo a que el recordatorio
            # quedara como "ultimo turno vivo" y se repitiera cada 10 min). El costo era
            # que nadie podia ver si se habia mandado. Ahora se guarda, y lo que evita la
            # repeticion es el chequeo del texto mas arriba.
    finally:
        con.close()


def _hilo_seguimiento_saludo():
    """Background: revisa cada SEGUIMIENTO_SALUDO_POLL_SEGUNDOS si hay conversaciones
    que se quedaron esperando respuesta. Nunca debe tumbar el webhook por esto."""
    while True:
        try:
            _revisar_seguimientos_saludo(_cfg_webhook())
        except Exception as e:
            print(f"[seguimiento][AVISO] el hilo de seguimiento fallo esta vuelta: {e}")
        time.sleep(SEGUIMIENTO_SALUDO_POLL_SEGUNDOS)


def _en_ventana_pico(ahora=None):
    """True si 'ahora' (hora de Chile) cae en HORA_INICIO_MONITOR_PICO..HORA_FIN_MONITOR_PICO
    inclusive. Parametro `ahora` aparte (no _ahora_chile() adentro) para poder probarlo
    con cualquier hora sin mockear nada."""
    ahora = ahora or _ahora_chile()
    return HORA_INICIO_MONITOR_PICO <= ahora.hour <= HORA_FIN_MONITOR_PICO


def _problemas_monitor_pico(db_path=None):
    """Junta las señales que ya expone /salud (alertas_fallidas, casos_humanos,
    avisos_jefes fallidos) en una lista de (clave, mensaje) -- una clave ESTABLE por
    problema, para que _avisar_si_nuevo pueda distinguir 'lo mismo de la vuelta
    anterior' de 'esto es nuevo'. No inventa ninguna medicion: es el mismo dato que
    ya se mira a mano en el panel, solo que ahora alguien lo mira siempre."""
    problemas = []
    if contar_alertas_fallidas(db_path=db_path) > 0:
        problemas.append((
            "alertas_fallidas",
            "🆘 Hay avisos del sistema que no le llegaron a NINGUN jefe por ningun "
            "canal. Revisar el panel del POS.",
        ))
    for caso in listar_casos_humanos(db_path=db_path):
        try:
            minutos = (datetime.now() - datetime.fromisoformat(caso["creado_en"])
                      ).total_seconds() / 60
        except ValueError:
            continue
        if minutos >= MINUTOS_CASO_HUMANO_SIN_ATENDER:
            problemas.append((
                f"caso_humano_{caso['id']}",
                f"🙋 Caso humano #{caso['id']} ({caso['motivo']}) sin cerrar hace "
                f"{int(minutos)} min.",
            ))
    for falla in resumen_avisos_jefes(dias=1, db_path=db_path).get("fallas", []):
        problemas.append((
            f"aviso_fallido_{falla['destinatario']}_{falla['codigo']}",
            f"📵 Avisos a {falla['destinatario']} fallando (codigo {falla['codigo']}, "
            f"{falla['veces']} veces en 24h).",
        ))
    return problemas


def _avisar_si_nuevo(clave, titulo, mensaje, topic, db_path=None):
    """Push por ntfy.sh (mismo patron que avisar() de
    proyectos/busqueda_laboral/monitor/monitor.py) SOLO la primera vez que se ve esta
    `clave` -- sin esto, un problema sin resolver mandaria un push nuevo cada
    MONITOR_PICO_POLL_SEGUNDOS mientras nadie lo atiende. Devuelve True si mando el
    push (o si ya estaba simulado por falta de topic -- ver el llamador)."""
    con = _con_respaldo(db_path)
    try:
        if con.execute("SELECT 1 FROM monitor_avisos WHERE clave=?", (clave,)).fetchone():
            return False
        con.execute(
            "INSERT INTO monitor_avisos (clave, avisado_en) VALUES (?,?)",
            (clave, datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()
    import urllib.request
    try:
        peticion = urllib.request.Request(
            NTFY_URL + topic, data=mensaje.encode("utf-8"), method="POST",
            headers={"Title": titulo.encode("ascii", "ignore").decode(), "Priority": "4"})
        with urllib.request.urlopen(peticion, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        print(f"[monitor_pico][AVISO] no se pudo avisar por ntfy ({clave}): {e}")
        return False


def _revisar_monitor_pico(topic, db_path=None):
    """Un ciclo del monitor de la ventana pico. Sin `topic` no hace nada -- no hay a
    quien avisar, y no tiene sentido gastar en consultas que nadie va a leer."""
    if not topic or not _en_ventana_pico():
        return 0
    avisados = 0
    for clave, mensaje in _problemas_monitor_pico(db_path=db_path):
        if _avisar_si_nuevo(clave, "Patata: algo necesita ojos", mensaje, topic,
                            db_path=db_path):
            avisados += 1
    return avisados


def _hilo_monitor_pico():
    """Background: revisa cada MONITOR_PICO_POLL_SEGUNDOS, pero solo ACTUA durante la
    ventana de mayor demanda (ver HORA_INICIO/FIN_MONITOR_PICO). Nunca debe tumbar el
    webhook por esto."""
    while True:
        try:
            _revisar_monitor_pico(NTFY_TOPIC_PATATA)
        except Exception as e:
            print(f"[monitor_pico][AVISO] el hilo del monitor fallo esta vuelta: {e}")
        time.sleep(MONITOR_PICO_POLL_SEGUNDOS)


def _con_respaldo(db_path=None):
    import sqlite3
    con = sqlite3.connect(db_path or RESPALDO_DB, check_same_thread=False)
    con.execute("""CREATE TABLE IF NOT EXISTS pendientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload_json TEXT NOT NULL,
        telefono TEXT,
        creado_en TEXT NOT NULL,
        intentos INTEGER DEFAULT 0,
        entregado INTEGER DEFAULT 0,
        entregado_en TEXT,
        pedido_id INTEGER
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS esperando_cobro (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telefono TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        direccion TEXT,
        creado_en TEXT NOT NULL,
        resuelto INTEGER DEFAULT 0    -- 0=esperando | 1=jefe respondio | 2=timeout
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS esperando_comprobante (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telefono TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        resuelto INTEGER DEFAULT 0    -- 0=esperando | 1=jefe confirmo | 2=timeout
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS alertas_fallidas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        texto TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        resuelto INTEGER DEFAULT 0
    )""")
    # 15-sep-2026 (caso real: un cliente adulto mayor pidio ayuda humana, el bot
    # avisó a los jefes por WhatsApp, Meta acepto el envio con 200 -- y Christian NUNCA
    # se entero. alertas_fallidas solo guarda el fracaso TOTAL (los DOS jefes sin
    # entrega); un WhatsApp "aceptado" que nadie lee es indistinguible de uno atendido,
    # y hasta hoy no quedaba NINGUN rastro fuera de ese telefono. Esta tabla es la
    # aplicacion del mismo patron que ya usan pendientes/rechazados (cola durable +
    # pantalla en vivo del POS que Christian SI mira mientras trabaja) a todo caso
    # donde el bot deriva la conversacion a una persona: humano prometido, pedido
    # grande, segundo pedido superpuesto, y una falla del cerebro. El WhatsApp sigue
    # mandandose igual (es rapido cuando funciona) pero deja de ser el UNICO rastro.
    con.execute("""CREATE TABLE IF NOT EXISTS casos_humanos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        motivo TEXT NOT NULL,
        telefono TEXT,
        texto TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        resuelto INTEGER DEFAULT 0,
        resuelto_en TEXT,
        resuelto_por TEXT
    )""")
    # 26-sep-2026 (bot 2.0, paso 1): rastro por turno de QUE candado de codigo actuo.
    # Sin esto no se puede medir si un candado ayuda o estorba (ver
    # bot_2_0_investigacion.md). Es solo para analisis: nada lo consulta al decidir.
    con.execute("""CREATE TABLE IF NOT EXISTS eventos_candado (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candado TEXT NOT NULL,
        telefono TEXT,
        detalle TEXT,
        creado_en TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS ultimo_pedido_cliente (
        telefono TEXT PRIMARY KEY,
        resumen TEXT NOT NULL,
        creado_en TEXT NOT NULL
    )""")
    # 06-ago-2026: una fila por cada alerta que SALIO hacia un jefe, con el acuse que
    # Meta manda despues (ver _registrar_aviso_jefe). Sin esto no habia forma de saber
    # si los avisos del sistema llegaban a alguien: alertas_fallidas solo cuenta el
    # fracaso TOTAL (los tres numeros fallando a la vez).
    con.execute("""CREATE TABLE IF NOT EXISTS avisos_jefes (
        msg_id TEXT PRIMARY KEY,
        telefono TEXT NOT NULL,
        extracto TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        estado TEXT NOT NULL DEFAULT 'enviado',   -- enviado|entregado|leido|fallido
        estado_en TEXT,
        detalle TEXT DEFAULT ''
    )""")
    # 06-ago-2026: nombre de perfil de WhatsApp por telefono. Vive ACA (volumen
    # persistente de Railway) y no en la cache de clientes del POS a proposito: no es un
    # cliente registrado por el local, es lo que la persona puso en su propio WhatsApp.
    # Sirve para que la comanda de un cliente nuevo lleve un nombre en vez de
    # NOMBRE_COMANDA_FALLBACK, sin preguntarle nada a nadie.
    con.execute("""CREATE TABLE IF NOT EXISTS perfil_whatsapp (
        telefono TEXT PRIMARY KEY,
        nombre TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    )""")
    # 10-ago-2026 (debounce, ver PLAN_DEBOUNCE_MENSAJES.md): los mensajes que estan
    # ESPERANDO a que el cliente termine de escribir. Va en SQLite y no en memoria a
    # proposito: con un buffer en RAM, un reinicio de Railway (deploy, crash, escalado)
    # se come los mensajes que estaban esperando SIN DEJAR RASTRO -- el cliente escribio
    # y no le contesta nadie, nunca. Es exactamente lo que el proyecto ya decidio no
    # permitir ("un pedido nunca puede quedar esperando invisible para siempre",
    # leccion del caso Matias). Al arrancar el proceso se vacia (ver vaciar_buffer_al_arrancar).
    #
    # 'telefono' es el NORMALIZADO (9 digitos) porque es la clave con que se agrupa y con
    # la que se toma el lock. 'telefono_meta' es el que mando Meta, y es a donde hay que
    # contestar: mandarle la respuesta al normalizado seria mandarla a un numero que no
    # existe.
    con.execute("""CREATE TABLE IF NOT EXISTS buffer_entrante (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telefono TEXT NOT NULL,
        telefono_meta TEXT NOT NULL,
        texto TEXT NOT NULL,
        llegado_en TEXT NOT NULL
    )""")
    # 10-ago-2026 (pedido de Gonza): CUANTO fragmenta cada cliente. Es la medicion que
    # falta para el "ajuste por cliente" de PLAN_DEBOUNCE_MENSAJES.md -- subirle la
    # espera al que escribe de a pedacitos y bajarsela al que manda todo junto. Ese
    # ajuste se decide con datos REALES en dos o tres semanas, no adivinando hoy: por
    # eso esto solo MIDE, no cambia ninguna espera todavia.
    # Es un acumulador y no un log: una fila por telefono que no crece con el tiempo
    # (un log de cada rafaga engordaria para siempre y no responde mejor la pregunta).
    # 'rafagas' cuenta TODAS las descargas del buffer, incluidas las de un solo mensaje:
    # sin ese denominador no se sabe si el cliente fragmenta siempre o fragmento una vez.
    con.execute("""CREATE TABLE IF NOT EXISTS fragmentacion_cliente (
        telefono TEXT PRIMARY KEY,
        rafagas INTEGER NOT NULL DEFAULT 0,
        mensajes INTEGER NOT NULL DEFAULT 0,
        rafagas_fragmentadas INTEGER NOT NULL DEFAULT 0,
        max_rafaga INTEGER NOT NULL DEFAULT 0,
        primera_vez TEXT,
        ultima_vez TEXT
    )""")
    # En que parte de la conversacion esta cada cliente. Hoy la unica etapa que se anota
    # es 'confirmacion' (salio la ficha del Paso 1 y se espera un si/no), y sirve para
    # acortar la espera del debounce: hacer esperar 8 s por un "si" se siente roto.
    con.execute("""CREATE TABLE IF NOT EXISTS etapa_cliente (
        telefono TEXT PRIMARY KEY,
        etapa TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    )""")
    # 03-ago-2026 (pregunta 1 de PREGUNTAS_CHRISTIAN_20260803.md, Gonza dijo 'si'):
    # cuenta cuantas veces una direccion NO calzo con la planilla de delivery, agrupada
    # por CALLE (sin el numero de casa: cada pedido trae un numero distinto, pero lo
    # que Gonza necesita saber es que calle le falta agregar). Ver _registrar_calle_sin_planilla.
    # 04-ago-2026 (P29): que pedidos programados ya recibieron su recordatorio/escalon,
    # para no repetirlo en cada vuelta del poll (mismo criterio que 'avisado' de
    # calles_sin_planilla).
    con.execute("""CREATE TABLE IF NOT EXISTS recordatorios_programados (
        pedido_id INTEGER PRIMARY KEY,
        recordado_en TEXT,
        escalado_en TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS calles_sin_planilla (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        calle_clave TEXT NOT NULL UNIQUE,
        calle_ejemplo TEXT NOT NULL,
        conteo INTEGER DEFAULT 1,
        primera_vez TEXT NOT NULL,
        ultima_vez TEXT NOT NULL,
        avisado INTEGER DEFAULT 0
    )""")
    # Migracion suave (30-jul-2026, escalon al jefe de respaldo): las dos tablas de
    # espera ganan 'escalado'/'escalado_en'. En produccion la base ya existe con filas
    # vivas, asi que la columna se agrega sobre la marcha en vez de recrear la tabla
    # (mismo criterio que el caja_id de pos/nucleo.py). Las filas viejas quedan en
    # escalado=0, o sea entran por el camino normal: escalan y despues vencen.
    for _tabla in ("esperando_cobro", "esperando_comprobante"):
        _cols = [r[1] for r in con.execute(f"PRAGMA table_info({_tabla})").fetchall()]
        if "escalado" not in _cols:
            con.execute(f"ALTER TABLE {_tabla} ADD COLUMN escalado INTEGER DEFAULT 0")
        if "escalado_en" not in _cols:
            con.execute(f"ALTER TABLE {_tabla} ADD COLUMN escalado_en TEXT")
    # 29-sep-2026 (monitor de la ventana pico, pedido de Gonza): una fila por cada
    # problema ya avisado por ntfy, para no machacar el celular con el mismo problema
    # cada vez que pasa el poll mientras nadie lo resuelve -- ver _avisar_si_nuevo.
    con.execute("""CREATE TABLE IF NOT EXISTS monitor_avisos (
        clave TEXT PRIMARY KEY,
        avisado_en TEXT NOT NULL
    )""")
    con.commit()
    return con


# Cuantas veces tiene que repetirse la MISMA calle sin match antes de avisar. En 1
# ya se sabe que la planilla no la cubre, pero podria ser una direccion rara de una
# sola vez; en 3 ya vale la pena que Gonza la agregue.
CORTE_AVISO_CALLE_SIN_PLANILLA = 3


def _registrar_calle_sin_planilla(direccion, db_path=None):
    """Suma un fallo de la calle de `direccion` contra la planilla (delivery_zonas).
    Agrupa por CALLE, no por direccion exacta (el numero de casa varia en cada
    pedido). Al llegar a CORTE_AVISO_CALLE_SIN_PLANILLA deja un log [planilla][AVISO]
    UNA sola vez por calle (no en cada pedido siguiente) -- Gonza revisa los logs de
    Railway, el bot no tiene un canal de WhatsApp propio hacia el."""
    from delivery_zonas import _norm as _norm_calle
    clave = re.sub(r"\d+", "", _norm_calle(direccion)).strip()
    if not clave:
        return
    con = _con_respaldo(db_path)
    try:
        ahora = datetime.now().isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO calles_sin_planilla (calle_clave, calle_ejemplo, conteo, "
            "primera_vez, ultima_vez) VALUES (?,?,1,?,?) "
            "ON CONFLICT(calle_clave) DO UPDATE SET conteo=conteo+1, ultima_vez=excluded.ultima_vez",
            (clave, direccion, ahora, ahora))
        con.commit()
        fila = con.execute(
            "SELECT conteo, calle_ejemplo, avisado FROM calles_sin_planilla WHERE calle_clave=?",
            (clave,)).fetchone()
        if fila and fila[0] >= CORTE_AVISO_CALLE_SIN_PLANILLA and not fila[2]:
            print(f"[planilla][AVISO] '{fila[1]}' no calzo con la tabla de tarifas "
                  f"{fila[0]} veces -- conviene agregarla a data/tarifas_delivery.json.")
            con.execute("UPDATE calles_sin_planilla SET avisado=1 WHERE calle_clave=?", (clave,))
            con.commit()
    finally:
        con.close()


def listar_calles_sin_planilla(db_path=None):
    """Para inspeccionar a mano que calles le faltan a la planilla (no tiene pantalla
    propia todavia)."""
    con = _con_respaldo(db_path)
    try:
        filas = con.execute(
            "SELECT calle_ejemplo, conteo, primera_vez, ultima_vez FROM calles_sin_planilla "
            "ORDER BY conteo DESC").fetchall()
        return [{"calle": f[0], "conteo": f[1], "primera_vez": f[2], "ultima_vez": f[3]}
                for f in filas]
    finally:
        con.close()


def _guardar_ultimo_pedido(telefono, resumen, db_path=None):
    """Recuerda el ULTIMO pedido despachado de este telefono, por
    MINUTOS_MEMORIA_PEDIDO_RECIENTE (27-jul-2026, S2.5 punto 3)."""
    con = _con_respaldo(db_path)
    try:
        con.execute(
            "INSERT INTO ultimo_pedido_cliente (telefono, resumen, creado_en) VALUES (?,?,?) "
            "ON CONFLICT(telefono) DO UPDATE SET resumen=excluded.resumen, "
            "creado_en=excluded.creado_en",
            (_norm_telefono(telefono), resumen, datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()


def _resumen_de_pedido(pedido):
    """La FIRMA de texto de un pedido: '2x Churrasco Italiano · retiro · tarjeta'.

    Una sola funcion para los dos usos (guardar el ultimo despachado y compararlo con el
    que llega ahora, ver _pedido_despachado_hace_poco). Si se escribieran por separado,
    el candado de duplicados dejaria de calzar el dia que alguien cambie el formato de
    uno de los dos, y nadie se enteraria hasta que salga un pedido doble a cocina."""
    items_txt = ", ".join(f"{i.get('cantidad', 1)}x {i.get('producto', '')}"
                          for i in (pedido.get("items") or []))
    return (f"{items_txt or 'pedido'} · {pedido.get('modalidad', '-')} · "
            f"{pedido.get('forma_pago', '-')}")


def _ultimo_pedido_despachado(telefono, db_path=None):
    """(resumen, minutos_de_antiguedad) del ultimo pedido despachado de este telefono, o
    None si nunca hubo uno. SIN ventana de tiempo: cada llamador aplica la suya (la nota
    interna al modelo y el candado de duplicados usan constantes distintas a proposito).
    La fila la escribe _guardar_ultimo_pedido al despachar."""
    con = _con_respaldo(db_path)
    try:
        fila = con.execute(
            "SELECT resumen, creado_en FROM ultimo_pedido_cliente WHERE telefono=?",
            (_norm_telefono(telefono),)).fetchone()
    finally:
        con.close()
    if not fila:
        return None
    resumen, creado_en = fila
    try:
        edad_min = (datetime.now() - datetime.fromisoformat(creado_en)).total_seconds() / 60
    except ValueError:
        return None
    return resumen, edad_min


def _pedido_despachado_hace_poco(telefono, db_path=None):
    """(resumen, minutos) si este telefono YA tiene un pedido en cocina de hace menos de
    MINUTOS_BLOQUEO_PEDIDO_DUPLICADO; None si no. Es la mitad de lectura del candado de
    pedido duplicado (05-ago-2026, pendiente #9): no guarda nada nuevo -- se apoya en la
    misma tabla que ya llenaba la memoria de pedido reciente."""
    reciente = _ultimo_pedido_despachado(telefono, db_path=db_path)
    if not reciente or reciente[1] > MINUTOS_BLOQUEO_PEDIDO_DUPLICADO:
        return None
    return reciente


def _comentarios_pedido_reciente(telefono, conv_db=None, respaldo_db_path=None):
    """La linea '•Comentarios:' de la ultima ficha que vio este cliente, si tiene un pedido
    despachado hace menos de MINUTOS_MEMORIA_PEDIDO_RECIENTE (caso 162). None si no."""
    reciente = _ultimo_pedido_despachado(telefono, db_path=respaldo_db_path)
    if not reciente or reciente[1] > MINUTOS_MEMORIA_PEDIDO_RECIENTE:
        return None
    desde = (datetime.now() - timedelta(minutes=MINUTOS_MEMORIA_PEDIDO_RECIENTE)
             ).isoformat(timespec="seconds")
    con = _con_conv() if conv_db is None else _con_conv_en(conv_db)
    try:
        fila = con.execute(
            "SELECT texto FROM turnos WHERE telefono=? AND rol='asistente' AND creado_en>=? "
            "AND texto LIKE ? ORDER BY id DESC LIMIT 1",
            (telefono, desde, f"%{prototipo.MARCA_COMENTARIOS_FICHA}%")).fetchone()
    finally:
        con.close()
    if not fila:
        return None
    return next((l for l in fila[0].splitlines()
                 if l.startswith(prototipo.MARCA_COMENTARIOS_FICHA)), None)


def _nota_pedido_reciente(telefono, db_path=None):
    """Si este telefono tiene un pedido despachado hace menos de
    MINUTOS_MEMORIA_PEDIDO_RECIENTE, arma una nota INTERNA (el cliente no la ve) para que
    el bot no lo trate como conversacion nueva sin memoria ni abra un pedido aparte por
    error. None si no hay nada reciente (cliente nuevo o fuera de la ventana)."""
    reciente = _ultimo_pedido_despachado(telefono, db_path=db_path)
    if not reciente:
        return None
    resumen, edad_min = reciente
    if edad_min > MINUTOS_MEMORIA_PEDIDO_RECIENTE:
        return None
    return (
        "AVISO INTERNO (no se lo repitas literal al cliente, es solo contexto tuyo): este "
        f"cliente ya hizo un pedido hace {int(edad_min)} min: {resumen}. Si en este mensaje "
        "esta agregando o cambiando algo de ESE pedido, NO lo cierres como un pedido nuevo -- "
        "probablemente ya esta en preparacion: sigue la regla de 'cambiar un pedido ya en "
        "preparacion' (deriva al humano, dale el numero del local). Si en cambio es claramente "
        "un pedido distinto y aparte, tratalo normal."
    )


def _guardar_nombre_perfil(telefono, nombre, db_path=None):
    """Recuerda el nombre de perfil de WhatsApp de este telefono. Devuelve el nombre ya
    limpio, o None si lo que venia no servia como nombre (ver _limpiar_nombre_perfil).

    Solo escribe cuando el nombre CAMBIO: esto se llama en cada mensaje entrante y el
    nombre de perfil casi nunca cambia -- no vale la pena un INSERT por cada 'hola'."""
    limpio = _limpiar_nombre_perfil(nombre)
    if not limpio:
        return None
    tel = _norm_telefono(telefono)
    con = _con_respaldo(db_path)
    try:
        fila = con.execute("SELECT nombre FROM perfil_whatsapp WHERE telefono=?",
                           (tel,)).fetchone()
        if fila and fila[0] == limpio:
            return limpio
        con.execute(
            "INSERT INTO perfil_whatsapp (telefono, nombre, actualizado_en) VALUES (?,?,?) "
            "ON CONFLICT(telefono) DO UPDATE SET nombre=excluded.nombre, "
            "actualizado_en=excluded.actualizado_en",
            (tel, limpio, datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()
    return limpio


def _nombre_perfil(telefono, db_path=None):
    """El nombre de perfil de WhatsApp guardado de este telefono, o None."""
    con = _con_respaldo(db_path)
    try:
        fila = con.execute("SELECT nombre FROM perfil_whatsapp WHERE telefono=?",
                           (_norm_telefono(telefono),)).fetchone()
    finally:
        con.close()
    return fila[0] if fila else None


def _nota_cliente_conocido(telefono):
    """Si este telefono ya es 'cliente registrado' (esta en CLIENTES_CACHE, la cache
    local de clientes del POS -- ver _refrescar_clientes_pos), arma una nota interna
    para que el bot no repregunte el nombre de cero. Fail-safe: None si el telefono no
    esta en la cache (cliente nuevo, o el POS/tunel todavia no alcanzo a sincronizar) --
    nunca inventa un "registrado" sin dato real.

    04-ago-2026 (diseno acordado con Gonza, 'nombre registrado'): ANTES esto consultaba
    el POS EN VIVO por HTTP (timeout corto de 2s, una vez por conversacion nueva);
    ahora lee la cache local que un hilo de fondo mantiene al dia (mismo patron que el
    menu, S4) -- ni siquiera ese timeout corto queda en el camino de la primera
    respuesta al cliente, y es mas liviano para el tunel/Railway."""
    tel9 = _norm_telefono(telefono)
    if not prototipo.CLIENTES_CACHE.exists():
        return None
    try:
        cache = json.loads(prototipo.CLIENTES_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None
    c = cache.get(tel9)
    if not c or not c.get("nombre"):
        return None
    partes = [f"nombre registrado '{c['nombre']}'"]
    if c.get("direccion"):
        partes.append(f"direccion habitual '{c['direccion']}'")
    return (
        "AVISO INTERNO (no se lo repitas literal, usalo para no repreguntar): este telefono "
        "ya es cliente conocido -- " + ", ".join(partes) + f". En la ficha, DEBAJO de la "
        f"linea de direccion, agrega '•Nombre: {c['nombre']}' (no la preguntes de cero). Si el "
        "pedido de ahora es para esa misma persona/direccion, confirmaselo corto en vez de "
        "preguntar todo de cero (ej. '¿el de siempre, a tu direccion de siempre?'). Si dice "
        "que es para otra persona o direccion distinta, o te da un nombre DISTINTO, usa lo "
        "que el diga -- eso manda."
    )


def _datos_cliente_registrado(telefono):
    """Nombre/direccion guardados del telefono, o None. Misma fuente que
    _nota_cliente_conocido (CLIENTES_CACHE); separada para poder re-inyectarlos en
    cada turno sin repetir la nota larga de bienvenida."""
    if not prototipo.CLIENTES_CACHE.exists():
        return None
    try:
        cache = json.loads(prototipo.CLIENTES_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None
    c = cache.get(_norm_telefono(telefono))
    return c if (c and c.get("nombre")) else None


def _nota_datos_fijos_cliente(telefono):
    """Datos del cliente registrado, re-inyectados en CADA turno (05-ago-2026).

    Bug real que arregla (pendiente #8, Gonza probando como cliente): el bot ya sabia
    su nombre y a mitad del pedido se lo volvio a preguntar. Causa: _nota_cliente_conocido
    se agrega UNA sola vez, al abrir la conversacion, y de ahi en adelante el dato
    sobrevive solo si el modelo se acuerda de arrastrarlo -- o sea la ficha del cliente
    dependia de la memoria del LLM, no del sistema. Esto lo fija por codigo: la nota
    vuelve en cada turno, corta y al final (lo ultimo que lee es lo que mejor obedece,
    misma leccion que cierre_pedido.md).

    Es gratis: lee la cache LOCAL de clientes que ya mantiene al dia un hilo de fondo
    (_refrescar_clientes_pos) -- ni una llamada de red en el camino de la respuesta."""
    c = _datos_cliente_registrado(telefono)
    if not c:
        return None
    partes = [f"nombre '{c['nombre']}'"]
    if c.get("direccion"):
        partes.append(f"direccion habitual '{c['direccion']}'")
    return (
        "DATOS FIJOS DE ESTE CLIENTE (los pone el sistema del local, no los deduzcas ni los "
        "vuelvas a preguntar): " + ", ".join(partes) + ". Si en ESTA conversacion el cliente "
        "te dio un nombre o una direccion distintos, mandan los que el dijo."
    )


def _guardar_alerta_fallida(texto, db_path=None):
    """Persiste un aviso de sistema que NO llego a NINGUN jefe por WhatsApp (27-jul-
    2026, S2 del plan). enviar_whatsapp ya reintenta ante fallas transitorias; si
    aun asi falla (ventana de 24h cerrada, numero dado de baja, Meta caido), el
    aviso no puede desaparecer en silencio -- queda visible en el panel del POS,
    que no depende de WhatsApp para mostrarlo."""
    con = _con_respaldo(db_path)
    try:
        con.execute(
            "INSERT INTO alertas_fallidas (texto, creado_en) VALUES (?,?)",
            (texto, datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()


def contar_alertas_fallidas(db_path=None):
    con = _con_respaldo(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM alertas_fallidas WHERE resuelto=0").fetchone()[0]
    finally:
        con.close()


def listar_alertas_fallidas(db_path=None):
    con = _con_respaldo(db_path)
    try:
        filas = con.execute(
            "SELECT id, texto, creado_en FROM alertas_fallidas WHERE resuelto=0 "
            "ORDER BY id").fetchall()
        return [{"id": fid, "texto": texto, "creado_en": creado}
                for fid, texto, creado in filas]
    finally:
        con.close()


def descartar_alerta_fallida(fid, db_path=None):
    con = _con_respaldo(db_path)
    try:
        cur = con.execute(
            "UPDATE alertas_fallidas SET resuelto=1 WHERE id=? AND resuelto=0", (fid,))
        con.commit()
        return {"ok": bool(cur.rowcount)}
    finally:
        con.close()


def _registrar_caso_humano(motivo, telefono, texto, db_path=None):
    """Deja un rastro DURABLE de que el bot derivo algo a una persona, sin importar
    si el WhatsApp de aviso llego o no (ver el comentario de la tabla en
    _con_respaldo). Se llama SIEMPRE junto a _avisar_jefes en los puntos que hoy no
    tienen ninguna otra pantalla que los muestre (pendientes y rechazados ya tienen
    la suya -- duplicarlos ahi solo confundiria)."""
    con = _con_respaldo(db_path)
    try:
        con.execute(
            "INSERT INTO casos_humanos (motivo, telefono, texto, creado_en) VALUES (?,?,?,?)",
            (motivo, telefono, texto, datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()


def _registrar_candado(candado, telefono, detalle="", db_path=None):
    """Anota que un candado de codigo actuo en este turno. FALLA HACIA ABIERTO: si el
    registro revienta (disco lleno, base bloqueada) el turno sigue igual -- un log de
    observabilidad jamas puede dejar a un cliente sin respuesta."""
    try:
        con = _con_respaldo(db_path)
        try:
            con.execute(
                "INSERT INTO eventos_candado (candado, telefono, detalle, creado_en) "
                "VALUES (?,?,?,?)",
                (candado, telefono, (detalle or "")[:300],
                 datetime.now().isoformat(timespec="seconds")))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        print(f"[eventos_candado][AVISO] no pude anotar {candado}: {e}")


def resumen_candados(dias=7, db_path=None, conv_db=None):
    """Cuantas veces actuo cada candado en los ultimos `dias`, y cuantos turnos reales
    de clientes hubo en el mismo periodo (el denominador de 'candados cada 100 turnos')."""
    desde = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    con = _con_respaldo(db_path)
    try:
        por_candado = dict(con.execute(
            "SELECT candado, COUNT(*) FROM eventos_candado WHERE creado_en>=? "
            "GROUP BY candado ORDER BY 2 DESC", (desde,)).fetchall())
    finally:
        con.close()
    con = _con_conv_en(conv_db) if conv_db else _con_conv()
    try:
        turnos = con.execute(
            "SELECT COUNT(*) FROM turnos WHERE rol='usuario' AND creado_en>=?",
            (desde,)).fetchone()[0]
    finally:
        con.close()
    total = sum(por_candado.values())
    return {"dias": dias, "turnos_de_clientes": turnos, "candados_total": total,
            "candados_cada_100_turnos": round(100 * total / turnos, 1) if turnos else None,
            "por_candado": por_candado}


def contar_casos_humanos(db_path=None):
    con = _con_respaldo(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM casos_humanos WHERE resuelto=0").fetchone()[0]
    finally:
        con.close()


def _hay_caso_humano_abierto(telefono, motivo, db_path=None):
    """True si ya hay un caso_humano de este motivo, sin resolver, para este telefono.

    Anti-spam del candado derivar_humano (28-sep-2026): sin esto, si el cliente insiste
    y el modelo vuelve a marcar derivar_humano en cada turno sin prometer una persona en
    el texto, cada turno manda un WhatsApp nuevo a los jefes. El resto de los candados
    de este tipo (pedido_grande, humano_prometido) evitan el bucle mirando el TEXTO que
    el cliente YA recibio (`previos`) -- pero el aviso de este candado es interno, solo
    a los jefes, y no deja marca en lo que el cliente lee. Por eso se apoya en el estado
    del propio caso_humano en vez de en el texto."""
    con = _con_respaldo(db_path)
    try:
        return con.execute(
            "SELECT 1 FROM casos_humanos WHERE telefono=? AND motivo=? AND resuelto=0 "
            "LIMIT 1", (telefono, motivo)).fetchone() is not None
    finally:
        con.close()


def listar_casos_humanos(db_path=None):
    con = _con_respaldo(db_path)
    try:
        filas = con.execute(
            "SELECT id, motivo, telefono, texto, creado_en FROM casos_humanos "
            "WHERE resuelto=0 ORDER BY id").fetchall()
        return [{"id": fid, "motivo": motivo, "telefono": telefono, "texto": texto,
                 "creado_en": creado}
                for fid, motivo, telefono, texto, creado in filas]
    finally:
        con.close()


def cerrar_caso_humano(fid, usuario="", db_path=None):
    con = _con_respaldo(db_path)
    try:
        cur = con.execute(
            "UPDATE casos_humanos SET resuelto=1, resuelto_en=?, resuelto_por=? "
            "WHERE id=? AND resuelto=0",
            (datetime.now().isoformat(timespec="seconds"), usuario, fid))
        con.commit()
        return {"ok": bool(cur.rowcount)}
    finally:
        con.close()


# ===========================================================================
# ACUSE DE RECIBO DE LAS ALERTAS A LOS JEFES (06-ago-2026)
# ---------------------------------------------------------------------------
# Todos los candados que escalan terminan en _avisar_jefes, o sea en un WhatsApp a
# tres numeros. Hasta hoy NADIE sabia si esos avisos llegaban a un ser humano: un
# 200 de Meta significa "aceptado para entrega", no "entregado" y mucho menos
# "leido". La unica senal de fracaso que se guardaba era la TOTAL (los tres numeros
# fallando a la vez, ver _guardar_alerta_fallida); un aviso entregado a un telefono
# que nadie mira era indistinguible de uno atendido.
#
# Y el dato ya estaba llegando gratis: por cada mensaje SALIENTE, Meta reenvia al
# mismo webhook un `statuses[]` con sent -> delivered -> read (o failed, con el
# codigo de error). Como _ids_del_payload solo recorre `messages`, esos payloads no
# matcheaban nada y se descartaban en silencio -- exactamente el mismo desperdicio
# que el nombre de perfil de WhatsApp antes del 06-ago (ver _nombres_de_perfil).
#
# Con esto, "¿alguien lee las alertas?" deja de ser una pregunta a una persona (y de
# depender de que conteste) y pasa a ser un numero en /salud.
# ===========================================================================
DIAS_RETENCION_AVISOS_JEFES = 30
_ESTADO_META = {"sent": "enviado", "delivered": "entregado", "read": "leido",
                "failed": "fallido"}
# Un estado solo puede AVANZAR: Meta no garantiza el orden de llegada y un
# 'delivered' atrasado no puede pisar un 'read' que ya entro.
_RANGO_ESTADO_AVISO = {"enviado": 0, "entregado": 1, "leido": 2}
# Un aviso que despues de esto sigue sin UN SOLO acuse no es "no leido": es NO
# MEDIDO (el webhook podria no estar suscrito al campo en Meta). La distincion es
# el corazon de esta medicion -- sin ella, "0 leidos" se lee como "nadie lo mira"
# cuando puede ser "nadie lo cuenta".
HORAS_PARA_DAR_ACUSE_POR_PERDIDO = 2


def _msg_id_de_meta(respuesta):
    """El wamid del mensaje que Meta acaba de aceptar, o None (modo simulado, error,
    o el espia de --test). Es la unica llave que despues permite cruzar el acuse."""
    if not isinstance(respuesta, dict):
        return None
    mensajes = respuesta.get("messages")
    if not isinstance(mensajes, list) or not mensajes:
        return None
    primero = mensajes[0]
    return (primero.get("id") or None) if isinstance(primero, dict) else None


def _registrar_aviso_jefe(msg_id, telefono, texto, db_path=None):
    """Anota que salio una alerta hacia un jefe, para poder cruzarla con su acuse.
    Guarda un extracto, no el texto entero: es un aviso interno del sistema.

    28-sep-2026: el corte sube de 80 a MAX_VARIABLE_PLANTILLA (300) porque ahora
    este extracto tambien alimenta el reintento por plantilla cuando un texto libre
    fallo (ver _reintentar_avisos_fallidos) -- 80 caracteres alcanzaban para LEER el
    numero, pero dejaban el reintento con un aviso cortado a la mitad."""
    if not msg_id:
        return
    con = _con_respaldo(db_path)
    try:
        ahora = datetime.now().isoformat(timespec="seconds")
        con.execute(
            "INSERT OR IGNORE INTO avisos_jefes (msg_id, telefono, extracto, creado_en, "
            "estado, estado_en) VALUES (?,?,?,?,'enviado',?)",
            (msg_id, telefono, (texto or "")[:MAX_VARIABLE_PLANTILLA], ahora, ahora))
        corte = (datetime.now() - timedelta(days=DIAS_RETENCION_AVISOS_JEFES)
                 ).isoformat(timespec="seconds")
        con.execute("DELETE FROM avisos_jefes WHERE creado_en < ?", (corte,))
        con.commit()
    finally:
        con.close()


# Los acuses llegan por CADA mensaje saliente, o sea sobre todo por los mensajes a
# CLIENTES, que son la enorme mayoria. Filtrarlos por destinatario aca evita abrir la
# base una vez por cada uno para descubrir que no nos interesa.
_NUMS_JEFE_NORM = frozenset(_norm_telefono(n) for n in NUMS_ALERTAS_JEFE)
# 10-ago-2026 (pendiente #7): /salud tiene que poder decir A QUIEN le falla la entrega,
# pero es un endpoint ABIERTO -- no lleva el candado de token que si llevan /pendientes y
# /calles-sin-planilla. Asi que se publica una ETIQUETA, nunca el celular de nadie. Con la
# etiqueta alcanza para lo unico que hay que contestar: si jefe1/jefe2 reciben o no.
# 13-sep-2026: la etiqueta pasa a ser el mismo ROLES_JEFE de arriba (jefe1/jefe2/otro) en
# vez de un dict propio con nombres de persona -- son el mismo dato, no hace falta
# mantener dos.


def _etiqueta_destinatario(telefono):
    return _rol_de(telefono) or "otro"


def _statuses_del_payload(payload):
    """Los acuses que Meta manda por cada mensaje SALIENTE. Llegan al mismo webhook
    que los mensajes entrantes, en `value.statuses` en vez de `value.messages`.

    Se devuelven SOLO los de mensajes a jefes: el rastro de entrega de lo que se le
    manda a un cliente no se sigue ni se guarda. Si el acuse viniera sin
    `recipient_id` se deja pasar y decide la base -- mejor una consulta de mas que
    perder el acuse que justamente se quiere medir."""
    salida = []
    for entry in (payload or {}).get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            for st in (change.get("value") or {}).get("statuses", []) or []:
                mid, estado = st.get("id"), _ESTADO_META.get(st.get("status"))
                if not mid or not estado:
                    continue
                dest = _norm_telefono(st.get("recipient_id") or "")
                if dest and dest not in _NUMS_JEFE_NORM:
                    continue
                detalle = ""
                errores = st.get("errors") or []
                if errores and isinstance(errores[0], dict):
                    detalle = f"{errores[0].get('code', '')} {errores[0].get('title', '')}".strip()
                salida.append({"msg_id": mid, "estado": estado, "detalle": detalle})
    return salida


def registrar_statuses(statuses, cfg=None, db_path=None):
    """Aplica los acuses a las alertas de jefe que se estan siguiendo.

    Los acuses de los mensajes a CLIENTES caen aca tambien y no hacen nada: el
    UPDATE solo toca filas de avisos_jefes, que existen unicamente para los jefes
    (no se guarda el rastro de entrega de las conversaciones de los clientes).

    Solo AVANZA de estado. 'fallido' manda siempre y es terminal: si Meta rechazo
    el mensaje, saberlo importa mas que el orden en que llegaron los acuses.

    28-sep-2026: medido en /salud -- 14 de 23 avisos que le tocaban a los DOS jefes
    fallaron para los DOS en 30 dias. La causa: Meta acepta el texto libre con un
    200 y recien ACA, por este webhook, avisa que en realidad reboto (131047,
    ventana cerrada) -- para ese momento _avisar_jefes ya termino y su fallback
    sincrono a la plantilla nunca llega a dispararse (mismo patron que ya describia
    el comentario de enviar_plantilla_whatsapp del 18-ago sobre el respaldo). Si
    `cfg` viene informado se reintenta ACA, apenas se sabe del rechazo -- ver
    _reintentar_avisos_fallidos. Sin `cfg` (todos los tests salvo el que prueba
    esto) el comportamiento es identico al de antes: solo actualiza estados."""
    if not statuses:
        return 0
    con = _con_respaldo(db_path)
    tocadas = 0
    a_reintentar = []
    try:
        ahora = datetime.now().isoformat(timespec="seconds")
        for st in statuses:
            fila = con.execute(
                "SELECT estado, telefono, extracto FROM avisos_jefes WHERE msg_id=?",
                (st["msg_id"],)).fetchone()
            if not fila:
                continue                      # acuse de un mensaje a un cliente
            actual, telefono, extracto = fila
            nuevo = st["estado"]
            if actual == "fallido":
                continue
            if nuevo != "fallido" and (_RANGO_ESTADO_AVISO.get(nuevo, -1)
                                       <= _RANGO_ESTADO_AVISO.get(actual, -1)):
                continue                      # atrasado o repetido: no retrocede
            detalle = st.get("detalle", "")
            con.execute(
                "UPDATE avisos_jefes SET estado=?, estado_en=?, detalle=? WHERE msg_id=?",
                (nuevo, ahora, detalle, st["msg_id"]))
            tocadas += 1
            if nuevo == "fallido" and "131047" in detalle:
                a_reintentar.append({"telefono": telefono, "extracto": extracto})
        con.commit()
    finally:
        con.close()
    if a_reintentar and cfg is not None:
        _reintentar_avisos_fallidos(a_reintentar, cfg, db_path=db_path)
    return tocadas


def _reintentar_avisos_fallidos(fallidos, cfg, db_path=None):
    """Segundo intento, por PLANTILLA, para un aviso de texto libre que Meta acepto
    y recien despues rechazo por 131047 (ver registrar_statuses). Es el mismo
    fallback sincrono que ya tiene _avisar_jefes para el rechazo INMEDIATO, aplicado
    ahora al rechazo TARDIO, que es el que de verdad ocurre en produccion.

    Solo se llama con codigo 131047 a proposito: es el UNICO rechazo que una
    plantilla no puede sufrir (no depende de la ventana de 24h), asi que reintentar
    con la MISMA plantilla ante cualquier otro codigo (plantilla no aprobada,
    numero invalido) fallaria igual y no aporta nada."""
    for f in fallidos:
        variables = [_var_plantilla("un aviso del local"), _var_plantilla(f["extracto"])]
        r = enviar_plantilla_whatsapp(f["telefono"], PLANTILLA_RESPALDO, variables, cfg)
        if "error" not in r:
            _registrar_aviso_jefe(_msg_id_de_meta(r), f["telefono"], f["extracto"],
                                  db_path=db_path)


def resumen_avisos_jefes(dias=7, db_path=None):
    """El numero que responde '¿alguien lee las alertas del bot?'.

    'sin_acuse' es la salvaguarda honesta: si TODOS los avisos viejos siguen en
    'enviado', lo que falta no es lectura sino la suscripcion al campo de estados
    en Meta -- o sea el 0 de 'leidos' no significa nada todavia."""
    con = _con_respaldo(db_path)
    try:
        ahora = datetime.now()
        desde = (ahora - timedelta(days=dias)).isoformat(timespec="seconds")
        por_estado = {e: n for e, n in con.execute(
            "SELECT estado, COUNT(*) FROM avisos_jefes WHERE creado_en >= ? GROUP BY estado",
            (desde,)).fetchall()}
        corte = (ahora - timedelta(hours=HORAS_PARA_DAR_ACUSE_POR_PERDIDO)
                 ).isoformat(timespec="seconds")
        sin_acuse = con.execute(
            "SELECT COUNT(*) FROM avisos_jefes WHERE creado_en >= ? AND creado_en < ? "
            "AND estado='enviado'", (desde, corte)).fetchone()[0]
        ultimo = con.execute(
            "SELECT estado_en FROM avisos_jefes WHERE estado='leido' AND creado_en >= ? "
            "ORDER BY estado_en DESC LIMIT 1", (desde,)).fetchone()
        # 10-ago-2026 (pendiente #7): el codigo de error de Meta se venia GUARDANDO en
        # 'detalle' y no se publicaba en ningun lado, asi que "5 fallidos" no decia POR
        # QUE fallaron. La hipotesis en pie es la ventana de 24 h -- codigo 131047, Meta
        # no entrega mensajes libres a un numero que no le escribio al bot en 24 h -- y
        # sin el codigo no habia forma ni de confirmarla ni de matarla.
        fallas = [{"destinatario": _etiqueta_destinatario(tel),
                   "codigo": ((det or "").split(" ", 1)[0] or "(sin codigo)"),
                   "detalle": (det or "(sin detalle)"),
                   "veces": n}
                  for tel, det, n in con.execute(
                      "SELECT telefono, detalle, COUNT(*) FROM avisos_jefes "
                      "WHERE creado_en >= ? AND estado='fallido' "
                      "GROUP BY telefono, detalle ORDER BY COUNT(*) DESC",
                      (desde,)).fetchall()]
        # Y el corte POR DESTINATARIO, que los totales no pueden dar: si el respaldo
        # nunca recibio nada, el segundo escalon de la salvaguarda anti-limbo
        # (ESPERA_BACKUP_MINUTOS, nacido del caso Matias) nunca funciono en la vida real.
        por_dest = {}
        for tel, estado, n in con.execute(
                "SELECT telefono, estado, COUNT(*) FROM avisos_jefes WHERE creado_en >= ? "
                "GROUP BY telefono, estado", (desde,)).fetchall():
            fila = por_dest.setdefault(
                _etiqueta_destinatario(tel),
                {"enviados": 0, "entregados": 0, "leidos": 0, "fallidos": 0})
            fila["enviados"] += n
            if estado in ("entregado", "leido"):
                fila["entregados"] += n
            if estado == "leido":
                fila["leidos"] += n
            if estado == "fallido":
                fila["fallidos"] += n
        return {"dias": dias,
                "enviados": sum(por_estado.values()),
                # un 'leido' tambien fue entregado: se cuenta en los dos
                "entregados": por_estado.get("entregado", 0) + por_estado.get("leido", 0),
                "leidos": por_estado.get("leido", 0),
                "fallidos": por_estado.get("fallido", 0),
                "sin_acuse": sin_acuse,
                "ultimo_leido_en": ultimo[0] if ultimo else None,
                "fallas": fallas,
                "por_destinatario": por_dest}
    finally:
        con.close()


def listar_avisos_jefes(dias=7, db_path=None):
    """Detalle fila por fila de los avisos a jefes -- resumen_avisos_jefes ya da el
    agregado (enviados/entregados/leidos), esto es para poder VERLOS uno por uno
    (18-sep-2026, pedido de Gonza: el Curador mostraba casos_humanos -derivaciones-
    pero no el resto de los avisos de rutina que tambien le llegan al jefe por
    WhatsApp). Publica la ETIQUETA del destinatario (jefe1/jefe2/otro), nunca su
    celular -- mismo criterio que /salud (ver _etiqueta_destinatario). El extracto ya
    viene truncado a 80 caracteres desde que se guarda (_registrar_aviso_jefe)."""
    con = _con_respaldo(db_path)
    try:
        desde = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
        filas = con.execute(
            "SELECT telefono, extracto, creado_en, estado, estado_en, detalle "
            "FROM avisos_jefes WHERE creado_en >= ? ORDER BY creado_en DESC",
            (desde,)).fetchall()
        return [{"destinatario": _etiqueta_destinatario(tel), "extracto": extracto,
                 "creado_en": creado, "estado": estado, "estado_en": estado_en,
                 "detalle": detalle}
                for tel, extracto, creado, estado, estado_en, detalle in filas]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# VENTANA DE 24 H, MIRADA ANTES DE MANDAR (25-sep-2026, caso Martina Rivas)
# ---------------------------------------------------------------------------
# El plan B de plantilla de _avisar_jefes (13-sep) casi nunca se activaba: Meta ACEPTA
# el texto libre con un 200 y recien lo rechaza despues, por el webhook de estados
# (131047). Para ese momento el envio ya se dio por bueno. Caso real 24-sep 12:50:
# "💳 CONFIRMAR TRANSFERENCIA" no le llego a NINGUNO de los dos jefes; el pedido se
# salvo porque el respaldo (que ya salia por plantilla) si llego 5 minutos despues.
#
# Ahora se decide ANTES: si el jefe le escribio al bot en las ultimas
# HORAS_VENTANA_WHATSAPP horas, su ventana esta abierta y va texto libre (llega
# completo). Si no, va derecho la plantilla aprobada, que no depende de la ventana.
# 23 y no 24: una hora de margen para no mandar texto libre justo cuando cierra.
HORAS_VENTANA_WHATSAPP = 23
# Solo para --test: los bloques escritos antes de este cambio miden el CONTENIDO de los
# avisos espiando enviar_whatsapp, o sea suponen la ventana abierta. Se fija asi para
# ellos, y el bloque del 25-sep lo vuelve a None para medir la decision de verdad. En
# produccion queda siempre en None.
_VENTANA_FORZADA_TEST = None


def _ventana_whatsapp_abierta(telefono, conv_db=None):
    """True si este numero le escribio al bot dentro de la ventana de WhatsApp."""
    if _VENTANA_FORZADA_TEST is not None:
        return _VENTANA_FORZADA_TEST
    corte = (datetime.now() - timedelta(hours=HORAS_VENTANA_WHATSAPP)).isoformat(timespec="seconds")
    con = _con_conv() if conv_db is None else _con_conv_en(conv_db)
    try:
        filas = con.execute("SELECT DISTINCT telefono FROM turnos WHERE rol='usuario' "
                            "AND creado_en >= ?", (corte,)).fetchall()
    except sqlite3.Error:
        return False   # sin poder mirar, lo seguro es la plantilla: llega siempre
    finally:
        con.close()
    objetivo = _norm_telefono(telefono)
    return any(_norm_telefono(f[0]) == objetivo for f in filas)


def _avisar_jefes(texto, cfg, db_path=None, conv_db=None):
    """Manda una alerta del sistema a TODOS los numeros con rol de jefe. Si NINGUNO
    la recibio (ni siquiera tras los reintentos de enviar_whatsapp NI la plantilla
    de respaldo) el aviso no puede quedar en la nada: se guarda para que el panel
    del POS lo muestre -- (27-jul-2026, S2 del plan: no depende de la ventana de
    24h de WhatsApp).

    06-ago-2026: cada envio aceptado queda anotado con su wamid para poder cruzarlo
    despues con el acuse de Meta (ver el bloque de arriba).

    13-sep-2026 (medido en /salud: 35 de 45 avisos fallidos en 7 dias, los dos
    jefes, todos codigo 131047): el texto libre SOLO le llega a un jefe que le
    escribio al bot en las ultimas 24h -- si no, Meta lo rechaza y antes quedaba
    perdido en silencio salvo que fallaran LOS DOS (recien ahi entraba a
    _guardar_alerta_fallida). Ahora, igual que ya hacia _avisar_backup desde el
    18-ago, cada numero que falla por texto libre cae a la misma plantilla
    aprobada antes de darlo por perdido."""
    algun_exito = False
    variables = [_var_plantilla("un aviso del local"), _var_plantilla(texto)]
    for num in NUMS_ALERTAS_JEFE:
        if _ventana_whatsapp_abierta(num, conv_db):
            r = enviar_whatsapp(num, texto, cfg)
            if "error" in r:
                r = enviar_plantilla_whatsapp(num, PLANTILLA_RESPALDO, variables, cfg)
        else:
            # Ventana cerrada: el texto libre seria rechazado. Plantilla primero; el
            # texto queda de respaldo por si la plantilla no sale (renombrada, etc.).
            r = enviar_plantilla_whatsapp(num, PLANTILLA_RESPALDO, variables, cfg)
            if "error" in r:
                print(f"[avisos] la plantilla no salio para {num}, voy con texto libre: "
                      f"{str(r)[:160]}")
                r = enviar_whatsapp(num, texto, cfg)
        if "error" not in r:
            algun_exito = True
            _registrar_aviso_jefe(_msg_id_de_meta(r), num, texto, db_path=db_path)
    if not algun_exito:
        _guardar_alerta_fallida(texto, db_path=db_path)


def _bloqueado_por_abuso(telefono, cfg, db_path=None):
    """True si este numero ya paso LIMITE_MENSAJES_ANTIABUSO turnos en la ventana
    movil de VENTANA_ANTIABUSO_MINUTOS (candado #34). Avisa a los jefes UNA sola vez
    por racha -- justo cuando el conteo ANTES de este mensaje toca el limite exacto
    (el mensaje que hace cruzar la raya), no en cada mensaje bloqueado despues. Mismo
    criterio anti-spam que el pedido duplicado y el 'humano prometido': no hace falta
    guardar un estado aparte, el propio conteo de `turnos` ya es monotonico mientras
    el numero siga escribiendo (cada mensaje bloqueado igual se registra como
    'usuario', ver procesar_mensaje)."""
    previos = _mensajes_recientes(telefono, db_path=db_path)
    if previos < LIMITE_MENSAJES_ANTIABUSO:
        return False
    if previos == LIMITE_MENSAJES_ANTIABUSO:
        _avisar_jefes(
            f"🚫 +{telefono} paso los {LIMITE_MENSAJES_ANTIABUSO} mensajes en "
            f"{VENTANA_ANTIABUSO_MINUTOS} min. El bot dejo de contestarle -- no esta "
            "baneado, se destraba solo si deja de escribir.", cfg)
    return True


def _avisar_backup(texto, cfg, db_path=None, motivo=None, detalle=None):
    """Escribe al JEFE DE RESPALDO. Se usa SOLO cuando una espera ya vencio sin
    respuesta del jefe: es el ultimo intento humano antes de que el bot avance solo.
    Si tampoco a el se le pudo entregar, el aviso queda en el panel del POS (igual
    que _avisar_jefes) -- una alerta de emergencia no puede perderse en silencio.

    18-ago-2026 -- SALE POR PLANTILLA, no por texto libre. El texto libre a este numero
    no llegaba NUNCA (3 de 3 rechazados en 7 dias, ver enviar_plantilla_whatsapp), y como
    Meta acepta el POST con 200 igual, el bot lo daba por entregado y nadie se enteraba.
    El texto libre queda como respaldo del respaldo: cubre mientras Meta no apruebe la
    plantilla, y el dia que este numero SI tenga la ventana abierta llega mas completo.

    `motivo` y `detalle` son las dos variables de la plantilla. Si no vienen se derivan
    del texto: vale mas un aviso generico que llega, que uno perfecto que rebota."""
    variables = [_var_plantilla(motivo or "un aviso del local"),
                 _var_plantilla(detalle or texto)]
    r = enviar_plantilla_whatsapp(JEFE_BACKUP, PLANTILLA_RESPALDO, variables, cfg)
    if "error" in r:
        # La plantilla no salio (sin aprobar todavia, renombrada, idioma cambiado): se
        # cae al camino de siempre para no quedar PEOR que antes del arreglo.
        print(f"[respaldo] la plantilla no salio, voy con texto libre: {str(r)[:160]}")
        r = enviar_whatsapp(JEFE_BACKUP, texto, cfg)
    if "error" in r:
        _guardar_alerta_fallida(texto, db_path=db_path)
    else:
        _registrar_aviso_jefe(_msg_id_de_meta(r), JEFE_BACKUP, texto, db_path=db_path)


# ===========================================================================
# CANDADO: si el bot prometio una persona, esa persona tiene que existir
# (06-ago-2026, encontrado por bot/banco_pruebas.py)
# ---------------------------------------------------------------------------
# La regla 3.9 del prompt le dice al modelo que derive a un humano en reclamos,
# anulaciones, cambios de un pedido en preparacion, catering y pedidos grandes:
# "di que en un momento le responde alguien del local y marca derivar_humano".
#
# El problema medido: NADIE se enteraba. Con derivar_humano=true el pedido no se
# despacha (bien) pero tampoco dispara ningun aviso -- el unico mensaje que salia
# del turno era el del cliente leyendo "en un momento te responde alguien del
# local". Y si el modelo lo decia conversando, sin tool-call, todavia menos.
# Verificado con cerebro falso (deterministico): un reclamo por pedido frio
# terminaba con 0 mensajes a los jefes.
#
# Es la MISMA forma que la red de seguridad ciega del 15-jul: una regla del prompt
# que se adelanta a un candado de codigo lo desactiva sin tocarlo. Por eso este
# candado NO mira la tool: mira lo que el cliente LEYO. Si el bot prometio una
# persona, se avisa -- haya emitido tool o no.
_RE_HUMANO_PERSONA = re.compile(
    r"\b(alguien|una\s+persona|un[a]?\s+encargad[oa])\b", re.IGNORECASE)
# Las formas verbales van por raiz a proposito: la primera version listaba "atiende" y
# "paso con", y el modelo escribio "te paso A alguien del local para que te ATIENDA" --
# el candado no disparo y el aviso se perdio igual. Medido con el banco, 06-ago.
_RE_HUMANO_CONTACTO = re.compile(
    r"\b(?:respond[ae]n?|atien[dt][ae]n?|contact[ae]n?|escrib[ae]n?|tom[ae]n?|"
    r"llam[ae]n?|paso\s+(?:con|a)|comunico\s+con|deriv[oa])\b", re.IGNORECASE)


def _promete_humano(texto):
    """True si el texto le promete al cliente que una PERSONA lo va a contactar.
    Exige las dos piezas en la misma oracion, para no confundirse con frases que
    solo nombran al local ('el local te avisa apenas este listo', regla 3.8, que no
    promete a nadie escribiendo) ni con 'llamalo directo al local', que es lo
    contrario: ahi el que contacta es el cliente."""
    for oracion in re.split(r"[.\n!?]+", texto or ""):
        if _RE_HUMANO_PERSONA.search(oracion) and _RE_HUMANO_CONTACTO.search(oracion):
            return True
    return False


def _ya_prometio_humano(previos):
    """Una sola alerta por conversacion (mismo criterio que el pedido grande): si en
    un turno anterior el bot ya prometio una persona, los jefes ya lo saben y cada
    mensaje nuevo del cliente no puede volver a sonarles el telefono."""
    return any(t.get("rol") == "asistente" and _promete_humano(t.get("texto") or "")
               for t in previos)


def _avisar_humano_prometido(telefono, texto_cliente, visible, cfg, db_path=None):
    texto_aviso = (
        "🙋 EL BOT PROMETIÓ UNA PERSONA — hay que contestarle a mano:\n\n"
        f"Cliente: +{telefono}\n"
        f"Lo que escribió: {(texto_cliente or '')[:300]}\n\n"
        f"Lo que le contestó el bot:\n{(visible or '')[:400]}")
    _avisar_jefes(texto_aviso, cfg, db_path=db_path)
    # 15-sep-2026: caso real (adulto mayor que no podia pagar por Banco de Chile) en
    # que este WhatsApp se dio por "aceptado" y nadie lo leyo -- ver el comentario de
    # la tabla casos_humanos. Este rastro NO depende de WhatsApp.
    _registrar_caso_humano("humano_prometido", telefono, texto_aviso, db_path=db_path)
    _registrar_candado("humano_prometido", telefono, db_path=db_path)
    print(f"[candado_humano_prometido] {telefono}: el bot prometio una persona; "
          "avisados los jefes.")


def _resumen_pedido_para_jefe(payload):
    items = ", ".join(f"{i.get('cantidad', 1)}x {i.get('producto', '')}"
                      for i in payload.get("items", []))
    return (f"Cliente: {payload.get('cliente', '-')}\n"
            f"Modalidad: {payload.get('modalidad', '-')}\n"
            f"Items: {items or '-'}\n"
            f"Tel: {payload.get('telefono', '-')}")


def _guardar_pendiente(payload, telefono, cfg, db_path=None):
    """Guarda un pedido que NO llego al POS (cola de reintento durable) y avisa al
    JEFE por WhatsApp de inmediato -- nunca se pierde en silencio como antes."""
    con = _con_respaldo(db_path)
    try:
        con.execute(
            "INSERT INTO pendientes (payload_json, telefono, creado_en) VALUES (?,?,?)",
            (json.dumps(payload, ensure_ascii=False), telefono,
             datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()
    resumen = _resumen_pedido_para_jefe(payload)
    _avisar_jefes(
        f"⚠️ No pude conectar con el POS. Guardé este pedido y voy a reintentar solo "
        f"cada {REINTENTO_SEGUNDOS}s:\n\n{resumen}\n\n"
        "Si no se refleja en tu cola en unos minutos, revisa que el POS esté abierto.",
        cfg)


def reintentar_pendientes(cfg, db_path=None):
    """Reintenta entregar al POS los pedidos de la cola de respaldo. La llama el hilo
    en background (cada REINTENTO_SEGUNDOS) y, de oportunidad, endpoint_recibir en
    cada mensaje real. Devuelve cuantos se entregaron en esta pasada.

    15-jul-2026 (bug real: un pedido quedo 'reintentando solo' para siempre con el
    POS corriendo al lado): un error HTTP 4xx significa que el POS esta VIVO pero
    RECHAZO el pedido (payload invalido) -- reintentarlo jamas va a funcionar. Esos
    se marcan 'rechazados' (entregado=2), se avisa al jefe con el motivo exacto, y
    salen del ciclo. Solo los errores de CONEXION (POS apagado) se reintentan por
    siempre, que es lo correcto."""
    import urllib.error
    url = cfg.get("pos_api_url") or "http://127.0.0.1:8000"
    con = _con_respaldo(db_path)
    try:
        filas = con.execute(
            "SELECT id, payload_json FROM pendientes WHERE entregado=0 ORDER BY id").fetchall()
        entregados = 0
        for fid, payload_json in filas:
            payload = json.loads(payload_json)
            try:
                r = _post_al_cajero(payload, url)
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    detalle = ""
                    try:
                        detalle = e.read().decode("utf-8", errors="replace")[:400]
                    except Exception:
                        pass
                    con.execute("UPDATE pendientes SET entregado=2, intentos=intentos+1 WHERE id=?",
                                (fid,))
                    con.commit()
                    print(f"[respaldo][RECHAZADO] el POS rechazo el pedido pendiente #{fid} "
                          f"(HTTP {e.code}): {detalle}")
                    _avisar_jefes(
                        f"❌ El POS rechazó un pedido de WhatsApp (no es problema de conexión; "
                        f"reintentar no sirve). Hay que ingresarlo A MANO en el POS:\n\n"
                        f"{_resumen_pedido_para_jefe(payload)}\n\n"
                        f"Motivo técnico: HTTP {e.code} — {detalle[:200]}",
                        cfg)
                else:
                    # 5xx: error interno del POS, puede ser transitorio -> se sigue intentando.
                    con.execute("UPDATE pendientes SET intentos=intentos+1 WHERE id=?", (fid,))
                    con.commit()
                continue
            except Exception:
                con.execute("UPDATE pendientes SET intentos=intentos+1 WHERE id=?", (fid,))
                con.commit()
                continue
            con.execute(
                "UPDATE pendientes SET entregado=1, entregado_en=?, pedido_id=? WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"), r.get("id"), fid))
            con.commit()
            entregados += 1
            _avisar_jefes(
                f"✅ El pedido que había quedado pendiente ya llegó a tu cola (#{r.get('id')}).",
                cfg)
        return entregados
    finally:
        con.close()


def contar_pendientes(db_path=None):
    con = _con_respaldo(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM pendientes WHERE entregado=0").fetchone()[0]
    finally:
        con.close()


def contar_rechazados(db_path=None):
    """Pedidos que el POS rechazo (payload invalido): requieren ingreso manual.
    Los DESCARTADOS por un humano (entregado=3) ya no cuentan: el aviso se resolvio."""
    con = _con_respaldo(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM pendientes WHERE entregado=2").fetchone()[0]
    finally:
        con.close()


def listar_rechazados(db_path=None):
    """Los pedidos rechazados CON su payload completo (21-jul-2026).

    Antes el POS solo sabia 'hay N rechazados' y mandaba a Christian a releer el chat
    de WhatsApp para reconstruir el pedido a mano. El detalle ya estaba guardado aca:
    ahora se entrega, para que el POS pueda PRECARGAR la pantalla de armar pedido."""
    con = _con_respaldo(db_path)
    try:
        filas = con.execute(
            "SELECT id, payload_json, telefono, creado_en, intentos FROM pendientes "
            "WHERE entregado=2 ORDER BY id").fetchall()
        out = []
        for fid, payload_json, tel, creado, intentos in filas:
            try:
                payload = json.loads(payload_json)
            except Exception:
                payload = {}
            out.append({"id": fid, "telefono": tel, "creado_en": creado,
                        "intentos": intentos, "payload": payload})
        return out
    finally:
        con.close()


def descartar_rechazado(fid, motivo="", usuario="", db_path=None):
    """Marca un rechazado como resuelto por un humano (entregado=3) para que el aviso
    deje de aparecer. Guarda motivo y quien lo hizo: descartar NO puede ser un agujero
    silencioso. Devuelve si efectivamente habia algo que descartar."""
    con = _con_respaldo(db_path)
    try:
        cur = con.execute(
            "UPDATE pendientes SET entregado=3, entregado_en=? WHERE id=? AND entregado=2",
            (datetime.now().isoformat(timespec="seconds"), fid))
        con.commit()
        if cur.rowcount:
            print(f"[respaldo][DESCARTADO] pedido #{fid} descartado por "
                  f"{usuario or 'alguien'} en el POS. Motivo: {motivo or '(sin motivo)'}")
        return {"ok": bool(cur.rowcount), "descartados": cur.rowcount}
    finally:
        con.close()


def listar_pendientes(db_path=None):
    """Los pedidos que NO llegaron al POS, CON su payload completo (29-jul-2026).

    Hasta hoy el POS solo recibia un NUMERO ("hay N pendientes, se reintentan solos")
    y no habia forma de verlos, rearmarlos ni cerrarlos: un aviso muerto. Los
    RECHAZADOS ya tenian este trato desde el 21-jul; los pendientes -- que son
    justamente los del corte de tunel -- se habian quedado sin el."""
    con = _con_respaldo(db_path)
    try:
        filas = con.execute(
            "SELECT id, payload_json, telefono, creado_en, intentos FROM pendientes "
            "WHERE entregado=0 ORDER BY id").fetchall()
        out = []
        for fid, payload_json, tel, creado, intentos in filas:
            try:
                payload = json.loads(payload_json)
            except Exception:
                payload = {}
            out.append({"id": fid, "telefono": tel, "creado_en": creado,
                        "intentos": intentos, "payload": payload})
        return out
    finally:
        con.close()


def telefono_de_pendiente(fid, db_path=None):
    """El telefono de un pendiente, para poder traer SU conversacion. Cae al del
    payload si la columna quedo vacia (pendientes viejos, antes de que se guardara)."""
    con = _con_respaldo(db_path)
    try:
        fila = con.execute(
            "SELECT telefono, payload_json FROM pendientes WHERE id=?", (fid,)).fetchone()
    finally:
        con.close()
    if not fila:
        return None
    tel = (fila[0] or "").strip()
    if tel:
        return tel
    try:
        return (json.loads(fila[1]) or {}).get("telefono") or None
    except Exception:
        return None


def cerrar_pendiente(fid, motivo="", usuario="", db_path=None):
    """Saca un PENDIENTE de la vista, ya sea porque un humano lo ingreso a mano en el
    POS o porque decidio no tomarlo. Mismo criterio que descartar_rechazado: queda
    entregado=3 con motivo y autor -- un aviso que se apaga sin rastro es un agujero.

    Ojo con la condicion `entregado=0`: si mientras tanto el reintento automatico
    logro entregarlo (entregado=1), esta llamada NO hace nada y devuelve ok=False.
    Es a proposito -- evita marcar como 'resuelto a mano' un pedido que en realidad
    entro solo, y le permite a la pantalla avisar que ya estaba en la cola."""
    con = _con_respaldo(db_path)
    try:
        cur = con.execute(
            "UPDATE pendientes SET entregado=3, entregado_en=? WHERE id=? AND entregado=0",
            (datetime.now().isoformat(timespec="seconds"), fid))
        con.commit()
        if cur.rowcount:
            print(f"[respaldo][CERRADO] pendiente #{fid} cerrado por "
                  f"{usuario or 'alguien'} en el POS. Motivo: {motivo or '(sin motivo)'}")
        return {"ok": bool(cur.rowcount), "cerrados": cur.rowcount}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Cobro de envio por direccion (15-jul-2026, pedido de Christian): el POS guarda
# el historial de direcciones por telefono con su delivery_cobro. Si la direccion
# del pedido calza con una guardada, se usa ese cobro. Si no, el pedido se RETIENE
# esperando que un jefe responda 'envio <monto>' (con timeout anti-limbo).
# ---------------------------------------------------------------------------
def _norm_dir(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


def _match_cobro(direccion_dada, direcciones_guardadas):
    """Busca la direccion dada entre las guardadas del cliente. Match: mismo numero de
    casa + al menos una palabra de calle (>=3 letras) en comun. Devuelve el
    delivery_cobro (>0) de la mejor coincidencia, o None."""
    dada = _norm_dir(direccion_dada)
    if not dada:
        return None
    nums_dada = set(re.findall(r"\d+", dada))
    toks_dada = {t for t in re.findall(r"[a-z]+", dada) if len(t) >= 3}
    for d in direcciones_guardadas or []:
        guardada = _norm_dir(d.get("direccion"))
        cobro = d.get("delivery_cobro") or 0
        if cobro <= 0:
            continue
        nums_g = set(re.findall(r"\d+", guardada))
        toks_g = {t for t in re.findall(r"[a-z]+", guardada) if len(t) >= 3}
        if nums_dada and nums_dada & nums_g and toks_dada & toks_g:
            return int(cobro)
    return None


def _buscar_cobro_direccion(telefono, direccion, cfg):
    """Consulta al POS las direcciones guardadas del cliente y matchea. None si el POS
    no responde, el cliente es nuevo, o la direccion no calza (fail-safe)."""
    import urllib.request
    tel9 = _norm_telefono(telefono)
    url = (cfg.get("pos_api_url") or "http://127.0.0.1:8000").rstrip("/") + \
          f"/api/clientes/{tel9}/direcciones"
    try:
        peticion = urllib.request.Request(url, headers=_headers_pos())
        with urllib.request.urlopen(peticion, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        return _match_cobro(direccion, data.get("direcciones"))
    except Exception:
        return None


def _guardar_esperando_cobro(payload, telefono, db_path=None):
    con = _con_respaldo(db_path)
    try:
        con.execute("INSERT INTO esperando_cobro (telefono, payload_json, direccion, creado_en) "
                    "VALUES (?,?,?,?)",
                    (telefono, json.dumps(payload, ensure_ascii=False),
                     payload.get("direccion"), datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()


def contar_esperando_cobro(db_path=None):
    con = _con_respaldo(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM esperando_cobro WHERE resuelto=0").fetchone()[0]
    finally:
        con.close()


# S2.5 punto 9 (27-jul-2026): formaliza un aviso que hoy el jefe manda a mano por su
# WhatsApp personal. 'en camino <telefono>' / 'afuera <telefono>' -- el telefono es el
# que ya recibe en cada aviso de pedido (_resumen_pedido_para_jefe imprime 'Tel: ...'),
# asi que copiarlo y pegarlo es el flujo natural. No cruza contra ningun pedido real
# (no hace falta: es solo un mensaje de cortesia al cliente correcto que el jefe eligio).
RE_EN_CAMINO_JEFE = re.compile(r"^(en camino|afuera)\s+(\d[\d\s]{7,})$", re.IGNORECASE)


def resolver_en_camino_jefe(estado, telefono_cliente, cfg):
    """Manda al CLIENTE el aviso de 'va en camino' / 'el repartidor esta afuera'."""
    tel9 = _norm_telefono(telefono_cliente)
    if len(tel9) != 9:
        return (f"No reconozco ese número ({telefono_cliente}). Mándame los 9 dígitos, "
                "ej: 'en camino 912345678'.")
    numero_completo = "56" + tel9
    en_camino = estado.lower() == "en camino"
    texto_cliente = ("🛵 ¡Tu pedido va en camino!" if en_camino else
                     "🛵 ¡El repartidor está afuera!")
    enviar_al_cliente(numero_completo, texto_cliente, cfg)
    return (f"Listo, le avisé a +{numero_completo} que "
            f"{'va en camino' if en_camino else 'el repartidor está afuera'} 🙌")


RE_ENVIO_JEFE = re.compile(r"env[ií]o\s*\$?\s*([\d.]+)", re.IGNORECASE)

# --- 'no llegamos': la OTRA respuesta posible al cobro de envio (03-ago-2026, P9) ---
# Christian: ante una direccion que no esta en la base, el jefe puede responder el monto
# "o tambien puede responder que no llegamos a la direccion en consulta". Hasta hoy el
# unico camino era 'envio <monto>': si no llegaban, no habia como decirlo y el pedido
# terminaba entrando a cocina por vencimiento -- se cocinaba para una direccion que
# nadie iba a cubrir. Tolerante a variantes y faltas de ortografia (pedido de Gonza):
# frase corta, sin numero, imposible de confundir con un monto.
RE_NO_LLEGAMOS_JEFE = re.compile(
    r"\bno\s+(?:le\s+)?(?:llegamos|llego|llega|llegan|yegamos|yego)\b|"
    r"\bfuera\s+de\s+(?:zona|rango|cobertura)\b|\bno\s+(?:hay\s+)?cobertura\b|"
    r"\bno\s+cubrimos\b|\bno\s+despachamos\b")
# Tope de palabras para que la frase cuente como COMANDO y no como conversacion. Sin
# esto, un jefe escribiendo "el pedido de las 3 no llega hace rato, llamalo" cancelaria
# un pedido real: 'no llega' aparece igual. Un comando de verdad es corto.
MAX_PALABRAS_NO_LLEGAMOS = 8


def _es_no_llegamos(texto):
    """True si el jefe esta respondiendo 'no llegamos a esa direccion' (P9). Tolerante a
    redaccion y a faltas de ortografia (pedido de Gonza), pero solo en mensajes CORTOS:
    la misma frase dentro de una conversacion larga no es una orden de cancelar."""
    t = re.sub(r"[^0-9a-záéíóúüñ ]+", " ", (texto or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    if not t or len(t.split()) > MAX_PALABRAS_NO_LLEGAMOS:
        return False
    return bool(RE_NO_LLEGAMOS_JEFE.search(t))

# Lo que lee el CLIENTE cuando el jefe dice que no llegan. Dictado literal por Christian
# en P9 (30-jul-2026). No se le ofrece retiro en el mismo mensaje a proposito: el pedido
# queda vivo en la conversacion y el cliente decide -- ofrecer retiro automatico seria
# inventar una decision comercial que nadie tomo.
MENSAJE_NO_LLEGAMOS = "Lo lamento, pero de momento no llegamos a tu zona."


def resolver_no_llegamos_jefe(cfg, db_path=None):
    """Un jefe respondio que no llegan a esa direccion: el pedido retenido MAS ANTIGUO
    se cierra SIN despacharse (no entra a cocina, no se cobra) y el cliente recibe la
    frase que dicto Christian. Devuelve el texto de confirmacion para el jefe.

    Mismo riesgo preexistente que 'envio <monto>': con dos pedidos esperando, la
    respuesta se aplica al mas antiguo. Se resuelve el dia que las esperas dejen de ser
    una cola FIFO ciega; no se agrava aca."""
    con = _con_respaldo(db_path)
    try:
        fila = con.execute("SELECT id, telefono, payload_json FROM esperando_cobro "
                           "WHERE resuelto=0 ORDER BY id LIMIT 1").fetchone()
        if not fila:
            return "No hay pedidos esperando cobro de envío ahora."
        eid, tel_cli, payload_json = fila
        payload = json.loads(payload_json)
        con.execute("UPDATE esperando_cobro SET resuelto=1 WHERE id=?", (eid,))
        con.commit()
    finally:
        con.close()
    enviar_al_cliente(tel_cli, MENSAJE_NO_LLEGAMOS, cfg)
    print(f"[no_llegamos] pedido de {payload.get('cliente')} "
          f"({payload.get('direccion')}) cerrado sin despachar.")
    return (f"Listo, le avisé a {payload.get('cliente') or 'el cliente'} "
            f"({payload.get('direccion')}) que no llegamos a su zona. "
            "El pedido NO entró a cocina.")


def resolver_cobro_jefe(monto, cfg, nucleo=None, db_path=None):
    """Un jefe respondio 'envio <monto>': se lo aplica al pedido MAS ANTIGUO que espera
    cobro, lo crea en el POS y avisa al cliente. Devuelve un texto de confirmacion."""
    con = _con_respaldo(db_path)
    try:
        fila = con.execute("SELECT id, telefono, payload_json FROM esperando_cobro "
                           "WHERE resuelto=0 ORDER BY id LIMIT 1").fetchone()
        if not fila:
            return "No hay pedidos esperando cobro de envío ahora."
        eid, tel_cli, payload_json = fila
        payload = json.loads(payload_json)
        payload["delivery_cobro"] = int(monto)
        # Con el envio ya resuelto, recien AHORA corresponde la puerta de transferencia:
        # es el momento en que el cliente puede saber cuanto transferir (29-jul-2026).
        r = _entregar_o_retener(payload, tel_cli, cfg, nucleo=nucleo)
        con.execute("UPDATE esperando_cobro SET resuelto=1 WHERE id=?", (eid,))
        con.commit()
    finally:
        con.close()
    if isinstance(r, dict) and r.get("esperando_comprobante"):
        enviar_al_cliente(tel_cli, r["aviso_cliente"], cfg)
        return (f"✅ Envío {_pesos(monto)} aplicado al pedido de {payload.get('cliente')} "
                f"({payload.get('direccion')}) — le pedí la transferencia por "
                f"{_pesos(_total_payload(payload) or 0)}. Responde 'confirmado' cuando "
                "veas el pantallazo.")
    # El total va PEGADO al aviso del envio, no en un mensaje aparte: es el numero que el
    # cliente estaba esperando desde que se le dijo "en un momento te confirmo el envio".
    aviso_total = r.get("aviso_cliente") if isinstance(r, dict) else None
    enviar_al_cliente(tel_cli,
                    f"🛵 ¡Listo! El envío a tu dirección es {_pesos(monto)}"
                    ". Tu pedido ya está en preparación. Te avisamos apenas salga 🙌" +
                    (f"\n\n{aviso_total}" if aviso_total else ""), cfg)
    pid = r.get("id") if isinstance(r, dict) else None
    return (f"✅ Envío {_pesos(monto)}"
            f" aplicado al pedido de {payload.get('cliente')} ({payload.get('direccion')})" +
            (f" — creado #{pid} en tu cola." if pid else " — quedó en la cola de reintento."))


def escalar_esperas_al_backup(cfg, db_path=None):
    """PRIMER escalon del anti-limbo (30-jul-2026): a los ESPERA_COBRO_MINUTOS sin
    respuesta del jefe, el pedido NO avanza todavia -- se le pasa al JEFE DE RESPALDO
    y se le dan ESPERA_BACKUP_MINUTOS mas. Recien si tampoco el responde entra a
    cocina sin confirmar (resolver_esperas_vencidas).

    El UPDATE va ANTES del aviso a proposito: si el WhatsApp al backup falla, la
    espera igual queda marcada y vence 5 min despues por el camino de siempre. Al
    reves quedaria escalando en cada vuelta del hilo, avisando para siempre."""
    con = _con_respaldo(db_path)
    try:
        limite = (datetime.now() - timedelta(minutes=ESPERA_COBRO_MINUTOS)
                  ).isoformat(timespec="seconds")
        filas = con.execute("SELECT id, telefono, payload_json FROM esperando_cobro "
                            "WHERE resuelto=0 AND escalado=0 AND creado_en < ?",
                            (limite,)).fetchall()
        for eid, _tel_cli, payload_json in filas:
            payload = json.loads(payload_json)
            con.execute("UPDATE esperando_cobro SET escalado=1, escalado_en=? WHERE id=?",
                        (datetime.now().isoformat(timespec="seconds"), eid))
            con.commit()
            _avisar_backup(
                f"🆘 RESPALDO — nadie respondió el cobro de envío en "
                f"{ESPERA_COBRO_MINUTOS} min. Responde 'envío <monto>' para despacharlo:\n\n"
                f"{_resumen_pedido_para_jefe(payload)}\n\n"
                f"(Si en {ESPERA_BACKUP_MINUTOS} min tampoco hay respuesta, el pedido entra "
                "a cocina con ENVÍO POR DEFINIR.)", cfg, db_path=db_path,
                motivo=f"el cobro del envío en {ESPERA_COBRO_MINUTOS} min",
                detalle=f"{_resumen_pedido_para_jefe(payload)} · responde "
                        "'envío <monto>'")
            _avisar_jefes(
                f"⏰ Pasaron {ESPERA_COBRO_MINUTOS} min sin respuesta del cobro de envío de "
                f"{payload.get('cliente')}: se lo pasé al jefe de respaldo. Todavía puedes "
                "responder tú 'envío <monto>'.", cfg, db_path=db_path)
        return len(filas)
    finally:
        con.close()


def resolver_esperas_vencidas(cfg, nucleo=None, db_path=None):
    """SEGUNDO escalon del anti-limbo: esperas de cobro que YA se escalaron al backup
    (escalar_esperas_al_backup) y llevan ESPERA_BACKUP_MINUTOS mas sin respuesta entran
    IGUAL a cocina con 'ENVIO POR DEFINIR' y se avisa a cliente y jefes. Una espera sin
    escalar nunca llega aca: el pedido no avanza sin que un humano haya tenido su
    turno (el del local primero, el de respaldo despues)."""
    con = _con_respaldo(db_path)
    try:
        limite = (datetime.now() - timedelta(minutes=ESPERA_BACKUP_MINUTOS)
                  ).isoformat(timespec="seconds")
        filas = con.execute("SELECT id, telefono, payload_json FROM esperando_cobro "
                            "WHERE resuelto=0 AND escalado=1 AND escalado_en < ?",
                            (limite,)).fetchall()
        for eid, tel_cli, payload_json in filas:
            payload = json.loads(payload_json)
            payload["notas"] = ((payload.get("notas") or "") + " | " if payload.get("notas")
                                else "") + "ENVIO POR DEFINIR (jefe no respondio a tiempo)"
            # A la comanda DIRECTO, sin la puerta de transferencia: aca el envio quedo sin
            # definir, o sea no hay total que pedirle al cliente. Encadenar otra espera de
            # 15 min seria dejarlo en el limbo que esta salvaguarda existe para evitar; el
            # pago se resuelve en persona, marcado en la comanda.
            _entregar_payload(payload, tel_cli, cfg, nucleo=nucleo)
            con.execute("UPDATE esperando_cobro SET resuelto=2 WHERE id=?", (eid,))
            con.commit()
            enviar_al_cliente(tel_cli, "🛵 Tu pedido ya está en preparación. El valor exacto "
                            "del envío te lo confirma el local al despachar 🙌", cfg)
            aviso_venc = (
                f"⏰ Nadie respondió el cobro de envío (ni el local en "
                f"{ESPERA_COBRO_MINUTOS} min, ni el respaldo en los "
                f"{ESPERA_BACKUP_MINUTOS} siguientes): el pedido de "
                f"{payload.get('cliente')} ({payload.get('direccion')}) entró a cocina "
                "con ENVÍO POR DEFINIR — ajústalo en el POS.")
            _avisar_jefes(aviso_venc, cfg, db_path=db_path)
            _avisar_backup(aviso_venc, cfg, db_path=db_path)
        return len(filas)
    finally:
        con.close()


def _guardar_esperando_comprobante(payload, telefono, db_path=None):
    con = _con_respaldo(db_path)
    try:
        con.execute("INSERT INTO esperando_comprobante (telefono, payload_json, creado_en) "
                    "VALUES (?,?,?)",
                    (telefono, json.dumps(payload, ensure_ascii=False),
                     datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()


def contar_esperando_comprobante(db_path=None):
    con = _con_respaldo(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM esperando_comprobante WHERE resuelto=0").fetchone()[0]
    finally:
        con.close()


# 26-sep-2026 (casos 45 y 42 del curador): dos respuestas que hasta hoy dependian solo
# del prompt (commit b9a5bc1) pasan a CODIGO, en linea con el bot 2.0: el estado del
# pedido lo maneja el codigo, el modelo solo redacta.
#  - Caso 45: con el pedido ya RETENIDO esperando comprobante, el cliente escribe
#    "lista la transferencia" y el modelo contestaba con la linea de estado en vivo.
#  - Caso 42: el cliente pregunta "cuanto sale total" cuando la ficha ya lo traia, y el
#    modelo decia que "lo muestra el sistema".
MENSAJE_TRANSFERENCIA_AVISADA = (
    "¡Gracias! 🙌 El local revisa que la transferencia haya llegado y, apenas la "
    "confirme, tu pedido pasa a cocina. Si tienes el pantallazo, mándalo por acá.")
_RE_YA_TRANSFIRIO = re.compile(
    r"\bya\s+transferi(mos)?\b"
    r"|\b(lista|listo|hecha|hecho|realizada|enviada|efectuada)\s+(la\s+)?transferencia\b"
    r"|\btransferencia\s+(ya\s+)?(esta\s+)?(lista|hecha|realizada|enviada|efectuada)\b"
    r"|\b(hice|hicimos|realice|mande|envie)\s+(la|el)\s+(transferencia|pago)\b")
_RE_NEGACION = re.compile(r"\b(no|aun no|todavia no)\b")
_RE_PIDE_TOTAL = re.compile(
    r"\bcuanto\s+(sale|es|seria|son|queda|me\s+sale)\s+(el\s+)?(en\s+)?total\b"
    r"|\bcual\s+es\s+el\s+total\b|^\s*(el\s+)?total\s*\??\s*$")
_RE_TOTAL_DICHO = re.compile(r"Total( con delivery)?\s*(\$\d[\d.]*\d)")


def _sin_tildes_min(texto):
    d = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in d if unicodedata.category(c) != "Mn")


# Frases de puro cierre/cortesia (02-oct-2026, caso 109 del curador): nada que pida o
# cambie algo del pedido, solo despedida. Lista cerrada a proposito -- cualquier cosa
# que NO calce completa con una de estas (ej. "solo eso", "si, confirmo") sigue tratandose
# como contenido real, que es lo que necesita el candado de pedido duplicado.
_CORTESIA_FRASES = {
    "gracias", "muchas gracias", "de nada", "ok", "okay", "dale", "listo",
    "perfecto", "genial", "buena", "buenisimo", "excelente", "yapo", "yap", "ya",
    "ya esta", "todo bien", "no hay problema", "no hay problemas",
    "sin problema", "sin problemas", "joya", "bacan",
    # 07-oct-2026 (caso 133): el cliente le devuelve la cortesia al bot ("gracias usted")
    "gracias usted", "gracias a usted", "gracias a ti", "gracias igual", "igualmente",
    "gracias igualmente", "chao", "chau", "adios", "hasta luego",
    # 13-oct-2026 (caso 138): a "Confirmado... ¡gracias!" el cliente contesta "A ustedes"
    "a ustedes", "a usted", "a ti", "gracias a ustedes", "a ustedes gracias",
    "igual", "gracias a uds", "a uds",
    # 15-oct-2026 (caso 170): "Oki" no calzaba y salio "ya lo tengo tomado"
    "oki", "oka", "okey", "oki doki",
}
# Acuse sin gracias (caso 170): a un "ok" no se le contesta "¡De nada, gracias a ti!"
# (Gonza, 15-oct-2026), se le contesta MENSAJE_ACUSE_DUPLICADO.
_ACUSE_FRASES = {
    "ok", "okay", "oki", "oka", "okey", "oki doki", "dale", "listo", "perfecto",
    "genial", "buena", "buenisimo", "excelente", "yapo", "yap", "ya", "ya esta",
    "todo bien", "no hay problema", "no hay problemas", "sin problema", "sin problemas",
    "joya", "bacan",
}


def _es_despedida_del_bot(texto):
    """True si el mensaje del BOT es su respuesta corta de despedida ("¡Gracias a ti!",
    "Perfecto, no hay problema. ¡Gracias a ti!"). Solo la dice ante una despedida del
    cliente (regla 3.10a), asi que sirve para saber que la conversacion ya se cerro con
    cortesia: contestar otra cortesia encima arma un bucle ("gracias" / "gracias a ti" /
    "gracias usted" / "gracias a ti", caso 133), y mandarle un seguimiento tambien es
    insistirle a quien ya se fue (caso 125). Largo maximo a proposito: un mensaje largo
    que casualmente diga "gracias a ti" no es una despedida."""
    t = _sin_tildes_min(texto or "")
    # "gracias por preferirnos": la respuesta fija a quien viene a pedir al local (caso 142).
    # "Perfecto" a secas: la respuesta a un "ok" sin gracias (caso 170).
    if t.strip(" .!") == "perfecto":
        return True
    return ("gracias a ti" in t or "gracias por preferirnos" in t) and len(t) <= 60


def _es_solo_cortesia(texto):
    """True si el mensaje es puro cierre/cortesia (gracias, dale, no hay problema...),
    sin pedir ni cambiar nada del pedido -- ver _CORTESIA_FRASES."""
    t = re.sub(r"[^\w\s,.;!]", " ", _sin_tildes_min(texto))
    partes = [p.strip() for p in re.split(r"[,.;!]+", t) if p.strip()]
    return bool(partes) and all(p in _CORTESIA_FRASES for p in partes)


def _es_solo_acuse(texto):
    """True si el mensaje es solo un acuse ("ok", "oki", "dale"), sin agradecer."""
    t = re.sub(r"[^\w\s,.;!]", " ", _sin_tildes_min(texto))
    partes = [p.strip() for p in re.split(r"[,.;!]+", t) if p.strip()]
    return bool(partes) and all(p in _ACUSE_FRASES for p in partes)


def _avisa_que_ya_transfirio(texto):
    """True si el cliente dice que YA transfirio. Si trae una negacion ("no he
    transferido") no se decide por codigo: lo ve el modelo."""
    t = _sin_tildes_min(texto)
    return bool(_RE_YA_TRANSFIRIO.search(t)) and not _RE_NEGACION.search(t)


def _tiene_comprobante_pendiente(telefono, db_path=None):
    """El pedido de este cliente esta retenido esperando que el jefe confirme el pago."""
    con = _con_respaldo(db_path)
    try:
        return con.execute("SELECT 1 FROM esperando_comprobante WHERE telefono=? "
                           "AND resuelto=0 LIMIT 1", (telefono,)).fetchone() is not None
    finally:
        con.close()


def _respuesta_total_repetido(texto, previos):
    """Si el cliente pide el total y la ULTIMA linea del bot ya lo traia, lo repite el
    codigo (nada mas). Devuelve None si no aplica: el modelo sigue con su camino."""
    if not _RE_PIDE_TOTAL.search(_sin_tildes_min(texto)):
        return None
    if not previos or previos[-1].get("rol") != "asistente":
        return None
    ultimo = previos[-1].get("texto") or ""
    m = _RE_TOTAL_DICHO.search(ultimo)
    if not m:
        return None
    linea = f"El total{m.group(1) or ''} es {m.group(2)}."
    if "quieres agregar/modificar" in ultimo:
        linea += " ¿Está bien así, o quieres agregar/modificar algo?"
    return linea


def _es_confirmacion_comprobante(texto):
    """Detecta que el jefe confirmo que llego el comprobante de transferencia. Frase
    corta y literal: asi 'confirmado' nunca se confunde con una frase larga de
    conversacion normal."""
    t = re.sub(r"[^0-9a-záéíóúüñ ]+", " ", (texto or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t in ("confirmado", "confirmada", "confirmo", "confirmó")


def resolver_comprobante_jefe(cfg, nucleo=None, db_path=None):
    """Un jefe escribio 'confirmado': despacha el pedido retenido MAS ANTIGUO que
    esperaba el comprobante de transferencia (encadena a _continuar_despacho, asi si
    ademas era delivery sin cobro conocido, sigue el flujo normal de cobro de envio)."""
    con = _con_respaldo(db_path)
    try:
        fila = con.execute("SELECT id, telefono, payload_json FROM esperando_comprobante "
                           "WHERE resuelto=0 ORDER BY id LIMIT 1").fetchone()
        if not fila:
            return "No hay pedidos esperando confirmar transferencia ahora."
        eid, tel_cli, payload_json = fila
        payload = json.loads(payload_json)
        con.execute("UPDATE esperando_comprobante SET resuelto=1 WHERE id=?", (eid,))
        con.commit()
    finally:
        con.close()
    # Directo a la comanda: el envio YA se resolvio antes de retener (ver
    # _entregar_o_retener). Volver a _continuar_despacho reharia esa busqueda y, peor,
    # volveria a pasar por la puerta de transferencia -> bucle infinito.
    payload["pago_confirmado"] = True
    r = _entregar_payload(payload, tel_cli, cfg, nucleo=nucleo)
    # 29-sep-2026 (Christian, curador): el cierre no decia la demora -- el cliente ya
    # habia transferido y quedaba sin saber cuanto esperar, justo el mismo dato que SI
    # se le da a cualquiera que paga en efectivo/tarjeta (ver confirmacion_cierre). Usa
    # el MISMO numero (minutos_demora), nunca uno aparte: si cambia la demora del dia,
    # cambia para los dos por igual.
    _minutos_cierre = prototipo.minutos_demora(payload.get("modalidad"))
    _demora_txt = f" Demora {_minutos_cierre} min aprox." if _minutos_cierre else ""
    enviar_al_cliente(tel_cli, f"✅ ¡Recibimos tu transferencia! Tu pedido ya está en "
                    f"preparación.{_demora_txt} 🙌", cfg)
    pid = r.get("id") if isinstance(r, dict) else None
    return (f"✅ Transferencia confirmada, pedido de {payload.get('cliente')} despachado" +
            (f" (#{pid})." if pid else " — quedó en cola de reintento."))


def escalar_comprobantes_al_backup(cfg, db_path=None):
    """Mismo primer escalon que escalar_esperas_al_backup, para las transferencias sin
    confirmar: a los ESPERA_COMPROBANTE_MINUTOS se le pide al JEFE DE RESPALDO que mire
    el pantallazo, con ESPERA_BACKUP_MINUTOS mas antes de que el pedido entre sin
    confirmar. El cliente ya transfirio: que nadie mire su comprobante es justo el
    momento en que hace falta un segundo par de ojos."""
    con = _con_respaldo(db_path)
    try:
        limite = (datetime.now() - timedelta(minutes=ESPERA_COMPROBANTE_MINUTOS)
                  ).isoformat(timespec="seconds")
        filas = con.execute("SELECT id, telefono, payload_json FROM esperando_comprobante "
                            "WHERE resuelto=0 AND escalado=0 AND creado_en < ?",
                            (limite,)).fetchall()
        for eid, _tel_cli, payload_json in filas:
            payload = json.loads(payload_json)
            con.execute("UPDATE esperando_comprobante SET escalado=1, escalado_en=? WHERE id=?",
                        (datetime.now().isoformat(timespec="seconds"), eid))
            con.commit()
            _avisar_backup(
                f"🆘 RESPALDO — nadie confirmó una transferencia en "
                f"{ESPERA_COMPROBANTE_MINUTOS} min. Responde 'confirmado' cuando veas el "
                f"pantallazo:\n\n{_resumen_pedido_para_jefe(payload)}\n\n"
                f"(Si en {ESPERA_BACKUP_MINUTOS} min tampoco hay respuesta, el pedido entra "
                "a cocina SIN CONFIRMAR.)", cfg, db_path=db_path,
                motivo=f"una transferencia en {ESPERA_COMPROBANTE_MINUTOS} min",
                detalle=f"{_resumen_pedido_para_jefe(payload)} · responde "
                        "'confirmado' cuando veas el pantallazo")
            _avisar_jefes(
                f"⏰ Pasaron {ESPERA_COMPROBANTE_MINUTOS} min sin confirmar la transferencia "
                f"de {payload.get('cliente')}: se la pasé al jefe de respaldo. Todavía puedes "
                "responder tú 'confirmado'.", cfg, db_path=db_path)
        return len(filas)
    finally:
        con.close()


def resolver_comprobantes_vencidos(cfg, nucleo=None, db_path=None):
    """Salvaguarda anti-limbo, SEGUNDO escalon: transferencias que ya se escalaron al
    backup y llevan ESPERA_BACKUP_MINUTOS mas sin confirmar entran IGUAL a cocina,
    marcadas para que Christian verifique el pago al preparar/entregar -- un cliente
    nunca queda esperando para siempre por un comprobante que nadie miro."""
    con = _con_respaldo(db_path)
    try:
        limite = (datetime.now() - timedelta(minutes=ESPERA_BACKUP_MINUTOS)
                  ).isoformat(timespec="seconds")
        filas = con.execute("SELECT id, telefono, payload_json FROM esperando_comprobante "
                            "WHERE resuelto=0 AND escalado=1 AND escalado_en < ?",
                            (limite,)).fetchall()
        for eid, tel_cli, payload_json in filas:
            payload = json.loads(payload_json)
            payload["notas"] = ((payload.get("notas") or "") + " | " if payload.get("notas")
                                else "") + "⚠️ TRANSFERENCIA SIN CONFIRMAR (nadie respondio a tiempo)"
            # Directo a la comanda, por lo mismo que en resolver_comprobante_jefe: el
            # envio ya estaba resuelto antes de retener. NO se marca 'pago_confirmado':
            # el pago justamente NO se confirmo, y por eso va con la nota de arriba.
            _entregar_payload(payload, tel_cli, cfg, nucleo=nucleo)
            con.execute("UPDATE esperando_comprobante SET resuelto=2 WHERE id=?", (eid,))
            con.commit()
            # Caso 167 (14-oct-2026, pedido de Gonza): el aviso dice que ESTE pedido fue
            # aceptado por omision de ambos jefes, para dejar el respaldo y poder medirlo
            # (en la base quedan con esperando_comprobante.resuelto = 2).
            _total_venc9 = _total_payload(payload)
            _donde_venc9 = (f"delivery a {payload.get('direccion')}"
                            if payload.get("modalidad") == "delivery" and payload.get("direccion")
                            else (payload.get("modalidad") or "-"))
            aviso_venc9 = (
                f"⚠️ Pedido de {payload.get('cliente') or 'un cliente'} (+{tel_cli}"
                + (f", {_pesos(_total_venc9)}" if _total_venc9 else "") + f", {_donde_venc9}) "
                "ACEPTADO POR OMISIÓN de ambos jefes: nadie confirmó la transferencia (ni el "
                f"local en {ESPERA_COMPROBANTE_MINUTOS} min, ni el respaldo en los "
                f"{ESPERA_BACKUP_MINUTOS} siguientes) y entró a cocina sin validar el pago. "
                "Revisa que haya llegado.")
            _avisar_jefes(aviso_venc9, cfg, db_path=db_path)
            _avisar_backup(aviso_venc9, cfg, db_path=db_path)
            # El cliente se queda esperando el aviso de que paso a cocina: se le da igual
            # (sin decir que la transferencia se recibio, porque nadie la vio).
            _min_venc9 = prototipo.minutos_demora(payload.get("modalidad"))
            enviar_al_cliente(tel_cli, "✅ Tu pedido ya está en preparación."
                              + (f" Demora {_min_venc9} min aprox." if _min_venc9 else "")
                              + " 🙌", cfg)
        return len(filas)
    finally:
        con.close()


def _entregar_payload(payload, telefono, cfg, nucleo=None):
    """Entrega un payload ya armado al POS (HTTP en produccion / nucleo en tests),
    con la misma caida a cola de reintento que _crear_pedido_en_pos."""
    if nucleo is None and cfg is not None:
        url = cfg.get("pos_api_url") or "http://127.0.0.1:8000"
        try:
            return _post_al_cajero(payload, url)
        except Exception as e:
            print(f"[POS][AVISO] no llegue al cajero en {url} ({e}); a la cola de reintento.")
            _guardar_pendiente(payload, telefono, cfg)
            return {"pendiente": True}
    from nucleo import NucleoPedidos   # diferido: solo --test local (pos/ existe en el monorepo)
    from rutas import DB_DEFAULT
    n = nucleo or NucleoPedidos(db_path=DB_DEFAULT)
    return n.crear(canal="whatsapp", cliente=payload["cliente"], modalidad=payload["modalidad"],
                   items=payload["items"], forma_pago=payload["forma_pago"],
                   direccion=payload["direccion"],
                   delivery_cobro=payload.get("delivery_cobro", 0),
                   notas=payload.get("notas", ""), telefono=telefono,
                   hora_programada=payload.get("hora_programada"))


def _hilo_reintento_pendientes():
    """Background: cada REINTENTO_SEGUNDOS intenta drenar la cola de respaldo hacia
    el POS. Falla en silencio ante cualquier excepcion (nunca debe tumbar el webhook)."""
    while True:
        time.sleep(REINTENTO_SEGUNDOS)
        try:
            reintentar_pendientes(_cfg_webhook())
        except Exception as e:
            print(f"[respaldo][AVISO] el hilo de reintento fallo esta vuelta: {e}")
        try:
            # El escalon al backup va SIEMPRE antes del vencimiento: una espera recien
            # escalada no puede vencer en la misma vuelta (el backup no tuvo su turno).
            escalar_esperas_al_backup(_cfg_webhook())
            resolver_esperas_vencidas(_cfg_webhook())
        except Exception as e:
            print(f"[cobro][AVISO] resolver esperas vencidas fallo esta vuelta: {e}")
        try:
            escalar_comprobantes_al_backup(_cfg_webhook())
            resolver_comprobantes_vencidos(_cfg_webhook())
        except Exception as e:
            print(f"[comprobante][AVISO] resolver comprobantes vencidos fallo esta vuelta: {e}")


def _crear_pedido_en_pos(pedido, telefono, cfg=None, nucleo=None):
    """Mete el pedido cerrado por el bot en la cola del POS.
    - Produccion (nucleo=None): se lo manda al CAJERO CORRIENDO por HTTP (POST /api/pedidos).
      El cajero es el unico que escribe -> nada de base compartida ni .exe-vs-fuente.
    - Si el cajero no responde: se guarda en la cola de reintento (RESPALDO_DB) + avisa
      al jefe por WhatsApp -- NUNCA se escribe directo a DB_DEFAULT (ese era el bug: en
      el .exe del bot, DB_DEFAULT resuelve a la carpeta del BOT, no del POS -- el pedido
      quedaba en una base fantasma que Christian jamas veia).
    - Si se inyecta 'nucleo' (--test / uso en proceso), escribe directo ahi (sin pasar
      por HTTP ni por la cola de respaldo) -- ese camino es solo para pruebas."""
    payload = _payload_pedido(pedido, telefono)
    if nucleo is None and cfg is not None:
        url = cfg.get("pos_api_url") or "http://127.0.0.1:8000"
        try:
            r = _post_al_cajero(payload, url)
            print(f"[POS] pedido whatsapp #{r.get('id')} enviado al cajero ({url})")
            return r
        except Exception as e:
            print(f"[POS][AVISO] no llegue al cajero en {url} ({e}); "
                  "lo guardo en la cola de reintento y aviso al jefe.")
            _guardar_pendiente(payload, telefono, cfg)
            return {"pendiente": True, "payload": payload}
    from nucleo import NucleoPedidos   # diferido: solo --test local (pos/ existe en el monorepo)
    from rutas import DB_DEFAULT
    n = nucleo or NucleoPedidos(db_path=DB_DEFAULT)
    return n.crear(canal="whatsapp", cliente=payload["cliente"], modalidad=payload["modalidad"],
                   items=payload["items"], forma_pago=payload["forma_pago"],
                   direccion=payload["direccion"], delivery_cobro=0,
                   notas=payload["notas"], telefono=telefono,
                   hora_programada=payload.get("hora_programada"))


def _post_gasto_al_wms(texto, cfg):
    """Manda un gasto que reporto el JEFE por WhatsApp al libro de caja del POS/WMS.
    Usa el endpoint 'llave' /wms/api/finanzas/gasto-bot (localhost, sin auth -- mismo
    criterio que POST /api/pedidos: la autorizacion real es que el mensaje vino del
    numero del jefe). Falla suave: si el POS no esta arriba, avisa y sigue."""
    import urllib.request
    url = (cfg.get("pos_api_url") or "http://127.0.0.1:8000").rstrip("/") + "/wms/api/finanzas/gasto-bot"
    datos = json.dumps({"texto": texto}).encode("utf-8")
    peticion = urllib.request.Request(url, data=datos, method="POST",
                                      headers=_headers_pos({"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(peticion, timeout=10) as r:
            res = json.loads(r.read().decode() or "{}")
        estado = "registrado" if res.get("registrado") else "no entendido"
        print(f"[GASTO][{estado}] {res.get('eco', '')}")
        return res
    except Exception as e:
        print(f"[GASTO][AVISO] no llegue al libro de caja en {url} ({e}); gasto NO registrado: {texto}")
        return None


INSTRUCCION_RESCATE = """INSTRUCCION DEL SISTEMA DEL LOCAL (esto NO lo escribio el cliente; no le respondas a el):
Revisa tu ULTIMO mensaje de asistente en esta conversacion.
- Si ese mensaje CERRO un pedido (el cliente ya lo habia confirmado y tu le avisaste que va a
  cocina / que le avisas cuando este listo, SIN terminar pidiendo mas datos ni una confirmacion
  pendiente), llama a la funcion emitir_pedido con ese pedido.
- Si ese mensaje le REPITIO el pedido al cliente para que lo confirme (le mostraste que lleva y le
  preguntaste si esta bien asi), llama a la funcion previsualizar_pedido con ese pedido: el
  sistema le va a mostrar la ficha con los precios reales, que tu no tienes.
- Si no era ninguna de las dos (estabas preguntando algo, faltaba un dato, o era solo
  conversacion), llama a la funcion marcar_incompleto."""

# Cuando vale la pena pagar la llamada de rescate (ver _rescatar_pedido). Historicamente
# la señal era el "$", porque todo cierre trae el total.
# 05-ago-2026 (pendiente #9bis): el modelo escribio el resumen de confirmacion A MANO y
# SIN precios -- sin "$" no se rescataba nada, y el cliente confirmo un pedido cuyo total
# nunca vio. Se agrega la otra forma que tiene un cierre: pedir confirmacion de un pedido
# que ya tiene modalidad y pago. Las tres condiciones juntas (pregunta + modalidad + pago)
# a proposito: "¿me confirmas tu nombre y como pagas?" -- que el propio prompt le pide
# decir a mitad del pedido -- trae la pregunta pero no las otras dos, y no dispara nada.
# OJO CON EL PATRON: este regex NO borra ni decide nada (a diferencia del candado de
# montos, que se comio el resumen entero un dia entero). Lo unico que decide es si se le
# PREGUNTA al modelo, de forma estructurada, que era ese mensaje. Un falso positivo cuesta
# una llamada barata que responde marcar_incompleto; un falso negativo cuesta un pedido.
_RE_PIDE_CONFIRMACION = re.compile(
    r"¿\s*(?:esta|está|todo|asi|así)[^?]{0,40}\bbien\b[^?]{0,60}\?"
    r"|¿[^?]{0,30}\bconfirm\w+[^?]{0,40}\?"
    r"|¿[^?]{0,20}\bdejamos\b[^?]{0,20}\?"
    r"|¿[^?]{0,20}\bte\s+parece\b[^?]{0,20}\?", re.IGNORECASE)
_RE_MODALIDAD_DICHA = re.compile(r"\b(retiro|retirar|delivery|domicilio|despacho)\b", re.IGNORECASE)
_RE_PAGO_DICHO = re.compile(r"\b(efectivo|tarjeta|transferencia)\b", re.IGNORECASE)
# El cierre del Paso 2 YA NO TRAE "$" -- y eso desarmo la red de seguridad sin que nadie
# lo notara (visto el 05-ago-2026 probando el rescate contra el modelo real). Hasta el
# 04-ago el cierre incluia el total, asi que el "$" alcanzaba; desde el bloque de salida
# de 2 pasos, el prompt le pide EXPRESAMENTE al modelo cerrar con "una confirmacion final
# breve" sin total ("Confirmado. Demora 10 min aprox, gracias!"). O sea: el mensaje que
# la red del 15-jul-2026 existe para atrapar -- un cierre de palabra sin emitir_pedido,
# que en una noche perdio 3 pedidos reales -- se habia vuelto invisible para ella.
# Se busca la FORMA del cierre, no su total. Deliberadamente NO se usa "demora ... min":
# el mensaje de bienvenida anuncia los tiempos del dia y dispararia un rescate por cada
# conversacion nueva, que es el mensaje mas frecuente que manda el bot.
_RE_CIERRE_DICHO = re.compile(
    r"\bconfirmad[oa]\b"
    r"|\bva\s+(?:a|para)\s+(?:la\s+)?cocina\b"
    r"|\blo\s+mando\s+a\s+(?:preparar|cocina)\b", re.IGNORECASE)


def _menu_activo_dict():
    try:
        return json.loads(prototipo.menu_json_activo().read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[menu][AVISO] no pude leer el menu activo ({e}).")
        return {"categorias": []}


def _agotados_inventados(texto):
    """Productos que el texto da por agotados sin instruccion vigente que lo diga (casos
    141 y 151). Vacio si no hay texto o si todo lo que dice que falta, de verdad falta."""
    if not texto or texto.startswith("[ERROR"):
        return []
    return prototipo.agotados_inventados(
        texto, prototipo.interpretar_instrucciones_estructuradas()["agotados"],
        prototipo.vocabulario_productos(_menu_activo_dict()))


def _parece_cierre_sin_tool(respuesta):
    """True si esta respuesta del modelo, que NO trajo tool-call, huele a cierre (Paso 2)
    o a resumen de confirmacion (Paso 1) -- las dos cosas que no pueden quedarse sin
    estructura: la primera pierde el pedido, la segunda le esconde el total al cliente."""
    texto = respuesta or ""
    if "$" in texto or _RE_CIERRE_DICHO.search(texto):
        return True
    return bool(_RE_PIDE_CONFIRMACION.search(texto)
                and _RE_MODALIDAD_DICHA.search(texto)
                and _RE_PAGO_DICHO.search(texto))


def _rescatar_pedido(cerebro, sistema, previos, texto_cliente, respuesta):
    """RED DE SEGURIDAD (15-jul-2026, tras 3 pedidos reales perdidos en una noche):
    el modelo cerro pedidos de palabra pero no llamo a emitir_pedido, asi que nada
    llego a la cocina y los clientes esperaron comida inexistente. Si la respuesta
    parece un cierre (ver _parece_cierre_sin_tool) y no hubo tool-call, se hace UNA
    segunda llamada dedicada con tool_choice='required' entre las tres tools
    (04-ago-2026: reemplaza el viejo sentinel de texto 'INCOMPLETO' -- la API ahora
    garantiza que esta llamada SIEMPRE devuelve una estructurada, nunca mas prosa suelta
    sin bloque). El costo extra es de fracciones de centavo y solo en cierres.

    05-ago-2026 (pendiente #9bis): tambien se ofrece previsualizar_pedido. Medido en 3
    corridas contra gpt-4.1-mini: en 2 llamo a la tool y la ficha salio perfecta, en la
    3ra escribio el resumen a mano, sin precios, y el cliente confirmo sin ver el total
    -- el mismo agujero de siempre entrando por otra puerta. La llamada de rescate YA se
    pagaba en ese caso; darle la tercera opcion convierte el Paso 1 en un candado de
    codigo en vez de una instruccion de prompt que el modelo obedece 2 de cada 3 veces.

    Devuelve el dict del pedido con su `_tool` (emitir_pedido / previsualizar_pedido) o
    None. QUIEN decide que hacer con el es el llamador: esta funcion no despacha nada."""
    mensajes = ([{"rol": "sistema", "texto": sistema}] + previos +
                [{"rol": "usuario", "texto": texto_cliente},
                 {"rol": "asistente", "texto": respuesta or ""},
                 {"rol": "usuario", "texto": INSTRUCCION_RESCATE}])
    try:
        _texto, pedido = cerebro.pensar(
            mensajes,
            tools=[TOOL_EMITIR_PEDIDO, TOOL_PREVISUALIZAR_PEDIDO, TOOL_MARCAR_INCOMPLETO],
            tool_choice="required")
    except Exception as e:
        print(f"[RESCATE][AVISO] la llamada de rescate fallo ({e}); sigo sin pedido.")
        return None
    # Validacion estricta: solo se acepta un pedido con lo minimo para cocinar -- que es
    # tambien lo minimo para poder escribir una ficha honesta (sin modalidad no se sabe
    # si hay envio, sin forma de pago la ficha saldria con un "(falta)" pegado).
    if not pedido or pedido.get("_error") or pedido.get("derivar_humano"):
        return None
    if not pedido.get("items") or not pedido.get("modalidad") or not pedido.get("forma_pago"):
        return None
    if pedido.get("_tool") == "previsualizar_pedido":
        print("[RESCATE] el resumen de confirmacion no trajo tool-call; la ficha con precios "
              "reales la arma el codigo (no va a cocina).")
        return pedido
    comentarios = (pedido.get("comentarios") or "").strip()
    pedido["comentarios"] = (comentarios + " | " if comentarios else "") + "recuperado por red de seguridad"
    print(f"[RESCATE] el cierre no trajo tool-call; pedido recuperado en segunda llamada.")
    return pedido


def _ya_se_pregunto_modalidad(previos):
    """True si el candado de modalidad YA hizo su pregunta en esta conversacion (ver
    MARCA_* en prototipo.py). Sirve para preguntar UNA vez y no dejar al cliente
    atrapado si el modelo insiste en armar el pedido incoherente."""
    marcas = (prototipo.MARCA_MODALIDAD, prototipo.MARCA_DELIVERY_SIN_DIRECCION)
    return any(t.get("rol") == "asistente" and any(mk in (t.get("texto") or "") for mk in marcas)
               for t in previos)


def _ahora_chile():
    """Hora actual en Chile -- funcion aparte (no datetime.now(TZ_CHILE) inline) para
    poder fijarla en --test, igual que se hace con estado_horario: sin esto, el corte de
    pedidos de las 23:50 haria no-determinista el test si alguien lo corre entre esa hora
    y la medianoche."""
    return datetime.now(TZ_CHILE)


def _toma_pedidos(menu, ahora=None):
    """Si el bot debe seguir tomando pedidos AHORA. Devuelve (toma: bool, motivo) con
    motivo en {None, 'cerrado_por_jefe', 'fuera_de_horario', 'corte_pedidos'} para que
    el llamador sepa que mensaje fijo mostrar. Llama a estado_horario() por NOMBRE (no
    directo al modulo prototipo) a proposito: los tests de --test mockean
    'estado_horario' reasignando este mismo global de webhook.py, y necesitan seguir
    pudiendolo hacer.

    'cerrado_por_jefe' (18-sep-2026, caso #51 del curador) se chequea PRIMERO y aparte
    de estado_horario: el jefe puede cerrar un dia que cae DENTRO del horario normal
    (ej. "cerrado hoy" un martes cualquiera), y antes de este candado esa instruccion
    solo vivia como texto libre en el prompt -- el chat real mostro al bot tomando un
    pedido completo despues de que el jefe avisara dos veces que estaba cerrado."""
    if interpretar_instrucciones_estructuradas().get("cerrado_hoy"):
        return False, "cerrado_por_jefe"
    abierto, _ = estado_horario(menu)
    if not abierto:
        return False, "fuera_de_horario"
    ahora = ahora or _ahora_chile()
    if ahora.strftime("%H:%M") >= CORTE_PEDIDOS:
        return False, "corte_pedidos"
    return True, None


def procesar_mensaje(telefono, texto, cfg, cerebro=None, nucleo=None, tarificador=None,
                      conv_db=None, llegado_en=None):
    """El corazon del bot: arma el contexto (historial + system prompt), llama al
    cerebro, decide si es el jefe o un cliente, y si el pedido cerro lo mete al POS.
    Todas las dependencias son inyectables (cerebro/nucleo/tarificador/conv_db) para
    poder verificar esta funcion SIN Meta ni Gemini reales (ver --test).

    'llegado_en' (10-ago-2026, debounce): cuando el mensaje ESCRIBIO el cliente, que con
    el buffer ya no es cuando se procesa. Solo lo usa el candado de las 23:50: si un
    mensaje entro 23:49:55 y el debounce lo suelta 23:50:03, el corte no puede rechazarlo
    -- el cliente escribio dentro del horario. En None (todo el resto del codigo y los
    483 candados) se usa la hora de ahora, igual que siempre."""
    tel_norm = _norm_telefono(telefono)
    # 13-sep-2026: el rol se decide en UN solo lugar (ROLES_JEFE / _rol_de). Cualquier
    # numero fuera de ese dict -- incluido el celular personal de Christian, que hasta
    # el 13-sep-2026 tenia el toggle 'modo jefe'/'modo prueba' -- es un cliente normal.
    es_jefe = _rol_de(telefono) is not None

    # --- Limite anti-abuso de mensajes entrantes (CODIGO, sin LLM; pendiente #34) ---
    # Solo aplica a clientes reales: un jefe nunca es "un cliente que abusa", y
    # bloquearlo lo dejaria sin poder operar el bot.
    if not es_jefe and _bloqueado_por_abuso(telefono, cfg, db_path=conv_db):
        guardar_turno(telefono, "usuario", texto, db_path=conv_db)
        _registrar_candado("antiabuso", tel_norm)
        print(f"[antiabuso] {tel_norm}: paso el limite de mensajes, no se contesta este turno")
        return None

    # --- Horario cerrado: mensaje FIJO, sin pasar por el cerebro (CODIGO, sin LLM) ---
    # 28-jul-2026: antes esto dependia 100% de que la IA leyera "Estado: CERRADO" dentro
    # del prompt y decidiera sola no tomar el pedido -- en la practica no lo respetaba
    # (tomaba pedidos completos y calculaba el total de madrugada, con el local cerrado).
    # Ahora el candado es de codigo: si el local esta cerrado, ni el cerebro se llama.
    # El jefe (instrucciones/mejoras/gastos) no se ve afectado por este candado.
    if not es_jefe:
        menu_actual = json.loads(prototipo.menu_json_activo().read_text(encoding="utf-8"))
        toma, motivo = _toma_pedidos(menu_actual, llegado_en)
        if not toma:
            if motivo == "fuera_de_horario":
                visible = mensaje_cerrado(menu_actual)
            elif motivo == "cerrado_por_jefe":
                visible = MENSAJE_CERRADO_POR_JEFE
            else:
                visible = mensaje_corte_pedidos()
            # Caso 113 (curador): el horario completo va una vez; despues, cortesia corta.
            visible = prototipo.mensaje_cerrado_escalonado(
                visible, historial(telefono, db_path=conv_db), texto)
            guardar_turno(telefono, "usuario", texto, db_path=conv_db)
            guardar_turno(telefono, "asistente", visible, db_path=conv_db)
            enviar_whatsapp(telefono, visible, cfg)
            return visible

    # --- 'envio <monto>' de un jefe: resuelve el pedido retenido (CODIGO, sin LLM) ---
    if es_jefe:
        # 'no llegamos' se chequea ANTES que el monto: es la otra respuesta posible a la
        # misma pregunta (P9) y no trae numero, asi que nunca puede confundirse con ella.
        if _es_no_llegamos(texto):
            visible = resolver_no_llegamos_jefe(cfg)
            guardar_turno(telefono, "usuario", texto, db_path=conv_db)
            guardar_turno(telefono, "asistente", visible, db_path=conv_db)
            enviar_whatsapp(telefono, visible, cfg)
            return visible
        m_envio = RE_ENVIO_JEFE.search(texto)
        if m_envio:
            monto = int(re.sub(r"\D", "", m_envio.group(1)) or 0)
            visible = resolver_cobro_jefe(monto, cfg, nucleo=nucleo) if monto > 0 else \
                "No entendí el monto. Escribe por ejemplo: envío 1500"
            guardar_turno(telefono, "usuario", texto, db_path=conv_db)
            guardar_turno(telefono, "asistente", visible, db_path=conv_db)
            enviar_whatsapp(telefono, visible, cfg)
            return visible
        # --- 'confirmado' de un jefe: libera el pedido retenido por transferencia ---
        # Caso 149 (curador, 09-oct-2026): el bot le pregunto al jefe "¿saco 'solo retiro'?
        # Confirmame" y su "confirmo" lo tomo este atajo: "No hay pedidos esperando
        # confirmar transferencia". Sin transferencia esperando, "confirmo" es conversacion.
        if _es_confirmacion_comprobante(texto) and contar_esperando_comprobante() > 0:
            visible = resolver_comprobante_jefe(cfg, nucleo=nucleo)
            guardar_turno(telefono, "usuario", texto, db_path=conv_db)
            guardar_turno(telefono, "asistente", visible, db_path=conv_db)
            enviar_whatsapp(telefono, visible, cfg)
            return visible
        # --- 'en camino <tel>' / 'afuera <tel>' de un jefe: avisa al CLIENTE (S2.5 #9) ---
        m_camino = RE_EN_CAMINO_JEFE.match(texto.strip())
        if m_camino:
            visible = resolver_en_camino_jefe(m_camino.group(1), m_camino.group(2), cfg)
            guardar_turno(telefono, "usuario", texto, db_path=conv_db)
            guardar_turno(telefono, "asistente", visible, db_path=conv_db)
            enviar_whatsapp(telefono, visible, cfg)
            return visible

    cerebro = cerebro or _cerebro_real()
    previos = historial(telefono, db_path=conv_db)
    # 07-oct-2026 (Christian, caso 133 del curador: "no repetir el gracias, se hace un
    # loop"): si lo ultimo que dijo el bot fue su despedida y el cliente solo le devuelve
    # otra cortesia ("gracias usted"), no se contesta. Se guarda el turno del cliente
    # (el curador lo ve) y no sale nada; el modelo ni se llama.
    if (not es_jefe and previos and previos[-1].get("rol") == "asistente"
            and _es_despedida_del_bot(previos[-1].get("texto")) and _es_solo_cortesia(texto)):
        guardar_turno(telefono, "usuario", texto, db_path=conv_db)
        _registrar_candado("cortesia_repetida", telefono)
        print(f"[candado_cortesia] {telefono}: la despedida ya se contesto, no se repite.")
        return None
    # Caso 138 (curador, 08-oct-2026): "Confirmado. Demora 30 min aprox, ¡gracias!" y el
    # cliente contesta "A ustedes": el modelo lo tomo como un pedido nuevo ("¿te lo agrego
    # al pedido que acabas de hacer?") y la conversacion se enredo. Cortesia pura despues
    # del cierre = una despedida corta fija; si vuelve a agradecer, el candado de arriba
    # ya no contesta.
    if (not es_jefe and previos and previos[-1].get("rol") == "asistente"
            and prototipo.es_cierre_del_bot(previos[-1].get("texto"))
            and _es_solo_cortesia(texto)):
        # Caso 170: a un "ok" no se le agradece.
        visible = MENSAJE_ACUSE_DUPLICADO if _es_solo_acuse(texto) else "¡Gracias a ti!"
        guardar_turno(telefono, "usuario", texto, db_path=conv_db)
        guardar_turno(telefono, "asistente", visible, db_path=conv_db)
        _registrar_candado("cortesia_tras_cierre", telefono)
        enviar_whatsapp(telefono, visible, cfg)
        return visible
    # Casos 159 y 160 (curador, 11-oct-2026): el bot ofrecio el menu y el cliente dijo
    # "Ok"; el modelo lo tomo como un gracias o pregunto "¿qué quieres pedir?" sin mandar
    # nada. Oferta de la carta + asentimiento corto = el link, sin llamar al modelo.
    if (not es_jefe and previos and previos[-1].get("rol") == "asistente"
            and prototipo.ofrecio_menu(previos[-1].get("texto"))
            and prototipo.asiente_al_menu(texto)):
        visible = prototipo.MENSAJE_LINK_MENU
        guardar_turno(telefono, "usuario", texto, db_path=conv_db)
        guardar_turno(telefono, "asistente", visible, db_path=conv_db)
        _registrar_candado("menu_ofrecido", telefono)
        enviar_whatsapp(telefono, visible, cfg)
        return visible
    # Caso 162 (curador, 12-oct-2026): con el pedido ya cerrado el cliente escribe
    # "recordar sin cebolla porfavor" y el bot contesto un parrafo dudando. Si esa
    # indicacion ya esta en la linea de comentarios de su ficha, se contesta corto.
    if not es_jefe and prototipo.recordatorio_ya_anotado(
            texto, _comentarios_pedido_reciente(telefono, conv_db)):
        visible = prototipo.MENSAJE_NOTA_YA_ANOTADA
        guardar_turno(telefono, "usuario", texto, db_path=conv_db)
        guardar_turno(telefono, "asistente", visible, db_path=conv_db)
        _registrar_candado("nota_ya_anotada", telefono)
        enviar_whatsapp(telefono, visible, cfg)
        return visible
    # Caso 142 (curador, 08-oct-2026): "Viene presencial" = va a pedir en el local; no es
    # un retiro por WhatsApp. Se agradece y no se le pide el pedido.
    if not es_jefe and previos and prototipo.viene_presencial(texto):
        visible = prototipo.MENSAJE_VIENE_PRESENCIAL
        guardar_turno(telefono, "usuario", texto, db_path=conv_db)
        guardar_turno(telefono, "asistente", visible, db_path=conv_db)
        _registrar_candado("viene_presencial", telefono)
        enviar_whatsapp(telefono, visible, cfg)
        return visible
    # Caso 68 (25-sep-2026): el cliente pregunta por un pedido que YA hizo ("acabo de
    # pedir", "cuanto me salio"). El bot no ve los pedidos del POS por su cuenta, y el
    # modelo, en vez de reconocerlo, le empezaba a tomar un pedido nuevo. Se deriva a
    # una persona sin llamar al modelo. Si ya se derivo en el turno anterior, no se
    # vuelve a avisar a los jefes (un aviso por consulta, no uno por mensaje).
    if not es_jefe and prototipo.pregunta_por_pedido_existente(texto):
        _ya_derivado = any(t.get("rol") == "asistente"
                           and t.get("texto") == prototipo.MENSAJE_PEDIDO_EXISTENTE
                           for t in previos[-2:])
        visible = prototipo.MENSAJE_PEDIDO_EXISTENTE
        guardar_turno(telefono, "usuario", texto, db_path=conv_db)
        guardar_turno(telefono, "asistente", visible, db_path=conv_db)
        if not _ya_derivado:
            _aviso_existente = (f"🔎 +{telefono} pregunta por un pedido que ya hizo: "
                                f"«{texto[:200]}». Alguien tiene que responderle.")
            _avisar_jefes(_aviso_existente, cfg, db_path=conv_db)
            _registrar_caso_humano("pedido_existente", telefono, _aviso_existente,
                                   db_path=conv_db)
        enviar_whatsapp(telefono, visible, cfg)
        return visible
    # Casos 45 y 42 (26-sep-2026): respuestas de CODIGO, sin llamar al modelo.
    if not es_jefe:
        visible = None
        if _avisa_que_ya_transfirio(texto) and _tiene_comprobante_pendiente(telefono):
            visible = MENSAJE_TRANSFERENCIA_AVISADA
            _registrar_candado("transferencia_avisada", telefono)
        else:
            visible = _respuesta_total_repetido(texto, previos)
            if visible:
                _registrar_candado("total_repetido", telefono)
        if visible:
            guardar_turno(telefono, "usuario", texto, db_path=conv_db)
            guardar_turno(telefono, "asistente", visible, db_path=conv_db)
            enviar_whatsapp(telefono, visible, cfg)
            return visible
    # F5 (06-ago-2026): el menu viaja por INTENCION (ver prototipo.foco_del_menu).
    # La conversacion que decide el foco incluye los mensajes del BOT, no solo los del
    # cliente: si el bot nombro un producto, ese producto queda expandido en el turno
    # siguiente aunque el cliente conteste "ese" o "el primero".
    # Se arma con el MISMO `previos` que ve el modelo (ya recortado a
    # MAX_TURNOS_HISTORIAL) a proposito: si un producto se cayo de la ventana, el modelo
    # tampoco lo tiene en contexto, asi que mandarle su precio no arreglaria nada. El
    # foco no puede saber mas que el que lo va a usar.
    conversacion = "\n".join([t.get("texto") or "" for t in previos] + [texto])
    sistema = construir_sistema(jefe=es_jefe, conversacion=conversacion)
    mensajes = [{"rol": "sistema", "texto": sistema}]
    # ABRE una conversacion tanto el que llega con el historial vacio como el que vuelve
    # despues de un silencio largo (05-ago-2026: el cliente que cancela y reaparece una
    # hora despues -- ver MINUTOS_REAPERTURA_CONVERSACION). Los dos merecen el saludo y
    # sus datos de cliente conocido; el hilo viejo NO se borra, sigue en el contexto.
    _silencio = minutos_desde_ultimo_turno(telefono, db_path=conv_db)
    abre_conversacion = (not previos) or (
        _silencio is not None and _silencio >= MINUTOS_REAPERTURA_CONVERSACION)
    if not es_jefe and abre_conversacion:
        # 27-jul-2026 (S2.5 puntos 3 y 4): solo se consulta al ABRIR una conversacion
        # (no en cada mensaje) -- una vez que la respuesta del bot ya trae el contexto,
        # el historial normal lo arrastra solo, sin pegarle al POS en cada mensaje.
        nota_reciente = _nota_pedido_reciente(telefono)
        if nota_reciente:
            mensajes.append({"rol": "sistema", "texto": nota_reciente})
        nota_cliente = _nota_cliente_conocido(telefono)
        if nota_cliente:
            mensajes.append({"rol": "sistema", "texto": nota_cliente})
    # Caso 153 (Christian, 13-oct-2026): levantada una restriccion, se borra del mapa. El
    # modelo no ve los mensajes fijos de "cerrado", "solo retiro" o "se nos acabo X" que
    # ya no estan vigentes, asi no hay nada de que disculparse ni que recordar. (Antes,
    # casos 74/75, se le inyectaba una nota para que se disculpara: Christian no lo quiere.)
    # Si el local estuviera cerrado, el mensaje fijo ya salio antes sin llegar hasta aca.
    if es_jefe:
        mensajes += previos
    else:
        mensajes += prototipo.sin_restricciones_levantadas(
            previos, interpretar_instrucciones_estructuradas())
    if not es_jefe and not abre_conversacion:
        # 05-ago-2026 (pendiente #8): a mitad del pedido el bot repreguntaba un nombre
        # que YA tenia, porque el dato solo entraba al abrir la conversacion y despues
        # dependia de que el modelo lo arrastrara. Ahora el sistema lo repone en cada
        # turno, al final del contexto. En el turno que ABRE no va: ahi ya lo trae
        # _nota_cliente_conocido, con sus instrucciones de saludo.
        nota_fijos = _nota_datos_fijos_cliente(telefono)
        if nota_fijos:
            mensajes.append({"rol": "sistema", "texto": nota_fijos})
    if not es_jefe:
        # Caso 130 (curador, 06-oct-2026): "Completo" como respuesta a "¿vienesa o As?".
        _nota_completo = prototipo.nota_completo_como_base(previos, texto)
        if _nota_completo:
            _registrar_candado("completo_como_base", telefono)
            mensajes.append({"rol": "sistema", "texto": _nota_completo})
    mensajes.append({"rol": "usuario", "texto": texto})
    pedido = None
    previsualizacion = None
    # Lo pone en True la rama que YA avisa a los jefes por su cuenta (pedido grande),
    # para que el candado de "prometio una persona" del final no mande la segunda alerta
    # por el mismo turno: su mensaje fijo tambien promete a alguien del local.
    humano_ya_avisado = False
    if es_jefe:
        respuesta = cerebro.pensar(mensajes)
    else:
        # 04-ago-2026: tool-calling nativo en vez de un bloque <PEDIDO_JSON> en texto --
        # pedido ya viene parseado y validado por OpenAI contra TOOL_EMITIR_PEDIDO.
        respuesta, pedido = cerebro.pensar(
            mensajes, tools=[TOOL_EMITIR_PEDIDO, TOOL_PREVISUALIZAR_PEDIDO])
        # Casos 141 y 151 (curador, 08/09-oct-2026): el bot dio por agotado el chacarero
        # copiando el ejemplo del prompt, sin ninguna instruccion del local. Se le pide la
        # respuesta UNA vez mas con una nota; si insiste, el saneo de abajo saca la frase.
        _falsos = _agotados_inventados(respuesta)
        if _falsos:
            _registrar_candado("agotado_inventado", telefono, ",".join(_falsos))
            print(f"[agotado][CANDADO] {telefono}: dijo que no hay {_falsos} sin instruccion; "
                  "se le pide la respuesta de nuevo.")
            respuesta, pedido = cerebro.pensar(
                mensajes + [{"rol": "sistema", "texto": prototipo.nota_agotado_inventado(_falsos)}],
                tools=[TOOL_EMITIR_PEDIDO, TOOL_PREVISUALIZAR_PEDIDO])
        # 05-ago-2026: el Paso 1 del cierre (mostrar la ficha con precios y pedir
        # confirmacion) NO es un pedido -- no va a cocina. Se separa apenas llega, asi
        # todo el resto de la funcion lo trata como lo que es: un turno de conversacion.
        # La ficha se arma abajo, con los precios del motor real.
        if pedido and pedido.get("_tool") == "previsualizar_pedido":
            previsualizacion, pedido = pedido, None
        # Caso 60 (25-sep-2026): "agregame unas papas al que acabo de hacer" despues de
        # "Confirmado" -> el modelo emitia directo a cocina sin mostrar que se agregaba ni
        # cuanto costaba. Si el pedido anterior ya se confirmo y el cliente pide agregar,
        # lo emitido se muestra primero como ficha para que lo confirme.
        if (pedido and not pedido.get("_error")
                and prototipo.es_agregado_post_confirmacion(previos, texto)):
            _registrar_candado("agregado_post_confirmacion", telefono)
            print(f"[agregado][CANDADO] {telefono}: agrega a un pedido ya confirmado; "
                  "primero la ficha, no directo a cocina.")
            previsualizacion, pedido = {**pedido, "_tool": "previsualizar_pedido"}, None
        # Candado del nombre (caso #54, 25-sep-2026): "a nombre de X" dicho por el
        # cliente en este pedido manda sobre el nombre que el POS tenga guardado.
        _nombre_dicho = prototipo.nombre_dado_por_cliente(
            prototipo.textos_cliente_del_pedido(previos, texto, abre_conversacion))
        prototipo.fijar_nombre_dicho(pedido, _nombre_dicho)
        prototipo.fijar_nombre_dicho(previsualizacion, _nombre_dicho)
        # Casos 144 y 137 (curador, 08-oct-2026): "completo <sabor>" es Vienesa, y "promo
        # 2x1 de X" son dos unidades. Se corrige el pedido antes de armar la ficha.
        _textos_pedido = prototipo.textos_cliente_del_pedido(previos, texto, abre_conversacion)
        _vienesas = next((
            [p.get("nombre") for p in c.get("productos") or []]
            for c in _menu_activo_dict().get("categorias") or []
            if c.get("nombre") == "Vienesas"), [])
        for _p in (pedido, previsualizacion):
            if _p and not _p.get("_error"):
                if prototipo.corregir_base_completo(_p, _textos_pedido, _vienesas):
                    _registrar_candado("completo_es_vienesa", telefono)
                if prototipo.corregir_promo_2x1(_p, _textos_pedido):
                    _registrar_candado("promo_2x1", telefono)
        # Casos 166 y 168 (curador, 13-oct-2026): "quiero la promocion" o "2 x 8900" sin
        # nombrar un producto, con la ficha ya vista -> el pedido no cambia ni se cierra.
        if (prototipo.ficha_vista_en_pedido_en_curso(previos)
                and prototipo.pide_promo_sin_producto(
                    texto, prototipo.palabras_que_nombran_producto(_menu_activo_dict()))):
            _registrar_candado("promo_sin_producto", telefono)
            print(f"[promo][CANDADO] {telefono}: pide una promo sin nombrar producto; "
                  "el pedido sigue como en la ficha.")
            pedido, previsualizacion, respuesta = None, None, prototipo.MENSAJE_PROMO_SIN_PRODUCTO
        # Caso 118 (curador, 05-oct-2026): ficha ya vista + solo el dato de pago + mismo
        # pedido -> se cierra aca, sin mostrar la ficha otra vez. El texto del modelo se
        # descarta (era la intro de una ficha que ya no va) y abajo sale la confirmacion
        # con la demora, igual que cuando el modelo cierra sin texto.
        _cierre_pago = _cierre_por_dato_de_pago(previsualizacion, previos, texto)
        if _cierre_pago:
            _registrar_candado("cierre_dato_pago", telefono)
            print(f"[cierre_pago][CANDADO] {telefono}: la ficha ya se mostro y solo faltaba "
                  "el pago; se cierra sin repetirla.")
            pedido, previsualizacion, respuesta = _cierre_pago, None, ""

    if (respuesta or "").startswith("[ERROR"):
        # 27-jul-2026 (S1 del plan): CerebroOpenAI/CerebroGemini NUNCA lanzan excepcion,
        # devuelven un string "[ERROR ...]" -- y hasta ahora ese string se lo mandabamos
        # TAL CUAL al cliente por WhatsApp (el stacktrace le llegaba de verdad) y quedaba
        # guardado en el historial, contaminando el contexto de la proxima llamada.
        # Ahora: mensaje humano al cliente, aviso a los jefes, y NO se guarda nada de
        # este intercambio (ni la pregunta del cliente ni el error) -- la proxima vez
        # arranca limpio, como si el mensaje fallido nunca hubiera pasado.
        print(f"[cerebro][ERROR] {telefono}: {respuesta[:300]}")
        _texto_aviso_cerebro = (f"⚠️ El bot no pudo responder a {telefono} (falla del cerebro):\n"
                                f"{respuesta[:300]}")
        _avisar_jefes(_texto_aviso_cerebro, cfg)
        _registrar_caso_humano("error_cerebro", telefono, _texto_aviso_cerebro)
        visible = "Dame un minutito 🙏 ya te respondo."
        enviar_whatsapp(telefono, visible, cfg)
        return visible

    guardar_turno(telefono, "usuario", texto, db_path=conv_db)
    _id_turno_respuesta = guardar_turno(telefono, "asistente", respuesta or "", db_path=conv_db)

    if es_jefe:
        visible, _hubo_instr, _n_mejoras, gastos = procesar_bloques_jefe(respuesta)
        for _g in gastos:                          # gastos que el jefe reporto -> libro de caja
            _post_gasto_al_wms(_g, cfg)
    else:
        # visible es directamente lo que dijo el modelo (ya no hay bloque que extraer).
        # Respaldo: la API no garantiza content + tool_call juntos en el mismo mensaje
        # -- si cerro (pedido) sin texto, el cliente igual recibe una confirmacion.
        visible = respuesta or ""
        if pedido and not visible.strip():
            # 05-ago-2026: la despedida ahora lleva la demora real (ejemplo de Gonza).
            # No es un caso raro: contra gpt-4.1-mini el cierre viene sin texto casi
            # siempre, asi que esto es lo que el cliente lee al confirmar.
            visible = prototipo.confirmacion_cierre(pedido.get("modalidad"),
                                                    forma_pago=pedido.get("forma_pago"))
            reescribir_ultimo_turno_asistente(telefono, visible, db_path=conv_db)
        # Candado 1.3 (03-ago-2026): si el modelo pregunto por el tamaño de un churrasco
        # o una mechada, la pregunta no llega al cliente. Va aca, sobre el texto ya
        # limpio de bloques, para que valga en TODAS las ramas de abajo (candado, prueba
        # y despacho real) sin repetirlo en cada una.
        _visible_crudo = visible
        # Caso 63 (25-sep-2026): la llamada interna escrita como texto no llega.
        visible = prototipo.sanear_texto_interno(visible)
        # Casos 138/140/150/153 (curador): ninguna mencion a una restriccion pasada ("estuvimos
        # cerrados", "ya volvimos a tener X") ni a un fallo llega al cliente.
        visible = prototipo.sanear_restriccion_levantada(visible)
        # Casos 141/151: si el reintento de arriba igual dijo que falta algo sin
        # instruccion, la frase no llega.
        _falsos_finales = _agotados_inventados(visible)
        if _falsos_finales:
            visible = prototipo.sanear_agotado_inventado(
                visible, _falsos_finales, prototipo.vocabulario_productos(_menu_activo_dict()))
        # Caso #54 (25-sep-2026): la AFIRMACION "no llevan tamaño" sin que nadie
        # preguntara. Va antes del candado de la pregunta, que tiene su propia frase.
        visible = prototipo.sanear_tamano_no_preguntado(visible, texto)
        visible = sanear_tamano_sandwich(visible, texto)
        # Candado del saludo (05-ago-2026, chat real de Gonza): la bienvenida
        # "Hola! Bienvenid@ a Patata Huechuraba" es SOLO del mensaje que ABRE la
        # conversacion. Si el modelo la repite a mitad del pedido, no llega al cliente.
        # Se apoya en la MISMA señal que las notas internas de arriba: historial vacio,
        # o vuelta despues de un silencio largo. La primera version miraba solo
        # `not previos` y por eso le comio el saludo al cliente que volvio a la hora.
        # Candado del ORDEN (24-sep-2026, caso del curador del 22-sep): saludo -> aviso ->
        # demora -> ficha en el primer mensaje, la demora una sola vez aunque venga como
        # aviso, y sin paquete de bienvenida en los turnos siguientes. Va ANTES del
        # candado del saludo: este ya saca el paquete entero, aquel queda de respaldo.
        # 26-sep-2026: "primer turno" = el bot todavia no dio la bienvenida en esta
        # conversacion (ver prototipo.ya_saludo), no solo "no pasaron 30 minutos".
        _primer_saludo = not prototipo.ya_saludo(previos, abre_conversacion)
        visible = prototipo.ordenar_bienvenida(visible, primer_turno=_primer_saludo)
        # Caso 141 (curador): sin instruccion vigente no hay linea de "Aviso:".
        visible = prototipo.sanear_aviso_sin_instruccion(
            visible, prototipo.leer_instrucciones() != "(ninguna, opera normal)")
        # Candados del caso 65 (25-sep-2026): si el cliente ya dijo con que paga, no se le
        # vuelve a preguntar; y el voseo que se le escape al modelo se pasa al tuteo.
        visible = prototipo.sanear_pregunta_pago(
            visible, prototipo.pago_ya_dicho(
                prototipo.textos_cliente_del_pedido(previos, texto, abre_conversacion)))
        visible = prototipo.sanear_voseo(visible)
        # Caso 47 (26-sep-2026): con "solo retiro" no se anuncia demora de delivery.
        visible = prototipo.sanear_demora_solo_retiro(
            visible, prototipo.interpretar_instrucciones_estructuradas()["solo_retiro"])
        visible = sanear_saludo_repetido(visible, primer_turno=_primer_saludo)
        # Caso 170 (15-oct-2026): con el pedido ya cerrado no se pregunta "¿confirmas el
        # pedido / quieres agregar algo?". Si el modelo arma un pedido o una ficha nueva,
        # no se toca: es otro pedido y esa pregunta si corresponde.
        if pedido is None and previsualizacion is None:
            visible = prototipo.sanear_pregunta_pedido_cerrado(
                visible, bool(_pedido_despachado_hace_poco(telefono)
                              or _tiene_comprobante_pendiente(telefono)))
            # Y a un "ok" no se le agradece aunque el modelo lo haga (regla 3.10a).
            if (_es_solo_acuse(texto) and _es_despedida_del_bot(visible)
                    and "gracias a ti" in _sin_tildes_min(visible)):
                visible = MENSAJE_ACUSE_DUPLICADO
        if visible != _visible_crudo:
            # El historial guarda la respuesta CRUDA del modelo, o sea la pregunta que
            # acabamos de borrar seguiria viva en su contexto y la repetiria en el turno
            # siguiente -- el candado taparia el sintoma para siempre sin corregir nada.
            # Misma leccion que el bucle de modalidad (28-jul): el historial tiene que
            # guardar lo que el cliente REALMENTE leyo.
            reescribir_ultimo_turno_asistente(telefono, visible, db_path=conv_db)
        if pedido is None and previsualizacion is None and _parece_cierre_sin_tool(respuesta):
            # Con previsualizacion NO se rescata: el modelo YA dijo, de forma
            # estructurada, que esto no es un cierre sino la ficha para confirmar.
            # Preguntarselo de nuevo seria pagar una llamada para que se contradiga.
            rescatado = _rescatar_pedido(cerebro, sistema, previos, texto, respuesta)
            if rescatado and rescatado.get("_tool") == "previsualizar_pedido":
                # Pendiente #9bis (05-ago-2026): el modelo escribio el resumen a mano en
                # vez de llamar a previsualizar_pedido. El rescate lo devuelve como lo que
                # era, y de aca en adelante sigue el MISMO camino que si hubiera obedecido:
                # la ficha con precios reales se pega abajo y nada va a cocina. Que el
                # cliente vea su total deja de depender de la obediencia del modelo.
                previsualizacion = rescatado
                prototipo.fijar_nombre_dicho(previsualizacion, _nombre_dicho)
            else:
                pedido = rescatado
        # Candado del link de la carta (07-oct-2026, caso 118): el link no se retipea.
        _visible_antes_link = visible
        visible = corregir_link_menu(visible)
        if visible != _visible_antes_link:
            _registrar_candado("link_menu", telefono)
            reescribir_ultimo_turno_asistente(telefono, visible, db_path=conv_db)
        # Candado de montos (04-ago-2026, Gonza): el resto de esta funcion (mas abajo)
        # ya garantiza el TOTAL de un pedido que se despacha (_aviso_total, 30-jul) --
        # ahi el diseño es dejar ver el numero equivocado del modelo Y el correcto
        # despues, a proposito. Esta rama es la complementaria: cuando NO hay un pedido
        # que vaya a despacharse este turno (charla suelta, JSON roto, o deriva a
        # humano), ningun otro candado mira los $ que el modelo haya escrito -- aca se
        # sacan los que no vienen del menu real.
        if not (pedido and not pedido.get("_error") and not pedido.get("derivar_humano")):
            _visible_antes_montos = visible
            _menu_para_montos = json.loads(prototipo.menu_json_activo().read_text(encoding="utf-8"))
            # Los montos que el CLIENTE escribio el mismo (el billete con que paga, por
            # ejemplo) van autorizados: no son precios inventados por el modelo. Se leen
            # del historial + el mensaje de ahora, que es donde el cliente habla.
            _montos_cliente = montos_dichos_por_el_cliente(
                [t.get("texto") for t in previos if t.get("rol") == "usuario"] + [texto])
            visible = sanear_montos_no_autorizados(
                visible, montos_validos_del_menu(_menu_para_montos), _montos_cliente)
            if visible != _visible_antes_montos:
                reescribir_ultimo_turno_asistente(telefono, visible, db_path=conv_db)
        # Caso 160 (curador, 11-oct-2026): el modelo cerro con emitir_pedido sin haber
        # mostrado nunca la ficha -- sin forma de pago y sin preguntar el aji del
        # chacarero -- y el pedido entro a cocina asi. Va despues del rescate, que
        # tambien puede devolver un cierre. Si falta el aji, o falta la forma de pago y
        # el cliente no vio la ficha en este pedido, lo emitido se muestra como ficha (o
        # se pregunta el aji) y no va a cocina. El texto de cierre del modelo
        # ("Confirmado...") no llega: seria falso.
        # Si el pedido choca con "solo retiro" o un agotado, manda el candado de
        # instrucciones de mas abajo: preguntar el aji de un chacarero agotado no sirve.
        if (pedido and not pedido.get("_error") and not pedido.get("derivar_humano")
                and not candado_instrucciones(pedido)):
            _sin_ficha = (not pedido.get("forma_pago")
                          and not prototipo.ficha_vista_en_pedido_en_curso(previos))
            _sin_aji = prototipo.falta_preguntar_aji(
                pedido, prototipo.textos_cliente_del_pedido(previos, texto, abre_conversacion),
                [t.get("texto") for t in previos if t.get("rol") == "asistente"])
            # Caso 165 (curador, 13-oct-2026): efectivo con delivery cerrado sin saber con
            # cuanto paga (y la ficha nunca lo pregunto). Vuelve a la ficha, que lo pregunta;
            # una sola vez, para no dejar al cliente sin poder cerrar.
            _textos_165 = prototipo.textos_cliente_del_pedido(previos, texto, abre_conversacion)
            _sin_vuelto = (
                str(pedido.get("forma_pago") or "").lower() == "efectivo"
                and str(pedido.get("modalidad") or "").lower() == "delivery"
                and not re.search(r"\d", pedido.get("comentarios") or "")
                and not montos_dichos_por_el_cliente(_textos_165)
                and not any(prototipo.dato_de_pago_solo(t) and prototipo.dato_de_pago_solo(t).get("monto")
                            for t in _textos_165)
                and not any("con cuanto pagas" in _sin_tildes_min(t.get("texto"))
                            for t in previos if t.get("rol") == "asistente"))
            if _sin_ficha or _sin_aji or _sin_vuelto:
                _registrar_candado("cierre_sin_ficha" if _sin_ficha else
                                   "cierre_sin_aji" if _sin_aji else "cierre_sin_vuelto", telefono)
                print(f"[cierre][CANDADO] {telefono}: cierre sin "
                      f"{'ficha' if _sin_ficha else 'aji' if _sin_aji else 'monto del vuelto'}; "
                      "primero la ficha, no a cocina.")
                previsualizacion, pedido = {**pedido, "_tool": "previsualizar_pedido"}, None
                visible = ""
        # Caso 165 (curador): el cliente ya dijo "Efectivo" y la ficha salio sin la linea de
        # pago, preguntandola de nuevo. Se completa con lo que el cliente dijo.
        if previsualizacion is not None and not previsualizacion.get("forma_pago"):
            _forma_dicha = prototipo.forma_pago_dicha(
                prototipo.textos_cliente_del_pedido(previos, texto, abre_conversacion))
            if _forma_dicha:
                previsualizacion["forma_pago"] = _forma_dicha
        if previsualizacion is not None:
            # PASO 1 del cierre: la ficha con precios reales la escribe el codigo y se
            # pega bajo la linea del modelo. Si no se pudo armar (menu que no reconoce un
            # producto, por ejemplo), el turno sigue como una respuesta normal -- nunca
            # se le muestra al cliente una ficha a medias ni un total inventado.
            # Casos 61 y 67 (25-sep-2026): chacarero sin decir con o sin aji -> se
            # pregunta UNA vez antes de mostrar la ficha.
            _textos_pedido = prototipo.textos_cliente_del_pedido(previos, texto, abre_conversacion)
            _bot_previos = [t.get("texto") for t in previos if t.get("rol") == "asistente"]
            if prototipo.falta_preguntar_aji(previsualizacion, _textos_pedido, _bot_previos):
                ficha = None
                visible = prototipo.pregunta_aji(previsualizacion)
                reescribir_ultimo_turno_asistente(telefono, visible, db_path=conv_db)
            else:
                ficha = _ficha_confirmacion(previsualizacion, telefono, cfg)
            if ficha is None and visible == prototipo.pregunta_aji(previsualizacion):
                pass
            elif ficha:
                intro = visible.strip()
                visible = (intro + "\n\n" + ficha) if intro else ficha
                reescribir_ultimo_turno_asistente(telefono, visible, db_path=conv_db)
            else:
                # 25-sep-2026 (caso 69): antes aca iba "Dame un segundo que reviso tu
                # pedido 🙏" -- una promesa que NADIE cumplia: el cliente espero 12 minutos
                # y tuvo que volver a preguntar. Ahora se le dice que falta, en concreto,
                # o si no se sabe, se avisa a los jefes para que alguien si le responda.
                extra = _mensaje_ficha_no_armada(previsualizacion, telefono, cfg, db_path=conv_db)
                if not visible.strip():
                    visible = extra
                elif extra and extra not in visible:
                    visible = visible.strip() + "\n\n" + extra
                reescribir_ultimo_turno_asistente(telefono, visible, db_path=conv_db)
        if pedido and not pedido.get("_error") and not pedido.get("derivar_humano"):
            # El envio que el modelo haya tomado del cliente se borra ANTES de todo lo
            # demas: ningun candado ni la comanda deben ver ese numero (30-jul-2026).
            envio_dictado = _sanear_envio_dictado(pedido)
            # --- candados de CODIGO antes de cocina (28-jul-2026) ---
            # 1. "solo retiro" / "se agoto X" dictados por el jefe.
            # 2. modalidad incoherente con la direccion (P8 de Christian: el pedido que
            #    salio "retiro" CON direccion y dejo a la clienta esperando en su casa).
            # Se chequean ANTES de decidir prueba/despacho, para ambos por igual: si el
            # jefe esta probando un escenario que el mismo dicto, quiere ver que el
            # candado responde igual que a un cliente real. No se pasa por el cerebro
            # para esta decision (ver candado_instrucciones/candado_modalidad, prototipo.py).
            # 0. PEDIDO DUPLICADO (05-ago-2026, pendiente #9). Va PRIMERO porque es el
            #    unico candado que protege plata ya gastada: los demas evitan un pedido
            #    mal tomado, este evita que la cocina prepare dos veces el mismo. El caso
            #    real: el cliente confirmo, el pedido se despacho, y su "si, confirmo"
            #    repetido hizo que el modelo llamara a emitir_pedido de nuevo.
            bloqueo = None
            despachado = _pedido_despachado_hace_poco(telefono)
            if despachado:
                resumen_previo, edad_min = despachado
                if _resumen_de_pedido(pedido).strip().lower() == resumen_previo.strip().lower():
                    # Exactamente el mismo pedido: no hay nada que un humano tenga que
                    # rescatar, asi que tampoco se molesta a los jefes.
                    # Caso 109 (02-oct-2026): si lo que disparo el reemitido fue un simple
                    # "yap, no hay problema" -- el cliente no pidio reenviar nada -- decirle
                    # "ese pedido ya lo tengo tomado" es una respuesta a una pregunta que
                    # nunca hizo. Esa aclaracion solo tiene sentido cuando el cliente SI
                    # intento reconfirmar de palabra (ej. "si, confirmo", caso original #9).
                    if _es_solo_acuse(texto):
                        bloqueo = MENSAJE_ACUSE_DUPLICADO
                    elif _es_solo_cortesia(texto):
                        bloqueo = MENSAJE_CIERRE_CORTESIA_DUPLICADO
                    else:
                        bloqueo = MENSAJE_PEDIDO_YA_TOMADO
                    _registrar_candado("duplicado_mismo", telefono, f"{int(edad_min)} min")
                    print(f"[candado_duplicado] {telefono}: MISMO pedido a los "
                          f"{int(edad_min)} min; no se despacha de nuevo.")
                else:
                    # Distinto: puede ser un agregado ("mandame tambien una bebida") o un
                    # pedido nuevo de verdad. Los dos van a una persona -- cambiar un
                    # pedido ya en preparacion es justamente lo que la regla 3.9 deriva --
                    # pero NO se pierde: los jefes reciben el detalle.
                    bloqueo = MENSAJE_SEGUNDO_PEDIDO_A_HUMANO
                    _registrar_candado("duplicado_distinto", telefono, f"{int(edad_min)} min")
                    ya_avisado_dup = any(
                        t.get("rol") == "asistente"
                        and MENSAJE_SEGUNDO_PEDIDO_A_HUMANO in (t.get("texto") or "")
                        for t in previos)
                    if not ya_avisado_dup:
                        _texto_aviso_dup = (
                            f"🔁 SEGUNDO PEDIDO de {pedido.get('cliente') or 'un cliente'} "
                            f"(+{telefono}) a los {int(edad_min)} min del anterior — NO entro "
                            f"a la cola, hay que tomarlo a mano.\n\n"
                            f"Antes: {resumen_previo}\nAhora: {_resumen_de_pedido(pedido)} · "
                            f"{pedido.get('direccion') or 'sin direccion'}")
                        _avisar_jefes(_texto_aviso_dup, cfg)
                        _registrar_caso_humano("pedido_duplicado", telefono, _texto_aviso_dup)
                    print(f"[candado_duplicado] {telefono}: pedido DISTINTO a los "
                          f"{int(edad_min)} min del anterior; lo toma una persona.")
            if not bloqueo:
                bloqueo = candado_instrucciones(pedido)
                if bloqueo:
                    _registrar_candado("instrucciones", telefono)
            if not bloqueo:
                bloqueo = candado_modalidad(pedido)
                if bloqueo:
                    _registrar_candado(
                        "modalidad_antibucle" if _ya_se_pregunto_modalidad(previos)
                        else "modalidad_pregunta", telefono)
                if bloqueo and _ya_se_pregunto_modalidad(previos):
                    # ANTI-BUCLE: ya se pregunto una vez en esta conversacion y el pedido
                    # sigue sin calzar. Insistir dejaria al cliente sin poder cerrar nunca
                    # (peor que el bug original: ahi al menos alguien cocinaba). Pasa a
                    # cocina, pero marcado para que una PERSONA lo confirme por telefono.
                    comentarios = (pedido.get("comentarios") or "").strip()
                    pedido["comentarios"] = ((comentarios + " | ") if comentarios else "") + \
                        prototipo.AVISO_MODALIDAD_SIN_ACLARAR
                    print(f"[candado_modalidad] {telefono}: preguntado sin aclarar; "
                          "despacho igual, marcado para el local.")
                    bloqueo = None
            # 3. pedido GRANDE (03-ago-2026, P28): desde UNIDADES_PEDIDO_GRANDE en una
            #    linea lo toma una persona. No va a la cola automatica.
            if not bloqueo:
                grande, detalle_grande = candado_pedido_grande(pedido)
                if grande:
                    bloqueo = grande
                    # El aviso al local se manda UNA sola vez por conversacion: el
                    # cliente no tiene nada que "corregir" aca, asi que cada mensaje
                    # suyo volveria a emitir el mismo pedido -- sin este guardia, los
                    # tres jefes reciben la misma alerta en loop (misma clase de bug
                    # que el bucle de modalidad, visto antes de que ocurriera).
                    ya_avisado = any(
                        t.get("rol") == "asistente"
                        and prototipo.MENSAJE_PEDIDO_GRANDE in (t.get("texto") or "")
                        for t in previos)
                    if not ya_avisado:
                        _texto_aviso_grande = (
                            f"🧾 PEDIDO GRANDE ({detalle_grande}) de {pedido.get('cliente') or 'un cliente'} "
                            f"(+{telefono}) — NO entro a la cola, hay que tomarlo a mano.\n\n"
                            f"Modalidad: {pedido.get('modalidad', '-')} · "
                            f"{pedido.get('direccion') or 'sin direccion'} · "
                            f"pago {pedido.get('forma_pago', '-')}")
                        _avisar_jefes(_texto_aviso_grande, cfg, db_path=conv_db)
                        _registrar_caso_humano("pedido_grande", telefono, _texto_aviso_grande,
                                               db_path=conv_db)
                        _registrar_candado("pedido_grande", telefono, detalle_grande,
                                           db_path=conv_db)
                        print(f"[candado_pedido_grande] {telefono}: {detalle_grande}; "
                              "no se despacha, avisados los jefes.")
                    # Avisado o ya avisado antes en esta conversacion: en los dos casos
                    # el candado del final no tiene que volver a sonarles el telefono.
                    humano_ya_avisado = True
            if bloqueo:
                visible = bloqueo   # NO se despacha, NO se limpia el historial:
                                    # el cliente sigue la conversacion para corregir.
                # El historial guarda lo que el cliente recibio (el mensaje del candado),
                # no la confirmacion que el modelo alcanzo a escribir -- si no, en el
                # turno siguiente el modelo cree que ya cerro el pedido y vuelve a cerrar.
                reescribir_ultimo_turno_asistente(telefono, bloqueo, db_path=conv_db)
            else:
                # El desmentido va ANTES del aviso de la cadena: el cliente lee primero
                # que el total de arriba no vale, y despues el numero bueno (o el "en un
                # momento te confirmo el envio"), no al reves.
                # Casos 147, 162 y 167 (curador): con transferencia el cierre es SIEMPRE
                # el fijo -- sin demora y sin "Confirmado", que significa que ya pasa a
                # cocina --, escriba lo que escriba el modelo; el aviso de abajo trae el
                # total y la cuenta. Va antes del aviso del envio para no pisarlo.
                if str(pedido.get("forma_pago") or "").lower() == "transferencia":
                    visible = prototipo.CONFIRMACION_CIERRE_TRANSFERENCIA
                    reescribir_ultimo_turno_asistente(telefono, visible, db_path=conv_db)
                if envio_dictado and pedido.get("modalidad") == "delivery":
                    visible += "\n\n" + AVISO_ENVIO_NO_LO_PONE_EL_CLIENTE
                resultado = _despachar_pedido(pedido, telefono, cfg=cfg, nucleo=nucleo)
                # El pedido puede quedar RETENIDO por transferencia (S2.5 #7) y el texto
                # del modelo ya cerro con "listo, aviso al local" -- mentira en ese caso.
                # Se corrige lo que el cliente lee, no lo que el modelo escribio; el aviso
                # viene armado desde la puerta, que es la que sabe el envio y el total.
                if isinstance(resultado, dict) and resultado.get("aviso_programado"):
                    # Confirmacion de hora agendada (P29): SIEMPRE sale, no compite con el
                    # dedup del total de abajo.
                    visible += "\n\n" + resultado["aviso_programado"]
                if isinstance(resultado, dict) and resultado.get("aviso_cliente"):
                    # El total del codigo NO se repite si el modelo ya escribio ESE mismo
                    # numero: ahi no aporta nada y deja dos totales en pantalla, que es
                    # justo el detalle sucio que se queria sacar. Si dijo otro (o ninguno,
                    # como en el chat de los 2 churrascos), el bueno va al final con ✅.
                    ya_dicho = resultado.get("total_anunciado")
                    if not (ya_dicho and _pesos(ya_dicho) in visible):
                        visible += "\n\n" + resultado["aviso_cliente"]
                # La MISMA firma que compara el candado de duplicados de arriba: se arma
                # en un solo lugar (_resumen_de_pedido) para que no puedan divergir.
                _guardar_ultimo_pedido(telefono, _resumen_de_pedido(pedido))
                limpiar_historial(telefono, db_path=conv_db)   # pedido cerrado: la proxima arranca fresca

    # --- CANDADO: la promesa de una persona tiene que llegarle a una persona ---
    # Va al FINAL y mira el texto que el cliente va a leer, no la estructura: es la
    # unica forma de que no dependa de que el modelo llame a la tool (ver el bloque de
    # _promete_humano). No se aplica al jefe: ahi no hay cliente real esperando, y
    # avisar seria ruido en el telefono de jefe1/jefe2.
    if not es_jefe and not humano_ya_avisado \
            and _promete_humano(visible) and not _ya_prometio_humano(previos):
        _avisar_humano_prometido(telefono, texto, visible, cfg, db_path=conv_db)
        humano_ya_avisado = True

    # --- CANDADO (28-sep-2026): si el modelo marco derivar_humano=true, SIEMPRE avisa
    # --- aunque el texto no haya prometido una persona (el candado de arriba, que SI
    # --- mira el texto, ya lo habria avisado si correspondia -- este es el respaldo).
    #
    # La regla 3.9 del prompt le pide al modelo que marque este flag en reclamos,
    # anulaciones, cambios de un pedido en preparacion, catering y pedidos muy grandes
    # -- y hasta hoy ese flag NO disparaba ningun aviso por si solo: solo servia para
    # (a) no despachar a cocina y (b) ocultar la nota en la ficha. Si el modelo marcaba
    # la tool correctamente pero escribia algo que no prometia una persona (sin
    # "alguien"/"persona"/"encargado" + un verbo de contacto), el pedido quedaba
    # retenido en silencio y nadie del local se enteraba. Encontrado revisando el
    # curador (28-sep): un cliente reclamo un pedido de otro dia ya pagado y el bot
    # goteo sin avisar a nadie (0 casos_humanos para ese telefono).
    #
    # Es la misma forma que el candado de arriba (06-ago): la señal ya es estructural
    # (el modelo la puso el mismo, via tool-call), asi que esto NO es un candado de
    # texto -- no hay heuristica que adivinar, solo conectar un flag que ya existia.
    if (not es_jefe and not humano_ya_avisado and pedido and pedido.get("derivar_humano")
            and not _hay_caso_humano_abierto(telefono, "derivar_humano", db_path=conv_db)):
        _texto_aviso_derivar = (
            "🙋 EL MODELO MARCÓ DERIVAR_HUMANO — hay que contestarle a mano:\n\n"
            f"Cliente: +{telefono}\n"
            f"Lo que escribió: {(texto or '')[:300]}\n\n"
            f"Lo que le contestó el bot:\n{(visible or '')[:400]}")
        _avisar_jefes(_texto_aviso_derivar, cfg, db_path=conv_db)
        _registrar_caso_humano("derivar_humano", telefono, _texto_aviso_derivar,
                               db_path=conv_db)
        _registrar_candado("derivar_humano", telefono, db_path=conv_db)
        print(f"[candado_derivar_humano] {telefono}: el modelo marco derivar_humano sin "
              "prometer una persona en el texto; avisados los jefes igual.")

    # Caso 167: lo guardado es lo enviado, con los avisos que se pegaron despues de
    # guardar (aunque la conversacion ya se haya archivado al cerrar el pedido).
    _fijar_texto_turno(_id_turno_respuesta, visible, db_path=conv_db)
    enviar_whatsapp(telefono, visible, cfg)
    return visible


def _pesos(monto):
    return f"${int(monto):,}".replace(",", ".")


def _mensaje_ficha_no_armada(previsualizacion, telefono, cfg, db_path=None):
    """Que decirle al cliente cuando la ficha no se pudo armar (caso 69, 25-sep-2026).

    Si el motor sabe QUE falta (un producto que no esta en el menu, o elegir churrasco
    o mechada), se le pregunta eso. Si no, se avisa a los jefes y se le dice al cliente
    que alguien del local le responde: nunca una espera sin nadie del otro lado.

    28-sep-2026: ademas de avisar por WhatsApp, ahora queda registrado como caso_humano
    -- antes este aviso dependia 100% de que alguien LEYERA el WhatsApp, mismo riesgo
    que ya se documento y arreglo el 15-sep para 'humano_prometido' (el WhatsApp se dio
    por leido y nadie lo leyo). Sin esto, el reclamo no queda visible en el curador."""
    cuenta = _cuenta_payload(previsualizacion) or {}
    items = [it for it in cuenta.get("items", []) if not it.get("ok")]
    no_esta = [it.get("producto") for it in items
               if "no esta en el menu" in (it.get("problema") or "")]
    if no_esta:
        nombres = ", ".join(f"«{n}»" for n in no_esta)
        return (f"No encontré {nombres} en nuestro menú 🙏 ¿Me dices cómo aparece en la "
                "carta, o qué lleva?")
    for it in items:
        m = re.search(r"falta variante \(opciones: ([^)]+)\)", it.get("problema") or "")
        if m:
            opciones = [label_variante(o.strip()) for o in m.group(1).split(",")]
            return f"¿El {it.get('producto')} lo quieres {' o '.join(opciones)}?"
    # Caso 63: "papas individual" -- ese tamaño no existe para ese producto. Se le
    # ofrecen los que SI hay, con su precio del menu.
    for it in items:
        if "sin precio" in (it.get("problema") or "") or "invalida" in (it.get("problema") or ""):
            opciones = _opciones_con_precio(it.get("producto"))
            if opciones:
                return (f"{it.get('producto')} viene en {opciones}. ¿Cuál prefieres?")
    _texto_aviso_ficha = (
        f"⚠️ No pude armar la ficha del pedido de +{telefono} "
        f"({'; '.join(str(it.get('problema')) for it in items) or 'sin detalle'}). "
        "Alguien tiene que responderle.")
    _avisar_jefes(_texto_aviso_ficha, cfg, db_path=db_path)
    _registrar_caso_humano("ficha_no_armada", telefono, _texto_aviso_ficha, db_path=db_path)
    return "Tuve un problema para armar el resumen de tu pedido. Ya le avisé al local y te escriben en un momento 🙏"


def _opciones_con_precio(producto):
    """'mediana ($2.500) o grande ($4.000)' para un producto con tamaños, o ''."""
    try:
        tar = Tarificador.desde_json(prototipo.menu_json_activo())
        cands = tar._por_prod.get(prototipo._norm(producto), [])
        if len(cands) != 1:
            return ""
        from tarificar import _variantes_validas, _precio_variante
        p = cands[0][1]
        partes = []
        for v in _variantes_validas(p):
            precio, _2x = _precio_variante(p, v)
            if precio:
                partes.append(f"{label_variante(v).lower()} ({_pesos(precio)})")
        return " o ".join(partes)
    except Exception:
        return ""


def _cuenta_payload(payload):
    """Cuenta COMPLETA del pedido (items tarificados + envio) con el MISMO motor de
    precios que usa el POS. Nunca el numero que dijo el modelo -- esa es la regla del
    proyecto desde el 24-jun-2026. Si el menu no se puede tarificar devuelve None, y los
    mensajes al cliente salen SIN total en vez de salir con uno inventado."""
    try:
        tar = Tarificador.desde_json(prototipo.menu_json_activo())
        return tar.tarificar_pedido({"items": payload.get("items", []),
                                     "delivery_cobro": payload.get("delivery_cobro", 0),
                                     "total": 0})
    except Exception as e:
        print(f"[total][AVISO] no pude tarificar para el aviso al cliente ({e}).")
        return None


def _total_payload(payload):
    """Solo el numero del total (ver _cuenta_payload)."""
    cuenta = _cuenta_payload(payload)
    return (cuenta["total"] or None) if cuenta else None


def _aviso_total(payload):
    """El total DEFINITIVO que el cliente lee al cerrar su pedido (30-jul-2026).

    Hasta ahora, en retiro (y en delivery con el envio ya resuelto) el UNICO total que
    veia el cliente era el que escribiera el modelo en el chat... si lo escribia. En un
    chat real de 2 Churrasco Italiano no escribio ninguno: el cliente confirmo sin saber
    cuanto pagaba. Y el numero de su bloque era $9.800 cuando el menu dice $8.900 (promo
    2x), asi que de haberlo dicho le habria cobrado $900 de mas. Ahora el total lo pone
    el CODIGO, con el mismo motor que cobra el POS, y va al final del mensaje de cierre.

    Solo lo usa el camino de ENTREGA: si el pedido queda esperando el cobro del envio
    todavia no hay total que decir, y si queda esperando la transferencia el total ya se
    lo dice _aviso_transferencia (que ademas aclara que no entro a cocina).

    Devuelve (texto, total) o None. El total va aparte para que quien pegue el aviso
    pueda callarselo si el mensaje YA dice ese mismo numero (ver procesar_mensaje)."""
    cuenta = _cuenta_payload(payload)
    if not cuenta or not cuenta.get("total"):
        return None
    if cuenta.get("problemas"):
        # tarificar_pedido suma SOLO los items que reconocio: con un producto que el menu
        # no encuentra, el total sale INCOMPLETO -- o sea menor al que le van a cobrar.
        # Callarse es mejor que prometer de menos; el POS lo recalcula igual al preparar.
        print(f"[total][AVISO] no anuncio total: {cuenta['problemas']}")
        return None
    texto = f"✅ Total: {_pesos(cuenta['total'])}"
    if any((it.get("pares_promo") or 0) > 0 for it in cuenta.get("items") or []):
        texto += " (ya con la promo 2x aplicada 🙌)"
    return texto, cuenta["total"]


# Las variantes del menu son claves internas ("xl_30cm"): al cliente se le muestra como
# las dice el local. Lo que no este aca no se muestra (mejor sin variante que con jerga).
_VARIANTE_LEGIBLE = {
    "xl_30cm": "XL", "normal_22cm": "Normal",
    "individual": "individual", "mediana": "mediana", "grande": "grande",
    "6": "x6", "10": "x10",
    # 07-oct-2026 (caso 123): los Especiales se piden con base churrasco o mechada
    # (categoria "Especiales" del menu). Sin esto la ficha decia "Especial Barros Champi"
    # a secas y ni el cliente ni la cocina veian cual de las dos era.
    "churrasco": "churrasco", "mechada": "mechada",
}


def _productos_ficha(cuenta):
    """Las lineas de producto de la ficha ("1 Papas Fritas grande ($4.000)"), tal cual las
    lee el cliente. Una sola funcion para la ficha y para el candado del caso 118, que
    compara la ficha nueva con la que el cliente ya vio: si cada una armara sus lineas por
    su lado, un cambio de formato en una haria creer que el pedido cambio."""
    productos = []
    for it in cuenta["items"]:
        if not it.get("ok"):
            continue
        nom = it.get("producto") or "?"
        var = _VARIANTE_LEGIBLE.get(str(it.get("variante") or "").lower())
        if var:
            nom += f" {var}"
        productos.append(f"{it.get('cantidad', 1)} {nom} ({_pesos(it.get('subtotal', 0))})")
    return productos


_RE_TOTAL_FICHA = re.compile(r"^Total(?: con delivery)? \$([\d.]+)\.$", re.MULTILINE)


def _cierre_por_dato_de_pago(previsualizacion, previos, texto):
    """Caso 118 del curador (05-oct-2026): si el mensaje anterior del bot FUE la ficha, el
    cliente contesta SOLO el dato de pago que faltaba y el modelo, en vez de cerrar, vuelve
    a pedir la ficha con el MISMO pedido, el codigo cierra: devuelve el pedido listo para
    emitir_pedido. None en cualquier otro caso, y el turno sigue como siempre.

    Todas las condiciones son de codigo y se exigen juntas (ante la duda, no cierra):
      1. el ultimo turno del bot es la ficha ("•Es delivery o retiro?" + "¿Está bien así");
      2. el mensaje es solo forma de pago y/o con cuanto paga (prototipo.dato_de_pago_solo);
      3. la ficha nueva trae exactamente las mismas lineas de producto, la misma modalidad
         y, en delivery, la misma direccion que la que el cliente ya vio;
      4. hay forma de pago, y es la que dijo el cliente;
      5. si dijo con cuanto paga, el monto alcanza para el total que vio (si no alcanza, o
         el total no se puede leer, lo resuelve el modelo con el cliente).
    Por que en codigo y no solo en el prompt: el 07-oct se gastaron ~2.700 llamadas
    midiendo variantes del prompt y cada una que cerraba el efectivo movia otro escenario
    (retiro_simple 16/20). Replay del turno real el 12-oct (10 corridas c/u): con el
    prompt del 05-oct el modelo volvio a pedir la ficha 10/10 y este candado cerro las 10;
    con el prompt vivo (3721564) el modelo ya cerro solo 9/9 y el candado no se disparo.
    Queda como red: si el modelo vuelve a repetir la ficha, el cierre no depende de el."""
    if not previsualizacion or previsualizacion.get("_error") or previsualizacion.get("derivar_humano"):
        return None
    if not previos or previos[-1].get("rol") != "asistente":
        return None
    ficha_vista = previos[-1].get("texto") or ""
    if "•Es delivery o retiro?:" not in ficha_vista or "esta bien asi" not in _sin_tildes_min(ficha_vista):
        return None
    dato = prototipo.dato_de_pago_solo(texto)
    if not dato:
        return None
    forma = str(previsualizacion.get("forma_pago") or "").strip().lower()
    if forma != dato["forma"]:
        return None
    cuenta = _cuenta_payload(previsualizacion)
    if not cuenta or not cuenta.get("items") or any(not it.get("ok") for it in cuenta["items"]):
        return None
    productos = _productos_ficha(cuenta)
    lineas_vistas = [l.strip() for l in ficha_vista.split("\n")]
    if len(productos) == 1:
        if f"•Pedido: {productos[0]}" not in lineas_vistas:
            return None
    else:
        vistos = [l[2:] for l in lineas_vistas if l.startswith("- ")]
        if "•Pedido:" not in lineas_vistas or vistos != productos:
            return None
    es_delivery = str(previsualizacion.get("modalidad") or "").strip().lower() == "delivery"
    if f"•Es delivery o retiro?: {'Delivery' if es_delivery else 'Retiro'}" not in lineas_vistas:
        return None
    if es_delivery:
        direccion = (previsualizacion.get("direccion") or "").strip()
        if not direccion or not any(l.startswith(f"•Dirección (si es Delivery): {direccion}")
                                    for l in lineas_vistas):
            return None
    if dato["monto"] is not None:
        m = _RE_TOTAL_FICHA.search(ficha_vista)
        if not m or dato["monto"] < int(m.group(1).replace(".", "")):
            return None
    pedido = {k: v for k, v in previsualizacion.items() if k != "_tool"}
    pedido["_tool"] = "emitir_pedido"
    if dato["monto"] is not None:
        comentarios = (pedido.get("comentarios") or "").strip()
        if str(dato["monto"]) not in re.sub(r"[^\d]", "", comentarios):
            pedido["comentarios"] = ((comentarios + " | ") if comentarios else "") + \
                f"Paga con {_pesos(dato['monto'])}"
    return pedido


def _ficha_confirmacion(pedido, telefono, cfg):
    """PASO 1 del cierre: la ficha que el cliente lee ANTES de confirmar (05-ago-2026,
    formato pedido por Gonza con un ejemplo real).

        •Es delivery o retiro?: Delivery
        •Direccion (si es Delivery): Cancion de la Pampa 5727 ($1.500)
        •Pedido: 1 Churrasco Italiano ($4.900)
        •Forma de pago: Tarjeta

        Total con delivery $6.400.

        ¿Esta bien asi, o quieres agregar/modificar algo?

    La escribe el CODIGO, no el modelo, y por eso cada peso es verdad: los items salen
    del mismo Tarificador que cobra la caja y el envio de la planilla por calle
    (_cobro_delivery: planilla -> historial del POS). El modelo solo aporta la linea de
    introduccion y QUE pidio el cliente.

    Fail-safe en dos puntos, ninguno inventa un numero:
      - envio desconocido (calle fuera de la planilla y sin historial): se muestra el
        total de los productos y se dice que el envio lo confirma el local.
      - producto que el menu no reconoce: no se muestra total (misma regla que
        _aviso_total -- prometer de menos es peor que no prometer).

    La linea de NOMBRE solo aparece si hay un nombre de verdad (el registrado del POS o
    el que dio el cliente). El ejemplo de Gonza no la trae: un cliente nuevo que no lo
    dijo no tiene por que ver una linea vacia. Al despachar, `_payload_pedido` ya pone
    "Cliente WhatsApp" cuando no hay nombre, asi que la comanda nunca queda rota.

    Devuelve el texto, o None si no hay nada confiable que mostrar (ahi el turno sigue
    como una respuesta normal del modelo)."""
    cuenta = _cuenta_payload(pedido)
    if not cuenta or not cuenta.get("items"):
        return None
    es_delivery = str(pedido.get("modalidad") or "").strip().lower() == "delivery"
    direccion = (pedido.get("direccion") or "").strip()
    envio = _cobro_delivery(telefono, direccion, cfg) if (es_delivery and direccion) else None

    # Orden fijado por Gonza (05-ago-2026): modalidad, direccion, NOMBRE, pedido, pago.
    # El nombre va DESPUES de la direccion, no arriba.
    lineas = [f"•Es delivery o retiro?: {'Delivery' if es_delivery else 'Retiro'}"]
    if es_delivery:
        detalle_dir = direccion or "(nos falta tu dirección)"
        if envio:
            detalle_dir += f" ({_pesos(envio)})"
        lineas.append(f"•Dirección (si es Delivery): {detalle_dir}")
    nombre = (pedido.get("cliente") or "").strip()
    if not _es_nombre_placeholder(nombre):
        lineas.append(f"•Nombre: {nombre}")
    # 25-sep-2026 (caso 65): la referencia para encontrar al cliente ("Colegio Mater
    # Dolorosa, entregar a Martina Rivas 7°B") viajaba a la comanda pero el cliente no
    # la veia, y no podia corregirla. Ahora va en la ficha. No se muestra cuando es una
    # explicacion interna (derivacion a humano o rescate de la red de seguridad).
    nota = (pedido.get("comentarios") or "").strip()
    if (nota and not pedido.get("derivar_humano")
            and "red de seguridad" not in nota.lower()):
        lineas.append(f"•Nota: {nota}")
    # 25-sep-2026 (caso 63): antes un producto que el motor no podia cobrar ("Papas
    # Fritas individual", tamaño que no existe) se SALTABA en silencio: el cliente
    # confirmo una ficha sin sus papas y sin total. Ahora, si algo no se puede cobrar, no
    # hay ficha: _mensaje_ficha_no_armada le pregunta al cliente que falta.
    if any(not it.get("ok") for it in cuenta["items"]):
        return None
    productos = _productos_ficha(cuenta)
    if not productos:
        return None
    if len(productos) == 1:
        lineas.append(f"•Pedido: {productos[0]}")
    else:
        lineas.append("•Pedido:")
        lineas += [f"   - {p}" for p in productos]
    # 29-sep-2026 (pedido de Gonza): la forma de pago YA NO es requisito para
    # previsualizar (ver TOOL_PREVISUALIZAR_PEDIDO en prototipo.py) -- si el cliente
    # todavia no la dijo, no se muestra la linea a medias: se pregunta mas abajo, junto
    # al total, para que decida viendo el numero real.
    # Caso 162 (curador): la nota de cada producto ("sin cebolla") ya viajaba a la comanda;
    # ahora el cliente tambien la ve en la ficha y puede confirmarla.
    _comentarios = prototipo.linea_comentarios_ficha(pedido.get("items"))
    if _comentarios:
        lineas.append(_comentarios)
    pago = (pedido.get("forma_pago") or "").strip()
    if pago:
        lineas.append(f"•Forma de pago: {pago.capitalize()}")

    partes = ["\n".join(lineas)]
    if cuenta.get("problemas"):
        print(f"[previsualizacion][AVISO] sin total: {cuenta['problemas']}")
    else:
        total_productos = cuenta["total"]      # _cuenta_payload no suma envio (delivery_cobro=0)
        if es_delivery and envio:
            partes.append(f"Total con delivery {_pesos(total_productos + envio)}.")
        elif es_delivery:
            partes.append(f"Total de los productos {_pesos(total_productos)}, más el envío "
                          "que te confirmamos según tu dirección.")
        else:
            partes.append(f"Total {_pesos(total_productos)}.")
    # 29-sep-2026 (pedido de Gonza): si todavia no sabemos como paga, la pregunta va
    # junto a esta misma ficha -- para que el cliente decida viendo el total real, y la
    # confirmacion salga de esta misma respuesta, sin un mensaje aparte antes de ver el
    # numero.
    if not pago:
        partes.append("¿Está bien así, o quieres agregar/modificar algo? ¿Y cómo prefieres "
                      "pagar: efectivo, tarjeta o transferencia?")
    # 25-sep-2026 (caso 60), ajustado 29-sep-2026 (caso 93 del curador): en efectivo el
    # local necesita saber con cuanto paga, y el cliente solo puede decirlo cuando ya vio
    # el total -- por eso la pregunta va aca, junto al total, y no antes. Pero SOLO en
    # delivery: en retiro el cliente paga en caja al llegar, ahi el vuelto no importa
    # (Christian lo marco como error en el caso 93: "si es retiro no importa llevar
    # vuelto, el cliente se acerca a la caja").
    elif pago.lower() == "efectivo" and es_delivery and not re.search(
            r"\d", pedido.get("comentarios") or ""):
        partes.append("¿Está bien así, o quieres agregar/modificar algo? Y como pagas en "
                      "efectivo, ¿con cuánto pagas, para llevarte el vuelto?")
    else:
        partes.append("¿Está bien así, o quieres agregar/modificar algo?")
    # PASO 1 emitido: de aca en adelante lo que se espera es un si/no, y el debounce
    # tiene que ser corto (2 s, no 8). Es la bandera EXPLICITA del plan -- se anota al
    # emitir la ficha, en vez de deducir despues la etapa mirando el texto.
    _guardar_etapa(telefono, ETAPA_CONFIRMACION)
    return "\n\n".join(partes)


def _aviso_transferencia(payload):
    """Lo que el cliente necesita para PODER transferir: cuanto es el envio, cuanto es
    el total, y que todavia no entra a cocina. Se arma DESPUES de resolver el envio, asi
    que el monto que se le pide ya es el definitivo."""
    partes = []
    cobro = payload.get("delivery_cobro") or 0
    if cobro:
        partes.append(f"El envío a tu dirección es {_pesos(cobro)}.")
    total = _total_payload(payload)
    if total:
        partes.append(f"Total a transferir: {_pesos(total)}.")
    # Casos 147 y 162 (curador, 09/12-oct-2026): el aviso decia cuanto pero no a que
    # cuenta; el cliente del 162 tuvo que preguntar "¿y los datos de transferencia?".
    datos = prototipo.datos_transferencia(_menu_activo_dict())
    if datos:
        partes.append(datos)
    # 29-sep-2026 (Christian, curador -- dos casos de transferencias): el bot NO lee
    # imagenes (nunca las interpreta, solo detecta que llego un adjunto -- ver el
    # comentario en el manejo de mensajes tipo "image"/"document"). Mencionar el
    # pantallazo, aunque fuera "opcional", sonaba a que el bot SI podia revisarlo. Ahora
    # no se pide ni se insinua: solo se le dice que transfiera y avise por aca, para que
    # el local reciba la solicitud de revision. Si el cliente igual manda la foto, se
    # sigue aceptando como aviso (ver el manejo de "image"/"document" mas abajo) -- lo
    # que cambia es que el bot ya no la ofrece como opcion.
    # Caso 167 (14-oct-2026): el flujo dicho por Gonza -- el pedido espera la validacion
    # del pago y pasa a cocina cuando un jefe la confirma; ahi se le avisa al cliente.
    partes.append("Apenas el local confirme la transferencia, pasa a cocina y te aviso.")
    return " ".join(partes)


def _despachar_pedido(pedido, telefono, cfg=None, nucleo=None):
    """Punto de entrada del despacho: traduce el PEDIDO_JSON y arranca la cadena
    ENVIO -> TRANSFERENCIA -> COMANDA, en ese orden (ver _entregar_o_retener)."""
    return _continuar_despacho(_payload_pedido(pedido, telefono), telefono, cfg, nucleo=nucleo)


def _entregar_o_retener(payload, telefono, cfg=None, nucleo=None):
    """ULTIMA puerta antes de la comanda: si el pago es por transferencia y nadie vio
    todavia el comprobante, retiene (S2.5 #7); si no, entrega.

    29-jul-2026 -- EL ORDEN ESTABA AL REVES. La transferencia se retenia ANTES de
    resolver el cobro del envio, asi que al cliente se le pedia transferir un total que
    todavia no incluia el envio (el que el modelo hubiera adivinado en la conversacion) y
    quedaba "esperando el valor del delivery" DESPUES de haber pagado. Ahora el envio se
    resuelve primero y esta puerta es lo ultimo de la cadena: cuando se pide la
    transferencia, el monto ya es el definitivo.

    `pago_confirmado` en el payload es el candado anti-bucle: los resolvedores del jefe
    lo marcan antes de reentrar, para que la puerta no vuelva a retener lo mismo."""
    if payload.get("forma_pago") == "transferencia" and not payload.get("pago_confirmado"):
        _guardar_esperando_comprobante(payload, telefono)
        _avisar_jefes(
            f"💳 CONFIRMAR TRANSFERENCIA — responde 'confirmado' cuando veas el pantallazo:\n\n"
            f"{_resumen_pedido_para_jefe(payload)}\n\n"
            f"(Si nadie responde en {ESPERA_COMPROBANTE_MINUTOS} min se lo paso al jefe de "
            f"respaldo, y {ESPERA_BACKUP_MINUTOS} min después entra a cocina igual, marcado "
            "para que lo verifiques al preparar.)", cfg)
        return {"esperando_comprobante": True, "aviso_cliente": _aviso_transferencia(payload)}
    # ENTREGA: el pedido entra a cocina y recien aca se conoce el total definitivo (el
    # envio, si habia, ya se resolvio antes en la cadena). El aviso viaja en el retorno
    # -- no se manda desde aca -- para que lo lea quien tenga el contexto del mensaje:
    # procesar_mensaje lo pega al cierre que ve el cliente, resolver_cobro_jefe al aviso
    # del envio. Si el POS devuelve algo que no es dict, igual se envuelve para no
    # perder el aviso (nadie usa ese retorno mas que por .get()).
    r = _entregar_payload(payload, telefono, cfg, nucleo=nucleo)
    aviso = _aviso_total(payload)
    if aviso:
        r = dict(r) if isinstance(r, dict) else {"resultado": r}
        r["aviso_cliente"], r["total_anunciado"] = aviso
    # 04-ago-2026 (P29): si el cliente pidio una hora, se lo confirmamos -- por eso pidio
    # agendar en primer lugar. Va en una CLAVE APARTE (no adentro de aviso_cliente): ese
    # campo se puede suprimir si el modelo ya dijo el mismo total (para no repetirlo en
    # pantalla), y esta confirmacion tiene que salir SIEMPRE, la haya dicho el modelo o no.
    if payload.get("hora_programada"):
        r = dict(r) if isinstance(r, dict) else {"resultado": r}
        r["aviso_programado"] = f"🟣 Quedó agendado para las {payload['hora_programada']} 🙌"
    return r


def _cobro_delivery(telefono, direccion, cfg):
    """Precedencia de cobro decidida por Gonza (3-ago-2026): 1º la planilla de tarifas
    por calle (`delivery_zonas.cobro_por_calle`, exige numero DENTRO del rango -- nunca
    calza solo por nombre) -- 2º el historial de direcciones que el POS guarda por
    cliente (`_buscar_cobro_direccion`) -- 3º ninguna de las dos sabe, se le pregunta
    al jefe (lo maneja el llamador). La planilla manda porque es la fuente mas nueva y
    cubre direcciones que el cliente nunca pidio antes; el historial del POS sigue
    sirviendo para las que la planilla no cubre (huecos, calles nuevas, etc)."""
    cobro = cobro_por_calle(direccion)
    if cobro:
        return cobro
    # 03-ago-2026 (pregunta 1, Gonza dijo 'si'): si la planilla no la cubre, se cuenta
    # -- para avisar cuando la MISMA calle se repite, y valga la pena agregarla. No
    # bloquea el flujo normal (sigue cayendo al historial del POS / al jefe).
    if direccion:
        _registrar_calle_sin_planilla(direccion)
    return _buscar_cobro_direccion(telefono, direccion, cfg or {})


def _continuar_despacho(payload, telefono, cfg=None, nucleo=None):
    """Paso 1 de la cadena: el COBRO DEL ENVIO. Delivery con direccion conocida usa el
    cobro guardado; sin cobro conocido queda RETENIDO esperando al jefe (con el timeout
    anti-limbo del hilo). Retiro/local no pagan envio y pasan derecho. En los tres casos
    el paso siguiente es _entregar_o_retener, nunca la comanda directa."""
    if payload["modalidad"] == "delivery":
        cobro = _cobro_delivery(telefono, payload.get("direccion"), cfg)
        if cobro:
            payload["delivery_cobro"] = cobro
            r = _entregar_o_retener(payload, telefono, cfg, nucleo=nucleo)
            if isinstance(r, dict) and r.get("esperando_comprobante"):
                return r   # el aviso de transferencia YA dice el envio y el total
            # 30-jul-2026: la condicion de arriba miraba "aviso_cliente", que hasta hoy
            # solo existia en la rama de transferencia. Ahora la ENTREGA tambien trae uno
            # (el total), asi que el discriminador honesto es esperando_comprobante -- con
            # el anterior, este mensaje del envio habria dejado de mandarse.
            enviar_al_cliente(telefono,
                            f"🛵 El envío a tu dirección es {_pesos(cobro)}."
                            " Ya está todo en marcha 🙌", cfg)
            return r
        _guardar_esperando_cobro(payload, telefono)
        enviar_al_cliente(telefono, "🛵 ¡Pedido tomado! En un momento te confirmo el valor "
                        "del envío a tu dirección" +
                        (", y ahí te digo el total para que transfieras 🙌"
                         if payload.get("forma_pago") == "transferencia" else "."), cfg)
        _avisar_jefes(
            f"🛵 COBRO DE ENVÍO — responde 'envío <monto>' para despachar:\n\n"
            f"{_resumen_pedido_para_jefe(payload)}\n\n"
            f"(Si nadie responde en {ESPERA_COBRO_MINUTOS} min se lo paso al jefe de "
            f"respaldo, y {ESPERA_BACKUP_MINUTOS} min después entra a cocina con ENVÍO "
            "POR DEFINIR.)", cfg)
        return {"esperando_cobro": True}
    return _entregar_o_retener(payload, telefono, cfg, nucleo=nucleo)


MENSAJE_TIPO_NO_SOPORTADO = ("Por ahora solo puedo leer mensajes de texto 🙏 "
                              "¿Me cuentas en palabras qué quieres pedir?")


def _responder_tipo_no_soportado(telefono, cfg):
    """Respuesta minima para audio/foto/ubicacion/sticker/video/documento, etc.
    (27-jul-2026, S2 del plan). Antes se descartaban con 'continue': el cliente
    quedaba en 'visto' sin ninguna senal de que su mensaje no llego a nadie. Es
    una respuesta FIJA, sin pasar por el cerebro (no hay nada que interpretar). Desde el
    caso 167 (14-oct-2026) queda guardada como todo lo que se le manda a un cliente."""
    enviar_al_cliente(telefono, MENSAJE_TIPO_NO_SOPORTADO, cfg)
    return MENSAJE_TIPO_NO_SOPORTADO


# ---------------------------------------------------------------------------
# DEBOUNCE DE MENSAJES ENTRANTES (10-ago-2026 — diseño en PLAN_DEBOUNCE_MENSAJES.md)
# ---------------------------------------------------------------------------
# Un cliente dicta el pedido en cuatro mensajes cortos ("hola" / "2 churrascos italianos"
# / "a Los Aromos 456" / "efectivo") y el bot contestaba CADA uno por separado: cuatro
# llamadas al cerebro y cuatro respuestas, cada una decidida sin ver lo que el cliente
# estaba terminando de escribir. El cliente veia un bot atropellado que le preguntaba la
# direccion justo cuando el la acababa de mandar. Ahora se espera a que deje de escribir
# y se llama al modelo UNA vez, con todo junto.
#
# 🔴 EL BUFFER VIVE ACA, EN LA CAPA DE ENTRADA, Y NUNCA DENTRO DE procesar_mensaje.
# Los 483 candados de --test y los 14 escenarios de banco_pruebas.py llaman a
# procesar_mensaje DIRECTO. Con el buffer adentro, toda la verificacion del proyecto
# pasaria a medir el temporizador en vez de medir el bot, y se pierde la unica red que
# hay. Por eso 'debounce' es un parametro que viene APAGADO por defecto: lo prende solo
# _procesar_en_segundo_plano, que es el unico camino de produccion. Todo lo que llama a
# procesar_payload_meta a mano sigue siendo sincrono, igual que siempre.

# La espera depende de la ETAPA, no del cliente. El mismo cliente cambia de ritmo dentro
# de una conversacion: dicta el pedido en cuatro mensajes y despues contesta "si" de a
# uno. Un promedio por telefono le pondria la espera larga justo al "si", que es cuando
# el bot tiene que ser instantaneo. La etapa predice el ritmo; el cliente solo lo matiza.
DEBOUNCE_PEDIDO_SEG = 8          # dictando el pedido: es donde fragmenta
DEBOUNCE_CONFIRMACION_SEG = 2    # espera un si/no del Paso 1: la respuesta es una palabra
DEBOUNCE_CONOCIDO_SEG = 4        # cliente registrado: ya no dicta direccion, manda menos
# Umbrales SOLO para leer la medicion de fragmentacion (ver fragmentacion_cliente).
# No cambian ninguna espera: hoy nada consulta esta tabla para decidir. 1.5 mensajes por
# rafaga = una de cada dos veces escribe partido. Con menos de 3 rafagas no se juzga a
# nadie: un cliente que escribio una sola vez, partido, no es "un cliente que fragmenta".
CORTE_FRAGMENTADOR = 1.5
MIN_RAFAGAS_PARA_JUZGAR = 3
ETAPA_CONFIRMACION = "confirmacion"
# Los jefes NO pasan por el buffer: un 'envio 1500' o un 'confirmado' libera un pedido
# retenido en cocina, y sumarle 8 s a la operacion del local es costo real sin beneficio.
# 13-sep-2026: se arma directo de ROLES_JEFE (jefe1 + jefe2) en vez de listar los
# numeros a mano, para no volver a desincronizarse de quien es jefe hoy.
_JEFES_SIN_BUFFER = frozenset(_norm_telefono(n) for n in ROLES_JEFE)


class _ProgramadorReal:
    """threading.Timer detras de una interfaz minima, para poder cambiarlo por un reloj
    falso en --test. El criterio de "listo" del plan pide candados SIN sleep real: un
    test que duerme 8 s por caso convierte una suite de 30 s en uno de minutos, y una
    suite que nadie corre no protege nada."""

    def programar(self, segundos, fn):
        t = threading.Timer(segundos, fn)
        t.daemon = True
        t.start()
        return t


_PROGRAMADOR = _ProgramadorReal()
_TIMERS_DEBOUNCE = {}
_TIMERS_GUARD = threading.Lock()


def _guardar_etapa(telefono, etapa, db_path=None):
    """Anota (o borra, con etapa='') en que parte de la conversacion esta el cliente.

    Es la opcion (a) del plan: bandera EXPLICITA al emitir la ficha del Paso 1, en vez de
    deducir la etapa mirando el texto del ultimo mensaje del asistente. Deducirla del
    texto es la misma clase de heuristica que ya se rompio con el candado de montos."""
    tel = _norm_telefono(telefono)
    con = _con_respaldo(db_path)
    try:
        if not etapa:
            con.execute("DELETE FROM etapa_cliente WHERE telefono=?", (tel,))
        else:
            con.execute(
                "INSERT INTO etapa_cliente (telefono, etapa, actualizado_en) VALUES (?,?,?) "
                "ON CONFLICT(telefono) DO UPDATE SET etapa=excluded.etapa, "
                "actualizado_en=excluded.actualizado_en",
                (tel, etapa, datetime.now().isoformat(timespec="seconds")))
        con.commit()
    except Exception as e:
        print(f"[debounce][AVISO] no se pudo guardar la etapa de {tel}: {e}")
    finally:
        con.close()


def _etapa_de(telefono, db_path=None):
    con = _con_respaldo(db_path)
    try:
        fila = con.execute("SELECT etapa FROM etapa_cliente WHERE telefono=?",
                           (_norm_telefono(telefono),)).fetchone()
        return fila[0] if fila else None
    except Exception:
        return None
    finally:
        con.close()


def _espera_debounce(telefono, db_path=None):
    """Segundos de silencio a esperar antes de contestarle a este telefono."""
    if _etapa_de(telefono, db_path) == ETAPA_CONFIRMACION:
        return DEBOUNCE_CONFIRMACION_SEG
    if _datos_cliente_registrado(telefono):
        return DEBOUNCE_CONOCIDO_SEG
    return DEBOUNCE_PEDIDO_SEG


def _encolar_entrante(telefono_meta, texto, llegado_en, db_path=None):
    """Mete el mensaje al buffer persistido. Devuelve cuantos quedaron esperando."""
    tel = _norm_telefono(telefono_meta)
    con = _con_respaldo(db_path)
    try:
        con.execute("INSERT INTO buffer_entrante (telefono, telefono_meta, texto, "
                    "llegado_en) VALUES (?,?,?,?)",
                    (tel, str(telefono_meta), texto, llegado_en.isoformat()))
        con.commit()
        return con.execute("SELECT COUNT(*) FROM buffer_entrante WHERE telefono=?",
                           (tel,)).fetchone()[0]
    finally:
        con.close()


def _registrar_fragmentacion(telefono, cuantos, con):
    """Anota que a este telefono se le juntaron `cuantos` mensajes en una sola rafaga.

    Recibe la conexion ABIERTA a proposito: lo llama _tomar_buffer, que ya la tiene y ya
    esta adentro del lock del telefono. Abrir una segunda conexion a la misma base desde
    el mismo hilo es pedir un 'database is locked' a cambio de nada.

    Medir NO es ajustar: nadie lee todavia esta tabla para decidir la espera. Primero
    juntar semanas de trafico real, despues discutir el umbral con numeros a la vista.
    """
    if cuantos < 1:
        return
    ahora = datetime.now().isoformat(timespec="seconds")
    try:
        con.execute(
            "INSERT INTO fragmentacion_cliente (telefono, rafagas, mensajes, "
            "rafagas_fragmentadas, max_rafaga, primera_vez, ultima_vez) "
            "VALUES (?,1,?,?,?,?,?) "
            "ON CONFLICT(telefono) DO UPDATE SET "
            "  rafagas = rafagas + 1, "
            "  mensajes = mensajes + excluded.mensajes, "
            "  rafagas_fragmentadas = rafagas_fragmentadas + excluded.rafagas_fragmentadas, "
            "  max_rafaga = MAX(max_rafaga, excluded.max_rafaga), "
            "  ultima_vez = excluded.ultima_vez",
            (_norm_telefono(telefono), cuantos, 1 if cuantos > 1 else 0, cuantos, ahora, ahora))
        con.commit()
    except Exception as e:
        # Un contador jamas puede voltear una respuesta al cliente: si falla, se pierde
        # el dato y el pedido sigue su camino.
        print(f"[debounce][AVISO] no se pudo anotar la fragmentacion de {telefono}: {e}")


def listar_fragmentacion(db_path=None, minimo_rafagas=1):
    """Cuanto fragmenta cada telefono, del que mas al que menos (para el ajuste por
    cliente). Lleva telefono: se sirve por /fragmentacion, que esta detras del token del
    panel -- NUNCA por /salud, que es abierto."""
    con = _con_respaldo(db_path)
    try:
        filas = con.execute(
            "SELECT telefono, rafagas, mensajes, rafagas_fragmentadas, max_rafaga, "
            "primera_vez, ultima_vez FROM fragmentacion_cliente WHERE rafagas >= ? "
            "ORDER BY (CAST(mensajes AS REAL) / rafagas) DESC, mensajes DESC",
            (minimo_rafagas,)).fetchall()
        return [{"telefono": f[0], "rafagas": f[1], "mensajes": f[2],
                 "rafagas_fragmentadas": f[3], "max_rafaga": f[4],
                 "promedio": round(f[2] / f[1], 2) if f[1] else 0,
                 "primera_vez": f[5], "ultima_vez": f[6]}
                for f in filas]
    finally:
        con.close()


def resumen_fragmentacion(db_path=None):
    """El MISMO dato sin un solo telefono, para /salud (endpoint abierto).

    Responde de una mirada si el debounce esta agrupando algo o no: si casi todas las
    rafagas son de 1 mensaje, el problema que vino a resolver no existe con este
    trafico; si hay clientes con promedio alto, ahi esta el ajuste por cliente."""
    con = _con_respaldo(db_path)
    try:
        fila = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(rafagas),0), COALESCE(SUM(mensajes),0), "
            "COALESCE(SUM(rafagas_fragmentadas),0), COALESCE(MAX(max_rafaga),0) "
            "FROM fragmentacion_cliente").fetchone()
        clientes, rafagas, mensajes, fragmentadas, peor = fila
        fragmentadores = con.execute(
            "SELECT COUNT(*) FROM fragmentacion_cliente WHERE rafagas >= ? "
            "AND (CAST(mensajes AS REAL) / rafagas) >= ?",
            (MIN_RAFAGAS_PARA_JUZGAR, CORTE_FRAGMENTADOR)).fetchone()[0]
        return {
            "clientes": clientes,
            "rafagas": rafagas,
            "mensajes": mensajes,
            "rafagas_fragmentadas": fragmentadas,
            "mensajes_por_rafaga": round(mensajes / rafagas, 2) if rafagas else 0,
            "rafaga_mas_larga": peor,
            # Cuantos clientes fragmentan lo bastante seguido como para merecer una
            # espera propia. Es el numero que decide si el ajuste por cliente vale la pena.
            "clientes_que_fragmentan": fragmentadores,
        }
    finally:
        con.close()


def _tomar_buffer(telefono, db_path=None):
    """Vacia el buffer de ese telefono y devuelve (texto_junto, telefono_meta, llegada).

    La hora que vuelve es la del PRIMER mensaje, no la de ahora: es la que vale para el
    corte de las 23:50 (ver _toma_pedidos). Un mensaje que llego 23:49:55 y se procesa
    23:50:03 fue escrito DENTRO del horario, y el cliente no tiene por que pagar los 8 s
    que el bot decidio esperar.

    Devuelve (None, None, None) si no habia nada."""
    tel = _norm_telefono(telefono)
    con = _con_respaldo(db_path)
    try:
        filas = con.execute(
            "SELECT id, telefono_meta, texto, llegado_en FROM buffer_entrante "
            "WHERE telefono=? ORDER BY id", (tel,)).fetchall()
        if not filas:
            return None, None, None
        marcas = ",".join("?" * len(filas))
        con.execute(f"DELETE FROM buffer_entrante WHERE id IN ({marcas})",
                    [f[0] for f in filas])
        con.commit()
        # Aca -- y no en _encolar_entrante -- porque una RAFAGA es lo que se descarga
        # junto: encolar cuenta mensajes sueltos y no sabe cuantos van a terminar
        # respondiendose de una sola vez, que es justo el dato que se busca.
        _registrar_fragmentacion(tel, len(filas), con)
    finally:
        con.close()
    texto = "\n".join(f[2] for f in filas if (f[2] or "").strip())
    try:
        llegada = datetime.fromisoformat(filas[0][3])
    except (ValueError, TypeError):
        llegada = None
    return (texto or None), filas[0][1], llegada


def _descargar_buffer(telefono, cfg, db_path=None, **deps):
    """Se acabo el silencio: junta lo que el cliente escribio y llama al modelo UNA vez."""
    tel = _norm_telefono(telefono)
    with _TIMERS_GUARD:
        _TIMERS_DEBOUNCE.pop(tel, None)
    try:
        with _lock_de(tel):
            texto, telefono_meta, llegada = _tomar_buffer(tel, db_path)
            if not texto:
                return None
            # El cliente ya contesto: la etapa se vuelve a anotar sola si sale otra ficha.
            _guardar_etapa(tel, "", db_path)
            return procesar_mensaje(telefono_meta, texto, cfg, llegado_en=llegada, **deps)
    except Exception as e:
        print(f"[debounce][ERROR] no se pudo procesar el buffer de {tel}: {e}")
        return None


def _agendar_descarga(telefono, cfg, db_path=None, **deps):
    """Arranca (o REINICIA) el reloj de silencio de ese telefono. Devuelve la espera."""
    tel = _norm_telefono(telefono)
    espera = _espera_debounce(tel, db_path)
    with _TIMERS_GUARD:
        anterior = _TIMERS_DEBOUNCE.pop(tel, None)
        if anterior is not None:
            anterior.cancel()      # cada mensaje nuevo del mismo cliente reinicia el reloj
        _TIMERS_DEBOUNCE[tel] = _PROGRAMADOR.programar(
            espera, lambda: _descargar_buffer(tel, cfg, db_path=db_path, **deps))
    return espera


def marcar_leido(msg_id, cfg):
    """Le pone el visto azul al mensaje del cliente mientras el debounce espera.

    Sin ninguna señal, 8 s de silencio parecen un bot caido y el cliente manda "hola?" --
    que reinicia el reloj y empeora la espera que venia a arreglar. El visto no gasta un
    mensaje ni una llamada al modelo: es lo que hace tolerable la espera y sale gratis.
    Best-effort a proposito -- si Meta lo rechaza, el pedido sigue igual."""
    token, phone_id = cfg.get("access_token"), cfg.get("phone_number_id")
    if not msg_id or not token or not phone_id:
        return {"simulado": True}
    import urllib.request
    import urllib.error
    body = json.dumps({"messaging_product": "whatsapp", "status": "read",
                       "message_id": msg_id}).encode("utf-8")
    peticion = urllib.request.Request(
        f"https://graph.facebook.com/v20.0/{phone_id}/messages", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(peticion, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[debounce][AVISO] no se pudo marcar como leido {msg_id}: {e}")
        return {"error": str(e)}


def vaciar_buffer_al_arrancar(cfg, db_path=None, **deps):
    """Procesa lo que haya quedado en el buffer cuando el proceso murio.

    Es la contracara del buffer persistido: guardarlo en SQLite no sirve de nada si al
    volver nadie lo mira. Corre UNA vez al arrancar, antes de aceptar trafico nuevo.
    Devuelve cuantos telefonos se destrabaron."""
    con = _con_respaldo(db_path)
    try:
        telefonos = [f[0] for f in
                     con.execute("SELECT DISTINCT telefono FROM buffer_entrante").fetchall()]
    except Exception as e:
        print(f"[debounce][AVISO] no se pudo leer el buffer al arrancar: {e}")
        return 0
    finally:
        con.close()
    for tel in telefonos:
        print(f"[debounce] el proceso murio con mensajes esperando de {tel}: se procesan ahora")
        _descargar_buffer(tel, cfg, db_path=db_path, **deps)
    return len(telefonos)


def procesar_payload_meta(payload, cfg, permitidos=None, debounce=False, **deps):
    """Recorre la estructura real de un webhook de WhatsApp Cloud API (entry ->
    changes -> value -> messages[]) y procesa cada mensaje. Los de tipo TEXTO se
    mandan al cerebro; los demas (audio, foto, ubicacion, etc. -- fuera de alcance
    de este bot) reciben una respuesta minima en vez de quedar en visto. Devuelve
    las respuestas visibles generadas (util para el --test).

    'permitidos' (27-jul-2026, S1 del plan): set de msg['id'] a procesar, o None
    para procesar todos (asi los tests y llamadas directas que no pasan por el
    dedupe del endpoint /webhook siguen funcionando igual que siempre). Un id que
    NO esta en 'permitidos' es un reintento de Meta ya procesado -- se descarta.

    Cada mensaje se procesa bajo el lock de SU telefono (ver _lock_de): dos
    mensajes del MISMO cliente casi simultaneos quedan en orden; de clientes
    DISTINTOS corren en paralelo sin esperarse.

    'debounce' (10-ago-2026): con True, los mensajes de CLIENTES no se procesan al
    instante -- se acumulan en el buffer persistido y se contestan de una sola vez
    cuando el cliente deja de escribir (ver el bloque de arriba). Viene en False a
    proposito: lo prende solo _procesar_en_segundo_plano, el unico camino de
    produccion, y asi los 483 candados de --test siguen siendo sincronos y valen
    tal cual. Con debounce=True la lista que se devuelve viene VACIA para esos
    mensajes: la respuesta la manda el temporizador despues, no este llamado."""
    visibles = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            valor = change.get("value", {})
            perfiles = _nombres_de_perfil(valor)
            for msg in valor.get("messages", []):
                if permitidos is not None and msg.get("id") not in permitidos:
                    continue
                telefono = msg.get("from", "")
                # El nombre de perfil se guarda ANTES de procesar el mensaje: si el
                # pedido cierra en este mismo turno, la comanda ya lo tiene disponible.
                if telefono and perfiles.get(_norm_telefono(telefono)):
                    _guardar_nombre_perfil(telefono, perfiles[_norm_telefono(telefono)])
                if msg.get("type") != "text":
                    if telefono:
                        with _lock_de(_norm_telefono(telefono)):
                            # 29-sep-2026 (Christian, WhatsApp -- "pide la foto pero
                            # despues dice que no puede ver fotos"): le pedimos el
                            # pantallazo de la transferencia y, cuando lo manda como
                            # imagen o PDF (lo mas natural), el bot lo rebotaba con el
                            # mensaje de "solo leo texto" -- le pedia una prueba que no
                            # podia recibir, y el pedido se quedaba sin la reafirmacion
                            # al cliente (el aviso al jefe SI habia salido antes, apenas
                            # se retuvo el pedido -- eso no dependia de este mensaje).
                            # Con un comprobante pendiente, CUALQUIER adjunto de imagen o
                            # documento se toma como la señal de que ya transfirio, igual
                            # que si hubiera escrito "ya transferi" (caso 45).
                            if (msg.get("type") in ("image", "document")
                                    and _tiene_comprobante_pendiente(telefono)):
                                _conv_db = deps.get("conv_db")
                                guardar_turno(telefono, "usuario",
                                              f"[{msg.get('type')} recibido]", db_path=_conv_db)
                                guardar_turno(telefono, "asistente",
                                              MENSAJE_TRANSFERENCIA_AVISADA, db_path=_conv_db)
                                enviar_whatsapp(telefono, MENSAJE_TRANSFERENCIA_AVISADA, cfg)
                                visibles.append(MENSAJE_TRANSFERENCIA_AVISADA)
                            else:
                                visibles.append(_responder_tipo_no_soportado(telefono, cfg))
                    continue
                texto = (msg.get("text") or {}).get("body", "")
                if not (telefono and texto):
                    continue
                # --- Debounce (solo produccion; ver el bloque grande de arriba) ---
                if debounce and _norm_telefono(telefono) not in _JEFES_SIN_BUFFER:
                    _encolar_entrante(telefono, texto, _ahora_chile())
                    # El visto ANTES de agendar: es la unica señal que recibe el cliente
                    # durante la espera, y tiene que salir aunque el reloj se reinicie.
                    marcar_leido(msg.get("id"), cfg)
                    _agendar_descarga(telefono, cfg, **deps)
                    continue
                with _lock_de(_norm_telefono(telefono)):
                    visibles.append(procesar_mensaje(telefono, texto, cfg, **deps))
    return visibles


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="Bot Patata — Webhook WhatsApp")


@app.get("/webhook")
def endpoint_verificar(request: Request):
    q = request.query_params
    status, cuerpo = verificar_token(
        q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"), _cfg_webhook())
    if status != 200:
        raise HTTPException(status_code=status, detail=cuerpo)
    return PlainTextResponse(cuerpo)


def _procesar_en_segundo_plano(payload, cfg, permitidos):
    """Todo el trabajo pesado del webhook, FUERA del ciclo request/response.

    27-jul-2026 (S1 del plan, bug real): antes 'procesar_payload_meta' corria
    DENTRO del propio handler async, bloqueando el unico event loop mientras
    esperaba al cerebro (OpenAI/Gemini) -- un mensaje lento (o caido) congelaba
    el bot ENTERO, incluidos los mensajes de otros clientes que llegaban mientras
    tanto. FastAPI/Starlette corren las funciones SINCRONAS de BackgroundTasks en
    un hilo de threadpool aparte (run_in_threadpool), asi que ahora el webhook
    responde 200 al instante (ver endpoint_recibir) y esto corre en paralelo."""
    try:
        reintentar_pendientes(cfg)   # oportunista: no esperar los 30s del hilo si ya hay trafico
        escalar_esperas_al_backup(cfg)
        resolver_esperas_vencidas(cfg)
        escalar_comprobantes_al_backup(cfg)
        resolver_comprobantes_vencidos(cfg)
    except Exception as e:
        print(f"[webhook][AVISO] reintentos/esperas de esta vuelta fallaron: {e}")
    try:
        # debounce=True: ESTE es el camino de produccion, el unico que buferea.
        procesar_payload_meta(payload, cfg, permitidos=permitidos, debounce=True)
    except Exception as e:
        print(f"[webhook][ERROR] procesar_payload_meta revento: {e}")


@app.post("/webhook")
async def endpoint_recibir(request: Request, background_tasks: BackgroundTasks):
    cuerpo = await request.body()
    cfg = _cfg_webhook()
    firma = request.headers.get("X-Hub-Signature-256", "")
    if not firma_valida(cuerpo, firma, cfg.get("app_secret")):
        raise HTTPException(status_code=403, detail="firma invalida")
    try:
        payload = json.loads(cuerpo or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON invalido")
    # Dedupe SINCRONO (rapido: solo un INSERT por mensaje) antes de responder --
    # asi un reintento que llega mientras el primero se procesa TODAVIA en el
    # hilo de background tambien queda descartado, no solo los que llegan despues.
    nuevos = _marcar_vistos_y_filtrar(_ids_del_payload(payload))
    if nuevos:
        background_tasks.add_task(_procesar_en_segundo_plano, payload, cfg, nuevos)
    # Los acuses de entrega (06-ago-2026) van por afuera del dedupe a proposito: un
    # mismo mensaje recibe VARIOS payloads (sent, delivered, read) con el mismo id, y
    # marcarlos como "vistos" haria descartar todos menos el primero. registrar_statuses
    # ya es idempotente y solo avanza de estado.
    acuses = _statuses_del_payload(payload)
    if acuses:
        background_tasks.add_task(registrar_statuses, acuses, cfg)
    return {"ok": True}


@app.get("/salud")
def salud():
    """06-ago-2026: devuelve tambien el COMMIT que esta corriendo. Sin esto, despues de
    un push no habia forma de saber desde afuera si Railway construyo y cambio el proceso
    o si el bot seguia con el codigo viejo -- /salud contestaba "ok" igual. La variable la
    inyecta Railway sola; corriendo en un PC local queda en None y no molesta."""
    cfg = _cfg_webhook()
    return {"ok": True, "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA"),
            "meta_configurado": bool(cfg.get("access_token") and cfg.get("phone_number_id")),
            "pendientes_respaldo": contar_pendientes(), "rechazados_respaldo": contar_rechazados(),
            "esperando_cobro": contar_esperando_cobro(),
            "esperando_comprobante": contar_esperando_comprobante(),
            "alertas_fallidas": contar_alertas_fallidas(),
            "casos_humanos": contar_casos_humanos(),
            "avisos_jefes": resumen_avisos_jefes(),
            # Sin un solo telefono: /salud es abierto. El detalle por cliente vive en
            # /fragmentacion, detras del token del panel.
            "fragmentacion": resumen_fragmentacion()}


def _exigir_panel(request: Request):
    """Candado de los endpoints del PANEL (21-jul-2026).

    /conversaciones y /rechazados exponen conversaciones y datos de clientes. Con el
    bot en la nube quedaron alcanzables desde internet por cualquiera que supiera la
    URL. Ahora, si existe BOT_PANEL_TOKEN, hay que mandarlo en 'X-Panel-Token'.

    Si la variable NO esta definida se deja pasar, igual que el candado del POS: sin
    eso, actualizar el bot antes que el POS dejaria a Christian sin panel y sin saber
    por que. El aviso de arranque avisa cuando esta abierto.
    """
    esperado = os.environ.get("BOT_PANEL_TOKEN", "").strip()
    if not esperado:
        return
    if not hmac.compare_digest(request.headers.get("x-panel-token", ""), esperado):
        raise HTTPException(status_code=403, detail="panel protegido")


@app.get("/conversaciones")
def endpoint_conversaciones(request: Request, desde_id: int = 0):
    """Para la pantalla en vivo del POS (proxy desde cajero.py): la conversacion
    completa del bot (todos los clientes) + cuantos pedidos siguen sin llegar al POS
    + cuantos fueron RECHAZADOS por el POS (esos hay que ingresarlos a mano)."""
    _exigir_panel(request)
    return {"turnos": todos_los_turnos(desde_id=desde_id),
            "pendientes_respaldo": contar_pendientes(),
            "rechazados_respaldo": contar_rechazados(),
            "alertas_fallidas": contar_alertas_fallidas(),
            "casos_humanos": contar_casos_humanos()}


@app.get("/candados")
def endpoint_candados(request: Request, dias: int = 7):
    """Bot 2.0, paso 1: que candados actuaron y cada cuantos turnos de clientes."""
    _exigir_panel(request)
    return resumen_candados(dias=max(1, min(dias, 90)))


@app.post("/avisar-jefes")
async def endpoint_avisar_jefes(request: Request):
    """Puerta para que el POS/WMS le mande un aviso a los jefes por WhatsApp.

    Nació para el conteo cíclico (21-jul-2026): cuando un conteo no cuadra, el jefe
    tiene que enterarse en el momento, no cuando alguien entre a mirar una pantalla.
    Sirve para cualquier alerta operativa futura."""
    _exigir_panel(request)
    try:
        cuerpo = await request.json()
    except Exception:
        cuerpo = {}
    texto = (cuerpo.get("texto") or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="falta 'texto'")
    _avisar_jefes(texto, _cfg_webhook())
    return {"ok": True, "avisados": list(NUMS_ALERTAS_JEFE)}


@app.get("/rechazados")
def endpoint_rechazados(request: Request):
    """Los rechazados CON su detalle, para que el POS pueda precargar el pedido en vez
    de mandar a Christian a releer el chat (21-jul-2026)."""
    _exigir_panel(request)
    return {"rechazados": listar_rechazados()}


@app.post("/rechazados/{fid}/descartar")
async def endpoint_descartar(fid: int, request: Request):
    _exigir_panel(request)
    """Saca un aviso de rechazado de la vista. Queda registrado quien y por que:
    un aviso que se puede apagar sin dejar rastro es un agujero."""
    try:
        cuerpo = await request.json()
    except Exception:
        cuerpo = {}
    return descartar_rechazado(fid, motivo=cuerpo.get("motivo", ""),
                               usuario=cuerpo.get("usuario", ""))


@app.get("/pendientes")
def endpoint_pendientes(request: Request):
    """Los pedidos que aun no llegaron al POS, CON su detalle (29-jul-2026).

    El POS ya sabia CUANTOS habia; ahora sabe CUALES son, para poder rearmarlos y
    cerrarlos desde su propia pantalla. Importa de donde sale cada llamada: esta va
    POS -> bot, la direccion que funciona SIEMPRE (salida HTTPS comun, sin tunel).
    Por eso el panel sirve justo cuando el tunel esta caido, que es cuando hay
    pendientes -- ver AUDITORIA_CAMINO_BOT_POS.md, parte B."""
    _exigir_panel(request)
    return {"pendientes": listar_pendientes()}


@app.get("/fragmentacion")
def endpoint_fragmentacion(request: Request, minimo: int = 1):
    """Cuanto fragmenta cada cliente sus mensajes (10-ago-2026, pedido de Gonza).

    Es el insumo del "ajuste por cliente" del debounce: dentro de dos o tres semanas,
    con trafico real, decidir a quien conviene subirle o bajarle la espera. Hoy solo
    mide -- ninguna espera depende de esto.

    Detras del token del panel porque lista TELEFONOS. El mismo dato sin telefonos sale
    por /salud, que es abierto."""
    _exigir_panel(request)
    return {"clientes": listar_fragmentacion(minimo_rafagas=minimo),
            "resumen": resumen_fragmentacion()}


@app.get("/calles-sin-planilla")
def endpoint_calles_sin_planilla(request: Request):
    """Calles que repetidamente NO calzan con data/tarifas_delivery.json (ver
    _registrar_calle_sin_planilla) -- que Gonza (o quien revise el proyecto) pueda
    consultarlas sin tener que leer los logs de Railway a mano."""
    _exigir_panel(request)
    return {"calles": listar_calles_sin_planilla()}


@app.get("/pendientes/{fid}/conversacion")
def endpoint_pendiente_conversacion(fid: int, request: Request):
    """La conversacion AISLADA del cliente que hizo ese pedido, para no tener que
    cazarla entre los mensajes de todos los demas. Lector de solo lectura a proposito
    (turnos_de, no historial: ver su docstring)."""
    _exigir_panel(request)
    tel = telefono_de_pendiente(fid)
    if not tel:
        return {"telefono": None, "turnos": []}
    return {"telefono": tel, "turnos": turnos_de(tel)}


@app.post("/pendientes/{fid}/descartar")
async def endpoint_cerrar_pendiente(fid: int, request: Request):
    """Cierra un pendiente: lo usa tanto el 'ya lo ingrese a mano' (motivo con el
    numero de pedido local) como el 'no lo voy a tomar'. Queda registrado quien y
    por que."""
    _exigir_panel(request)
    try:
        cuerpo = await request.json()
    except Exception:
        cuerpo = {}
    return cerrar_pendiente(fid, motivo=cuerpo.get("motivo", ""),
                            usuario=cuerpo.get("usuario", ""))


@app.get("/alertas")
def endpoint_alertas(request: Request):
    """Avisos de sistema que NO llegaron a NINGUN jefe por WhatsApp, ni siquiera
    tras los reintentos de enviar_whatsapp (27-jul-2026, S2 del plan) -- para que
    no dependan de la ventana de 24h de WhatsApp ni de que alguien este mirando
    el chat en ese momento."""
    _exigir_panel(request)
    return {"alertas": listar_alertas_fallidas()}


@app.post("/alertas/{fid}/descartar")
def endpoint_descartar_alerta(fid: int, request: Request):
    _exigir_panel(request)
    return descartar_alerta_fallida(fid)


@app.get("/casos-humanos")
def endpoint_casos_humanos(request: Request):
    """Todo caso donde el bot derivo la conversacion a una persona (humano prometido,
    pedido grande, segundo pedido superpuesto, falla del cerebro) -- CON el WhatsApp
    de aviso a los jefes ya mandado, sin importar si Meta lo entrego o alguien lo leyo
    (15-sep-2026, caso real: un WhatsApp "aceptado" que nunca se leyo). No depende de
    que alguien este mirando el celular en ese momento, igual que /alertas y /pendientes."""
    _exigir_panel(request)
    return {"casos": listar_casos_humanos()}


@app.get("/avisos-jefes")
def endpoint_avisos_jefes(request: Request, dias: int = 7):
    """Detalle fila por fila de lo que el bot le mandó a los jefes por WhatsApp,
    entregado o no -- /salud ya publica el agregado (enviados/entregados/leidos) pero
    es un endpoint ABIERTO y no lleva ni un celular; este es del panel, con token, y
    sigue sin publicar ninguno (ver listar_avisos_jefes)."""
    _exigir_panel(request)
    return {"avisos": listar_avisos_jefes(dias=dias)}


@app.post("/casos-humanos/{fid}/descartar")
async def endpoint_descartar_caso_humano(fid: int, request: Request):
    _exigir_panel(request)
    try:
        cuerpo = await request.json()
    except Exception:
        cuerpo = {}
    return cerrar_caso_humano(fid, usuario=cuerpo.get("usuario", ""))


# ---------------------------------------------------------------------------
# Auto-test SIN credenciales reales (ni Meta ni Gemini) -- verifica la LOGICA:
# parseo del payload, seguridad (verify_token + firma), enganche real al nucleo del
# POS (tarificado real contra menu.json) y modo jefe, con un cerebro SIMULADO.
# ---------------------------------------------------------------------------
# Sintaxis interna de _CerebroFalso para autorear escenarios de cierre de pedido
# (04-ago-2026): ya NO es el protocolo real (produccion usa tool-calling nativo,
# ver TOOL_EMITIR_PEDIDO en prototipo.py) -- es solo la forma comoda en que los
# ~30 tests de escenarios de pedido de aca abajo ya arman variantes con
# respuesta_cerrada.replace(...). Reescribirlos uno por uno no aportaba nada; esto
# los deja intactos y _CerebroFalso los traduce al contrato real (texto, pedido).
_RE_PEDIDO_FALSO = re.compile(r"<PEDIDO_JSON>\s*(\{.*?\})\s*</PEDIDO_JSON>", re.DOTALL)


class _CerebroFalso:
    """Devuelve una respuesta fija por llamada, en orden -- simula a OpenAI sin
    llamar a ninguna API real. Guarda los 'mensajes' de la ULTIMA llamada (S2.5,
    27-jul-2026) para que los tests puedan verificar que notas internas se inyectaron
    al contexto (ej. cliente conocido / pedido reciente).

    04-ago-2026 (tool-calling): sin tools=, igual que siempre -- devuelve el string
    tal cual (lo usan el modo jefe y los tests que no tocan pedidos). Con tools=,
    simula el contrato real de CerebroOpenAI -- (texto, pedido_o_None) -- leyendo el
    bloque <PEDIDO_JSON> de la respuesta fija (ver _RE_PEDIDO_FALSO arriba) en vez
    de devolverlo como texto visible."""
    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.llamadas = 0
        self.ultimos_mensajes = None

    def pensar(self, mensajes, tools=None, tool_choice=None):
        self.ultimos_mensajes = mensajes
        i = min(self.llamadas, len(self._respuestas) - 1)
        self.llamadas += 1
        r = self._respuestas[i]
        if not tools:
            return r
        mt = _RE_PEDIDO_FALSO.search(r)
        if not mt:
            return r, None
        texto = _RE_PEDIDO_FALSO.sub("", r).strip()
        try:
            return texto, json.loads(mt.group(1))
        except json.JSONDecodeError:
            return texto, {"_error": "JSON del pedido mal formado"}


if __name__ == "__main__":
    if "--init" in sys.argv:
        _init_config()
        sys.exit(0)

    if "--test" not in sys.argv:
        cfg = _cfg_webhook()
        if not CONFIG.exists():
            print(f"[aviso] no existe {CONFIG.name}: corriendo con placeholders "
                  "(el bot va a SIMULAR el envio, no va a hablar con WhatsApp de verdad). "
                  "Corre 'python webhook.py --init' y completa las credenciales.")
        print(f"Webhook escuchando en http://0.0.0.0:{cfg['puerto']}/webhook")
        # ANTES de aceptar trafico nuevo: si el proceso anterior murio (deploy, crash,
        # escalado) con mensajes esperando en el buffer del debounce, se contestan ahora.
        # Persistirlos no sirve de nada si al volver nadie los mira.
        _destrabados = vaciar_buffer_al_arrancar(cfg)
        if _destrabados:
            print(f"[debounce] {_destrabados} conversacion(es) quedaron esperando y se procesaron")
        threading.Thread(target=_hilo_reintento_pendientes, daemon=True).start()
        threading.Thread(target=_hilo_sincronizar_menu, daemon=True).start()
        threading.Thread(target=_hilo_sincronizar_clientes, daemon=True).start()
        threading.Thread(target=_hilo_recordar_programados, daemon=True).start()
        threading.Thread(target=_hilo_seguimiento_saludo, daemon=True).start()
        threading.Thread(target=_hilo_monitor_pico, daemon=True).start()
        if not NTFY_TOPIC_PATATA:
            print("[monitor_pico][aviso] sin NTFY_TOPIC_PATATA: el monitor de la "
                  "ventana pico corre pero no manda nada")
        uvicorn.run(app, host="0.0.0.0", port=int(cfg.get("puerto", 8001)))
        sys.exit(0)

    # ---- --test ----
    import shutil
    import tempfile
    from contextlib import contextmanager
    from nucleo import NucleoPedidos   # solo aca: el --test SI corre un POS real en memoria

    @contextmanager
    def carpeta_temporal():
        """Como tempfile.TemporaryDirectory(), pero tolera que SQLite deje el .db
        abierto al salir (Windows no permite borrar un archivo con handle abierto;
        NucleoPedidos no expone un metodo cerrar() -- no hace falta agregarlo solo
        para el test, total es basura de /tmp que el SO limpia solo)."""
        d = tempfile.mkdtemp()
        try:
            yield Path(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _envejecer_cierre(tel, db, minutos=MINUTOS_VENTANA_POSCIERRE + 1):
        """Corre el `cerrado_en` hacia atras para simular que la ventana de poscierre
        (18-ago-2026) ya vencio, sin dormir de verdad. Los candados de archivado no
        pueden depender de que el reloj avance: la ventana dura 15 minutos."""
        _c = _con_conv_en(db)
        _c.execute(
            "UPDATE turnos SET cerrado_en=? WHERE telefono=? AND cerrado_en IS NOT NULL",
            ((datetime.now() - timedelta(minutes=minutos)).isoformat(timespec="seconds"),
             tel))
        _c.commit()
        _c.close()

    # 27-jul-2026 (S2.5): _nota_pedido_reciente/_guardar_ultimo_pedido (punto 3) leen/escriben
    # RESPALDO_DB por defecto en CUALQUIER mensaje que abra una conversacion nueva -- sin este
    # swap, cualquier test de aca abajo que no pida explicitamente una base aparte terminaria
    # creando tablas (o filas) en el archivo REAL (bot/respaldo_pedidos.db). Todo el --test
    # arranca apuntando a un archivo temporal; los bloques que ya hacen su propio swap de
    # RESPALDO_DB (guardan/restauran) siguen funcionando igual, ahora sobre una base segura.
    RESPALDO_DB = Path(tempfile.mkdtemp()) / "respaldo_test_global.db"

    # Mismo criterio que RESPALDO_DB arriba (27-jul-2026, S4): sin este swap, cualquier
    # test que dispare construir_sistema()/procesar_mensaje() usaria el cache REAL del
    # menu del POS si por casualidad ya existe en este PC (ej. corriste el webhook real
    # antes) -- los tests deben ser deterministas contra data/menu.json siempre.
    prototipo.MENU_CACHE = Path(tempfile.mkdtemp()) / "menu_cache_test_no_existe.json"
    # Mismo motivo (04-ago-2026, "nombre registrado"): sin este swap, los tests de
    # _refrescar_clientes_pos/_nota_cliente_conocido leerian/escribirian/borrarian la
    # cache REAL de clientes del POS si por casualidad ya existe en este PC.
    prototipo.CLIENTES_CACHE = Path(tempfile.mkdtemp()) / "clientes_cache_test_no_existe.json"

    # 28-jul-2026: el candado de horario cerrado (ver "Horario cerrado" en procesar_mensaje)
    # ahora es de CODIGO -- antes el horario real no afectaba en nada el flujo de prueba (la
    # IA "decidia" sola, sin cortar nunca el flujo). Sin este swap, TODO el resto de --test
    # se volveria no-determinista: fallaria de noche/madrugada y pasaria de dia, segun la
    # hora real en que alguien lo corra. Se fuerza "abierto" para el grueso del test (que no
    # esta probando horario); el test dedicado de horario cerrado (mas abajo) lo cierra
    # puntualmente y lo vuelve a abrir.
    _estado_horario_real = estado_horario
    estado_horario = lambda menu: (True, "[test] ABIERTO (forzado)")  # noqa: E731,F811
    _estado_horario_forzado_abierto = estado_horario   # para restaurar DESPUES del test de horario cerrado

    # Mismo motivo (03-ago-2026): _toma_pedidos compara la hora real contra CORTE_PEDIDOS
    # (23:50) ademas de estado_horario -- sin fijarla, el --test se volveria no-determinista
    # otra vez si alguien lo corre entre las 23:50 y la medianoche en Chile. Se fuerza un
    # mediodia cualquiera para el grueso del test; el test dedicado del corte (mas abajo) usa
    # su propio 'ahora' explicito, sin depender de este global.
    _ahora_chile_real = _ahora_chile
    _ahora_chile = lambda: datetime(2026, 7, 28, 12, 0, tzinfo=TZ_CHILE)  # noqa: E731,F811
    _VENTANA_FORZADA_TEST = True   # ver su comentario: los bloques viejos miden contenido

    ok = []

    def chk(desc, cond):
        print(f"  [{'OK' if cond else 'FALLA'}] {desc}")
        ok.append(cond)

    print("== verificar_token (handshake GET de Meta) ==")
    cfg_test = {**CONFIG_DEFAULT, "verify_token": "secreto123"}
    s, c = verificar_token("subscribe", "secreto123", "el-challenge", cfg_test)
    chk("token correcto -> 200 + challenge", s == 200 and c == "el-challenge")
    s, c = verificar_token("subscribe", "otro-token", "el-challenge", cfg_test)
    chk("token incorrecto -> 403 (fail-closed)", s == 403)
    s, c = verificar_token(None, None, None, cfg_test)
    chk("sin parametros -> 403", s == 403)

    print("== firma_valida (X-Hub-Signature-256) ==")
    secreto = "app-secret-de-prueba"
    cuerpo = b'{"hola":"mundo"}'
    firma_ok = "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    chk("firma correcta -> True", firma_valida(cuerpo, firma_ok, secreto))
    chk("firma incorrecta -> False", firma_valida(cuerpo, "sha256=deadbeef", secreto) is False)
    chk("sin firma -> False (si hay secreto configurado)", firma_valida(cuerpo, "", secreto) is False)
    chk("sin secreto configurado -> True (modo pruebas, documentado)", firma_valida(cuerpo, "", "") is True)

    print("== enviar_whatsapp sin credenciales (modo simulado) ==")
    r = enviar_whatsapp("56900000000", "hola", CONFIG_DEFAULT)
    chk("no revienta y devuelve simulado=True", r.get("simulado") is True)

    print("== S2: enviar_whatsapp reintenta ante fallas transitorias, no ante un 4xx ==")
    import io as _io
    import urllib.error as _urlerr
    import urllib.request as _urlreq_e
    cfg_meta_e = {**CONFIG_DEFAULT, "access_token": "tok-prueba", "phone_number_id": "123"}

    def _fake_urlopen(intentos_registro, fallas_antes_de_ok, tipo_error=500):
        contador = {"n": 0}
        def _f(peticion, timeout=20):
            contador["n"] += 1
            intentos_registro.append(contador["n"])
            if contador["n"] <= fallas_antes_de_ok:
                raise _urlerr.HTTPError(peticion.full_url, tipo_error, "fail", {},
                                        _io.BytesIO(b"detalle de prueba"))
            class _Resp:
                def read(self_): return b'{"messages":[{"id":"wamid.OK"}]}'
                def __enter__(self_): return self_
                def __exit__(self_, *a): return False
            return _Resp()
        return _f

    _orig_urlopen_e = _urlreq_e.urlopen
    try:
        intentos1 = []
        _urlreq_e.urlopen = _fake_urlopen(intentos1, fallas_antes_de_ok=1, tipo_error=500)
        r1 = enviar_whatsapp("56900000000", "hola", cfg_meta_e)
        chk("5xx transitorio: reintenta y el 2do intento entrega bien",
            r1.get("messages", [{}])[0].get("id") == "wamid.OK" and intentos1 == [1, 2])

        intentos2 = []
        _urlreq_e.urlopen = _fake_urlopen(intentos2, fallas_antes_de_ok=99, tipo_error=400)
        r2 = enviar_whatsapp("56900000000", "hola", cfg_meta_e)
        chk("4xx (definitivo, ej. fuera de la ventana de 24h): NO reintenta",
            intentos2 == [1] and "error" in r2)

        intentos3 = []
        _urlreq_e.urlopen = _fake_urlopen(intentos3, fallas_antes_de_ok=99, tipo_error=503)
        r3 = enviar_whatsapp("56900000000", "hola", cfg_meta_e)
        chk("5xx persistente: agota los reintentos y devuelve error, sin reventar",
            intentos3 == [1, 2] and "error" in r3)
    finally:
        _urlreq_e.urlopen = _orig_urlopen_e

    print("== 18-ago-2026: el aviso al RESPALDO sale por PLANTILLA (ventana de 24 h) ==")
    # Medido en /salud ANTES de este cambio: respaldo 3 enviados / 3 fallidos / 0
    # entregados (codigo 131047), jefe 2 de 2 leidos. El texto libre a ese numero no
    # llegaba nunca, y como Meta devuelve 200 igual, el bot lo daba por entregado: el
    # segundo escalon de la salvaguarda anti-limbo no existia en la vida real.
    chk("_var_plantilla aplana los saltos (Meta rechaza el mensaje ENTERO si no)",
        "\n" not in _var_plantilla("Cliente: Ana\nModalidad: delivery\nTel: 569"))
    chk("...y junta las lineas con un separador que se lee bien en el telefono",
        _var_plantilla("Cliente: Ana\nTel: 569") == "Cliente: Ana · Tel: 569")
    chk("...aplasta los espacios seguidos (mas de 4 tambien es rechazo)",
        "  " not in _var_plantilla("Items:     2x Italiano"))
    chk("...recorta lo muy largo en vez de mandar algo ilegible",
        len(_var_plantilla("x" * 900)) == MAX_VARIABLE_PLANTILLA)
    chk("...y nunca deja un parametro vacio, que tambien lo rechaza",
        _var_plantilla("") == "-" and _var_plantilla(None) == "-")

    _cuerpos_pl = []

    def _urlopen_captura(falla_plantilla=False):
        """Captura el cuerpo de cada POST. Con falla_plantilla=True, el POST type=template
        se cae con un 4xx -- el caso REAL mientras Meta todavia no aprueba la plantilla."""
        def _f(peticion, timeout=20):
            cuerpo = json.loads(peticion.data.decode("utf-8"))
            _cuerpos_pl.append(cuerpo)
            if falla_plantilla and cuerpo.get("type") == "template":
                raise _urlerr.HTTPError(peticion.full_url, 400, "fail", {},
                                        _io.BytesIO(b"(#132001) template does not exist"))
            class _Resp:
                def read(self_): return b'{"messages":[{"id":"wamid.PL"}]}'
                def __enter__(self_): return self_
                def __exit__(self_, *a): return False
            return _Resp()
        return _f

    _orig_urlopen_p = _urlreq_e.urlopen
    with carpeta_temporal() as tmp_pl:
        db_pl = Path(tmp_pl) / "respaldo_plantilla.db"
        try:
            _urlreq_e.urlopen = _urlopen_captura()
            _cuerpos_pl.clear()
            _avisar_backup("texto largo\ncon saltos", cfg_meta_e, db_path=db_pl,
                           motivo="una transferencia en 5 min",
                           detalle="Cliente: Ana\nItems: 2x Italiano")
            chk("al respaldo se le manda una PLANTILLA, no texto libre",
                len(_cuerpos_pl) == 1 and _cuerpos_pl[0].get("type") == "template")
            _pl = _cuerpos_pl[0]["template"]
            chk("con el nombre y el idioma EXACTOS de la plantilla aprobada en Meta",
                _pl["name"] == PLANTILLA_RESPALDO
                and _pl["language"]["code"] == PLANTILLA_IDIOMA)
            _params = [p["text"] for p in _pl["components"][0]["parameters"]]
            chk("con las dos variables, y ninguna con saltos de linea adentro",
                len(_params) == 2 and all("\n" not in p for p in _params))
            chk("la primera variable es el motivo tal cual se le paso",
                _params[0] == "una transferencia en 5 min")

            _cuerpos_pl.clear()
            _urlreq_e.urlopen = _urlopen_captura(falla_plantilla=True)
            _avisar_backup("aviso de emergencia", cfg_meta_e, db_path=db_pl)
            chk("si la plantilla no sale (sin aprobar aun), cae al texto libre de siempre",
                [c.get("type") for c in _cuerpos_pl] == ["template", "text"])
            chk("...y ese texto libre lleva el aviso COMPLETO, no la version recortada",
                _cuerpos_pl[1]["text"]["body"] == "aviso de emergencia")

            _antes_pl = contar_alertas_fallidas(db_path=db_pl)
            _urlreq_e.urlopen = _fake_urlopen([], fallas_antes_de_ok=99, tipo_error=400)
            _avisar_backup("no llega por ningun lado", cfg_meta_e, db_path=db_pl)
            chk("si fallan LAS DOS, el aviso queda en el panel del POS y no se pierde",
                contar_alertas_fallidas(db_path=db_pl) == _antes_pl + 1)
        finally:
            _urlreq_e.urlopen = _orig_urlopen_p

    print("== 25-sep-2026: ventana de 24 h mirada ANTES de avisar a los jefes ==")
    _VENTANA_FORZADA_TEST = None
    with carpeta_temporal() as tmp_v:
        conv_v = Path(tmp_v) / "conv_v.db"
        db_v = Path(tmp_v) / "resp_v.db"
        chk("un jefe que nunca escribio tiene la ventana CERRADA",
            not _ventana_whatsapp_abierta(JEFE_NUM, conv_v))
        guardar_turno(JEFE_NUM, "usuario", "hola", db_path=conv_v)
        chk("si escribio recien, la ventana esta ABIERTA",
            _ventana_whatsapp_abierta(JEFE_NUM, conv_v))
        chk("...y se reconoce aunque el numero venga con '+' adelante",
            _ventana_whatsapp_abierta("+" + JEFE_NUM, conv_v))
        _cv = _con_conv_en(conv_v)
        _cv.execute("UPDATE turnos SET creado_en=?",
                    ((datetime.now() - timedelta(hours=25)).isoformat(timespec="seconds"),))
        _cv.commit()
        _cv.close()
        chk("si su ultimo mensaje fue hace 25 h, la ventana esta CERRADA",
            not _ventana_whatsapp_abierta(JEFE_NUM, conv_v))
        guardar_turno(JEFE_NUM, "asistente", "respuesta del bot", db_path=conv_v)
        chk("un mensaje DEL BOT no abre la ventana (tiene que escribir el jefe)",
            not _ventana_whatsapp_abierta(JEFE_NUM, conv_v))

        try:
            _cuerpos_pl.clear()
            _urlreq_e.urlopen = _urlopen_captura()
            _avisar_jefes("💳 CONFIRMAR TRANSFERENCIA — responde 'confirmado'", cfg_meta_e,
                          db_path=db_v, conv_db=conv_v)
            chk("caso real: con la ventana cerrada, a CADA jefe le sale la plantilla primero",
                [c.get("type") for c in _cuerpos_pl] == ["template"] * len(NUMS_ALERTAS_JEFE))
            _pv = [p["text"] for p in _cuerpos_pl[0]["template"]["components"][0]["parameters"]]
            chk("...y el aviso viaja completo en la variable de detalle",
                "CONFIRMAR TRANSFERENCIA" in _pv[1])
            # El falso de Meta devuelve el MISMO wamid a todos, y el registro deduplica por
            # wamid: por eso se pide al menos uno, no uno por jefe.
            chk("...y queda anotado para su acuse, igual que el texto libre",
                resumen_avisos_jefes(db_path=db_v)["enviados"] >= 1)

            _cuerpos_pl.clear()
            _urlreq_e.urlopen = _urlopen_captura(falla_plantilla=True)
            _avisar_jefes("aviso con plantilla caida", cfg_meta_e, db_path=db_v, conv_db=conv_v)
            chk("si la plantilla no sale, cae al texto libre (no queda peor que antes)",
                [c.get("type") for c in _cuerpos_pl] == ["template", "text"] * len(NUMS_ALERTAS_JEFE))

            guardar_turno(JEFE_NUM, "usuario", "confirmado", db_path=conv_v)
            _cuerpos_pl.clear()
            _urlreq_e.urlopen = _urlopen_captura()
            _avisar_jefes("aviso con un jefe activo", cfg_meta_e, db_path=db_v, conv_db=conv_v)
            _tipos = {c["to"]: c.get("type") for c in _cuerpos_pl}
            chk("mezcla real: al jefe que escribio le va texto libre, al otro plantilla",
                _tipos.get(JEFE_NUM) == "text"
                and all(_tipos.get(n) == "template" for n in NUMS_ALERTAS_JEFE if n != JEFE_NUM))
        finally:
            _urlreq_e.urlopen = _orig_urlopen_p
            _VENTANA_FORZADA_TEST = True

    print("== S2: si NINGUN jefe recibe una alerta, queda visible en el panel del POS "
          "(no depende de la ventana de 24h de WhatsApp) ==")
    with carpeta_temporal() as tmp_al:
        db_al = Path(tmp_al) / "alertas_test.db"
        chk("arranca sin alertas pendientes", contar_alertas_fallidas(db_path=db_al) == 0)
        try:
            _urlreq_e.urlopen = _fake_urlopen([], fallas_antes_de_ok=99, tipo_error=400)
            _avisar_jefes("⚠️ prueba de alerta que WhatsApp no pudo entregar", cfg_meta_e,
                          db_path=db_al)
        finally:
            _urlreq_e.urlopen = _orig_urlopen_e
        chk("si NINGUN jefe la recibio, la alerta se guarda para el panel",
            contar_alertas_fallidas(db_path=db_al) == 1)
        alertas_pend = listar_alertas_fallidas(db_path=db_al)
        chk("el texto completo queda disponible para mostrarlo en el panel",
            len(alertas_pend) == 1 and "no pudo entregar" in alertas_pend[0]["texto"])
        d = descartar_alerta_fallida(alertas_pend[0]["id"], db_path=db_al)
        chk("se puede descartar desde el panel (mismo patron que rechazados)", d.get("ok") is True)
        chk("tras descartar ya no cuenta", contar_alertas_fallidas(db_path=db_al) == 0)

        # Si al menos UN jefe la recibe (modo simulado, sin credenciales = "exito"),
        # no hace falta guardar nada -- ya llego por WhatsApp.
        chk("si el envio SI tuvo exito (aunque sea simulado), no se guarda alerta",
            contar_alertas_fallidas(db_path=db_al) == 0)
        _avisar_jefes("aviso que si se manda bien", CONFIG_DEFAULT, db_path=db_al)
        chk("confirmado: con credenciales simuladas (siempre 'exitosas') no se acumulan alertas",
            contar_alertas_fallidas(db_path=db_al) == 0)

    print("== ACUSE DE RECIBO: si una alerta a los jefes llego a un humano de verdad "
          "(06-ago-2026) ==")
    # El agujero que cierra: un 200 de Meta es "aceptado", no "leido". Con tres numeros
    # y sin acuse, un aviso entregado a un telefono que nadie mira era indistinguible
    # de uno atendido -- y de eso depende TODO candado que escala a una persona.
    with carpeta_temporal() as tmp_ac:
        db_ac = Path(tmp_ac) / "acuses_test.db"

        def _payload_status(mid, estado, errores=None):
            """Un webhook de Meta con SOLO acuses (asi son los reales: sin 'messages')."""
            # El destinatario tiene que ser un jefe VIGENTE: _statuses_del_payload filtra
            # por _NUMS_JEFE_NORM, que sale de NUMS_ALERTAS_JEFE. Cuando el telefono del
            # mostrador salio de esa tupla (10-ago-2026), este literal dejo de matchear y
            # tumbo los nueve chequeos de acuse de golpe. Va por constante, no por numero.
            st = {"id": mid, "status": estado, "recipient_id": JEFE_NUM}
            if errores:
                st["errors"] = errores
            return {"entry": [{"changes": [{"value": {
                "messaging_product": "whatsapp", "statuses": [st]}}]}]}

        _wa_orig_ac = enviar_whatsapp
        _enviados_ac = []

        def _espia_ac(telefono, texto, cfg, intentos=2):
            _enviados_ac.append(telefono)
            return {"messaging_product": "whatsapp",
                    "messages": [{"id": f"wamid.TEST{len(_enviados_ac)}"}]}

        enviar_whatsapp = _espia_ac
        # Con la ventana ABIERTA (los dos jefes escribieron hace poco) va texto libre,
        # que es lo que este bloque mide. La ventana cerrada tiene su bloque aparte.
        _conv_ac = Path(tmp_ac) / "conv_ac.db"
        for _j in NUMS_ALERTAS_JEFE:
            guardar_turno(_j, "usuario", "ok", db_path=_conv_ac)
        try:
            _avisar_jefes("⚠️ un cliente reclama, alguien tiene que responderle",
                          CONFIG_DEFAULT, db_path=db_ac, conv_db=_conv_ac)
        finally:
            enviar_whatsapp = _wa_orig_ac
        r_ac = resumen_avisos_jefes(db_path=db_ac)
        chk("la alerta queda anotada una vez por jefe, con el id que devolvio Meta",
            r_ac["enviados"] == len(NUMS_ALERTAS_JEFE) and len(_enviados_ac) == len(NUMS_ALERTAS_JEFE))
        chk("recien enviada NO cuenta como entregada ni leida",
            r_ac["entregados"] == 0 and r_ac["leidos"] == 0)
        chk("un payload de acuse no trae 'messages': por eso el flujo viejo lo tiraba entero",
            _ids_del_payload(_payload_status("wamid.TEST1", "delivered")) == [])

        registrar_statuses(_statuses_del_payload(
            _payload_status("wamid.TEST1", "delivered")), db_path=db_ac)
        chk("llega 'delivered': el aviso pasa a entregado (llego al telefono)",
            resumen_avisos_jefes(db_path=db_ac)["entregados"] == 1)

        registrar_statuses(_statuses_del_payload(
            _payload_status("wamid.TEST1", "read")), db_path=db_ac)
        r_leido = resumen_avisos_jefes(db_path=db_ac)
        chk("llega 'read': un humano ABRIO la alerta -- que es lo que nunca se supo",
            r_leido["leidos"] == 1 and bool(r_leido["ultimo_leido_en"]))

        registrar_statuses(_statuses_del_payload(
            _payload_status("wamid.TEST1", "delivered")), db_path=db_ac)
        chk("un 'delivered' que llega atrasado NO borra el 'read' que ya entro",
            resumen_avisos_jefes(db_path=db_ac)["leidos"] == 1)

        registrar_statuses(_statuses_del_payload(
            _payload_status("wamid.TEST1", "read")), db_path=db_ac)
        chk("el mismo acuse repetido (Meta reenvia) no duplica nada",
            resumen_avisos_jefes(db_path=db_ac)["enviados"] == len(NUMS_ALERTAS_JEFE))

        tocadas_cli = registrar_statuses(_statuses_del_payload(
            _payload_status("wamid.DE_UN_CLIENTE", "read")), db_path=db_ac)
        chk("un id desconocido no crea filas ni revienta",
            tocadas_cli == 0 and resumen_avisos_jefes(db_path=db_ac)["enviados"] == len(NUMS_ALERTAS_JEFE))

        _pl_cliente = _payload_status("wamid.DE_UN_CLIENTE", "read")
        _pl_cliente["entry"][0]["changes"][0]["value"]["statuses"][0]["recipient_id"] = "56911113333"
        chk("el acuse de un mensaje a un CLIENTE se descarta sin ni siquiera abrir la base",
            _statuses_del_payload(_pl_cliente) == [])
        _pl_sin_dest = _payload_status("wamid.TEST1", "read")
        _pl_sin_dest["entry"][0]["changes"][0]["value"]["statuses"][0].pop("recipient_id")
        chk("...pero un acuse sin destinatario NO se tira: pasa y decide la base",
            len(_statuses_del_payload(_pl_sin_dest)) == 1)

        registrar_statuses(_statuses_del_payload(_payload_status(
            "wamid.TEST2", "failed",
            [{"code": 131047, "title": "Re-engagement message"}])), db_path=db_ac)
        r_fail = resumen_avisos_jefes(db_path=db_ac)
        chk("un 'failed' (ventana de 24h cerrada) queda contado como fallido, con su codigo",
            r_fail["fallidos"] == 1)

        # El aviso "sin acuse" se fabrica aparte a proposito. Antes se reciclaba el wamid del
        # TERCER jefe, y cuando los jefes pasaron de tres a dos (10-ago-2026) el UPDATE dejo
        # de tocar ninguna fila -- en silencio, porque un UPDATE que no matchea no es error.
        # Un chequeo no puede depender de CUANTOS destinatarios tenga la lista.
        _registrar_aviso_jefe("wamid.SIN_ACUSE", JEFE_NUM, "aviso que nadie acuso",
                              db_path=db_ac)
        con_ac = _con_respaldo(db_ac)
        try:
            _viejo = (datetime.now() - timedelta(
                hours=HORAS_PARA_DAR_ACUSE_POR_PERDIDO + 1)).isoformat(timespec="seconds")
            con_ac.execute("UPDATE avisos_jefes SET creado_en=? WHERE msg_id=?",
                           (_viejo, "wamid.SIN_ACUSE"))
            con_ac.commit()
        finally:
            con_ac.close()
        chk("un aviso viejo sin NINGUN acuse se reporta aparte: es 'no medido', no 'no leido'",
            resumen_avisos_jefes(db_path=db_ac)["sin_acuse"] == 1)

        # 18-sep-2026: listar_avisos_jefes es el detalle fila por fila (resumen_avisos_jefes
        # ya probó el agregado arriba) -- lo que le faltaba al Curador para poder MOSTRARLOS,
        # no solo contarlos.
        detalle_avisos = listar_avisos_jefes(db_path=db_ac)
        chk("lista las 3 filas de este fixture (TEST1 leido, TEST2 fallido, SIN_ACUSE enviado)",
            len(detalle_avisos) == 3)
        chk("nunca publica el celular real: solo la etiqueta jefe1/jefe2/otro",
            all(a["destinatario"] in ("jefe1", "jefe2", "otro") for a in detalle_avisos)
            and all(JEFE_NUM not in (a["destinatario"] or "") for a in detalle_avisos))
        chk("el fallido trae el código de Meta en el detalle",
            any(a["estado"] == "fallido" and "131047" in a["detalle"] for a in detalle_avisos))
        chk("el extracto del texto viaja (truncado, no vacío)",
            any(a["extracto"] and "reclama" in a["extracto"] for a in detalle_avisos))

        def _espia_sim(telefono, texto, cfg, intentos=2):
            return {"simulado": True}

        # Lo que se prueba es que NO aparezcan filas nuevas, asi que se compara contra el
        # conteo de antes y no contra un numero escrito a mano.
        _enviados_antes = resumen_avisos_jefes(db_path=db_ac)["enviados"]
        enviar_whatsapp = _espia_sim
        try:
            _avisar_jefes("aviso sin credenciales de Meta", CONFIG_DEFAULT, db_path=db_ac)
        finally:
            enviar_whatsapp = _wa_orig_ac
        chk("sin wamid (modo simulado o error) no se inventa una fila que despues mienta",
            resumen_avisos_jefes(db_path=db_ac)["enviados"] == _enviados_antes)

    print("== REINTENTO: un texto libre que Meta acepto y recien DESPUES rechazo "
          "(131047) se reintenta por plantilla apenas se sabe del rechazo (28-sep-2026) ==")
    # Caso real del 24-sep: el aviso "CONFIRMAR TRANSFERENCIA" fallo para los DOS
    # jefes con 200 y todo, y nadie lo reintento -- el fallback sincrono de
    # _avisar_jefes ya habia terminado para cuando llego el rechazo real.
    with carpeta_temporal() as tmp_rt:
        db_rt = Path(tmp_rt) / "reintento_test.db"
        conv_rt = Path(tmp_rt) / "conv_rt.db"
        for _j in NUMS_ALERTAS_JEFE:
            guardar_turno(_j, "usuario", "ok", db_path=conv_rt)   # ventana abierta

        _wa_orig_rt = enviar_whatsapp
        _pl_orig_rt = enviar_plantilla_whatsapp
        _enviados_rt = {}
        _plantillas_rt = []

        def _espia_wa_rt(telefono, texto, cfg, intentos=2):
            mid = f"wamid.RT.{telefono}"
            _enviados_rt[telefono] = mid
            return {"messages": [{"id": mid}]}

        def _espia_pl_rt(telefono, plantilla, variables, cfg, idioma=PLANTILLA_IDIOMA,
                          intentos=2):
            _plantillas_rt.append({"telefono": telefono, "variables": variables})
            return {"messages": [{"id": f"wamid.RTPL{len(_plantillas_rt)}"}]}

        enviar_whatsapp = _espia_wa_rt
        try:
            _avisar_jefes("💳 CONFIRMAR TRANSFERENCIA — responde 'confirmado'", CONFIG_DEFAULT,
                          db_path=db_rt, conv_db=conv_rt)
        finally:
            enviar_whatsapp = _wa_orig_rt
        chk("salio por texto libre a los dos jefes (ventana abierta)",
            set(_enviados_rt) == set(NUMS_ALERTAS_JEFE))
        chk("todavia no se reintento con plantilla", len(_plantillas_rt) == 0)

        enviar_plantilla_whatsapp = _espia_pl_rt
        try:
            registrar_statuses(_statuses_del_payload(_payload_status(
                _enviados_rt[JEFE_NUM], "failed",
                [{"code": 131047, "title": "Re-engagement message"}])),
                cfg=CONFIG_DEFAULT, db_path=db_rt)
        finally:
            enviar_plantilla_whatsapp = _pl_orig_rt
        chk("el rechazo TARDIO si dispara un reintento por plantilla",
            len(_plantillas_rt) == 1 and _plantillas_rt[0]["telefono"] == JEFE_NUM)
        chk("...con el aviso original en la variable de detalle, no cortado a 80",
            "CONFIRMAR TRANSFERENCIA" in _plantillas_rt[0]["variables"][1])
        chk("el reintento exitoso queda anotado como un aviso nuevo",
            resumen_avisos_jefes(db_path=db_rt)["enviados"] == len(NUMS_ALERTAS_JEFE) + 1)

        # Si el reintento TAMBIEN falla, no revienta ni inventa una fila fantasma.
        _antes_de_fallar = resumen_avisos_jefes(db_path=db_rt)["enviados"]

        def _espia_pl_falla_rt(telefono, plantilla, variables, cfg,
                                idioma=PLANTILLA_IDIOMA, intentos=2):
            return {"error": "rechazado", "http_status": 400}

        enviar_plantilla_whatsapp = _espia_pl_falla_rt
        try:
            registrar_statuses(_statuses_del_payload(_payload_status(
                _enviados_rt[JEFE_BACKUP], "failed",
                [{"code": 131047, "title": "Re-engagement message"}])),
                cfg=CONFIG_DEFAULT, db_path=db_rt)
        finally:
            enviar_plantilla_whatsapp = _pl_orig_rt
        chk("si el reintento tambien falla, no revienta ni inventa una fila",
            resumen_avisos_jefes(db_path=db_rt)["enviados"] == _antes_de_fallar)

        # Sin `cfg` (todos los llamadores existentes salvo el webhook real) el
        # comportamiento es IDENTICO al de antes de este cambio: no reintenta.
        _registrar_aviso_jefe("wamid.RT.sincfg", JEFE_NUM, "aviso de control", db_path=db_rt)
        enviar_plantilla_whatsapp = _espia_pl_rt
        _plantillas_rt.clear()
        try:
            registrar_statuses(_statuses_del_payload(_payload_status(
                "wamid.RT.sincfg", "failed",
                [{"code": 131047, "title": "Re-engagement message"}])), db_path=db_rt)
        finally:
            enviar_plantilla_whatsapp = _pl_orig_rt
        chk("sin cfg no reintenta -- mismo comportamiento que antes del 28-sep",
            len(_plantillas_rt) == 0)

        # Un codigo que NO es 131047 no reintenta: la MISMA plantilla fallaria igual.
        _registrar_aviso_jefe("wamid.RT.otrocodigo", JEFE_NUM, "aviso de control 2",
                              db_path=db_rt)
        enviar_plantilla_whatsapp = _espia_pl_rt
        try:
            registrar_statuses(_statuses_del_payload(_payload_status(
                "wamid.RT.otrocodigo", "failed",
                [{"code": 131026, "title": "Message undeliverable"}])),
                cfg=CONFIG_DEFAULT, db_path=db_rt)
        finally:
            enviar_plantilla_whatsapp = _pl_orig_rt
        chk("un codigo de rechazo que no es 131047 no dispara el reintento",
            len(_plantillas_rt) == 0)

    print("== MONITOR DE LA VENTANA PICO: visibilidad real 20h-23h, avisa por ntfy "
          "solo lo NUEVO (29-sep-2026) ==")
    chk("20h, 22h y 23h (bordes inclusive) caen en la ventana pico",
        _en_ventana_pico(datetime(2026, 9, 29, 20, 0))
        and _en_ventana_pico(datetime(2026, 9, 29, 22, 0))
        and _en_ventana_pico(datetime(2026, 9, 29, 23, 59)))
    chk("19h y 00h quedan AFUERA (un minuto antes/despues de la ventana)",
        not _en_ventana_pico(datetime(2026, 9, 29, 19, 59))
        and not _en_ventana_pico(datetime(2026, 9, 29, 0, 0)))

    with carpeta_temporal() as tmp_mp:
        db_mp = Path(tmp_mp) / "monitor_test.db"
        chk("arranca sin problemas", _problemas_monitor_pico(db_path=db_mp) == [])

        _guardar_alerta_fallida("nadie recibio este aviso", db_path=db_mp)
        _claves_mp = [c for c, _ in _problemas_monitor_pico(db_path=db_mp)]
        chk("una alerta totalmente fallida SI es un problema",
            "alertas_fallidas" in _claves_mp)

        _registrar_caso_humano("pedido_grande", "56911112222", "caso fresco",
                               db_path=db_mp)
        chk("un caso humano RECIEN abierto todavia no es un problema (no le dio tiempo a nadie)",
            not any(c.startswith("caso_humano_") for c, _ in _problemas_monitor_pico(db_path=db_mp)))
        con_mp = _con_respaldo(db_mp)
        try:
            _viejo_mp = (datetime.now() - timedelta(
                minutes=MINUTOS_CASO_HUMANO_SIN_ATENDER + 1)).isoformat(timespec="seconds")
            con_mp.execute("UPDATE casos_humanos SET creado_en=?", (_viejo_mp,))
            con_mp.commit()
        finally:
            con_mp.close()
        chk(f"pasados los {MINUTOS_CASO_HUMANO_SIN_ATENDER} min sin cerrar, SI es un problema",
            any(c.startswith("caso_humano_") for c, _ in _problemas_monitor_pico(db_path=db_mp)))

        _registrar_aviso_jefe("wamid.MP1", JEFE_NUM, "aviso que fallo", db_path=db_mp)
        registrar_statuses(_statuses_del_payload(_payload_status(
            "wamid.MP1", "failed", [{"code": 131047, "title": "Re-engagement message"}])),
            db_path=db_mp)   # sin cfg: no reintenta, se queda fallido para este chequeo
        chk("un aviso a jefe fallido en las ultimas 24h tambien es un problema",
            any(c.startswith("aviso_fallido_") for c, _ in _problemas_monitor_pico(db_path=db_mp)))

        # _avisar_si_nuevo: dedup real, con el POST a ntfy espiado.
        _posts_mp = []

        def _urlopen_ntfy(peticion, timeout=15):
            _posts_mp.append({"url": peticion.full_url, "titulo": peticion.headers.get("Title")})
            class _Resp:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b"{}"
            return _Resp()

        _urlreq_e.urlopen = _urlopen_ntfy
        try:
            r1 = _avisar_si_nuevo("clave_x", "titulo", "mensaje", "topic-secreto-test",
                                  db_path=db_mp)
            r2 = _avisar_si_nuevo("clave_x", "titulo", "mensaje", "topic-secreto-test",
                                  db_path=db_mp)
        finally:
            _urlreq_e.urlopen = _orig_urlopen_e
        chk("la primera vez SI manda el push por ntfy", r1 is True and len(_posts_mp) == 1)
        chk("la segunda vez con la MISMA clave no vuelve a mandar nada (evita el bucle)",
            r2 is False and len(_posts_mp) == 1)
        chk("le pega al topic correcto, no a uno adivinado",
            _posts_mp[0]["url"] == NTFY_URL + "topic-secreto-test")

        # _revisar_monitor_pico: junta todo, pero solo actua con topic Y en la ventana.
        _posts_mp.clear()
        con_mp2 = _con_respaldo(db_mp)
        try:
            con_mp2.execute("DELETE FROM monitor_avisos")
            con_mp2.commit()
        finally:
            con_mp2.close()
        _ahora_orig_mp = _ahora_chile
        _urlreq_e.urlopen = _urlopen_ntfy
        try:
            _ahora_chile = lambda: datetime(2026, 9, 29, 22, 0)
            chk("sin topic no manda nada, aunque este en la ventana pico",
                _revisar_monitor_pico("", db_path=db_mp) == 0 and len(_posts_mp) == 0)
            chk("con topic Y en la ventana, SI avisa (los 3 problemas de arriba)",
                _revisar_monitor_pico("topic-secreto-test", db_path=db_mp) == 3
                and len(_posts_mp) == 3)
            _posts_mp.clear()
            _ahora_chile = lambda: datetime(2026, 9, 29, 15, 0)
            chk("mismos problemas, pero FUERA de la ventana pico: no manda nada",
                _revisar_monitor_pico("topic-secreto-test", db_path=db_mp) == 0
                and len(_posts_mp) == 0)
        finally:
            _ahora_chile = _ahora_orig_mp
            _urlreq_e.urlopen = _orig_urlopen_e

    print("== flujo CLIENTE: payload real de Meta -> pedido en la cola del POS ==")
    with carpeta_temporal() as tmp:
        db_pedidos = Path(tmp) / "pedidos_test.db"
        conv_db = Path(tmp) / "conv_test.db"
        nucleo_test = NucleoPedidos(db_path=db_pedidos)
        tarif_test = Tarificador.desde_json(MENU_JSON)

        respuesta_cerrada = (
            "Perfecto, quedan 2 Churrasco Italiano, retiro, pagas con tarjeta. "
            "Total $8.900, se confirma enseguida!\n"
            "<PEDIDO_JSON>\n"
            '{"cliente":"Juan Test","modalidad":"retiro","direccion":null,'
            '"items":[{"categoria":"Churrascos","producto":"Churrasco Italiano",'
            '"variante":null,"cantidad":2,"precio_unitario":4900,"subtotal":8900,"notas":""}],'
            '"delivery_cobro":0,"total":8900,"forma_pago":"tarjeta","derivar_humano":false,'
            '"comentarios":""}\n'
            "</PEDIDO_JSON>"
        )
        cerebro_falso = _CerebroFalso([respuesta_cerrada])
        payload_meta = {
            "entry": [{"changes": [{"value": {"messages": [
                {"type": "text", "from": "56988887777", "text": {"body": "2 italianos retiro tarjeta"}}
            ]}}]}]
        }
        visibles = procesar_payload_meta(
            payload_meta, CONFIG_DEFAULT, cerebro=cerebro_falso, nucleo=nucleo_test,
            tarificador=tarif_test, conv_db=conv_db,
        )
        chk("el bot respondio (no crasheo)", len(visibles) == 1)
        chk("el bloque PEDIDO_JSON no quedo visible para el cliente",
            "<PEDIDO_JSON>" not in visibles[0])
        cola = nucleo_test.cola()
        chk("el pedido aparece en la cola del POS", len(cola) == 1)
        if cola:
            p = cola[0]
            chk("canal='whatsapp' (misma puerta que cajero/web)", p["canal"] == "whatsapp")
            chk("telefono normalizado a 9 digitos", p["telefono"] == "988887777")
            chk("total TARIFICADO de verdad contra menu.json (no lo que dijo la IA)",
                p["total"] == 8900)
        chk("cerrado el pedido, la conversacion sigue viva la ventana de poscierre "
            "(18-ago: sin esto el bot saluda de cero al que acaba de confirmar)",
            historial("56988887777", db_path=conv_db) != [])
        _envejecer_cierre("56988887777", conv_db)
        chk("...y vencida la ventana, ahi si se limpia sola",
            historial("56988887777", db_path=conv_db) == [])

    print("== flujo CLIENTE por HTTP: el bot le habla al CAJERO CORRIENDO (POST /api/pedidos) ==")
    import os as _os
    import socket as _socket
    import threading as _threading
    import time as _time
    import urllib.request as _urlreq
    with carpeta_temporal() as tmp:
        _s = _socket.socket(); _s.bind(("127.0.0.1", 0)); _port = _s.getsockname()[1]; _s.close()
        _os.environ["POS_DB"] = str(Path(tmp) / "pedidos_cajero.db")
        import cajero  # se importa DESPUES de fijar POS_DB -> su NUCLEO usa la base temporal
        # 16-jul-2026 (bug real): el cajero auto-imprime los pedidos whatsapp, y en un PC
        # sin termica cae a la impresora predeterminada de Windows -- este test mando
        # comandas "Juan Test" reales a la impresora de una oficina. En el test NUNCA se
        # imprime: se anula el unico punto de salida al spooler (imprimir_raw).
        cajero.impresora.imprimir_raw = lambda *a, **k: "(test: impresion desactivada)"
        _server = uvicorn.Server(uvicorn.Config(cajero.app, host="127.0.0.1", port=_port, log_level="error"))
        _threading.Thread(target=_server.run, daemon=True).start()
        for _ in range(60):
            try:
                _urlreq.urlopen(f"http://127.0.0.1:{_port}/", timeout=2)
                break
            except Exception:
                _time.sleep(0.1)
        cfg_http = {**CONFIG_DEFAULT, "pos_api_url": f"http://127.0.0.1:{_port}"}
        cerebro_http = _CerebroFalso([respuesta_cerrada])
        payload_http = {"entry": [{"changes": [{"value": {"messages": [
            {"type": "text", "from": "56999998888", "text": {"body": "2 italianos retiro tarjeta"}}
        ]}}]}]}
        # SIN nucleo inyectado -> camino de PRODUCCION (HTTP al cajero corriendo)
        procesar_payload_meta(payload_http, cfg_http, cerebro=cerebro_http,
                              conv_db=Path(tmp) / "conv_http.db")
        cola_http = cajero.NUCLEO.cola()
        chk("el pedido llego al cajero por HTTP (POST /api/pedidos)", len(cola_http) == 1)
        if cola_http:
            chk("canal='whatsapp' via HTTP", cola_http[0]["canal"] == "whatsapp")
            chk("total tarificado por el cajero ($8.900, promo 2x)", cola_http[0]["total"] == 8900)
            chk("telefono ligado al cliente", cola_http[0]["telefono"] == "999998888")
            chk("auto-paso a 'en_preparacion' (no espera click 'A cocina')",
                cola_http[0]["estado"] == "en_preparacion")

        print("== S4: sincronizar el menu desde el POS real (GET /api/menu-edit) ==")
        chk("cache no existe todavia -> menu_json_activo() usa el empaquetado",
            prototipo.menu_json_activo() == MENU_JSON)
        ok_sync = _refrescar_menu_pos(cfg_http)
        chk("_refrescar_menu_pos trajo el menu del POS real y valido OK", ok_sync)
        chk("el cache quedo escrito", prototipo.MENU_CACHE.exists())
        chk("con cache -> menu_json_activo() ya no usa el empaquetado",
            prototipo.menu_json_activo() == prototipo.MENU_CACHE)
        sistema_con_cache = construir_sistema()
        chk("construir_sistema() carga el cache sin reventar (mismo menu, misma carta)",
            "Churrasco Italiano" in sistema_con_cache)
        _s3 = _socket.socket(); _s3.bind(("127.0.0.1", 0)); _puerto_muerto_menu = _s3.getsockname()[1]; _s3.close()
        cfg_pos_caido = {**CONFIG_DEFAULT, "pos_api_url": f"http://127.0.0.1:{_puerto_muerto_menu}"}
        _cache_antes = prototipo.MENU_CACHE.read_text(encoding="utf-8")
        chk("POS caido -> _refrescar_menu_pos falla sin reventar (fail-safe)",
            _refrescar_menu_pos(cfg_pos_caido) is False)
        chk("el cache viejo NO se toca si la traida falla",
            prototipo.MENU_CACHE.read_text(encoding="utf-8") == _cache_antes)

        print("== nombre registrado: sincronizar clientes desde el POS real (GET /api/clientes-export) ==")
        cajero.NUCLEO.guardar_cliente(telefono="956781234", nombre="Marisol",
                                      direccion="Los Aromos 456")
        cajero.NUCLEO.guardar_cliente(telefono="", nombre="Sin Telefono", direccion="")
        ok_sync_cli = _refrescar_clientes_pos(cfg_http)
        chk("_refrescar_clientes_pos trajo los clientes del POS real y escribio la cache",
            ok_sync_cli and prototipo.CLIENTES_CACHE.exists())
        cache_cli = json.loads(prototipo.CLIENTES_CACHE.read_text(encoding="utf-8"))
        chk("la cache queda keyed por telefono normalizado, con nombre y direccion",
            cache_cli.get("956781234") == {"nombre": "Marisol", "direccion": "Los Aromos 456"})
        chk("el cliente sin telefono no ensucia la cache (no hay con que calzarlo)",
            not any(v.get("nombre") == "Sin Telefono" for v in cache_cli.values()))
        _cache_cli_antes = prototipo.CLIENTES_CACHE.read_text(encoding="utf-8")
        chk("POS caido -> _refrescar_clientes_pos falla sin reventar (fail-safe)",
            _refrescar_clientes_pos(cfg_pos_caido) is False)
        chk("la cache vieja de clientes NO se toca si la traida falla",
            prototipo.CLIENTES_CACHE.read_text(encoding="utf-8") == _cache_cli_antes)

        _server.should_exit = True
        _time.sleep(0.3)

    print("== cola de reintento: pedido que NO llega al POS se guarda y se recupera solo ==")
    with carpeta_temporal() as tmp2:
        _respaldo_original = RESPALDO_DB
        RESPALDO_DB = Path(tmp2) / "respaldo_test.db"
        try:
            # Puerto SIN nadie escuchando -- simula el POS cerrado/caido.
            _s2 = _socket.socket(); _s2.bind(("127.0.0.1", 0)); _puerto_muerto = _s2.getsockname()[1]; _s2.close()
            cfg_caido = {**CONFIG_DEFAULT, "pos_api_url": f"http://127.0.0.1:{_puerto_muerto}"}
            cerebro_caido = _CerebroFalso([respuesta_cerrada])
            payload_caido = {"entry": [{"changes": [{"value": {"messages": [
                {"type": "text", "from": "56977776666", "text": {"body": "2 italianos retiro tarjeta"}}
            ]}}]}]}
            procesar_payload_meta(payload_caido, cfg_caido, cerebro=cerebro_caido,
                                  conv_db=Path(tmp2) / "conv_caido.db")
            chk("el pedido NO se perdio: quedo en la cola de reintento (POS caido)",
                contar_pendientes() == 1)

            # El POS "vuelve" en el MISMO puerto -> reintentar_pendientes debe entregarlo.
            _os.environ["POS_DB"] = str(Path(tmp2) / "pedidos_recuperado.db")
            import importlib as _importlib
            _importlib.reload(cajero)   # fuerza una NUCLEO nueva sobre la base "recuperada"
            cajero.impresora.imprimir_raw = lambda *a, **k: "(test: impresion desactivada)"
            _server2 = uvicorn.Server(uvicorn.Config(cajero.app, host="127.0.0.1",
                                                     port=_puerto_muerto, log_level="error"))
            _threading.Thread(target=_server2.run, daemon=True).start()
            for _ in range(60):
                try:
                    _urlreq.urlopen(f"http://127.0.0.1:{_puerto_muerto}/", timeout=2)
                    break
                except Exception:
                    _time.sleep(0.1)
            entregados = reintentar_pendientes(cfg_caido)
            chk("reintentar_pendientes entrego el pedido pendiente", entregados == 1)
            chk("ya no quedan pendientes", contar_pendientes() == 0)
            chk("el pedido aparece en la cola del POS recuperado",
                len(cajero.NUCLEO.cola()) == 1)

            # 15-jul: un pedido INVALIDO (el POS vivo lo rechaza con 400) no debe quedar
            # girando para siempre -- se marca rechazado y sale del ciclo.
            _conr = _con_respaldo()
            _conr.execute(
                "INSERT INTO pendientes (payload_json, telefono, creado_en) VALUES (?,?,?)",
                (json.dumps({"canal": "invalido", "cliente": "X", "modalidad": "retiro",
                             "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                                        "cantidad": 1}]}),
                 "56900000000", datetime.now().isoformat(timespec="seconds")))
            _conr.commit(); _conr.close()
            chk("el pendiente invalido entro a la cola", contar_pendientes() == 1)
            reintentar_pendientes(cfg_caido)
            chk("el POS lo rechazo -> sale de pendientes (no gira infinito)",
                contar_pendientes() == 0)
            chk("quedo marcado como RECHAZADO (para ingreso manual)",
                contar_rechazados() == 1)
            reintentar_pendientes(cfg_caido)
            chk("una pasada posterior NO lo re-procesa ni duplica el aviso",
                contar_rechazados() == 1 and contar_pendientes() == 0)
            _server2.should_exit = True
            _time.sleep(0.3)
        finally:
            RESPALDO_DB = _respaldo_original

    print("== todos_los_turnos: conversacion completa para la pantalla en vivo del POS ==")
    with carpeta_temporal() as tmp3:
        conv3 = Path(tmp3) / "conv_vivo.db"
        guardar_turno("111111111", "usuario", "hola quiero pedir", db_path=conv3)
        guardar_turno("111111111", "asistente", "hola! que te gustaria pedir?", db_path=conv3)
        guardar_turno("222222222", "usuario", "hacen delivery?", db_path=conv3)
        todos = todos_los_turnos(db_path=conv3)
        chk("trae los 3 turnos de AMBOS telefonos", len(todos) == 3)
        chk("orden cronologico ascendente", [t["id"] for t in todos] == sorted(t["id"] for t in todos))
        chk("cada turno trae telefono/rol/texto", todos[0]["telefono"] == "111111111"
            and todos[0]["rol"] == "usuario" and todos[0]["texto"] == "hola quiero pedir")
        segundo_id = todos[1]["id"]
        incremental = todos_los_turnos(desde_id=segundo_id, db_path=conv3)
        chk("polling incremental (desde_id) trae solo lo nuevo", len(incremental) == 1
            and incremental[0]["telefono"] == "222222222")

    print("== cerrar un pedido ARCHIVA la conversacion, ya no la borra (29-jul-2026) ==")
    with carpeta_temporal() as tmp3c:
        conv3c = Path(tmp3c) / "conv_archivo.db"
        guardar_turno("56933330000", "usuario", "hola, un italiano", db_path=conv3c)
        guardar_turno("56933330000", "asistente", "listo, lo mando 🙌", db_path=conv3c)
        limpiar_historial("56933330000", db_path=conv3c)
        chk("recien cerrado, el hilo SIGUE en el prompt: es la ventana de poscierre",
            len(historial("56933330000", db_path=conv3c)) == 2)
        _envejecer_cierre("56933330000", conv3c)
        chk("el cerebro arranca fresco (el efecto que se buscaba con el borrado)",
            historial("56933330000", db_path=conv3c) == [])
        chk("la pantalla en vivo del POS conserva los 2 turnos",
            len(todos_los_turnos(db_path=conv3c)) == 2)
        chk("el panel de pendientes tambien puede leerlos",
            len(turnos_de("56933330000", db_path=conv3c)) == 2)
        guardar_turno("56933330000", "usuario", "hola de nuevo", db_path=conv3c)
        vivo3c = historial("56933330000", db_path=conv3c)
        chk("una conversacion nueva NO arrastra la archivada",
            len(vivo3c) == 1 and vivo3c[0]["texto"] == "hola de nuevo")
        chk("y el POS ve las dos conversaciones (3 turnos)",
            len(todos_los_turnos(db_path=conv3c)) == 3)
        # Poda: sin esto la base del volumen creceria sin techo, porque ya nada se borra.
        _ca = _con_conv_en(conv3c)
        _ca.execute("UPDATE turnos SET cerrado_en=? WHERE cerrado_en IS NOT NULL",
                    ((datetime.now() - timedelta(days=DIAS_RETENCION_TURNOS + 1)
                      ).isoformat(timespec="seconds"),))
        _ca.commit(); _ca.close()
        limpiar_historial("56933330000", db_path=conv3c)
        chk("la poda borra de verdad lo archivado mas viejo que la retencion",
            len(todos_los_turnos(db_path=conv3c)) == 1)

        # Migracion suave: la base que YA existe en el volumen de Railway no tiene la
        # columna nueva. Si el ALTER no corriera, el bot caeria en cada mensaje.
        import sqlite3 as _sq3
        conv3d = Path(tmp3c) / "conv_vieja.db"
        _cv2 = _sq3.connect(conv3d)
        _cv2.execute("CREATE TABLE turnos (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "telefono TEXT NOT NULL, rol TEXT NOT NULL, texto TEXT NOT NULL, "
                     "creado_en TEXT NOT NULL)")
        _cv2.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56944440000", "usuario", "pedido de ayer",
                      datetime.now().isoformat(timespec="seconds")))
        _cv2.commit(); _cv2.close()
        chk("base VIEJA sin la columna: se migra sola y conserva lo que tenia dentro",
            len(turnos_de("56944440000", db_path=conv3d)) == 1)
        limpiar_historial("56944440000", db_path=conv3d)
        _envejecer_cierre("56944440000", conv3d)
        chk("y ya migrada, archivar funciona igual que en una base nueva",
            historial("56944440000", db_path=conv3d) == []
            and len(turnos_de("56944440000", db_path=conv3d)) == 1)

    print("== panel de pendientes del POS (29-jul-2026): ver, rearmar y cerrar ==")
    with carpeta_temporal() as tmp3b:
        # --- turnos_de SOLO LEE: es la razon de que exista en vez de reusar historial() ---
        conv3b = Path(tmp3b) / "conv_pend.db"
        guardar_turno("56911112222", "usuario", "hola, 2 italianos", db_path=conv3b)
        guardar_turno("56911112222", "asistente", "listo! retiro o delivery?", db_path=conv3b)
        guardar_turno("56999998888", "usuario", "soy otro cliente", db_path=conv3b)
        # Envejecemos la conversacion mas alla de la caducidad del cerebro: un pedido
        # pendiente de anoche SIEMPRE llega al panel en este estado.
        _cv = _con_conv_en(conv3b)
        _viejo = (datetime.now() - timedelta(hours=HORAS_CADUCIDAD_HISTORIAL + 2)
                  ).isoformat(timespec="seconds")
        _cv.execute("UPDATE turnos SET creado_en=? WHERE telefono=?", (_viejo, "56911112222"))
        _cv.commit(); _cv.close()

        t = turnos_de("56911112222", db_path=conv3b)
        chk("turnos_de trae SOLO la conversacion de ese telefono", len(t) == 2)
        chk("turnos_de en orden cronologico ascendente", t[0]["texto"] == "hola, 2 italianos")
        chk("turnos_de NO borra la conversacion vencida (leerla dos veces da lo mismo)",
            len(turnos_de("56911112222", db_path=conv3b)) == 2)
        # Este es el candado de la trampa: si alguien cambia turnos_de por historial(),
        # abrir el panel devolveria VACIO -- un pendiente de anoche siempre esta vencido.
        chk("historial() NO la ve: la vencida se archiva y sale del contexto del cerebro",
            historial("56911112222", db_path=conv3b) == [])
        chk("...pero el panel la sigue leyendo entera -- archivar NO es borrar",
            len(turnos_de("56911112222", db_path=conv3b)) == 2
            and len(turnos_de("56999998888", db_path=conv3b)) == 1)

        # --- listar / telefono / cerrar ---
        _respaldo_original = RESPALDO_DB
        RESPALDO_DB = Path(tmp3b) / "respaldo_panel.db"
        try:
            _c = _con_respaldo()
            _c.execute("INSERT INTO pendientes (payload_json, telefono, creado_en) VALUES (?,?,?)",
                       (json.dumps({"cliente": "Ana", "modalidad": "delivery",
                                    "direccion": "Los Robles 123", "telefono": "56911112222",
                                    "items": [{"categoria": "Churrascos",
                                               "producto": "Churrasco Italiano", "cantidad": 2}]}),
                        "56911112222", datetime.now().isoformat(timespec="seconds")))
            # Uno viejo SIN la columna telefono: asi quedaron los de antes de que se guardara.
            _c.execute("INSERT INTO pendientes (payload_json, telefono, creado_en) VALUES (?,?,?)",
                       (json.dumps({"cliente": "Beto", "telefono": "56955554444", "items": []}),
                        None, datetime.now().isoformat(timespec="seconds")))
            _c.commit(); _c.close()

            lista = listar_pendientes()
            chk("listar_pendientes trae los 2 CON su payload completo",
                len(lista) == 2 and lista[0]["payload"]["cliente"] == "Ana"
                and lista[0]["payload"]["direccion"] == "Los Robles 123")
            chk("telefono_de_pendiente lee la columna",
                telefono_de_pendiente(lista[0]["id"]) == "56911112222")
            chk("telefono_de_pendiente cae al payload si la columna quedo vacia",
                telefono_de_pendiente(lista[1]["id"]) == "56955554444")
            chk("telefono_de_pendiente devuelve None si el id no existe",
                telefono_de_pendiente(999999) is None)

            r_cerrar = cerrar_pendiente(lista[0]["id"],
                                        motivo="ingresado a mano como pedido #7", usuario="Gonza")
            chk("cerrar_pendiente lo saca de la vista", r_cerrar["ok"] and contar_pendientes() == 1)
            chk("el cerrado ya no aparece en la lista",
                [p["id"] for p in listar_pendientes()] == [lista[1]["id"]])
            chk("cerrarlo de nuevo no hace nada la segunda vez",
                cerrar_pendiente(lista[0]["id"])["ok"] is False)
            chk("cerrar un pendiente NO lo cuenta como rechazado", contar_rechazados() == 0)

            # El candado que evita mentir: si el reintento automatico lo entrego mientras
            # el cajero miraba la pantalla, cerrarlo a mano no puede decir que lo resolvio.
            _c2 = _con_respaldo()
            _c2.execute("UPDATE pendientes SET entregado=1 WHERE id=?", (lista[1]["id"],))
            _c2.commit(); _c2.close()
            chk("si el reintento automatico ya lo entrego, cerrar_pendiente devuelve ok=False",
                cerrar_pendiente(lista[1]["id"])["ok"] is False)
        finally:
            RESPALDO_DB = _respaldo_original

    print("== RED DE SEGURIDAD: cierre SIN bloque (la falla real del 14-jul) se rescata solo ==")
    with carpeta_temporal() as tmp4:
        db4 = Path(tmp4) / "pedidos_rescate.db"
        conv4 = Path(tmp4) / "conv_rescate.db"
        nucleo4 = NucleoPedidos(db_path=db4)
        # 1a respuesta: el modelo cierra DE PALABRA pero sin bloque (lo que paso esa noche).
        cierre_sin_bloque = ("Listo! Tu pedido es 2 Churrasco Italiano, retiro, pagas con tarjeta. "
                             "Total $8.900. Apenas este listo te aviso 🙌")
        # 2a respuesta (la llamada de rescate): SOLO el bloque.
        solo_bloque = respuesta_cerrada[respuesta_cerrada.index("<PEDIDO_JSON>"):]
        cerebro_rescate = _CerebroFalso([cierre_sin_bloque, solo_bloque])
        visible4 = procesar_mensaje("56911112222", "2 italianos retiro tarjeta",
                                    {**CONFIG_DEFAULT}, cerebro=cerebro_rescate,
                                    nucleo=nucleo4, conv_db=conv4)
        cola4 = nucleo4.cola()
        chk("el pedido SE RESCATO y llego a la cola (antes se perdia)", len(cola4) == 1)
        if cola4:
            chk("total tarificado correcto via rescate", cola4[0]["total"] == 8900)
            chk("queda trazado como recuperado",
                "recuperado por red de seguridad" in (cola4[0].get("notas") or ""))
        chk("se hicieron exactamente 2 llamadas al cerebro (respuesta + rescate)",
            cerebro_rescate.llamadas == 2)
        _envejecer_cierre("56911112222", conv4)
        chk("el historial se limpio tras el rescate",
            historial("56911112222", db_path=conv4) == [])
        chk("el cliente NO ve el bloque (respuesta visible intacta)",
            "PEDIDO_JSON" not in visible4)

        # Caso negativo: respuesta con precios que NO es un cierre -> INCOMPLETO, sin pedido.
        lista_precios = "Tenemos Box 1 $6.500, Box 2 $7.000. ¿Cual te gustaria? 😊"
        cerebro_neg = _CerebroFalso([lista_precios, "INCOMPLETO"])
        procesar_mensaje("56933334444", "que boxes tienen?", {**CONFIG_DEFAULT},
                         cerebro=cerebro_neg, nucleo=nucleo4, conv_db=conv4)
        chk("una respuesta con precios que NO cierra no crea pedido", len(nucleo4.cola()) == 1)
        chk("rescate consulto y respeto el INCOMPLETO", cerebro_neg.llamadas == 2)

        # Respuesta sin '$' (saludo) ni bloque -> NI SIQUIERA se llama al rescate.
        cerebro_saludo = _CerebroFalso(["Hola! Bienvenido a Patata 🌭 ¿que deseas pedir?"])
        procesar_mensaje("56955556666", "hola", {**CONFIG_DEFAULT},
                         cerebro=cerebro_saludo, nucleo=nucleo4, conv_db=conv4)
        chk("un saludo sin '$' no gatilla la llamada extra", cerebro_saludo.llamadas == 1)

    print("== 13-sep-2026: el celular personal de Christian pierde el rol de jefe ==")
    with carpeta_temporal() as tmp6:
        db6 = Path(tmp6) / "pedidos_prueba.db"
        conv6 = Path(tmp6) / "conv_prueba.db"
        nucleo6 = NucleoPedidos(db_path=db6)
        EX_JEFE_PERSONAL = "56986923342"   # hasta el 13-sep-2026 era JEFE_PERSONAL
        chk("el ex-numero personal ya no tiene rol", _rol_de(EX_JEFE_PERSONAL) is None)
        # Sin rol: el bot lo trata como a cualquier cliente. El modo PRUEBA (que cerraba
        # el pedido sin mandarlo a cocina) desaparecio junto con el rol.
        cerebro_p = _CerebroFalso([respuesta_cerrada])
        vis_p = procesar_mensaje(EX_JEFE_PERSONAL, "2 italianos retiro tarjeta", {**CONFIG_DEFAULT},
                                 cerebro=cerebro_p, nucleo=nucleo6, conv_db=conv6)
        chk("sin rol: el pedido SI llega a cocina, como el de cualquier cliente",
            len(nucleo6.cola()) == 1)
        chk("sin rol: no aparece la marca de PRUEBA", "PRUEBA" not in vis_p)
        # 'modo jefe' / 'modo prueba' ya no son comandos de codigo: sin el toggle (que
        # solo existia para este numero), un cliente que los escribe pasa por el
        # cerebro como cualquier otro mensaje -- ya no hay atajo que gaste 0 llamadas.
        cerebro_txt = _CerebroFalso(["Disculpa, no entendí bien tu pedido 😅 ¿me lo repites?"])
        vis_t = procesar_mensaje(EX_JEFE_PERSONAL, "modo jefe", {**CONFIG_DEFAULT},
                                 cerebro=cerebro_txt, nucleo=nucleo6, conv_db=conv6)
        chk("'modo jefe' ya no es un comando: se llama al cerebro como con cualquier texto",
            cerebro_txt.llamadas == 1 and "JEFE activado" not in vis_t)

    print("== COBRO DE ENVIO: match de direccion, retencion, respuesta del jefe y timeout ==")
    chk("match: mismo numero + calle en comun",
        _match_cobro("los arrayanes 564", [{"direccion": "Los Arrayanes 564, Huechuraba",
                                            "delivery_cobro": 1500}]) == 1500)
    chk("no-match: numero distinto",
        _match_cobro("los arrayanes 999", [{"direccion": "Los Arrayanes 564",
                                            "delivery_cobro": 1500}]) is None)
    chk("no-match: cobro guardado en 0 no sirve",
        _match_cobro("los arrayanes 564", [{"direccion": "Los Arrayanes 564",
                                            "delivery_cobro": 0}]) is None)
    with carpeta_temporal() as tmp7:
        _respaldo_orig7 = RESPALDO_DB
        RESPALDO_DB = Path(tmp7) / "respaldo_cobro.db"
        try:
            db7 = Path(tmp7) / "pedidos_cobro.db"
            conv7 = Path(tmp7) / "conv_cobro.db"
            nucleo7 = NucleoPedidos(db_path=db7)
            respuesta_delivery = respuesta_cerrada.replace(
                '"modalidad":"retiro","direccion":null',
                '"modalidad":"delivery","direccion":"Pasaje Nuevo 123"')
            cerebro_d = _CerebroFalso([respuesta_delivery])
            # cfg=None en lookup -> sin POS -> cobro desconocido -> RETENIDO.
            procesar_mensaje("56977778888", "2 italianos delivery tarjeta", {**CONFIG_DEFAULT},
                             cerebro=cerebro_d, nucleo=nucleo7, conv_db=conv7)
            chk("delivery sin cobro conocido queda RETENIDO (no llega a cocina)",
                len(nucleo7.cola()) == 0 and contar_esperando_cobro() == 1)
            # El jefe responde 'envio 1500' -> el pedido se crea con ese cobro.
            vis_j = procesar_mensaje(JEFE_NUM, "envío 1500", {**CONFIG_DEFAULT},
                                     cerebro=_CerebroFalso(["no aplica"]), nucleo=nucleo7,
                                     conv_db=conv7)
            cola7 = nucleo7.cola()
            chk("'envío 1500' del jefe creo el pedido retenido", len(cola7) == 1)
            chk("el pedido lleva el delivery_cobro respondido",
                cola7 and cola7[0].get("delivery_cobro") == 1500)
            chk("el jefe recibe confirmacion con el cliente", "Juan Test" in vis_j)
            chk("ya no quedan esperas activas", contar_esperando_cobro() == 0)
            # Timeout anti-limbo: otra espera, vieja -> entra igual con ENVIO POR DEFINIR.
            _guardar_esperando_cobro({"canal": "whatsapp", "cliente": "Tardio", "modalidad":
                                      "delivery", "items": [{"categoria": "Churrascos",
                                      "producto": "Churrasco Italiano", "cantidad": 1}],
                                      "forma_pago": "efectivo", "direccion": "Calle Lenta 1",
                                      "delivery_cobro": 0, "notas": "", "telefono": "911110000"},
                                     "56911110000")
            import sqlite3 as _sq7
            _c7 = _con_respaldo()
            _c7.execute("UPDATE esperando_cobro SET creado_en=? WHERE resuelto=0",
                        ((datetime.now() - timedelta(minutes=ESPERA_COBRO_MINUTOS + 1)
                          ).isoformat(timespec="seconds"),))
            _c7.commit(); _c7.close()
            # 30-jul-2026: a los 10 min el pedido NO avanza -- primero es el turno del
            # jefe de respaldo. Antes de este escalon, esta misma llamada lo despachaba.
            n_esc = escalar_esperas_al_backup({**CONFIG_DEFAULT})
            chk("a los 10 min se escala al backup y el pedido NO avanza",
                n_esc == 1 and len(nucleo7.cola()) == 1 and contar_esperando_cobro() == 1)
            chk("el vencimiento no dispara mientras el backup tiene su turno",
                resolver_esperas_vencidas({**CONFIG_DEFAULT}, nucleo=nucleo7) == 0
                and len(nucleo7.cola()) == 1)
            chk("y no se vuelve a escalar en la siguiente vuelta del hilo",
                escalar_esperas_al_backup({**CONFIG_DEFAULT}) == 0)
            _c7b = _con_respaldo()
            _c7b.execute("UPDATE esperando_cobro SET escalado_en=? WHERE resuelto=0",
                         ((datetime.now() - timedelta(minutes=ESPERA_BACKUP_MINUTOS + 1)
                           ).isoformat(timespec="seconds"),))
            _c7b.commit(); _c7b.close()
            n_venc = resolver_esperas_vencidas({**CONFIG_DEFAULT}, nucleo=nucleo7)
            chk("timeout: recien con el backup vencido tambien, entro IGUAL a cocina",
                n_venc == 1 and len(nucleo7.cola()) == 2)
            tardio = [p for p in nucleo7.cola() if p["cliente"] == "Tardio"]
            chk("quedo marcada ENVIO POR DEFINIR",
                tardio and "ENVIO POR DEFINIR" in (tardio[0].get("notas") or ""))
        finally:
            RESPALDO_DB = _respaldo_orig7

    print("== TRANSFERENCIA SIN COMPROBANTE: retencion, confirmacion del jefe y timeout (S2.5 #7) ==")
    with carpeta_temporal() as tmp9:
        _respaldo_orig9 = RESPALDO_DB
        RESPALDO_DB = Path(tmp9) / "respaldo_transferencia.db"
        try:
            db9 = Path(tmp9) / "pedidos_transferencia.db"
            conv9 = Path(tmp9) / "conv_transferencia.db"
            nucleo9 = NucleoPedidos(db_path=db9)
            respuesta_transferencia = respuesta_cerrada.replace(
                '"forma_pago":"tarjeta"', '"forma_pago":"transferencia"')
            cerebro_t = _CerebroFalso([respuesta_transferencia])
            vis_t9 = procesar_mensaje("56966665555", "2 italianos retiro transferencia",
                                      {**CONFIG_DEFAULT}, cerebro=cerebro_t, nucleo=nucleo9,
                                      conv_db=conv9)
            chk("transferencia sin confirmar queda RETENIDA (no llega a cocina)",
                len(nucleo9.cola()) == 0 and contar_esperando_comprobante() == 1)
            # 29-jul-2026: hasta hoy el cliente leia el cierre del modelo ("aviso al
            # local") y creia que se estaba cocinando. Ahora se entera de que falta la
            # revision del local -- si no, espera 30 min algo que nunca arranco.
            #
            # 29-sep-2026 (curador): ya NO se menciona "pantallazo" (el bot no lee
            # imagenes, mencionarlo sonaba a que si podia revisarlo) -- se le dice que
            # transfiera y avise, para que el local reciba la solicitud de revision.
            # 14-oct-2026 (caso 167): sin "Confirmado" -- esa palabra significa que ya pasa
            # a cocina -- y con el flujo de Gonza: queda en espera de validar el pago.
            chk("y el CLIENTE se entera de que espera la validacion del pago, sin 'Confirmado'",
                vis_t9.startswith(prototipo.CONFIRMACION_CIERRE_TRANSFERENCIA)
                and "Apenas el local confirme la transferencia, pasa a cocina" in vis_t9
                and "confirmado" not in vis_t9.lower() and "pantallazo" not in vis_t9)
            chk("...con el TOTAL calculado por el codigo, no por el modelo",
                "$8.900" in vis_t9)
            # El jefe confirma -> el pedido se crea.
            #
            # 29-sep-2026 (curador, pedido de Gonza): el cierre al CLIENTE no decia la
            # demora -- transfirio y quedaba sin saber cuanto esperar, el mismo dato que
            # ya se le da a cualquiera que paga en efectivo/tarjeta. Se espia
            # enviar_whatsapp para ver el mensaje real que le llega (retiro -> 10 min,
            # mismo numero que minutos_demora -- nunca inventado aparte).
            _wa9, _wa_orig9 = [], enviar_whatsapp
            enviar_whatsapp = lambda n, t, c, intentos=2: _wa9.append((n, t)) or {"ok": True}  # noqa: E731
            try:
                vis_c = procesar_mensaje(JEFE_NUM, "confirmado", {**CONFIG_DEFAULT},
                                         cerebro=_CerebroFalso(["no aplica"]), nucleo=nucleo9,
                                         conv_db=conv9)
            finally:
                enviar_whatsapp = _wa_orig9
            cola9 = nucleo9.cola()
            chk("'confirmado' del jefe despacho el pedido retenido", len(cola9) == 1)
            chk("el jefe recibe confirmacion con el cliente", "Juan Test" in vis_c)
            chk("ya no quedan comprobantes pendientes", contar_esperando_comprobante() == 0)
            _al_cliente9 = [t for n, t in _wa9 if n == "56966665555"]
            chk("el cliente se entera de que ya esta en preparacion",
                any("Recibimos tu transferencia" in t and "preparación" in t
                    for t in _al_cliente9))
            chk("...con la demora de HOY para retiro (mismo numero que minutos_demora)",
                any(f"Demora {prototipo.minutos_demora('retiro')} min aprox" in t
                    for t in _al_cliente9))
            # Timeout anti-limbo: otra espera, vieja -> entra igual marcada SIN CONFIRMAR.
            _guardar_esperando_comprobante({"canal": "whatsapp", "cliente": "Tardio2", "modalidad":
                                           "retiro", "items": [{"categoria": "Churrascos",
                                           "producto": "Churrasco Italiano", "cantidad": 1}],
                                           "forma_pago": "transferencia", "direccion": None,
                                           "delivery_cobro": 0, "notas": "", "telefono": "911119999"},
                                          "56911119999")
            _c9 = _con_respaldo()
            _c9.execute("UPDATE esperando_comprobante SET creado_en=? WHERE resuelto=0",
                        ((datetime.now() - timedelta(minutes=ESPERA_COMPROBANTE_MINUTOS + 1)
                          ).isoformat(timespec="seconds"),))
            _c9.commit(); _c9.close()
            n_esc9 = escalar_comprobantes_al_backup({**CONFIG_DEFAULT})
            chk("a los 15 min la transferencia se escala al backup, sin avanzar",
                n_esc9 == 1 and len(nucleo9.cola()) == 1
                and contar_esperando_comprobante() == 1)
            chk("el vencimiento no dispara mientras el backup tiene su turno",
                resolver_comprobantes_vencidos({**CONFIG_DEFAULT}, nucleo=nucleo9) == 0)
            _c9b = _con_respaldo()
            _c9b.execute("UPDATE esperando_comprobante SET escalado_en=? WHERE resuelto=0",
                         ((datetime.now() - timedelta(minutes=ESPERA_BACKUP_MINUTOS + 1)
                           ).isoformat(timespec="seconds"),))
            _c9b.commit(); _c9b.close()
            _wa9b = []
            _avisos9b = []
            _avj9b, _avb9b = _avisar_jefes, _avisar_backup
            enviar_whatsapp = lambda tel, txt, cfg, intentos=2: _wa9b.append((tel, txt)) or {"simulado": True}
            _avisar_jefes = lambda txt, cfg, db_path=None, **kw: _avisos9b.append(("jefes", txt))
            _avisar_backup = lambda txt, cfg, db_path=None, **kw: _avisos9b.append(("backup", txt))
            try:
                n_venc9 = resolver_comprobantes_vencidos({**CONFIG_DEFAULT}, nucleo=nucleo9)
            finally:
                enviar_whatsapp, _avisar_jefes, _avisar_backup = _wa_orig9, _avj9b, _avb9b
            chk("timeout: recien con el backup vencido tambien, entro IGUAL a cocina",
                n_venc9 == 1 and len(nucleo9.cola()) == 2)
            tardio9 = [p for p in nucleo9.cola() if p["cliente"] == "Tardio2"]
            chk("quedo marcada SIN CONFIRMAR",
                tardio9 and "SIN CONFIRMAR" in (tardio9[0].get("notas") or ""))
            # Caso 167 (14-oct-2026, Gonza): los DOS jefes saben que ESE pedido entro por
            # omision de ambos, para dejar el respaldo y medirlo.
            chk("caso 167: a jefes y respaldo les llega 'ACEPTADO POR OMISIÓN' con cliente y total",
                {d for d, _ in _avisos9b} == {"jefes", "backup"}
                and all("ACEPTADO POR OMISIÓN de ambos jefes" in t and "Tardio2" in t
                        and "$4.900" in t for _, t in _avisos9b))
            chk("...y el cliente se entera de que paso a cocina (sin decir que llego la plata)",
                any(n == "56911119999" and "ya está en preparación" in t
                    and "Recibimos" not in t for n, t in _wa9b))
        finally:
            RESPALDO_DB = _respaldo_orig9

    print("== ORDEN de la cadena: ENVIO primero, transferencia despues (29-jul-2026) ==")
    # El caso real de la prueba de Gonza: delivery + transferencia a una direccion sin
    # tarifa guardada. Antes se retenia por transferencia PRIMERO, asi que al cliente se
    # le pedia pagar un total sin envio y despues quedaba "esperando el valor del
    # delivery" -- ya habiendo transferido de mas o de menos.
    with carpeta_temporal() as tmp10:
        _respaldo_orig10 = RESPALDO_DB
        RESPALDO_DB = Path(tmp10) / "respaldo_orden.db"
        _wa_orig10 = enviar_whatsapp
        _wa10 = []

        def _espia10(telefono, texto, cfg, intentos=2):
            _wa10.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia10
        try:
            db10 = Path(tmp10) / "pedidos_orden.db"
            conv10 = Path(tmp10) / "conv_orden.db"
            nucleo10 = NucleoPedidos(db_path=db10)
            respuesta_dt = respuesta_cerrada.replace(
                '"modalidad":"retiro","direccion":null',
                '"modalidad":"delivery","direccion":"Pasaje Ficticio 790"').replace(
                '"forma_pago":"tarjeta"', '"forma_pago":"transferencia"')
            vis10 = procesar_mensaje("56933710254", "2 italianos delivery transferencia",
                                     {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([respuesta_dt]),
                                     nucleo=nucleo10, conv_db=conv10)
            chk("1o el ENVIO: queda esperando cobro, NO esperando comprobante",
                contar_esperando_cobro() == 1 and contar_esperando_comprobante() == 0)
            chk("no hay comanda todavia", len(nucleo10.cola()) == 0)
            chk("al cliente NO se le pide transferir sin saber el envio",
                "avísame por acá" not in vis10)

            # El jefe pone el envio -> RECIEN AHORA se le pide la transferencia.
            vis_j10 = procesar_mensaje(JEFE_NUM, "envío 1500", {**CONFIG_DEFAULT},
                                       cerebro=_CerebroFalso(["no aplica"]), nucleo=nucleo10,
                                       conv_db=conv10)
            chk("2o la TRANSFERENCIA: pasa a esperar comprobante, sin comanda aun",
                contar_esperando_cobro() == 0 and contar_esperando_comprobante() == 1
                and len(nucleo10.cola()) == 0)
            al_cliente10 = [t for n, t in _wa10 if n == "56933710254"]
            chk("al cliente se le pide la transferencia CON el envio ya resuelto",
                any("Apenas el local confirme la transferencia" in t and "1.500" in t for t in al_cliente10))
            chk("...y con el total que INCLUYE el envio (8.900 + 1.500)",
                any("10.400" in t for t in al_cliente10))
            chk("el jefe ve que quedo pedida la transferencia, no despachado",
                "transferencia" in vis_j10.lower())

            # El jefe confirma el pantallazo -> comanda, y sin volver a retener (bucle).
            procesar_mensaje(JEFE_NUM, "confirmado", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso(["no aplica"]), nucleo=nucleo10,
                             conv_db=conv10)
            cola10 = nucleo10.cola()
            chk("3o la COMANDA: recien con el pantallazo confirmado", len(cola10) == 1)
            chk("y lleva el envio que puso el jefe",
                cola10 and cola10[0].get("delivery_cobro") == 1500)
            chk("no se volvio a retener (sin bucle en la puerta)",
                contar_esperando_comprobante() == 0)
        finally:
            RESPALDO_DB = _respaldo_orig10
            enviar_whatsapp = _wa_orig10

    print("== EL ENVIO NO LO PONE EL CLIENTE (30-jul-2026) ==")
    # Prueba real de Gonza: el cliente escribio cuanto "le sale" el delivery, el modelo
    # lo acepto y lo sumo al total del chat. El monto del cliente no puede llegar ni a la
    # comanda ni al total que se le pide transferir.
    with carpeta_temporal() as tmp11:
        _respaldo_orig11 = RESPALDO_DB
        RESPALDO_DB = Path(tmp11) / "respaldo_envio.db"
        try:
            db11 = Path(tmp11) / "pedidos_envio.db"
            conv11 = Path(tmp11) / "conv_envio.db"
            nucleo11 = NucleoPedidos(db_path=db11)
            # El modelo le hizo caso al cliente: envio 2.000 sumado al total.
            respuesta_dictada = respuesta_cerrada.replace(
                '"modalidad":"retiro","direccion":null',
                '"modalidad":"delivery","direccion":"Pasaje Ficticio 456"').replace(
                '"delivery_cobro":0,"total":8900', '"delivery_cobro":2000,"total":10900')
            vis11 = procesar_mensaje("56944445555", "2 italianos delivery, el envio me sale 2000",
                                     {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([respuesta_dictada]),
                                     nucleo=nucleo11, conv_db=conv11)
            chk("al cliente se le desmiente el total que incluia SU envio",
                "no puedo tomarlo" in vis11 and "no el que aparece más arriba" in vis11)
            chk("el pedido igual queda esperando el cobro REAL del local",
                contar_esperando_cobro() == 1 and len(nucleo11.cola()) == 0)
            # El jefe pone el envio de verdad -> manda el suyo, no el del cliente.
            procesar_mensaje(JEFE_NUM, "envío 1500", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso(["no aplica"]), nucleo=nucleo11,
                             conv_db=conv11)
            cola11 = nucleo11.cola()
            chk("la comanda lleva el envio del LOCAL (1500), nunca el del cliente (2000)",
                len(cola11) == 1 and cola11[0].get("delivery_cobro") == 1500)
            # Y el saneo en si, aislado (incluye la escritura "2.000" con punto).
            p_sano = {"modalidad": "delivery", "delivery_cobro": "2.000"}
            chk("_sanear_envio_dictado devuelve el monto y deja el campo en 0",
                _sanear_envio_dictado(p_sano) == 2000 and p_sano["delivery_cobro"] == 0)
            chk("un pedido sin envio dictado no gatilla nada",
                _sanear_envio_dictado({"modalidad": "retiro", "delivery_cobro": 0}) == 0)
        finally:
            RESPALDO_DB = _respaldo_orig11

    print("== EL TOTAL LO PONE EL CODIGO, NO EL MODELO (30-jul-2026) ==")
    # Chat real de un pedido de 2 Churrasco Italiano para retiro: el modelo NO dijo ningun
    # total y el de su bloque era $9.800, cuando el menu dice $8.900 (promo 2x). El cliente
    # confirmo sin saber cuanto pagaba, y de habersele dicho habria sido $900 de mas.
    _pl_2ital = {"items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                            "variante": None, "cantidad": 2, "notas": ""}],
                 "delivery_cobro": 0}
    _av = _aviso_total(_pl_2ital)
    chk("el aviso trae el total del MENU ($8.900), no el del modelo ($9.800)",
        _av is not None and _av[1] == 8900 and "$8.900" in _av[0])
    chk("...y le dice al cliente que la promo 2x ya esta aplicada", "promo 2x" in _av[0])
    chk("un producto que el menu NO reconoce no anuncia un total incompleto",
        _aviso_total({"items": _pl_2ital["items"] + [
            {"categoria": "Inventada", "producto": "Producto Fantasma", "cantidad": 1}],
            "delivery_cobro": 0}) is None)
    chk("el envio ya resuelto entra en el total",
        (_aviso_total({**_pl_2ital, "delivery_cobro": 1500}) or (None, 0))[1] == 10400)

    with carpeta_temporal() as tmp13:
        _respaldo_orig13 = RESPALDO_DB
        RESPALDO_DB = Path(tmp13) / "respaldo_total.db"
        try:
            db13 = Path(tmp13) / "pedidos_total.db"
            conv13 = Path(tmp13) / "conv_total.db"
            nucleo13 = NucleoPedidos(db_path=db13)
            # (a) El modelo cierra SIN decir el total -- el caso exacto del chat real.
            sin_total = respuesta_cerrada.replace(
                "Total $8.900, se confirma enseguida!", "Ya cierro tu pedido. Gracias!")
            vis13 = procesar_mensaje("56955556666", "dos churrascos italianos para retiro, tarjeta",
                                     {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([sin_total]),
                                     nucleo=nucleo13, conv_db=conv13)
            chk("retiro cerrado: el cliente recibe el total aunque el modelo no lo diga",
                "✅ Total: $8.900" in vis13 and len(nucleo13.cola()) == 1)
            # (b) El modelo dice un total EQUIVOCADO: el del codigo va igual, y va ULTIMO.
            con_error = respuesta_cerrada.replace("Total $8.900", "Total $9.800")
            vis13b = procesar_mensaje("56955557777", "dos churrascos italianos retiro tarjeta",
                                      {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([con_error]),
                                      nucleo=nucleo13, conv_db=conv13)
            chk("si el modelo dice un total equivocado, el bueno sale igual y queda al final",
                "$9.800" in vis13b and "✅ Total: $8.900" in vis13b
                and vis13b.index("$9.800") < vis13b.index("✅ Total: $8.900"))
            # (c) ...pero si YA dijo el correcto, NO se repite (dos totales en pantalla).
            vis13c = procesar_mensaje("56955558888", "dos churrascos italianos retiro tarjeta",
                                      {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([respuesta_cerrada]),
                                      nucleo=nucleo13, conv_db=conv13)
            chk("si el modelo ya dijo el total correcto, el codigo no lo repite",
                vis13c.count("$8.900") == 1)

            # (d) CANDADO DE REGRESION: delivery con tarifa ya conocida. El aviso del envio
            # se decidia mirando "aviso_cliente", que hasta hoy solo existia en la rama de
            # transferencia; ahora la entrega tambien trae uno, asi que este mensaje habria
            # dejado de mandarse en silencio. Va con espia porque sale FUERA del visible.
            _buscar_orig13 = _buscar_cobro_direccion
            _wa_orig13, _wa13 = enviar_whatsapp, []

            def _espia13(telefono, texto, cfg, intentos=2):
                _wa13.append((telefono, texto))

            _buscar_cobro_direccion = lambda tel, dire, cfg: 1500   # noqa: E731
            enviar_whatsapp = _espia13
            try:
                deliv13 = respuesta_cerrada.replace(
                    '"modalidad":"retiro","direccion":null',
                    '"modalidad":"delivery","direccion":"Pasaje Ficticio 456"')
                procesar_mensaje("56955559999", "2 italianos delivery a Pasaje Ficticio 456, tarjeta",
                                 {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([deliv13]),
                                 nucleo=nucleo13, conv_db=conv13)
            finally:
                _buscar_cobro_direccion = _buscar_orig13
                enviar_whatsapp = _wa_orig13
            _envios13 = [t for _, t in _wa13 if "El envío a tu dirección es $1.500" in t]
            chk("delivery con tarifa conocida: al cliente le sigue llegando el aviso del envio",
                len(_envios13) == 1)
            chk("...y el total que lee incluye ese envio ($8.900 + $1.500)",
                any("✅ Total: $10.400" in t for _, t in _wa13))
        finally:
            RESPALDO_DB = _respaldo_orig13

    print("== PRECEDENCIA del cobro de envio (3-ago-2026): planilla > historial del POS > jefe ==")
    with carpeta_temporal() as tmp14:
        _respaldo_orig14 = RESPALDO_DB
        RESPALDO_DB = Path(tmp14) / "respaldo_planilla.db"
        _wa_orig14, _wa14 = enviar_whatsapp, []

        def _espia14(telefono, texto, cfg, intentos=2):
            _wa14.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia14
        try:
            db14 = Path(tmp14) / "pedidos_planilla.db"
            conv14 = Path(tmp14) / "conv_planilla.db"
            nucleo14 = NucleoPedidos(db_path=db14)
            # Calle real de la planilla (Los Cerezos, sobre el corte de 600 -> 1.500).
            deliv14 = respuesta_cerrada.replace(
                '"modalidad":"retiro","direccion":null',
                '"modalidad":"delivery","direccion":"Los Cerezos 750"')
            procesar_mensaje("56911114444", "2 italianos delivery a Los Cerezos 750, tarjeta",
                             {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([deliv14]),
                             nucleo=nucleo14, conv_db=conv14)
            chk("una calle de la planilla se cobra SOLA, sin pasar por el jefe",
                contar_esperando_cobro() == 0 and len(nucleo14.cola()) == 1)
            chk("...con el precio de la planilla (1.500, tramo sobre el corte de 600)",
                nucleo14.cola()[0].get("delivery_cobro") == 1500)
            chk("y el cliente lo lee sin que nadie del local haya escrito nada",
                any(n == "56911114444" and "El envío a tu dirección es $1.500" in t
                    for n, t in _wa14))

            # La planilla GANA aunque el historial del POS tenga otro monto guardado para
            # la misma direccion (precedencia decidida por Gonza, 3-ago-2026: 1o planilla).
            _buscar_orig14 = _buscar_cobro_direccion
            _buscar_cobro_direccion = lambda tel, dire, cfg: 9999   # noqa: E731
            try:
                deliv14b = respuesta_cerrada.replace(
                    '"modalidad":"retiro","direccion":null',
                    '"modalidad":"delivery","direccion":"Los Cerezos 750"')
                procesar_mensaje("56911115555", "2 italianos delivery a Los Cerezos 750, tarjeta",
                                 {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([deliv14b]),
                                 nucleo=nucleo14, conv_db=conv14)
            finally:
                _buscar_cobro_direccion = _buscar_orig14
            cola14 = nucleo14.cola()
            chk("la planilla le gana al historial del POS (1.500, nunca el 9.999 del mock)",
                any(p.get("delivery_cobro") == 1500 for p in cola14)
                and not any(p.get("delivery_cobro") == 9999 for p in cola14))

            # Cuando la planilla NO calza (direccion inventada), el historial del POS
            # sigue funcionando -- no quedo tapado por el paso nuevo.
            _buscar_cobro_direccion = lambda tel, dire, cfg: 1700   # noqa: E731
            try:
                deliv14c = respuesta_cerrada.replace(
                    '"modalidad":"retiro","direccion":null',
                    '"modalidad":"delivery","direccion":"Pasaje Ficticio 321"')
                procesar_mensaje("56911116666", "2 italianos delivery a Pasaje Ficticio 321, tarjeta",
                                 {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([deliv14c]),
                                 nucleo=nucleo14, conv_db=conv14)
            finally:
                _buscar_cobro_direccion = _buscar_orig14
            chk("sin match en la planilla, el historial del POS resuelve igual que antes",
                any(p.get("delivery_cobro") == 1700 for p in nucleo14.cola()))
        finally:
            RESPALDO_DB = _respaldo_orig14
            enviar_whatsapp = _wa_orig14

    print("== 03-ago-2026 (pregunta 1): avisar cuando una calle se repite sin match ==")
    with carpeta_temporal() as tmp15:
        db15 = Path(tmp15) / "respaldo_calles.db"
        _registrar_calle_sin_planilla("Pasaje Nuevo 123", db_path=db15)
        lista15a = listar_calles_sin_planilla(db_path=db15)
        chk("calle nueva: queda con conteo 1",
            len(lista15a) == 1 and lista15a[0]["conteo"] == 1
            and lista15a[0]["calle"] == "Pasaje Nuevo 123")
        # Numeros de casa distintos de la MISMA calle se agrupan juntos (no por
        # direccion exacta) -- es la calle la que hay que agregar, no cada numero.
        _registrar_calle_sin_planilla("Pasaje Nuevo 456", db_path=db15)
        _registrar_calle_sin_planilla("pasaje   nuevo 789", db_path=db15)   # espacios/mayusculas
        lista15b = listar_calles_sin_planilla(db_path=db15)
        chk("se agrupan por calle (sin el numero): sigue habiendo 1 sola fila",
            len(lista15b) == 1 and lista15b[0]["conteo"] == 3)
        _registrar_calle_sin_planilla("Los Alamos 50", db_path=db15)
        chk("una calle DISTINTA no se mezcla con la anterior",
            len(listar_calles_sin_planilla(db_path=db15)) == 2)
    with carpeta_temporal() as tmp16:
        db16 = Path(tmp16) / "respaldo_calles2.db"
        import io
        from contextlib import redirect_stdout
        _buf16 = io.StringIO()
        with redirect_stdout(_buf16):   # solo captura estos prints -- los chk() de abajo
            for _ in range(CORTE_AVISO_CALLE_SIN_PLANILLA - 1):
                _registrar_calle_sin_planilla("Calle Repetida 10", db_path=db16)
        chk("todavia no avisa antes de llegar al corte",
            "[planilla][AVISO]" not in _buf16.getvalue())
        _buf16 = io.StringIO()
        with redirect_stdout(_buf16):
            _registrar_calle_sin_planilla("Calle Repetida 20", db_path=db16)
        chk("al llegar al corte, avisa UNA vez",
            _buf16.getvalue().count("[planilla][AVISO]") == 1)
        _buf16 = io.StringIO()
        with redirect_stdout(_buf16):
            _registrar_calle_sin_planilla("Calle Repetida 30", db_path=db16)
            _registrar_calle_sin_planilla("Calle Repetida 40", db_path=db16)
        chk("seguir fallando NO repite el aviso (una vez por calle, no un spam)",
            "[planilla][AVISO]" not in _buf16.getvalue())

    print("== JEFE DE RESPALDO: mismas funciones, y turno propio antes de avanzar solo ==")
    with carpeta_temporal() as tmp12:
        _respaldo_orig12 = RESPALDO_DB
        RESPALDO_DB = Path(tmp12) / "respaldo_backup.db"
        _wa_orig12 = enviar_whatsapp
        _wa12 = []

        def _espia12(telefono, texto, cfg, intentos=2):
            _wa12.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia12
        # 18-ago-2026: al respaldo ya no le llega texto libre sino una PLANTILLA, asi que
        # espiar solo enviar_whatsapp dejaria de ver el escalamiento entero.
        _pl_orig12 = enviar_plantilla_whatsapp
        _pl12 = []

        def _espia_pl12(telefono, plantilla, variables, cfg,
                        idioma=PLANTILLA_IDIOMA, intentos=2):
            _pl12.append((telefono, plantilla, list(variables)))
            return {"simulado": True}
        enviar_plantilla_whatsapp = _espia_pl12
        try:
            db12 = Path(tmp12) / "pedidos_backup.db"
            conv12 = Path(tmp12) / "conv_backup.db"
            nucleo12 = NucleoPedidos(db_path=db12)
            # 03-ago-2026: el respaldo es la esposa de Christian, socia y duena del local.
            # Mismos tratos y privilegios que el (decision de Gonza) -- antes este mismo
            # test afirmaba lo contrario, que solo se le escribia en emergencias.
            chk("el backup recibe TAMBIEN las alertas de rutina (es socia, no un contacto de emergencia)",
                JEFE_BACKUP in NUMS_ALERTAS_JEFE)
            # 10-ago-2026 -> revertido 13-sep-2026: las alertas dejaron de ir al telefono
            # del mostrador (nadie lo miraba). El pedido explicito de Gonza el 13-sep-2026
            # fue simetria total entre jefe1 (JEFE_NUM) y jefe2 (JEFE_BACKUP): ambos
            # mandan comandos Y reciben las alertas de rutina.
            chk("las alertas van a los DOS jefes vigentes, jefe1 y jefe2",
                set(NUMS_ALERTAS_JEFE) == {JEFE_NUM, JEFE_BACKUP})
            chk("...y el telefono del mostrador (jefe1) vuelve a recibirlas",
                JEFE_NUM in NUMS_ALERTAS_JEFE)
            respuesta_del12 = respuesta_cerrada.replace(
                '"modalidad":"retiro","direccion":null',
                '"modalidad":"delivery","direccion":"Los Aromos 12"')
            procesar_mensaje("56922223333", "2 italianos delivery tarjeta", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso([respuesta_del12]), nucleo=nucleo12,
                             conv_db=conv12)
            chk("el pedido queda esperando cobro", contar_esperando_cobro() == 1)
            chk("recien tomado, al backup ya le llega la alerta de rutina (como a los otros dos)",
                len([t for n, t in _wa12 if n == JEFE_BACKUP]) == 1)
            chk("...pero todavia NO es un escalamiento",
                not [t for n, t in _wa12 if n == JEFE_BACKUP and "RESPALDO" in t]
                and not _pl12)
            _c12 = _con_respaldo()
            _c12.execute("UPDATE esperando_cobro SET creado_en=? WHERE resuelto=0",
                         ((datetime.now() - timedelta(minutes=ESPERA_COBRO_MINUTOS + 1)
                           ).isoformat(timespec="seconds"),))
            _c12.commit(); _c12.close()
            escalar_esperas_al_backup({**CONFIG_DEFAULT})
            al_backup12 = [v for n, p, v in _pl12 if n == JEFE_BACKUP]
            chk("vencido el turno del local, el backup recibe el pedido completo -- por "
                "PLANTILLA, lo unico que Meta le entrega fuera de la ventana (18-ago-2026)",
                len(al_backup12) == 1 and "Juan Test" in al_backup12[0][1])
            chk("...y la plantilla dice DE QUE espera se trata, no solo que hay algo",
                "cobro del envío" in al_backup12[0][0])
            chk("y el jefe del local se entera de que se escalo",
                any("respaldo" in t.lower() for n, t in _wa12 if n in NUMS_ALERTAS_JEFE))
            # El backup responde como jefe: mismo comando, mismo efecto.
            vis_b12 = procesar_mensaje(JEFE_BACKUP, "envío 1500", {**CONFIG_DEFAULT},
                                       cerebro=_CerebroFalso(["no deberia llamarse"]),
                                       nucleo=nucleo12, conv_db=conv12)
            chk("'envío 1500' del BACKUP resuelve el pedido escalado",
                "Juan Test" in vis_b12 and contar_esperando_cobro() == 0)
            cola12 = nucleo12.cola()
            chk("...y la comanda lleva su monto", len(cola12) == 1
                and cola12[0].get("delivery_cobro") == 1500)
            # Y tambien puede confirmar transferencias (mismas funciones que el jefe).
            _guardar_esperando_comprobante(
                {"canal": "whatsapp", "cliente": "Transf Backup", "modalidad": "retiro",
                 "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                            "cantidad": 1}], "forma_pago": "transferencia", "direccion": None,
                 "delivery_cobro": 0, "notas": "", "telefono": "911112222"}, "56911112222")
            vis_b12c = procesar_mensaje(JEFE_BACKUP, "confirmado", {**CONFIG_DEFAULT},
                                        cerebro=_CerebroFalso(["no deberia llamarse"]),
                                        nucleo=nucleo12, conv_db=conv12)
            chk("'confirmado' del BACKUP despacha la transferencia retenida",
                "Transf Backup" in vis_b12c and contar_esperando_comprobante() == 0
                and len(nucleo12.cola()) == 2)
        finally:
            RESPALDO_DB = _respaldo_orig12
            enviar_whatsapp = _wa_orig12
            enviar_plantilla_whatsapp = _pl_orig12

    print("== caducidad del historial: conversacion vieja arranca fresca ==")
    with carpeta_temporal() as tmp5:
        conv5 = Path(tmp5) / "conv_vieja.db"
        guardar_turno("56900001111", "usuario", "quiero pedir algo", db_path=conv5)
        chk("historial reciente se conserva", len(historial("56900001111", db_path=conv5)) == 1)
        import sqlite3 as _sq
        _viejo = (datetime.now() - timedelta(hours=HORAS_CADUCIDAD_HISTORIAL + 1)
                  ).isoformat(timespec="seconds")
        _c5 = _sq.connect(conv5)
        _c5.execute("UPDATE turnos SET creado_en=?", (_viejo,))
        _c5.commit(); _c5.close()
        chk(f"historial de hace >{HORAS_CADUCIDAD_HISTORIAL}h se descarta (arranca fresca)",
            historial("56900001111", db_path=conv5) == [])

    print("== flujo JEFE: instrucciones del dia se persisten en archivo APARTE (no toca el real) ==")
    import prototipo as _proto
    orig_instr, orig_mej = _proto.INSTRUCCIONES, _proto.MEJORAS
    with carpeta_temporal() as tmp:
        _proto.INSTRUCCIONES = Path(tmp) / "instrucciones_test.txt"
        _proto.MEJORAS = Path(tmp) / "mejoras_test.md"
        try:
            respuesta_jefe = (
                "Listo jefe, anotado: solo retiro hoy.\n"
                "<INSTRUCCIONES_LOCAL>\nsolo retiro hoy\n</INSTRUCCIONES_LOCAL>"
            )
            cerebro_falso_jefe = _CerebroFalso([respuesta_jefe])
            with carpeta_temporal() as tmp2:
                conv_db2 = Path(tmp2) / "conv_jefe.db"
                nucleo_dummy = NucleoPedidos(db_path=Path(tmp2) / "pedidos_dummy.db")
                payload_jefe = {"entry": [{"changes": [{"value": {"messages": [
                    {"type": "text", "from": JEFE_NUM, "text": {"body": "hoy solo retiro"}}
                ]}}]}]}
                procesar_payload_meta(payload_jefe, CONFIG_DEFAULT, cerebro=cerebro_falso_jefe,
                                       nucleo=nucleo_dummy, conv_db=conv_db2)
            chk("instrucciones_local (de PRUEBA) quedo con la instruccion del jefe",
                _proto.INSTRUCCIONES.exists() and "solo retiro" in _proto.INSTRUCCIONES.read_text(encoding="utf-8"))
            chk("quedo fechada con el dia de HOY (Chile)",
                _proto.INSTRUCCIONES.read_text(encoding="utf-8").startswith(
                    "FECHA: " + datetime.now(_proto.TZ_CHILE).strftime("%Y-%m-%d")))
        finally:
            _proto.INSTRUCCIONES, _proto.MEJORAS = orig_instr, orig_mej

    print("== S2: las instrucciones del dia expiran solas a la madrugada (no quedan pegadas) ==")
    with carpeta_temporal() as tmp_h:
        _proto.INSTRUCCIONES = Path(tmp_h) / "instrucciones_test3.txt"
        try:
            hoy_h = datetime.now(_proto.TZ_CHILE).strftime("%Y-%m-%d")
            ayer_h = (datetime.now(_proto.TZ_CHILE) - timedelta(days=1)).strftime("%Y-%m-%d")
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_h}\nsolo retiro hoy\n", encoding="utf-8")
            chk("instruccion fechada HOY sigue vigente", "solo retiro" in _proto.leer_instrucciones())
            _proto.INSTRUCCIONES.write_text(f"FECHA: {ayer_h}\nsolo retiro hoy\n", encoding="utf-8")
            chk("instruccion fechada AYER ya expiro sola (nadie tuvo que borrarla a mano)",
                _proto.leer_instrucciones() == "(ninguna, opera normal)")
            _proto.INSTRUCCIONES.write_text("solo retiro hoy\n", encoding="utf-8")
            chk("formato viejo sin fecha (de antes de este cambio) tambien se trata como vencido",
                _proto.leer_instrucciones() == "(ninguna, opera normal)")
        finally:
            _proto.INSTRUCCIONES = orig_instr

    print("== conocimiento_local DESCONECTADO (28-jul-2026): no contamina el prompt ==")
    # Antes este bloque probaba lo contrario (que el archivo SIEMPRE llegara al prompt).
    # Se dio vuelta a proposito: el archivo nunca tuvo una regla real de Christian, pero
    # inyectaba 2.039 caracteres de instructivo ("abri este archivo en GitHub y toca el
    # lapiz") presentados al modelo como "reglas permanentes". Ahora es candado
    # anti-regresion: si alguien reconecta la inyeccion sin querer, esto falla.
    orig_conoc = _proto.CONOCIMIENTO_LOCAL
    with carpeta_temporal() as tmp8:
        _proto.CONOCIMIENTO_LOCAL = Path(tmp8) / "conocimiento_test.md"
        try:
            _proto.CONOCIMIENTO_LOCAL.write_text(
                "El Italiano XL es As/Vienesa XL con tomate, palta y mayo, nunca churrasco.",
                encoding="utf-8")
            sistema_cliente = _proto.construir_sistema(jefe=False)
            sistema_jefe = _proto.construir_sistema(jefe=True)
            chk("el archivo NO llega al prompt del CLIENTE",
                "Italiano XL es As/Vienesa XL" not in sistema_cliente)
            chk("el archivo NO llega al prompt del JEFE",
                "Italiano XL es As/Vienesa XL" not in sistema_jefe)
            chk("ya no queda el encabezado 'LO QUE EL LOCAL TE ENSEÑO'",
                "LO QUE EL LOCAL TE ENSEÑ" not in sistema_cliente)
            # La funcion sigue viva y correcta para el dia que se reconecte (ver su docstring).
            chk("leer_conocimiento_local() sigue funcionando por si sola",
                "Italiano XL es As/Vienesa XL" in _proto.leer_conocimiento_local())
            _proto.CONOCIMIENTO_LOCAL.unlink()
            chk("sin archivo no revienta (devuelve vacio)",
                _proto.leer_conocimiento_local() == "")
        finally:
            _proto.CONOCIMIENTO_LOCAL = orig_conoc

    print("== cierre_pedido.md se inyecta AL FINAL del prompt (28-jul-2026) ==")
    _sis_cli = _proto.construir_sistema(jefe=False)
    _sis_jefe = _proto.construir_sistema(jefe=True)
    chk("la funcion emitir_pedido esta nombrada en el prompt del cliente",
        "emitir_pedido" in _sis_cli)
    # OJO: el BLOQUE 1 ya NOMBRA emitir_pedido antes del menu, asi que se compara contra
    # el encabezado unico del archivo de cierre, no contra la primera mencion.
    chk("va DESPUES del menu (es lo ultimo que lee el modelo)",
        _sis_cli.index("FORMATO DEL CIERRE") > _sis_cli.index("DATOS DEL LOCAL Y MENU"))
    chk("el checklist de las 4 reglas criticas cierra el prompt",
        "REVISA ESTAS 4" in "\n".join(_sis_cli.splitlines()[-12:]))
    chk("el modo JEFE no lleva el bloque de cierre (no toma pedidos)",
        "emitir_pedido" not in _sis_jefe)
    chk("las 4 reglas duras estan en el BLOQUE 1 del prompt de cliente",
        "LAS 4 QUE NUNCA SE ROMPEN" in _sis_cli)

    print("== candado del POS: el bot manda el token que el POS exige por el tunel ==")
    import os as _os2
    _orig_tok = _os2.environ.pop("POS_BOT_TOKEN", None)
    _orig_cid = _os2.environ.pop("POS_ACCESS_CLIENT_ID", None)
    _orig_sec = _os2.environ.pop("POS_ACCESS_CLIENT_SECRET", None)
    try:
        # 20-jul-2026: ahora SIEMPRE viaja un User-Agent propio (sin el, Cloudflare
        # bloqueaba al bot con 403/1010 por venir como 'Python-urllib'). Lo que se
        # sigue exigiendo aca es que SIN credenciales no se filtre ningun header de
        # AUTENTICACION -- que era la intencion original de este chequeo.
        _h_sin = _headers_pos({"Content-Type": "application/json"})
        chk("sin variables: no manda ningun header de autenticacion",
            "X-Bot-Token" not in _h_sin and "CF-Access-Client-Id" not in _h_sin
            and "CF-Access-Client-Secret" not in _h_sin)
        chk("pero SI manda el User-Agent propio (evita el bloqueo 1010 de Cloudflare)",
            _h_sin.get("User-Agent", "").startswith("BotPatata/"))
        chk("y respeta los headers que le pasaron",
            _h_sin["Content-Type"] == "application/json")
        # Opcion B (la que usamos): candado por token compartido en el codigo del POS.
        _os2.environ["POS_BOT_TOKEN"] = "tok_bot_secreto"
        chk("con POS_BOT_TOKEN: manda el header X-Bot-Token (Opcion B)",
            _headers_pos().get("X-Bot-Token") == "tok_bot_secreto")
        _os2.environ.pop("POS_BOT_TOKEN", None)
        # Opcion A (soportada por si algun dia se activa Cloudflare Access).
        _os2.environ["POS_ACCESS_CLIENT_ID"] = "id123"
        chk("solo UNA variable de Access: sigue sin mandar nada (evita candado a medias)",
            "CF-Access-Client-Id" not in _headers_pos())
        _os2.environ["POS_ACCESS_CLIENT_SECRET"] = "secreto123"
        h = _headers_pos()
        chk("con las 2 variables de Access: manda el service token de Cloudflare",
            h.get("CF-Access-Client-Id") == "id123" and h.get("CF-Access-Client-Secret") == "secreto123")
    finally:
        for _k, _v in (("POS_BOT_TOKEN", _orig_tok), ("POS_ACCESS_CLIENT_ID", _orig_cid),
                       ("POS_ACCESS_CLIENT_SECRET", _orig_sec)):
            if _v is None:
                _os2.environ.pop(_k, None)
            else:
                _os2.environ[_k] = _v

    print("== BOT_DATA_DIR (Fase 2): las bases del bot sobreviven a un redeploy si hay volumen ==")
    with carpeta_temporal() as tmp9:
        _volumen = Path(tmp9) / "volumen_railway"
        _os2.environ["BOT_DATA_DIR"] = str(_volumen)
        try:
            import importlib as _importlib2
            import importlib.util as _ilu
            _importlib2.reload(_proto)   # recalcula DATOS_DIR/INSTRUCCIONES/MEJORAS con la var nueva
            chk("DATOS_DIR paso a apuntar al volumen (no a la carpeta del codigo)",
                _proto.DATOS_DIR == _volumen and _volumen.exists())
            chk("instrucciones_local.txt del volumen (sobrevive a un redeploy)",
                _proto.INSTRUCCIONES == _volumen / "instrucciones_local.txt")
            # Carga una copia FRESCA de webhook.py bajo otro nombre (sin tocar el
            # __main__ que esta corriendo este mismo test) para confirmar que
            # CONV_DB/RESPALDO_DB tambien migran al volumen.
            _spec = _ilu.spec_from_file_location("webhook_recargado", __file__)
            _wh2 = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_wh2)
            chk("conversaciones_bot.db tambien apunta al volumen",
                _wh2.CONV_DB == _volumen / "conversaciones_bot.db")
            chk("respaldo_pedidos.db (cola de reintento) tambien apunta al volumen",
                _wh2.RESPALDO_DB == _volumen / "respaldo_pedidos.db")
        finally:
            _os2.environ.pop("BOT_DATA_DIR", None)
            _importlib2.reload(_proto)   # deja prototipo como estaba para el resto del --test

    print("== S2: mensajes no soportados (audio/foto/ubicacion) no rompen el webhook "
          "y ahora reciben una respuesta minima (antes quedaban en visto) ==")
    payload_raro = {"entry": [{"changes": [{"value": {"messages": [
        {"type": "image", "from": "56911111111"}
    ]}}]}]}
    with carpeta_temporal() as tmp:
        visibles_raro = procesar_payload_meta(
            payload_raro, CONFIG_DEFAULT,
            nucleo=NucleoPedidos(db_path=Path(tmp) / "p.db"), conv_db=Path(tmp) / "c.db")
    chk("mensaje tipo imagen no crashea y devuelve una respuesta",
        visibles_raro == [MENSAJE_TIPO_NO_SOPORTADO])
    payload_raro2 = {"entry": [{"changes": [{"value": {"messages": [
        {"type": "audio", "from": "56911111112"},
        {"type": "location", "from": "56911111113"},
    ]}}]}]}
    with carpeta_temporal() as tmp:
        visibles_raro2 = procesar_payload_meta(
            payload_raro2, CONFIG_DEFAULT,
            nucleo=NucleoPedidos(db_path=Path(tmp) / "p.db"), conv_db=Path(tmp) / "c.db")
    chk("audio Y ubicacion tambien reciben la respuesta minima, cada uno la suya",
        visibles_raro2 == [MENSAJE_TIPO_NO_SOPORTADO, MENSAJE_TIPO_NO_SOPORTADO])

    print("== 29-sep-2026 (Christian, WhatsApp): una imagen o PDF con comprobante "
          "pendiente ya NO se rebota con 'solo leo texto' ==")
    _resp_orig93, _wa_orig93 = RESPALDO_DB, enviar_whatsapp
    RESPALDO_DB = Path(tempfile.mkdtemp()) / "respaldo_comprobante_img.db"
    enviar_whatsapp = lambda telefono, texto, cfg, intentos=2: {"simulado": True}
    try:
        tel_img = "56922223333"
        _guardar_esperando_comprobante({"cliente": "X", "items": []}, tel_img)
        payload_img = {"entry": [{"changes": [{"value": {"messages": [
            {"type": "image", "from": tel_img}
        ]}}]}]}
        with carpeta_temporal() as tmp_img:
            conv_img = Path(tmp_img) / "c.db"
            vis_img = procesar_payload_meta(
                payload_img, CONFIG_DEFAULT,
                nucleo=NucleoPedidos(db_path=Path(tmp_img) / "p.db"), conv_db=conv_img)
            chk("con comprobante pendiente, la imagen se toma como 'ya transferi'",
                vis_img == [MENSAJE_TRANSFERENCIA_AVISADA])
            chk("...y el intercambio queda anotado en el historial de la conversacion",
                [t["texto"] for t in historial(tel_img, db_path=conv_img)]
                == ["[image recibido]", MENSAJE_TRANSFERENCIA_AVISADA])
        tel_pdf = "56922223334"
        _guardar_esperando_comprobante({"cliente": "X", "items": []}, tel_pdf)
        payload_pdf = {"entry": [{"changes": [{"value": {"messages": [
            {"type": "document", "from": tel_pdf}
        ]}}]}]}
        with carpeta_temporal() as tmp_pdf:
            vis_pdf = procesar_payload_meta(
                payload_pdf, CONFIG_DEFAULT,
                nucleo=NucleoPedidos(db_path=Path(tmp_pdf) / "p.db"),
                conv_db=Path(tmp_pdf) / "c.db")
            chk("un PDF cuenta igual que una imagen", vis_pdf == [MENSAJE_TRANSFERENCIA_AVISADA])
        tel_audio = "56922223335"
        _guardar_esperando_comprobante({"cliente": "X", "items": []}, tel_audio)
        payload_audio = {"entry": [{"changes": [{"value": {"messages": [
            {"type": "audio", "from": tel_audio}
        ]}}]}]}
        with carpeta_temporal() as tmp_audio:
            vis_audio = procesar_payload_meta(
                payload_audio, CONFIG_DEFAULT,
                nucleo=NucleoPedidos(db_path=Path(tmp_audio) / "p.db"),
                conv_db=Path(tmp_audio) / "c.db")
            chk("un audio, aunque haya comprobante pendiente, sigue con la respuesta "
                "generica (no todo adjunto es un comprobante)",
                vis_audio == [MENSAJE_TIPO_NO_SOPORTADO])
    finally:
        RESPALDO_DB = _resp_orig93
        enviar_whatsapp = _wa_orig93

    print("== S1: dedupe de reintentos de Meta -- el MISMO msg id no duplica la comanda ==")
    with carpeta_temporal() as tmp10:
        db10 = Path(tmp10) / "pedidos_dedupe.db"
        conv10 = Path(tmp10) / "conv_dedupe.db"
        nucleo10 = NucleoPedidos(db_path=db10)
        payload_dup = {"entry": [{"changes": [{"value": {"messages": [
            {"id": "wamid.IDENTICO123", "type": "text", "from": "56955554444",
             "text": {"body": "2 italianos retiro tarjeta"}}
        ]}}]}]}
        ids1 = _ids_del_payload(payload_dup)
        chk("el payload trae el id del mensaje", ids1 == ["wamid.IDENTICO123"])
        nuevos1 = _marcar_vistos_y_filtrar(ids1, db_path=conv10)
        chk("primera vez: el id se marca como nuevo", nuevos1 == {"wamid.IDENTICO123"})
        procesar_payload_meta(payload_dup, CONFIG_DEFAULT, permitidos=nuevos1,
                              cerebro=_CerebroFalso([respuesta_cerrada]), nucleo=nucleo10,
                              conv_db=conv10)
        chk("primer envio: el pedido SI entra a la cola", len(nucleo10.cola()) == 1)

        # Meta reenvia EXACTAMENTE el mismo payload (mismo msg id) -- el reintento real.
        nuevos2 = _marcar_vistos_y_filtrar(_ids_del_payload(payload_dup), db_path=conv10)
        chk("reintento: el id YA estaba visto, no se vuelve a marcar como nuevo", nuevos2 == set())
        procesar_payload_meta(payload_dup, CONFIG_DEFAULT, permitidos=nuevos2,
                              cerebro=_CerebroFalso([respuesta_cerrada]), nucleo=nucleo10,
                              conv_db=conv10)
        chk("reintento: la comanda NO se duplico", len(nucleo10.cola()) == 1)

    print("== S1: error del cerebro -- NO llega crudo al cliente ni queda en el historial ==")
    with carpeta_temporal() as tmp11:
        conv11 = Path(tmp11) / "conv_error.db"
        cerebro_error = _CerebroFalso(["[ERROR de la API de OpenAI: 429] limite de uso alcanzado, "
                                       "reintenta en unos segundos"])
        vis_err = procesar_mensaje("56933332222", "hola quiero pedir", CONFIG_DEFAULT,
                                   cerebro=cerebro_error, conv_db=conv11)
        chk("el cliente NO ve el error crudo", "[ERROR" not in vis_err)
        chk("el cliente ve un mensaje humano en su lugar", "minutito" in vis_err.lower())
        chk("nada quedo guardado en el historial (ni la pregunta ni el error)",
            historial("56933332222", db_path=conv11) == [])

    print("== S1: concurrencia -- clientes DISTINTOS no se bloquean; el MISMO cliente queda en orden ==")

    class _CerebroLento:
        """Simula una llamada lenta al LLM y registra cuando entra/sale cada
        llamada -- para probar que clientes distintos corren en paralelo (no se
        congela el bot entero con uno lento) y que el MISMO cliente no se solapa."""
        def __init__(self, demora, eventos):
            self._demora, self._eventos = demora, eventos

        def pensar(self, mensajes, tools=None, tool_choice=None):
            self._eventos.append(("entra", _time.time()))
            _time.sleep(self._demora)
            self._eventos.append(("sale", _time.time()))
            texto = "Hola! bienvenido a Patata, que te gustaria pedir?"
            return (texto, None) if tools else texto

    with carpeta_temporal() as tmp12:
        conv12 = Path(tmp12) / "conv_concurrencia.db"

        # 04-ago-2026: hasta S2.5 punto 4 (27-jul), el primer mensaje de una conversacion
        # nueva consultaba al POS EN VIVO (_nota_cliente_conocido) y este bloque mockeaba
        # urlopen para que esa llamada no demorara segundos reales. Ya no hace falta:
        # _nota_cliente_conocido ahora lee CLIENTES_CACHE (archivo local, sin red) -- este
        # bloque mide SOLO el paralelismo del lock por telefono, que es lo que prueba.

        # (a) dos CLIENTES DISTINTOS casi al mismo tiempo -> no deben esperarse entre si.
        eventos_a, eventos_b = [], []
        t_inicio = _time.time()

        def _lanzar(payload, eventos):
            procesar_payload_meta(payload, CONFIG_DEFAULT,
                                  cerebro=_CerebroLento(0.4, eventos), conv_db=conv12)

        hiloA = _threading.Thread(target=_lanzar, args=(
            {"entry": [{"changes": [{"value": {"messages": [
                {"id": "wamid.A1", "type": "text", "from": "56911111111", "text": {"body": "hola"}}
            ]}}]}]}, eventos_a))
        hiloB = _threading.Thread(target=_lanzar, args=(
            {"entry": [{"changes": [{"value": {"messages": [
                {"id": "wamid.B1", "type": "text", "from": "56922222222", "text": {"body": "hola"}}
            ]}}]}]}, eventos_b))
        hiloA.start(); hiloB.start()
        hiloA.join(); hiloB.join()
        chk("2 clientes distintos con 0.4s de demora cada uno terminan en paralelo (< 0.7s, no 0.8s)",
            (_time.time() - t_inicio) < 0.7)

        # (b) el MISMO cliente manda 2 mensajes casi juntos -> deben quedar en orden, sin solape.
        eventos_c = []
        payload_c1 = {"entry": [{"changes": [{"value": {"messages": [
            {"id": "wamid.C1", "type": "text", "from": "56933333333", "text": {"body": "hola"}}
        ]}}]}]}
        payload_c2 = {"entry": [{"changes": [{"value": {"messages": [
            {"id": "wamid.C2", "type": "text", "from": "56933333333", "text": {"body": "agregame algo"}}
        ]}}]}]}
        hiloC1 = _threading.Thread(target=_lanzar, args=(payload_c1, eventos_c))
        hiloC2 = _threading.Thread(target=_lanzar, args=(payload_c2, eventos_c))
        hiloC1.start()
        _time.sleep(0.05)   # asegura que C1 ya tomo el lock antes de que arranque C2
        hiloC2.start()
        hiloC1.join(); hiloC2.join()
        chk("el mismo cliente: ambas llamadas al cerebro corrieron, sin solaparse (lock por telefono)",
            len(eventos_c) == 4 and eventos_c[1][1] <= eventos_c[2][1])

    print("== S2.5 punto 3: memoria de pedido reciente (~30 min) -- el bot no ignora un "
          "pedido que acaba de cerrar ==")
    _respaldo_orig_mem = RESPALDO_DB
    with carpeta_temporal() as tmp_mem:
        RESPALDO_DB = Path(tmp_mem) / "respaldo_memoria.db"
        try:
            with carpeta_temporal() as tmp_mem2:
                conv_mem = Path(tmp_mem2) / "conv_memoria.db"
                nucleo_mem = NucleoPedidos(db_path=Path(tmp_mem2) / "pedidos_memoria.db")
                cerebro_cierre = _CerebroFalso([respuesta_cerrada])
                procesar_mensaje("56911119999", "2 italianos retiro tarjeta", CONFIG_DEFAULT,
                                 cerebro=cerebro_cierre, nucleo=nucleo_mem, conv_db=conv_mem)
                chk("tras despachar, queda memoria del pedido reciente",
                    _nota_pedido_reciente("56911119999") is not None)

                cerebro_seguimiento = _CerebroFalso(["Ya te lo agrego, dame un segundo."])
                procesar_mensaje("56911119999", "oye agregame una bebida mas", CONFIG_DEFAULT,
                                 cerebro=cerebro_seguimiento, nucleo=nucleo_mem, conv_db=conv_mem)
                chk("dentro de la ventana de poscierre el modelo recibe el HILO del pedido "
                    "-- mas que la nota, y es lo que le permite aplicar la 3.10 (18-ago)",
                    cerebro_seguimiento.ultimos_mensajes is not None and
                    any("2 italianos retiro tarjeta" in (m["texto"] or "")
                        for m in cerebro_seguimiento.ultimos_mensajes
                        if m["rol"] == "usuario"))

                # EL CASO REAL que motivo todo esto (chat del 15-ago, 23:16:08 -> 23:16:18):
                # el cliente escribio "A eso me referia" DIEZ SEGUNDOS despues de la
                # confirmacion y el bot le contesto con la bienvenida completa, como a un
                # desconocido. No era el modelo desobedeciendo la 3.10: el historial se
                # vaciaba al cerrar, y el candado del saludo se apoya en esa misma señal
                # (historial vacio = conversacion nueva), asi que lo dejaba pasar.
                cerebro_saludo_post = _CerebroFalso(
                    ["Hola! Bienvenid@ a Patata Huechuraba 🌭🍟\n¡Gracias a ti! "
                     "Cualquier cosa me avisas."])
                vis_post = procesar_mensaje("56911119999", "A eso me referia", CONFIG_DEFAULT,
                                            cerebro=cerebro_saludo_post, nucleo=nucleo_mem,
                                            conv_db=conv_mem)
                chk("el que escribe justo despues del cierre NO recibe la bienvenida "
                    "(caso real del 15-ago, a 10 s de confirmar el pedido)",
                    "Bienvenid" not in vis_post and "Gracias a ti" in vis_post)
                # LIMITE conocido y deliberado (ver sanear_saludo_repetido en prototipo.py):
                # si el modelo manda SOLO el saludo y nada mas, el candado lo deja pasar --
                # un WhatsApp vacio seria peor que una bienvenida repetida. O sea que el
                # filtro de salida NUNCA iba a poder arreglar esto solo: la red de verdad es
                # el contexto, y por eso el arreglo del 18-ago va en historial().
                chk("...y ese candado, solo, no alcanzaba: no puede tapar un mensaje que "
                    "es UNICAMENTE el saludo",
                    prototipo.sanear_saludo_repetido(
                        "Hola! Bienvenid@ a Patata Huechuraba", False)
                    == "Hola! Bienvenid@ a Patata Huechuraba")

                # Vencida la ventana se turnan las dos capas: se va el hilo, entra la nota.
                # Por eso MINUTOS_VENTANA_POSCIERRE es MENOR que la memoria del pedido: si
                # duraran lo mismo, esta nota no se leeria nunca.
                limpiar_historial("56911119999", db_path=conv_mem)
                _envejecer_cierre("56911119999", conv_mem)
                cerebro_tarde = _CerebroFalso(["Dime que necesitas."])
                procesar_mensaje("56911119999", "hola de nuevo", CONFIG_DEFAULT,
                                 cerebro=cerebro_tarde, nucleo=nucleo_mem, conv_db=conv_mem)
                chk("vencida la ventana, el siguiente mensaje del MISMO telefono trae la "
                    "nota interna del pedido reciente",
                    cerebro_tarde.ultimos_mensajes is not None and
                    any("pedido hace" in m["texto"] for m in cerebro_tarde.ultimos_mensajes
                        if m["rol"] == "sistema"))

                cerebro_otro = _CerebroFalso(["Hola! bienvenido a Patata, que te gustaria pedir?"])
                procesar_mensaje("56900001234", "hola", CONFIG_DEFAULT,
                                 cerebro=cerebro_otro, nucleo=nucleo_mem, conv_db=conv_mem)
                chk("un telefono SIN pedido reciente no recibe la nota",
                    cerebro_otro.ultimos_mensajes is not None and
                    not any("pedido hace" in m["texto"] for m in cerebro_otro.ultimos_mensajes
                            if m["rol"] == "sistema"))

                import sqlite3 as _sq3
                _viejo_mem = (datetime.now() -
                             timedelta(minutes=MINUTOS_MEMORIA_PEDIDO_RECIENTE + 5)
                             ).isoformat(timespec="seconds")
                _c_mem = _sq3.connect(RESPALDO_DB)
                _c_mem.execute("UPDATE ultimo_pedido_cliente SET creado_en=? WHERE telefono=?",
                              (_viejo_mem, "911119999"))
                _c_mem.commit(); _c_mem.close()
                chk(f"pasados los {MINUTOS_MEMORIA_PEDIDO_RECIENTE} min, la nota expira sola",
                    _nota_pedido_reciente("56911119999") is None)
        finally:
            RESPALDO_DB = _respaldo_orig_mem

    print("== S2.5 punto 4 / nombre registrado: cliente conocido -- el bot no repregunta "
          "nombre/direccion de cero ==")
    # 04-ago-2026: ANTES este bloque mockeaba urlopen (consulta en vivo al POS). Ahora
    # _nota_cliente_conocido lee CLIENTES_CACHE (la cache local que refresca el hilo de
    # fondo, ver _refrescar_clientes_pos) -- se prueba escribiendo esa cache directo, sin
    # red de por medio.
    prototipo.CLIENTES_CACHE.unlink(missing_ok=True)
    try:
        chk("sin cache de clientes todavia: sin nota (nunca inventa un 'registrado')",
            _nota_cliente_conocido("56955551234") is None)

        prototipo.CLIENTES_CACHE.write_text(json.dumps({
            "955551234": {"nombre": "Marisol", "direccion": "Los Aromos 456"},
        }, ensure_ascii=False), encoding="utf-8")
        chk("telefono que NO esta en la cache: sin nota",
            _nota_cliente_conocido("56900000000") is None)

        nota_cli = _nota_cliente_conocido("56955551234")
        chk("cliente conocido: la nota trae su nombre", bool(nota_cli) and "Marisol" in nota_cli)
        chk("cliente conocido: la nota trae su direccion",
            bool(nota_cli) and "Los Aromos 456" in nota_cli)
        # 05-ago-2026: la ficha ya no lleva linea de nombre fija (el nombre dejo de ser un
        # dato obligatorio, ver system_prompt BLOQUE 2). Cuando SI se conoce, la nota pide
        # agregarlo arriba de la ficha como una linea normal.
        chk("cliente conocido: la nota le pide poner su nombre en la ficha",
            bool(nota_cli) and "•Nombre: Marisol" in nota_cli)
        chk("...y le dice que va DEBAJO de la direccion (orden pedido por Gonza)",
            bool(nota_cli) and "DEBAJO de la linea de direccion" in nota_cli)

        with carpeta_temporal() as tmp_cli:
            cerebro_conocido = _CerebroFalso(["Hola! ¿el de siempre?"])
            procesar_mensaje("56955551234", "hola", CONFIG_DEFAULT, cerebro=cerebro_conocido,
                             conv_db=Path(tmp_cli) / "conv_conocido.db")
            chk("procesar_mensaje inyecta la nota de cliente conocido en la primera vuelta",
                cerebro_conocido.ultimos_mensajes is not None and
                any("cliente conocido" in m["texto"] for m in cerebro_conocido.ultimos_mensajes
                    if m["rol"] == "sistema"))

        # 05-ago-2026 (pendiente #8): el dato ya no depende de la memoria del modelo.
        print("== 05-ago-2026: los datos del cliente se REPONEN en cada turno (no los "
              "arrastra el modelo) ==")
        nota_fijos = _nota_datos_fijos_cliente("56955551234")
        chk("la nota de datos fijos trae nombre y direccion",
            bool(nota_fijos) and "Marisol" in nota_fijos and "Los Aromos 456" in nota_fijos)
        chk("...y deja claro que el cliente manda si dice otra cosa",
            bool(nota_fijos) and "mandan los que el dijo" in nota_fijos)
        chk("un telefono desconocido no recibe datos fijos",
            _nota_datos_fijos_cliente("56900000000") is None)
        with carpeta_temporal() as tmp_cli2:
            conv_cli2 = Path(tmp_cli2) / "conv_fijos.db"
            procesar_mensaje("56955551234", "hola", CONFIG_DEFAULT,
                             cerebro=_CerebroFalso(["Hola! ¿que vas a querer?"]),
                             conv_db=conv_cli2)
            cerebro_t2 = _CerebroFalso(["Dale, 1 Churrasco Italiano anotado."])
            procesar_mensaje("56955551234", "1 churrasco italiano", CONFIG_DEFAULT,
                             cerebro=cerebro_t2, conv_db=conv_cli2)
            _sis_t2 = [m["texto"] for m in (cerebro_t2.ultimos_mensajes or [])
                       if m["rol"] == "sistema"]
            chk("en el SEGUNDO turno el sistema repone el nombre (antes se perdia y el bot "
                "lo repreguntaba)",
                any("DATOS FIJOS" in t and "Marisol" in t for t in _sis_t2))
            chk("...y va al FINAL del contexto, pegado al mensaje del cliente",
                (cerebro_t2.ultimos_mensajes or [])[-1]["rol"] == "usuario" and
                "DATOS FIJOS" in (cerebro_t2.ultimos_mensajes or [])[-2]["texto"])
            # Se busca el encabezado EXACTO de la nota larga: la frase "cliente conocido"
            # suelta tambien vive en el BLOQUE 2 del system prompt, y daba falso positivo.
            chk("la nota LARGA de bienvenida no se repite en el segundo turno",
                not any("usalo para no repreguntar" in t for t in _sis_t2))
    finally:
        prototipo.CLIENTES_CACHE.unlink(missing_ok=True)

    print("== 06-ago-2026: el NOMBRE de la comanda (Gonza: 'repara lo del nombre') ==")
    # 1) Limpieza del nombre de perfil: en su WhatsApp la gente pone de todo.
    chk("un nombre normal pasa tal cual", _limpiar_nombre_perfil("  Marisol  ") == "Marisol")
    chk("los emojis se van (en el ticket termico salen como basura)",
        _limpiar_nombre_perfil("🔥Juan Pérez🔥") == "Juan Pérez")
    chk("un perfil que es solo emojis no deja nombre", _limpiar_nombre_perfil("🔥🔥🔥") is None)
    chk("un telefono escrito como nombre no es un nombre (ya sabemos el numero)",
        _limpiar_nombre_perfil("+56 9 XXXX XXXX") is None)
    chk("el propio placeholder nunca se acepta como nombre",
        _limpiar_nombre_perfil(NOMBRE_COMANDA_FALLBACK) is None)
    chk("un nombre kilometrico se corta antes de desarmar la comanda",
        len(_limpiar_nombre_perfil("Maria " * 20)) <= LARGO_MAX_NOMBRE_PERFIL)
    # 2) El dato existia y se tiraba: Meta lo manda en contacts[], al lado de messages[].
    _valor_meta = {
        "contacts": [{"profile": {"name": "Marisol Fuentes"}, "wa_id": "56977778888"}],
        "messages": [{"from": "56977778888", "id": "wamid.PERFIL", "type": "text",
                      "text": {"body": "hola"}}]}
    chk("_nombres_de_perfil lo saca del payload real de Meta",
        _nombres_de_perfil(_valor_meta).get(_norm_telefono("56977778888")) == "Marisol Fuentes")
    chk("...y no inventa nada si Meta no manda contacts",
        _nombres_de_perfil({"messages": []}) == {})
    # 3) La cadena de confianza de _nombre_para_comanda, sobre bases propias.
    with carpeta_temporal() as tmp_nom:
        _respaldo_orig_nom = RESPALDO_DB
        RESPALDO_DB = Path(tmp_nom) / "respaldo_nombre.db"
        prototipo.CLIENTES_CACHE.unlink(missing_ok=True)
        try:
            _ped_anon = {
                "cliente": "", "modalidad": "retiro", "direccion": None,
                "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                           "variante": None, "cantidad": 1, "precio_unitario": 4900,
                           "subtotal": 4900, "notas": ""}],
                "delivery_cobro": 0, "total": 4900, "forma_pago": "efectivo",
                "hora_programada": None, "derivar_humano": False, "comentarios": "",
            }
            chk("sin ningun dato de nombre, la comanda cae al fallback de siempre",
                _payload_pedido(_ped_anon, "56977778888")["cliente"] == NOMBRE_COMANDA_FALLBACK)
            _guardar_nombre_perfil("56977778888", "Marisol Fuentes")
            chk("EL FIX: con el nombre de perfil guardado, la comanda ya no sale anonima",
                _payload_pedido(_ped_anon, "56977778888")["cliente"] == "Marisol Fuentes")
            chk("lo que el cliente DIJO en la conversacion le gana al perfil",
                _payload_pedido({**_ped_anon, "cliente": "Pedro"},
                                "56977778888")["cliente"] == "Pedro")
            prototipo.CLIENTES_CACHE.write_text(json.dumps({
                _norm_telefono("56977778888"): {"nombre": "Marisol Soto",
                                               "direccion": "Los Aromos 456"},
            }, ensure_ascii=False), encoding="utf-8")
            chk("el nombre que el LOCAL tiene guardado le gana al de perfil",
                _payload_pedido(_ped_anon, "56977778888")["cliente"] == "Marisol Soto")
            # El bug silencioso que esto tapa: nucleo.crear guarda este campo en la base
            # de clientes del POS, asi que mandar el placeholder PISABA el nombre real de
            # un cliente conocido cada vez que el modelo se olvidaba de incluirlo.
            chk("un cliente conocido NUNCA se manda al POS como 'Cliente WhatsApp'",
                _payload_pedido({**_ped_anon, "cliente": NOMBRE_COMANDA_FALLBACK},
                                "56977778888")["cliente"] == "Marisol Soto")
            # 4) Lo que el cliente LEE no cambia: el nombre de perfil no aparece en la
            # ficha (el no lo dio en esta conversacion; verlo ahi es justo lo que hace
            # sentir vigilado a alguien que solo queria un churrasco).
            prototipo.CLIENTES_CACHE.unlink(missing_ok=True)
            chk("la ficha del cliente NO muestra el nombre sacado de su perfil",
                "•Nombre" not in (_ficha_confirmacion(_ped_anon, "56977778888",
                                                     CONFIG_DEFAULT) or ""))
            # 5) Camino completo: payload de Meta -> comanda en la cola del POS.
            with carpeta_temporal() as tmp_e2e:
                nucleo_e2e = NucleoPedidos(db_path=Path(tmp_e2e) / "pos_nombre.db")
                _resp_e2e = ("<PEDIDO_JSON>\n" +
                             json.dumps({**_ped_anon, "_tool": "emitir_pedido"}) +
                             "\n</PEDIDO_JSON>")
                procesar_payload_meta(
                    {"entry": [{"changes": [{"value": {
                        "contacts": [{"profile": {"name": "Marisol Fuentes 🌭"},
                                      "wa_id": "56977778888"}],
                        "messages": [{"from": "56977778888", "id": "wamid.NOMBRE1",
                                      "type": "text",
                                      "text": {"body": "1 churrasco italiano, retiro, efectivo"}}],
                    }}]}]},
                    CONFIG_DEFAULT, cerebro=_CerebroFalso([_resp_e2e]), nucleo=nucleo_e2e,
                    conv_db=Path(tmp_e2e) / "conv_nombre.db")
                _cola_e2e = nucleo_e2e.cola()
                chk("el pedido llego a la cola del POS", len(_cola_e2e) == 1)
                chk("...y la comanda que ve Christian lleva el nombre del cliente, "
                    "no 'Cliente WhatsApp'",
                    bool(_cola_e2e) and _cola_e2e[0]["cliente"] == "Marisol Fuentes")
        finally:
            RESPALDO_DB = _respaldo_orig_nom
            prototipo.CLIENTES_CACHE.unlink(missing_ok=True)
    # 6) La cache de clientes no ingiere el placeholder que el propio bot dejo en el POS.
    chk("_es_nombre_placeholder reconoce el fallback venga como venga",
        _es_nombre_placeholder("cliente whatsapp") and _es_nombre_placeholder("  ") and
        _es_nombre_placeholder(None) and not _es_nombre_placeholder("Marisol"))

    print("== S2.5 punto 9: 'en camino <tel>' / 'afuera <tel>' del jefe avisa al cliente ==")
    _llamadas_en_camino = []
    _enviar_wa_orig = enviar_whatsapp

    def _espia_enviar_wa(telefono, texto, cfg):
        _llamadas_en_camino.append((telefono, texto))
        return {"simulado": True}
    enviar_whatsapp = _espia_enviar_wa
    try:
        r1 = resolver_en_camino_jefe("en camino", "912345678", CONFIG_DEFAULT)
        chk("'en camino': confirma al jefe con el numero completo", "56912345678" in r1)
        chk("'en camino': le llego al CLIENTE el aviso correcto",
            _llamadas_en_camino and _llamadas_en_camino[-1][0] == "56912345678" and
            "en camino" in _llamadas_en_camino[-1][1].lower())

        r2 = resolver_en_camino_jefe("afuera", "987654321", CONFIG_DEFAULT)
        chk("'afuera': le llego al CLIENTE el aviso de repartidor afuera",
            _llamadas_en_camino[-1][0] == "56987654321" and
            "afuera" in _llamadas_en_camino[-1][1].lower())

        r3 = resolver_en_camino_jefe("en camino", "123", CONFIG_DEFAULT)
        chk("numero invalido: avisa sin reventar y no le manda nada a nadie",
            "No reconozco" in r3 and len(_llamadas_en_camino) == 2)
    finally:
        enviar_whatsapp = _enviar_wa_orig

    with carpeta_temporal() as tmp_camino:
        conv_camino = Path(tmp_camino) / "conv_camino.db"
        vis_cliente = procesar_mensaje("56977778888", "en camino 912345678", CONFIG_DEFAULT,
                                       cerebro=_CerebroFalso(["hola"]), conv_db=conv_camino)
        chk("un CLIENTE (no jefe) que escribe 'en camino ...' NO dispara el comando -- va al LLM normal",
            vis_cliente == "hola")

        cerebro_jefe_camino = _CerebroFalso(["no deberia llamarse"])
        vis_jefe = procesar_mensaje(JEFE_NUM, "en camino 912345678", CONFIG_DEFAULT,
                                    cerebro=cerebro_jefe_camino, conv_db=conv_camino)
        chk("el JEFE si dispara el comando (interceptado por codigo, sin LLM)",
            cerebro_jefe_camino.llamadas == 0 and "912345678" in vis_jefe)

    print("== 28-jul-2026: horario cerrado -- candado de CODIGO, sin pasar por el cerebro ==")
    estado_horario = lambda menu: (False, "[test] CERRADO (forzado)")  # noqa: E731,F811
    try:
        with carpeta_temporal() as tmp_cerrado:
            conv_cerrado = Path(tmp_cerrado) / "conv_cerrado.db"
            cerebro_cliente_cerrado = _CerebroFalso(["esto no deberia llegar a mandarse"])
            vis_cliente_cerrado = procesar_mensaje(
                "56900009999", "Hola", CONFIG_DEFAULT,
                cerebro=cerebro_cliente_cerrado, conv_db=conv_cerrado)
            chk("cliente real: NO se llama al cerebro estando cerrado",
                cerebro_cliente_cerrado.llamadas == 0)
            chk("cliente real: recibe el mensaje FIJO de cerrado (no el de la IA)",
                vis_cliente_cerrado == mensaje_cerrado(
                    json.loads(MENU_JSON.read_text(encoding="utf-8"))))

            # Caso 113 (curador): la segunda respuesta es una cortesia corta y la tercera
            # el fijo corto; una pregunta por el horario vuelve a recibir el completo.
            _completo113 = mensaje_cerrado(json.loads(MENU_JSON.read_text(encoding="utf-8")))
            _v113b = procesar_mensaje("56900009999", "Ok", CONFIG_DEFAULT,
                                      cerebro=cerebro_cliente_cerrado, conv_db=conv_cerrado)
            chk("caso 113: tras el horario completo, 'Ok' recibe la cortesia corta (no el horario otra vez)",
                _v113b == prototipo.MENSAJE_CERRADO_SEGUNDO)
            _v113c = procesar_mensaje("56900009999", "ya", CONFIG_DEFAULT,
                                      cerebro=cerebro_cliente_cerrado, conv_db=conv_cerrado)
            chk("caso 113: el tercer mensaje recibe el fijo corto",
                _v113c == prototipo.MENSAJE_CERRADO_TERCERO)
            _v113d = procesar_mensaje("56900009999", "gracias", CONFIG_DEFAULT,
                                      cerebro=cerebro_cliente_cerrado, conv_db=conv_cerrado)
            chk("...y del cuarto en adelante sigue el fijo corto",
                _v113d == prototipo.MENSAJE_CERRADO_TERCERO)
            _v113e = procesar_mensaje("56900009999", "a que hora abren?", CONFIG_DEFAULT,
                                      cerebro=cerebro_cliente_cerrado, conv_db=conv_cerrado)
            chk("caso 113: si pregunta por el horario, recibe el completo de nuevo",
                _v113e == _completo113)
            _v113f = procesar_mensaje("56900008888", "Hola", CONFIG_DEFAULT,
                                      cerebro=cerebro_cliente_cerrado, conv_db=conv_cerrado)
            chk("control: otro cliente que escribe por primera vez recibe el completo",
                _v113f == _completo113)
            chk("...y en ningun turno se llamo al cerebro", cerebro_cliente_cerrado.llamadas == 0)
            chk("caso 113: los mensajes cortos cuentan como 'cerrado' (se borran al reabrir)",
                prototipo.es_mensaje_de_cerrado(prototipo.MENSAJE_CERRADO_SEGUNDO)
                and prototipo.es_mensaje_de_cerrado(prototipo.MENSAJE_CERRADO_TERCERO))
            chk("...asi al reabrir el modelo ya no los ve (caso 153)",
                prototipo.sin_restricciones_levantadas(
                    [{"rol": "asistente", "texto": prototipo.MENSAJE_CERRADO_TERCERO}], {}) == [])

            cerebro_jefe_cerrado = _CerebroFalso(["Listo jefe, dale."])
            vis_jefe_cerrado = procesar_mensaje(
                JEFE_NUM, "se agoto el chacarero", CONFIG_DEFAULT,
                cerebro=cerebro_jefe_cerrado, conv_db=conv_cerrado)
            chk("el JEFE sigue funcionando igual aunque el local este cerrado",
                cerebro_jefe_cerrado.llamadas == 1 and "Listo jefe" in vis_jefe_cerrado)
    finally:
        # Restaura al mock "forzado abierto" (no al real, que depende del reloj real) --
        # el resto del --test que corre DESPUES de este bloque no esta probando horario y
        # necesita seguir siendo determinista sin importar a que hora del dia se ejecute.
        estado_horario = _estado_horario_forzado_abierto

    print("== 03-ago-2026 (P2 de Christian): corte de pedidos a las 23:50, "
          "antes del cierre real (23:59) ==")
    # estado_horario sigue forzado 'abierto' aca (recien restaurado arriba): estos
    # checks prueban SOLO el corte, pasando la hora explicita (no dependen del reloj
    # real ni del mock de _ahora_chile).
    _menu_corte = json.loads(MENU_JSON.read_text(encoding="utf-8"))
    chk("antes del corte (23:49), toma pedidos normal",
        _toma_pedidos(_menu_corte, datetime(2026, 7, 28, 23, 49, tzinfo=TZ_CHILE)) == (True, None))
    chk("justo en el corte (23:50), deja de tomar",
        _toma_pedidos(_menu_corte, datetime(2026, 7, 28, 23, 50, tzinfo=TZ_CHILE))
        == (False, "corte_pedidos"))
    chk("dentro del corte, antes del cierre real (23:55)",
        _toma_pedidos(_menu_corte, datetime(2026, 7, 28, 23, 55, tzinfo=TZ_CHILE))
        == (False, "corte_pedidos"))
    chk("bien dentro de horario (14:00), toma pedidos normal",
        _toma_pedidos(_menu_corte, datetime(2026, 7, 28, 14, 0, tzinfo=TZ_CHILE)) == (True, None))
    chk("mensaje del corte NO repite el horario completo (no es el mismo texto que cerrado)",
        mensaje_corte_pedidos() != mensaje_cerrado(_menu_corte)
        and "mañana" in mensaje_corte_pedidos().lower())

    _ahora_chile_orig_test = _ahora_chile
    estado_horario = lambda menu: (True, "[test] ABIERTO (forzado)")  # noqa: E731,F811
    _ahora_chile = lambda: datetime(2026, 7, 28, 23, 52, tzinfo=TZ_CHILE)  # noqa: E731,F811
    try:
        with carpeta_temporal() as tmp_corte:
            conv_corte = Path(tmp_corte) / "conv_corte.db"
            cerebro_cliente_corte = _CerebroFalso(["esto no deberia llegar a mandarse"])
            vis_cliente_corte = procesar_mensaje(
                "56900001111", "Hola", CONFIG_DEFAULT,
                cerebro=cerebro_cliente_corte, conv_db=conv_corte)
            chk("cliente real dentro del corte: NO se llama al cerebro",
                cerebro_cliente_corte.llamadas == 0)
            chk("cliente real dentro del corte: recibe el mensaje FIJO del corte, "
                "no el de 'estamos cerrados'",
                vis_cliente_corte == mensaje_corte_pedidos())

            cerebro_jefe_corte = _CerebroFalso(["Listo jefe, dale."])
            vis_jefe_corte = procesar_mensaje(
                JEFE_NUM, "se agoto el chacarero", CONFIG_DEFAULT,
                cerebro=cerebro_jefe_corte, conv_db=conv_corte)
            chk("el JEFE sigue funcionando igual dentro del corte de pedidos",
                cerebro_jefe_corte.llamadas == 1 and "Listo jefe" in vis_jefe_corte)
    finally:
        estado_horario = _estado_horario_forzado_abierto
        _ahora_chile = _ahora_chile_orig_test

    print("== 28-jul-2026: candado de CODIGO para 'solo retiro' / 'se agoto X' "
          "(ya no dependen de que el LLM se acuerde) ==")
    with carpeta_temporal() as tmp_cand:
        orig_instr_cand = _proto.INSTRUCCIONES
        _proto.INSTRUCCIONES = Path(tmp_cand) / "instrucciones_candado.txt"
        # RESPALDO_DB propio (05-ago-2026, mismo motivo que tmp17): el telefono de este
        # bloque ya despacho un pedido en el test de dedupe de Meta, y el candado de
        # pedido duplicado -- que corre ANTES que este -- lo bloqueaba por eso, no por
        # "solo retiro". Aca se prueba el candado de instrucciones, no la herencia.
        _respaldo_orig_cand = RESPALDO_DB
        RESPALDO_DB = Path(tmp_cand) / "respaldo_candado_aparte.db"
        try:
            hoy_c = datetime.now(_proto.TZ_CHILE).strftime("%Y-%m-%d")

            print("  -- parser: tolerante a variantes de redaccion --")
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\nsolo retiro hoy\n", encoding="utf-8")
            chk("detecta 'solo retiro hoy'",
                _proto.interpretar_instrucciones_estructuradas()["solo_retiro"] is True)
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\nno hay delivery hoy\n", encoding="utf-8")
            chk("detecta 'no hay delivery hoy' (redaccion distinta)",
                _proto.interpretar_instrucciones_estructuradas()["solo_retiro"] is True)
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\nse agoto el chacarero\n", encoding="utf-8")
            agotados_1 = _proto.interpretar_instrucciones_estructuradas()["agotados"]
            chk("detecta 'se agoto X'", agotados_1 == ["chacarero"])
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\nno quedan papas\n", encoding="utf-8")
            agotados_2 = _proto.interpretar_instrucciones_estructuradas()["agotados"]
            chk("detecta 'no quedan X'", agotados_2 == ["papas"])
            _proto.INSTRUCCIONES.write_text(
                f"FECHA: {hoy_c}\nse agoto el chacarero por hoy\n", encoding="utf-8")
            agotados_3 = _proto.interpretar_instrucciones_estructuradas()["agotados"]
            chk("descarta relleno final ('por hoy') sin perder el producto",
                agotados_3 == ["chacarero"])

            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\ncerrado hoy\n", encoding="utf-8")
            chk("detecta 'cerrado hoy' (caso #51 del curador, 18-sep-2026)",
                _proto.interpretar_instrucciones_estructuradas()["cerrado_hoy"] is True)
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\nno estamos atendiendo\n", encoding="utf-8")
            chk("detecta 'no estamos atendiendo' (redaccion distinta, mismo chat real)",
                _proto.interpretar_instrucciones_estructuradas()["cerrado_hoy"] is True)
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\nsolo retiro hoy\n", encoding="utf-8")
            chk("'solo retiro hoy' NO activa 'cerrado hoy' (son candados distintos)",
                _proto.interpretar_instrucciones_estructuradas()["cerrado_hoy"] is False)

            print("  -- integracion: delivery se bloquea por CODIGO con 'solo retiro' activo --")
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\nsolo retiro hoy\n", encoding="utf-8")
            db_cand1 = Path(tmp_cand) / "pedidos_candado1.db"
            conv_cand1 = Path(tmp_cand) / "conv_candado1.db"
            nucleo_cand1 = NucleoPedidos(db_path=db_cand1)
            respuesta_delivery_bloqueada = respuesta_cerrada.replace(
                '"modalidad":"retiro","direccion":null',
                '"modalidad":"delivery","direccion":"Pasaje Nuevo 123"')
            vis_bloqueo = procesar_mensaje(
                "56955554444", "2 italianos delivery tarjeta", CONFIG_DEFAULT,
                cerebro=_CerebroFalso([respuesta_delivery_bloqueada]),
                nucleo=nucleo_cand1, conv_db=conv_cand1)
            chk("delivery bloqueado: nunca llega a cocina", len(nucleo_cand1.cola()) == 0)
            chk("el cliente recibe el mensaje fijo de solo retiro",
                vis_bloqueo == _proto.MENSAJE_SOLO_RETIRO)
            chk("la conversacion sigue abierta (no se limpio el historial)",
                len(historial("56955554444", db_path=conv_cand1)) > 0)

            print("  -- integracion: producto agotado se bloquea por CODIGO --")
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\nse agoto el chacarero\n", encoding="utf-8")
            db_cand2 = Path(tmp_cand) / "pedidos_candado2.db"
            conv_cand2 = Path(tmp_cand) / "conv_candado2.db"
            nucleo_cand2 = NucleoPedidos(db_path=db_cand2)
            respuesta_agotado = respuesta_cerrada.replace(
                '"producto":"Churrasco Italiano"', '"producto":"Churrasco Chacarero"')
            vis_agotado = procesar_mensaje(
                "56933332222", "2 chacareros retiro tarjeta", CONFIG_DEFAULT,
                cerebro=_CerebroFalso([respuesta_agotado]),
                nucleo=nucleo_cand2, conv_db=conv_cand2)
            chk("producto agotado bloqueado: nunca llega a cocina", len(nucleo_cand2.cola()) == 0)
            chk("el cliente recibe el aviso de agotado con el nombre del producto",
                "Churrasco Chacarero" in vis_agotado)

            print("  -- control: sin candado activo, un pedido normal se despacha igual --")
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\n", encoding="utf-8")
            db_cand3 = Path(tmp_cand) / "pedidos_candado3.db"
            conv_cand3 = Path(tmp_cand) / "conv_candado3.db"
            nucleo_cand3 = NucleoPedidos(db_path=db_cand3)
            procesar_mensaje(
                "56911119999", "2 italianos retiro tarjeta", CONFIG_DEFAULT,
                cerebro=_CerebroFalso([respuesta_cerrada]),
                nucleo=nucleo_cand3, conv_db=conv_cand3)
            chk("sin candado activo, un pedido normal SI llega a cocina (no se rompio nada)",
                len(nucleo_cand3.cola()) == 1)

            print("  -- integracion: 'cerrado hoy' bloquea ANTES de llamar al cerebro "
                  "(caso #51 del curador, 18-sep-2026) --")
            _proto.INSTRUCCIONES.write_text(f"FECHA: {hoy_c}\ncerrado hoy\n", encoding="utf-8")
            db_cand4 = Path(tmp_cand) / "pedidos_candado4.db"
            conv_cand4 = Path(tmp_cand) / "conv_candado4.db"
            nucleo_cand4 = NucleoPedidos(db_path=db_cand4)
            cerebro_cerrado_jefe = _CerebroFalso(["esto no deberia llegar a mandarse"])
            vis_cerrado_jefe = procesar_mensaje(
                "56944443333", "hola quiero 2 italianos retiro efectivo", CONFIG_DEFAULT,
                cerebro=cerebro_cerrado_jefe, nucleo=nucleo_cand4, conv_db=conv_cand4)
            chk("'cerrado hoy': el cerebro NUNCA se llama (candado de codigo, no de prompt)",
                cerebro_cerrado_jefe.llamadas == 0)
            chk("'cerrado hoy': el cliente recibe el mensaje fijo, no el de horario normal",
                vis_cerrado_jefe == _proto.MENSAJE_CERRADO_POR_JEFE)
            chk("'cerrado hoy': nunca llega a cocina", len(nucleo_cand4.cola()) == 0)

            print("  -- integracion: el JEFE sigue pudiendo operar con 'cerrado hoy' activo --")
            cerebro_jefe_cerrado = _CerebroFalso(["Listo jefe, anotado."])
            vis_jefe_cerrado = procesar_mensaje(
                JEFE_NUM, "se agoto el chacarero", CONFIG_DEFAULT,
                cerebro=cerebro_jefe_cerrado, conv_db=conv_cand4)
            chk("el candado de 'cerrado hoy' no afecta al jefe",
                cerebro_jefe_cerrado.llamadas == 1 and "Listo jefe" in vis_jefe_cerrado)
        finally:
            _proto.INSTRUCCIONES = orig_instr_cand
            RESPALDO_DB = _respaldo_orig_cand

    print("== 28-jul-2026 (P8 de Christian): candado de CODIGO para modalidad vs. direccion ==")
    chk("retiro CON direccion: se bloquea y se pregunta (el bug real de la clienta Yoce)",
        "Carlos Aguirre Luco 1104" in (_proto.candado_modalidad(
            {"modalidad": "retiro", "direccion": "Carlos Aguirre Luco 1104"}) or ""))
    chk("delivery SIN direccion: se bloquea y se pide la direccion",
        _proto.candado_modalidad({"modalidad": "delivery", "direccion": None})
        == _proto.MENSAJE_DELIVERY_SIN_DIRECCION)
    chk("delivery CON direccion: pasa (pedido coherente)",
        _proto.candado_modalidad(
            {"modalidad": "delivery", "direccion": "Pasaje Nuevo 123"}) is None)
    chk("retiro SIN direccion: pasa (el caso mas comun del local)",
        _proto.candado_modalidad({"modalidad": "retiro", "direccion": None}) is None)
    chk("relleno en el campo direccion NO cuenta como direccion ('-', 'retiro en local')",
        _proto.candado_modalidad({"modalidad": "retiro", "direccion": "-"}) is None and
        _proto.candado_modalidad({"modalidad": "retiro", "direccion": "Retiro en local"}) is None)

    with carpeta_temporal() as tmp_mod:
        db_mod = Path(tmp_mod) / "pedidos_modalidad.db"
        conv_mod = Path(tmp_mod) / "conv_modalidad.db"
        nucleo_mod = NucleoPedidos(db_path=db_mod)
        respuesta_retiro_con_direccion = respuesta_cerrada.replace(
            '"direccion":null', '"direccion":"Carlos Aguirre Luco 1104"')
        vis_mod = procesar_mensaje(
            "56922221111", "2 italianos, Carlos Aguirre Luco 1104, tarjeta", CONFIG_DEFAULT,
            cerebro=_CerebroFalso([respuesta_retiro_con_direccion]),
            nucleo=nucleo_mod, conv_db=conv_mod)
        chk("integracion: un 'retiro' con direccion NUNCA llega a cocina asi",
            len(nucleo_mod.cola()) == 0)
        chk("integracion: al cliente le llega la pregunta, no la confirmacion de la IA",
            "retirarlo al local" in vis_mod and "Total $8.900" not in vis_mod)
        chk("integracion: la conversacion sigue abierta para corregir",
            len(historial("56922221111", db_path=conv_mod)) > 0)
        _turnos_mod = historial("56922221111", db_path=conv_mod)
        chk("el historial guarda la PREGUNTA del candado, no la confirmacion del modelo "
            "(si no, en el turno siguiente el modelo cree que ya cerro)",
            _turnos_mod[-1]["rol"] == "asistente" and
            _proto.MARCA_MODALIDAD in _turnos_mod[-1]["texto"] and
            "Total $8.900" not in _turnos_mod[-1]["texto"])

        # ANTI-BUCLE: el cliente contesta, el modelo insiste con el mismo pedido torcido.
        # Preguntar de nuevo lo dejaria sin poder cerrar NUNCA -- peor que el bug original.
        vis_mod2 = procesar_mensaje(
            "56922221111", "retiro", CONFIG_DEFAULT,
            cerebro=_CerebroFalso([respuesta_retiro_con_direccion]),
            nucleo=nucleo_mod, conv_db=conv_mod)
        chk("preguntado UNA vez y sin aclarar: el pedido si entra a cocina (no hay bucle)",
            len(nucleo_mod.cola()) == 1)
        chk("...pero entra MARCADO para que una persona lo confirme",
            _proto.AVISO_MODALIDAD_SIN_ACLARAR in (nucleo_mod.cola()[0].get("notas") or ""))
        chk("...y al cliente le llega la respuesta normal del bot, no otra pregunta fija",
            _proto.MARCA_MODALIDAD not in vis_mod2)

    print("== 28-jul-2026 (P9 de Christian): tiempos de espera calculados por CODIGO ==")
    _viernes_noche = datetime(2026, 7, 31, 20, 30, tzinfo=_proto.TZ_CHILE)   # viernes 20:30
    _viernes_tarde = datetime(2026, 7, 31, 13, 0, tzinfo=_proto.TZ_CHILE)    # viernes 13:00
    _jueves_noche = datetime(2026, 7, 30, 20, 30, tzinfo=_proto.TZ_CHILE)    # jueves 20:30
    chk("viernes 20:30 -> retiro 15 min y delivery 30-40 (excepcion dictada por Christian)",
        "15 minutos" in _proto.tiempos_estimados(_viernes_noche) and
        "30-40 minutos" in _proto.tiempos_estimados(_viernes_noche))
    chk("viernes 13:00 (antes de las 19) -> tiempos normales, la excepcion NO se adelanta",
        "10 minutos" in _proto.tiempos_estimados(_viernes_tarde) and
        "30 minutos" in _proto.tiempos_estimados(_viernes_tarde))
    chk("jueves 20:30 -> tiempos normales (la excepcion es solo del viernes)",
        "10 minutos" in _proto.tiempos_estimados(_jueves_noche) and
        "30 minutos" in _proto.tiempos_estimados(_jueves_noche))
    chk("el prompt del CLIENTE lleva los tiempos ya calculados (no los deduce el modelo)",
        "TIEMPOS DE HOY" in construir_sistema())
    chk("el prompt del JEFE no los lleva (no atiende clientes)",
        "TIEMPOS DE HOY" not in construir_sistema(jefe=True))

    print("== 28-jul-2026 (P1 de Christian): sabores reales de la lata en el menu de respaldo ==")
    _menu_respaldo = json.loads(MENU_JSON.read_text(encoding="utf-8"))
    _lata = next((p for c in _menu_respaldo["categorias"] if c["nombre"] == "Bebestibles"
                  for p in c["productos"] if p["nombre"] == "Lata CCU"), None)
    chk("la Lata CCU trae los 6 sabores que confirmo Christian",
        _lata is not None and
        [_proto._norm(s) for s in _lata.get("variedades", [])] ==
        ["pepsi", "pepsi zero", "bilz", "pap", "kem", "limon soda"])
    chk("ya no se ofrecen sabores que el local no vende (Coca Zero / Jugo Durazno)",
        _lata is not None and
        not any(_proto._norm(s) in ("coca zero", "jugo durazno")
                for s in _lata.get("variedades", [])))

    print("== 03-ago-2026 (P11/P14): las esperas bajan a 5 + 5 ==")
    chk("espera del cobro de envio = 5 min", ESPERA_COBRO_MINUTOS == 5)
    chk("espera de la transferencia = 5 min", ESPERA_COMPROBANTE_MINUTOS == 5)
    chk("y el respaldo sigue con sus 5 min propios", ESPERA_BACKUP_MINUTOS == 5)

    print("== 03-ago-2026 (P3): el mensaje de CERRADO respeta la apertura que dicto el jefe ==")
    _menu_cerr = json.loads(MENU_JSON.read_text(encoding="utf-8"))
    chk("sin instruccion del dia, recita el horario de siempre (como hasta hoy)",
        "lunes a viernes" in mensaje_cerrado(_menu_cerr, ""))
    _cerr_hoy = mensaje_cerrado(_menu_cerr, "hoy abrimos a las 13:00")
    chk("con 'hoy abrimos a las 13:00', dice ESA hora",
        "13:00" in _cerr_hoy and "lunes a viernes" not in _cerr_hoy)
    chk("...y no se contradice recitando el horario generico",
        "12:00" not in _cerr_hoy)
    chk("tolera la redaccion suelta ('hoy abro 14 hrs')",
        "14:00" in mensaje_cerrado(_menu_cerr, "hoy abro 14 hrs"))
    chk("una instruccion que NO habla de apertura no lo cambia",
        "lunes a viernes" in mensaje_cerrado(_menu_cerr, "se acabo el chacarero"))
    chk("una hora imposible no se cuela",
        "lunes a viernes" in mensaje_cerrado(_menu_cerr, "hoy abrimos a las 99:00"))

    print("== 03-ago-2026 (P18): candado de TEXTO -- churrasco/mechada sin tamaño ==")
    _t1 = _proto.sanear_tamano_sandwich(
        "Perfecto. ¿Quieres algun tamaño en los churrascos o solo queda asi nomas?")
    chk("la pregunta por el tamaño de un churrasco no llega al cliente",
        "tamano" not in _proto._norm(_t1).replace("tamaño", "tamano") or "?" not in _t1)
    chk("...y en su lugar queda la frase que dicto Christian",
        _proto.FRASE_TAMANO_UNICO in _t1)
    chk("lo que el modelo dijo ANTES se conserva", _t1.startswith("Perfecto."))
    # Regresion: la primera version rejuntaba las oraciones con espacios y aplastaba los
    # saltos de linea -- la plantilla de bienvenida habria llegado en un solo renglon.
    _t5 = ("Hola! Bienvenid@ a Patata Huechuraba\n•Nombre:\n•Pedido:\n"
           "¿El churrasco lo quieres XL?")
    _t5s = _proto.sanear_tamano_sandwich(_t5)
    chk("al sacar la pregunta, los saltos de linea del resto quedan intactos",
        "•Nombre:\n•Pedido:" in _t5s and "XL" not in _t5s)
    _t2 = "Listo, 2 Vienesas Italiano XL. ¿Algo mas?"
    chk("una pregunta de tamaño en VIENESA (que si lleva) no se toca",
        _proto.sanear_tamano_sandwich(_t2) == _t2)
    _t3 = "Anotado: 1 Churrasco Italiano. ¿Lo quieres con papas?"
    chk("una pregunta normal sobre un churrasco no se toca",
        _proto.sanear_tamano_sandwich(_t3) == _t3)
    _t4 = "El churrasco viene en tamaño unico."
    chk("una AFIRMACION sobre el tamaño (sin pregunta) no se toca",
        _proto.sanear_tamano_sandwich(_t4) == _t4)
    # Integracion: el candado tiene que morder en el MENSAJE REAL, no solo como funcion
    # suelta. Es el caso textual del chat que trajo Gonza el 30-jul.
    with carpeta_temporal() as tmp16:
        conv16 = Path(tmp16) / "conv_tamano.db"
        vis16 = procesar_mensaje(
            "56944445555", "quiero 2 churrascos italianos", {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso(
                ["Anotado. ¿Los quieres XL o normales los churrascos?"]),
            nucleo=NucleoPedidos(db_path=Path(tmp16) / "p16.db"), conv_db=conv16)
        chk("en la conversacion real, la pregunta por el tamaño no llega al cliente",
            "XL" not in vis16)
        # 25-sep-2026 (caso 67): este cliente NO pregunto por el tamaño, asi que ya no
        # se le agrega la frase del local -- era dar informacion interna sin que la
        # pidieran. La frase queda para cuando el cliente SI pregunta (abajo).
        chk("...y como el cliente no pregunto por el tamaño, NO se le agrega la frase",
            _proto.FRASE_TAMANO_UNICO not in vis16 and "Anotado" in vis16)
        chk("si el cliente SI pregunto por el tamaño, lee la frase del local",
            _proto.FRASE_TAMANO_UNICO in _proto.sanear_tamano_sandwich(
                "Anotado. ¿Los quieres XL o normales los churrascos?",
                "los churrascos tienen tamaño?"))
        chk("el historial guarda lo que el cliente REALMENTE leyo",
            all("XL" not in (t.get("texto") or "")
                for t in historial("56944445555", db_path=conv16)
                if t.get("rol") == "asistente"))

    print("== 24-sep-2026: candado del ORDEN de la bienvenida (caso del curador 22-sep) ==")
    _SAL = "Hola! Soy Patata bot 🤖. Bienvenid@ a Patata Huechuraba 🌭🍟"
    _DIN = ("Dinos;\n•Es delivery o retiro?:\n•Direccion (si es Delivery):\n•Pedido:\n"
            "•Forma de pago:")
    _DEM = "Hoy estamos con un tiempo aproximado de 30 min para delivery y 10 min para retiro."
    _AVD = "Aviso: hoy estamos con un tiempo aproximado de 30 min para delivery y 10 min para retiro."
    _o1 = _proto.ordenar_bienvenida(_AVD + "\n\n" + _SAL + "\n\n" + _DIN, primer_turno=True)
    chk("caso real turno 1: el saludo va PRIMERO aunque el modelo puso el aviso adelante",
        _o1.startswith("Hola! Soy Patata bot"))
    chk("...la demora que vino como aviso pierde el 'Aviso:' y queda antes de la ficha",
        "Aviso" not in _o1 and _o1.index("tiempo aproximado") < _o1.index("Dinos;"))
    _o1b = _proto.ordenar_bienvenida(
        _SAL + "\n\n" + _AVD + "\n\n" + _DEM + "\n\n" + _DIN, primer_turno=True)
    chk("aviso sobre la demora + demora normal: la demora se dice UNA sola vez",
        _o1b.count("tiempo aproximado") == 1)
    _o1c = _proto.ordenar_bienvenida(
        _SAL + "\n\n" + _DEM + "\n\nAviso: hoy estamos con un tiempo aproximado de 60 min "
        "para delivery y 10 min para retiro.\n\n" + _DIN, primer_turno=True)
    chk("si las dos demoras difieren, gana la del aviso (la puso el local)",
        "60 min" in _o1c and "30 min" not in _o1c)
    _o1d = _proto.ordenar_bienvenida(
        "Aviso: hoy nos quedamos sin chacarero.\n\n" + _SAL + "\n\n" + _DEM + "\n\n" + _DIN,
        primer_turno=True)
    chk("orden completo: saludo -> aviso -> demora -> ficha",
        _o1d.index("Soy Patata") < _o1d.index("chacarero") < _o1d.index("tiempo aproximado")
        < _o1d.index("Dinos;"))
    _o1e = _proto.ordenar_bienvenida(
        "Si, estamos atendiendo.\n\n" + _SAL + "\n\n" + _DEM + "\n\n" + _DIN, primer_turno=True)
    chk("la respuesta a su pregunta del primer mensaje queda ARRIBA del saludo",
        _o1e.startswith("Si, estamos atendiendo.\n\nHola! Soy Patata bot"))
    _o2 = _proto.ordenar_bienvenida(
        "Si, estamos atendiendo.\n\n" + _AVD + "\n\nHola! Soy Patata bot 🤖\n\n" + _DIN,
        primer_turno=False)
    chk("caso real turno 2: sin saludo, sin aviso y sin ficha vacia -- solo la respuesta",
        _o2 == "Si, estamos atendiendo.")
    _cierre = "Confirmado. Demora 10 min aprox, ¡gracias!"
    chk("el cierre con la demora (sin paquete de bienvenida) no se toca",
        _proto.ordenar_bienvenida(_cierre, primer_turno=False) == _cierre)
    _resumen = ("•Es delivery o retiro?: Retiro\n•Pedido:\n   - 1 Papas Fritas grande ($4.000)\n"
                "•Forma de pago: Tarjeta\n\nTotal $5.000.")
    chk("el resumen del pedido (viñetas sin 'Dinos;') no se toca",
        _proto.ordenar_bienvenida(_resumen, primer_turno=False) == _resumen)

    print("== 25-sep-2026: candados del caso 65 (pago repetido, voseo, nota en la ficha) ==")
    _prev65 = [{"rol": "usuario", "texto": "Delivery\nTransferencia"},
               {"rol": "asistente", "texto": "Perfecto, transferencia. ¿Me confirmas tu dirección?"}]
    _pago65 = _proto.pago_ya_dicho(
        _proto.textos_cliente_del_pedido(_prev65, "Carlos Aguirre luco 760", False))
    chk("el pago dicho en un turno anterior cuenta como conocido", _pago65)
    chk("...pero el de un pedido YA cerrado no cuenta para el pedido nuevo",
        not _proto.pago_ya_dicho(_proto.textos_cliente_del_pedido(
            _prev65 + [{"rol": "asistente", "texto": "Confirmado. Demora 30 min aprox, ¡gracias!"}],
            "quiero otras papas", False)))
    chk("...y al abrir conversacion solo cuenta el mensaje actual",
        not _proto.pago_ya_dicho(_proto.textos_cliente_del_pedido(_prev65, "hola", True)))
    _p1 = _proto.sanear_pregunta_pago(
        "Perfecto. ¿Querés agregar algo más a las papas grandes? ¿O cómo pagas?", True)
    chk("caso real: la pregunta '¿O cómo pagas?' se saca y la otra queda",
        _p1 == "Perfecto. ¿Querés agregar algo más a las papas grandes?")
    _p2 = _proto.sanear_pregunta_pago(
        "Perfecto, sumo una salsa de queso también. ¿Cómo prefieres pagar: efectivo, "
        "tarjeta o transferencia?", True)
    chk("caso real: '¿Cómo prefieres pagar...?' se saca entera",
        _p2 == "Perfecto, sumo una salsa de queso también.")
    _p3 = _proto.sanear_pregunta_pago(
        "Las papas fritas grandes valen $4.000. ¿Me confirmas si es para retiro o delivery, "
        "y cómo pagas?", True)
    chk("pregunta doble: se saca SOLO la parte del pago",
        _p3 == "Las papas fritas grandes valen $4.000. ¿Me confirmas si es para retiro o delivery?")
    chk("si el pago NO se sabe, la pregunta se respeta",
        _proto.sanear_pregunta_pago("¿Cómo pagas?", False) == "¿Cómo pagas?")
    _p4 = "Perfecto. Mándame el pantallazo cuando pagues, ¿ya?"
    chk("pedir el pantallazo NO es preguntar el pago",
        _proto.sanear_pregunta_pago(_p4, True) == _p4)
    _p5 = _proto.sanear_pregunta_pago(
        "Perfecto, retiro. Y confirmame si la forma de pago es tarjeta (débito o crédito "
        "se considera igual).", True)
    chk("caso 22-sep: 'confirmame si la forma de pago es tarjeta' tampoco llega",
        _p5 == "Perfecto, retiro.")
    chk("voseo: '¿Querés agregar...?' -> '¿Quieres agregar...?'",
        _proto.sanear_voseo("¿Querés agregar algo más?") == "¿Quieres agregar algo más?")
    chk("voseo: 'Confirmame' -> 'Confírmame' (mayuscula conservada)",
        _proto.sanear_voseo("Confirmame también la dirección") == "Confírmame también la dirección")
    chk("voseo: 'Dale' NO se toca (lo usa el bot en frases validas)",
        _proto.sanear_voseo("Dale, delivery a Los Aromos 456.") == "Dale, delivery a Los Aromos 456.")
    chk("voseo: 'quieres' y 'puedes' neutros no se tocan",
        _proto.sanear_voseo("¿Quieres algo más? Puedes pedir.") == "¿Quieres algo más? Puedes pedir.")

    print("== 05-ago-2026: candado del SALUDO -- la bienvenida se dice UNA vez ==")
    _FICHA = ("•Nombre registrado: Gonzalo ✅\n•Retiro o delivery: delivery ✅\n"
              "•Direccion (si es Delivery):\n•Pedido: Vienesa Italiano normal\n"
              "•Forma de pago:")
    _s1 = "Hola! Bienvenid@ a Patata Huechuraba 🌭🍟\nPara realizar tu pedido, envianos:\n" + _FICHA
    chk("en el PRIMER mensaje el saludo se respeta entero",
        _proto.sanear_saludo_repetido(_s1, primer_turno=True) == _s1)
    _s1b = _proto.sanear_saludo_repetido(_s1, primer_turno=False)
    chk("a mitad de la conversacion la bienvenida no llega al cliente",
        "Bienvenid" not in _s1b)
    chk("...y su pie 'Para realizar tu pedido' se va con ella",
        "Para realizar tu pedido" not in _s1b)
    chk("...pero la ficha queda COMPLETA, con los datos ya rellenados",
        _s1b == _FICHA)
    # Las variantes reales que aparecen en los chats de Patata (con/sin 👋, con/sin ¡!).
    for _v in ("Hola 👋 ¡Bienvenid@ a Patata Huechuraba! 🌭🍟",
               "Hola, ¡Bienvenid@ a Patata Huechuraba!🌭🍟",
               "Bienvenid@ a Patata Huechuraba🌭🍟"):
        chk(f"variante de saludo reconocida: {_v[:22]}...",
            "Bienvenid" not in _proto.sanear_saludo_repetido(_v + "\n" + _FICHA,
                                                             primer_turno=False))
    # Caso textual del chat que trajo Gonza (05-ago): el saludo colado DESPUES de una
    # pregunta del propio bot.
    _s2 = ("¿Me confirmas tu nombre completo para el pedido?\n\n"
           "Hola! Bienvenid@ a Patata Huechuraba 🌭🍟\n" + _FICHA)
    _s2b = _proto.sanear_saludo_repetido(_s2, primer_turno=False)
    chk("lo que el bot dijo ANTES del saludo se conserva",
        _s2b.startswith("¿Me confirmas tu nombre completo"))
    chk("...y no queda un hueco de lineas vacias donde estaba el saludo",
        "\n\n\n" not in _s2b)
    _s3 = "Hola! Bienvenid@ a Patata Huechuraba 🌭🍟 Tenemos completos, churrascos y papas."
    chk("si el saludo viene pegado a contenido real, el contenido NO se pierde",
        "Tenemos completos" in _proto.sanear_saludo_repetido(_s3, primer_turno=False))
    _s4 = "Hola! Bienvenid@ a Patata Huechuraba 🌭🍟"
    chk("un mensaje que es SOLO el saludo no se vacia (un WhatsApp vacio es peor)",
        _proto.sanear_saludo_repetido(_s4, primer_turno=False) == _s4)
    _s5 = "Listo, anotado. ¿Algo mas?"
    chk("un mensaje sin bienvenida no se toca",
        _proto.sanear_saludo_repetido(_s5, primer_turno=False) == _s5)
    # Integracion: el candado tiene que morder en la CONVERSACION real, no solo como
    # funcion suelta -- y el historial debe guardar lo que el cliente leyo, si no el
    # modelo se sigue viendo repetir el saludo en su propio contexto.
    with carpeta_temporal() as tmp17:
        conv17 = Path(tmp17) / "conv_saludo.db"
        nuc17 = NucleoPedidos(db_path=Path(tmp17) / "p17.db")
        cer17 = _CerebroFalso([_s1, "Cambio a retiro entonces.\n" + _s1])
        vis17a = procesar_mensaje("56933334444", "hola", {**CONFIG_DEFAULT},
                                  cerebro=cer17, nucleo=nuc17, conv_db=conv17)
        chk("primer mensaje de la conversacion: el cliente SI lee la bienvenida",
            "Bienvenid" in vis17a)
        vis17b = procesar_mensaje("56933334444", "mejor yo voy a retirar",
                                  {**CONFIG_DEFAULT}, cerebro=cer17, nucleo=nuc17,
                                  conv_db=conv17)
        chk("segundo mensaje: la bienvenida repetida ya no le llega",
            "Bienvenid" not in vis17b)
        chk("...y lo que el bot respondio de verdad se conserva",
            "Cambio a retiro" in vis17b and "Forma de pago" in vis17b)
        chk("el historial guarda lo que el cliente REALMENTE leyo",
            sum(1 for t in historial("56933334444", db_path=conv17)
                if t.get("rol") == "asistente" and "Bienvenid" in (t.get("texto") or "")) == 1)

    # Regresion del MISMO dia (05-ago): la primera version de este candado miraba solo
    # "historial vacio", y Gonza la rompio probando en produccion -- cancelo el pedido,
    # volvio a escribir "hola" 74 minutos despues y recibio una ficha PELADA: sin saludo
    # (el candado se lo comio) y sin su nombre registrado (la nota de cliente conocido
    # tampoco se inyecto). Un solo sintoma visible, dos efectos, una sola causa.
    with carpeta_temporal() as tmp18:
        import sqlite3 as _sq3
        conv18 = Path(tmp18) / "conv_reapertura.db"
        nuc18 = NucleoPedidos(db_path=Path(tmp18) / "p18.db")
        cer18 = _CerebroFalso([_s1, _s1])
        tel18 = "56922221111"
        procesar_mensaje(tel18, "hola", {**CONFIG_DEFAULT}, cerebro=cer18,
                         nucleo=nuc18, conv_db=conv18)
        # El cliente cancela y se va. Cancelar NO despacha nada, asi que la conversacion
        # queda viva y no caduca (faltan 4h45 para las 6h). Se envejecen sus turnos los
        # 74 minutos del chat real.
        _c18 = _sq3.connect(conv18)
        _c18.execute("UPDATE turnos SET creado_en=? WHERE telefono=?",
                     ((datetime.now() - timedelta(minutes=74)).isoformat(timespec="seconds"),
                      tel18))
        _c18.commit()
        _c18.close()
        chk("el hilo viejo NO se borra: sigue vivo para el contexto",
            len(historial(tel18, db_path=conv18)) > 0)
        chk("...y el bot mide bien el silencio que hubo",
            70 < (minutos_desde_ultimo_turno(tel18, db_path=conv18) or 0) < 80)
        vis18 = procesar_mensaje(tel18, "hola", {**CONFIG_DEFAULT}, cerebro=cer18,
                                 nucleo=nuc18, conv_db=conv18)
        chk("el que vuelve tras un silencio largo SI recibe el saludo de nuevo",
            "Bienvenid" in vis18)
        chk("...y no le llega una ficha pelada sin contexto",
            "Forma de pago" in vis18 and "Bienvenid" in vis18)
        chk("un telefono sin turnos previos da silencio None (no se inventa un cero)",
            minutos_desde_ultimo_turno("56900000000", db_path=conv18) is None)

    print("== 04-ago-2026: candado de MONTOS -- ningun $ suelto que no venga del menu ==")
    _menu_montos = json.loads(MENU_JSON.read_text(encoding="utf-8"))
    _montos = montos_validos_del_menu(_menu_montos)
    chk("el precio real de un Churrasco Italiano esta en el set", 4900 in _montos)
    chk("el precio 2x tambien esta", 8900 in _montos)
    chk("la tarifa minima de delivery tambien esta", 1500 in _montos)
    chk("un monto inventado NO esta", 12345 not in _montos)
    _m1 = "El Churrasco Italiano vale $4.900. ¿Te preparo uno?"
    chk("una frase con un precio REAL del menu no se toca",
        sanear_montos_no_autorizados(_m1, _montos) == _m1)
    _m2 = "El Churrasco Italiano vale $6.300. ¿Te preparo uno?"
    _m2s = sanear_montos_no_autorizados(_m2, _montos)
    chk("una frase con un precio INVENTADO se saca", "6.300" not in _m2s)
    chk("...pero solo esa oracion -- el resto de la respuesta se conserva (mismo "
        "criterio que sanear_tamano_sandwich)", "Te preparo" in _m2s)
    _m3 = "Hola! ¿En que te ayudo?"
    chk("un texto sin ningun $ no se toca (ni siquiera se evalua)",
        sanear_montos_no_autorizados(_m3, _montos) == _m3)
    # Integracion: charla suelta (sin PEDIDO_JSON este turno) con un precio inventado.
    with carpeta_temporal() as tmp_monto:
        conv_monto = Path(tmp_monto) / "conv_monto.db"
        vis_monto = procesar_mensaje(
            "56933339999", "cuanto cuesta el churrasco barros luco", {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso(["El Churrasco Barros Luco vale $7.100, ¿te preparo uno?"]),
            conv_db=conv_monto)
        chk("en la conversacion real, un precio inventado no llega al cliente",
            "7.100" not in vis_monto)
        chk("el historial guarda lo que el cliente REALMENTE leyo (sin el monto falso)",
            all("7.100" not in (t.get("texto") or "")
                for t in historial("56933339999", db_path=conv_monto)
                if t.get("rol") == "asistente"))
    # El candado NO debe tocar el flujo de despacho (_aviso_total ya lo cubre, y ahi el
    # diseño es dejar ver el monto equivocado del modelo + el correcto despues).
    with carpeta_temporal() as tmp_monto2:
        conv_monto2 = Path(tmp_monto2) / "conv_monto2.db"
        nucleo_monto2 = NucleoPedidos(db_path=Path(tmp_monto2) / "p_monto2.db")
        con_error_monto = respuesta_cerrada.replace("Total $8.900", "Total $9.800")
        vis_monto2 = procesar_mensaje(
            "56933338888", "dos churrascos italianos retiro tarjeta", {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso([con_error_monto]), nucleo=nucleo_monto2, conv_db=conv_monto2)
        chk("el despacho real sigue mostrando el monto equivocado del modelo + el correcto "
            "(el candado de montos no interfiere con _aviso_total)",
            "$9.800" in vis_monto2 and "✅ Total: $8.900" in vis_monto2)

    print("== 05-ago-2026: el candado de montos ya NO mutila el resumen del cierre ==")
    # Causa medida (pendiente #8): el Paso 1 del cierre (repetir el pedido y pedir
    # confirmacion, SIN llamar a emitir_pedido) cae en esta misma rama, porque
    # _rescatar_pedido devuelve None cuando el modelo marca `marcar_incompleto`. El
    # cliente leia "¿todo bien asi?" sin haber visto nunca su pedido ni el total.
    _cierre_p1 = ("Quedaria 2 Churrasco Italiano + 1 Lata CCU, retiro, pagas con tarjeta, "
                  "total $10.400. ¿todo bien asi? confirmamelo y lo mando enseguida")
    _cierre_p1s = sanear_montos_no_autorizados(_cierre_p1, _montos)
    chk("el total (una SUMA, que nunca puede estar en el menu) se saca",
        "10.400" not in _cierre_p1s)
    chk("...pero el pedido que el cliente tiene que confirmar SOBREVIVE",
        "2 Churrasco Italiano" in _cierre_p1s and "Lata CCU" in _cierre_p1s
        and "retiro" in _cierre_p1s and "tarjeta" in _cierre_p1s)
    chk("y la oracion queda bien cerrada, sin restos del recorte",
        "tarjeta." in _cierre_p1s and ", ." not in _cierre_p1s)
    _sub = ("Va quedando asi: 2 Vienesa Completo XL y 1 Lata CCU. El subtotal es $11.300 "
            "y el envio se confirma segun tu direccion. ¿Lo dejamos asi?")
    _subs = sanear_montos_no_autorizados(_sub, _montos)
    chk("un SUBtotal invalido tampoco se lleva puesta la frase del envio",
        "11.300" not in _subs and "envio se confirma" in _subs)
    chk("...y no quedan dos oraciones pegadas sin espacio",
        "CCU. El envio" in _subs)
    chk("un precio de PRODUCTO inventado sigue tirando abajo la oracion entera "
        "(no-regresion: el recorte quirurgico es solo para totales)",
        "6.300" not in sanear_montos_no_autorizados(_m2, _montos)
        and "vale" not in sanear_montos_no_autorizados(_m2, _montos))
    # Montos que dijo el CLIENTE: son su plata, no un precio inventado por el modelo.
    chk("'$20.000' escrito por el cliente se reconoce",
        20000 in montos_dichos_por_el_cliente(["pago con $20.000"]))
    chk("'20 lucas' tambien (asi se escribe de verdad por WhatsApp)",
        20000 in montos_dichos_por_el_cliente(["cancelo con 20 lucas"]))
    chk("'10 mil' tambien", 10000 in montos_dichos_por_el_cliente(["con 10 mil"]))
    chk("el numero de la CALLE no se cuenta como plata (sin $ ni 'lucas')",
        montos_dichos_por_el_cliente(["mando a Los Aromos 456"]) == set())
    _vuelto = "Listo, pagas con $20.000 y te llevamos el vuelto. ¿Confirmas?"
    chk("el bot puede repetir el billete con que paga el cliente (antes se censuraba)",
        sanear_montos_no_autorizados(_vuelto, _montos,
                                     montos_dichos_por_el_cliente(["pago con 20 lucas"]))
        == _vuelto)
    chk("...pero un monto que el cliente NUNCA dijo se sigue sacando",
        "20.000" not in sanear_montos_no_autorizados(_vuelto, _montos))
    # Integracion: el Paso 1 del cierre, por el camino real de procesar_mensaje.
    with carpeta_temporal() as tmp_p1:
        conv_p1 = Path(tmp_p1) / "conv_p1.db"
        vis_p1 = procesar_mensaje(
            "56933337777", "2 churrascos italianos, retiro, pago con 20 lucas",
            {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso([
                "Quedaria 2 Churrasco Italiano, retiro, pagas con $20.000, total $9.800. "
                "¿Confirmas y lo mando?"]),
            conv_db=conv_p1)
        chk("en la conversacion real, el cliente SI ve el pedido que debe confirmar",
            "2 Churrasco Italiano" in vis_p1 and "Confirmas" in vis_p1)
        chk("...ve el billete con que dijo que paga",  "$20.000" in vis_p1)
        chk("...y NO ve el total inventado por el modelo", "9.800" not in vis_p1)

    print("== 05-ago-2026: BLOQUE DE SALIDA en 2 pasos -- la ficha con precios la pone "
          "el codigo (ejemplo de Gonza) ==")
    # Paso 1: previsualizar_pedido. El modelo dice QUE pidio el cliente; el codigo dice
    # CUANTO sale (items del Tarificador + envio de la planilla por calle).
    _prev_delivery = {
        "cliente": "", "modalidad": "delivery", "direccion": "Cancion de la Pampa 5727",
        "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                   "variante": None, "cantidad": 1, "precio_unitario": 4900,
                   "subtotal": 4900, "notas": ""}],
        "delivery_cobro": 0, "total": 0, "forma_pago": "tarjeta",
        "hora_programada": None, "derivar_humano": False, "comentarios": "",
    }
    _ficha = _ficha_confirmacion(_prev_delivery, "56911112222", CONFIG_DEFAULT)
    chk("la ficha existe", bool(_ficha))
    chk("trae la modalidad", "•Es delivery o retiro?: Delivery" in (_ficha or ""))
    chk("trae la direccion CON su tarifa real de la planilla (no la minima adivinada)",
        "Cancion de la Pampa 5727 ($1.500)" in (_ficha or ""))
    chk("trae el producto con su precio real del menu",
        "1 Churrasco Italiano ($4.900)" in (_ficha or ""))
    chk("trae la forma de pago", "•Forma de pago: Tarjeta" in (_ficha or ""))
    chk("el TOTAL suma productos + envio, calculado por el codigo",
        "Total con delivery $6.400." in (_ficha or ""))
    chk("y cierra pidiendo confirmacion", "¿Está bien así" in (_ficha or ""))
    chk("sin nombre real, la linea de Nombre no aparece (ejemplo de Gonza)",
        "•Nombre" not in (_ficha or ""))
    _prev_con_nombre = {**_prev_delivery, "cliente": "Marisol"}
    _ficha_nom = _ficha_confirmacion(_prev_con_nombre, "56911112222", CONFIG_DEFAULT) or ""
    chk("con nombre real, si aparece", "•Nombre: Marisol" in _ficha_nom)
    chk("y va DESPUES de la direccion, no arriba (orden pedido por Gonza)",
        _ficha_nom.index("•Dirección") < _ficha_nom.index("•Nombre") < _ficha_nom.index("•Pedido"))
    # 25-sep-2026 (caso 65): la referencia para encontrar al cliente se ve en la ficha.
    _ficha_nota = _ficha_confirmacion(
        {**_prev_con_nombre, "comentarios": "Colegio Mater Dolorosa, entregar a Martina Rivas 7°B"},
        "56911112222", CONFIG_DEFAULT) or ""
    chk("la nota (colegio y curso) aparece en la ficha",
        "•Nota: Colegio Mater Dolorosa, entregar a Martina Rivas 7°B" in _ficha_nota)
    chk("...despues del nombre y antes del pedido",
        _ficha_nota.index("•Nombre") < _ficha_nota.index("•Nota") < _ficha_nota.index("•Pedido"))
    chk("sin comentarios no hay linea de Nota", "•Nota" not in _ficha_nom)
    chk("una derivacion a humano NO se muestra como nota",
        "•Nota" not in (_ficha_confirmacion(
            {**_prev_con_nombre, "derivar_humano": True, "comentarios": "pidio hablar con alguien"},
            "56911112222", CONFIG_DEFAULT) or ""))
    # Retiro: sin linea de direccion y sin "con delivery" en el total.
    _prev_retiro = {**_prev_delivery, "modalidad": "retiro", "direccion": None,
                    "items": [{**_prev_delivery["items"][0], "cantidad": 2}]}
    _ficha_r = _ficha_confirmacion(_prev_retiro, "56911112222", CONFIG_DEFAULT)
    chk("en retiro no se muestra linea de direccion", "•Dirección" not in (_ficha_r or ""))
    chk("en retiro el total es solo de productos, CON la promo 2x del motor real "
        "(2 x $4.900 = $8.900, no $9.800)", "Total $8.900." in (_ficha_r or ""))
    # Calle que la planilla no cubre: no se inventa un envio ni un total final.
    _prev_sin_tarifa = {**_prev_delivery, "direccion": "Avenida Inventada 500"}
    _ficha_s = _ficha_confirmacion(_prev_sin_tarifa, "56911112222", CONFIG_DEFAULT)
    chk("calle fuera de la planilla: NO se inventa un monto de envio",
        "Avenida Inventada 500" in (_ficha_s or "") and
        "Avenida Inventada 500 ($" not in (_ficha_s or ""))
    chk("...y se le dice que el envio lo confirma el local",
        "envío que te confirmamos" in (_ficha_s or ""))
    # Producto que el menu no reconoce: sin total (misma regla que _aviso_total).
    _prev_roto = {**_prev_delivery,
                  "items": [{"categoria": "Churrascos", "producto": "Churrasco Marciano",
                             "variante": None, "cantidad": 1, "precio_unitario": 9999,
                             "subtotal": 9999, "notas": ""}]}
    chk("producto inexistente: no se muestra ficha a medias",
        _ficha_confirmacion(_prev_roto, "56911112222", CONFIG_DEFAULT) is None)
    # Integracion: el turno completo, y sobre todo que NO entre a cocina.
    with carpeta_temporal() as tmp_prev:
        conv_prev = Path(tmp_prev) / "conv_prev.db"
        nucleo_prev = NucleoPedidos(db_path=Path(tmp_prev) / "p_prev.db")
        _resp_prev = ("Perfecto.\n<PEDIDO_JSON>\n" +
                      json.dumps({**_prev_delivery, "_tool": "previsualizar_pedido"}) +
                      "\n</PEDIDO_JSON>")
        vis_prev = procesar_mensaje(
            "56911112222", "1 churrasco italiano a cancion de la pampa 5727, tarjeta",
            {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([_resp_prev]),
            nucleo=nucleo_prev, conv_db=conv_prev)
        chk("el cliente recibe la linea del modelo Y la ficha del sistema",
            vis_prev.startswith("Perfecto.") and "Total con delivery $6.400." in vis_prev)
        chk("PASO 1 NO manda el pedido a cocina (la cola sigue vacia)",
            len(nucleo_prev.cola()) == 0)
        chk("el historial guarda lo que el cliente leyo, ficha incluida",
            any("Total con delivery" in (t.get("texto") or "")
                for t in historial("56911112222", db_path=conv_prev)
                if t.get("rol") == "asistente"))
    # PASO 2: la despedida lleva la demora real (el modelo cierra sin texto casi siempre).
    _lunes_normal = datetime(2026, 8, 3, 20, 0, tzinfo=prototipo.TZ_CHILE)
    _viernes_cargado = datetime(2026, 8, 7, 20, 0, tzinfo=prototipo.TZ_CHILE)
    chk("retiro: la despedida dice la demora de retiro",
        prototipo.confirmacion_cierre("retiro", _lunes_normal) ==
        "Confirmado. Demora 10 min aprox, ¡gracias!")
    chk("delivery: la despedida dice la demora de delivery",
        prototipo.confirmacion_cierre("delivery", _lunes_normal) ==
        "Confirmado. Demora 30 min aprox, ¡gracias!")
    chk("viernes despues de las 19:00, la demora sube sola (misma regla que "
        "tiempos_estimados)",
        "30-40" in prototipo.confirmacion_cierre("delivery", _viernes_cargado) and
        "15 min" in prototipo.confirmacion_cierre("retiro", _viernes_cargado))
    chk("modalidad rara: cae a la linea de siempre, nunca a un mensaje roto",
        prototipo.confirmacion_cierre(None) == CONFIRMACION_CIERRE_RESPALDO)
    with carpeta_temporal() as tmp_cierre:
        conv_cierre = Path(tmp_cierre) / "conv_cierre.db"
        nucleo_cierre = NucleoPedidos(db_path=Path(tmp_cierre) / "p_cierre.db")
        _resp_cierre = "<PEDIDO_JSON>\n" + json.dumps({
            "cliente": "", "modalidad": "retiro", "direccion": None,
            "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                       "variante": None, "cantidad": 1, "precio_unitario": 4900,
                       "subtotal": 4900, "notas": ""}],
            "delivery_cobro": 0, "total": 4900, "forma_pago": "efectivo",
            "hora_programada": None, "derivar_humano": False, "comentarios": "",
        }) + "\n</PEDIDO_JSON>"
        vis_cierre = procesar_mensaje(
            "56911113333", "si, confirmo", {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso([_resp_cierre]), nucleo=nucleo_cierre, conv_db=conv_cierre)
        chk("cierre real sin texto del modelo: el cliente lee la demora, no un WhatsApp vacio",
            "Demora 10 min" in vis_cierre)
        chk("...y el pedido SI entro a cocina", len(nucleo_cierre.cola()) == 1)

    print("== 05-ago-2026 (pendiente #9bis): el PASO 1 deja de depender de la obediencia ==")
    # Medido: en 3 corridas contra gpt-4.1-mini, en 1 el modelo escribio el resumen a
    # mano en vez de llamar a previsualizar_pedido -- sin precios, y el cliente confirmo
    # sin ver nunca el total. El rescate ahora ofrece TAMBIEN esa tool.
    chk("un cierre con precios sigue disparando el rescate (no-regresion del '$')",
        _parece_cierre_sin_tool("Son $9.800 en total, lo mando enseguida"))
    _resumen_a_mano = ("Perfecto, entonces seria 1 Churrasco Italiano, delivery a Cancion de la "
                       "Pampa 5727, pagas con tarjeta. ¿Esta bien asi?")
    chk("un resumen escrito a mano y SIN precios ahora tambien lo dispara (el agujero real)",
        _parece_cierre_sin_tool(_resumen_a_mano))
    chk("la pregunta de media conversacion que el propio prompt le pide decir NO lo dispara",
        not _parece_cierre_sin_tool("¿me confirmas tu nombre y como pagas?"))
    chk("ni una pregunta normal del pedido",
        not _parece_cierre_sin_tool("Dale, delivery a Los Aromos 456. ¿Como pagas, "
                                    "efectivo, tarjeta o transferencia?"))
    chk("ni una respuesta cualquiera sin cierre",
        not _parece_cierre_sin_tool("Si, tenemos delivery. ¿Que te gustaria pedir?"))
    # El cierre del Paso 2 ya no lleva total, asi que el "$" dejo de alcanzar: sin esto,
    # un cierre de palabra sin emitir_pedido volvia a ser un pedido perdido (15-jul-2026).
    chk("el cierre REAL del Paso 2 (sin un solo $) dispara la red de seguridad",
        _parece_cierre_sin_tool(prototipo.confirmacion_cierre("retiro")))
    chk("...y tambien la variante de respaldo",
        _parece_cierre_sin_tool(CONFIRMACION_CIERRE_RESPALDO))
    chk("pero la BIENVENIDA, que anuncia los tiempos del dia, NO lo dispara "
        "(es el mensaje mas frecuente del bot: seria pagar un rescate por conversacion)",
        not _parece_cierre_sin_tool(
            "Hola! Bienvenid@ a Patata Huechuraba 🌭🍟\n\nHoy estamos con un tiempo "
            "aproximado de 30 min para delivery y 10 min para retiro.\n\nDinos;\n"
            "•Es delivery o retiro?:\n•Pedido:\n•Forma de pago:"))
    print("== 25-sep-2026: candados del caso #54 (tamaño no preguntado, nombre pisado) ==")
    _t54 = "Quisiera 4 churrasco italiano\nCon efectivo\nA nombre de Junior"
    _r54a = _proto.sanear_tamano_no_preguntado(
        "Los churrascos no tienen tamaño y ya tomé 4 churrascos italiano para ti.", _t54)
    chk("caso real: se saca la aclaracion y queda lo demas de la oracion",
        _r54a == "Ya tomé 4 churrascos italiano para ti.")
    _r54b = _proto.sanear_tamano_no_preguntado(
        "Perfecto. Solo recuerdo que los churrascos no llevan tamaño.\n\n¿Te los preparo así?", _t54)
    chk("replay del 25-sep: 'Solo recuerdo que... no llevan tamaño' no llega",
        _r54b == "Perfecto.\n\n¿Te los preparo así?")
    _p54 = "Los churrascos no tienen tamaño y ya tomé 4 churrascos italiano para ti."
    chk("si el CLIENTE pregunto por el tamaño, la aclaracion se respeta",
        _proto.sanear_tamano_no_preguntado(_p54, "el churrasco es grande o normal?") == _p54)
    chk("un mensaje que es SOLO la aclaracion no se vacia",
        _proto.sanear_tamano_no_preguntado(_proto.FRASE_TAMANO_UNICO, _t54)
        == _proto.FRASE_TAMANO_UNICO)
    chk("un mensaje sin la aclaracion no se toca",
        _proto.sanear_tamano_no_preguntado("Perfecto, retiro.", _t54) == "Perfecto, retiro.")
    chk("nombre: 'A nombre de Junior' se lee Junior", _proto.nombre_dado_por_cliente([_t54]) == "Junior")
    chk("nombre: el curso '7°b' no entra al nombre",
        _proto.nombre_dado_por_cliente(["Todo a nombre de Martina Rivas 7°b"]) == "Martina Rivas")
    chk("nombre: se corta en 'con efectivo'",
        _proto.nombre_dado_por_cliente(["a nombre de juan con efectivo"]) == "Juan")
    chk("nombre: 'a nombre de mi hijo' no inventa un nombre",
        _proto.nombre_dado_por_cliente(["a nombre de mi hijo"]) is None)
    chk("nombre: el guardado mas completo no se pisa ('Martina' vs 'Martina Rivas')",
        _proto.fijar_nombre_dicho({"cliente": "Martina Rivas"}, "Martina")["cliente"] == "Martina Rivas")
    with carpeta_temporal() as tmp54:
        _conv54 = Path(tmp54) / "conv54.db"
        guardar_turno("56983090000", "usuario", "Puedo hacer un pedido con retiro", db_path=_conv54)
        guardar_turno("56983090000", "asistente", "Perfecto, retiro. ¿Qué quieres pedir?", db_path=_conv54)
        guardar_turno("56983090000", "usuario", _t54, db_path=_conv54)
        guardar_turno("56983090000", "asistente", "¿Lo dejo como retiro?", db_path=_conv54)
        _prev54 = {**_prev_delivery, "cliente": "David", "modalidad": "retiro", "direccion": None,
                   "forma_pago": "efectivo", "_tool": "previsualizar_pedido"}
        _vis54 = procesar_mensaje(
            "56983090000", "Si con retiro", {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso(["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps(_prev54)
                                   + "\n</PEDIDO_JSON>"]),
            nucleo=NucleoPedidos(db_path=Path(tmp54) / "p54.db"), conv_db=_conv54)
        chk("integracion: la ficha sale a nombre de Junior aunque el modelo puso David",
            "•Nombre: Junior" in (_vis54 or "") and "David" not in (_vis54 or ""))

    print("== 25-sep-2026: caso 69 (churrasco brasileño = Especial, y sin espera muerta) ==")
    _tar69 = Tarificador.desde_json(prototipo.menu_json_activo())
    _i69 = _tar69.precio_item("Churrascos", "Churrasco Brasileno", None, 2)
    chk("caso real: 'Churrasco Brasileno' se cobra como Especial Brasileño con base churrasco",
        _i69["ok"] and _i69["producto"] == "Especial Brasileno" and _i69["variante"] == "churrasco")
    chk("...y en plural femenino con base mechada ('Mechadas Brasileñas')",
        _tar69.precio_item("", "Mechadas Brasileñas", None, 1).get("variante") == "mechada")
    chk("un nombre que tampoco es un especial sigue sin encontrarse",
        not _tar69.precio_item("Churrascos", "Churrasco Marciano", None, 1)["ok"])
    chk("lo que ya funcionaba no cambia (Churrasco Italiano)",
        _tar69.precio_item("Churrascos", "Churrasco Italiano", None, 1)["producto"] == "Churrasco Italiano")
    _item69 = {**_prev_delivery["items"][0], "categoria": "Churrascos", "cantidad": 2}
    _m69a = _mensaje_ficha_no_armada(
        {**_prev_delivery, "items": [{**_item69, "producto": "Churrasco Marciano"}]},
        "56900000069", CONFIG_DEFAULT)
    chk("si un producto no esta en el menu, se le pregunta cual es (no 'dame un segundo')",
        "«Churrasco Marciano»" in _m69a and "segundo" not in _m69a)
    _m69b = _mensaje_ficha_no_armada(
        {**_prev_delivery, "items": [{**_item69, "categoria": "Especiales",
                                      "producto": "Especial Brasileno", "variante": None}]},
        "56900000069", CONFIG_DEFAULT)
    chk("si falta elegir la base, se le pregunta churrasco o mechada",
        "Churrasco o Mechada" in _m69b)
    with carpeta_temporal() as tmp69:
        _conv69 = Path(tmp69) / "conv69.db"
        guardar_turno("56900000069", "usuario", "Me da dos churrascos brasileños", db_path=_conv69)
        guardar_turno("56900000069", "asistente", "¿Con cuánto pagas o justo?", db_path=_conv69)
        _prev69 = {**_prev_delivery, "items": [{**_item69, "producto": "Churrasco Brasileno",
                                                "variante": None}],
                   "forma_pago": "efectivo", "_tool": "previsualizar_pedido"}
        _vis69 = procesar_mensaje(
            "56900000069", "Justo cuanto seria", {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso(["<PEDIDO_JSON>\n" + json.dumps(_prev69) + "\n</PEDIDO_JSON>"]),
            nucleo=NucleoPedidos(db_path=Path(tmp69) / "p69.db"), conv_db=_conv69)
        chk("integracion caso 69: el cliente ve la ficha con el total, no una espera muerta",
            "Especial Brasileno" in (_vis69 or "") and "$11.200" in (_vis69 or "")
            and "segundo" not in (_vis69 or ""))

    print("== 25-sep-2026: casos 59-68 del curador ==")
    _t63 = ("Perfecto.\n\nfunctions.previsualizar_pedido árlonsiderando los datos dados:\n"
            "cliente: \"Paola Campillay\"\nforma_pago: \"tarjeta\"\n"
            "(No se pone total ni precios, el sistema lo calcula)")
    chk("63: la llamada interna escrita como texto no llega al cliente",
        _proto.sanear_texto_interno(_t63) == "Perfecto.")
    chk("63: un mensaje normal no se toca",
        _proto.sanear_texto_interno("Perfecto, retiro.") == "Perfecto, retiro.")
    _it_papas = {"categoria": "Papas Fritas", "producto": "Papas Fritas", "variante": "individual",
                 "cantidad": 1, "precio_unitario": 0, "subtotal": 0, "notas": ""}
    _prev63 = {**_prev_delivery, "items": [_prev_delivery["items"][0], _it_papas]}
    chk("63: si un producto no se puede cobrar, NO sale una ficha que lo esconda",
        _ficha_confirmacion(_prev63, "56911112222", CONFIG_DEFAULT) is None)
    _m63 = _mensaje_ficha_no_armada(_prev63, "56911112222", CONFIG_DEFAULT)
    chk("63: ...y se le ofrecen los tamaños que SI hay, con precio",
        "mediana ($2.500)" in _m63 and "grande ($4.000)" in _m63)
    _f60 = _ficha_confirmacion({**_prev_delivery, "forma_pago": "efectivo"},
                               "56911112222", CONFIG_DEFAULT) or ""
    chk("60: en efectivo, la ficha pregunta con cuanto paga JUNTO al total",
        "Total con delivery" in _f60 and "¿con cuánto pagas" in _f60)
    chk("60: ...pero no si el cliente ya dijo con cuanto paga",
        "¿con cuánto pagas" not in (_ficha_confirmacion(
            {**_prev_delivery, "forma_pago": "efectivo", "comentarios": "paga con 10000"},
            "56911112222", CONFIG_DEFAULT) or ""))
    chk("60: con tarjeta no se pregunta por el vuelto", "vuelto" not in (_ficha or ""))
    _chac = {**_prev_delivery["items"][0], "producto": "Churrasco Chacarero", "cantidad": 5}
    _prev61 = {**_prev_delivery, "items": [_chac]}
    chk("61: 5 chacareros sin decir aji -> falta preguntar",
        _proto.falta_preguntar_aji(_prev61, ["5 chacareros y 1 italiano"], []))
    chk("61: ...y la pregunta va en plural", "Los chacareros" in _proto.pregunta_aji(_prev61))
    chk("61: si el cliente dijo 'sin aji', no se pregunta",
        not _proto.falta_preguntar_aji(_prev61, ["5 chacareros sin aji"], []))
    chk("61: si el bot ya pregunto, no se vuelve a frenar (el cliente contesta 'con' a secas)",
        not _proto.falta_preguntar_aji(_prev61, ["con"], [_proto.pregunta_aji(_prev61)]))
    chk("61: sin chacareros no se pregunta nada",
        not _proto.falta_preguntar_aji(_prev_delivery, ["1 italiano"], []))
    for _f68 in ("Acabo de pedir a camino de la colina 1464", "Quería saber cuánto me salió",
                 "ya pedí hace rato, donde va mi pedido"):
        chk(f"68: '{_f68[:30]}...' es un pedido YA hecho", _proto.pregunta_por_pedido_existente(_f68))
    for _f68n in ("hola quiero pedir 2 italianos", "acabo de ver el menu, quiero un churrasco"):
        chk(f"68: '{_f68n[:30]}...' NO es un pedido ya hecho",
            not _proto.pregunta_por_pedido_existente(_f68n))
    with carpeta_temporal() as tmp6x:
        _conv6x = Path(tmp6x) / "conv6x.db"
        _cer68 = _CerebroFalso(["esto no deberia llegar"])
        _v68 = procesar_mensaje("56993470000", "Acabo de pedir a camino de la colina 1464",
                                {**CONFIG_DEFAULT}, cerebro=_cer68,
                                nucleo=NucleoPedidos(db_path=Path(tmp6x) / "p68.db"),
                                conv_db=_conv6x)
        chk("68 integracion: se deriva a una persona sin llamar al modelo",
            _v68 == _proto.MENSAJE_PEDIDO_EXISTENTE and _cer68.llamadas == 0)
        chk("68 integracion: ...y queda un caso humano para el POS",
            contar_casos_humanos(db_path=_conv6x) == 1)
        procesar_mensaje("56993470000", "cuanto me salio?", {**CONFIG_DEFAULT}, cerebro=_cer68,
                         nucleo=NucleoPedidos(db_path=Path(tmp6x) / "p68.db"), conv_db=_conv6x)
        chk("68 integracion: si insiste, no se duplica el aviso a los jefes",
            contar_casos_humanos(db_path=_conv6x) == 1)

        guardar_turno("56961610000", "usuario", "5 chacareros para retiro, tarjeta", db_path=_conv6x)
        guardar_turno("56961610000", "asistente", "Perfecto.", db_path=_conv6x)
        _p61 = {**_prev61, "_tool": "previsualizar_pedido"}
        _v61 = procesar_mensaje(
            "56961610000", "si eso", {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso(["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps(_p61) + "\n</PEDIDO_JSON>"]),
            nucleo=NucleoPedidos(db_path=Path(tmp6x) / "p61.db"), conv_db=_conv6x)
        chk("61 integracion: antes de la ficha se pregunta con o sin aji",
            _v61 == _proto.pregunta_aji(_prev61) and "Total" not in _v61)
        _v61b = procesar_mensaje(
            "56961610000", "con", {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso(["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps(_p61) + "\n</PEDIDO_JSON>"]),
            nucleo=NucleoPedidos(db_path=Path(tmp6x) / "p61.db"), conv_db=_conv6x)
        chk("61 integracion: contestado eso, ya sale la ficha con el total",
            "Total con delivery" in (_v61b or ""))

        guardar_turno("56991830000", "usuario", "2 italianos retiro efectivo", db_path=_conv6x)
        guardar_turno("56991830000", "asistente", "Confirmado. Demora 10 min aprox, ¡gracias!",
                      db_path=_conv6x)
        _nuc60 = NucleoPedidos(db_path=Path(tmp6x) / "p60.db")
        _emit60 = {**_prev_delivery, "_tool": "emitir_pedido"}
        _v60 = procesar_mensaje(
            "56991830000", "al que acabo de hacer y si tambien puede agregar unas papas grandes",
            {**CONFIG_DEFAULT},
            cerebro=_CerebroFalso(["<PEDIDO_JSON>\n" + json.dumps(_emit60) + "\n</PEDIDO_JSON>"]),
            nucleo=_nuc60, conv_db=_conv6x)
        chk("60 integracion: agregar a un pedido confirmado muestra la ficha, no 'Confirmado'",
            "¿Está bien así" in (_v60 or "") and not (_v60 or "").startswith("Confirmado"))
        chk("60 integracion: ...y no entra nada a cocina todavia", len(_nuc60.cola()) == 0)

    _s59 = ("Hola! Soy Patata bot 🤖. Bienvenid@ a Patata Huechuraba 🌭🍟\n\nDinos;\n"
            "•Es delivery o retiro?:\n•Pedido:\nSí, tenemos papas fritas. ¿Quieres pedir alguna?")
    chk("59: la respuesta que quedo DEBAJO de la ficha sube arriba del saludo",
        _proto.ordenar_bienvenida(_s59, True).startswith("Sí, tenemos papas fritas"))

    with carpeta_temporal() as tmp_seg:
        _conv_seg = Path(tmp_seg) / "conv_seg.db"
        _viejo_seg = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
        _cs = _con_conv_en(_conv_seg)
        _cs.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                    ("56945960000", "usuario", "Tienen papitas?", _viejo_seg))
        _cs.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                    ("56945960000", "asistente", "Sí, tenemos papas...", _viejo_seg))
        _cs.commit(); _cs.close()
        _wa_seg, _wa_orig_seg = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg)
            chk("59: el recordatorio 'Sigues ahi?' queda en la conversacion (visible en el curador)",
                historial("56945960000", db_path=_conv_seg)[-1]["texto"] == MENSAJE_SEGUIMIENTO_SALUDO)
            _cs = _con_conv_en(_conv_seg)
            _cs.execute("UPDATE turnos SET creado_en=?", (_viejo_seg,)); _cs.commit(); _cs.close()
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg)
            chk("59: ...y aunque pase el tiempo, NO se vuelve a insistir", len(_wa_seg) == 1)
        finally:
            enviar_whatsapp = _wa_orig_seg

    print("== 28-sep-2026: bug real del curador (casos 77/81/83) -- 'sigues ahi' "
          "despues de un pedido ya despachado, cuando el cliente solo se estaba "
          "despidiendo ==")
    # 07-oct-2026: estos dos casos usaban "¡Gracias a ti!" como ultimo turno del bot, pero
    # ahora esa despedida ya NO recibe seguimiento nunca (caso 125). Para que sigan midiendo
    # lo que median (con pedido despachado no insiste / sin pedido si insiste) se usa una
    # respuesta que no es la despedida.
    with carpeta_temporal() as tmp_seg3:
        _conv_seg3 = Path(tmp_seg3) / "conv_seg3.db"
        _resp_seg3 = Path(tmp_seg3) / "respaldo_seg3.db"
        _viejo_seg3 = (datetime.now() - timedelta(minutes=25)).isoformat(timespec="seconds")
        _cs3 = _con_conv_en(_conv_seg3)
        _cs3.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911182222", "usuario", "No nada mas, gracias", _viejo_seg3))
        _cs3.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911182222", "asistente", "De nada, que disfrutes.", _viejo_seg3))
        _cs3.commit(); _cs3.close()
        _wa_seg3, _wa_orig_seg3 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg3.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg3,
                                          respaldo_db_path=_resp_seg3)
            chk("control negativo: sin pedido despachado, SI insiste (es el otro uso real)",
                len(_wa_seg3) == 1)
        finally:
            enviar_whatsapp = _wa_orig_seg3

        # Ahora SI hubo un pedido despachado hace poco para ese mismo telefono (como en
        # los casos reales 77/81/83: confirmar -> el cliente dice "gracias" -> ese "gracias"
        # es el turno vivo que el ciclo de arriba ve).
        _guardar_ultimo_pedido("56911182222", "1x Churrasco Italiano · delivery · efectivo",
                                db_path=_resp_seg3)
        _cs3 = _con_conv_en(_conv_seg3)
        _cs3.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911182222", "usuario", "Ok", _viejo_seg3))
        _cs3.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911182222", "asistente", "De nada, que disfrutes.", _viejo_seg3))
        _cs3.commit(); _cs3.close()
        _wa_seg4, _wa_orig_seg4 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg4.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg3,
                                          respaldo_db_path=_resp_seg3)
            chk("bug real: con pedido despachado hace poco, YA NO insiste con 'sigues ahi'",
                len(_wa_seg4) == 0)
        finally:
            enviar_whatsapp = _wa_orig_seg4

    print("== 29-sep-2026: bug real del curador (conversacion 93) -- la ventana de "
          "'pedido reciente' se media desde el DESPACHO, no desde el ultimo mensaje del "
          "cliente, y un cliente esperando caia justo en el borde ==")
    with carpeta_temporal() as tmp_seg6:
        _conv_seg6 = Path(tmp_seg6) / "conv_seg6.db"
        _resp_seg6 = Path(tmp_seg6) / "respaldo_seg6.db"
        # Caso real: despachado hace 61 min (recien cruzo el viejo limite de
        # MINUTOS_MEMORIA_PEDIDO_RECIENTE=60), pero el cliente pregunto "esta listo?"
        # hace solo 10 min -- sigue esperando de verdad, no hay que insistirle.
        _guardar_ultimo_pedido("56911185555", "2x Vienesa Italiano · retiro · efectivo",
                                db_path=_resp_seg6)
        _resp6 = _con_respaldo(_resp_seg6)
        _resp6.execute("UPDATE ultimo_pedido_cliente SET creado_en=? WHERE telefono=?",
                        ((datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds"),
                         "56911185555"))
        _resp6.commit(); _resp6.close()
        _hace_10_seg6 = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        _cs6 = _con_conv_en(_conv_seg6)
        _cs6.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911185555", "usuario", "Esta listo?", _hace_10_seg6))
        _cs6.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911185555", "asistente", "El local te avisa apenas este listo...",
                      _hace_10_seg6))
        _cs6.commit(); _cs6.close()
        _wa_seg6, _wa_orig_seg6 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg6.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg6,
                                          respaldo_db_path=_resp_seg6)
            chk("conversacion 93: pasados los 60 min de DESPACHO pero con el cliente "
                "activo hace poco, YA NO insiste con 'sigues ahi'",
                len(_wa_seg6) == 0)
        finally:
            enviar_whatsapp = _wa_orig_seg6
        # Control negativo: mismo despacho viejo, pero el cliente TAMBIEN escribio hace
        # rato (sin actividad reciente de verdad) -- ahi si tiene que insistir, es el
        # comportamiento que no hay que romper.
        _cs6 = _con_conv_en(_conv_seg6)
        _viejo_seg6 = (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds")
        _cs6.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911186666", "usuario", "Hola", _viejo_seg6))
        _cs6.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911186666", "asistente", "Hola! Soy Patata bot...", _viejo_seg6))
        _cs6.commit(); _cs6.close()
        _wa_seg7, _wa_orig_seg7 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg7.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg6,
                                          respaldo_db_path=_resp_seg6)
            chk("control: sin pedido reciente para ESE telefono, sigue insistiendo normal",
                len(_wa_seg7) == 1)
        finally:
            enviar_whatsapp = _wa_orig_seg7

    print("== 01-oct-2026: bug real del curador (conversacion 97) -- 'sigues ahi' una "
          "HORA despues de un pedido ya confirmado, porque el cliente solo agrego un "
          "dato (el nombre) en la cola de cortesia y la ventana de 60 min termino "
          "igual expirando ==")
    with carpeta_temporal() as tmp_seg9:
        _conv_seg9 = Path(tmp_seg9) / "conv_seg9.db"
        _resp_seg9 = Path(tmp_seg9) / "respaldo_seg9.db"
        # Caso real: pedido confirmado, 9 min despues el cliente solo agrega "a nombre
        # de Javiera" (dentro de MINUTOS_VENTANA_POSCIERRE=15) y el bot le pregunta si
        # quiere algo mas. Nadie vuelve a escribir. El despacho quedo registrado hace
        # 69 min y el ultimo turno del cliente hace 60 min y medio -- ya fuera del
        # viejo limite de MINUTOS_MEMORIA_PEDIDO_RECIENTE (60), que es justo cuando el
        # ciclo decidia que ya no era "reciente" y mandaba el "sigues ahi" sin sentido.
        _guardar_ultimo_pedido("56934040659", "2x Vienesa Completo Normal · retiro · tarjeta",
                                db_path=_resp_seg9)
        _resp9 = _con_respaldo(_resp_seg9)
        _resp9.execute("UPDATE ultimo_pedido_cliente SET creado_en=? WHERE telefono=?",
                        ((datetime.now() - timedelta(minutes=69)).isoformat(timespec="seconds"),
                         _norm_telefono("56934040659")))
        _resp9.commit(); _resp9.close()
        _tramo_seg9 = (datetime.now() - timedelta(minutes=60, seconds=31)).isoformat(timespec="seconds")
        _cs9 = _con_conv_en(_conv_seg9)
        _cs9.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56934040659", "usuario", "A nombre de Javiera el pedido", _tramo_seg9))
        _cs9.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56934040659", "asistente",
                      "Perfecto, quedo a nombre de Javiera. ¿Quieres agregar algo más a tu pedido?",
                      _tramo_seg9))
        _cs9.commit(); _cs9.close()
        _wa_seg9, _wa_orig_seg9 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg9.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg9,
                                          respaldo_db_path=_resp_seg9)
            chk("conversacion 97: cola de cortesia que arranco DENTRO de la ventana de "
                "poscierre, YA NO insiste con 'sigues ahi' aunque pasara la hora entera",
                len(_wa_seg9) == 0)
        finally:
            enviar_whatsapp = _wa_orig_seg9
        # Control negativo: mismo despacho viejo, pero el tramo vivo arranca TARDE --
        # fuera de la ventana de poscierre, como una conversacion nueva de verdad -- y
        # con el cliente sin actividad reciente (fuera de MINUTOS_MEMORIA_PEDIDO_RECIENTE):
        # ahi si insiste, es el comportamiento que no hay que romper.
        _cs9 = _con_conv_en(_conv_seg9)
        _tramo_nuevo_seg9 = (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds")
        _cs9.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56934049999", "usuario", "Hola, otra vez yo", _tramo_nuevo_seg9))
        _cs9.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56934049999", "asistente", "Hola! Soy Patata bot...", _tramo_nuevo_seg9))
        _cs9.commit(); _cs9.close()
        _guardar_ultimo_pedido("56934049999", "1x Churrasco Mechada · retiro · efectivo",
                                db_path=_resp_seg9)
        _resp9 = _con_respaldo(_resp_seg9)
        _resp9.execute("UPDATE ultimo_pedido_cliente SET creado_en=? WHERE telefono=?",
                        ((datetime.now() - timedelta(hours=5)).isoformat(timespec="seconds"),
                         _norm_telefono("56934049999")))
        _resp9.commit(); _resp9.close()
        _wa_seg10, _wa_orig_seg10 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg10.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg9,
                                          respaldo_db_path=_resp_seg9)
            chk("control: tramo vivo que arranca fuera de la ventana de poscierre (un "
                "despacho viejo de 5h no es la misma conversacion) sigue insistiendo normal",
                len(_wa_seg10) == 1)
        finally:
            enviar_whatsapp = _wa_orig_seg10
        # Caso 157 (curador, 10-oct-2026): cliente que pidio hace una semana saluda, el bot
        # le pregunta delivery o retiro y el cliente no contesta. El recordatorio va a los
        # 10 min, no a la hora (antes cualquier pedido viejo lo atrasaba a 60 min).
        _cs9 = _con_conv_en(_conv_seg9)
        _tramo157 = (datetime.now() - timedelta(minutes=11)).isoformat(timespec="seconds")
        _cs9.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56934040157", "usuario", "Como 3stas tu", _tramo157))
        _cs9.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56934040157", "asistente",
                      "Estoy para ayudarte con tu pedido. ¿Quieres hacer delivery o retiro?", _tramo157))
        _cs9.commit(); _cs9.close()
        _guardar_ultimo_pedido("56934040157", "1x Churrasco Italiano · retiro · tarjeta",
                                db_path=_resp_seg9)
        _resp9 = _con_respaldo(_resp_seg9)
        _resp9.execute("UPDATE ultimo_pedido_cliente SET creado_en=? WHERE telefono=?",
                        ((datetime.now() - timedelta(days=7)).isoformat(timespec="seconds"),
                         _norm_telefono("56934040157")))
        _resp9.commit(); _resp9.close()
        _wa157, _wa_orig157 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa157.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg9,
                                          respaldo_db_path=_resp_seg9)
            chk("caso 157: con un pedido de hace una semana el recordatorio sale a los 10 min, "
                "no a la hora", "56934040157" in _wa157)
        finally:
            enviar_whatsapp = _wa_orig157

    print("== 01-oct-2026: bug real del curador -- el cliente declino el PRIMER "
          "'sigues ahi' ('No gracias') y aun asi, 10 min despues, le llego un SEGUNDO ==")
    with carpeta_temporal() as tmp_seg11:
        _conv_seg11 = Path(tmp_seg11) / "conv_seg11.db"
        _viejo_seg11 = (datetime.now() - timedelta(minutes=15)).isoformat(timespec="seconds")
        # Caso real: sin chacarero hoy, se manda el primer 'sigues ahi'; el cliente
        # contesta "No gracias" / "Para la otra pedire" -- declino, no quedo a medias
        # -- pero cada respuesta suya abre un turno NUEVO, y el viejo candado (que solo
        # miraba si el ULTIMO turno ERA el recordatorio) dejaba de verlo.
        _cs11 = _con_conv_en(_conv_seg11)
        _cs11.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56994103200", "usuario", "Quiero una promo de chacarera", _viejo_seg11))
        _cs11.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56994103200", "asistente", "Se nos acabo el chacarero por hoy...", _viejo_seg11))
        _cs11.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56994103200", "asistente", MENSAJE_SEGUIMIENTO_SALUDO, _viejo_seg11))
        _cs11.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56994103200", "usuario", "No gracias", _viejo_seg11))
        _cs11.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56994103200", "asistente", "Perfecto, no hay problema. ¡Gracias a ti!",
                      _viejo_seg11))
        _cs11.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56994103200", "usuario", "Para la otra pedire. Gracias!", _viejo_seg11))
        _cs11.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56994103200", "asistente", "¡Gracias a ti!", _viejo_seg11))
        _cs11.commit(); _cs11.close()
        _wa_seg11, _wa_orig_seg11 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg11.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg11)
            chk("el cliente ya declino el primer 'sigues ahi' en este mismo tramo vivo: "
                "NO le llega un segundo, aunque su despedida abra un turno nuevo",
                len(_wa_seg11) == 0)
        finally:
            enviar_whatsapp = _wa_orig_seg11
        # Control negativo: mismo tramo pero SIN que el recordatorio ya haya salido --
        # ahi si tiene que insistir (comportamiento normal que no hay que romper).
        _cs11 = _con_conv_en(_conv_seg11)
        _cs11.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56994103201", "usuario", "Quiero una promo de chacarera", _viejo_seg11))
        _cs11.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56994103201", "asistente", "Se nos acabo el chacarero por hoy...",
                      _viejo_seg11))
        _cs11.commit(); _cs11.close()
        _wa_seg12, _wa_orig_seg12 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg12.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg11)
            chk("control: sin recordatorio previo en el tramo, SI insiste (el 'sigues "
                "ahi' de siempre, una sola vez)",
                len(_wa_seg12) == 1)
        finally:
            enviar_whatsapp = _wa_orig_seg12

    print("== 29-sep-2026: bug real del curador (conversacion 94) -- 'sigues ahi' a un "
          "cliente con la transferencia retenida, esperando a un HUMANO, no al bot ==")
    with carpeta_temporal() as tmp_seg8:
        _conv_seg8 = Path(tmp_seg8) / "conv_seg8.db"
        _resp_seg8 = Path(tmp_seg8) / "respaldo_seg8.db"
        # Caso real: el cliente transfirio y mando el comprobante, quedo RETENIDO
        # esperando que el jefe confirme -- una hora despues, sin que nadie hubiera
        # confirmado, igual le llego "sigues ahi? que te gustaria pedir".
        _guardar_esperando_comprobante({"cliente": "Test94", "items": []}, "56911187777",
                                        db_path=_resp_seg8)
        _viejo_seg8 = (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds")
        _cs8 = _con_conv_en(_conv_seg8)
        _cs8.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911187777", "usuario", "[image recibido]", _viejo_seg8))
        _cs8.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911187777", "asistente", MENSAJE_TRANSFERENCIA_AVISADA, _viejo_seg8))
        _cs8.commit(); _cs8.close()
        _wa_seg8, _wa_orig_seg8 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg8.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg8,
                                          respaldo_db_path=_resp_seg8)
            chk("conversacion 94: con la transferencia retenida (esperando al jefe), "
                "YA NO insiste con 'sigues ahi', sin importar cuanto lleve esperando",
                len(_wa_seg8) == 0)
        finally:
            enviar_whatsapp = _wa_orig_seg8

    print("== 28-sep-2026: bug real del curador (caso 86) -- 'sigues ahi' sobre un turno "
          "caducado (el fijo de 'estamos cerrados'), horas despues, cuando el local reabre ==")
    with carpeta_temporal() as tmp_seg5:
        _conv_seg5 = Path(tmp_seg5) / "conv_seg5.db"
        # Igual que el caso real: el cliente saludo con el local cerrado, recibio el
        # mensaje fijo de horario, y nadie volvio a escribir en HORAS_CADUCIDAD_HISTORIAL
        # + horas -- ese turno nunca se archivo porque no hubo despacho.
        _viejo_seg5 = (datetime.now()
                       - timedelta(hours=HORAS_CADUCIDAD_HISTORIAL + 1)).isoformat(timespec="seconds")
        _cs5 = _con_conv_en(_conv_seg5)
        _cs5.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911183333", "usuario", "Hola estaran atendiendo", _viejo_seg5))
        _cs5.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911183333", "asistente", "Hola, estamos cerrados...", _viejo_seg5))
        _cs5.commit(); _cs5.close()
        _wa_seg5, _wa_orig_seg5 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg5.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg5)
            chk("caso 86: un turno caducado (>6h) NO dispara 'sigues ahi', aunque siga vivo",
                len(_wa_seg5) == 0)
            chk("...y de paso queda archivado (no se re-evalua cada ciclo)",
                historial("56911183333", db_path=_conv_seg5) == [])
        finally:
            enviar_whatsapp = _wa_orig_seg5
        # Control negativo: el MISMO turno, pero DENTRO de las 6 h, si tiene que insistir
        # (es el comportamiento normal que no hay que romper).
        _reciente_seg5 = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
        _cs5 = _con_conv_en(_conv_seg5)
        _cs5.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911184444", "usuario", "Hola, tienen papas?", _reciente_seg5))
        _cs5.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                     ("56911184444", "asistente", "Si, tenemos...", _reciente_seg5))
        _cs5.commit(); _cs5.close()
        _wa_seg6, _wa_orig_seg6 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, t, c, intentos=2: _wa_seg6.append(n) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg5)
            chk("control negativo: un turno reciente (30 min) SI sigue disparando el aviso",
                len(_wa_seg6) == 1)
        finally:
            enviar_whatsapp = _wa_orig_seg6

    print("== 26-sep-2026: la bienvenida despues del mensaje de CERRADO no se desarma ==")
    _cerrado = _proto.mensaje_cerrado()
    chk("el mensaje fijo de cerrado NO cuenta como saludo",
        not _proto.ya_saludo([{"rol": "usuario", "texto": "Hola!!"},
                              {"rol": "asistente", "texto": _cerrado}], False))
    chk("el 'cerrado por decision del dueño' (el del 25-sep) tampoco cuenta como saludo",
        not _proto.ya_saludo([{"rol": "asistente", "texto": _proto.MENSAJE_CERRADO_POR_JEFE}], False))
    with carpeta_temporal() as tmp_nc:
        _conv_nc = Path(tmp_nc) / "conv_nc.db"
        guardar_turno("56954590000", "usuario", "Hola", db_path=_conv_nc)
        guardar_turno("56954590000", "asistente", _proto.MENSAJE_CERRADO_POR_JEFE, db_path=_conv_nc)
        _cer_nc = _CerebroFalso(["¡Perdón! Estuvimos cerrados un rato, pero ya estamos atendiendo."
                                 "\n\n¿Qué te gustaría pedir?"])
        _v_nc = procesar_mensaje("56954590000", "Pensé q estaba cerrado", {**CONFIG_DEFAULT},
                                 cerebro=_cer_nc, nucleo=NucleoPedidos(db_path=Path(tmp_nc) / "p.db"),
                                 conv_db=_conv_nc)
        chk("caso 153 integracion: al reabrir, el modelo ya no ve el mensaje de 'cerrado'",
            not any(_proto.es_mensaje_de_cerrado(m.get("texto"))
                    for m in (_cer_nc.ultimos_mensajes or []) if m.get("rol") == "asistente"))
        chk("...y si igual se disculpa, al cliente no le llega la disculpa",
            "cerrados" not in (_v_nc or "") and "Qué te gustaría pedir" in (_v_nc or ""))
    chk("el mensaje de CORTE (ya cerramos por hoy) tampoco cuenta como saludo",
        not _proto.ya_saludo([{"rol": "asistente", "texto": _proto.mensaje_corte_pedidos()}], False))
    chk("un pedido ya confirmado cuenta como conversacion en curso (caso 15-ago)",
        _proto.ya_saludo([{"rol": "asistente", "texto": "Confirmado. Demora 10 min aprox"}], False))
    chk("una bienvenida de verdad SI cuenta",
        _proto.ya_saludo([{"rol": "asistente", "texto": "Hola! Soy Patata bot 🤖. Bienvenid@ a "
                                                         "Patata Huechuraba 🌭🍟"}], False))
    chk("al reabrir la conversacion (silencio largo) se vuelve a saludar",
        not _proto.ya_saludo([{"rol": "asistente", "texto": "Hola! Soy Patata bot 🤖"}], True))
    with carpeta_temporal() as tmp_cerr:
        _conv_cerr = Path(tmp_cerr) / "conv_cerr.db"
        guardar_turno("56900001157", "usuario", "Hola!!", db_path=_conv_cerr)
        guardar_turno("56900001157", "asistente", _cerrado, db_path=_conv_cerr)
        _bienv = ("Hola! Soy Patata bot 🤖. Bienvenid@ a Patata Huechuraba 🌭🍟\n\n"
                  "Hoy estamos con un tiempo aproximado de 30 min para delivery y 10 min "
                  "para retiro.\n\nDinos;\n•Es delivery o retiro?:\n"
                  "•Direccion (si es Delivery):\n•Nombre: Marilí ✅\n•Pedido:\n•Forma de pago:")
        _v_cerr = procesar_mensaje("56900001157", "Hola!!", {**CONFIG_DEFAULT},
                                   cerebro=_CerebroFalso([_bienv]),
                                   nucleo=NucleoPedidos(db_path=Path(tmp_cerr) / "p.db"),
                                   conv_db=_conv_cerr)
        chk("caso real 25-sep 12:01: a los 4 min del 'cerrado' la bienvenida llega COMPLETA",
            "Soy Patata bot" in _v_cerr and "tiempo aproximado" in _v_cerr and "Dinos;" in _v_cerr)
        _v_cerr2 = procesar_mensaje("56900001157", "ok", {**CONFIG_DEFAULT},
                                    cerebro=_CerebroFalso([_bienv]),
                                    nucleo=NucleoPedidos(db_path=Path(tmp_cerr) / "p.db"),
                                    conv_db=_conv_cerr)
        chk("...y si el modelo la repite en el turno siguiente, ahi SI se saca",
            "Soy Patata bot" not in _v_cerr2 and "tiempo aproximado" not in _v_cerr2)

    with carpeta_temporal() as tmp_9bis:
        conv_9bis = Path(tmp_9bis) / "conv_9bis.db"
        nucleo_9bis = NucleoPedidos(db_path=Path(tmp_9bis) / "p_9bis.db")
        # 1a llamada: el modelo responde el resumen a mano, sin tool. 2a (el rescate):
        # reconoce que era el Paso 1 y devuelve previsualizar_pedido.
        _rescate_prev = ("<PEDIDO_JSON>\n" +
                         json.dumps({**_prev_delivery, "_tool": "previsualizar_pedido"}) +
                         "\n</PEDIDO_JSON>")
        vis_9bis = procesar_mensaje(
            "56966660000", "1 churrasco italiano a cancion de la pampa 5727, tarjeta",
            {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([_resumen_a_mano, _rescate_prev]),
            nucleo=nucleo_9bis, conv_db=conv_9bis)
        chk("el cliente ve el total REAL aunque el modelo no haya llamado a la tool",
            "Total con delivery $6.400." in vis_9bis)
        chk("...y ese Paso 1 rescatado NO manda nada a cocina", len(nucleo_9bis.cola()) == 0)

    print("== 05-ago-2026 (pendiente #9): candado de PEDIDO DUPLICADO ==")
    chk("la firma de un pedido es estable (la misma que se guarda y la que se compara)",
        _resumen_de_pedido({"items": [{"cantidad": 2, "producto": "Churrasco Italiano"}],
                            "modalidad": "retiro", "forma_pago": "tarjeta"})
        == "2x Churrasco Italiano · retiro · tarjeta")
    with carpeta_temporal() as tmp_dup:
        _respaldo_orig_dup = RESPALDO_DB
        RESPALDO_DB = Path(tmp_dup) / "respaldo_dup.db"
        _wa_orig_dup, _wa_dup = enviar_whatsapp, []

        def _espia_dup(telefono, texto, cfg, intentos=2):
            _wa_dup.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia_dup
        try:
            conv_dup = Path(tmp_dup) / "conv_dup.db"
            nucleo_dup = NucleoPedidos(db_path=Path(tmp_dup) / "p_dup.db")
            procesar_mensaje("56977770000", "2 churrascos italianos retiro tarjeta",
                             {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([respuesta_cerrada]),
                             nucleo=nucleo_dup, conv_db=conv_dup)
            chk("el primer pedido entra a cocina normal", len(nucleo_dup.cola()) == 1)
            # El caso real: el cliente vuelve a escribir "si, confirmo" y el modelo
            # reemite el MISMO pedido.
            _wa_dup.clear()
            vis_dup = procesar_mensaje("56977770000", "si, confirmo", {**CONFIG_DEFAULT},
                                       cerebro=_CerebroFalso([respuesta_cerrada]),
                                       nucleo=nucleo_dup, conv_db=conv_dup)
            chk("el MISMO pedido reemitido NO entra una segunda vez a cocina",
                len(nucleo_dup.cola()) == 1)
            chk("...y al cliente se le dice que ya esta en preparacion",
                MENSAJE_PEDIDO_YA_TOMADO in vis_dup)
            chk("...sin molestar a los jefes (no hay nada que rescatar a mano)",
                not [t for _, t in _wa_dup if "SEGUNDO PEDIDO" in t])
            # Pedido DISTINTO dentro de la ventana: tampoco entra solo, pero se avisa.
            _wa_dup.clear()
            resp_otro = respuesta_cerrada.replace('"cantidad":2', '"cantidad":1')
            vis_otro = procesar_mensaje("56977770000", "agregame una bebida", {**CONFIG_DEFAULT},
                                        cerebro=_CerebroFalso([resp_otro]),
                                        nucleo=nucleo_dup, conv_db=conv_dup)
            chk("un pedido DISTINTO dentro de la ventana tampoco entra solo a cocina",
                len(nucleo_dup.cola()) == 1)
            chk("...el cliente sabe que lo toma una persona",
                MENSAJE_SEGUNDO_PEDIDO_A_HUMANO in vis_otro)
            chk("...y los jefes reciben el detalle para no perder la venta",
                len([t for _, t in _wa_dup if "SEGUNDO PEDIDO" in t]) == len(NUMS_ALERTAS_JEFE))
            _wa_dup.clear()
            procesar_mensaje("56977770000", "si, esa bebida", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso([resp_otro]),
                             nucleo=nucleo_dup, conv_db=conv_dup)
            chk("...pero ese aviso NO se repite en cada mensaje (anti-spam, como el "
                "pedido grande)", not [t for _, t in _wa_dup if "SEGUNDO PEDIDO" in t])
            # Pasada la ventana, el candado se abre solo: el cliente puede volver a pedir.
            con_dup = _con_respaldo(RESPALDO_DB)
            con_dup.execute(
                "UPDATE ultimo_pedido_cliente SET creado_en=? WHERE telefono=?",
                ((datetime.now() - timedelta(minutes=MINUTOS_BLOQUEO_PEDIDO_DUPLICADO + 5))
                 .isoformat(timespec="seconds"), _norm_telefono("56977770000")))
            con_dup.commit()
            con_dup.close()
            chk(f"pasados los {MINUTOS_BLOQUEO_PEDIDO_DUPLICADO} min el candado se abre solo",
                _pedido_despachado_hace_poco("56977770000") is None)
            vis_tarde = procesar_mensaje("56977770000", "quiero pedir de nuevo",
                                         {**CONFIG_DEFAULT},
                                         cerebro=_CerebroFalso([respuesta_cerrada]),
                                         nucleo=nucleo_dup, conv_db=conv_dup)
            chk("...y un pedido posterior SI vuelve a entrar a cocina",
                len(nucleo_dup.cola()) == 2
                and MENSAJE_PEDIDO_YA_TOMADO not in vis_tarde)
        finally:
            RESPALDO_DB = _respaldo_orig_dup
            enviar_whatsapp = _wa_orig_dup

    print("== 02-oct-2026 (casos 109/110 del curador): cierre de cortesia tras un "
          "pedido ya despachado ==")
    chk("'Yap, no hay problema' es puro cierre/cortesia",
        _es_solo_cortesia("Yap, no hay problema"))
    chk("'gracias' solo es cortesia", _es_solo_cortesia("Gracias!"))
    chk("control: 'solo eso' NO es puro cierre (confirma un cambio real)",
        not _es_solo_cortesia("Muchas gracias solo eso"))
    chk("control: 'si, confirmo' NO es cortesia (reconfirma de palabra, caso original #9)",
        not _es_solo_cortesia("si, confirmo"))
    with carpeta_temporal() as tmp_dup2:
        _respaldo_orig_dup2 = RESPALDO_DB
        RESPALDO_DB = Path(tmp_dup2) / "respaldo_dup2.db"
        _wa_orig_dup2, _wa_dup2 = enviar_whatsapp, []

        def _espia_dup2(telefono, texto, cfg, intentos=2):
            _wa_dup2.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia_dup2
        try:
            conv_dup2 = Path(tmp_dup2) / "conv_dup2.db"
            nucleo_dup2 = NucleoPedidos(db_path=Path(tmp_dup2) / "p_dup2.db")
            procesar_mensaje("56977771111", "2 churrascos italianos retiro tarjeta",
                             {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([respuesta_cerrada]),
                             nucleo=nucleo_dup2, conv_db=conv_dup2)
            # Caso 109: el modelo reemite el MISMO pedido ante un "Yap, no hay problema"
            # -- el cliente no pidio nada, solo se estaba despidiendo (regla 3.10a).
            vis_cortesia = procesar_mensaje(
                "56977771111", "Yap, no hay problema", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso([respuesta_cerrada]),
                nucleo=nucleo_dup2, conv_db=conv_dup2)
            chk("el MISMO pedido reemitido por cortesia tampoco entra dos veces",
                len(nucleo_dup2.cola()) == 1)
            chk("...pero la respuesta es un acuse, no 'ya lo tengo tomado' ni 'gracias a ti' "
                "(caso 170: a un 'ok' no se le agradece)",
                vis_cortesia == MENSAJE_ACUSE_DUPLICADO)
            # Caso 110: agrega un item (pedido DISTINTO) y confirma con cortesia. El
            # mensaje al cliente tiene que decir que es EL MISMO pedido, nunca sugerir
            # que le llegaran dos.
            resp_otro2 = respuesta_cerrada.replace('"cantidad":2', '"cantidad":1')
            vis_otro2 = procesar_mensaje(
                "56977771111", "Muchas gracias solo eso", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso([resp_otro2]),
                nucleo=nucleo_dup2, conv_db=conv_dup2)
            chk("el agregado tampoco entra solo a cocina", len(nucleo_dup2.cola()) == 1)
            chk("...y el mensaje aclara que se suma al MISMO pedido (caso 110: antes "
                "sonaba a un segundo pedido aparte)",
                MENSAJE_SEGUNDO_PEDIDO_A_HUMANO in vis_otro2
                and "ESE MISMO pedido" in vis_otro2
                and "no te llega aparte" in vis_otro2)
        finally:
            RESPALDO_DB = _respaldo_orig_dup2
            enviar_whatsapp = _wa_orig_dup2

    print("== 15-oct-2026 (caso 170 del curador): pedido cerrado no se reabre con una "
          "pregunta; a un 'Oki' se contesta 'Perfecto' ==")
    chk("'Oki' es acuse", _es_solo_acuse("Oki") and _es_solo_cortesia("Oki"))
    chk("control: 'Gracias!' no es acuse (lleva su 'de nada')", not _es_solo_acuse("Gracias!"))
    chk("control: 'si, confirmo' no es acuse", not _es_solo_acuse("si, confirmo"))
    _preg_170 = ("Perfecto, transferencia a nombre de Ulloa Y Silva Spa.\n\nAvísame por acá "
                 "apenas la hagas. ¿Quieres confirmar el pedido así o quieres agregar o "
                 "modificar algo?")
    with carpeta_temporal() as tmp_170:
        _respaldo_orig_170 = RESPALDO_DB
        RESPALDO_DB = Path(tmp_170) / "respaldo_170.db"
        _wa_orig_170 = enviar_whatsapp
        enviar_whatsapp = lambda telefono, texto, cfg, intentos=2: {"simulado": True}
        try:
            conv_170 = Path(tmp_170) / "conv_170.db"
            nucleo_170 = NucleoPedidos(db_path=Path(tmp_170) / "p_170.db")
            vis_antes = procesar_mensaje(
                "56977772222", "Cual es la cuenta para transferir", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso([_preg_170]), nucleo=nucleo_170, conv_db=conv_170)
            chk("control: sin pedido cerrado la pregunta se mantiene",
                "agregar o modificar" in vis_antes)
            procesar_mensaje("56977772222", "2 churrascos italianos retiro tarjeta",
                             {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([respuesta_cerrada]),
                             nucleo=nucleo_170, conv_db=conv_170)
            vis_cuenta = procesar_mensaje(
                "56977772222", "Cual es la cuenta para transferir", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso([_preg_170]), nucleo=nucleo_170, conv_db=conv_170)
            chk("con el pedido cerrado la pregunta que lo reabre no sale",
                "agregar" not in vis_cuenta and "confirmar el pedido" not in vis_cuenta
                and "apenas la hagas." in vis_cuenta)
            chk("...y el historial guarda lo que el cliente leyo (sin la pregunta)",
                "confirmar el pedido" not in historial("56977772222", db_path=conv_170)[-1]["texto"])
            vis_oki = procesar_mensaje(
                "56977772222", "Oki", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso([respuesta_cerrada]), nucleo=nucleo_170, conv_db=conv_170)
            chk("'Oki' con el mismo pedido reemitido: no entra dos veces",
                len(nucleo_170.cola()) == 1)
            chk("...y se contesta 'Perfecto', sin emoji", vis_oki == "Perfecto")
            vis_gracias = procesar_mensaje(
                "56977772222", "Gracias!", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso([respuesta_cerrada]), nucleo=nucleo_170, conv_db=conv_170)
            chk("'Perfecto' cuenta como despedida: un 'Gracias!' encima no se contesta (caso 133)",
                vis_gracias is None)
            # Otro cliente: a un 'Gracias!' con el pedido reemitido se mantiene el de nada.
            procesar_mensaje("56977773333", "2 churrascos italianos retiro tarjeta",
                             {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([respuesta_cerrada]),
                             nucleo=nucleo_170, conv_db=conv_170)
            procesar_mensaje("56977773333", "a que hora cierran", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso(["Cerramos a las 23:00."]),
                             nucleo=nucleo_170, conv_db=conv_170)
            vis_gr2 = procesar_mensaje(
                "56977773333", "Gracias!", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso([respuesta_cerrada]), nucleo=nucleo_170, conv_db=conv_170)
            chk("a un 'Gracias!' se mantiene el de nada", vis_gr2 == MENSAJE_CIERRE_CORTESIA_DUPLICADO)
            # Cortesia justo despues del cierre (candado del caso 138): 'Ok' -> 'Perfecto'.
            _cierre_conf = respuesta_cerrada.replace(
                "Perfecto, quedan 2 Churrasco Italiano, retiro, pagas con tarjeta. "
                "Total $8.900, se confirma enseguida!", "Confirmado. Demora 10 min aprox, ¡gracias!")
            procesar_mensaje("56977774444", "2 churrascos italianos retiro tarjeta",
                             {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([_cierre_conf]),
                             nucleo=nucleo_170, conv_db=conv_170)
            vis_ok_cierre = procesar_mensaje(
                "56977774444", "Ok", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso(["no deberia llamarse"]), nucleo=nucleo_170, conv_db=conv_170)
            chk("'Ok' justo despues del cierre: 'Perfecto', no '¡Gracias a ti!'",
                vis_ok_cierre == "Perfecto")
            # El modelo le agradece un 'Oki' por su cuenta: se cambia por 'Perfecto'.
            procesar_mensaje("56977775555", "a que hora cierran", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso(["Cerramos a las 23:00."]),
                             nucleo=nucleo_170, conv_db=conv_170)
            vis_mod = procesar_mensaje(
                "56977775555", "Oki", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso(["¡Gracias a ti!"]), nucleo=nucleo_170, conv_db=conv_170)
            chk("si el modelo le agradece un 'Oki', sale 'Perfecto'", vis_mod == "Perfecto")
            vis_mod2 = procesar_mensaje(
                "56977775555", "a que hora abren", {**CONFIG_DEFAULT},
                cerebro=_CerebroFalso(["¡Gracias a ti!"]), nucleo=nucleo_170, conv_db=conv_170)
            chk("control: si no fue un 'ok', el texto del modelo no se toca",
                vis_mod2 == "¡Gracias a ti!")
        finally:
            RESPALDO_DB = _respaldo_orig_170
            enviar_whatsapp = _wa_orig_170

    print("== 07-oct-2026 (casos 133/125/127/123/118 del curador): cortesia repetida, seguimiento "
          "tras despedida, texto del seguimiento, base del Especial y link de la carta ==")
    chk("'Gracias usted' es cortesia (caso 133)", _es_solo_cortesia("Gracias usted"))
    chk("control: 'gracias, quiero otra cosa' NO es cortesia pura",
        not _es_solo_cortesia("gracias, quiero otra cosa"))
    chk("'¡Gracias a ti!' es la despedida del bot", _es_despedida_del_bot("¡Gracias a ti!"))
    chk("'Perfecto, no hay problema. ¡Gracias a ti!' tambien",
        _es_despedida_del_bot("Perfecto, no hay problema. ¡Gracias a ti!"))
    chk("control: 'Confirmado. Demora 10 min aprox, ¡gracias!' NO es despedida",
        not _es_despedida_del_bot("Confirmado. Demora 10 min aprox, ¡gracias!"))
    chk("control: un mensaje largo que diga 'gracias a ti' NO es despedida",
        not _es_despedida_del_bot("Gracias a ti por escribir. " + "Tenemos churrascos italianos. " * 4))
    chk("el seguimiento dice 'Quedamos atentos.' (Christian, caso 127)",
        MENSAJE_SEGUIMIENTO_SALUDO == "Quedamos atentos.")
    with carpeta_temporal() as tmp_cor:
        conv_cor = Path(tmp_cor) / "conv_cor.db"
        _wa_orig_cor, _wa_cor = enviar_whatsapp, []
        enviar_whatsapp = lambda n, tx, c, intentos=2: _wa_cor.append(tx) or {"simulado": True}  # noqa: E731
        try:
            for rol_, tx_ in (("usuario", "Hola"), ("asistente", "Hola! Soy Patata bot"),
                              ("usuario", "Retiro papas medianas efectivo"),
                              ("asistente", "Confirmado. Demora 10 min aprox, ¡gracias!"),
                              ("usuario", "Ya"), ("asistente", "¡Gracias a ti!")):
                guardar_turno("56933330133", rol_, tx_, db_path=conv_cor)
            cer_cor = _CerebroFalso(["¡Gracias a ti!"])
            vis_cor = procesar_mensaje("56933330133", "Gracias usted", {**CONFIG_DEFAULT},
                                       cerebro=cer_cor, conv_db=conv_cor)
            chk("caso 133: devolver la cortesia tras la despedida NO se contesta",
                vis_cor is None and cer_cor.llamadas == 0 and _wa_cor == [])
            chk("...pero el turno del cliente queda guardado (el curador lo ve)",
                historial("56933330133", db_path=conv_cor)[-1]["texto"] == "Gracias usted")
            cer_cor2 = _CerebroFalso(["Claro, ¿qué te gustaría pedir?"])
            vis_cor2 = procesar_mensaje("56933330133", "quiero pedir otra cosa", {**CONFIG_DEFAULT},
                                        cerebro=cer_cor2, conv_db=conv_cor)
            chk("control: un mensaje con contenido real SI se contesta",
                cer_cor2.llamadas == 1 and vis_cor2)
        finally:
            enviar_whatsapp = _wa_orig_cor
    with carpeta_temporal() as tmp_seg125:
        _conv_seg125 = Path(tmp_seg125) / "conv_seg125.db"
        _v125 = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
        _c125 = _con_conv_en(_conv_seg125)
        for tel_, rol_, tx_ in (("56933330125", "usuario", "Buuu yaaa / Gracias"),
                                ("56933330125", "asistente", "¡Gracias a ti!"),
                                ("56933330127", "usuario", "Hasta que hora atienden?"),
                                ("56933330127", "asistente", "Estamos abiertos hasta las 23:59.")):
            _c125.execute("INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                          (tel_, rol_, tx_, _v125))
        _c125.commit(); _c125.close()
        _wa125, _wa_orig125 = [], enviar_whatsapp
        enviar_whatsapp = lambda n, tx, c, intentos=2: _wa125.append((n, tx)) or {"ok": True}  # noqa: E731
        try:
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=_conv_seg125,
                                          respaldo_db_path=Path(tmp_seg125) / "resp125.db")
            chk("caso 125: tras la despedida '¡Gracias a ti!' NO llega seguimiento",
                all(n != "56933330125" for n, _ in _wa125))
            chk("caso 127: quien solo pregunto el horario SI recibe 'Quedamos atentos.'",
                _wa125 == [("56933330127", "Quedamos atentos.")])
        finally:
            enviar_whatsapp = _wa_orig125
    _tar_esp = Tarificador(json.loads(prototipo.menu_json_activo().read_text(encoding="utf-8")))
    _it_esp = _tar_esp.precio_item("Especiales", "Especial Barros Champi", "churrasco", 1)
    chk("caso 123: el Especial Barros Champi se cobra con variante churrasco ($5.600)",
        _it_esp["ok"] and _it_esp["variante"] == "churrasco" and _it_esp["subtotal"] == 5600)
    _it_esp_m = _tar_esp.precio_item("Especiales", "Especial Barros Champi", "mechada", 1)
    chk("...y con variante mechada ($6.200)", _it_esp_m["ok"] and _it_esp_m["subtotal"] == 6200)
    _ficha_esp = _ficha_confirmacion(
        {"modalidad": "retiro", "forma_pago": "transferencia", "nombre": "Test",
         "items": [{"categoria": "Especiales", "producto": "Especial Barros Champi",
                    "variante": "churrasco", "cantidad": 1}]}, "56933330123", {**CONFIG_DEFAULT})
    chk("caso 123: la ficha muestra la base del Especial (churrasco)",
        _ficha_esp is not None and "Especial Barros Champi churrasco ($5.600)" in _ficha_esp)
    # Caso 118 (curador, reabierto 07-oct; candado de codigo 12-oct-2026): ficha ya vista
    # + solo el dato de pago + mismo pedido -> se cierra sin repetir la ficha.
    chk("118: '6000' es solo un dato de pago (efectivo con monto)",
        prototipo.dato_de_pago_solo("6000") == {"forma": "efectivo", "monto": 6000})
    chk("...'tengo justo' tambien (efectivo sin monto)",
        prototipo.dato_de_pago_solo("tengo justo") == {"forma": "efectivo", "monto": None})
    chk("...'tarjeta' tambien", prototipo.dato_de_pago_solo("tarjeta") == {"forma": "tarjeta", "monto": None})
    chk("...'Retiro en el local Pago con tarjeta' NO (trae otro dato)",
        prototipo.dato_de_pago_solo("Retiro en el local Pago con tarjeta") is None)
    chk("...'Ya hice la transferencia' NO (es aviso de pago, otro flujo)",
        prototipo.dato_de_pago_solo("Ya hice la transferencia") is None)
    chk("...'2' NO (puede ser una cantidad)", prototipo.dato_de_pago_solo("2") is None)
    chk("...'Esta bien' NO (eso ya lo cierra el modelo)", prototipo.dato_de_pago_solo("Esta bien") is None)
    chk("...'tarjeta 6000' NO (un vuelto con tarjeta no tiene sentido)",
        prototipo.dato_de_pago_solo("tarjeta 6000") is None)
    _prev118 = {
        "cliente": "", "modalidad": "delivery", "direccion": "Cancion de la Pampa 5727",
        "items": [{"categoria": "Papas Fritas", "producto": "Papas Fritas", "variante": "grande",
                   "cantidad": 1, "precio_unitario": 4000, "subtotal": 4000, "notas": ""}],
        "delivery_cobro": 0, "total": 0, "forma_pago": "efectivo",
        "hora_programada": None, "derivar_humano": False, "comentarios": "",
    }

    def _turno118(prev_segundo, texto_segundo, prev_primero=None, tel="56933330118"):
        """Turno 1: el modelo pide la ficha. Turno 2: el cliente contesta y el modelo
        (como en produccion) vuelve a pedir la ficha. Devuelve (visible2, cola)."""
        with carpeta_temporal() as tmp118:
            conv118 = Path(tmp118) / "conv118.db"
            nucleo118 = NucleoPedidos(db_path=Path(tmp118) / "p118.db")
            _r1 = ("Perfecto.\n<PEDIDO_JSON>\n" + json.dumps(
                {**(prev_primero or _prev118), "_tool": "previsualizar_pedido"}) + "\n</PEDIDO_JSON>")
            _r2 = ("Perfecto.\n<PEDIDO_JSON>\n" + json.dumps(
                {**prev_segundo, "_tool": "previsualizar_pedido"}) + "\n</PEDIDO_JSON>")
            cer = _CerebroFalso([_r1, _r2])
            procesar_mensaje(tel, "papas fritas grande a cancion de la pampa 5727, efectivo",
                             {**CONFIG_DEFAULT}, cerebro=cer, nucleo=nucleo118, conv_db=conv118)
            v2 = procesar_mensaje(tel, texto_segundo, {**CONFIG_DEFAULT}, cerebro=cer,
                                  nucleo=nucleo118, conv_db=conv118)
            return v2, nucleo118.cola()

    _v118, _c118 = _turno118({**_prev118, "comentarios": "Efectivo con 6000 para vuelto"}, "6000")
    chk("caso 118: '6000' tras la ficha CIERRA (Confirmado), no repite la ficha",
        _v118.startswith("Confirmado") and "•Es delivery o retiro?" not in _v118)
    chk("...y el pedido entra a la cola una sola vez", len(_c118) == 1)
    chk("...con el monto del vuelto para el repartidor",
        len(_c118) == 1 and "6000" in json.dumps(_c118[0], ensure_ascii=False).replace(".", ""))
    _v118b, _c118b = _turno118(dict(_prev118), "6000", tel="56933331182")
    chk("...si el modelo no anoto el monto, el codigo lo agrega a la nota",
        len(_c118b) == 1 and "6.000" in json.dumps(_c118b[0], ensure_ascii=False))
    _v118c, _c118c = _turno118(dict(_prev118), "5000", tel="56933331183")
    chk("control: '5000' no alcanza para $5.500 -> NO cierra (lo ve el modelo)",
        len(_c118c) == 0 and "•Es delivery o retiro?" in _v118c)
    _prev118_mas = {**_prev118, "items": [{**_prev118["items"][0], "cantidad": 2,
                                           "subtotal": 8000}]}
    _v118d, _c118d = _turno118(_prev118_mas, "6000", tel="56933331184")
    chk("control: si la ficha nueva trae OTRO pedido, NO cierra",
        len(_c118d) == 0 and "•Es delivery o retiro?" in _v118d)
    _v118e, _c118e = _turno118(dict(_prev118), "y una bebida", tel="56933331185")
    chk("control: si el cliente agrega algo, NO cierra", len(_c118e) == 0)
    _v118f, _c118f = _turno118({**_prev118, "forma_pago": "tarjeta"}, "efectivo", tel="56933331186")
    chk("control: si el modelo anota otra forma de pago que la dicha, NO cierra", len(_c118f) == 0)
    _prev129 = {**_prev118, "modalidad": "retiro", "direccion": None, "forma_pago": None}
    _v129, _c129 = _turno118({**_prev129, "forma_pago": "tarjeta"}, "tarjeta", prev_primero=_prev129,
                             tel="56933330129")
    chk("caso 129: ficha sin pago + 'tarjeta' -> cierra", len(_c129) == 1 and _v129.startswith("Confirmado"))
    chk("caso 118: el link de la carta mal escrito se corrige",
        corregir_link_menu("Si, con delivery. https://linktr.ee/patatahuechaba /\n\nPerfecto")
        == "Si, con delivery. https://linktr.ee/patatahuechuraba /\n\nPerfecto")
    chk("...el correcto queda igual",
        corregir_link_menu("Menu: https://linktr.ee/patatahuechuraba.") == "Menu: https://linktr.ee/patatahuechuraba.")
    chk("...y sin 'https://' tambien", corregir_link_menu("linktr.ee/otra") == "https://linktr.ee/patatahuechuraba")
    chk("...un texto sin link no cambia", corregir_link_menu("Hola, ¿qué te gustaría pedir?") == "Hola, ¿qué te gustaría pedir?")

    print("== 13-oct-2026: casos 130 y 137-151 del curador (notas de Christian del 09-oct) ==")
    _voc13 = prototipo.vocabulario_productos(_menu_activo_dict())
    _saludo13 = ("Hola! Soy Patata bot 🤖. Bienvenid@ a Patata Huechuraba 🌭🍟\n\n"
                 "Hoy estamos con un tiempo aproximado de 30 min para delivery y 10 min para retiro.\n\n"
                 "Dinos;\n•Es delivery o retiro?:\n•Direccion (si es Delivery):\n•Pedido:\n•Forma de pago:")
    _saludo13_aviso = _saludo13.replace("\n\nHoy estamos", "\n\nAviso: hoy nos quedamos sin chacarero.\n\nHoy estamos")
    chk("141: 'Aviso: hoy nos quedamos sin chacarero' sin instruccion = agotado inventado",
        prototipo.agotados_inventados(_saludo13_aviso, [], _voc13) == ["chacarero"])
    chk("...151: 'No tenemos chacarero por hoy' tambien",
        prototipo.agotados_inventados("No tenemos chacarero por hoy, ¿quieres otro?", [], _voc13) == ["chacarero"])
    chk("...99: 'chacarero (no disponible hoy)' tambien",
        prototipo.agotados_inventados("Tenemos italiano, chacarero (no disponible hoy).", [], _voc13) == ["chacarero"])
    chk("control: con 'chacarero agotado' vigente NO es inventado",
        prototipo.agotados_inventados(_saludo13_aviso, ["chacarero"], _voc13) == [])
    chk("control: 'no hay problema' / 'no tenemos delivery' no son productos",
        prototipo.agotados_inventados("No hay problema. Hoy no tenemos delivery, solo retiro.", [], _voc13) == [])
    chk("control: 'no trabajamos pollo asado' / 'no hay pollo asado' (BLOQUE 4) no es un agotado",
        prototipo.agotados_inventados("No hay pollo asado, tenemos pollo crispy.", [], _voc13) == [])
    chk("control: 'no tenemos Coca-Cola' (bebidas) no la mira este candado",
        prototipo.agotados_inventados("No tenemos Coca-Cola 🙏 Tenemos Pepsi Zero.", [], _voc13) == [])
    chk("141: sin instruccion vigente, la linea 'Aviso:' del saludo no sale",
        prototipo.sanear_aviso_sin_instruccion(_saludo13_aviso, False) == _saludo13)
    chk("...con instruccion vigente, si sale",
        prototipo.sanear_aviso_sin_instruccion(_saludo13_aviso, True) == _saludo13_aviso)
    chk("...la plantilla 'Aviso: (lo que dice...)' nunca sale tal cual",
        prototipo.sanear_aviso_sin_instruccion(_saludo13.replace(
            "\n\nHoy estamos", "\n\nAviso: (lo que dice la instruccion vigente, en una linea)\n\nHoy estamos"),
            True) == _saludo13)
    chk("143/144: el jefe escribe 'mechada agotada' (producto antes) y el parser lo lee",
        prototipo.interpretar_instrucciones_estructuradas("- mechada agotada")["agotados"] == ["mechada"])
    chk("...'se agoto la carne mechada' se sigue leyendo igual",
        prototipo.interpretar_instrucciones_estructuradas("- se agoto la carne mechada")["agotados"]
        == ["carne mechada"])
    chk("...y 'solo retiro hoy' no agota nada",
        prototipo.interpretar_instrucciones_estructuradas("- solo retiro hoy")["agotados"] == [])
    chk("99: el respaldo saca solo el parentesis en una lista de sabores",
        prototipo.sanear_agotado_inventado("Tenemos italiano, chacarero (no disponible hoy), tomate/palta.",
                                           ["chacarero"], _voc13) == "Tenemos italiano, chacarero, tomate/palta.")
    chk("137: 'Llamaré a previsualizar tu pedido' no llega al cliente",
        prototipo.sanear_texto_interno("Perfecto.\n\nLlamaré a previsualizar tu pedido con 2 Churrasco "
                                       "Chacarero.") == "Perfecto.")
    chk("140: la disculpa por un cierre que no hubo se saca",
        prototipo.sanear_restriccion_levantada("¡Perdón! Estuvimos cerrados un rato, pero ya estamos "
                                               "atendiendo.\n\n" + _saludo13) == _saludo13)
    chk("153: y tambien despues de un cierre real (sin excepcion)",
        prototipo.sanear_disculpa_cierre("¡Perdón! Estuvimos cerrados un rato.\n\nHola!", True) == "Hola!")
    for _frase153 in ("Ya volvimos a tener chacarero.", "Ya tenemos delivery de nuevo.",
                      "Disculpa la confusión con el cierre.", "Hubo un problema con el sistema.",
                      "Ya no estamos solo retiro."):
        chk(f"153: '{_frase153}' no llega al cliente",
            prototipo.sanear_restriccion_levantada(_frase153 + " ¿Qué te gustaría pedir?")
            == "¿Qué te gustaría pedir?")
    chk("control 153: un mensaje normal no se toca",
        prototipo.sanear_restriccion_levantada("Tenemos chacarero y mechada. ¿Qué te gustaría pedir?")
        == "Tenemos chacarero y mechada. ¿Qué te gustaría pedir?")
    _hist153 = [{"rol": "usuario", "texto": "2 chacareros delivery"},
                {"rol": "asistente", "texto": prototipo.MENSAJE_SOLO_RETIRO},
                {"rol": "asistente", "texto": prototipo.mensaje_agotado("Churrasco Chacarero")},
                {"rol": "asistente", "texto": prototipo.MENSAJE_CERRADO_POR_JEFE}]
    chk("153: levantadas las restricciones, el modelo solo ve lo que pidio el cliente",
        prototipo.sin_restricciones_levantadas(_hist153, {"solo_retiro": False, "agotados": []})
        == _hist153[:1])
    chk("control 153: con 'solo retiro' y 'chacarero agotado' vigentes, esos avisos siguen",
        prototipo.sin_restricciones_levantadas(_hist153, {"solo_retiro": True, "agotados": ["chacarero"]})
        == _hist153[:3])
    chk("140: el bloque 'Dinos;' repetido va una sola vez",
        prototipo.ordenar_bienvenida(_saludo13 + "\nDinos;\n•Es delivery o retiro?:\n•Direccion (si es "
                                     "Delivery):\n•Pedido:\n•Forma de pago:", True).count("Dinos") == 1)

    _orig_instr13 = prototipo.INSTRUCCIONES
    with carpeta_temporal() as tmp13:
        prototipo.INSTRUCCIONES = Path(tmp13) / "instrucciones_13.txt"
        _hoy13 = datetime.now(prototipo.TZ_CHILE).strftime("%Y-%m-%d")
        try:
            def _un_turno13(tel, texto13, respuestas, sembrar=()):
                conv13 = Path(tmp13) / f"conv_{tel}.db"
                for _rol, _txt in sembrar:
                    guardar_turno(tel, _rol, _txt, db_path=conv13)
                cer13 = _CerebroFalso(respuestas)
                v13 = procesar_mensaje(tel, texto13, {**CONFIG_DEFAULT}, cerebro=cer13,
                                       nucleo=NucleoPedidos(db_path=Path(tmp13) / f"p_{tel}.db"),
                                       conv_db=conv13)
                return v13, cer13

            _v141, _c141 = _un_turno13("56933330141", "Hola\nTiene chacarero\n??",
                                       [_saludo13_aviso, _saludo13])
            chk("caso 141: sin instruccion, el aviso inventado se reintenta y no llega",
                "sin chacarero" not in _v141 and "Soy Patata bot" in _v141 and _c141.llamadas == 2)
            _malo151 = "No tenemos chacarero por hoy, ¿quieres probar otro sanguche?\n\n" + _saludo13
            _v141b, _c141b = _un_turno13("56933331141", "Quiero dos chacarero\n1 con aji",
                                         [_malo151, _malo151])
            chk("...151: si el modelo insiste, el saneo saca la frase igual",
                "no tenemos chacarero" not in _v141b.lower() and "Soy Patata bot" in _v141b)
            _v141d, _ = _un_turno13("56933333141", "Hola", [_saludo13.replace(
                "\n\nHoy estamos", "\n\nAviso: hoy estamos con mucha demanda.\n\nHoy estamos")])
            chk("...sin instruccion, ningun 'Aviso:' inventado llega al cliente (llega el saludo)",
                "Aviso" not in _v141d and "Soy Patata bot" in _v141d)
            prototipo.INSTRUCCIONES.write_text(f"FECHA: {_hoy13}\nchacarero agotado\n", encoding="utf-8")
            _v141c, _c141c = _un_turno13("56933332141", "Hola\nTiene chacarero\n??", [_saludo13_aviso])
            chk("control: con 'chacarero agotado' vigente el aviso SI llega (una llamada)",
                "sin chacarero" in _v141c and _c141c.llamadas == 1)
            prototipo.INSTRUCCIONES.write_text(f"FECHA: {_hoy13}\n", encoding="utf-8")

            _v140, _ = _un_turno13("56933330140", "Holaa\nHasta que hora atienden hoy",
                                   ["¡Perdón! Estuvimos cerrados un rato, pero ya estamos atendiendo.\n\n"
                                    + _saludo13])
            chk("caso 140: saludo sin la disculpa por un cierre que no hubo",
                "cerrados un rato" not in _v140 and "Soy Patata bot" in _v140)

            _v138, _c138 = _un_turno13(
                "56933330138", "A ustedes",
                ["¿Me confirmas si te lo agrego al pedido que acabas de hacer o es uno aparte?"],
                sembrar=[("usuario", "Esta bien gracias"),
                         ("asistente", "Confirmado. Demora 30 min aprox, ¡gracias!")])
            chk("caso 138: 'A ustedes' tras el Confirmado = despedida fija, sin llamar al modelo",
                _v138 == "¡Gracias a ti!" and _c138.llamadas == 0)

            _prev144 = {"cliente": "", "modalidad": "delivery", "direccion": "Papa Juan XXIII 962",
                        "items": [{"categoria": "Churrascos", "producto": "Churrasco Dinamico",
                                   "variante": None, "cantidad": 1, "notas": ""}],
                        "delivery_cobro": 0, "total": 0, "forma_pago": "tarjeta",
                        "hora_programada": None, "derivar_humano": False, "comentarios": ""}
            _v144, _ = _un_turno13(
                "56933330144", "Papa Juan XXIII 962\nCompleto dinámico pequeña\nPago con crédito",
                ["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps({**_prev144, "_tool": "previsualizar_pedido"})
                 + "\n</PEDIDO_JSON>"])
            chk("caso 144: 'Completo dinámico' sale en la ficha como Vienesa Dinamico, no Churrasco",
                "Vienesa Dinamico" in _v144 and "Churrasco Dinamico" not in _v144)

            _prev137 = {**_prev144, "direccion": "Fabian Oyanedel 5582", "forma_pago": "efectivo",
                        "items": [{"categoria": "Churrascos", "producto": "Churrasco Chacarero",
                                   "variante": None, "cantidad": 3, "notas": "1 sin aji"}]}
            _v137, _ = _un_turno13(
                "56933330137", "Delivery\nFabian oyanedel 5582\n1promo 2x1 chacarero 1 sin aji\nEfectivo",
                ["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps({**_prev137, "_tool": "previsualizar_pedido"})
                 + "\n</PEDIDO_JSON>"])
            chk("caso 137: '1 promo 2x1 chacarero' = 2 Churrasco Chacarero en la ficha, no 3",
                "2 Churrasco Chacarero" in _v137 and "3 Churrasco Chacarero" not in _v137)

            # Casos 166 y 168 (curador, 13-oct-2026): promo pedida sin nombrar producto.
            _ficha166 = ("•Es delivery o retiro?: Delivery\n•Dirección (si es Delivery): Reinaldo prado "
                         "5361 ($1.500)\n•Pedido:\n   - 1 Churrasco Dinamico ($5.200)\n   - 1 Churrasco "
                         "Chacarero ($5.200)\n•Forma de pago: Tarjeta\n\nTotal con delivery $11.900.\n\n"
                         "¿Está bien así, o quieres agregar/modificar algo?")
            _sembrar166 = [("usuario", "Delivery Reinaldo prado 5361 1 churrasco dinámico 1 churrasco "
                                       "chacarero Tarjeta"), ("asistente", _ficha166)]
            _ped166 = {**_prev144, "direccion": "Reinaldo prado 5361", "items": [
                {"categoria": "Churrascos", "producto": "Churrasco Dinamico", "variante": None,
                 "cantidad": 2, "notas": ""},
                {"categoria": "Churrascos", "producto": "Churrasco Chacarero", "variante": None,
                 "cantidad": 2, "notas": "con aji"}]}
            _v166, _ = _un_turno13(
                "56933330166", "Quiero la promoción",
                ["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps({**_ped166, "_tool": "previsualizar_pedido"})
                 + "\n</PEDIDO_JSON>"], sembrar=_sembrar166)
            chk("caso 166: 'Quiero la promoción' sin producto no duplica el pedido",
                _v166 == prototipo.MENSAJE_PROMO_SIN_PRODUCTO)
            _v166b, _ = _un_turno13(
                "56933331166", "2x 9200",
                ["<PEDIDO_JSON>\n" + json.dumps({**_ped166, "_tool": "emitir_pedido"}) + "\n</PEDIDO_JSON>"],
                sembrar=_sembrar166)
            chk("...y '2x 9200' no cuenta como el si: no cierra",
                _v166b == prototipo.MENSAJE_PROMO_SIN_PRODUCTO and "Confirmado" not in _v166b)
            _v168, _ = _un_turno13(
                "56933330168", "disculpa los churrascos no entran 2 x 8900 y solo uno quedaría fuera "
                               "de la promoción?",
                ["Perfecto. 3 churrascos (Barros Luco, Italiano y Dinámico). ¿Está bien así?"],
                sembrar=_sembrar166)
            chk("caso 168: '2 x 8900' con 'los churrascos' (sin decir cual) no rearma el pedido",
                _v168 == prototipo.MENSAJE_PROMO_SIN_PRODUCTO)
            _v166c, _ = _un_turno13(
                "56933332166", "Son 2 promos",
                ["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps({**_ped166, "_tool": "previsualizar_pedido"})
                 + "\n</PEDIDO_JSON>"], sembrar=_sembrar166)
            chk("control: 'Son 2 promos' (dice cuantas) sigue al modelo",
                "2 Churrasco Dinamico" in _v166c)
            _v166d, _ = _un_turno13(
                "56933333166", "Quiero la promo de chacarero",
                ["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps({**_ped166, "_tool": "previsualizar_pedido"})
                 + "\n</PEDIDO_JSON>"], sembrar=_sembrar166)
            chk("control: la promo CON producto nombrado sigue al modelo",
                "2 Churrasco Chacarero" in _v166d)
            _v166e, _ = _un_turno13(
                "56933334166", "Quiero la promoción", ["¿De qué producto quieres la promo?"],
                sembrar=[("usuario", "Hola")])
            chk("control: sin ficha vista no actua", _v166e != prototipo.MENSAJE_PROMO_SIN_PRODUCTO)
            chk("'chacarera' nombra al chacarero",
                not prototipo.pide_promo_sin_producto(
                    "Quiero una promo de chacarera",
                    prototipo.palabras_que_nombran_producto(_menu_activo_dict())))

            _, _c130 = _un_turno13(
                "56933330130", "Completo", ["Perfecto, una Vienesa Italiano. ¿Normal o XL?"],
                sembrar=[("usuario", "3 barros luco\n1 churrasco italiano\n1 italiano"),
                         ("asistente", "Perfecto, ¿quieres que los italianos sean vienesas o as?")])
            chk("caso 130: 'Completo' como respuesta a '¿vienesa o as?' lleva la nota de base Vienesa",
                any("responde la BASE" in (m.get("texto") or "") for m in _c130.ultimos_mensajes))

            # Caso 160 (curador, 11-oct-2026): "No solo eso" y el modelo cerro directo, sin
            # ficha, sin forma de pago y sin preguntar el aji del chacarero.
            _ped160 = {**_prev144, "direccion": "Cerro el Carbon 913", "forma_pago": None,
                       "items": [{"categoria": "Churrascos", "producto": "Churrasco Chacarero",
                                  "variante": None, "cantidad": 1, "notas": ""}]}
            _sem160 = [("usuario", "Delivery"), ("asistente", "Perfecto, delivery."),
                       ("usuario", "Cerro el carbón 913"), ("asistente", "¿Qué te gustaría pedir?"),
                       ("usuario", "Chacarero"), ("asistente", "¿En base a qué: Vienesa, As o Churrasco?"),
                       ("usuario", "Churrasco"), ("asistente", "¿Quieres agregar algo más?")]
            _v160, _ = _un_turno13(
                "56933330160", "No solo eso",
                ["Confirmado. Demora 30 min aprox, ¡gracias!\n<PEDIDO_JSON>\n"
                 + json.dumps({**_ped160, "_tool": "emitir_pedido"}) + "\n</PEDIDO_JSON>"],
                sembrar=_sem160)
            chk("caso 160: cierre sin ficha y con chacarero sin aji -> se pregunta el aji, no va a cocina",
                _v160 == prototipo.pregunta_aji(_ped160)
                and not NucleoPedidos(db_path=Path(tmp13) / "p_56933330160.db").cola())
            _ped160b = {**_ped160, "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                                              "variante": None, "cantidad": 1, "notas": ""}]}
            _v160b, _ = _un_turno13(
                "56933331160", "No solo eso",
                ["Confirmado. Demora 30 min aprox, ¡gracias!\n<PEDIDO_JSON>\n"
                 + json.dumps({**_ped160b, "_tool": "emitir_pedido"}) + "\n</PEDIDO_JSON>"],
                sembrar=_sem160)
            chk("...160: sin ficha y sin forma de pago -> sale la ficha (sin el 'Confirmado'), no va a cocina",
                "Está bien así" in _v160b and "Confirmado" not in _v160b
                and not NucleoPedidos(db_path=Path(tmp13) / "p_56933331160.db").cola())
            _ped160c = {**_ped160b, "forma_pago": "tarjeta", "modalidad": "retiro", "direccion": None}
            _v160c, _ = _un_turno13(
                "56933332160", "Retiro con tarjeta, eso es todo",
                ["<PEDIDO_JSON>\n" + json.dumps({**_ped160c, "_tool": "emitir_pedido"})
                 + "\n</PEDIDO_JSON>"])
            # Caso 165 (curador, 13-oct-2026): dijo "Efectivo" y la ficha salio sin la linea
            # de pago; con "Esta bien" se cerro en efectivo delivery sin saber con cuanto paga.
            _ped165 = {**_prev144, "direccion": "Avenida Recoleta 5925", "forma_pago": None,
                       "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                                  "variante": None, "cantidad": 1, "notas": ""}]}
            _v165a, _ = _un_turno13(
                "56933330165", "Efectivo",
                ["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps({**_ped165, "_tool": "previsualizar_pedido"})
                 + "\n</PEDIDO_JSON>"],
                sembrar=[("usuario", "Es Delivery\nAvenida Recoleta 5925\n1 churrasco italiano"),
                         ("asistente", "¿Con qué forma de pago prefieres pagar: efectivo, tarjeta o transferencia?")])
            chk("caso 165: el 'Efectivo' que dijo el cliente sale en la ficha y se pregunta con cuanto paga",
                "•Forma de pago: Efectivo" in (_v165a or "") and "con cuánto pagas" in (_v165a or ""))
            _sem165 = [("usuario", "Es Delivery\nAvenida Recoleta 5925\n1 churrasco italiano\nEfectivo"),
                       ("asistente", "•Pedido: 1 Churrasco Italiano ($4.900)\n\nTotal con delivery $6.400.\n\n"
                                     "¿Está bien así, o quieres agregar/modificar algo?")]
            _v165b, _ = _un_turno13(
                "56933331165", "Esta bien",
                ["Confirmado. Demora 30 min aprox, ¡gracias!\n<PEDIDO_JSON>\n"
                 + json.dumps({**_ped165, "forma_pago": "efectivo", "_tool": "emitir_pedido"})
                 + "\n</PEDIDO_JSON>"], sembrar=_sem165)
            chk("...165: efectivo delivery sin monto y sin preguntarlo -> ficha con '¿con cuánto pagas?', no a cocina",
                "con cuánto pagas" in (_v165b or "") and "Confirmado" not in (_v165b or "")
                and not NucleoPedidos(db_path=Path(tmp13) / "p_56933331165.db").cola())
            _v165c, _ = _un_turno13(
                "56933332165", "Esta bien",
                ["Confirmado. Demora 30 min aprox, ¡gracias!\n<PEDIDO_JSON>\n"
                 + json.dumps({**_ped165, "forma_pago": "efectivo", "_tool": "emitir_pedido"})
                 + "\n</PEDIDO_JSON>"],
                sembrar=[_sem165[0], ("asistente", _sem165[1][1] + " Y como pagas en efectivo, "
                                                   "¿con cuánto pagas, para llevarte el vuelto?")])
            chk("control 165: si ya se le pregunto con cuanto paga, no se le vuelve a frenar",
                "con cuánto pagas" not in (_v165c or ""))
            chk("control 160: cierre directo con forma de pago y sin chacarero se despacha igual",
                (_v160c or "").startswith("Confirmado")
                and len(NucleoPedidos(db_path=Path(tmp13) / "p_56933332160.db").cola()) == 1)

            # Casos 159 y 160 (curador): el bot ofrecio el menu y el "Ok" no lo mando.
            _v159, _c159 = _un_turno13(
                "56933330159", "Ok", ["¡Gracias a ti! Si quieres hacer un pedido, dime si es delivery o retiro."],
                sembrar=[("usuario", "Hasta que hora esta atendiendo"),
                         ("asistente", "Hoy estamos abiertos hasta las 23:59.\n¿Quieres hacer un pedido o necesitas el menú?")])
            chk("caso 159: 'Ok' a '¿necesitas el menú?' manda el link, sin llamar al modelo",
                _v159 == prototipo.MENSAJE_LINK_MENU and _c159.llamadas == 0)
            _v160m, _ = _un_turno13(
                "56933333160", "Oj\nOk", ["Dale, ¿qué quieres pedir?"],
                sembrar=[("usuario", "Que tienes"),
                         ("asistente", "Tenemos Vienesas, Churrascos y más.\n\nSi quieres, puedo enviarte el link con el menú completo.")])
            chk("...160: 'Oj / Ok' a 'puedo enviarte el link con el menú' manda el link",
                "linktr.ee/patatahuechuraba" in (_v160m or ""))
            _v159c, _c159c = _un_turno13(
                "56933331159", "Ok, quiero 2 italianos", ["¿Vienesa o As?"],
                sembrar=[("asistente", "¿Quieres hacer un pedido o necesitas el menú?")])
            chk("control 159: si el 'Ok' trae un pedido, sigue el modelo",
                _c159c.llamadas >= 1 and "linktr.ee" not in (_v159c or ""))
            _v159d, _c159d = _un_turno13(
                "56933332159", "Ok", ["¿Qué te gustaría pedir?"],
                sembrar=[("asistente", "No tenemos mechada hoy, ¿quieres otra cosa de la carta?")])
            chk("control 159: '¿otra cosa de la carta?' no es una oferta del link",
                _c159d.llamadas >= 1 and "linktr.ee" not in (_v159d or ""))

            # Caso 162 (curador): la nota del producto sale en la ficha.
            _ped162 = {**_prev144, "modalidad": "retiro", "direccion": None,
                       "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                                  "variante": None, "cantidad": 1, "notas": "sin tomate"}]}
            _v162f, _ = _un_turno13(
                "56933331162", "1 churrasco italiano sin tomate, retiro, tarjeta",
                ["Perfecto.\n<PEDIDO_JSON>\n" + json.dumps({**_ped162, "_tool": "previsualizar_pedido"})
                 + "\n</PEDIDO_JSON>"])
            chk("caso 162: la nota del producto sale en la ficha como linea de comentarios",
                "•Comentarios: Churrasco Italiano sin tomate" in (_v162f or ""))

            # Caso 160 (curador): "¿Quieres que sea normal o XL?" con el churrasco en la
            # oracion anterior.
            chk("caso 160: la pregunta de tamaño se saca aunque el churrasco este en otra oracion",
                prototipo.sanear_tamano_sandwich(
                    "Perfecto, un Churrasco Chacarero.\n\n¿Quieres que sea normal o XL? "
                    "(ya que sólo existe una versión)", "Churrasco") == "Perfecto, un Churrasco Chacarero.")
            chk("control 160: la de una vienesa sigue igual",
                prototipo.sanear_tamano_sandwich("Perfecto, una Vienesa Italiano. ¿Normal o XL?", "italiano")
                == "Perfecto, una Vienesa Italiano. ¿Normal o XL?")

            # Caso 142 (curador): "Viene presencial" tras el saludo.
            _v142, _c142 = _un_turno13(
                "56933330142", "Viene presencial", ["Perfecto, retiro entonces. ¿Qué te gustaría pedir?"],
                sembrar=[("usuario", "Hola\nEstan atendiendo?"), ("asistente", _saludo13)])
            chk("caso 142: 'Viene presencial' = gracias por preferirnos, sin pedirle el pedido",
                _v142 == prototipo.MENSAJE_VIENE_PRESENCIAL and _c142.llamadas == 0
                and _es_despedida_del_bot(_v142))
            _, _c142c = _un_turno13(
                "56933331142", "voy a retirar 2 italianos", ["¿Vienesa o As?"],
                sembrar=[("usuario", "Hola"), ("asistente", _saludo13)])
            chk("control 142: 'voy a retirar 2 italianos' es un pedido, sigue el modelo",
                _c142c.llamadas >= 1)

            _resp_orig13 = RESPALDO_DB
            RESPALDO_DB = Path(tmp13) / "respaldo_13.db"
            try:
                _v149, _c149 = _un_turno13(JEFE_NUM, "confirmo", ["Listo, operamos normal con delivery 👍"])
                chk("caso 149: el 'confirmo' del jefe sin transferencia esperando va a la conversacion",
                    _c149.llamadas == 1 and "No hay pedidos esperando" not in (_v149 or ""))

                # Casos 147 y 162 (curador): cierre por transferencia sin la cuenta y con
                # "Demora 30 min", como si ya estuviera en cocina.
                _ped147 = {**_prev144, "modalidad": "retiro", "direccion": None,
                           "forma_pago": "transferencia",
                           "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                                      "variante": None, "cantidad": 2, "notas": ""}]}
                _sem147 = [("usuario", "2 churrasco italiano, retiro, transferencia"),
                           ("asistente", "•Pedido: 2 Churrasco Italiano ($8.900)\n\nTotal $8.900.\n\n"
                                         "¿Está bien así, o quieres agregar/modificar algo?")]
                for _tel147, _txt147 in (("56933330147", ""),
                                         ("56933330162", "Confirmado. Demora 10 min aprox, ¡gracias!")):
                    _v147, _ = _un_turno13(
                        _tel147, "Esta perfecto",
                        [_txt147 + "\n<PEDIDO_JSON>\n" + json.dumps({**_ped147, "_tool": "emitir_pedido"})
                         + "\n</PEDIDO_JSON>"], sembrar=_sem147)
                    chk(f"caso {_tel147[-3:]}: cierre por transferencia sin demora y con la cuenta del local"
                        + (" (texto del modelo)" if _txt147 else " (cierre fijo)"),
                        (_v147 or "").startswith(prototipo.CONFIRMACION_CIERRE_TRANSFERENCIA)
                        and "Demora" not in _v147 and "Confirmado" not in _v147
                        and "XXXX-XXXX-XX" in _v147 and "Total a transferir" in _v147)
                    # Caso 167 (14-oct-2026): el curador mostraba solo la primera linea; el
                    # aviso con el total y la cuenta se pegaba despues de guardar el turno.
                    import sqlite3 as _sq167
                    _cn167 = _sq167.connect(Path(tmp13) / f"conv_{_tel147}.db")
                    _guardado167 = _cn167.execute(
                        "SELECT texto FROM turnos WHERE rol='asistente' ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                    _cn167.close()
                    chk(f"caso 167 ({_tel147[-3:]}): lo guardado es EXACTAMENTE lo que se envio",
                        _guardado167 and _guardado167[0] == _v147)
                # Caso 167: el cierre en espera de pago cuenta como cierre para los candados
                # (el "A ustedes" despues no es un pedido nuevo).
                _v167c, _c167c = _un_turno13(
                    "56933331167", "A ustedes",
                    ["¿Te lo agrego al pedido?"],
                    sembrar=[("usuario", "Esta perfecto"),
                             ("asistente", prototipo.CONFIRMACION_CIERRE_TRANSFERENCIA
                              + "\n\nTotal a transferir: $8.900.")])
                chk("caso 167: tras el cierre en espera de pago, 'A ustedes' es despedida",
                    _v167c == "¡Gracias a ti!" and _c167c.llamadas == 0)

                # Caso 162 (curador): "recordar sin cebolla" con el pedido ya cerrado.
                _guardar_ultimo_pedido("56933332162", "1x Churrasco Italiano · retiro · tarjeta")
                _sem162 = [("asistente", "•Es delivery o retiro?: Retiro\n•Pedido: 1 Churrasco Italiano ($4.900)\n"
                                         "•Comentarios: Churrasco Italiano sin tomate\n•Forma de pago: Tarjeta"),
                           ("usuario", "Está bien así"),
                           ("asistente", "Confirmado. Demora 10 min aprox, ¡gracias!")]
                _v162r, _c162r = _un_turno13(
                    "56933332162", "recordar sin tomate porfavor, gracias",
                    ["¿Quieres que anote sin tomate? La cual ya está quitada según tu pedido."], sembrar=_sem162)
                chk("caso 162: recordar una nota ya anotada = 'Ya está anotado', sin llamar al modelo",
                    _v162r == prototipo.MENSAJE_NOTA_YA_ANOTADA and _c162r.llamadas == 0)
                _, _c162s = _un_turno13(
                    "56933332162", "recordar sin cebolla porfavor", ["Perfecto, se lo aviso al local."])
                chk("control 162: una indicacion que NO esta anotada la ve el modelo",
                    _c162s.llamadas >= 1)
            finally:
                RESPALDO_DB = _resp_orig13
        finally:
            prototipo.INSTRUCCIONES = _orig_instr13

    print("== 17-ago-2026 (pendiente #34): limite anti-abuso de mensajes entrantes ==")
    with carpeta_temporal() as tmp_abuso:
        conv_abuso = Path(tmp_abuso) / "conv_abuso.db"
        tel_abuso = "56955550000"
        # Simula que el numero ya dejo LIMITE_MENSAJES_ANTIABUSO turnos DENTRO de la
        # ventana (alternando cliente/bot, igual que un pedido real -- el candado
        # cuenta los dos roles, no solo al cliente).
        for i in range(LIMITE_MENSAJES_ANTIABUSO):
            guardar_turno(tel_abuso, "usuario" if i % 2 == 0 else "asistente",
                          f"mensaje {i}", db_path=conv_abuso)
        _wa_orig_abuso, _wa_abuso = enviar_whatsapp, []

        def _espia_abuso(telefono, texto, cfg, intentos=2):
            _wa_abuso.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia_abuso
        try:
            cerebro_abuso = _CerebroFalso(
                ["ERROR: no deberia llamarse -- el candado tenia que cortar antes"])
            vis_abuso = procesar_mensaje(tel_abuso, "mensaje numero 21", {**CONFIG_DEFAULT},
                                         cerebro=cerebro_abuso, conv_db=conv_abuso)
            chk(f"al turno {LIMITE_MENSAJES_ANTIABUSO + 1} el bot deja de contestar "
                "(no llama al cerebro)", cerebro_abuso.llamadas == 0 and vis_abuso is None)
            chk("...y no le manda NADA al cliente por WhatsApp",
                not [t for tel, t in _wa_abuso if tel == tel_abuso])
            chk("...pero SI avisa a los jefes, una vez",
                len([t for tel, t in _wa_abuso if tel in NUMS_ALERTAS_JEFE
                     and "paso los" in t]) == len(NUMS_ALERTAS_JEFE))
            chk("...y el mensaje bloqueado igual queda en el historial (auditable)",
                _mensajes_recientes(tel_abuso, db_path=conv_abuso)
                == LIMITE_MENSAJES_ANTIABUSO + 1)
            # Un segundo mensaje bloqueado NO repite la alerta (anti-spam a los jefes,
            # mismo criterio que el pedido duplicado / humano prometido).
            _wa_abuso.clear()
            cerebro_abuso2 = _CerebroFalso(["ERROR: no deberia llamarse"])
            procesar_mensaje(tel_abuso, "mensaje numero 22", {**CONFIG_DEFAULT},
                             cerebro=cerebro_abuso2, conv_db=conv_abuso)
            chk("...sin repetir el aviso a los jefes en el siguiente mensaje bloqueado",
                not [t for tel, t in _wa_abuso if tel in NUMS_ALERTAS_JEFE])

            # Control negativo: un cliente SIN ese historial no se ve afectado.
            _wa_abuso.clear()
            cerebro_normal = _CerebroFalso([respuesta_cerrada])
            nucleo_abuso = NucleoPedidos(db_path=Path(tmp_abuso) / "p_abuso.db")
            vis_normal = procesar_mensaje(
                "56955550099", "2 churrascos italianos retiro tarjeta", {**CONFIG_DEFAULT},
                cerebro=cerebro_normal, nucleo=nucleo_abuso, conv_db=conv_abuso)
            chk("un cliente sin esa racha SI pasa normal (control negativo)",
                cerebro_normal.llamadas == 1 and vis_normal is not None)

            # Control negativo: el JEFE nunca se bloquea, aunque tenga mas turnos que
            # el limite -- necesita poder seguir operando el bot siempre.
            for i in range(LIMITE_MENSAJES_ANTIABUSO + 5):
                guardar_turno(JEFE_NUM, "usuario" if i % 2 == 0 else "asistente",
                              f"jefe {i}", db_path=conv_abuso)
            _wa_abuso.clear()
            vis_jefe = procesar_mensaje(JEFE_NUM, "no llegamos", {**CONFIG_DEFAULT},
                                        conv_db=conv_abuso)
            chk("...el JEFE nunca se bloquea aunque pase el limite de turnos "
                "(control negativo)", vis_jefe is not None)
        finally:
            enviar_whatsapp = _wa_orig_abuso

    print("== 03-ago-2026 (P28): pedido grande (10+ por linea) lo toma una persona ==")
    chk("9 unidades todavia son un pedido normal",
        _proto.candado_pedido_grande(
            {"items": [{"producto": "Churrasco Italiano", "cantidad": 9}]})[0] is None)
    _grande, _det = _proto.candado_pedido_grande(
        {"items": [{"producto": "Churrasco Italiano", "cantidad": 10}]})
    chk("10 unidades de una linea SI disparan el candado", _grande is not None)
    chk("...y el detalle nombra el item", _det == "10x Churrasco Italiano")
    chk("la SUMA de lineas distintas no dispara (Gonza: 'qty por linea')",
        _proto.candado_pedido_grande(
            {"items": [{"producto": "Churrasco Italiano", "cantidad": 6},
                       {"producto": "Vienesa Completo", "cantidad": 6}]})[0] is None)
    with carpeta_temporal() as tmp14:
        _wa_orig14, _wa14 = enviar_whatsapp, []

        def _espia14(telefono, texto, cfg, intentos=2):
            _wa14.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia14
        try:
            db14 = Path(tmp14) / "pedidos_grande.db"
            conv14 = Path(tmp14) / "conv_grande.db"
            nucleo14 = NucleoPedidos(db_path=db14)
            resp14 = respuesta_cerrada.replace('"cantidad":2', '"cantidad":12')
            vis14 = procesar_mensaje("56977776666", "quiero 12 churrascos italianos, retiro, tarjeta",
                                     {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([resp14]),
                                     nucleo=nucleo14, conv_db=conv14)
            chk("el pedido grande NO entra a la cola automatica", len(nucleo14.cola()) == 0)
            chk("el cliente recibe el mensaje fijo de que lo toma una persona",
                _proto.MENSAJE_PEDIDO_GRANDE in vis14)
            chk("y los jefes reciben el detalle para no perder la venta",
                len([t for _, t in _wa14 if "PEDIDO GRANDE" in t]) == len(NUMS_ALERTAS_JEFE))
            chk("15-sep: y queda un caso_humano visible en el POS, sin depender de WhatsApp",
                [c["motivo"] for c in listar_casos_humanos(db_path=conv14)] == ["pedido_grande"])
            # ANTI-SPAM: el cliente insiste y el modelo reemite el MISMO pedido grande.
            _wa14.clear()
            procesar_mensaje("56977776666", "si, esos 12", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso([resp14]), nucleo=nucleo14, conv_db=conv14)
            chk("...pero el aviso a los jefes NO se repite en cada mensaje",
                not [t for _, t in _wa14 if "PEDIDO GRANDE" in t])
            chk("...ni el caso_humano se duplica en el POS",
                len(listar_casos_humanos(db_path=conv14)) == 1)
        finally:
            enviar_whatsapp = _wa_orig14

    print("== 06-ago-2026: si el bot promete una PERSONA, esa persona se entera ==")
    # Encontrado por bot/banco_pruebas.py. La regla 3.9 manda derivar a un humano en
    # reclamos, anulaciones y catering -- y hasta hoy NADIE se enteraba: derivar_humano
    # no despacha el pedido (bien) pero tampoco avisaba, y si el modelo lo decia
    # conversando, sin tool-call, menos todavia.
    chk("'te paso con alguien del local' es promesa de persona",
        _promete_humano("Te paso con alguien del local para que te atienda mejor."))
    chk("'te responde alguien del local' tambien",
        _promete_humano("Mejor te responde alguien del local para coordinar ese pedido."))
    chk("el mensaje fijo del pedido grande tambien lo es",
        _promete_humano(_proto.MENSAJE_PEDIDO_GRANDE))
    # Redaccion REAL que se le escapo a la primera version del candado (banco, 06-ago):
    # decia "paso con" y el modelo escribio "paso A", y "atienda" en vez de "atiende".
    chk("'te paso A alguien ... para que te ATIENDA' (la que se escapo)",
        _promete_humano("Pedido grande de 12 churrascos, te paso a alguien del local "
                        "para que te atienda bien. Un momento porfa."))
    chk("'un encargado te llama' tambien",
        _promete_humano("Un encargado te llama en unos minutos."))
    chk("'el local te avisa apenas este listo' NO lo es (regla 3.8, no promete a nadie)",
        not _promete_humano("El local te avisa apenas este listo, yo no manejo el estado en vivo."))
    chk("'llamalo directo al local' NO lo es (ahi contacta el cliente)",
        not _promete_humano("Mejor llamalo directo al local para resolverlo al tiro: +56 9 XXXX XXXX"))
    chk("un cierre normal NO lo es",
        not _promete_humano("Perfecto, quedan 2 Churrasco Italiano, retiro. Total $8.900."))
    with carpeta_temporal() as tmp15:
        _wa_orig15, _wa15 = enviar_whatsapp, []

        def _espia15(telefono, texto, cfg, intentos=2):
            _wa15.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia15
        try:
            db15 = Path(tmp15) / "pedidos_humano.db"
            conv15 = Path(tmp15) / "conv_humano.db"
            nucleo15 = NucleoPedidos(db_path=db15)
            # Reclamo: el modelo marca derivar_humano y promete una persona.
            resp15 = ("Uy, que lata lo del pedido frio. En un momento te responde alguien "
                      "del local.\n" +
                      respuesta_cerrada[respuesta_cerrada.index("<PEDIDO_JSON>"):].replace(
                          '"derivar_humano":false', '"derivar_humano":true'))
            procesar_mensaje("56911119999", "me llego frio el pedido, quiero reclamar",
                             {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([resp15]),
                             nucleo=nucleo15, conv_db=conv15)
            _alertas15 = [t for _, t in _wa15 if "PROMETIÓ UNA PERSONA" in t]
            chk("un reclamo derivado SI le llega a los jefes (antes: a nadie)",
                len(_alertas15) == len(NUMS_ALERTAS_JEFE))
            chk("...con lo que escribio el cliente, para poder contestarle",
                any("me llego frio" in t for t in _alertas15))
            chk("...y el pedido NO entro a cocina", len(nucleo15.cola()) == 0)
            chk("15-sep: y queda un caso_humano visible en el POS, sin depender de WhatsApp",
                [c["motivo"] for c in listar_casos_humanos(db_path=conv15)] == ["humano_prometido"])
            # ANTI-SPAM: el cliente insiste, los jefes ya lo saben.
            _wa15.clear()
            procesar_mensaje("56911119999", "hola? alguien?", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso(["Ya les avise, alguien te responde."]),
                             nucleo=nucleo15, conv_db=conv15)
            chk("el aviso NO se repite en cada mensaje de la misma conversacion",
                not [t for _, t in _wa15 if "PROMETIÓ UNA PERSONA" in t])
            chk("...ni el caso_humano se duplica en el POS",
                len(listar_casos_humanos(db_path=conv15)) == 1)
            # SIN RUIDO NUEVO: un pedido normal no despierta a nadie.
            _wa15.clear()
            procesar_mensaje("56911118888", "2 italianos retiro tarjeta", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso([respuesta_cerrada]),
                             nucleo=nucleo15, conv_db=conv15)
            chk("un pedido normal no genera ninguna alerta de persona",
                not [t for _, t in _wa15 if "PROMETIÓ UNA PERSONA" in t])
            chk("...ni un caso_humano nuevo", len(listar_casos_humanos(db_path=conv15)) == 1)

            # 28-sep-2026: el boquete real -- derivar_humano=true pero el texto NO
            # promete una persona (sin "alguien"/"persona"/"encargado" + verbo de
            # contacto). Antes: 0 avisos, el pedido quedaba retenido en silencio.
            _wa15.clear()
            _cola_antes15b = len(nucleo15.cola())
            resp15b = ("Uy, que lata lo del pedido incompleto. Ya reviso qué pasó y vemos "
                       "cómo lo solucionamos.\n" +
                       respuesta_cerrada[respuesta_cerrada.index("<PEDIDO_JSON>"):].replace(
                           '"derivar_humano":false', '"derivar_humano":true'))
            chk("...control: ese texto de prueba en si mismo NO promete una persona",
                not _promete_humano(resp15b))
            procesar_mensaje("56911117777", "me llego incompleto el pedido de ayer",
                             {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([resp15b]),
                             nucleo=nucleo15, conv_db=conv15)
            _alertas15b = [t for _, t in _wa15 if "DERIVAR_HUMANO" in t]
            chk("derivar_humano SIN promesa de persona en el texto, IGUAL avisa a los jefes",
                len(_alertas15b) == len(NUMS_ALERTAS_JEFE))
            chk("...no se confunde con el candado de 'PROMETIÓ UNA PERSONA'",
                not [t for _, t in _wa15 if "PROMETIÓ UNA PERSONA" in t])
            chk("...el pedido tampoco entro a cocina",
                len(nucleo15.cola()) == _cola_antes15b)
            chk("...y queda un caso_humano 'derivar_humano' visible en el POS",
                "derivar_humano" in [c["motivo"] for c in listar_casos_humanos(db_path=conv15)])

            # 28-sep-2026: _mensaje_ficha_no_armada (caso 69) tambien queda visible en
            # el POS -- antes SOLO mandaba WhatsApp, sin rastro si nadie lo leia. Este
            # es el caso limite en que el motor NI SIQUIERA sabe que preguntar (ni "no
            # esta en el menu", ni "falta variante", ni tamaños con precio que ofrecer)
            # -- por eso se mockea _cuenta_payload, para no depender de encontrar un
            # producto real del menu que caiga justo en ese hueco.
            _cp_orig15 = _cuenta_payload
            _cuenta_payload = lambda payload: {
                "items": [{"producto": "Combo Misterioso", "ok": False, "problema": "sin precio"}]}
            try:
                _wa15.clear()
                _casos_antes15c = len(listar_casos_humanos(db_path=conv15))
                _mensaje_ficha_no_armada({}, "56911116666", CONFIG_DEFAULT, db_path=conv15)
                chk("ficha no armada: SIGUE avisando por WhatsApp como antes",
                    len([t for _, t in _wa15 if "armar la ficha" in t]) == len(NUMS_ALERTAS_JEFE))
                chk("...y AHORA ademas queda como caso_humano en el POS (antes: nada)",
                    len(listar_casos_humanos(db_path=conv15)) == _casos_antes15c + 1 and
                    listar_casos_humanos(db_path=conv15)[-1]["motivo"] == "ficha_no_armada")
            finally:
                _cuenta_payload = _cp_orig15
        finally:
            enviar_whatsapp = _wa_orig15

    print("== 03-ago-2026 (P9): 'no llegamos' -- la otra respuesta posible del jefe ==")
    chk("'no llegamos' se reconoce", _es_no_llegamos("no llegamos"))
    chk("tolera falta de ortografia ('no yegamos')", _es_no_llegamos("no yegamos"))
    chk("y variantes ('fuera de zona')", _es_no_llegamos("fuera de zona"))
    chk("'no llegamos a esa direccion' tambien",
        _es_no_llegamos("no llegamos a esa direccion"))
    chk("NO se dispara dentro de una frase larga de conversacion",
        not _es_no_llegamos("oye el pedido de las 3 no llega hace rato, llamalo tu por favor"))
    chk("ni se confunde con un monto", not _es_no_llegamos("envío 2000"))
    with carpeta_temporal() as tmp15:
        _respaldo_orig15 = RESPALDO_DB
        RESPALDO_DB = Path(tmp15) / "respaldo_nollegamos.db"
        _wa_orig15, _wa15 = enviar_whatsapp, []

        def _espia15(telefono, texto, cfg, intentos=2):
            _wa15.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia15
        try:
            db15 = Path(tmp15) / "pedidos_nollegamos.db"
            conv15 = Path(tmp15) / "conv_nollegamos.db"
            nucleo15 = NucleoPedidos(db_path=db15)
            resp15 = respuesta_cerrada.replace(
                '"modalidad":"retiro","direccion":null',
                '"modalidad":"delivery","direccion":"Camino Largo 9999"')
            procesar_mensaje("56933332222", "delivery a Camino Largo 9999, tarjeta",
                             {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([resp15]),
                             nucleo=nucleo15, conv_db=conv15)
            chk("el pedido queda esperando el cobro del jefe", contar_esperando_cobro() == 1)
            vis15 = procesar_mensaje(JEFE_NUM, "no llegamos", {**CONFIG_DEFAULT},
                                     cerebro=_CerebroFalso(["no deberia llamarse"]),
                                     nucleo=nucleo15, conv_db=conv15)
            chk("la espera se cierra", contar_esperando_cobro() == 0)
            chk("el pedido NO entra a cocina", len(nucleo15.cola()) == 0)
            chk("el cliente recibe la frase literal de Christian",
                any(t == MENSAJE_NO_LLEGAMOS for n, t in _wa15 if n == "56933332222"))
            chk("y el jefe recibe la confirmacion de lo que paso",
                "no entró a cocina" in vis15.lower())
        finally:
            RESPALDO_DB = _respaldo_orig15
            enviar_whatsapp = _wa_orig15

    print("== 04-ago-2026 (P29): pedido PROGRAMADO -- captura, aviso al cliente, recordatorio ==")
    _ped_prog = {
        "cliente": "Ana", "modalidad": "retiro", "direccion": None,
        "items": [{"categoria": "Churrascos", "producto": "Churrasco Italiano",
                   "cantidad": 1, "notas": ""}],
        "delivery_cobro": 0, "total": 4900, "forma_pago": "efectivo",
        "hora_programada": "21:00", "derivar_humano": False, "comentarios": "",
    }
    _pay_prog = _payload_pedido(_ped_prog, "56911112222")
    chk("_payload_pedido traduce una hora_programada valida", _pay_prog["hora_programada"] == "21:00")
    _ped_prog_mala = {**_ped_prog, "hora_programada": "no-es-una-hora"}
    chk("una hora_programada invalida del modelo se ignora (no rompe el pedido)",
        _payload_pedido(_ped_prog_mala, "56911112222")["hora_programada"] is None)
    chk("un pedido normal (sin hora_programada) queda en None",
        _payload_pedido({**_ped_prog, "hora_programada": None}, "56911112222")["hora_programada"] is None)

    # _minutos_para_hora: aislado del reloj real via 'ahora' explicito.
    _ahora_prog = datetime(2026, 7, 28, 20, 50, tzinfo=TZ_CHILE)
    chk("faltan 10 minutos para las 21:00 desde las 20:50", _minutos_para_hora("21:00", _ahora_prog) == 10)
    chk("la hora ya paso -> negativo",
        _minutos_para_hora("20:00", _ahora_prog) == -50)

    # Integracion del despacho: el aviso al cliente confirma la hora, y el pedido
    # sigue viendose normal en la comanda que llega al POS.
    with carpeta_temporal() as tmp17:
        # RESPALDO_DB propio (05-ago-2026): este bloque usa un telefono que OTRO test de
        # mas arriba ya despacho, y desde el candado de pedido duplicado esa herencia lo
        # hacia fallar -- el candado veia un pedido de "hace 0 min" y no despachaba. El
        # test de programados no tiene nada que ver con eso: se aisla, como tmp15/tmp_dup.
        _respaldo_orig17 = RESPALDO_DB
        RESPALDO_DB = Path(tmp17) / "respaldo_prog_aparte.db"
        try:
            conv17 = Path(tmp17) / "conv_prog.db"
            nucleo17 = NucleoPedidos(db_path=Path(tmp17) / "p_prog.db")
            resp17 = respuesta_cerrada.replace(
                '"derivar_humano":false', '"hora_programada":"21:00","derivar_humano":false')
            vis17 = procesar_mensaje("56911113333", "quiero mi pedido para las 21:00, retiro, tarjeta",
                                     {**CONFIG_DEFAULT}, cerebro=_CerebroFalso([resp17]),
                                     nucleo=nucleo17, conv_db=conv17)
            chk("el cliente recibe la confirmacion de la hora agendada", "21:00" in vis17 and "agendado" in vis17.lower())
            cola17 = nucleo17.cola()
            chk("el pedido programado SI entra a la cola (visible, no escondido)", len(cola17) == 1)
            chk("...marcado con su hora_programada", cola17 and cola17[0].get("hora_programada") == "21:00")
        finally:
            RESPALDO_DB = _respaldo_orig17

    # _revisar_programados: recordatorio a los 15 min antes, escalon a los 5 sin avance.
    with carpeta_temporal() as tmp18:
        db18 = Path(tmp18) / "respaldo_prog.db"
        _wa_orig18, _wa18 = enviar_whatsapp, []

        def _espia18(telefono, texto, cfg, intentos=2):
            _wa18.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia18
        _pend_orig18 = _pedidos_programados_pendientes
        # _ahora_chile esta forzado a mediodia (12:00) para todo el --test (ver arriba,
        # mismo motivo que el corte de pedidos): 12:10 cae DENTRO de la ventana de
        # RECORDATORIO_PROGRAMADO_MINUTOS (15) sin depender del reloj real.
        _pedido_falso18 = {"id": 42, "estado": "recibido", "hora_programada": "12:10",
                           "cliente": "Ana", "modalidad": "retiro", "items": [], "telefono": "56911114444"}
        _pedidos_programados_pendientes = lambda cfg: [_pedido_falso18]  # noqa: E731,F811
        try:
            _revisar_programados({**CONFIG_DEFAULT}, db_path=db18)
            # 13-sep-2026: el primer aviso va a jefe1 (JEFE_NUM) y, si nadie lo confirma,
            # escala a jefe2 (JEFE_BACKUP) -- ver ROLES_JEFE y _avisar_jefe_programado.
            chk("primer aviso: le llega a jefe1",
                any(n == JEFE_NUM and "PROGRAMADO" in t and "12:10" in t for n, t in _wa18))
            chk("...y todavia no a jefe2 (no paso el tiempo de escalar)",
                not any(n == JEFE_BACKUP for n, t in _wa18))
            _revisar_programados({**CONFIG_DEFAULT}, db_path=db18)
            chk("una segunda vuelta inmediata NO repite el primer aviso (una vez, no spam)",
                sum(1 for n, t in _wa18 if n == JEFE_NUM) == 1)
            # Forzar que el reloj del escalon ya paso (recordado_en viejo).
            con18 = _con_respaldo(db18)
            con18.execute("UPDATE recordatorios_programados SET recordado_en=? WHERE pedido_id=42",
                          ((datetime.now() - timedelta(minutes=ESCALA_PROGRAMADO_MINUTOS + 1))
                           .isoformat(timespec="seconds"),))
            con18.commit(); con18.close()
            _revisar_programados({**CONFIG_DEFAULT}, db_path=db18)
            chk("tras el tiempo de escalon sin avance, SI avisa al respaldo",
                any(n == JEFE_BACKUP and "PROGRAMADO" in t for n, t in _wa18))
            _wa18.clear()
            _revisar_programados({**CONFIG_DEFAULT}, db_path=db18)
            chk("ya escalado, no vuelve a avisar a nadie (de aca es decision humana)",
                len(_wa18) == 0)
            # Si el pedido YA avanzo a cocina, no hay nada que recordar.
            _wa18.clear()
            _pedido_falso18["estado"] = "en_preparacion"
            _revisar_programados({**CONFIG_DEFAULT}, db_path=db18)
            chk("un pedido que ya paso a cocina no dispara ningun aviso mas", len(_wa18) == 0)
        finally:
            _pedidos_programados_pendientes = _pend_orig18
            enviar_whatsapp = _wa_orig18

    print("== 13-sep-2026: seguimiento de saludo sin respuesta (UN mensaje, nunca una caceria) ==")
    with carpeta_temporal() as tmp19:
        conv19 = Path(tmp19) / "conv_seg.db"
        _wa_orig19, _wa19 = enviar_whatsapp, []

        def _espia19(telefono, texto, cfg, intentos=2):
            _wa19.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia19
        _eh_orig19 = estado_horario
        estado_horario = lambda menu: (True, "[test] ABIERTO (forzado)")  # noqa: E731,F811
        try:
            viejo = (datetime.now() - timedelta(minutes=MINUTOS_SEGUIMIENTO_SALUDO + 1)
                     ).isoformat(timespec="seconds")
            reciente = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")

            def _sembrar(con, telefono, turnos):
                for rol, texto, creado in turnos:
                    con.execute(
                        "INSERT INTO turnos (telefono, rol, texto, creado_en) VALUES (?,?,?,?)",
                        (telefono, rol, texto, creado))

            con19 = _con_conv_en(conv19)
            # A: saludo sin respuesta hace rato -- tiene que disparar.
            _sembrar(con19, "56911111111",
                     [("usuario", "Hola", viejo), ("asistente", "Hola! Bienvenido...", viejo)])
            # B: saludo sin respuesta, pero recien -- todavia no.
            _sembrar(con19, "56922222222",
                     [("usuario", "Hola", reciente), ("asistente", "Hola! Bienvenido...", reciente)])
            # C: un jefe (jefe1) sin respuesta hace rato -- nunca se le vende.
            _sembrar(con19, JEFE_NUM, [("asistente", "Listo, anotado.", viejo)])
            # D: el cliente YA contesto -- el ultimo turno es suyo, no del bot.
            _sembrar(con19, "56944444444",
                     [("usuario", "Hola", viejo), ("asistente", "Hola! Bienvenido...", viejo),
                      ("usuario", "Un churrasco italiano", viejo)])
            con19.commit()
            # E: pedido ya confirmado -- turno archivado (cerrado_en puesto).
            _sembrar(con19, "56955555555", [("asistente", "Hola! Bienvenido...", viejo)])
            con19.execute("UPDATE turnos SET cerrado_en=? WHERE telefono='56955555555'",
                          (datetime.now().isoformat(timespec="seconds"),))
            con19.commit()
            con19.close()

            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=conv19)
            chk("saludo sin respuesta hace rato: SI se le manda el recordatorio",
                any(n == "56911111111" for n, t in _wa19))
            chk("el mensaje es un recordatorio amigable, no una insistencia",
                all(t == MENSAJE_SEGUIMIENTO_SALUDO and "%" not in t and "descuento" not in t.lower()
                    for n, t in _wa19 if n == "56911111111"))
            chk("saludo reciente: todavia NO se le manda nada", not any(n == "56922222222" for n, t in _wa19))
            chk("jefe: nunca se le manda un mensaje de venta", not any(n == JEFE_NUM for n, t in _wa19))
            chk("el cliente ya habia contestado: no hace falta recordarle nada",
                not any(n == "56944444444" for n, t in _wa19))
            chk("pedido ya confirmado (turno archivado): tampoco se le manda", not any(n == "56955555555" for n, t in _wa19))

            _wa19.clear()
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=conv19)
            chk("una segunda vuelta NO repite el recordatorio ya mandado -- UNA vez, nunca una caceria",
                len(_wa19) == 0)

            # F: local CERRADO -- ni al candidato mas obvio se le manda nada. Seria
            # contradictorio avisar "cerramos" y despues insistir con el pedido.
            con19 = _con_conv_en(conv19)
            _sembrar(con19, "56966666666",
                     [("usuario", "Hola", viejo), ("asistente", "Hola! Bienvenido...", viejo)])
            con19.commit(); con19.close()
            estado_horario = lambda menu: (False, "[test] CERRADO (forzado)")  # noqa: E731,F811
            _wa19.clear()
            _revisar_seguimientos_saludo({**CONFIG_DEFAULT}, db_path=conv19)
            chk("local cerrado: no se manda ningun recordatorio, ni al mas candidato",
                len(_wa19) == 0)
        finally:
            enviar_whatsapp = _wa_orig19
            estado_horario = _eh_orig19

    print("== 06-ago-2026 (F5): el MENU viaja por INTENCION, no completo en cada mensaje ==")
    _menu_f5 = json.loads(MENU_JSON.read_text(encoding="utf-8"))
    _completo_f5 = _proto.menu_a_texto(_menu_f5)
    _todos_f5 = [p["nombre"] for c in _menu_f5["categorias"] for p in c["productos"]]

    # --- Lo que NO puede cambiar: el menu completo de siempre ---
    chk("sin foco, el bloque de menu sale identico al de siempre (modo jefe, CLI, auditoria)",
        _completo_f5 == _proto.menu_a_texto(_menu_f5, foco=None))
    chk("con el foco puesto en TODO, tambien sale identico -- ni una linea de mas",
        _proto.menu_a_texto(_menu_f5, foco=_proto._todo_el_menu(_menu_f5)) == _completo_f5)
    chk("el prompt del JEFE sigue llevando el menu entero (no toma pedidos, no se toca)",
        _proto.menu_a_texto(_menu_f5) in construir_sistema(jefe=True, conversacion="hola"))

    # --- Lo que se gana ---
    _foco_hola = _proto.foco_del_menu(_menu_f5, "hola, tienen delivery?")
    _txt_hola = _proto.menu_a_texto(_menu_f5, foco=_foco_hola)
    chk("un turno sin comida (saludo, direccion, 'si') recorta mas de 3.000 caracteres",
        len(_completo_f5) - len(_txt_hola) > 3000)
    _foco_ch = _proto.foco_del_menu(_menu_f5, "quiero 2 churrasco italiano")
    _txt_ch = _proto.menu_a_texto(_menu_f5, foco=_foco_ch)
    chk("en mitad de un pedido tambien recorta (el foco es por PRODUCTO, no por categoria)",
        len(_completo_f5) - len(_txt_ch) > 2000)
    chk("nombrar la base trae la categoria entera: 'churrasco' -> los 12 Churrascos",
        len(_foco_ch.get("Churrascos", ())) == 12)
    chk("...y los Especiales, que su nota dice que van 'como Churrasco o Mechada'",
        len(_foco_ch.get("Especiales", ())) == 5)
    chk("'italiano' trae el Vienesa Italiano pero NO las otras diez Vienesas "
        "(esto es lo que hacia inutil el foco por categoria: 1,2% de ahorro)",
        _foco_ch.get("Vienesas") == {"Vienesa Italiano"})

    # --- Lo que NO se puede perder nunca ---
    chk("el precio del producto pedido esta en el texto",
        "$" in _txt_ch.split("Churrasco Italiano (")[1][:120])
    for _caso_f5 in ("hola", "mi direccion es Del Valle 1234", "quiero 2 churrasco italiano",
                     "una papas grande", "quiero el box 2", "si", "gracias"):
        _t = _proto.menu_a_texto(_menu_f5, foco=_proto.foco_del_menu(_menu_f5, _caso_f5))
        chk(f"ningun producto DESAPARECE del prompt con '{_caso_f5[:28]}' -- lo no "
            "expandido igual viaja nombrado",
            all(n in _t for n in _todos_f5))
    chk("lo no expandido viaja SIN precio (ese es el ahorro)",
        "- Vienesa Completo (" not in _txt_ch and "Vienesa Completo" in _txt_ch)
    chk("y el prompt le explica al modelo que eso existe igual",
        "NOMBRADO PERO SIN PRECIO" in _txt_ch)

    # --- Los casos que decidieron el diseno ---
    chk("pedir la carta sin nombrar nada trae el menu ENTERO (ahi ahorrar seria un bot mudo)",
        _proto.foco_del_menu(_menu_f5, "me mandas el menu?") == _proto._todo_el_menu(_menu_f5))
    chk("...pero preguntar un precio nombrando el producto NO trae la carta entera",
        _proto.foco_del_menu(_menu_f5, "cuanto sale el churrasco italiano")
        != _proto._todo_el_menu(_menu_f5))
    chk("bebidas, agregados y salsas viajan SIEMPRE (son el cierre de cualquier pedido)",
        all(_foco_hola.get(c) for c in ("Bebestibles", "Agregados", "Salsas")))
    chk("un numero suelto no enfoca: 'quiero 1' no puede traer los Boxes (por 'Box 1')",
        "Boxes Pollo Crispy" not in _proto.foco_del_menu(_menu_f5, "quiero 1 de eso"))
    # El plural: el menu dice "Vienesa Completo" y el cliente escribe "2 completos".
    chk("el plural del cliente matchea el singular del menu ('completos' -> Completo)",
        "Vienesa Completo" in _proto.foco_del_menu(
            _menu_f5, "quiero 2 completos").get("Vienesas", set()))
    chk("...y 'churrascos' trae los Churrascos igual que 'churrasco'",
        _proto.foco_del_menu(_menu_f5, "dos churrascos italianos").get("Churrascos") ==
        _proto.foco_del_menu(_menu_f5, "dos churrasco italiano").get("Churrascos"))
    chk("la raiz no toca las palabras cortas: 'as' sigue siendo la categoria As",
        _proto._raiz("as") == "as" and _proto._raiz("dos") == "dos")
    chk("'patata' es el nombre del local, no un pedido de salsa Mayo Patata",
        "Salsas" not in {k for k, v in _proto.foco_del_menu(_menu_f5, "hola patata").items()}
        or _proto.foco_del_menu(_menu_f5, "hola patata") == _foco_hola)
    chk("una conversacion vacia o None no revienta",
        isinstance(_proto.foco_del_menu(_menu_f5, None), dict))

    # ES ACUMULATIVO: el foco se calcula sobre la conversacion ENTERA, incluidos los
    # mensajes del BOT. Sin esto, el cliente que contesta "si" a mitad del cierre perdia
    # los precios de lo que ya habia pedido.
    _conv_larga = ("hola\nHola! Bienvenid@ a Patata\nquiero 2 churrasco italiano\n"
                   "listo, ¿algo mas?\nno\n¿tu direccion?\nDel Valle 1234\nsi\ndale")
    chk("el foco NO se achica a mitad del pedido: lo pedido en el turno 2 sigue con "
        "precio en el turno 9",
        len(_proto.foco_del_menu(_menu_f5, _conv_larga).get("Churrascos", ())) == 12)
    chk("y si el BOT nombro un producto, ese producto queda expandido aunque el cliente "
        "conteste 'ese'",
        "Chorrillana" in _proto.menu_a_texto(
            _menu_f5, foco=_proto.foco_del_menu(
                _menu_f5, "algo mas?\ntambien tenemos Chorrillana\nese")).split(
            "== TAMBIEN TENEMOS ==")[0])

    # Integracion real: el camino de produccion arma un prompt mas chico que el completo.
    with carpeta_temporal() as tmp_f5:
        conv_f5 = Path(tmp_f5) / "conv_f5.db"
        _sis_f5 = []
        _cs_orig = _proto.construir_sistema

        def _espia_sistema(jefe=False, conversacion=None):
            s = _cs_orig(jefe=jefe, conversacion=conversacion)
            _sis_f5.append(s)
            return s
        _proto.construir_sistema = _espia_sistema
        globals()["construir_sistema"] = _espia_sistema
        try:
            procesar_mensaje("56955557777", "hola, tienen delivery?", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso(["Si, tenemos delivery propio."]),
                             nucleo=NucleoPedidos(db_path=Path(tmp_f5) / "p_f5.db"),
                             conv_db=conv_f5)
            chk("produccion arma el prompt CON la conversacion (no manda el menu completo)",
                bool(_sis_f5) and len(_sis_f5[0]) < len(_cs_orig()))
        finally:
            _proto.construir_sistema = _cs_orig
            globals()["construir_sistema"] = _cs_orig

    print("== 10-ago-2026 (pendiente #7): /salud publica POR QUE fallo la alerta ==")
    with carpeta_temporal() as tmp_err:
        _respaldo_orig_err = RESPALDO_DB
        RESPALDO_DB = Path(tmp_err) / "respaldo_errores.db"
        try:
            _registrar_aviso_jefe("wamid.err1", _norm_telefono(JEFE_NUM), "aviso a jefe1")
            _registrar_aviso_jefe("wamid.err2", _norm_telefono(JEFE_BACKUP), "aviso a jefe2")
            _registrar_aviso_jefe("wamid.err3", _norm_telefono(JEFE_BACKUP), "otro a jefe2")
            registrar_statuses([
                {"msg_id": "wamid.err1", "estado": "leido", "detalle": ""},
                {"msg_id": "wamid.err2", "estado": "fallido",
                 "detalle": "131047 Re-engagement message"},
                {"msg_id": "wamid.err3", "estado": "fallido",
                 "detalle": "131047 Re-engagement message"},
            ])
            r_err = resumen_avisos_jefes()
            chk("el resumen ya no solo CUENTA fallidos: dice el codigo de error de Meta",
                bool(r_err["fallas"]) and r_err["fallas"][0]["codigo"] == "131047")
            chk("y cuantas veces se repitio ese codigo", r_err["fallas"][0]["veces"] == 2)
            chk("dice a QUIEN le falla, por etiqueta",
                r_err["fallas"][0]["destinatario"] == "jefe2")
            chk("y NO publica ningun celular: /salud es un endpoint abierto",
                _norm_telefono(JEFE_BACKUP) not in json.dumps(r_err)
                and _norm_telefono(JEFE_NUM) not in json.dumps(r_err))
            chk("el corte por destinatario contesta '¿jefe2 recibio ALGUNA?'",
                r_err["por_destinatario"]["jefe2"]["entregados"] == 0
                and r_err["por_destinatario"]["jefe2"]["fallidos"] == 2)
            chk("...y que a jefe1 si le llego, y la leyo",
                r_err["por_destinatario"]["jefe1"]["leidos"] == 1)
            chk("los totales de siempre siguen dando lo mismo",
                r_err["enviados"] == 3 and r_err["fallidos"] == 2 and r_err["leidos"] == 1)
        finally:
            RESPALDO_DB = _respaldo_orig_err

    print("== 10-ago-2026: DEBOUNCE -- se espera a que el cliente termine de escribir ==")

    class _ProgramadorFalso:
        """Reloj falso: guarda el callback en vez de dormir, y el test decide cuando
        'vence' llamando a disparar(). Con temporizadores reales cada uno de estos casos
        costaria 8 s, la suite pasaria de 30 s a minutos, y una suite que nadie corre no
        protege nada."""

        def __init__(self):
            self.pendientes = []
            self.cancelados = 0

        def programar(self, segundos, fn):
            prog = self

            class _Handle:
                def cancel(self):
                    prog.cancelados += 1
                    prog.pendientes[:] = [p for p in prog.pendientes if p[1] is not fn]
            self.pendientes.append((segundos, fn))
            return _Handle()

        def disparar(self):
            """Lo que haria el temporizador al vencer el silencio."""
            pend, self.pendientes = list(self.pendientes), []
            for _, fn in pend:
                fn()
            return len(pend)

    def _payload_deb(desde, texto, mid):
        return {"entry": [{"changes": [{"value": {"messages": [
            {"type": "text", "id": mid, "from": desde, "text": {"body": texto}}
        ]}}]}]}

    with carpeta_temporal() as tmp_deb:
        _respaldo_orig_deb = RESPALDO_DB
        RESPALDO_DB = Path(tmp_deb) / "respaldo_debounce.db"
        _prog_orig, _prog = _PROGRAMADOR, _ProgramadorFalso()
        _PROGRAMADOR = _prog
        _wa_orig_deb, _wa_deb = enviar_whatsapp, []
        _ahora_orig_deb = _ahora_chile

        def _espia_deb(telefono, texto, cfg, intentos=2):
            _wa_deb.append((telefono, texto))
            return {"simulado": True}
        enviar_whatsapp = _espia_deb
        try:
            conv_deb = Path(tmp_deb) / "conv_debounce.db"
            nucleo_deb = NucleoPedidos(db_path=Path(tmp_deb) / "pedidos_debounce.db")

            # --- Cuatro mensajes seguidos = UNA llamada al modelo y UNA respuesta ---
            cerebro_deb = _CerebroFalso(["Perfecto, ¿algo mas?"])
            for _i, _t in enumerate(["hola", "quiero 2 churrascos italianos",
                                     "a Los Aromos 456", "efectivo"]):
                procesar_payload_meta(
                    _payload_deb("56912340001", _t, f"wamid.deb{_i}"), CONFIG_DEFAULT,
                    debounce=True, cerebro=cerebro_deb, nucleo=nucleo_deb, conv_db=conv_deb)
            chk("los 4 mensajes NO se contestan al llegar: el cerebro no se llamo ni una vez",
                cerebro_deb.llamadas == 0)
            chk("el cliente no leyo nada todavia (nada de cuatro respuestas atropelladas)",
                not _wa_deb)
            chk("cada mensaje nuevo REINICIA el reloj: queda 1 solo timer vivo, 3 cancelados",
                len(_prog.pendientes) == 1 and _prog.cancelados == 3)
            chk("y ese timer espera los 8 s de 'dictando el pedido'",
                _prog.pendientes[0][0] == DEBOUNCE_PEDIDO_SEG)
            _prog.disparar()
            chk("al callar el cliente, el cerebro se llama UNA sola vez",
                cerebro_deb.llamadas == 1)
            chk("y el cliente recibe UNA sola respuesta",
                len([t for n, t in _wa_deb if n == "56912340001"]) == 1)
            _visto_deb = json.dumps(cerebro_deb.ultimos_mensajes, ensure_ascii=False)
            chk("el modelo vio los 4 mensajes JUNTOS, del primero al ultimo",
                "hola" in _visto_deb and "Los Aromos 456" in _visto_deb
                and "efectivo" in _visto_deb)
            chk("el buffer quedo vacio despues de contestar",
                _tomar_buffer("56912340001") == (None, None, None))

            # --- Un mensaje suelto tambien se contesta (no se queda esperando) ---
            _wa_deb.clear()
            cerebro_solo = _CerebroFalso(["Si, tenemos delivery propio."])
            procesar_payload_meta(
                _payload_deb("56912340002", "tienen delivery?", "wamid.solo"),
                CONFIG_DEFAULT, debounce=True, cerebro=cerebro_solo, nucleo=nucleo_deb,
                conv_db=conv_deb)
            _prog.disparar()
            chk("un mensaje suelto se contesta igual, sin quedarse esperando",
                cerebro_solo.llamadas == 1 and
                any(n == "56912340002" for n, _ in _wa_deb))

            # --- El jefe NO pasa por el buffer ---
            _wa_deb.clear()
            _prog.pendientes.clear()
            cerebro_jefe_deb = _CerebroFalso(["Anotado, jefe."])
            vis_jefe_deb = procesar_payload_meta(
                _payload_deb(JEFE_NUM, "se agoto el chacarero", "wamid.jefe"),
                CONFIG_DEFAULT, debounce=True, cerebro=cerebro_jefe_deb,
                nucleo=nucleo_deb, conv_db=conv_deb)
            chk("el jefe se procesa DE INMEDIATO: un 'envio <monto>' no puede esperar 8 s",
                cerebro_jefe_deb.llamadas == 1 and len(vis_jefe_deb) == 1)
            chk("y no dejo nada agendado ni en el buffer",
                not _prog.pendientes and _tomar_buffer(JEFE_NUM) == (None, None, None))

            # --- La ventana se acorta cuando se espera un si/no (Paso 1) ---
            chk("por defecto la espera es la larga (dictando el pedido)",
                _espera_debounce("56912340003") == DEBOUNCE_PEDIDO_SEG)
            _guardar_etapa("56912340003", ETAPA_CONFIRMACION)
            chk("con la ficha del Paso 1 emitida, la espera baja a 2 s "
                "(hacer esperar 8 s por un 'si' se siente roto)",
                _espera_debounce("56912340003") == DEBOUNCE_CONFIRMACION_SEG)
            _guardar_etapa("56912340003", "")
            chk("y al contestar el cliente la etapa se limpia sola",
                _espera_debounce("56912340003") == DEBOUNCE_PEDIDO_SEG)
            _dcr_orig = _datos_cliente_registrado
            _datos_cliente_registrado = lambda tel: {"nombre": "Ana"}  # noqa: E731,F811
            try:
                chk("un cliente registrado espera 4 s: ya no dicta direccion",
                    _espera_debounce("56912340003") == DEBOUNCE_CONOCIDO_SEG)
            finally:
                _datos_cliente_registrado = _dcr_orig
            chk("_ficha_confirmacion deja la bandera puesta (no se deduce del texto)",
                bool(_ficha_confirmacion(_prev_retiro, "56912340004", CONFIG_DEFAULT))
                and _etapa_de("56912340004") == ETAPA_CONFIRMACION)

            # --- El corte de las 23:50 mira la hora de LLEGADA, no la de proceso ---
            _wa_deb.clear()
            cerebro_corte_deb = _CerebroFalso(["Perfecto, lo anoto."])
            _encolar_entrante("56912340005", "quiero 2 italianos",
                              datetime(2026, 7, 28, 23, 49, 55, tzinfo=TZ_CHILE))
            _ahora_chile = lambda: datetime(2026, 7, 28, 23, 50, 3, tzinfo=TZ_CHILE)  # noqa: E731,F811
            _descargar_buffer("56912340005", CONFIG_DEFAULT, cerebro=cerebro_corte_deb,
                              nucleo=nucleo_deb, conv_db=conv_deb)
            chk("escrito 23:49:55 y procesado 23:50:03: el corte NO lo rechaza",
                cerebro_corte_deb.llamadas == 1)
            chk("y el cliente no recibe el mensaje de 'ya cerramos'",
                not [t for n, t in _wa_deb
                     if n == "56912340005" and t == mensaje_corte_pedidos()])
            # El mismo mensaje, pero escrito DESPUES del corte, si tiene que rebotar.
            _wa_deb.clear()
            cerebro_tarde_deb = _CerebroFalso(["esto no deberia mandarse"])
            _encolar_entrante("56912340006", "quiero 2 italianos",
                              datetime(2026, 7, 28, 23, 51, 0, tzinfo=TZ_CHILE))
            _descargar_buffer("56912340006", CONFIG_DEFAULT, cerebro=cerebro_tarde_deb,
                              nucleo=nucleo_deb, conv_db=conv_deb)
            chk("...pero uno escrito 23:51 SI lo corta el horario (el candado sigue vivo)",
                cerebro_tarde_deb.llamadas == 0)
            _ahora_chile = _ahora_orig_deb  # noqa: F811

            # --- Reinicio del proceso con el buffer lleno: no se pierde nada ---
            _wa_deb.clear()
            cerebro_reinicio = _CerebroFalso(["Perfecto, lo tomo."])
            _encolar_entrante("56912340007", "hola", _ahora_chile())
            _encolar_entrante("56912340007", "2 italianos retiro", _ahora_chile())
            _prog.pendientes.clear()      # el proceso murio: el temporizador se fue con el
            chk("los mensajes siguen en la base aunque el temporizador ya no exista",
                bool(_con_respaldo().execute(
                    "SELECT COUNT(*) FROM buffer_entrante WHERE telefono=?",
                    (_norm_telefono("56912340007"),)).fetchone()[0] == 2))
            _destrabados = vaciar_buffer_al_arrancar(
                CONFIG_DEFAULT, cerebro=cerebro_reinicio, nucleo=nucleo_deb,
                conv_db=conv_deb)
            chk("al arrancar de nuevo, el buffer se vacia procesando lo que quedo",
                _destrabados == 1 and cerebro_reinicio.llamadas == 1)
            chk("y el cliente que escribio antes del reinicio SI recibe respuesta",
                any(n == "56912340007" for n, _ in _wa_deb))
            chk("el buffer queda limpio (no se reprocesa en el proximo arranque)",
                vaciar_buffer_al_arrancar(CONFIG_DEFAULT, cerebro=cerebro_reinicio,
                                          nucleo=nucleo_deb, conv_db=conv_deb) == 0)

            # --- Contador de fragmentacion (10-ago-2026, pedido de Gonza) ---
            # Mide cuanto parte sus mensajes cada cliente, para decidir DESPUES -- con
            # semanas de trafico real -- a quien conviene subirle o bajarle la espera.
            # Hoy nada lee esta tabla para decidir: solo mide.
            def _frag(tel):
                return next((c for c in listar_fragmentacion()
                             if c["telefono"] == _norm_telefono(tel)), None)

            _f_rafaga = _frag("56912340001")
            chk("la rafaga de 4 mensajes quedo anotada como UNA rafaga de 4",
                bool(_f_rafaga) and _f_rafaga["rafagas"] == 1 and _f_rafaga["mensajes"] == 4
                and _f_rafaga["max_rafaga"] == 4 and _f_rafaga["rafagas_fragmentadas"] == 1)
            _f_solo = _frag("56912340002")
            chk("el mensaje suelto tambien cuenta como rafaga, pero NO como fragmentada "
                "(sin ese denominador no se sabe si fragmenta siempre o fragmento una vez)",
                bool(_f_solo) and _f_solo["rafagas"] == 1 and _f_solo["mensajes"] == 1
                and _f_solo["rafagas_fragmentadas"] == 0 and _f_solo["promedio"] == 1)
            _encolar_entrante("56912340008", "hola", _ahora_chile())
            _encolar_entrante("56912340008", "un italiano", _ahora_chile())
            _tomar_buffer("56912340008")
            _encolar_entrante("56912340008", "y una bebida", _ahora_chile())
            _tomar_buffer("56912340008")
            _f_acum = _frag("56912340008")
            chk("dos rafagas del mismo cliente se ACUMULAN en su fila (2 rafagas, 3 "
                "mensajes, la peor de 2)",
                bool(_f_acum) and _f_acum["rafagas"] == 2 and _f_acum["mensajes"] == 3
                and _f_acum["max_rafaga"] == 2 and _f_acum["promedio"] == 1.5)
            _juzgables = [c["telefono"] for c in
                          listar_fragmentacion(minimo_rafagas=MIN_RAFAGAS_PARA_JUZGAR)]
            chk(f"con 2 rafagas todavia no se juzga a nadie (hacen falta "
                f"{MIN_RAFAGAS_PARA_JUZGAR}): escribir partido UNA vez no es fragmentar",
                _norm_telefono("56912340008") not in _juzgables)
            _resumen_frag = resumen_fragmentacion()
            chk("el resumen que sale por /salud no lleva NI UN telefono (es abierto)",
                "5691234" not in json.dumps(_resumen_frag, ensure_ascii=False))
            chk("...y aun asi responde lo que importa: cuantas rafagas y de que tamano",
                _resumen_frag["rafagas"] >= 4 and _resumen_frag["mensajes"] >= 8
                and _resumen_frag["rafaga_mas_larga"] == 4)
            chk("un contador roto NO voltea la respuesta al cliente (solo se pierde el dato)",
                _registrar_fragmentacion("56912340009", 2, None) is None)
        finally:
            RESPALDO_DB = _respaldo_orig_deb
            _PROGRAMADOR = _prog_orig
            enviar_whatsapp = _wa_orig_deb
            _ahora_chile = _ahora_orig_deb

    print("== 26-sep-2026 (bot 2.0, paso 1): rastro por turno de que candado actuo ==")
    with carpeta_temporal() as tmp_ev:
        _respaldo_orig_ev, _wa_orig_ev = RESPALDO_DB, enviar_whatsapp
        RESPALDO_DB = Path(tmp_ev) / "respaldo_ev.db"
        enviar_whatsapp = lambda telefono, texto, cfg, intentos=2: {"simulado": True}
        conv_ev = Path(tmp_ev) / "conv_ev.db"
        try:
            tel_ev = "56955551111"
            for i in range(LIMITE_MENSAJES_ANTIABUSO):
                guardar_turno(tel_ev, "usuario" if i % 2 == 0 else "asistente",
                              f"m{i}", db_path=conv_ev)
            procesar_mensaje(tel_ev, "uno mas", {**CONFIG_DEFAULT},
                             cerebro=_CerebroFalso(["no deberia llamarse"]), conv_db=conv_ev)
            res_ev = resumen_candados(dias=1, conv_db=conv_ev)
            chk("el antiabuso deja UN evento en eventos_candado",
                res_ev["por_candado"].get("antiabuso") == 1)
            chk("el resumen cuenta los turnos reales de clientes (denominador)",
                res_ev["turnos_de_clientes"] == LIMITE_MENSAJES_ANTIABUSO // 2 + 1)
            chk("...y da candados cada 100 turnos",
                res_ev["candados_cada_100_turnos"] is not None)
            _registrar_candado("modalidad_pregunta", tel_ev, "x" * 900)
            chk("el detalle largo se recorta (no infla la base)",
                max(len(f[0] or "") for f in _con_respaldo().execute(
                    "SELECT detalle FROM eventos_candado").fetchall()) <= 300)
            RESPALDO_DB = Path(tmp_ev) / "no_existe" / "roto.db"
            chk("un registro roto NO voltea el turno (falla hacia abierto)",
                _registrar_candado("antiabuso", tel_ev) is None)
        finally:
            RESPALDO_DB = _respaldo_orig_ev
            enviar_whatsapp = _wa_orig_ev

    print("== 26-sep-2026 (caso 47): con 'solo retiro' el saludo no anuncia delivery ==")
    _dem47 = "Hoy estamos con un tiempo aproximado de 30 min para delivery y 10 min para retiro."
    chk("con solo retiro se saca la demora de delivery",
        _proto.sanear_demora_solo_retiro("Hola!\n\n" + _dem47, True)
        == "Hola!\n\nHoy estamos con un tiempo aproximado de 10 min para retiro.")
    chk("sin solo retiro el saludo queda igual (control negativo)",
        _proto.sanear_demora_solo_retiro("Hola!\n\n" + _dem47, False) == "Hola!\n\n" + _dem47)
    chk("la demora del viernes cargado (30-40 min) tambien se saca",
        "delivery" not in _proto.sanear_demora_solo_retiro(
            "tiempo aproximado de 30-40 min para delivery y 15 min para retiro.", True))
    chk("un texto sin demora no se toca", _proto.sanear_demora_solo_retiro("Confirmado. Demora 10 min aprox", True)
        == "Confirmado. Demora 10 min aprox")

    print("== 26-sep-2026 (casos 45 y 42): transferencia avisada y total repetido, por codigo ==")
    chk("'Lista la transferencia' se reconoce", _avisa_que_ya_transfirio("Lista la transferencia"))
    chk("'ya hice la transferencia' se reconoce", _avisa_que_ya_transfirio("Ya hice la transferencia!"))
    chk("'ya transferí' (con tilde) se reconoce", _avisa_que_ya_transfirio("ya transferí"))
    chk("'datos para transferencia?' NO es un aviso", not _avisa_que_ya_transfirio("Datos para transferencia?"))
    chk("'quiero transferir' NO es un aviso", not _avisa_que_ya_transfirio("quiero transferir"))
    chk("'aun no hago la transferencia' NO es un aviso (negacion)",
        not _avisa_que_ya_transfirio("aun no hice la transferencia"))
    _fic = {"rol": "asistente", "texto": "•Pedido: x\n\nTotal con delivery $10.400.\n\n"
            "¿Está bien así, o quieres agregar/modificar algo?"}
    chk("'Cuanto sale total' repite el total de la ficha y sigue la confirmacion",
        _respuesta_total_repetido("Cuanto sale total", [_fic])
        == "El total con delivery es $10.400. ¿Está bien así, o quieres agregar/modificar algo?")
    chk("sin total en el ultimo turno del bot, no responde (lo ve el modelo)",
        _respuesta_total_repetido("Cuanto sale total", [{"rol": "asistente", "texto": "Hola"}]) is None)
    chk("'cuanto sale la promo' NO es pedir el total",
        _respuesta_total_repetido("cuanto sale la promo", [_fic]) is None)
    with carpeta_temporal() as tmp_t45:
        _resp_orig45, _wa_orig45 = RESPALDO_DB, enviar_whatsapp
        RESPALDO_DB = Path(tmp_t45) / "respaldo45.db"
        enviar_whatsapp = lambda telefono, texto, cfg, intentos=2: {"simulado": True}
        conv45 = Path(tmp_t45) / "conv45.db"
        try:
            tel45 = "56955552222"
            guardar_turno(tel45, "usuario", "3 italianos retiro transferencia", db_path=conv45)
            guardar_turno(tel45, "asistente", "Confirmado. Demora 10 min aprox", db_path=conv45)
            cer45 = _CerebroFalso(["ERROR: no deberia llamarse"])
            v45 = procesar_mensaje(tel45, "Lista la transferencia", {**CONFIG_DEFAULT},
                                   cerebro=cer45, conv_db=conv45)
            chk("SIN pedido retenido, 'lista la transferencia' sigue al modelo (control negativo)",
                cer45.llamadas == 1)
            _guardar_esperando_comprobante({"cliente": "X", "items": []}, tel45)
            cer45b = _CerebroFalso(["ERROR: no deberia llamarse"])
            v45b = procesar_mensaje(tel45, "Lista la transferencia", {**CONFIG_DEFAULT},
                                    cerebro=cer45b, conv_db=conv45)
            chk("CON pedido retenido, responde el codigo y NO llama al modelo",
                cer45b.llamadas == 0 and v45b == MENSAJE_TRANSFERENCIA_AVISADA)
            chk("...y queda en el historial lo que el cliente leyo",
                historial(tel45, db_path=conv45)[-1]["texto"] == MENSAJE_TRANSFERENCIA_AVISADA)
            chk("...y el candado queda registrado",
                resumen_candados(dias=1, conv_db=conv45)["por_candado"].get("transferencia_avisada") == 1)
            tel42 = "56955553333"
            guardar_turno(tel42, "usuario", "2 churrascos delivery efectivo", db_path=conv45)
            guardar_turno(tel42, "asistente", _fic["texto"], db_path=conv45)
            cer42 = _CerebroFalso(["ERROR: no deberia llamarse"])
            v42 = procesar_mensaje(tel42, "Cuanto sale total", {**CONFIG_DEFAULT},
                                   cerebro=cer42, conv_db=conv45)
            chk("caso 42: el total ya dado lo repite el codigo, sin modelo",
                cer42.llamadas == 0 and v42 and "10.400" in v42 and "sistema" not in v42)
        finally:
            RESPALDO_DB = _resp_orig45
            enviar_whatsapp = _wa_orig45

    print("== 29-sep-2026 (caso 93 del curador + pedido de Gonza): forma de pago ya no "
          "frena la ficha, y el vuelto solo se pregunta en delivery ==")
    chk("previsualizar_pedido acepta forma_pago null en su schema",
        _proto.TOOL_PREVISUALIZAR_PEDIDO["function"]["parameters"]["properties"]
        ["forma_pago"]["type"] == ["string", "null"])
    chk("...pero emitir_pedido (Paso 2) sigue exigiendo una forma de pago real",
        _proto.TOOL_EMITIR_PEDIDO["function"]["parameters"]["properties"]
        ["forma_pago"]["type"] == "string")
    _prev_sin_pago = {**_prev_retiro, "forma_pago": None}
    _f_sin_pago = _ficha_confirmacion(_prev_sin_pago, "56911112222", CONFIG_DEFAULT) or ""
    chk("sin forma de pago, la ficha NO muestra la linea a medias",
        "•Forma de pago" not in _f_sin_pago)
    chk("...y la pregunta de pago sale junto con la confirmacion, en el MISMO mensaje",
        "¿Está bien así" in _f_sin_pago and "¿Y cómo prefieres pagar" in _f_sin_pago
        and "efectivo, tarjeta o transferencia" in _f_sin_pago)
    chk("...con el total igual visible, para que el cliente decida viendo el numero real",
        "Total $8.900." in _f_sin_pago)
    _prev_deliv_sin_pago = {**_prev_delivery, "forma_pago": None}
    _f_deliv_sin_pago = _ficha_confirmacion(
        _prev_deliv_sin_pago, "56911112222", CONFIG_DEFAULT) or ""
    chk("lo mismo en delivery: ficha sin la linea de pago y con la pregunta combinada",
        "•Forma de pago" not in _f_deliv_sin_pago
        and "¿Y cómo prefieres pagar" in _f_deliv_sin_pago)
    # Caso 93 real: retiro + efectivo -> Christian marco como error que se preguntara el
    # vuelto ("si es retiro no importa llevar vuelto, el cliente se acerca a la caja").
    _prev_retiro_efectivo = {**_prev_retiro, "forma_pago": "efectivo"}
    _f_retiro_efectivo = _ficha_confirmacion(
        _prev_retiro_efectivo, "56911112222", CONFIG_DEFAULT) or ""
    chk("caso 93: retiro + efectivo -> NO se pregunta por el vuelto",
        "vuelto" not in _f_retiro_efectivo)
    chk("...la ficha si trae la forma de pago (ya la sabiamos)",
        "•Forma de pago: Efectivo" in _f_retiro_efectivo)
    chk("...y la confirmacion queda simple, sin la pregunta combinada de pago",
        "¿Está bien así, o quieres agregar/modificar algo?" in _f_retiro_efectivo
        and "¿Y cómo prefieres pagar" not in _f_retiro_efectivo)
    # Control positivo: delivery + efectivo SI pregunta el vuelto (no se rompio el 60).
    _prev_deliv_efectivo = {**_prev_delivery, "forma_pago": "efectivo"}
    _f_deliv_efectivo = _ficha_confirmacion(
        _prev_deliv_efectivo, "56911112222", CONFIG_DEFAULT) or ""
    chk("control: delivery + efectivo SI pregunta por el vuelto (caso 60 no se rompio)",
        "¿con cuánto pagas" in _f_deliv_efectivo)

    print()
    if all(ok):
        print("TODO OK")
    else:
        print(">>> HAY FALLAS <<<")
        sys.exit(1)

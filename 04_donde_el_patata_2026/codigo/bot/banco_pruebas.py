#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
banco_pruebas.py — El banco de regresion del bot (F3 del PLAN_AUDITORIA_PROMPT).
================================================================================

QUE RESUELVE
------------
`webhook.py --test` (423 aserciones) prueba el CODIGO con un cerebro FALSO: verifica
los candados, no si el bot atiende bien. El curador de prompts responde "¿sigue
fallando ESTE caso puntual?". Faltaba lo tercero, y es lo que frena la poda del
prompt: **"¿rompi algo mas?"**.

Este banco corre conversaciones REALES (destiladas de `Conversaciones de ejemplo/`)
contra el **modelo de verdad**, y mide el RESULTADO: que pedido llego a cocina, con
que items, que total, que modalidad, que envio. Da un numero ("9/10") comparable
antes y despues de tocar el prompt.

    python bot/banco_pruebas.py                      # corrida completa
    python bot/banco_pruebas.py --escenario retiro_simple
    python bot/banco_pruebas.py --repeticiones 2     # mide estabilidad
    python bot/banco_pruebas.py --json base.json     # guarda la linea base

QUE NO TOCA (mismo blindaje que --test, ver _neutralizar_efectos)
-----------------------------------------------------------------
Base de pedidos, cache de menu, cache de clientes y cola de respaldo van a carpetas
temporales; `enviar_whatsapp` queda reemplazado por un espia. **Ningun mensaje sale
a WhatsApp y ninguna comanda llega al POS de Christian.** Lo unico real que sale a
internet es la llamada a OpenAI.

DOS DECISIONES QUE HAY QUE SABER PARA LEER EL NUMERO
----------------------------------------------------
1. **Temperatura 0.** En produccion el bot corre con la temperatura por defecto (1).
   Un banco a temperatura 1 da un numero distinto cada corrida y no sirve para
   comparar antes/despues de una poda. El precio de esa estabilidad es que el banco
   mide una version levemente mas deterministica que la que atiende a los clientes:
   sirve para comparar DOS PROMPTS entre si, no para predecir la tasa de acierto
   real. Con `--temperatura -1` se corre igual que produccion.
2. **Sin modelos de respaldo.** `CerebroOpenAI` cae a gpt-4o-mini si el primario da
   429/404. En produccion eso es resiliencia; en un banco es veneno — mediria otro
   modelo sin avisar y la comparacion no querria decir nada. Aca la corrida FALLA
   ruidosa en vez de cambiar de modelo en silencio.

COMO SE PUNTUA
--------------
Cada escenario tiene aserciones **duras** (el resultado: pedido, total, modalidad,
envio, nombre) — son las que cuentan para el numero. Aparte se reportan los
**empujones**: cuantos turnos extra de confirmacion necesito el bot para cerrar algo
que el cliente ya habia dicho entero. Los empujones NO restan puntos, pero son la
medida de "se puso mas pregunton"; si suben tras una poda, el prompt se rompio de
una forma que las aserciones duras no ven.

Creado 2026-08-06 (Alma, modo fable). F3 del plan de auditoria del prompt.
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PROYECTO = AQUI.parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(PROYECTO / "pos"))

import prototipo                      # noqa: E402
import webhook                        # noqa: E402
from nucleo import NucleoPedidos      # noqa: E402


# ===========================================================================
# Presupuesto: que una prueba no se coma el saldo de la cuenta
# ===========================================================================
# 07-oct-2026: midiendo variantes del prompt se hicieron ~2.700 llamadas (~25 M de tokens,
# del orden de USD 12-20) con la key DEDICADA del bot y se acabo el saldo: 429 "You have
# no credits remaining". Esa key es la que usa webhook.py en produccion (dedicado=True),
# asi que si la local es la de Railway, una prueba puede dejar al bot sin responderle a
# clientes reales. El banco antes reintentaba ese 429 como si fuera el tope por minuto y
# no tenia ningun limite de llamadas. Ahora: costo estimado a la vista, tope de llamadas
# que solo se sube a proposito, y parada inmediata sin saldo.
LLAMADAS_POR_ESCENARIO = 4.2        # medido: ~63 llamadas por corrida de 15 escenarios
USD_POR_LLAMADA = 0.004             # orden de magnitud (gpt-4.1-mini, prompt de ~9k tokens)
TOPE_LLAMADAS_SIN_AUTORIZAR = 150   # ~2 corridas completas; mas que esto pide --max-llamadas
_FRASES_SIN_SALDO = ("no credits remaining", "insufficient_quota", "exceeded your current quota")


import gasto_pruebas
from gasto_pruebas import PresupuestoAgotado   # tope del dia (persistente), de la ejecucion o sin saldo


_gasto = {"llamadas": 0, "tope": TOPE_LLAMADAS_SIN_AUTORIZAR}


def estimar_costo(n_escenarios, repeticiones):
    llamadas = round(n_escenarios * repeticiones * LLAMADAS_POR_ESCENARIO)
    return llamadas, llamadas * USD_POR_LLAMADA


# ===========================================================================
# Cerebro del banco
# ===========================================================================
class CerebroBanco(prototipo.CerebroOpenAI):
    """El cerebro de produccion, con las dos diferencias explicadas en el encabezado:
    sin respaldo silencioso a otro modelo, y con temperatura fija."""

    MODELOS_RESPALDO = []          # <- si el primario no responde, que se note
    REINTENTOS = 4

    def __init__(self, modelo="gpt-4.1-mini", temperatura=0.0, semilla=7):
        super().__init__(modelo=modelo, dedicado=False)
        self.temperatura = temperatura
        self.semilla = semilla
        self.llamadas = 0
        self.segundos = 0.0
        self.errores_api = 0        # errores que se recuperaron reintentando
        self.errores_fatales = 0    # errores que NO se recuperaron -> corrida invalida

    def pensar(self, mensajes, tools=None, tool_choice=None):
        """Reintenta el MISMO modelo ante un error de la API.

        Por que hace falta (medido el 6-ago-2026): tres corridas seguidas del banco
        dieron 8, 7 y 4. La caida no era el bot portandose peor, era el tope de tokens
        por minuto de la cuenta: el prompt son ~9k tokens por turno y el banco hace ~40
        llamadas en 100 segundos. `CerebroOpenAI` devuelve el 429 como un string
        "[ERROR ...]", el bot le manda al cliente su mensaje humano de falla, no cierra
        ningun pedido -- y el banco lo anotaba como "el bot atendio mal". Un banco que
        puntua la cuota de la API en vez del prompt no mide nada."""
        respuesta = None
        for intento in range(self.REINTENTOS):
            if _gasto["llamadas"] >= _gasto["tope"]:
                raise PresupuestoAgotado(
                    f"[banco] tope de {_gasto['tope']} llamadas alcanzado: me detengo. "
                    f"Si de verdad hace falta mas, repite con --max-llamadas <N> y avisa el costo.")
            gasto_pruebas.autorizar()          # ANTES de la llamada: la que no se hace no se paga
            self.llamadas += 1
            _gasto["llamadas"] += 1
            t0 = time.time()
            respuesta = super().pensar(mensajes, tools=tools, tool_choice=tool_choice)
            gasto_pruebas.registrar(1)
            self.segundos += time.time() - t0
            texto = respuesta[0] if isinstance(respuesta, tuple) else respuesta
            if not str(texto or "").startswith("[ERROR"):
                return respuesta
            if any(f in str(texto).lower() for f in _FRASES_SIN_SALDO):
                raise PresupuestoAgotado(
                    "[banco] SIN SALDO en la cuenta de OpenAI de esta key: paro todo y no "
                    "reintento. Si es la key de produccion, el bot puede estar sin responder: "
                    "recargar en platform.openai.com/settings/organization/billing.")
            self.errores_api += 1
            espera = 10 * (intento + 1)
            print(f"   [banco] la API fallo, espero {espera}s y reintento "
                  f"({intento + 1}/{self.REINTENTOS}): {str(texto)[:110]}")
            time.sleep(espera)
        self.errores_fatales += 1
        return respuesta


# ===========================================================================
# Blindaje: que el banco no toque nada real
# ===========================================================================
# Martes 28-jul-2026, 19:00 en Chile: local abierto, dia normal (no viernes, asi que
# los tiempos estimados son los de siempre). Todo el banco corre en ese instante.
RELOJ = datetime(2026, 7, 28, 19, 0, tzinfo=prototipo.TZ_CHILE)


def _neutralizar_efectos(base_tmp):
    """Replica el blindaje de `webhook.py --test`, que vive dentro de su __main__ y
    por eso no se puede reutilizar importandolo. Si alguno de estos swaps falta, el
    banco escribe en la base/cache REAL de este PC."""
    prototipo.MENU_CACHE = base_tmp / "menu_cache_inexistente.json"
    prototipo.CLIENTES_CACHE = base_tmp / "clientes_cache.json"
    webhook.RESPALDO_DB = base_tmp / "respaldo_global.db"

    # --- Reloj congelado, en los DOS lugares que lo miran ---
    # Esto se aprendio corriendo el banco: patchear solo `webhook.estado_horario` (que es
    # lo que hace --test) silencia el CANDADO de horario pero no el PROMPT. El texto
    # "Hoy es X. Estado: ABIERTO/CERRADO" lo arma `prototipo.construir_sistema` llamando
    # al `estado_horario` de SU modulo. Corriendo el banco a las 09:00, el candado dejaba
    # pasar pero el modelo leia "CERRADO" y contestaba "el local esta cerrado ahora" ->
    # el banco medía la hora del PC, no el prompt.
    _estado_real, _tiempos_real = prototipo.estado_horario, prototipo.tiempos_estimados
    prototipo.estado_horario = lambda menu, ahora=None: _estado_real(menu, RELOJ)
    prototipo.tiempos_estimados = lambda ahora=None: _tiempos_real(RELOJ)
    webhook.estado_horario = prototipo.estado_horario
    webhook._ahora_chile = lambda: RELOJ


class Espia:
    """Reemplaza enviar_whatsapp. Guarda todo lo que el bot HABRIA mandado.
    Hace falta de verdad: el total que el cliente lee ('Total: $10.400') y el aviso
    del envio no salen por el return de procesar_mensaje, salen por aca."""

    def __init__(self):
        self.mensajes = []

    def __call__(self, telefono, texto, cfg, intentos=2):
        self.mensajes.append((webhook._norm_telefono(telefono), texto or ""))
        return {"simulado": True}


# ===========================================================================
# Contexto que reciben las aserciones de cada escenario
# ===========================================================================
class Resultado:
    def __init__(self, telefono, visibles, cola, mensajes):
        self.telefono = webhook._norm_telefono(telefono)
        self.visibles = visibles              # lo que devolvio procesar_mensaje, turno a turno
        self.cola = cola                      # pedidos que llegaron a cocina
        self._mensajes = mensajes

    # --- pedidos ---
    @property
    def pedidos(self):
        return len(self.cola)

    @property
    def unico(self):
        """El pedido, si hay exactamente uno. None si hay cero o varios -- asi una
        asercion sobre el total nunca 'pasa' mirando el pedido equivocado."""
        return self.cola[0] if len(self.cola) == 1 else None

    def campo(self, nombre, default=None):
        p = self.unico
        return default if p is None else p.get(nombre, default)

    # --- lo que leyo el cliente / lo que leyeron los jefes ---
    @property
    def al_cliente(self):
        return "\n".join(t for tel, t in self._mensajes if tel == self.telefono)

    @property
    def a_jefes(self):
        jefes = {webhook._norm_telefono(n) for n in webhook.ROLES_JEFE}
        return "\n".join(t for tel, t in self._mensajes if tel in jefes)

    def dice(self, *fragmentos):
        """True si el cliente leyo TODOS los fragmentos (sin importar mayusculas)."""
        txt = self.al_cliente.lower()
        return all(f.lower() in txt for f in fragmentos)

    def dice_monto(self, monto):
        """El monto, escrito como sea que lo escriba el modelo ($8.900 / 8900 / 8,900)."""
        txt = self.al_cliente
        return any(v in txt for v in (f"{monto:,}".replace(",", "."), f"{monto:,}", str(monto)))


def plata(n):
    return f"${n:,}".replace(",", ".")


# ===========================================================================
# Registro del idioma (10-ago-2026, pedido de Gonza: "neutro y formal sencillo")
# ---------------------------------------------------------------------------
# El bot hablaba mezclando voseo chileno y argentino porque el BLOQUE 2 le pedia
# "chileno" y "nada formal", y varios EJEMPLOS del prompt estaban escritos asi. Como
# el registro es una propiedad de TODAS las respuestas y no de un escenario, este
# chequeo se agrega a los trece (ver correr_escenario).
# ===========================================================================
# Se buscan como PALABRA COMPLETA: "dale" no puede saltar dentro de "regalale", ni
# "po" dentro de "poco" o "pote".
JERGA_PALABRAS = ("cachai", "cachái", "querís", "queris", "tenís", "po", "bacan",
                  "bacán", "filo", "weon", "weón", "fome",
                  "dale", "tenés", "tenes", "querés", "queres", "che")
# Estas van como frase: sueltas darian falsos positivos ("la raja" vs "raja").
JERGA_FRASES = ("al tiro", "ya po", "si po", "la raja", "mira vos")


def jerga_en(texto):
    """Los modismos que aparecen en lo que LEYO el cliente. Lista vacia = habla neutro."""
    t = (texto or "").lower()
    hallados = [p for p in JERGA_PALABRAS if re.search(rf"\b{re.escape(p)}\b", t)]
    hallados += [f for f in JERGA_FRASES if f in t]
    return sorted(set(hallados))


# ===========================================================================
# El "aparte" NO SE NIEGA (corregido por Gonza, 10-ago-2026 por la noche)
# ---------------------------------------------------------------------------
# La primera version de este candado exigia que el bot DIJERA QUE NO, y estaba al
# reves. El prompt tambien lo estaba, desde antes: decia "NO se manda NADA aparte".
#
# La regla real del local, dicha por Gonza: **el agregado aparte SI se envia.** Lo
# que no puede es entrar como COMENTARIO en la comanda, porque asi sale gratis. O
# sea es un ITEM COBRADO, y el bot no tiene absolutamente nada que negar.
#
# Por eso lo que se mide aca es que el cliente NO lea un no. La lista es angosta a
# proposito: con el candado invertido, un falso positivo reprueba una respuesta
# correcta, asi que solo entran frases que hablan de NO MANDAR, no cualquier "no".
# ===========================================================================
# "no manda" (sin la S) cubre "no mandamos" y "el local no manda nada aparte".
NEGATIVAS_APARTE = ("aparte no", "no manda", "no se manda", "no envia", "no envía",
                    "no se envia", "no se envía", "no se puede mandar",
                    "no se puede enviar")
# La misma negativa disfrazada de limitacion del bot.
NEGATIVAS_EXCUSA = ("no te la puedo", "no te lo puedo", "no me deja", "no puedo mandar",
                    "no puedo enviar")


def dice_que_no_al_aparte(texto):
    """True si el bot le NEGO el aparte al cliente. Ahora eso es una FALLA."""
    t = (texto or "").lower()
    return (any(f in t for f in NEGATIVAS_EXCUSA)
            or any(f in t for f in NEGATIVAS_APARTE))


# ===========================================================================
# LOS ESCENARIOS
# ---------------------------------------------------------------------------
# Todos salen de conversaciones reales del local (bot/Conversaciones de ejemplo/).
# Se conserva la forma de escribir del cliente real -- minusculas, sin tildes,
# "vianessa", el pedido y la direccion en mensajes separados: el prompt tiene que
# aguantar eso, no un castellano de laboratorio.
# ===========================================================================
ESCENARIOS = [
    {
        "id": "retiro_simple",
        "que_prueba": "El camino feliz: el cliente manda todo junto y el pedido llega a cocina bien.",
        "origen": "conversacion real #3 (Oscar, 1-jul-2026)",
        "telefono": "56990000001",
        "turnos": [
            "Holas buenas tardes",
            "Quiero hacer un pedido",
            "Oscar\nRetiro\nUn churrasco italiano\nEfectivo",
        ],
        "empujon": "si, confirmo",
        "duras": lambda r: [
            ("llega exactamente 1 pedido a cocina", r.pedidos == 1),
            ("modalidad retiro", r.campo("modalidad") == "retiro"),
            ("forma de pago efectivo", r.campo("forma_pago") == "efectivo"),
            (f"total {plata(4900)}", r.campo("total") == 4900),
            ("la comanda sale a nombre de Oscar", "oscar" in str(r.campo("cliente", "")).lower()),
        ],
    },
    {
        "id": "delivery_planilla",
        "que_prueba": ("El envio lo pone el CODIGO desde la planilla, no el modelo. Las Moras "
                       "6109 cuesta $2.000; si el bot dijera la tarifa minima ($1.500) le "
                       "estaria regalando $500 al cliente en cada pedido de ese tramo."),
        "origen": "conversacion real #11 (Dafne, 22-jul-2026)",
        "telefono": "56990000002",
        "turnos": [
            "Hola buenasb",
            "Estan trabajando?",
            "Me envia 3 churrasco italiano",
            "A las moras 6109",
            "Con tarjeta",
        ],
        "empujon": "si, confirmo",
        "duras": lambda r: [
            ("llega exactamente 1 pedido a cocina", r.pedidos == 1),
            ("modalidad delivery", r.campo("modalidad") == "delivery"),
            ("la direccion queda registrada", "moras" in str(r.campo("direccion", "")).lower()),
            (f"envio {plata(2000)} (el de la planilla, NO la tarifa minima)",
             r.campo("delivery_cobro") == 2000),
            (f"total {plata(15800)} = 3 italianos {plata(13800)} + envio {plata(2000)}",
             r.campo("total") == 15800),
            (f"el cliente LEE el total {plata(15800)}", r.dice_monto(15800)),
        ],
    },
    {
        "id": "promo_2x_mixta",
        "que_prueba": ("Regla de negocio de Christian que ningun codigo generico adivina: en un "
                       "2x de productos DISTINTOS, el par se cobra al precio_2x del mas caro."),
        "origen": "regla confirmada por Christian (duenno del local)",
        "telefono": "56990000003",
        "turnos": [
            "hola",
            "dos churrascos, uno completo y uno dinamico, para retiro, pago con tarjeta",
        ],
        "empujon": "si, confirmo",
        "duras": lambda r: [
            ("llega exactamente 1 pedido a cocina", r.pedidos == 1),
            (f"el par mixto se cobra {plata(9200)} (2x del Dinamico, el mas caro), "
             f"no {plata(8900)} ni {plata(10100)}", r.campo("total") == 9200),
        ],
    },
    {
        "id": "consulta_sin_pedido",
        "que_prueba": ("Preguntar precios NO es pedir. Nada tiene que llegar a cocina, y los "
                       "precios que diga tienen que ser los del menu real."),
        "origen": "conversacion real #10 (4-mar-2026)",
        "telefono": "56990000004",
        "espera_pedido": False,
        "turnos": [
            "holaa",
            "tiene churrasco italiano y barros luco?",
            "valores?",
        ],
        "duras": lambda r: [
            ("NO se crea ningun pedido", r.pedidos == 0),
            (f"da un precio real del menu ({plata(4900)} o {plata(5200)})",
             r.dice_monto(4900) or r.dice_monto(5200)),
        ],
    },
    {
        "id": "cliente_conocido",
        "que_prueba": ("El cliente registrado no tiene que repetir quien es ni donde vive, y su "
                       "nombre REAL tiene que llegar a la comanda (pendiente #10)."),
        "origen": "conversacion real #11 — Christian: 'esta persona se llama Dafne'",
        "telefono": "56990000005",
        "cliente_registrado": {"nombre": "Dafne", "direccion": "Las Moras 6109"},
        "turnos": [
            "Hola buenas",
            "Me envia 2 churrasco italiano a la direccion de siempre, con tarjeta",
        ],
        "empujon": "si, esa misma, confirmo",
        "duras": lambda r: [
            ("llega exactamente 1 pedido a cocina", r.pedidos == 1),
            ("la comanda lleva el nombre real (Dafne), no un placeholder",
             "dafne" in str(r.campo("cliente", "")).lower()),
            ("usa la direccion guardada sin pedirsela de nuevo",
             "moras" in str(r.campo("direccion", "")).lower()),
            (f"total {plata(10900)} = promo 2x {plata(8900)} + envio {plata(2000)}",
             r.campo("total") == 10900),
        ],
    },
    {
        "id": "vienesa_xl_sin_preguntar_debito",
        "que_prueba": ("Dos cosas: que 'italiano grande vianesa' se tarifique como Vienesa XL en "
                       "promo, y que el bot NO pregunte debito o credito — el defecto que "
                       "reporto Christian y que el local no guarda en ningun campo."),
        "origen": "conversaciones reales #6 y #8 (24-jul y 29-mar-2026)",
        "telefono": "56990000006",
        "turnos": [
            "holaa",
            "tiene italianos grandes ?vianesa",
            "dos grandes porfavor, para retiro",
            "debito",
        ],
        "empujon": "si, confirmo",
        "duras": lambda r: [
            ("llega exactamente 1 pedido a cocina", r.pedidos == 1),
            (f"promo Vienesa Italiano XL 2x = {plata(5700)}", r.campo("total") == 5700),
            ("forma de pago tarjeta", r.campo("forma_pago") == "tarjeta"),
            ("NO pregunta si la tarjeta es debito o credito",
             "credito" not in r.al_cliente.lower() and "crédito" not in r.al_cliente.lower()),
        ],
    },
    {
        "id": "pedido_grande",
        "que_prueba": "Un pedido de 10+ por linea no entra solo a cocina: lo mira una persona.",
        "origen": "candado_pedido_grande (28-jul-2026)",
        "telefono": "56990000007",
        "espera_pedido": False,
        "turnos": [
            "Hola",
            "Necesito 12 churrascos italianos para retiro, pago efectivo",
            "si, confirmo",
        ],
        "duras": lambda r: [
            ("NO entra solo a cocina", r.pedidos == 0),
            ("los jefes se enteran", len(r.a_jefes.strip()) > 0),
        ],
    },
    {
        "id": "confirma_dos_veces",
        "que_prueba": ("El cliente que confirma dos veces no genera dos comandas "
                       "(candado de pedido duplicado, 5-ago-2026)."),
        "origen": "pendiente #9 del proyecto",
        "telefono": "56990000008",
        "turnos": [
            "Hola",
            "2 churrascos italianos, retiro, tarjeta",
            "si, confirmo",
            "si, confirmo",
            "confirmado porfa",
        ],
        "duras": lambda r: [
            ("sale UNA sola comanda, no dos", r.pedidos == 1),
            (f"y con el total correcto {plata(8900)}", r.campo("total") == 8900),
        ],
    },
    {
        "id": "calle_fuera_de_planilla",
        "que_prueba": ("Una calle que no esta en la planilla no se tarifica a ojo: el pedido "
                       "espera a que un jefe diga el envio, y el cliente no lee un precio falso."),
        "origen": "delivery_zonas.cobro_por_calle (3-ago-2026)",
        "telefono": "56990000009",
        "espera_pedido": False,
        "turnos": [
            "Hola",
            "2 churrascos italianos delivery a Pasaje Que No Existe 999, con tarjeta",
            "si, confirmo",
        ],
        "duras": lambda r: [
            ("no entra solo a cocina con un envio inventado", r.pedidos == 0),
            ("se le escala a los jefes para que pongan el envio",
             len(r.a_jefes.strip()) > 0),
        ],
    },
    {
        "id": "ficha_con_precios",
        "que_prueba": ("LA regresion mas cara del proyecto: el Paso 1 del cierre tiene que "
                       "mostrar QUE pidio y CUANTO sale. Durante un dia entero de agosto el "
                       "candado de montos se comia esa frase y el cliente leia solo "
                       "'¿todo bien asi?' — confirmando un pedido que nunca vio."),
        "origen": "pendiente #8(c) — bug real en produccion, 4/5-ago-2026",
        "telefono": "56990000010",
        "espera_pedido": False,
        "turnos": [
            "Hola",
            "2 churrascos italianos, retiro, pago con tarjeta",
        ],
        "duras": lambda r: [
            (f"el cliente LEE el total ({plata(8900)}) antes de confirmar", r.dice_monto(8900)),
            ("el cliente LEE que son churrascos", r.dice("churrasco")),
            ("el mensaje de confirmacion no quedo en un muñon de 3 palabras",
             len(r.al_cliente.strip()) > 40),
            ("todavia NO fue a cocina (el Paso 1 no cocina)", r.pedidos == 0),
        ],
    },
    # -----------------------------------------------------------------------
    # 10-ago-2026: los tres de abajo cubren reglas escritas ESE dia. Antes de
    # existir, el banco solo sabia decir "no rompiste nada": ninguno de los diez
    # escenarios de arriba toca la palabra "completo", la ventana de despues del
    # cierre ni un ingrediente pedido aparte.
    # -----------------------------------------------------------------------
    {
        "id": "completo_italiano",
        "que_prueba": ("'Completo' es la palabra mas ambigua del menu chileno: nombra la BASE "
                       "(cualquier pan con vienesa), no el sabor. 'Un completo italiano' es UNA "
                       "Vienesa Italiano -- no una Vienesa Completo, que es otro sabor, ni dos "
                       "sanguches. Caso real: el bot se enredo con este mismo pedido."),
        "origen": "reportado por Gonza el 10-ago-2026 (pedido real de un cliente)",
        "telefono": "56990000011",
        "turnos": [
            "hola",
            "me das un completo italiano",
            "normal, para retiro",
            "efectivo",
        ],
        "empujon": "si, confirmo",
        "duras": lambda r: [
            ("llega exactamente 1 pedido a cocina", r.pedidos == 1),
            ("el sabor es Italiano, NO el sabor Completo (chucrut y americana)",
             any("italiano" in str(i.get("producto", "")).lower()
                 for i in (r.campo("items") or []))),
            ("la base es Vienesa (no Churrasco ni As)",
             any("vienesa" in str(i.get("producto", "")).lower()
                 for i in (r.campo("items") or []))),
            ("es UN sanguche, no dos: no partio 'completo' + 'italiano' en dos items",
             sum(int(i.get("cantidad", 1) or 1) for i in (r.campo("items") or [])) == 1),
            ("tamaño normal 22cm, no XL",
             any("normal" in str(i.get("variante", "") or "")
                 for i in (r.campo("items") or []))),
            (f"total {plata(2700)}", r.campo("total") == 2700),
        ],
    },
    {
        "id": "emoji_no_es_pedido",
        "que_prueba": ("La ventana de despues del cierre (regla 3.10): cerrado el pedido, un "
                       "emoji o un 'gracias' es una DESPEDIDA. El bot contesta corto y no "
                       "arranca otro pedido ni vuelve a pedir los datos."),
        "origen": "pedido de Gonza, 10-ago-2026",
        "telefono": "56990000012",
        "turnos": [
            "hola",
            "un churrasco italiano para retiro, pago efectivo",
            "si, confirmo",
            "👍",
            "gracias!! 🙏",
        ],
        "empujon": "si, confirmo",
        "duras": lambda r: [
            ("sale UNA sola comanda: el emoji no abrio un pedido nuevo", r.pedidos == 1),
            ("la despedida es CORTA, no repite la ficha",
             len((r.visibles[-1] or "").strip()) <= 160),
            ("no vuelve a preguntar modalidad ni forma de pago",
             not any(p in (r.visibles[-1] or "").lower()
                     for p in ("retiro o delivery", "como pagas", "forma de pago",
                               "¿retiro", "para retiro o"))),
        ],
    },
    {
        "id": "mayo_aparte",
        "que_prueba": ("El aparte SI se manda, pero como ITEM COBRADO y nunca como comentario "
                       "en la comanda (asi saldria gratis). 'Italiano con la mayo aparte' se "
                       "arma como el italiano SIN mayo (Vienesa Tomate/Palta) + un pote de Mayo "
                       "Patata cobrado. El bot NO tiene que negar nada: acepta y dice el precio."),
        "origen": "regla del local dictada por Gonza el 10-ago-2026 (corregida esa misma noche: "
                  "la primera version decia que el aparte no se podia, y era falso)",
        "telefono": "56990000013",
        # El "justo" no es relleno: pagando en efectivo el bot pregunta con cuanto viene
        # (para el vuelto), y sin esa respuesta el guion se comia los dos empujones
        # contestando otra cosa y el pedido no cerraba. Un cliente real contesta eso.
        "turnos": [
            "hola",
            "quiero un vienesa italiano normal pero con la mayo aparte",
            "retiro, efectivo",
            "justo",
        ],
        "empujon": "si, confirmo",
        "duras": lambda r: [
            ("llega exactamente 1 pedido a cocina", r.pedidos == 1),
            ("el sanguche va SIN mayo (Tomate/Palta), no como Italiano",
             any("tomate" in str(i.get("producto", "")).lower()
                 for i in (r.campo("items") or []))),
            ("la mayo va COBRADA como item de Salsas, no regalada en una nota",
             any("salsa" in str(i.get("categoria", "")).lower()
                 for i in (r.campo("items") or []))),
            (f"total {plata(3200)} = sanguche {plata(2700)} + pote {plata(500)}",
             r.campo("total") == 3200),
            ("el bot NO le niega el aparte: se puede, y no hay nada que negar",
             not dice_que_no_al_aparte(r.al_cliente)),
            ("y le dice el precio del pote en el mismo mensaje, no despues",
             r.dice("500")),
        ],
    },
    {
        "id": "lenguaje_neutro",
        "que_prueba": ("El cliente escribe en jerga cerrada y el bot NO se le acomoda: "
                       "contesta en neutro. Espejar el dialecto del cliente es justo lo que "
                       "Gonza pidio sacar el 10-ago; el bot suena a persona por ser breve y "
                       "claro, no por hablar en dialecto."),
        "origen": "pedido de Gonza, 10-ago-2026: 'neutro y formal sencillo'",
        "telefono": "56990000014",
        "turnos": [
            "hola, cachai que queria pedir algo",
            "ya po, me tinca un churrasco italiano al tiro, para retiro",
            "efectivo, justo",
        ],
        "empujon": "si, confirmo",
        "duras": lambda r: [
            ("llega exactamente 1 pedido a cocina", r.pedidos == 1),
            (f"entendio el pedido pese a la jerga: total {plata(4900)}",
             r.campo("total") == 4900),
        ],
    },
    {
        "id": "empaque_no_ofrece_aparte",
        "que_prueba": ("Preguntar como viene EMPACADO (que aguante un viaje largo) no tiene "
                       "nada que ver con 'aparte' (separar un ingrediente como item cobrado). "
                       "El bot no tiene que ofrecer desagregar ingredientes sin que el cliente "
                       "lo haya pedido -- es complejidad de cocina de gratis."),
        "origen": "curador, conversacion 97 (29-sep-2026): Christian marco como error que el "
                  "bot ofreciera 'pedir que algunos ingredientes vayan aparte' cuando el "
                  "cliente solo pregunto por el empaque, sin pedir separar nada.",
        "telefono": "56990000015",
        "turnos": [
            "hola, dos completos y una papas medianas para retiro, pago con tarjeta",
            "que manera de empaque tienen los complementos, que voy lejos",
        ],
        "empujon": "si, confirmo",
        "duras": lambda r: [
            ("llega exactamente 1 pedido a cocina", r.pedidos == 1),
            ("no ofrece separar ingredientes ('aparte') sin que el cliente lo pidiera",
             "aparte" not in r.al_cliente.lower()),
        ],
    },
]

MAX_EMPUJONES = 2


# ===========================================================================
# Corrida
# ===========================================================================
def correr_escenario(esc, cerebro, base_tmp, verboso=False):
    carpeta = Path(tempfile.mkdtemp(dir=base_tmp))
    conv_db = carpeta / "conversaciones.db"
    nucleo = NucleoPedidos(db_path=carpeta / "pedidos.db")

    # Cada escenario con su propia cola de respaldo. Es la leccion del 5-ago: dos tests
    # compartian la base global y uno heredaba el pedido despachado por el otro, asi que
    # el candado de duplicados los hacia fallar por contagio, no por su propio defecto.
    respaldo_previo = webhook.RESPALDO_DB
    webhook.RESPALDO_DB = carpeta / "respaldo.db"

    # Cache de clientes: se reescribe por escenario (o se borra) para que "cliente
    # conocido" nunca se filtre al escenario siguiente.
    registrado = esc.get("cliente_registrado")
    if registrado:
        prototipo.CLIENTES_CACHE.write_text(
            json.dumps({webhook._norm_telefono(esc["telefono"]): registrado}, ensure_ascii=False),
            encoding="utf-8")
    elif prototipo.CLIENTES_CACHE.exists():
        prototipo.CLIENTES_CACHE.unlink()

    espia = Espia()
    enviar_previo = webhook.enviar_whatsapp
    webhook.enviar_whatsapp = espia

    visibles, empujones = [], 0
    try:
        for turno in esc["turnos"]:
            visibles.append(webhook.procesar_mensaje(
                esc["telefono"], turno, dict(webhook.CONFIG_DEFAULT),
                cerebro=cerebro, nucleo=nucleo, conv_db=conv_db) or "")
            if verboso:
                print(f"    > {turno}")
                print(f"    < {visibles[-1][:220]}")

        # Empujones: si el escenario espera una comanda y todavia no salio, se le manda
        # la confirmacion que un cliente real mandaria. No agregan informacion nueva --
        # solo miden cuanto le costo cerrar algo que ya estaba dicho.
        empujon = esc.get("empujon") if esc.get("espera_pedido", True) else None
        while empujon and not nucleo.cola() and empujones < MAX_EMPUJONES:
            empujones += 1
            visibles.append(webhook.procesar_mensaje(
                esc["telefono"], empujon, dict(webhook.CONFIG_DEFAULT),
                cerebro=cerebro, nucleo=nucleo, conv_db=conv_db) or "")
            if verboso:
                print(f"    > [empujon {empujones}] {empujon}")
                print(f"    < {visibles[-1][:220]}")
    finally:
        webhook.enviar_whatsapp = enviar_previo
        webhook.RESPALDO_DB = respaldo_previo

    r = Resultado(esc["telefono"], visibles, nucleo.cola(), espia.mensajes)
    checks = list(esc["duras"](r))
    # Transversal: el idioma neutro no es de un escenario, es de todas las respuestas. Va
    # en los trece para que una recaida aparezca DONDE ocurre y no solo en el escenario
    # que la busca a proposito.
    jerga = jerga_en(r.al_cliente)
    checks.append(("habla neutro, sin modismos ni voseo"
                   + (f" -- encontrado: {', '.join(jerga)}" if jerga else ""),
                   not jerga))
    return {
        "id": esc["id"],
        "checks": [{"desc": d, "ok": bool(v)} for d, v in checks],
        "ok": all(v for _, v in checks),
        "empujones": empujones,
        "turnos": len(visibles),
        "al_cliente": r.al_cliente,
        "cola": r.cola,
    }


def correr(escenarios, modelo, temperatura, verboso=False, pausa=5):
    base = Path(tempfile.mkdtemp(prefix="banco_patata_"))
    _neutralizar_efectos(base)
    cerebro = CerebroBanco(modelo=modelo,
                           temperatura=None if temperatura < 0 else temperatura)
    resultados = []
    try:
        for n, esc in enumerate(escenarios):
            # Respiro entre escenarios: el banco quema ~9k tokens por turno y la cuenta
            # tiene tope por MINUTO. Sin esto la corrida se autolimita a mitad de camino
            # y el puntaje mide la cuota, no el bot (ver CerebroBanco.pensar).
            if n and pausa:
                time.sleep(pausa)
            print(f"\n== {esc['id']} ==")
            print(f"   {esc['que_prueba']}")
            print(f"   origen: {esc['origen']}")
            res = correr_escenario(esc, cerebro, base, verboso=verboso)
            for c in res["checks"]:
                print(f"   [{'OK' if c['ok'] else 'FALLA'}] {c['desc']}")
            if res["empujones"]:
                print(f"   (necesito {res['empujones']} empujon/es de confirmacion)")
            if not res["ok"] and not verboso:
                print(f"   ---- lo que leyo el cliente ----\n"
                      f"   {res['al_cliente'][:600].replace(chr(10), chr(10) + '   ')}")
                if res["cola"]:
                    p = res["cola"][0]
                    print(f"   ---- comanda: total={p.get('total')} "
                          f"modalidad={p.get('modalidad')} pago={p.get('forma_pago')} "
                          f"envio={p.get('delivery_cobro')} cliente={p.get('cliente')!r}")
            resultados.append(res)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return resultados, cerebro


def main():
    ap = argparse.ArgumentParser(description="Banco de regresion del bot de Patata.")
    ap.add_argument("--escenario", action="append", default=None,
                    help="corre solo estos ids (se puede repetir)")
    ap.add_argument("--modelo", default="gpt-4.1-mini")
    ap.add_argument("--temperatura", type=float, default=0.0,
                    help="0 = comparable entre corridas; -1 = como produccion")
    ap.add_argument("--repeticiones", type=int, default=1,
                    help="corre el banco N veces y reporta si el numero se repite")
    ap.add_argument("--pausa", type=int, default=5,
                    help="segundos entre escenarios, para no chocar con el tope por minuto")
    ap.add_argument("--json", default=None, help="guarda el resultado en un archivo")
    ap.add_argument("--verboso", action="store_true", help="imprime la conversacion completa")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--max-llamadas", type=int, default=None,
                    help=f"tope de llamadas al modelo en toda la ejecucion (por defecto "
                         f"{TOPE_LLAMADAS_SIN_AUTORIZAR}); subirlo es una decision, no un descuido")
    args = ap.parse_args()

    if args.listar:
        for e in ESCENARIOS:
            print(f"{e['id']:32s} {e['que_prueba'][:90]}")
        return 0

    escenarios = ESCENARIOS
    if args.escenario:
        pedidos = set(args.escenario)
        escenarios = [e for e in ESCENARIOS if e["id"] in pedidos]
        faltan = pedidos - {e["id"] for e in escenarios}
        if faltan:
            print(f"[error] no existe(n): {', '.join(sorted(faltan))}")
            return 2

    llamadas_est, usd_est = estimar_costo(len(escenarios), args.repeticiones)
    print(f"[costo] estimado: ~{llamadas_est} llamadas, ~USD {usd_est:.2f} "
          f"(key dedicada del bot: puede ser la MISMA cuenta que produccion)")
    print(f"[presupuesto] {gasto_pruebas.resumen()}")
    if llamadas_est > gasto_pruebas.restantes_hoy():
        print(f"[error] eso pasa lo que queda del presupuesto de hoy "
              f"({gasto_pruebas.restantes_hoy()} llamadas). No corro, para no llegar al 429.")
        return 2
    if args.max_llamadas is not None:
        _gasto["tope"] = args.max_llamadas
    elif llamadas_est > TOPE_LLAMADAS_SIN_AUTORIZAR:
        print(f"[error] eso pasa el tope de {TOPE_LLAMADAS_SIN_AUTORIZAR} llamadas. No corro. "
              f"Si Gonza lo sabe y lo quiere: --max-llamadas {llamadas_est}")
        return 2

    corridas = []
    for n in range(args.repeticiones):
        if args.repeticiones > 1:
            print(f"\n{'#' * 70}\n# CORRIDA {n + 1} de {args.repeticiones}\n{'#' * 70}")
        resultados, cerebro = correr(escenarios, args.modelo, args.temperatura,
                                     verboso=args.verboso, pausa=args.pausa)
        puntaje = sum(1 for r in resultados if r["ok"])
        empujones = sum(r["empujones"] for r in resultados)
        print(f"\n{'=' * 70}")
        print(f"BANCO: {puntaje}/{len(resultados)}   "
              f"empujones: {empujones}   "
              f"llamadas al modelo: {cerebro.llamadas}   "
              f"{cerebro.segundos:.0f}s")
        fallados = [r["id"] for r in resultados if not r["ok"]]
        if fallados:
            print(f"fallan: {', '.join(fallados)}")
        if cerebro.errores_api:
            print(f"[aviso] la API fallo {cerebro.errores_api} vez/veces y se reintento "
                  f"(casi siempre el tope de tokens por minuto)")
        if cerebro.errores_fatales:
            print(f"🔴 CORRIDA NO COMPARABLE: {cerebro.errores_fatales} llamada(s) nunca "
                  f"obtuvieron respuesta. Este puntaje mide la cuota de la API, no el bot.")
        print("=" * 70)
        corridas.append({"puntaje": puntaje, "de": len(resultados), "empujones": empujones,
                         "modelo": args.modelo, "temperatura": args.temperatura,
                         "errores_api": cerebro.errores_api,
                         "errores_fatales": cerebro.errores_fatales,
                         "escenarios": [{k: r[k] for k in ("id", "ok", "empujones", "checks")}
                                        for r in resultados]})

    if args.repeticiones > 1:
        # El total por si solo MIENTE: en la medicion del 6-ago dos corridas dieron 9 y 9,
        # pero no eran los mismos 9 -- un escenario paso y otro fallo, turnandose. Por eso
        # la linea base que hay que comparar despues de podar el prompt es FIRME (los que
        # pasan siempre), no el total de una corrida suelta.
        por_id = {}
        for c in corridas:
            for r in c["escenarios"]:
                por_id.setdefault(r["id"], []).append(r["ok"])
        firmes = sorted(i for i, oks in por_id.items() if all(oks))
        rotos = sorted(i for i, oks in por_id.items() if not any(oks))
        inestables = sorted((i, sum(oks), len(oks)) for i, oks in por_id.items()
                            if any(oks) and not all(oks))
        total = len(por_id)
        print(f"\n{'=' * 70}")
        print(f"LINEA BASE ({args.repeticiones} corridas, modelo {args.modelo}, "
              f"temperatura {args.temperatura}):")
        print(f"  FIRMES     {len(firmes)}/{total}  pasan siempre  -> {', '.join(firmes) or '-'}")
        print(f"  INESTABLES {len(inestables)}/{total}  " +
              (", ".join(f"{i} ({n}/{d})" for i, n, d in inestables) or "-"))
        print(f"  ROTOS      {len(rotos)}/{total}  fallan siempre -> {', '.join(rotos) or '-'}")
        print(f"  totales por corrida: {[c['puntaje'] for c in corridas]}")
        print("=" * 70)

    if args.json:
        Path(args.json).write_text(
            json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"),
                        "corridas": corridas}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\nguardado en {args.json}")

    return 0 if all(c["puntaje"] == c["de"] for c in corridas) else 1


if __name__ == "__main__":
    sys.exit(main())

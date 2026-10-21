#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
curador.py — App de curación del system_prompt.md de "Donde el Patata" (04-ago-2026).
=======================================================================================
Herramienta LOCAL (no se despliega a Railway). Corre en el PC de Gonza como
`python curador.py`. Existe ademas compilada como `.exe` PARA que la corra Christian
(ver `build_pyi/Curador Patata.spec` y `dist/Curador Patata/LEEME_CHRISTIAN.md`):
mismo codigo, dos formas de correrlo. El paquete se le entrego el 04-ago-2026, pero
al 05-ago **nadie confirmo que lo haya instalado ni abierto** -- entregado no es
instalado; hasta que el lo diga, el unico uso verificado es el de Gonza.
Alcance acordado con Gonza el 04-ago-2026:

  1. Trae conversaciones NUEVAS reales (las de producción, no las 50+ archivadas que
     ya se usaron para entrenar el prompt inicial) desde el panel del bot en Railway.
  2. Curación MANUAL: Gonza marca en qué turno del bot algo salió mal, con una nota.
  3. Botón "¿Sigue fallando?": re-arma el mismo contexto (system_prompt.md VIGENTE +
     historial real hasta ese punto) y le pega una vez más al modelo real, para ver si
     el problema sigue existiendo tras un ajuste al prompt.
  4. Edición del prompt a mano (esta app no reescribe el prompt sola).
  5. Guardar hace PUSH automático a despliegue_railway (solo system_prompt.md). El
     DEPLOY en Railway sigue siendo SIEMPRE manual desde su dashboard -- esta app
     nunca lo toca.

Esta app NUNCA toca webhook.py ni ningún otro archivo del bot -- solo lee/escribe
bot/system_prompt.md y su propia base local (curaciones.db, cache de curación).

Uso:
    python curador.py                # levanta el servidor en http://127.0.0.1:8002
    python curador.py --port 9000
"""

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

if getattr(sys, "frozen", False):
    # Empaquetado como .exe (Curador Patata, PC de Christian, 04-ago-2026): todo lo
    # que el curador necesita vive JUNTO al ejecutable -- mismo criterio que
    # webhook.py/cajero.py (ver build_pyi/Curador Patata.spec). prototipo.py y
    # tarificar.py ya viajan empaquetados ADENTRO del .exe (modulos hermanos
    # resueltos en tiempo de build via pathex), no hace falta sys.path.insert ni
    # existe una carpeta bot/ real al lado -- por eso DESPLIEGUE cuelga directo de
    # AQUI, no dos niveles arriba como en modo desarrollo.
    AQUI = Path(sys.executable).resolve().parent
    DESPLIEGUE = AQUI / "despliegue_railway"
else:
    AQUI = Path(__file__).resolve().parent           # bot/curador/
    BOT = AQUI.parent                                 # bot/
    PROYECTO = BOT.parent                             # Donde el Patata/
    DESPLIEGUE = PROYECTO / "despliegue_railway"
    sys.path.insert(0, str(BOT))                      # prototipo.py vive en bot/, y a su vez
import prototipo                                   # noqa: E402  importa tarificar.py (sibling)
import sobre_veredicto as papel                     # noqa: E402  el motor del sobre y el veredicto

DB_PATH = AQUI / "curaciones.db"
CONFIG_PATH = AQUI / "config_curador.json"
CONFIG_EJEMPLO = AQUI / "config_curador.ejemplo.json"
INSTRUCCIONES_CURADOR_PATH = AQUI / "INSTRUCCIONES_DEL_CURADOR.md"

# 13-sep-2026, pedido de Gonza: sacar la vuelta manual por el proyecto de ChatGPT de
# Christian -- la key dedicada del bot (la misma que ya usa "¿Qué contestaría hoy?",
# ver _reintentar) alcanza para que el Curador razone solo. Sin lista de respaldo a
# proposito: INSTRUCCIONES_DEL_CURADOR.md es explicito en que esta tarea no admite
# caer a un modelo mas chico sin que nadie se entere. Subir esto a mano cuando OpenAI
# publique algo mas nuevo -- no hay forma de "detectar el mas nuevo" desde la API.
MODELO_CURADOR_AUTOMATICO = "gpt-4.1"

# Banco rapido ANTES de aplicar (13-sep-2026, pedido de Gonza): sin esto, un cambio
# roto solo se descubre ~25 min despues via el banco del CI, y cuando se descubre nadie
# sabe por que -- el diagnostico de una regresion real (el candado de pago rompiendo
# pedido_grande y calle_fuera_de_planilla) lo hizo Alma a mano, escenario por escenario.
# Esto NO reemplaza el banco completo del CI (14 escenarios, corre en cada push): es un
# primer filtro, mas chico y en el momento de decidir, no despues. Los 4 elegidos son
# los que hoy demostraron atrapar regresiones reales de un cambio de prompt.
# 18-sep-2026: subido de 12 a 20 -- con 12 el banco rápido chocó DOS veces seguidas
# contra el tope de tokens/minuto y no llegó a terminar (ver #4 en CLAUDE.md: 20 fue el
# valor que en la medición del 12-ago dejó 0 errores de API en 6 corridas del banco
# completo). No arregla la inestabilidad de fondo de `pedido_grande`/
# `calle_fuera_de_planilla` (esas fallan por el modelo, no por la cuota, ~1 de 6 aun con
# el prompt sin tocar) -- solo saca la cuota como causa de un "rompe" que no era real.
ESCENARIOS_BANCO_RAPIDO = ("ficha_con_precios", "pedido_grande",
                           "calle_fuera_de_planilla", "mayo_aparte")
SEGUNDOS_PAUSA_BANCO_RAPIDO = 20

# Mismo umbral que HORAS_CADUCIDAD_HISTORIAL en webhook.py: un hueco mayor a esto
# entre dos turnos del mismo teléfono se trata como conversación nueva, no como
# continuación. Duplicado a propósito (constante de 1 línea) para no importar todo
# webhook.py -- ese módulo instancia FastAPI y arranca hilos de fondo al importarse,
# cosas que este proceso local no necesita ni quiere.
HORAS_NUEVA_CONVERSACION = 6


# ===========================================================================
# Config (bot_url / bot_panel_token) -- mismo patrón que pos/cajero.py
# ===========================================================================
def _cfg():
    if not CONFIG_PATH.exists():
        if CONFIG_EJEMPLO.exists():
            CONFIG_PATH.write_text(CONFIG_EJEMPLO.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            CONFIG_PATH.write_text(json.dumps(
                {"bot_url": "https://bot.dondeelpatata.cl", "bot_panel_token": ""},
                indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _bot_url():
    return str(_cfg().get("bot_url") or "").rstrip("/")


def _headers_panel():
    tok = str(_cfg().get("bot_panel_token") or "").strip()
    if not tok:
        return None
    return {"X-Panel-Token": tok}


# ===========================================================================
# Base local de curación (separada de conversaciones_bot.db -- esa vive en el
# volumen de Railway; acá solo se cachea lo necesario para curar)
# ===========================================================================
def _con():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("""CREATE TABLE IF NOT EXISTS conversaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telefono TEXT NOT NULL,
        inicio TEXT NOT NULL,
        fin TEXT NOT NULL,
        estado TEXT NOT NULL DEFAULT 'pendiente',
        turno_fallo_id INTEGER,
        nota TEXT DEFAULT '',
        ultimo_reintento TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS turnos_cache (
        id INTEGER PRIMARY KEY,
        conversacion_id INTEGER NOT NULL,
        telefono TEXT NOT NULL,
        rol TEXT NOT NULL,
        texto TEXT NOT NULL,
        creado_en TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS sync_estado (
        clave TEXT PRIMARY KEY, valor TEXT
    )""")
    # 19-ago-2026 (el Meson): la nota de Christian sola no alcanzaba -- "lo que tendria
    # que haber contestado" es el dato que mas ayuda al Curador y no habia donde ponerlo.
    # El ALTER va en try porque este mismo codigo corre sobre bases viejas y nuevas.
    try:
        con.execute("ALTER TABLE conversaciones ADD COLUMN deberia TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # 25-sep-2026 (Gonza): "acumular casos resueltos no me hace sentido a la vista". La
    # pestaña Resueltos muestra solo lo resuelto en los ultimos dias, y para eso hace
    # falta CUANDO se resolvio (el `fin` es cuando termino la conversacion, no el caso).
    # Un trigger y no un UPDATE mas en cada camino: hay tres caminos que resuelven
    # (boton, veredicto NO_CAMBIAR, veredicto aplicado) y un cuarto futuro no se olvida.
    try:
        con.execute("ALTER TABLE conversaciones ADD COLUMN resuelto_en TEXT")
    except sqlite3.OperationalError:
        pass
    con.execute("""CREATE TRIGGER IF NOT EXISTS marca_resuelto_en
        AFTER UPDATE OF estado ON conversaciones
        WHEN NEW.estado = 'ok' AND OLD.estado IS NOT 'ok'
        BEGIN
            UPDATE conversaciones SET resuelto_en = strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')
            WHERE id = NEW.id;
        END""")
    # Cada papel que entra o sale queda guardado. Un ciclo de curacion que no deja
    # rastro es exactamente el que nadie puede auditar despues -- y el defecto que se
    # arregla acá era, justamente, una nota que moria sin destino.
    con.execute("""CREATE TABLE IF NOT EXISTS papeles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversacion_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,          -- sobre | veredicto | aplicado
        texto TEXT NOT NULL,
        extra TEXT DEFAULT '',       -- huella, decision o commit, segun el tipo
        creado_en TEXT NOT NULL
    )""")
    con.commit()
    return con


def _get_estado(con, clave, default=None):
    fila = con.execute("SELECT valor FROM sync_estado WHERE clave=?", (clave,)).fetchone()
    return fila[0] if fila else default


def _set_estado(con, clave, valor):
    con.execute("INSERT INTO sync_estado (clave, valor) VALUES (?,?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (clave, str(valor)))


# ===========================================================================
# Sincronizar conversaciones reales desde el panel del bot en Railway
# ===========================================================================
def sincronizar():
    """Trae turnos nuevos desde {bot_url}/conversaciones (mismo endpoint que usa
    pos/cajero.py para la pantalla en vivo) y los agrupa en conversaciones locales.

    LIMITACIÓN HEREDADA (no se corrige acá -- tocar ese endpoint es tocar webhook.py,
    fuera de alcance de esta app): /conversaciones devuelve como máximo los 300 turnos
    MÁS RECIENTES posteriores a desde_id, no los 300 siguientes a desde_id. Si pasan
    varios días sin sincronizar y hay más de 300 turnos nuevos, los del medio se
    pierden. Para el ritmo de un local chico esto no debería pasar nunca si se
    sincroniza con regularidad, pero queda dicho.
    """
    import urllib.request
    import urllib.error

    headers = _headers_panel()
    if headers is None:
        return {"ok": False, "error": "falta bot_panel_token en config_curador.json"}
    url = _bot_url()
    if not url:
        return {"ok": False, "error": "falta bot_url en config_curador.json"}

    con = _con()
    try:
        desde_id = int(_get_estado(con, "ultimo_id", 0))
        pet = urllib.request.Request(f"{url}/conversaciones?desde_id={desde_id}", headers=headers)
        try:
            with urllib.request.urlopen(pet, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code} del panel del bot"}
        except urllib.error.URLError as e:
            return {"ok": False, "error": f"no se pudo conectar a {url}: {e.reason}"}

        turnos = data.get("turnos", [])
        nuevas, actualizadas = 0, 0
        max_id = desde_id
        for t in turnos:
            tid, tel, rol, texto, creado = t["id"], t["telefono"], t["rol"], t["texto"], t["creado_en"]
            max_id = max(max_id, tid)
            if con.execute("SELECT 1 FROM turnos_cache WHERE id=?", (tid,)).fetchone():
                continue  # ya cacheado (reintento de polling)

            ultima = con.execute(
                "SELECT id, fin, estado FROM conversaciones WHERE telefono=? "
                "ORDER BY id DESC LIMIT 1", (tel,)).fetchone()
            conv_id = None
            if ultima:
                gap_h = _horas_entre(ultima[1], creado)
                if gap_h is not None and gap_h <= HORAS_NUEVA_CONVERSACION:
                    conv_id = ultima[0]
                    con.execute("UPDATE conversaciones SET fin=? WHERE id=?", (creado, conv_id))
                    if ultima[2] != "pendiente":
                        # la conversación siguió después de curada -- vuelve a revisión
                        con.execute("UPDATE conversaciones SET estado='pendiente' WHERE id=?", (conv_id,))
                    actualizadas += 1
            if conv_id is None:
                cur = con.execute(
                    "INSERT INTO conversaciones (telefono, inicio, fin, estado) VALUES (?,?,?,'pendiente')",
                    (tel, creado, creado))
                conv_id = cur.lastrowid
                nuevas += 1

            con.execute(
                "INSERT INTO turnos_cache (id, conversacion_id, telefono, rol, texto, creado_en) "
                "VALUES (?,?,?,?,?,?)", (tid, conv_id, tel, rol, texto, creado))

        _set_estado(con, "ultimo_id", max_id)
        _set_estado(con, "ultima_sync", datetime.now().isoformat(timespec="seconds"))
        con.commit()
        return {"ok": True, "turnos_nuevos": len(turnos), "conversaciones_nuevas": nuevas,
                "conversaciones_actualizadas": actualizadas}
    finally:
        con.close()


def obtener_casos_humanos():
    """Trae del bot los casos donde derivó a un humano (pedido grande, humano
    prometido, pedido duplicado superpuesto, falla del cerebro) -- CON el WhatsApp
    de aviso a los jefes ya mandado (ver /casos-humanos en webhook.py). Solo
    LECTURA: no marca nada como resuelto del lado del bot, y no toca webhook.py ni
    ningún otro archivo del bot -- mismo límite que el resto de esta app (18-sep-2026,
    pedido de Gonza: el curador no mostraba lo que el bot le dice a los jefes, porque
    esos avisos nunca pasan por `/conversaciones`, ver sincronizar())."""
    headers = _headers_panel()
    if headers is None:
        return {"ok": False, "error": "falta bot_panel_token en config_curador.json"}
    url = _bot_url()
    if not url:
        return {"ok": False, "error": "falta bot_url en config_curador.json"}
    pet = urllib.request.Request(f"{url}/casos-humanos", headers=headers)
    try:
        with urllib.request.urlopen(pet, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code} del panel del bot"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"no se pudo conectar a {url}: {e.reason}"}
    return {"ok": True, "casos": data.get("casos", [])}


def obtener_avisos_jefes(dias=7):
    """Trae del bot el detalle fila por fila de lo que le mandó a los jefes por
    WhatsApp (rutina, no solo derivaciones) -- /avisos-jefes en webhook.py, agregado
    el 18-sep-2026 junto con esta función, mismo motivo que obtener_casos_humanos:
    solo lectura, no toca el estado del bot."""
    headers = _headers_panel()
    if headers is None:
        return {"ok": False, "error": "falta bot_panel_token en config_curador.json"}
    url = _bot_url()
    if not url:
        return {"ok": False, "error": "falta bot_url en config_curador.json"}
    pet = urllib.request.Request(f"{url}/avisos-jefes?dias={dias}", headers=headers)
    try:
        with urllib.request.urlopen(pet, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code} del panel del bot"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"no se pudo conectar a {url}: {e.reason}"}
    return {"ok": True, "avisos": data.get("avisos", [])}


def _horas_entre(iso_a, iso_b):
    try:
        a, b = datetime.fromisoformat(iso_a), datetime.fromisoformat(iso_b)
        return abs((b - a).total_seconds()) / 3600
    except (TypeError, ValueError):
        return None


# ===========================================================================
# Replay: "¿sigue fallando?" -- llama al cerebro REAL contra el prompt VIGENTE
# ===========================================================================
def _reintentar(conv_id, turno_fallo_id, veces=3):
    con = _con()
    try:
        turnos = con.execute(
            "SELECT id, rol, texto FROM turnos_cache WHERE conversacion_id=? ORDER BY id ASC",
            (conv_id,)).fetchall()
        idx_fallo = next((i for i, t in enumerate(turnos) if t[0] == turno_fallo_id), None)
        if idx_fallo is None:
            return {"ok": False, "error": "el turno marcado ya no está en la cache local"}

        previos = turnos[:idx_fallo]
        if not previos or previos[-1][1] != "usuario":
            return {"ok": False, "error": "no encontré el mensaje del cliente que gatilló esa "
                                           "respuesta (¿marcaste el turno correcto?)"}
        trigger = previos[-1][2]
        historial = [{"rol": r, "texto": t} for _, r, t in previos[:-1]]

        try:
            cerebro = prototipo.CerebroOpenAI(dedicado=True)
        except SystemExit as e:
            return {"ok": False, "error": f"no pude cargar la key de OpenAI del bot: {e}"}

        # El foco del menu (F5) se calcula con la MISMA conversacion que veria
        # produccion: si el curador mandara el catalogo completo, estaria probando un
        # prompt que el bot real nunca arma, y el "¿sigue fallando?" seria mentira.
        sistema = prototipo.construir_sistema(
            jefe=False,
            conversacion="\n".join([t["texto"] or "" for t in historial] + [trigger]))
        mensajes = [{"rol": "sistema", "texto": sistema}] + historial + [{"rol": "usuario", "texto": trigger}]

        # TRES veces, no una (18-ago-2026, medido leyendo el codigo). El bot atiende a
        # los clientes con la temperatura por defecto -- o sea que la MISMA pregunta con
        # el MISMO prompt puede dar respuestas distintas. Un boton que consulta una sola
        # vez y muestra "ahora contesta bien" es una maquina de falsos alivios: alcanza
        # con tener suerte una vez para dar por cerrado un caso que sigue roto. Con tres
        # tiros, la pantalla puede decir "contesto bien 1 de 3", que es la verdad.
        respuestas = [cerebro.pensar(mensajes) for _ in range(max(1, veces))]

        con.execute("UPDATE conversaciones SET ultimo_reintento=? WHERE id=?",
                    (datetime.now().isoformat(timespec="seconds"), conv_id))
        con.commit()
        return {"ok": True, "trigger": trigger, "respuesta_vieja": turnos[idx_fallo][2],
                "respuestas": respuestas, "veces": len(respuestas)}
    finally:
        con.close()


# ===========================================================================
# Curador automático: la misma key dedicada, sin pasar por el proyecto de ChatGPT
# ===========================================================================
_ABRE_COPIA_CURADOR = "<!-- ─────────── DESDE ACÁ SE COPIA AL PROYECTO DE CHATGPT ─────────── -->"
_CIERRA_COPIA_CURADOR = "<!-- ─────────── HASTA ACÁ ─────────── -->"


def _prompt_del_curador():
    """El texto que hoy se pega a mano en el proyecto de ChatGPT de Christian
    (INSTRUCCIONES_DEL_CURADOR.md), recortado a la parte que son las reglas del
    Curador -- la nota de instalación de más arriba en ese archivo no es parte del
    system prompt."""
    texto = INSTRUCCIONES_CURADOR_PATH.read_text(encoding="utf-8")
    if _ABRE_COPIA_CURADOR not in texto or _CIERRA_COPIA_CURADOR not in texto:
        raise RuntimeError(
            "INSTRUCCIONES_DEL_CURADOR.md no tiene las marcas de recorte esperadas "
            "-- revísalo a mano antes de curar automático.")
    return texto.split(_ABRE_COPIA_CURADOR, 1)[1].split(_CIERRA_COPIA_CURADOR, 1)[0].strip()


def _preguntar_openai_curador(mensajes):
    """Una llamada a la API de OpenAI con la lista de mensajes dada. Un solo modelo,
    SIN la lista de respaldo que sí tiene CerebroOpenAI: ver el comentario de
    MODELO_CURADOR_AUTOMATICO. Devuelve el texto crudo de la respuesta."""
    try:
        key = prototipo._buscar_key_openai(dedicado=True)
    except SystemExit as e:
        raise RuntimeError(f"no pude cargar la key de OpenAI del bot: {e}")

    cuerpo = json.dumps({"model": MODELO_CURADOR_AUTOMATICO, "messages": mensajes}).encode("utf-8")
    # 25-sep-2026 (Gonza: "parece que se gastó algo... la espera es falsa"). No se gasto
    # nada: la cuenta tiene un tope de 30.000 tokens POR MINUTO para gpt-4.1, y un solo
    # "Curar automatico" usa ~11.000 por consulta y reintenta hasta 3 veces si la
    # respuesta no pasa la revision -- se puede pasar del tope el solo. OpenAI contesta
    # 429 con "try again in 11s", y antes eso se le mostraba a la persona: esperaba 11
    # s, apretaba de nuevo, y el ciclo nuevo volvia a chocar. Ahora espera el curador.
    for intento in range(REINTENTOS_LIMITE_OPENAI + 1):
        pet = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=cuerpo, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(pet, timeout=90) as r:
                resp = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", errors="replace")[:300]
            if e.code == 429 and intento < REINTENTOS_LIMITE_OPENAI:
                m = re.search(r"try again in ([\d.]+)\s*(ms|s)", detalle)
                espera = (float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)) if m else 20
                # +3 s de margen: la ventana es movil, justo al segundo pedido vuelve a no alcanzar
                espera = min(max(espera + 3, 5), 65)
                print(f"[curador] OpenAI al tope de tokens por minuto; espero {espera:.0f}s "
                      f"y reintento ({intento + 1}/{REINTENTOS_LIMITE_OPENAI}).")
                time.sleep(espera)
                continue
            if e.code == 429:
                raise RuntimeError(
                    "OpenAI está al tope de tokens por minuto de la cuenta (30.000 para "
                    f"{MODELO_CURADOR_AUTOMATICO}) y ya esperé {REINTENTOS_LIMITE_OPENAI} "
                    "veces. No se gastó saldo: es un límite por minuto. Espera 2 minutos "
                    "sin apretar nada y vuelve a intentarlo.")
            raise RuntimeError(f"OpenAI respondió {e.code}: {detalle}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"No se pudo hablar con OpenAI: {e.reason}")
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Respuesta inesperada de OpenAI: {json.dumps(resp)[:300]}")


INTENTOS_CURADOR_AUTOMATICO = 3
REINTENTOS_LIMITE_OPENAI = 4


def _consultar_curador_automatico(sobre_texto, conv_id, huella_actual):
    """Le manda el sobre directo a la API de OpenAI en vez de esperar que alguien lo
    pegue en ChatGPT. Si el veredicto no pasa los candados (ej. voseo, un BUSCAR que
    no calza), NO se rinde en el primer intento -- eso deja al usuario clavado con un
    mensaje de error escrito para que un HUMANO se lo pase al Curador en ChatGPT,
    cuando acá no hay nadie para pasarselo (bug real, encontrado probando el 13-sep).
    En vez de eso, le manda el error de vuelta al modelo y le pide que corrija, hasta
    INTENTOS_CURADOR_AUTOMATICO veces -- mismo intercambio que haria Christian a mano,
    automatico. Devuelve texto crudo ya validado, listo para _procesar_veredicto()."""
    instrucciones = prototipo.SYSTEM_PROMPT.read_text(encoding="utf-8")
    mensajes = [{"role": "system", "content": _prompt_del_curador()},
                {"role": "user", "content": sobre_texto}]
    ultimo_error = None
    for _intento in range(INTENTOS_CURADOR_AUTOMATICO):
        pegado = _preguntar_openai_curador(mensajes)
        try:
            v = papel.leer_veredicto(pegado)
            decision = papel.validar_veredicto(v, conv_id, huella_actual)
            if decision == "CAMBIAR":
                # Dry-run: el BUSCAR tiene que existir de verdad en lo que el bot
                # aprendio, no en la conversacion (bug real, caso 10: el modelo trato
                # de "borrar" una respuesta del chat como si fuera texto del prompt).
                # aplicar_cambios no escribe nada -- ver su propio docstring.
                papel.aplicar_cambios(instrucciones, v["cambios"])
        except papel.ErrorDeVeredicto as e:
            ultimo_error = str(e)
            mensajes.append({"role": "assistant", "content": pegado})
            mensajes.append({"role": "user", "content":
                              f"Eso no pasó la revisión: {ultimo_error} Corrígelo y "
                              "devuélveme el veredicto completo de nuevo, con el mismo "
                              "formato exacto."})
            continue
        return pegado
    raise RuntimeError(
        f"Después de {INTENTOS_CURADOR_AUTOMATICO} intentos no logré un veredicto "
        f"válido (último problema: {ultimo_error}). Puedes intentarlo a mano con "
        "\"O hazlo a mano con el Curador de ChatGPT\", o dejar el caso pendiente.")


def _correr_banco_rapido(texto_nuevo):
    """Prueba el texto candidato (todavia SIN aplicar) contra un puñado de escenarios
    reales del banco, con la key dedicada del bot. Subprocess aparte, NUNCA import
    directo de banco_pruebas.py: ese modulo importa webhook.py entero, y webhook.py
    arranca hilos de fondo (sincronizar menu, seguimiento de saludo, etc.) con solo
    importarlo -- exactamente lo que este proceso no quiere hacer por accidente.

    El archivo real (system_prompt.md) se pisa TEMPORALMENTE para la corrida y se
    restaura pase lo que pase, incluso si el subprocess se cae o tarda demasiado --
    el bloque `finally` no depende de que la corrida haya salido bien.

    Devuelve (ok: bool, resumen: str). Un ok=False NO dice cuAl de varios cambios lo
    rompio si hubiera mas de uno en juego -- esto prueba UN candidato a la vez, que es
    como se usa el Curador en el uso normal (un caso, un cambio chico)."""
    ruta = prototipo.SYSTEM_PROMPT
    original = ruta.read_text(encoding="utf-8")
    ruta.write_text(texto_nuevo, encoding="utf-8")
    proc = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            salida = Path(tmp) / "resultado.json"
            args = [sys.executable, str(BOT / "banco_pruebas.py"),
                    "--modelo", MODELO_CURADOR_AUTOMATICO,
                    "--pausa", str(SEGUNDOS_PAUSA_BANCO_RAPIDO),
                    "--json", str(salida)]
            for esc_id in ESCENARIOS_BANCO_RAPIDO:
                args += ["--escenario", esc_id]
            timeout = 60 + 60 * len(ESCENARIOS_BANCO_RAPIDO)
            try:
                proc = subprocess.run(args, capture_output=True, text=True,
                                      encoding="utf-8", timeout=timeout)
            except subprocess.TimeoutExpired:
                return False, (f"El banco rápido no terminó en {timeout}s -- probable "
                               "límite de tokens por minuto de la cuenta. Prueba de "
                               "nuevo en un rato.")
            if not salida.exists():
                detalle = ((proc.stderr or proc.stdout or "sin detalle")[-500:]
                           if proc else "sin detalle")
                return False, f"No pude correr el banco rápido: {detalle}"
            datos = json.loads(salida.read_text(encoding="utf-8"))
    finally:
        ruta.write_text(original, encoding="utf-8")

    corrida = datos["corridas"][0]
    rotos = [e for e in corrida["escenarios"] if not e["ok"]]
    if not rotos:
        return True, f"{corrida['puntaje']}/{corrida['de']} en verde."
    detalle = "; ".join(
        f"{e['id']} ({', '.join(c['desc'] for c in e['checks'] if not c['ok'])})"
        for e in rotos)
    return False, f"{corrida['puntaje']}/{corrida['de']} -- rompe: {detalle}"


# ===========================================================================
# Prompt: leer / guardar con backup + push automático (deploy SIEMPRE manual)
# ===========================================================================
def _git_despliegue(*args):
    """git dentro de despliegue_railway/. Timeout porque fetch/push salen a internet
    y esta app corre sin consola: un git colgado dejaría la pantalla en 'Guardando…'
    para siempre, sin nada que mirar."""
    return subprocess.run(["git", "-C", str(DESPLIEGUE), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=90)


def _traer_prompt_de_github():
    """Trae a la copia local el system_prompt.md que esté en GitHub, si allá cambió,
    y pone al día el clon en el mismo movimiento (las dos cosas o ninguna).

    ES LA SALIDA DEL CANDADO de _sincronizar_despliegue, y sin esto el arreglo del
    11-ago no sirve de nada. El curador edita `prototipo.SYSTEM_PROMPT` -- el archivo
    que vive al lado del .exe -- y ese archivo NO se entera nunca de lo que se edita
    por el editor web de github.com. Christian usa ese editor: sus dos commits
    (a1f65d9 06-ago, 8a77a3b 07-ago) tienen committer "GitHub <noreply@github.com>" y
    los dos tocan bot/system_prompt.md. Sin este traído, en cuanto el prompt cambia
    allá el guardado queda bloqueado y no hay forma de destrabarlo desde la app.

    Solo pisa la copia local cuando NO hay nada propio que perder: si el archivo local
    difiere de la versión del HEAD del clon, hay una edición local sin subir y no se
    toca nada (solo se avisa). Siempre deja respaldo antes de escribir.

    Nunca falla hacia afuera: sin repo, sin git o sin internet devuelve {} y el editor
    abre con la copia local, igual que hasta el 11-ago-2026.

    ⚠️ El chequeo es sobre `.git`, no sobre la carpeta sola (bug real, encontrado el
    08-sep-2026 probando el camino entero, no solo leyendo el código): `despliegue_railway/`
    vive DENTRO del repo principal, que también es un repo git. Si esa carpeta existe pero
    su clon está roto o a medio hacer (sin `.git` adentro -- p.ej. un clon que se cortó),
    `git -C despliegue_railway` NO falla: git busca `.git` para arriba, encuentra el del
    repo principal, y termina operando sobre EL REPO EQUIVOCADO en vez de avisar que falta
    el clon. Con solo `DESPLIEGUE.exists()` ese caso pasaba el candado y explotaba más abajo
    con un error crudo en vez del aviso pensado para Christian.
    """
    if not (DESPLIEGUE / ".git").exists():
        return {}
    try:
        if _git_despliegue("fetch", "origin", "main").returncode != 0:
            return {}
        remoto = _git_despliegue("show", "origin/main:bot/system_prompt.md")
        cabeza = _git_despliegue("show", "HEAD:bot/system_prompt.md")
        if remoto.returncode != 0 or cabeza.returncode != 0:
            return {}
        texto_remoto = remoto.stdout.replace("\r\n", "\n")
        texto_cabeza = cabeza.stdout.replace("\r\n", "\n")
        texto_local = prototipo.SYSTEM_PROMPT.read_text(encoding="utf-8").replace("\r\n", "\n")
        if not texto_remoto.strip() or texto_remoto == texto_local:
            return {}
        if texto_local != texto_cabeza:
            # Hay una edición local que todavía no se subió: pisarla sería borrar
            # trabajo de verdad. Se avisa y se deja como está -- decide la persona.
            return {"hay_version_nueva_en_github": True}

        respaldo = prototipo.SYSTEM_PROMPT.with_name(
            f"system_prompt.md.bak_{datetime.now().strftime('%Y%m%d%H%M%S')}")
        respaldo.write_text(texto_local, encoding="utf-8")
        # Sin newline="\n" a propósito: la copia local es de Windows y va en CRLF
        # (mismo criterio que api_post_prompt, ver el comentario largo de allá).
        prototipo.SYSTEM_PROMPT.write_text(texto_remoto, encoding="utf-8")

        # 🪦 El clon TAMBIÉN tiene que avanzar, no solo el archivo de al lado.
        # Medido el 11-ago (tarde) contra el .exe, camino entero: traer el texto y
        # dejar el clon atrás hacía que el guardado siguiente viera el commit recién
        # traído como si lo hubiera hecho otra persona, y devolviera "alguien editó
        # el prompt en GitHub". Christian hacía lo que el cartel le pedía (cerrar y
        # reabrir el editor) y volvía a chocar con el mismo cartel: el candado del
        # 11-ago (mañana) quedaba abierto a medias -- se destrababa la LECTURA y
        # seguía trabada la SUBIDA. Acá no hay nada que perder por construcción:
        # unas líneas más arriba se verificó que la copia local era idéntica al HEAD
        # del clon, así que ponerse al día no pisa trabajo de nadie.
        _git_despliegue("checkout", "--", "bot/system_prompt.md")
        if _git_despliegue("-c", "rebase.autoStash=true",
                           "rebase", "origin/main").returncode != 0:
            _git_despliegue("rebase", "--abort")
        return {"traido_de_github": True, "respaldo": respaldo.name}
    except (OSError, subprocess.SubprocessError):
        return {}


def _sincronizar_despliegue(contenido_lf):
    """Copia system_prompt.md (ya en LF) a despliegue_railway/bot/, y pushea a
    christian-ulloa/bot-patata. Railway está configurado para pedir aprobación
    manual antes de desplegar un push nuevo (ver CLAUDE.md §Arquitectura) -- un
    push de acá deja la versión LISTA, pero no la pone en producción sola.

    Antes de tocar nada: git fetch. Si origin/main avanzó, el clon SE PONE AL DÍA con
    rebase (11-ago-2026). Se detiene SOLO si los commits que entran tocaron
    bot/system_prompt.md: ahí guardar pisaría el ajuste de otra persona, y eso no lo
    puede decidir el programa.

    🪦 Qué había acá antes, para no volver a ponerlo: un abort seco -- si origin/main
    estaba adelante, devolvía error y no guardaba nada. Como el curador nunca hacía
    pull, el candado no tenía salida: el clon de Christian quedó atrás el 07-ago y no
    se recuperó más. Medido el 11-ago: CERO commits "Curador:" en toda la historia del
    repo de deploy. La herramienta no falló, se trabó -- y el mensaje que mostraba le
    pedía a Christian algo que no podía hacer.

    ⚠️ El chequeo es sobre `.git`, no sobre la carpeta sola (mismo bug que en
    `_traer_prompt_de_github`, encontrado el 08-sep-2026 corriendo el camino CAMBIAR
    entero contra un caso de prueba, no solo leyendo el código). `despliegue_railway/`
    vive DENTRO del repo principal. Si la carpeta existe pero el clon está roto o a
    medio hacer (sin `.git` adentro), `git -C despliegue_railway fetch` NO falla: git
    busca `.git` para arriba, encuentra el del repo principal, y el `fetch`/`push`
    siguiente terminaría operando sobre EL REPO EQUIVOCADO -- en el peor caso,
    pusheando al repo de trabajo en vez de al de deploy. Antes de este arreglo, el
    síntoma para Christian no era ni siquiera un aviso: era un HTTP 500 crudo, porque
    el candado dejaba pasar la carpeta vacía y el crash llegaba dos líneas más abajo,
    en `destino.write_text(...)`, que es exactamente el tipo de fallo que este archivo
    entero existe para evitar (ver la cabecera de `_error_de_push`)."""
    if not (DESPLIEGUE / ".git").exists():
        return {"ok": False, "error":
                f"No encontré el clon de despliegue_railway (o está a medio hacer) en "
                f"{DESPLIEGUE}. Tu cambio quedó guardado acá en el PC, pero no lo pude "
                "subir. Esto se arregla una sola vez: avísale a Gonzalo."}

    _git = _git_despliegue

    fetch = _git("fetch", "origin", "main")
    if fetch.returncode != 0:
        return {"ok": False, "error":
                "Guardé tu cambio acá en el PC, pero no pude conectarme a GitHub para subirlo. "
                "Suele ser el internet de este computador. Prueba de nuevo en un rato; si sigue "
                f"igual, mándame esta pantalla. (detalle técnico: {fetch.stderr.strip()[:200]})"}

    local = _git("rev-parse", "HEAD").stdout.strip()
    remoto = _git("rev-parse", "origin/main").stdout.strip()
    if local and remoto and local != remoto:
        base = _git("merge-base", "HEAD", "origin/main").stdout.strip()
        if base and base != remoto:
            # origin/main trae commits que este clon no tiene. Antes de ponerse al día:
            # ¿alguno tocó el prompt? Se compara BASE contra el remoto, no HEAD contra el
            # remoto: así se mira solo lo que ENTRA. Con HEAD, un commit local sin subir
            # (un intento anterior que commiteó y falló al pushear) se vería como si
            # alguien hubiera editado en GitHub, y volveríamos al candado sin salida.
            toco_prompt = _git("diff", "--quiet", base, "origin/main",
                               "--", "bot/system_prompt.md").returncode != 0
            if toco_prompt:
                return {"ok": False, "conflicto_prompt": True, "error":
                        "Tu cambio quedó guardado acá en el PC, pero NO lo subí: alguien editó el "
                        "prompt en GitHub y subirlo ahora le borraría ese cambio. "
                        "Cierra el editor y vuelve a abrirlo con el botón del prompt: te va a "
                        "aparecer la versión nueva, ya con lo de GitHub adentro. Vuelve a hacer tu "
                        "ajuste sobre esa y aprieta Guardar de nuevo."}
            # Nadie tocó el prompt allá: ponerse al día y seguir de largo.
            # bot/system_prompt.md se reescribe entero unas líneas más abajo, así que
            # descartar lo que tenga sin commitear es inocuo -- y sin eso el rebase se
            # niega a correr con el árbol sucio.
            _git("checkout", "--", "bot/system_prompt.md")
            rebase = _git("-c", "rebase.autoStash=true", "rebase", "origin/main")
            if rebase.returncode != 0:
                _git("rebase", "--abort")
                return {"ok": False, "error":
                        "Tu cambio quedó guardado acá en el PC, pero no pude subirlo: al ponerme "
                        "al día con GitHub se me cruzaron dos versiones y preferí no tocar nada. "
                        "Esto lo tengo que mirar yo -- sácale una foto a esta pantalla y mándamela. "
                        f"(detalle técnico: {rebase.stderr.strip()[:200]})"}

    destino = DESPLIEGUE / "bot" / "system_prompt.md"
    destino.write_text(contenido_lf, encoding="utf-8", newline="\n")

    _git("add", "bot/system_prompt.md")
    diff = _git("diff", "--cached", "--quiet")
    if diff.returncode == 0:
        # Nada nuevo que commitear. Puede quedar igual un commit de un intento anterior
        # que commiteó y falló al pushear: si lo hay, este es el momento de subirlo.
        atrasados = _git("rev-list", "--count", "origin/main..HEAD").stdout.strip()
        if atrasados.isdigit() and int(atrasados) > 0:
            push = _git("push", "origin", "main")
            if push.returncode != 0:
                return {"ok": False, "error": _error_de_push(push)}
            return {"ok": True, "pusheado": True, "detalle":
                    "el prompt ya estaba igual, pero había un ajuste anterior sin subir y lo subí. "
                    "Falta aprobar el deploy a mano en Railway."}
        return {"ok": True, "pusheado": False, "detalle":
                "no había nada que subir: el prompt que está en línea ya era igual a este."}

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit = _git("commit", "-m", f"Curador: ajuste system_prompt.md ({fecha})")
    if commit.returncode != 0:
        return {"ok": False, "error":
                "Guardé tu cambio acá en el PC, pero no pude prepararlo para subirlo. "
                "Sácale una foto a esta pantalla y mándamela. "
                f"(detalle técnico: {commit.stderr.strip()[:200]})"}

    push = _git("push", "origin", "main")
    if push.returncode != 0:
        return {"ok": False, "error": _error_de_push(push)}
    return {"ok": True, "pusheado": True,
            "detalle": "el cambio ya está subido. Falta aprobar el deploy a mano en Railway."}


def _error_de_push(push):
    """El push es lo último y lo que más formas tiene de fallar. Cada motivo lleva la
    ÚNICA acción que Christian puede hacer al respecto; el detalle técnico va al final
    y entre paréntesis, para que se pueda leer en una foto de WhatsApp sin entenderlo."""
    salida = f"{push.stderr}\n{push.stdout}".lower()
    if "authentication" in salida or "could not read username" in salida or "403" in salida:
        return ("Tu cambio quedó guardado acá en el PC, pero GitHub no me dejó subirlo: no "
                "reconoció el usuario de este computador. Esto lo arreglo yo, avísame. "
                f"(detalle técnico: {push.stderr.strip()[:200]})")
    if "non-fast-forward" in salida or "rejected" in salida:
        return ("Tu cambio quedó guardado acá en el PC, pero GitHub lo rechazó porque alguien "
                "subió algo justo antes. Aprieta Guardar de nuevo: la segunda vez suele pasar. "
                f"(detalle técnico: {push.stderr.strip()[:200]})")
    return ("Tu cambio quedó guardado acá en el PC, pero no pude subirlo a GitHub. Fíjate que "
            "este computador tenga internet y aprieta Guardar de nuevo; si sigue igual, sácale "
            f"una foto a esta pantalla. (detalle técnico: {push.stderr.strip()[:200]})")


# ===========================================================================
# FastAPI
# ===========================================================================
app = FastAPI(title="Curador de prompts — Donde el Patata")


class MarcaIn(BaseModel):
    turno_id: int
    nota: str = ""
    deberia: str = ""


class VeredictoIn(BaseModel):
    pegado: str


class ResolverIn(BaseModel):
    nota: str = ""


class PromptIn(BaseModel):
    contenido: str


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((AQUI / "curador.html").read_text(encoding="utf-8"))


@app.get("/api/estado")
def api_estado():
    con = _con()
    try:
        conteos = dict(con.execute(
            "SELECT estado, COUNT(*) FROM conversaciones GROUP BY estado").fetchall())
        return {
            "config_ok": bool(_headers_panel()) and bool(_bot_url()),
            "bot_url": _bot_url(),
            "ultima_sync": _get_estado(con, "ultima_sync"),
            "pendientes": conteos.get("pendiente", 0),
            "malas": conteos.get("mala", 0),
            "para_gonzalo": conteos.get("para_gonzalo", 0),
            "ok": conteos.get("ok", 0),
            "ok_recientes": con.execute(
                "SELECT COUNT(*) FROM conversaciones WHERE estado='ok' AND "
                "COALESCE(resuelto_en, fin) >= ?", (_desde_dias(DIAS_RESUELTOS),)).fetchone()[0],
            "dias_resueltos": DIAS_RESUELTOS,
            # Si falta el clon, guardar el prompt NUNCA va a poder subir. Se responde acá
            # —al abrir la app— y no recién al guardar, porque descubrirlo al guardar es
            # descubrirlo cuando ya se hizo el trabajo. Christian dijo el 11-ago que el
            # curador "no dijo nada de alertas, solo avanzó y no hizo nada": el aviso
            # existía, pero era un renglón chico al lado del botón y llegaba tarde.
            "despliegue_ok": (DESPLIEGUE / ".git").exists(),
            "despliegue_ruta": str(DESPLIEGUE),
        }
    finally:
        con.close()


@app.post("/api/sincronizar")
def api_sincronizar():
    return sincronizar()


@app.get("/api/casos-humanos")
def api_casos_humanos():
    return obtener_casos_humanos()


@app.get("/api/avisos-jefes")
def api_avisos_jefes():
    return obtener_avisos_jefes()


DIAS_RESUELTOS = 7


def _desde_dias(dias):
    return (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")


@app.get("/api/conversaciones")
def api_conversaciones(estado: str = "todas", todos: bool = False):
    con = _con()
    try:
        cols = "id, telefono, inicio, fin, estado, nota, resuelto_en"
        ocultos = 0
        if estado == "todas":
            filas = con.execute(
                f"SELECT {cols} FROM conversaciones ORDER BY fin DESC LIMIT 300").fetchall()
        elif estado == "ok" and not todos:
            # Resueltos: solo los recientes, del mas nuevo al mas viejo por fecha de
            # resolucion. Lo demas sigue en la base; se ve con "ver todos".
            desde = _desde_dias(DIAS_RESUELTOS)
            filas = con.execute(
                f"SELECT {cols} FROM conversaciones WHERE estado='ok' AND "
                "COALESCE(resuelto_en, fin) >= ? ORDER BY COALESCE(resuelto_en, fin) DESC",
                (desde,)).fetchall()
            ocultos = con.execute(
                "SELECT COUNT(*) FROM conversaciones WHERE estado='ok' AND "
                "COALESCE(resuelto_en, fin) < ?", (desde,)).fetchone()[0]
        else:
            filas = con.execute(
                f"SELECT {cols} FROM conversaciones WHERE estado=? ORDER BY fin DESC LIMIT 300",
                (estado,)).fetchall()
        out = []
        for cid, tel, ini, fin, est, nota, resuelto in filas:
            n = con.execute("SELECT COUNT(*) FROM turnos_cache WHERE conversacion_id=?", (cid,)).fetchone()[0]
            # Primer mensaje del cliente: para saber de que se trata sin abrirla.
            primero = con.execute(
                "SELECT texto FROM turnos_cache WHERE conversacion_id=? AND rol='usuario' "
                "ORDER BY id ASC LIMIT 1", (cid,)).fetchone()
            out.append({"id": cid, "telefono": tel, "inicio": ini, "fin": fin,
                        "estado": est, "nota": nota, "turnos": n, "resuelto_en": resuelto,
                        "vista_previa": (primero[0] if primero else "")[:90]})
        return {"conversaciones": out, "ocultos": ocultos, "dias": DIAS_RESUELTOS}
    finally:
        con.close()


@app.get("/api/conversaciones/{conv_id}")
def api_conversacion_detalle(conv_id: int):
    con = _con()
    try:
        cabeza = con.execute(
            "SELECT id, telefono, inicio, fin, estado, turno_fallo_id, nota, resuelto_en "
            "FROM conversaciones WHERE id=?", (conv_id,)).fetchone()
        if not cabeza:
            raise HTTPException(404, "conversación no encontrada")
        turnos = con.execute(
            "SELECT id, rol, texto, creado_en FROM turnos_cache WHERE conversacion_id=? ORDER BY id ASC",
            (conv_id,)).fetchall()
        return {
            "id": cabeza[0], "telefono": cabeza[1], "inicio": cabeza[2], "fin": cabeza[3],
            "estado": cabeza[4], "turno_fallo_id": cabeza[5], "nota": cabeza[6],
            "resuelto_en": cabeza[7],
            "turnos": [{"id": t[0], "rol": t[1], "texto": t[2], "creado_en": t[3]} for t in turnos],
        }
    finally:
        con.close()


@app.post("/api/conversaciones/{conv_id}/marcar")
def api_marcar(conv_id: int, cuerpo: MarcaIn):
    con = _con()
    try:
        turno = con.execute("SELECT rol FROM turnos_cache WHERE id=? AND conversacion_id=?",
                            (cuerpo.turno_id, conv_id)).fetchone()
        if not turno:
            raise HTTPException(404, "ese turno no pertenece a esta conversación")
        if turno[0] != "asistente":
            raise HTTPException(400, "solo se puede marcar como fallo un mensaje del BOT")
        con.execute("UPDATE conversaciones SET estado='mala', turno_fallo_id=?, nota=?, "
                    "deberia=? WHERE id=?",
                    (cuerpo.turno_id, cuerpo.nota, cuerpo.deberia, conv_id))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@app.post("/api/conversaciones/{conv_id}/resolver")
def api_resolver(conv_id: int, cuerpo: ResolverIn):
    con = _con()
    try:
        con.execute("UPDATE conversaciones SET estado='ok', nota=? WHERE id=?", (cuerpo.nota, conv_id))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@app.post("/api/conversaciones/{conv_id}/reintentar")
def api_reintentar(conv_id: int):
    con = _con()
    try:
        fila = con.execute("SELECT turno_fallo_id FROM conversaciones WHERE id=?", (conv_id,)).fetchone()
        if not fila or fila[0] is None:
            raise HTTPException(400, "esta conversación no tiene un turno marcado como fallo")
        turno_fallo_id = fila[0]
    finally:
        con.close()
    resultado = _reintentar(conv_id, turno_fallo_id)
    if not resultado["ok"]:
        raise HTTPException(400, resultado["error"])
    return resultado


@app.get("/api/prompt")
def api_get_prompt():
    # Abrir el editor es también el momento de ponerse al día con GitHub: es la salida
    # del candado de _sincronizar_despliegue, y el único paso que Christian ya hace solo
    # (abrir el prompt) sin que haya que enseñarle nada nuevo.
    aviso = _traer_prompt_de_github()
    return {"contenido": prototipo.SYSTEM_PROMPT.read_text(encoding="utf-8"), **aviso}


def _guardar_prompt(contenido):
    """Respaldo + escritura local + push. Sale de api_post_prompt (19-ago-2026) para que
    el Meson aplique un veredicto por EXACTAMENTE el mismo camino que un ajuste a mano:
    dos caminos distintos hacia el mismo archivo terminan siendo dos comportamientos
    distintos, y solo uno queda probado."""

    respaldo = prototipo.SYSTEM_PROMPT.with_name(
        f"system_prompt.md.bak_{datetime.now().strftime('%Y%m%d%H%M%S')}")
    respaldo.write_text(prototipo.SYSTEM_PROMPT.read_text(encoding="utf-8"), encoding="utf-8")

    # SIN newline="\n" acá a propósito: bot/system_prompt.md es el archivo LOCAL de
    # Windows (el que usa el .exe de Christian), viene en CRLF -- dejar que Python
    # traduzca \n -> os.linesep en el write() mantiene esa convención. El forzado a
    # LF es SOLO para la copia que recibe despliegue_railway (Linux), en
    # _sincronizar_despliegue. Bug real (04-ago): forzar LF acá también convirtió
    # el archivo local de CRLF a LF sin querer, con contenido idéntico.
    prototipo.SYSTEM_PROMPT.write_text(contenido, encoding="utf-8")

    resultado_push = _sincronizar_despliegue(contenido)
    return {"respaldo": respaldo.name, "push": resultado_push}


@app.post("/api/prompt")
def api_post_prompt(cuerpo: PromptIn):
    contenido = cuerpo.contenido.replace("\r\n", "\n")
    if not contenido.strip():
        raise HTTPException(400, "el prompt no puede quedar vacío")

    # Mismo candado que ya protegia a Curar automatico (13-sep-2026), extendido acá
    # (18-sep-2026): antes SOLO ese camino corría el banco rápido antes de guardar, así
    # que un ajuste a mano -- el que de verdad se usa casi siempre, incluida esta misma
    # sesión -- podía romper otro escenario sin que nadie se enterara hasta que "se
    # rompe en la realidad". Es justo el hueco que _guardar_prompt decía evitar ("dos
    # caminos... terminan siendo dos comportamientos distintos, y solo uno queda
    # probado") pero que el banco rápido, agregado después, no había cerrado.
    # ⚠️ Esto NO vuelve confiable la validación: el banco tiene escenarios ya conocidos
    # como inestables (~1 de 6 falla solo, ver CLAUDE.md #4) y choca con el tope de
    # tokens por minuto de vez en cuando. Un "rompe" acá es una señal para mirar, no un
    # veredicto — si parece falso positivo, correlo de nuevo antes de descartar el ajuste.
    banco_ok, banco_resumen = _correr_banco_rapido(contenido)
    if not banco_ok:
        return {"ok": True, "banco_ok": False, "banco_resumen": banco_resumen,
                "mensaje": f"El banco rápido encontró un problema y NO se guardó nada: "
                           f"{banco_resumen} Tu texto sigue en el editor, el archivo "
                           f"real no se tocó."}

    return {"ok": True, "banco_ok": True, "banco_resumen": banco_resumen,
            **_guardar_prompt(contenido)}


# ===========================================================================
# El Meson: el sobre y el veredicto (19-ago-2026)
#
# El ciclo que faltaba. Antes, la nota de Christian moria en curaciones.db y la unica
# forma de tocar lo que el bot aprendio era un textarea con 451 lineas crudas. Ahora:
# el Meson arma el sobre, Christian lo pega en el Curador (su proyecto de ChatGPT),
# pega la respuesta de vuelta, y el veredicto se aplica solo si pasa los candados.
# El contrato completo esta en CONTRATO_SOBRE_Y_VEREDICTO.md.
# ===========================================================================
def _guardar_papel(conv_id, tipo, texto, extra=""):
    con = _con()
    try:
        con.execute("INSERT INTO papeles (conversacion_id, tipo, texto, extra, creado_en) "
                    "VALUES (?,?,?,?,?)",
                    (conv_id, tipo, texto, extra,
                     datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()


def _armar_sobre_de_caso(conv_id: int):
    """El papel que Christian copia con un boton y pega en el Curador (o que se le
    manda directo a la API en el camino automático). Devuelve (texto, huella)."""
    con = _con()
    try:
        fila = con.execute("SELECT turno_fallo_id, nota, deberia FROM conversaciones "
                           "WHERE id=?", (conv_id,)).fetchone()
        if not fila:
            raise HTTPException(404, "no encontré ese caso")
        if fila[0] is None:
            raise HTTPException(400, "primero marca cuál fue el mensaje del bot que "
                                     "salió mal")
        turnos = [{"id": i, "rol": r, "texto": x, "creado_en": c}
                  for i, r, x, c in con.execute(
                      "SELECT id, rol, texto, creado_en FROM turnos_cache "
                      "WHERE conversacion_id=? ORDER BY id", (conv_id,)).fetchall()]
    finally:
        con.close()
    instrucciones = prototipo.SYSTEM_PROMPT.read_text(encoding="utf-8")
    h = papel.huella(instrucciones)
    texto = papel.armar_sobre(conv_id, turnos, fila[0], fila[1], fila[2], instrucciones)
    _guardar_papel(conv_id, "sobre", texto, h)
    return texto, h


@app.get("/api/casos/{conv_id}/sobre")
def api_sobre(conv_id: int):
    texto, h = _armar_sobre_de_caso(conv_id)
    return {"texto": texto, "huella": h}


def _procesar_veredicto(conv_id: int, pegado: str):
    """La respuesta del Curador, pegada entera (a mano, o traída sola por el camino
    automático). Si el veredicto pasa los candados y dice CAMBIAR, se aplica y se
    sube: de ahi en adelante manda el CI, que corre el banco contra el modelo real, y
    Railway no despliega en rojo (Wait for CI)."""
    instrucciones = prototipo.SYSTEM_PROMPT.read_text(encoding="utf-8")
    h = papel.huella(instrucciones)
    try:
        v = papel.leer_veredicto(pegado)
        decision = papel.validar_veredicto(v, conv_id, h)
    except papel.ErrorDeVeredicto as e:
        raise HTTPException(400, str(e))
    motivo = v.get("motivo", "")
    _guardar_papel(conv_id, "veredicto", pegado, decision)

    if decision != "CAMBIAR":
        estado = "ok" if decision == "NO_CAMBIAR" else "para_gonzalo"
        con = _con()
        try:
            con.execute("UPDATE conversaciones SET estado=?, nota=? WHERE id=?",
                        (estado, motivo, conv_id))
            con.commit()
        finally:
            con.close()
        return {"decision": decision, "motivo": motivo, "aplicado": False,
                "mensaje": ("Listo, el caso queda cerrado." if decision == "NO_CAMBIAR"
                            else "Esto no se arregla enseñándole al bot: lo tiene que "
                                 "ver Gonzalo. Ya se lo dejé anotado.")}

    try:
        nuevo, detalle = papel.aplicar_cambios(instrucciones, v["cambios"])
    except papel.ErrorDeVeredicto as e:
        raise HTTPException(400, str(e))
    resumen = papel.resumen_para_christian(instrucciones, nuevo)

    # Banco rapido ANTES de aplicar (13-sep-2026): mas barato que enterarse por el CI
    # 25 min despues, y esto SI dice que escenario se rompio -- el CI en rojo, sin mas,
    # deja el caso "para Gonzalo" sin ninguna pista de por que.
    banco_ok, banco_resumen = _correr_banco_rapido(nuevo)
    if not banco_ok:
        con = _con()
        try:
            con.execute("UPDATE conversaciones SET estado='mala', nota=? WHERE id=?",
                        (f"{motivo}\n\nEl banco rápido frenó este cambio antes de "
                         f"aplicarlo: {banco_resumen}", conv_id))
            con.commit()
        finally:
            con.close()
        return {"decision": decision, "motivo": motivo, "aplicado": False,
                "banco_ok": False,
                "mensaje": f"El banco rápido encontró un problema y NO se aplicó nada: "
                           f"{banco_resumen} Pídele al Curador un cambio más chico, o "
                           "dejalo para revisar a mano."}

    resultado = _guardar_prompt(nuevo)
    push = resultado.get("push") or {}
    if not push.get("ok"):
        raise HTTPException(400, push.get("error") or "no pude subir el cambio")

    commit = _git_despliegue("rev-parse", "HEAD").stdout.strip()
    con = _con()
    try:
        _set_estado(con, "esperando_commit", commit)
        _set_estado(con, "esperando_desde", datetime.now().isoformat(timespec="seconds"))
        _set_estado(con, "esperando_caso", conv_id)
        con.execute("UPDATE conversaciones SET estado='ok', nota=? WHERE id=?",
                    (motivo, conv_id))
        con.commit()
    finally:
        con.close()
    _guardar_papel(conv_id, "aplicado", nuevo, commit[:12])
    return {"decision": decision, "motivo": motivo, "aplicado": True,
            "resumen": resumen, "detalle": detalle, "commit": commit[:7],
            "mensaje": "Se lo mandé a probar. En unos minutos te digo si lo aprendió."}


@app.post("/api/casos/{conv_id}/veredicto")
def api_veredicto(conv_id: int, cuerpo: VeredictoIn):
    return _procesar_veredicto(conv_id, cuerpo.pegado)


@app.post("/api/casos/{conv_id}/curar_automatico")
def api_curar_automatico(conv_id: int):
    """Salta la vuelta manual por el proyecto de ChatGPT de Christian (13-sep-2026,
    pedido de Gonza): arma el sobre y se lo manda directo a la API de OpenAI con la
    key dedicada del bot -- mismos candados que el camino manual, incluido el de
    voseo, con reintento automatico si alguno rechaza la respuesta.

    A PROPOSITO no aplica nada: devuelve el veredicto ya validado para que se
    revise, igual que Christian revisaria lo que le devuelve ChatGPT antes de
    pegarlo. Aplicarlo es una accion aparte (POST /veredicto, la misma que usa el
    camino manual) -- probando esto el 13-sep, Gonza encontro que aplicar solo, sin
    poder decir 'este no' antes de que se suba, es quedarse sin poder decidir."""
    texto_sobre, huella = _armar_sobre_de_caso(conv_id)
    try:
        pegado = _consultar_curador_automatico(texto_sobre, conv_id, huella)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    v = papel.leer_veredicto(pegado)
    decision = papel.validar_veredicto(v, conv_id, huella)
    return {"pegado": pegado, "decision": decision, "motivo": v.get("motivo", "")}


MINUTOS_ESPERA_APRENDIZAJE = 25


@app.get("/api/aprendizaje")
def api_aprendizaje():
    """Si el cambio llego a produccion o no. Se mira lo que el bot esta CORRIENDO
    (/salud publica su commit), no si el CI termino: preguntarle a GitHub si el check
    paso necesitaria un token nuevo en el PC del local y contestaria otra cosa."""
    import urllib.request
    con = _con()
    try:
        sha = _get_estado(con, "esperando_commit")
        desde = _get_estado(con, "esperando_desde")
        caso = _get_estado(con, "esperando_caso")
    finally:
        con.close()
    if not sha:
        return {"estado": "nada"}
    minutos = round((_horas_entre(desde, datetime.now().isoformat(timespec="seconds")) or 0) * 60)
    try:
        with urllib.request.urlopen(f"{_bot_url()}/salud", timeout=6) as r:
            vivo = json.loads(r.read().decode("utf-8")).get("commit") or ""
    except Exception:
        return {"estado": "sin_conexion", "minutos": minutos, "caso": caso}
    if vivo[:12] == sha[:12]:
        con = _con()
        try:
            _set_estado(con, "esperando_commit", "")
            con.commit()
        finally:
            con.close()
        return {"estado": "aprendido", "minutos": minutos, "caso": caso}
    if minutos >= MINUTOS_ESPERA_APRENDIZAJE:
        return {"estado": "no_llego", "minutos": minutos, "caso": caso}
    return {"estado": "esperando", "minutos": minutos, "caso": caso}


if __name__ == "__main__":
    import uvicorn
    port = 8002
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--open" in sys.argv or getattr(sys, "frozen", False):  # el .exe abre el navegador solo
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port)

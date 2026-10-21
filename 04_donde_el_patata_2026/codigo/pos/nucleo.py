#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nucleo.py — Núcleo común de pedidos del POS de "Donde el Patata".
=================================================================
Es el CORAZÓN donde convergen los 3 canales (bot WhatsApp, web cliente, cajero
presencial). Cada canal arma un pedido "crudo" (qué pidió el cliente) y se lo
entrega al núcleo; el núcleo:
  1. lo TARIFICA con el motor compartido (`bot/tarificar.py`, fuente de verdad de precios),
  2. le asigna id + estado y lo GUARDA (SQLite),
  3. expone la COLA de pedidos y la transición de ESTADOS hacia la cocina,
  4. arma la COMANDA en texto.

No tiene interfaz (ni web ni cajero): esas son capas que se enchufan encima.
Sin dependencias externas: solo librería estándar + el tarificador.
"""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import sys
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))                   # para importar 'rutas'
from rutas import MENU_JSON, DB_DEFAULT, BOT, BACKUPS    # noqa: E402  (rutas conscientes de .py vs .exe)
# El motor de precios y la carta son COMPARTIDOS (viven en bot/). Reutilizarlos, no duplicarlos.
sys.path.insert(0, str(BOT))                    # dev: para importar tarificar
from tarificar import Tarificador, fmt_pesos, label_variante    # noqa: E402

# --- Estados del pedido y transiciones permitidas (máquina de estados) ---
ESTADOS = ["recibido", "confirmado", "en_preparacion", "listo", "entregado", "cancelado"]
# Flujo del cajero (2026): recibido -> [A cocina] en_preparacion -> [Entregado] entregado.
# 'confirmado' y 'listo' quedan permitidos por compatibilidad (pedidos viejos / el bot),
# pero el cajero ya no los usa. 'A cocina' imprime; 'Entregado' saca de la cola (todas las
# modalidades). La confirmacion real de entrega del delivery la hara la app del repartidor.
TRANSICIONES = {
    "recibido":       {"en_preparacion", "confirmado", "cancelado"},
    "confirmado":     {"en_preparacion", "cancelado"},
    "en_preparacion": {"entregado", "listo", "cancelado"},
    "listo":          {"entregado"},
    "entregado":      set(),   # final
    "cancelado":      set(),   # final
}
CANALES = {"whatsapp", "web", "cajero"}

# Acciones del POS que un perfil puede tener permitidas (perfiles configurables, puntos 10/15)
PERMISOS = ["vender", "abrir_caja", "cerrar_caja", "editar_pedido", "redistribuir_pago",
            "ver_informe", "anular", "editar_menu", "gestionar_clientes", "gestionar_usuarios"]


def _ahora():
    return datetime.now().isoformat(timespec="seconds")


def _desglose_pagos(pedido):
    """Monto por metodo de pago de un pedido. Usa 'pagos' (lista {metodo, monto})
    si existe; si no, cae a 'forma_pago' por el total (retrocompatibilidad)."""
    pagos = pedido.get("pagos")
    if pagos:
        out = {}
        for pg in pagos:
            m = pg.get("metodo") or "sin especificar"
            out[m] = out.get(m, 0) + (pg.get("monto") or 0)
        return out
    fp = pedido.get("forma_pago") or "sin especificar"
    return {fp: pedido.get("total") or 0}


def _lineas_pago(pedido):
    """Lineas del/los metodo(s) de pago para mostrar BAJO el total. Uno solo: 'Pago: X'.
    Varios: 'Pago (mixto):' + cada metodo con su monto, de mayor a menor."""
    pagos = pedido.get("pagos")
    if pagos and len(pagos) > 1:
        out = ["Pago (mixto):"]
        for pg in sorted(pagos, key=lambda x: -(x.get("monto") or 0)):
            out.append(f"  - {pg.get('metodo')}: {fmt_pesos(pg.get('monto') or 0)}")
        return out
    if pagos and len(pagos) == 1:
        return [f"Pago: {pagos[0].get('metodo')}"]
    fp = pedido.get("forma_pago")
    return [f"Pago: {fp}"] if fp else []


def _fecha_hora_legible(iso):
    """'2026-06-28T14:30:00' -> '28-06-2026 14:30' (timestamp para la comanda)."""
    if not iso or len(iso) < 16:
        return iso or ""
    y, m, d = iso[:10].split("-")
    return f"{d}-{m}-{y} {iso[11:16]}"


class LoginBloqueado(Exception):
    """Demasiados fallos seguidos: el login queda en espera `segundos`."""
    def __init__(self, segundos):
        super().__init__(f"Demasiados intentos. Espera {segundos} s.")
        self.segundos = segundos


class NucleoPedidos:
    def __init__(self, db_path=DB_DEFAULT, menu_path=MENU_JSON):
        self.db_path = Path(db_path)
        self.tarificador = Tarificador.desde_json(menu_path)
        # etiqueta singular por categoria (para la comanda: 'Churrasco Italiano')
        self._etiqueta_cat = {c["nombre"]: c.get("etiqueta", "")
                              for c in self.tarificador.menu.get("categorias", [])}
        self._lock = threading.RLock()
        # check_same_thread=False: el POS lo usa un servidor web (cajero hoy, bot
        # mañana) que atiende en varios hilos. El RLock serializa el acceso a la
        # conexion para que no haya choques entre hilos.
        self._con = sqlite3.connect(self.db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._crear_tabla()

    def recargar_menu(self, tarificador):
        """Reemplaza el motor de precios/menu EN CALIENTE (tras editar el menu.json
        desde la pantalla de administracion), sin reiniciar el servidor."""
        self.tarificador = tarificador
        self._etiqueta_cat = {c["nombre"]: c.get("etiqueta", "")
                              for c in tarificador.menu.get("categorias", [])}

    def _crear_tabla(self):
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS pedidos (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                canal         TEXT NOT NULL,
                estado        TEXT NOT NULL,
                cliente       TEXT,
                modalidad     TEXT,
                total         INTEGER,
                creado_en     TEXT,
                actualizado_en TEXT,
                data          TEXT NOT NULL,
                caja_id       INTEGER
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS cajas (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                abierta_en     TEXT,
                cerrada_en     TEXT,
                fondo_inicial  INTEGER,
                estado         TEXT,
                contado_cierre INTEGER,
                data           TEXT
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                telefono       TEXT,
                nombre         TEXT,
                direccion      TEXT,
                solo_whatsapp  INTEGER,
                nombre_norm    TEXT,
                creado_en      TEXT,
                actualizado_en TEXT
            )
        """)
        # telefono unico cuando existe (los clientes de "local" pueden no tener telefono)
        self._con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cliente_tel ON clientes(telefono) "
                          "WHERE telefono IS NOT NULL AND telefono!=''")
        self._con.execute("CREATE INDEX IF NOT EXISTS idx_cliente_nom ON clientes(nombre_norm)")
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS perfiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT, permisos TEXT, es_admin INTEGER DEFAULT 0
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT, email TEXT, pin_hash TEXT,
                perfil_id INTEGER, activo INTEGER DEFAULT 1, creado_en TEXT
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS intentos_login (
                clave TEXT PRIMARY KEY, fallos INTEGER, rachas INTEGER, bloqueado_hasta TEXT
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS sesiones (
                token_hash TEXT PRIMARY KEY, usuario_id INTEGER,
                creada_en TEXT, expira_en TEXT
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                en TEXT, usuario TEXT, accion TEXT, pedido_id INTEGER, detalle TEXT
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS direcciones_cliente (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telefono TEXT NOT NULL, direccion TEXT NOT NULL,
                delivery_cobro INTEGER DEFAULT 0, usos INTEGER DEFAULT 1,
                creado_en TEXT, actualizado_en TEXT
            )
        """)
        self._con.execute("CREATE INDEX IF NOT EXISTS idx_direcciones_tel ON direcciones_cliente(telefono)")
        self._con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_direcciones_tel_dir "
                          "ON direcciones_cliente(telefono, direccion)")
        # Migracion suave: agregar caja_id a una tabla de pedidos vieja que no lo tenga.
        cols = [r["name"] for r in self._con.execute("PRAGMA table_info(pedidos)").fetchall()]
        if "caja_id" not in cols:
            self._con.execute("ALTER TABLE pedidos ADD COLUMN caja_id INTEGER")
        self._con.commit()
        self._seed_usuarios()
        self._migrar_direcciones_cliente()

    # ---------------------------------------------------------------
    # Pagos: uno o varios metodos que cubren el total (pago combinado)
    # ---------------------------------------------------------------
    @staticmethod
    def _normalizar_pagos(pagos, forma_pago, total):
        """Devuelve (lista_pagos, forma_pago_resumen).
        - Si 'pagos' viene (lista de {metodo, monto}), valida que sumen EXACTO el total.
        - Si no viene, deriva un unico pago desde 'forma_pago' por el total.
        forma_pago_resumen = el metodo si es uno solo, 'mixto' si son varios."""
        if pagos:
            limpia, suma = [], 0
            for pg in pagos:
                m = str(pg.get("metodo") or "").strip()
                try:
                    monto = int(pg.get("monto") or 0)
                except (TypeError, ValueError):
                    monto = 0
                if not m or monto <= 0:
                    continue
                limpia.append({"metodo": m, "monto": monto})
                suma += monto
            if limpia:
                if suma != int(total):
                    raise ValueError(
                        f"los pagos suman {suma}, pero el total es {int(total)}; deben coincidir")
                fp = limpia[0]["metodo"] if len(limpia) == 1 else "mixto"
                return limpia, fp
        if forma_pago:
            return [{"metodo": forma_pago, "monto": int(total)}], forma_pago
        return [], forma_pago

    @staticmethod
    def _monto_efectivo(pagos, forma_pago, total):
        """Cuanto del pedido se paga en EFECTIVO (base para el vuelto)."""
        if pagos:
            return sum(pg.get("monto", 0) for pg in pagos if pg.get("metodo") == "efectivo")
        return int(total) if forma_pago == "efectivo" else 0

    # ---------------------------------------------------------------
    # Crear un pedido (lo llama cualquier canal)
    # ---------------------------------------------------------------
    def crear(self, canal, cliente, modalidad, items, forma_pago=None,
              direccion=None, delivery_cobro=0, notas="", pago_con=None,
              mesa=None, telefono=None, pagos=None, hora_programada=None):
        """items: lista de {categoria, producto, variante, cantidad, notas}.
        El núcleo pone el precio con el tarificador (la verdad de precios).
        Si hay una caja abierta, el pedido queda asociado a ella (caja_id).
        modalidad: retiro | delivery | local (local lleva 'mesa' opcional).
        telefono: si viene (delivery/retiro), el pedido se liga al cliente y la base se actualiza.
        pago_con: con cuánto paga el cliente (efectivo) -> el núcleo calcula el vuelto.
        hora_programada: "HH:MM" si el cliente pidió el pedido para más tarde (04-ago-2026,
        P29). Entra a la cola YA (visible, no se esconde) pero marcado -- la pantalla lo
        pinta distinto para que cocina no lo prepare antes de tiempo; el bot manda un
        recordatorio cerca de la hora (ver _hilo_recordar_programados, webhook.py)."""
        if canal not in CANALES:
            raise ValueError(f"canal invalido: {canal} (use {sorted(CANALES)})")
        if modalidad not in ("retiro", "delivery", "local"):
            raise ValueError(f"modalidad invalida: {modalidad}")
        if hora_programada is not None and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", hora_programada):
            raise ValueError(f"hora_programada invalida: {hora_programada!r} (use HH:MM)")

        cuenta = self.tarificador.tarificar_pedido({
            "items": items, "delivery_cobro": delivery_cobro, "total": 0,
        })
        pagos_norm, forma_pago = self._normalizar_pagos(pagos, forma_pago, cuenta["total"])
        ahora = _ahora()
        caja = self.caja_abierta()
        caja_id = caja["id"] if caja else None
        pedido = {
            "canal": canal, "estado": "recibido", "cliente": cliente,
            "modalidad": modalidad, "direccion": direccion, "mesa": mesa,
            "telefono": self.norm_telefono(telefono) or None, "forma_pago": forma_pago,
            "pagos": pagos_norm,
            "items": cuenta["items"], "delivery_cobro": cuenta["delivery_cobro"],
            "total": cuenta["total"], "problemas": cuenta["problemas"], "notas": notas,
            "caja_id": caja_id, "hora_programada": hora_programada,
            "creado_en": ahora, "actualizado_en": ahora,
            "historial_estados": [{"estado": "recibido", "en": ahora}],
        }
        # Vuelto: solo sobre la parte en EFECTIVO (no el total, por si el pago es mixto).
        efectivo = self._monto_efectivo(pagos_norm, forma_pago, cuenta["total"])
        if pago_con not in (None, "") and efectivo > 0:
            try:
                pc = int(pago_con)
                pedido["pago_con"] = pc
                pedido["vuelto"] = max(0, pc - efectivo)
            except (TypeError, ValueError):
                pass
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO pedidos (canal, estado, cliente, modalidad, total, creado_en, "
                "actualizado_en, data, caja_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (canal, "recibido", cliente, modalidad, cuenta["total"], ahora, ahora,
                 json.dumps(pedido, ensure_ascii=False), caja_id),
            )
            self._con.commit()
        pedido["id"] = cur.lastrowid
        # Mantener la base de clientes al día: delivery/retiro traen teléfono.
        if pedido["telefono"]:
            try:
                self.guardar_cliente(telefono=pedido["telefono"], nombre=cliente, direccion=direccion)
                monto, resto = self._partir_direccion(direccion)
                if resto:
                    self.guardar_direccion_cliente(pedido["telefono"], resto, monto)
            except Exception:
                pass
        return pedido

    def editar(self, pedido_id, cliente, modalidad, items, forma_pago=None,
               direccion=None, delivery_cobro=0, notas="", pago_con=None,
               mesa=None, telefono=None, pagos=None, usuario=None):
        """Reemplaza el contenido de un pedido ya creado (conserva id, estado, caja, canal).
        Re-tarifica. Solo se permite si la caja del pedido SIGUE ABIERTA (o no tiene caja),
        para no descuadrar un día ya cerrado."""
        p = self.obtener(pedido_id)
        if not p:
            raise ValueError(f"pedido {pedido_id} no existe")
        if modalidad not in ("retiro", "delivery", "local"):
            raise ValueError(f"modalidad invalida: {modalidad}")
        if p.get("caja_id"):
            caja = self.obtener_caja(p["caja_id"])
            if caja and caja["estado"] != "abierta":
                raise ValueError("la caja de este pedido ya está cerrada; no se puede editar")
        cuenta = self.tarificador.tarificar_pedido({
            "items": items, "delivery_cobro": delivery_cobro, "total": 0})
        pagos_norm, forma_pago = self._normalizar_pagos(pagos, forma_pago, cuenta["total"])
        ahora = _ahora()
        p.update({
            "cliente": cliente, "modalidad": modalidad, "direccion": direccion, "mesa": mesa,
            "telefono": self.norm_telefono(telefono) or None, "forma_pago": forma_pago,
            "pagos": pagos_norm,
            "items": cuenta["items"], "delivery_cobro": cuenta["delivery_cobro"],
            "total": cuenta["total"], "problemas": cuenta["problemas"], "notas": notas,
            "actualizado_en": ahora,
        })
        p.setdefault("historial_estados", []).append(
            {"estado": p["estado"], "en": ahora, "editado": True})
        p.pop("pago_con", None)
        p.pop("vuelto", None)
        efectivo = self._monto_efectivo(pagos_norm, forma_pago, cuenta["total"])
        if pago_con not in (None, "") and efectivo > 0:
            try:
                pc = int(pago_con)
                p["pago_con"] = pc
                p["vuelto"] = max(0, pc - efectivo)
            except (TypeError, ValueError):
                pass
        with self._lock:
            self._con.execute(
                "UPDATE pedidos SET cliente=?, modalidad=?, total=?, actualizado_en=?, data=? WHERE id=?",
                (cliente, modalidad, cuenta["total"], ahora,
                 json.dumps(p, ensure_ascii=False), pedido_id))
            self._con.commit()
        if p["telefono"]:
            try:
                self.guardar_cliente(telefono=p["telefono"], nombre=cliente, direccion=direccion)
                monto, resto = self._partir_direccion(direccion)
                if resto:
                    self.guardar_direccion_cliente(p["telefono"], resto, monto)
            except Exception:
                pass
        self.registrar_auditoria(usuario, "editar_pedido", pedido_id,
                                 f"nuevo total ${cuenta['total']}, pago '{forma_pago}'")
        return p

    def registrar_pagos(self, pedido_id, pagos, pago_con=None, usuario=None):
        """Redistribuye SOLO el pago de un pedido entre metodos (pago combinado),
        sin tocar items ni total. Permitido mientras la caja del pedido siga abierta
        (o si el pedido no tiene caja). Es la capa que usa el 'Informe de ventas':
        un empleado puede repartir el 100% del total en metodos, nada mas.
        Si hay efectivo y viene 'pago_con', recalcula el vuelto sobre la parte efectivo."""
        p = self.obtener(pedido_id)
        if not p:
            raise ValueError(f"pedido {pedido_id} no existe")
        if p.get("caja_id"):
            caja = self.obtener_caja(p["caja_id"])
            if caja and caja["estado"] != "abierta":
                raise ValueError("la caja de este pedido ya esta cerrada; no se puede cambiar el pago")
        pagos_norm, fp = self._normalizar_pagos(pagos, None, p["total"])
        if not pagos_norm:
            raise ValueError("indica al menos un metodo de pago que cubra el total")
        ahora = _ahora()
        p["pagos"] = pagos_norm
        p["forma_pago"] = fp
        p.pop("pago_con", None)
        p.pop("vuelto", None)
        efectivo = self._monto_efectivo(pagos_norm, fp, p["total"])
        if pago_con not in (None, "") and efectivo > 0:
            try:
                pc = int(pago_con)
                p["pago_con"] = pc
                p["vuelto"] = max(0, pc - efectivo)
            except (TypeError, ValueError):
                pass
        p["actualizado_en"] = ahora
        p.setdefault("historial_estados", []).append(
            {"estado": p["estado"], "en": ahora, "pago_editado": True})
        with self._lock:
            self._con.execute(
                "UPDATE pedidos SET actualizado_en=?, data=? WHERE id=?",
                (ahora, json.dumps(p, ensure_ascii=False), pedido_id))
            self._con.commit()
        self.registrar_auditoria(usuario, "redistribuir_pago", pedido_id,
                                 f"nuevo pago '{fp}' (${p['total']})")
        return p

    # ---------------------------------------------------------------
    # Leer / mover de estado
    # ---------------------------------------------------------------
    def obtener(self, pedido_id):
        with self._lock:
            row = self._con.execute("SELECT data FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return None
        p = json.loads(row["data"])
        p["id"] = pedido_id
        return p

    def transicionar(self, pedido_id, nuevo_estado, usuario=None):
        if nuevo_estado not in ESTADOS:
            raise ValueError(f"estado inexistente: {nuevo_estado}")
        with self._lock:
            p = self.obtener(pedido_id)
            if not p:
                raise ValueError(f"pedido {pedido_id} no existe")
            actual = p["estado"]
            if nuevo_estado not in TRANSICIONES[actual]:
                raise ValueError(
                    f"transicion invalida: {actual} -> {nuevo_estado} "
                    f"(permitidas desde '{actual}': {sorted(TRANSICIONES[actual]) or 'ninguna, es final'})")
            ahora = _ahora()
            p["estado"] = nuevo_estado
            p["actualizado_en"] = ahora
            p["historial_estados"].append({"estado": nuevo_estado, "en": ahora})
            self._con.execute(
                "UPDATE pedidos SET estado=?, actualizado_en=?, data=? WHERE id=?",
                (nuevo_estado, ahora, json.dumps(p, ensure_ascii=False), pedido_id))
            self._con.commit()
        if nuevo_estado == "cancelado":   # anular: la accion sensible que hay que auditar
            self.registrar_auditoria(usuario, "anular", pedido_id,
                                     f"pedido #{pedido_id} (${p['total']}) cancelado, estaba '{actual}'")
        # Gancho opcional POS -> WMS (Capa 5): el orquestador (cajero.py) puede conectar
        # `nucleo._post_transicion = inv.sincronizar_estado` para que la venta REAL
        # descuente stock ('A cocina') o mande a limbo lo confeccionado ('cancelado').
        # A prueba de fallos: si el WMS revienta, la venta NO se ve afectada.
        cb = getattr(self, "_post_transicion", None)
        if cb:
            try:
                cb(pedido_id, nuevo_estado, usuario)
            except Exception as e:
                print(f"[nucleo][AVISO] hook post-transicion fallo (venta intacta): {e}")
        return p

    def cola(self, estados=None):
        """Pedidos activos para la cocina (no entregados ni cancelados). El cajero avanza
        recibido -> en_preparacion (A cocina) -> entregado (Entregado, sale de la cola)."""
        if estados is None:
            estados = ["recibido", "confirmado", "en_preparacion", "listo"]
        marcas = ",".join("?" * len(estados))
        with self._lock:
            filas = self._con.execute(
                f"SELECT id FROM pedidos WHERE estado IN ({marcas}) ORDER BY creado_en", estados).fetchall()
        return [self.obtener(f["id"]) for f in filas]

    # ---------------------------------------------------------------
    # Caja: apertura / cierre / resumen del día (cuadre de efectivo)
    # ---------------------------------------------------------------
    def abrir_caja(self, fondo_inicial, cajero=""):
        """Abre la caja con el efectivo inicial (fondo). Una sola caja abierta a la vez."""
        if self.caja_abierta():
            raise ValueError("ya hay una caja abierta; ciérrala antes de abrir otra")
        try:
            fondo = int(fondo_inicial)
        except (TypeError, ValueError):
            raise ValueError("fondo inicial inválido")
        ahora = _ahora()
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO cajas (abierta_en, cerrada_en, fondo_inicial, estado, "
                "contado_cierre, data) VALUES (?,?,?,?,?,?)",
                (ahora, None, fondo, "abierta", None,
                 json.dumps({"cajero": cajero}, ensure_ascii=False)),
            )
            self._con.commit()
            cid = cur.lastrowid
        return self.obtener_caja(cid)

    def obtener_caja(self, caja_id):
        with self._lock:
            row = self._con.execute("SELECT * FROM cajas WHERE id=?", (caja_id,)).fetchone()
        if not row:
            return None
        c = dict(row)
        try:
            c["data"] = json.loads(c.get("data") or "{}")
        except (TypeError, ValueError):
            c["data"] = {}
        return c

    def caja_abierta(self):
        """La caja abierta actual, o None si no hay ninguna."""
        with self._lock:
            row = self._con.execute(
                "SELECT id FROM cajas WHERE estado='abierta' ORDER BY id DESC LIMIT 1").fetchone()
        return self.obtener_caja(row["id"]) if row else None

    def resumen_caja(self, caja_id):
        """Resumen del día de una caja: ventas por forma de pago, totales y cuánto
        DEBERÍA haber en efectivo (fondo + ventas en efectivo). Excluye cancelados."""
        caja = self.obtener_caja(caja_id)
        if not caja:
            return None
        with self._lock:
            filas = self._con.execute(
                "SELECT data FROM pedidos WHERE caja_id=? AND estado!='cancelado'",
                (caja_id,)).fetchall()
        # forma_pago/pagos y total viven dentro del JSON 'data' (no son columnas).
        por_metodo, total_ventas = {}, 0
        for f in filas:
            try:
                d = json.loads(f["data"])
            except (TypeError, ValueError):
                continue
            t = d.get("total") or 0
            for m, monto in _desglose_pagos(d).items():
                por_metodo[m] = por_metodo.get(m, 0) + monto
            total_ventas += t
        efectivo_ventas = por_metodo.get("efectivo", 0)
        fondo = caja["fondo_inicial"] or 0
        esperado = fondo + efectivo_ventas
        contado = caja["contado_cierre"]
        # Ajuste por metodo NO efectivo (punto E2, 03-jul): tarjeta/transferencia no se
        # cuentan fisico, pero el cajero puede confirmarlas contra el resumen del posnet
        # o la cartola bancaria al cerrar. Vive en 'data' (sin columna nueva): se guarda
        # solo al cerrar, junto a 'cajero'.
        contado_otros = caja["data"].get("contado_otros") or {}
        descuadre_otros = {m: (v - por_metodo.get(m, 0))
                           for m, v in contado_otros.items() if v is not None}
        return {
            "caja_id": caja_id, "estado": caja["estado"],
            "fondo_inicial": fondo, "abierta_en": caja["abierta_en"],
            "cerrada_en": caja["cerrada_en"], "cajero": caja["data"].get("cajero", ""),
            "n_pedidos": len(filas), "total_ventas": total_ventas,
            "por_metodo": por_metodo, "efectivo_ventas": efectivo_ventas,
            "esperado_efectivo": esperado, "contado_cierre": contado,
            "descuadre": (contado - esperado) if contado is not None else None,
            "contado_otros": contado_otros, "descuadre_otros": descuadre_otros,
        }

    def cerrar_caja(self, contado_cierre, contado_otros=None):
        """Cierra la caja abierta con el efectivo contado y devuelve el resumen + descuadre.
        contado_otros: dict opcional {metodo: monto} con lo confirmado en tarjeta/
        transferencia (contra el posnet o la cartola bancaria) -- ninguno es obligatorio.
        Dispara un BACKUP automatico de la base (punto 15/21): si el backup falla (disco
        lleno, permisos...) NO impide cerrar la caja, solo queda registrado en el resultado."""
        caja = self.caja_abierta()
        if not caja:
            raise ValueError("no hay caja abierta")
        try:
            contado = int(contado_cierre)
        except (TypeError, ValueError):
            raise ValueError("efectivo contado inválido")
        otros = {}
        for m, v in (contado_otros or {}).items():
            try:
                otros[str(m)] = int(v)
            except (TypeError, ValueError):
                continue
        ahora = _ahora()
        data = dict(caja["data"])
        data["contado_otros"] = otros
        with self._lock:
            self._con.execute(
                "UPDATE cajas SET estado='cerrada', cerrada_en=?, contado_cierre=?, data=? WHERE id=?",
                (ahora, contado, json.dumps(data, ensure_ascii=False), caja["id"]))
            self._con.commit()
        resumen = self.resumen_caja(caja["id"])
        resumen["backup"] = self._backup_db()
        return resumen

    def _backup_db(self):
        """Copia de seguridad de pedidos.db (Online Backup API de sqlite3: segura aunque
        haya otros hilos escribiendo). No lanza excepcion si falla: devuelve el error para
        que quien llama decida que avisar, sin romper el flujo principal (cerrar la caja)."""
        try:
            BACKUPS.mkdir(parents=True, exist_ok=True)
            marca = datetime.now().strftime("%Y%m%d_%H%M%S")
            destino = BACKUPS / f"pedidos_{marca}.db"
            with self._lock:
                con_destino = sqlite3.connect(destino)
                try:
                    self._con.backup(con_destino)
                finally:
                    con_destino.close()
            return {"ok": True, "archivo": str(destino)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def listar_cajas(self, limite=30):
        """Historico de cajas (mas recientes primero, abiertas y cerradas), para el
        punto 12: consultar cierres pasados. Reusa resumen_caja (misma cuenta que se
        ve al cerrar), asi que la vista de historial y la de cierre nunca se desincronizan."""
        with self._lock:
            filas = self._con.execute(
                "SELECT id FROM cajas ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [self.resumen_caja(f["id"]) for f in filas]

    def ventas_periodo(self, desde=None, hasta=None):
        """Informe de ventas por rango de fechas (por 'creado_en', formato YYYY-MM-DD,
        ambos inclusive; None = sin limite por ese lado). Incluye TODOS los pedidos del
        rango (tambien entregados y cancelados, que ya no estan en la cola). Devuelve la
        lista + totales por metodo de pago. Los cancelados no suman a ventas."""
        with self._lock:
            filas = self._con.execute(
                "SELECT id, data FROM pedidos ORDER BY creado_en").fetchall()
        pedidos, por_metodo, total_ventas, total_cancelado = [], {}, 0, 0
        for f in filas:
            try:
                d = json.loads(f["data"])
            except (TypeError, ValueError):
                continue
            d["id"] = f["id"]
            fecha = (d.get("creado_en") or "")[:10]
            if desde and fecha < desde:
                continue
            if hasta and fecha > hasta:
                continue
            pedidos.append(d)
            if d.get("estado") == "cancelado":
                total_cancelado += d.get("total") or 0
                continue
            total_ventas += d.get("total") or 0
            for m, monto in _desglose_pagos(d).items():
                por_metodo[m] = por_metodo.get(m, 0) + monto
        return {"desde": desde, "hasta": hasta, "n_pedidos": len(pedidos),
                "total_ventas": total_ventas, "total_cancelado": total_cancelado,
                "por_metodo": por_metodo, "pedidos": pedidos}

    # ---------------------------------------------------------------
    # Clientes (base para delivery/retiro por teléfono; local por nombre)
    # ---------------------------------------------------------------
    @staticmethod
    def norm_telefono(tel):
        """Solo dígitos; si trae +56/56 adelante, toma los últimos 9 (móvil chileno)."""
        d = "".join(ch for ch in str(tel or "") if ch.isdigit())
        return d[-9:] if len(d) > 9 else d

    @staticmethod
    def _norm_nombre(nombre):
        s = unicodedata.normalize("NFKD", str(nombre or ""))
        s = "".join(c for c in s if not unicodedata.combining(c))
        return " ".join(s.lower().split())

    def buscar_cliente_telefono(self, telefono):
        tel = self.norm_telefono(telefono)
        if not tel:
            return None
        with self._lock:
            row = self._con.execute("SELECT * FROM clientes WHERE telefono=?", (tel,)).fetchone()
        return dict(row) if row else None

    def buscar_clientes_nombre(self, texto, limite=8):
        q = self._norm_nombre(texto)
        if not q:
            return []
        with self._lock:
            filas = self._con.execute(
                "SELECT * FROM clientes WHERE nombre_norm LIKE ? ORDER BY nombre LIMIT ?",
                (f"%{q}%", limite)).fetchall()
        return [dict(r) for r in filas]

    def buscar_clientes_telefono(self, prefijo, limite=8):
        """Clientes cuyo telefono EMPIEZA con lo digitado (para sugerir mientras se
        escribe el telefono en delivery/retiro, sin tener que tipear los 9 digitos)."""
        d = self.norm_telefono(prefijo)
        if not d:
            return []
        with self._lock:
            filas = self._con.execute(
                "SELECT * FROM clientes WHERE telefono LIKE ? ORDER BY telefono LIMIT ?",
                (f"{d}%", limite)).fetchall()
        return [dict(r) for r in filas]

    def guardar_cliente(self, telefono=None, nombre="", direccion="", solo_whatsapp=None):
        """Crea o actualiza un cliente. Llave: teléfono si viene; si no, por nombre."""
        tel = self.norm_telefono(telefono) or None
        nombre = (nombre or "").strip()
        direccion = (direccion or "").strip()
        nom_norm = self._norm_nombre(nombre)
        ahora = _ahora()
        with self._lock:
            existente = self._con.execute(
                "SELECT id FROM clientes WHERE telefono=?", (tel,)).fetchone() if tel else None
            if existente:
                self._con.execute(
                    "UPDATE clientes SET nombre=?, direccion=?, nombre_norm=?, actualizado_en=? "
                    "WHERE id=?", (nombre, direccion, nom_norm, ahora, existente["id"]))
            else:
                self._con.execute(
                    "INSERT INTO clientes (telefono, nombre, direccion, solo_whatsapp, "
                    "nombre_norm, creado_en, actualizado_en) VALUES (?,?,?,?,?,?,?)",
                    (tel, nombre, direccion, solo_whatsapp, nom_norm, ahora, ahora))
            self._con.commit()
        return (self.buscar_cliente_telefono(tel) if tel
                else {"telefono": None, "nombre": nombre, "direccion": direccion})

    # ---------------------------------------------------------------
    # Multiples direcciones por telefono (un cliente puede repartir a mas
    # de un lugar: casa, trabajo...). clientes.direccion sigue siendo "la
    # ultima usada" (nadie mas lo toca); esta tabla es el HISTORIAL completo
    # para el selector cuando hay mas de una guardada.
    # ---------------------------------------------------------------
    # El delimitador (parentesis o corchete) es OBLIGATORIO. Un numero suelto al
    # inicio de una direccion real chilena puede ser parte de la calle (ej. "12 de
    # Marzo", "5 de Febrero" - calles con nombre de fecha) o del numero de casa
    # escrito antes que la calle; NO se puede asumir que sea un monto de delivery
    # sin el corchete/parentesis que lo marque como tal (bug real encontrado y
    # corregido con Gonza: la version vieja sin este requisito capturaba 187
    # direcciones reales como si tuvieran un monto que nunca existio).
    _RE_PREFIJO_MONTO = re.compile(r"^[(\[]\s*\$?\s*(\d+)\s*[)\]]\s*")

    @classmethod
    def _partir_direccion(cls, direccion):
        """'(1500) Los Alamos 200' -> (1500, 'Los Alamos 200'). Sin prefijo -> (0, texto)."""
        direccion = (direccion or "").strip()
        m = cls._RE_PREFIJO_MONTO.match(direccion)
        if not m:
            return 0, direccion
        return int(m.group(1)), direccion[m.end():].strip()

    def _migrar_direcciones_cliente(self):
        """Una sola vez (si la tabla esta vacia): puebla el historial a partir de las
        direcciones ya guardadas en clientes.direccion (incluye los 4.421 importados)."""
        with self._lock:
            if self._con.execute("SELECT COUNT(*) FROM direcciones_cliente").fetchone()[0]:
                return
            filas = self._con.execute(
                "SELECT telefono, direccion FROM clientes WHERE telefono IS NOT NULL "
                "AND telefono!='' AND direccion IS NOT NULL AND direccion!=''").fetchall()
            ahora = _ahora()
            for f in filas:
                monto, resto = self._partir_direccion(f["direccion"])
                if not resto:
                    continue
                self._con.execute(
                    "INSERT OR IGNORE INTO direcciones_cliente "
                    "(telefono, direccion, delivery_cobro, usos, creado_en, actualizado_en) "
                    "VALUES (?,?,?,1,?,?)", (f["telefono"], resto, monto, ahora, ahora))
            self._con.commit()

    def guardar_direccion_cliente(self, telefono, direccion, delivery_cobro=0):
        """Guarda/actualiza una direccion del historial de ese telefono. Si la MISMA
        direccion (sin distinguir mayus/minus) ya existe para ese telefono, actualiza
        el monto y suma un uso (para ordenar 'la mas usada' primero); si no, la agrega."""
        tel = self.norm_telefono(telefono)
        direccion = (direccion or "").strip()
        if not tel or not direccion:
            return
        ahora = _ahora()
        with self._lock:
            existente = self._con.execute(
                "SELECT id, usos FROM direcciones_cliente WHERE telefono=? AND LOWER(direccion)=LOWER(?)",
                (tel, direccion)).fetchone()
            if existente:
                self._con.execute(
                    "UPDATE direcciones_cliente SET delivery_cobro=?, usos=?, actualizado_en=? WHERE id=?",
                    (delivery_cobro, existente["usos"] + 1, ahora, existente["id"]))
            else:
                self._con.execute(
                    "INSERT INTO direcciones_cliente (telefono, direccion, delivery_cobro, usos, "
                    "creado_en, actualizado_en) VALUES (?,?,?,1,?,?)",
                    (tel, direccion, delivery_cobro, ahora, ahora))
            self._con.commit()

    def listar_direcciones_cliente(self, telefono, limite=8):
        """Direcciones guardadas de un telefono, mas usada/reciente primero. 0 o 1
        resultado = no hace falta selector; 2+ = la UI ofrece elegir."""
        tel = self.norm_telefono(telefono)
        if not tel:
            return []
        with self._lock:
            filas = self._con.execute(
                "SELECT direccion, delivery_cobro, usos FROM direcciones_cliente "
                "WHERE telefono=? ORDER BY usos DESC, actualizado_en DESC LIMIT ?",
                (tel, limite)).fetchall()
        return [dict(r) for r in filas]

    def listar_clientes_export(self):
        """Todos los clientes CON telefono y nombre (04-ago-2026, 'nombre registrado' --
        ver bot/webhook.py:_refrescar_clientes_pos). Sin telefono el bot no tiene con que
        calzarlos contra el numero que escribe por WhatsApp, asi que no sirven de nada
        ahi -- se excluyen. Es el volcado que /api/clientes-export expone para que el bot
        lo cachee localmente en un hilo de fondo, igual que hace con el menu (S4)."""
        with self._lock:
            filas = self._con.execute(
                "SELECT telefono, nombre, direccion FROM clientes "
                "WHERE telefono IS NOT NULL AND telefono!='' AND nombre!=''").fetchall()
        return [dict(r) for r in filas]

    # ---------------------------------------------------------------
    # Usuarios, perfiles y permisos (login del POS)
    # ---------------------------------------------------------------
    PIN_ITERACIONES = 200_000

    @staticmethod
    def _hash_pin(pin):
        """PIN -> 'pbkdf2$iteraciones$sal$hash'. Sal aleatoria por usuario: dos usuarios con el
        mismo PIN no comparten hash, y no hay tabla precalculada que sirva."""
        sal = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", str(pin or "").encode("utf-8"),
                                bytes.fromhex(sal), NucleoPedidos.PIN_ITERACIONES).hex()
        return f"pbkdf2${NucleoPedidos.PIN_ITERACIONES}${sal}${h}"

    @staticmethod
    def _pin_coincide(pin, guardado):
        """True si el PIN corresponde al hash guardado. Acepta el formato viejo (SHA-256 con
        sal fija) para no dejar sin entrar a quien ya tenia usuario; autenticar() lo migra."""
        guardado = guardado or ""
        if guardado.startswith("pbkdf2$"):
            try:
                _, iters, sal, esperado = guardado.split("$")
                h = hashlib.pbkdf2_hmac("sha256", str(pin or "").encode("utf-8"),
                                        bytes.fromhex(sal), int(iters)).hex()
            except ValueError:
                return False
            return hmac.compare_digest(h, esperado)
        viejo = hashlib.sha256(("patata::" + str(pin or "")).encode("utf-8")).hexdigest()
        return hmac.compare_digest(viejo, guardado)

    def _seed_usuarios(self):
        """Si aun no hay perfiles: crea 'Administrador' (todos los permisos) + 'Cajero', y
        los dos admins fijos (Christian y Ulloa y Silva). PIN inicial de ejemplo 0000 (cada uno lo
        puede cambiar despues desde Admin > Usuarios > PIN)."""
        with self._lock:
            if self._con.execute("SELECT COUNT(*) FROM perfiles").fetchone()[0]:
                return
            self._con.execute("INSERT INTO perfiles (nombre, permisos, es_admin) VALUES (?,?,1)",
                              ("Administrador", json.dumps(PERMISOS)))
            self._con.execute("INSERT INTO perfiles (nombre, permisos, es_admin) VALUES (?,?,0)",
                              ("Cajero", json.dumps(["vender", "abrir_caja", "cerrar_caja",
                                                     "editar_pedido", "redistribuir_pago"])))
            admin_id = self._con.execute("SELECT id FROM perfiles WHERE es_admin=1").fetchone()[0]
            ahora = _ahora()
            for nom, mail in (("Christian", "dueno@ejemplo.cl"),
                              ("Ulloa y Silva", "admin@ejemplo.cl")):
                self._con.execute(
                    "INSERT INTO usuarios (nombre, email, pin_hash, perfil_id, activo, creado_en) "
                    "VALUES (?,?,?,?,1,?)", (nom, mail, self._hash_pin("0000"), admin_id, ahora))
            self._con.commit()

    def listar_perfiles(self):
        with self._lock:
            filas = self._con.execute("SELECT * FROM perfiles ORDER BY id").fetchall()
        out = []
        for r in filas:
            d = dict(r)
            try:
                d["permisos"] = json.loads(d.get("permisos") or "[]")
            except (TypeError, ValueError):
                d["permisos"] = []
            out.append(d)
        return out

    def crear_perfil(self, nombre, permisos, es_admin=0, usuario=None):
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("el perfil necesita un nombre")
        permisos = [p for p in (permisos or []) if p in PERMISOS]
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO perfiles (nombre, permisos, es_admin) VALUES (?,?,?)",
                (nombre, json.dumps(permisos), 1 if es_admin else 0))
            self._con.commit()
            pid = cur.lastrowid
        self.registrar_auditoria(usuario, "crear_perfil", None, f"perfil '{nombre}'")
        return pid

    def _admins_activos_excluyendo(self, usuario_id):
        """Cuenta usuarios ACTIVOS con perfil admin, sin contar a 'usuario_id' (para
        simular si tras desactivarlo/reasignarlo aun queda al menos un admin)."""
        with self._lock:
            filas = self._con.execute(
                "SELECT u.id FROM usuarios u JOIN perfiles p ON p.id=u.perfil_id "
                "WHERE u.activo=1 AND p.es_admin=1 AND u.id!=?", (usuario_id,)).fetchall()
        return len(filas)

    def _admins_activos_fuera_de_perfil(self, perfil_id):
        """Cuenta usuarios ACTIVOS admin cuyo perfil NO es 'perfil_id' (para simular si,
        al quitarle es_admin a este perfil, el sistema se queda sin ningun admin)."""
        with self._lock:
            filas = self._con.execute(
                "SELECT u.id FROM usuarios u JOIN perfiles p ON p.id=u.perfil_id "
                "WHERE u.activo=1 AND p.es_admin=1 AND p.id!=?", (perfil_id,)).fetchall()
        return len(filas)

    def editar_perfil(self, perfil_id, nombre=None, permisos=None, es_admin=None, usuario=None):
        """Edicion parcial: solo cambia los campos que vienen. Si se le quita es_admin a
        un perfil, exige que quede al menos otro admin activo fuera de este perfil."""
        with self._lock:
            row = self._con.execute("SELECT * FROM perfiles WHERE id=?", (perfil_id,)).fetchone()
        if not row:
            raise ValueError(f"perfil {perfil_id} no existe")
        p = dict(row)
        if nombre is not None:
            nombre = nombre.strip()
            if not nombre:
                raise ValueError("el perfil necesita un nombre")
            p["nombre"] = nombre
        if permisos is not None:
            p["permisos"] = json.dumps([x for x in permisos if x in PERMISOS])
        if es_admin is not None:
            nuevo_es_admin = 1 if es_admin else 0
            if p["es_admin"] and not nuevo_es_admin and self._admins_activos_fuera_de_perfil(perfil_id) == 0:
                raise ValueError(
                    "no se puede quitar el rol admin a este perfil: quedaria el sistema "
                    "sin ningun administrador activo")
            p["es_admin"] = nuevo_es_admin
        with self._lock:
            self._con.execute("UPDATE perfiles SET nombre=?, permisos=?, es_admin=? WHERE id=?",
                              (p["nombre"], p["permisos"], p["es_admin"], perfil_id))
            self._con.commit()
        self.registrar_auditoria(usuario, "editar_perfil", None, f"perfil #{perfil_id} '{p['nombre']}'")
        return next(x for x in self.listar_perfiles() if x["id"] == perfil_id)

    def eliminar_perfil(self, perfil_id, usuario=None):
        """Rechaza si hay algun usuario (activo o no) que use este perfil: primero hay
        que reasignarlos a otro perfil."""
        with self._lock:
            n = self._con.execute(
                "SELECT COUNT(*) FROM usuarios WHERE perfil_id=?", (perfil_id,)).fetchone()[0]
            if n:
                raise ValueError(
                    f"no se puede borrar: {n} usuario(s) tienen este perfil; reasignalos primero")
            fila = self._con.execute("SELECT nombre FROM perfiles WHERE id=?", (perfil_id,)).fetchone()
            if not fila:
                raise ValueError(f"perfil {perfil_id} no existe")
            self._con.execute("DELETE FROM perfiles WHERE id=?", (perfil_id,))
            self._con.commit()
        self.registrar_auditoria(usuario, "eliminar_perfil", None, f"perfil #{perfil_id} '{fila['nombre']}'")

    def listar_usuarios(self):
        with self._lock:
            filas = self._con.execute(
                "SELECT u.id, u.nombre, u.email, u.perfil_id, u.activo, p.nombre AS perfil "
                "FROM usuarios u LEFT JOIN perfiles p ON p.id=u.perfil_id ORDER BY u.id").fetchall()
        return [dict(r) for r in filas]

    def crear_usuario(self, nombre, pin, perfil_id, email="", usuario=None):
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("el usuario necesita un nombre")
        if not str(pin or "").strip():
            raise ValueError("el usuario necesita un PIN")
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO usuarios (nombre, email, pin_hash, perfil_id, activo, creado_en) "
                "VALUES (?,?,?,?,1,?)",
                (nombre, (email or "").strip(), self._hash_pin(pin), perfil_id, _ahora()))
            self._con.commit()
            uid = cur.lastrowid
        self.registrar_auditoria(usuario, "crear_usuario", None, f"usuario '{nombre}' (perfil #{perfil_id})")
        return uid

    def editar_usuario(self, usuario_id, nombre=None, email=None, perfil_id=None, usuario=None):
        """Edicion parcial (nombre/email/perfil). Si el cambio de perfil deja a este
        usuario sin ser admin y era el ultimo admin activo, lo rechaza."""
        with self._lock:
            row = self._con.execute("SELECT * FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
        if not row:
            raise ValueError(f"usuario {usuario_id} no existe")
        u = dict(row)
        if perfil_id is not None and perfil_id != u["perfil_id"]:
            with self._lock:
                existe = self._con.execute("SELECT es_admin FROM perfiles WHERE id=?", (perfil_id,)).fetchone()
            if not existe:
                raise ValueError(f"perfil {perfil_id} no existe")
            era_admin = self._con.execute(
                "SELECT es_admin FROM perfiles WHERE id=?", (u["perfil_id"],)).fetchone()
            if (u["activo"] and era_admin and era_admin["es_admin"] and not existe["es_admin"]
                    and self._admins_activos_excluyendo(usuario_id) == 0):
                raise ValueError(
                    "no se puede cambiar el perfil: es el unico administrador activo")
            u["perfil_id"] = perfil_id
        if nombre is not None:
            nombre = nombre.strip()
            if not nombre:
                raise ValueError("el usuario necesita un nombre")
            u["nombre"] = nombre
        if email is not None:
            u["email"] = email.strip()
        with self._lock:
            self._con.execute("UPDATE usuarios SET nombre=?, email=?, perfil_id=? WHERE id=?",
                              (u["nombre"], u["email"], u["perfil_id"], usuario_id))
            self._con.commit()
        self.registrar_auditoria(usuario, "editar_usuario", None,
                                 f"usuario #{usuario_id} '{u['nombre']}'")
        return next(x for x in self.listar_usuarios() if x["id"] == usuario_id)

    def desactivar_usuario(self, usuario_id, usuario=None):
        """No permite desactivar al ULTIMO administrador activo (blindaje de dia 0)."""
        with self._lock:
            row = self._con.execute("SELECT * FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
        if not row:
            raise ValueError(f"usuario {usuario_id} no existe")
        u = dict(row)
        if u["activo"]:
            with self._lock:
                perfil = self._con.execute(
                    "SELECT es_admin FROM perfiles WHERE id=?", (u["perfil_id"],)).fetchone()
            if perfil and perfil["es_admin"] and self._admins_activos_excluyendo(usuario_id) == 0:
                raise ValueError(
                    "no se puede desactivar: es el unico administrador activo")
        with self._lock:
            self._con.execute("UPDATE usuarios SET activo=0 WHERE id=?", (usuario_id,))
            self._con.commit()
        self.registrar_auditoria(usuario, "desactivar_usuario", None,
                                 f"usuario #{usuario_id} '{u['nombre']}'")

    def reactivar_usuario(self, usuario_id, usuario=None):
        with self._lock:
            fila = self._con.execute("SELECT nombre FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
            if not fila:
                raise ValueError(f"usuario {usuario_id} no existe")
            self._con.execute("UPDATE usuarios SET activo=1 WHERE id=?", (usuario_id,))
            self._con.commit()
        self.registrar_auditoria(usuario, "reactivar_usuario", None,
                                 f"usuario #{usuario_id} '{fila['nombre']}'")

    def cambiar_pin(self, usuario_id, nuevo_pin, usuario=None):
        if not str(nuevo_pin or "").strip():
            raise ValueError("el PIN no puede estar vacio")
        with self._lock:
            existe = self._con.execute("SELECT 1 FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
            if not existe:
                raise ValueError(f"usuario {usuario_id} no existe")
            self._con.execute("UPDATE usuarios SET pin_hash=? WHERE id=?",
                              (self._hash_pin(nuevo_pin), usuario_id))
            self._con.commit()
        # el detalle NUNCA incluye el PIN nuevo (solo se guarda hasheado en la BD)
        self.registrar_auditoria(usuario, "cambiar_pin", None, f"usuario #{usuario_id}")

    def _usuario_con_permisos(self, row):
        """Arma el dict de usuario (sin PIN) + su perfil resuelto (nombre, es_admin,
        permisos). Compartido por autenticar() y usuario_por_id()."""
        u = dict(row)
        u.pop("pin_hash", None)
        perfil = next((p for p in self.listar_perfiles() if p["id"] == u["perfil_id"]), None)
        u["perfil"] = perfil["nombre"] if perfil else None
        u["es_admin"] = bool(perfil and perfil["es_admin"])
        u["permisos"] = perfil["permisos"] if perfil else []
        return u

    LOGIN_FALLOS_MAX = 5          # fallos seguidos antes de bloquear
    LOGIN_BLOQUEO_BASE_SEG = 60   # primer bloqueo; se duplica en cada racha, tope 15 min

    def _segundos_bloqueado(self, clave):
        fila = self._con.execute(
            "SELECT bloqueado_hasta FROM intentos_login WHERE clave=?", (clave,)).fetchone()
        if not fila or not fila[0]:
            return 0
        return max(0, int((datetime.fromisoformat(fila[0]) - datetime.now()).total_seconds()) + 1)

    def autenticar(self, identificador, pin):
        """Devuelve el usuario + sus permisos si el PIN coincide (busca por nombre o email);
        None si no. Tras LOGIN_FALLOS_MAX fallos seguidos sobre el mismo identificador bloquea
        (LoginBloqueado) con espera creciente: adivinar un PIN de 4 digitos deja de ser cosa de
        segundos. Cuenta tambien identificadores que no existen, para no revelar quien existe."""
        ident = (identificador or "").strip().lower()
        with self._lock:
            espera = self._segundos_bloqueado(ident)
            if espera:
                raise LoginBloqueado(espera)
            filas = self._con.execute(
                "SELECT * FROM usuarios WHERE activo=1 AND (LOWER(nombre)=? OR LOWER(email)=?)",
                (ident, ident)).fetchall()
            ok = next((r for r in filas if self._pin_coincide(pin, r["pin_hash"])), None)
            if ok:
                self._con.execute("DELETE FROM intentos_login WHERE clave=?", (ident,))
                if not (ok["pin_hash"] or "").startswith("pbkdf2$"):     # migra el formato viejo
                    self._con.execute("UPDATE usuarios SET pin_hash=? WHERE id=?",
                                      (self._hash_pin(pin), ok["id"]))
                self._con.commit()
                return self._usuario_con_permisos(ok)
            fila = self._con.execute(
                "SELECT fallos, rachas FROM intentos_login WHERE clave=?", (ident,)).fetchone()
            fallos, rachas = (fila[0] + 1, fila[1]) if fila else (1, 0)
            hasta = None
            if fallos >= self.LOGIN_FALLOS_MAX:
                seg = min(900, self.LOGIN_BLOQUEO_BASE_SEG * (2 ** rachas))
                hasta = (datetime.now() + timedelta(seconds=seg)).isoformat(timespec="seconds")
                fallos, rachas = 0, rachas + 1
            self._con.execute(
                "INSERT OR REPLACE INTO intentos_login (clave, fallos, rachas, bloqueado_hasta) "
                "VALUES (?,?,?,?)", (ident, fallos, rachas, hasta))
            self._con.commit()
        return None

    # ---------------------------------------------------------------
    # Sesiones: el login entrega un token aleatorio; el backend guarda solo su hash.
    # Reemplaza al header X-Usuario-Id (un numero adivinable que cualquiera podia falsificar).
    # ---------------------------------------------------------------
    SESION_HORAS = 12

    @staticmethod
    def _hash_token(token):
        return hashlib.sha256(("sesion::" + str(token or "")).encode("utf-8")).hexdigest()

    def crear_sesion(self, usuario_id):
        """Devuelve un token nuevo (se muestra una sola vez) valido SESION_HORAS horas."""
        token = secrets.token_urlsafe(32)
        ahora = datetime.now()
        with self._lock:
            self._con.execute("DELETE FROM sesiones WHERE expira_en < ?",
                              (ahora.strftime("%Y-%m-%d %H:%M:%S"),))
            self._con.execute(
                "INSERT INTO sesiones (token_hash, usuario_id, creada_en, expira_en) VALUES (?,?,?,?)",
                (self._hash_token(token), usuario_id, ahora.strftime("%Y-%m-%d %H:%M:%S"),
                 (ahora + timedelta(hours=self.SESION_HORAS)).strftime("%Y-%m-%d %H:%M:%S")))
            self._con.commit()
        return token

    def usuario_por_sesion(self, token):
        """El usuario (sin PIN) + permisos si el token existe, no vencio y el usuario sigue activo."""
        if not token:
            return None
        with self._lock:
            fila = self._con.execute(
                "SELECT usuario_id FROM sesiones WHERE token_hash=? AND expira_en >= ?",
                (self._hash_token(token), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))).fetchone()
        return self.usuario_por_id(fila[0]) if fila else None

    def cerrar_sesion(self, token):
        with self._lock:
            self._con.execute("DELETE FROM sesiones WHERE token_hash=?", (self._hash_token(token),))
            self._con.commit()

    def usuario_por_id(self, usuario_id):
        """El usuario (sin PIN) + sus permisos, por id y ACTIVO. Lo usa el backend
        (punto 21c) para validar permisos en cada request sensible y para loguear
        auditoria con el nombre real de quien actuo, a partir del header X-Usuario-Id."""
        if usuario_id is None:
            return None
        with self._lock:
            row = self._con.execute(
                "SELECT * FROM usuarios WHERE id=? AND activo=1", (usuario_id,)).fetchone()
        return self._usuario_con_permisos(row) if row else None

    # ---------------------------------------------------------------
    # Auditoria: quien hizo que (punto 21b)
    # ---------------------------------------------------------------
    def registrar_auditoria(self, usuario, accion, pedido_id=None, detalle=""):
        with self._lock:
            self._con.execute(
                "INSERT INTO auditoria (en, usuario, accion, pedido_id, detalle) VALUES (?,?,?,?,?)",
                (_ahora(), usuario or "(sin identificar)", accion, pedido_id, detalle))
            self._con.commit()

    def listar_auditoria(self, limite=200):
        with self._lock:
            filas = self._con.execute(
                "SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [dict(r) for r in filas]

    # ---------------------------------------------------------------
    # Comanda en texto (para cocina / impresion / pantalla)
    # ---------------------------------------------------------------
    def _nombre_comanda(self, it):
        """Nombre del item para la comanda = la descripcion del SKU TAL CUAL vive en el
        menu (BBDD), sin anteponer nada.

        07-jul-2026 (pedido de Gonza): se ANULA la 'primera reparacion' que ponia la
        etiqueta de la categoria delante del producto ('Churrasco' + 'Italiano'). Esa
        reparacion nacio cuando el SKU se llamaba solo 'Italiano' y la cocina no sabia de
        que categoria era; pero desde que Christian puede editar el nombre del SKU a mano,
        las dos soluciones chocaron: al renombrar el producto a 'Churrasco Italiano'
        completo, la comanda imprimia 'Churrasco Churrasco Italiano' (doble). Ahora manda
        SOLO lo que Christian escribe en el nombre del producto; desambiguar entre
        categorias es responsabilidad del nombre del SKU (el dueno decide que se lee).
        (self._etiqueta_cat se mantiene por compatibilidad; ya no se antepone aca.)"""
        return it.get("producto", "?")

    def pedido_comanda(self, pedido_id):
        """El pedido con cada item enriquecido con 'nombre_comanda' (etiqueta+producto),
        para que la impresora (que no conoce el menu) formatee sin re-resolver nada."""
        p = self.obtener(pedido_id)
        if not p:
            return None
        for it in p.get("items", []):
            it["nombre_comanda"] = self._nombre_comanda(it)
        return p

    def comanda_texto(self, pedido_id):
        p = self.obtener(pedido_id)
        if not p:
            return f"(pedido {pedido_id} no existe)"
        L = [
            "=" * 44,
            f"  COMANDA #{p['id']}  [{p['canal']}]  -  {p['estado'].upper()}",
            f"  {_fecha_hora_legible(p.get('creado_en'))}",
            "=" * 44,
        ]
        # Telefono primero (lo usa el repartidor), despues el nombre
        if p.get("telefono"):
            L.append(f"  Tel     : {p['telefono']}")
        L.append(f"  Cliente : {p.get('cliente') or '-'}")
        # Modo limpio: 'Local' / 'Delivery: direccion' / 'Retiro'
        modo = p.get("modalidad", "")
        if modo == "delivery":
            L.append(f"  Delivery: {p.get('direccion') or '(sin direccion)'}")
        elif modo == "local":
            L.append("  Local" + (f"  | Mesa: {p['mesa']}" if p.get('mesa') else ""))
        else:
            L.append("  Retiro")
        L.append("  Items:")
        for it in p["items"]:
            nombre = self._nombre_comanda(it)
            var = f" [{label_variante(it['variante'])}]" if it.get("variante") else ""
            notas = f"  ({it['notas']})" if it.get("notas") else ""
            if it.get("ok"):
                promo = "  (promo 2x)" if it.get("pares_promo") else ""
                L.append(f"    {it['cantidad']}x {nombre}{var} = {fmt_pesos(it['subtotal'])}{promo}{notas}")
                if it.get("componentes"):
                    L.append(f"      incluye: {', '.join(it['componentes'])}")
            else:
                L.append(f"    {it['cantidad']}x {nombre}{var}  /!\\ {it['problema']}{notas}")
        if p.get("delivery_cobro"):
            L.append(f"    + delivery = {fmt_pesos(p['delivery_cobro'])}")
        if p.get("notas"):
            L.append(f"  NOTA: {p['notas']}")
        L.append("  " + "-" * 40)
        L.append(f"  TOTAL: {fmt_pesos(p['total'])}")
        # Metodo(s) de pago BAJO el total, ordenados si son varios
        for linea in _lineas_pago(p):
            L.append("  " + linea)
        if p.get("pago_con") is not None:
            L.append(f"  Paga con {fmt_pesos(p['pago_con'])}  ->  Vuelto {fmt_pesos(p.get('vuelto', 0))}")
        if p.get("problemas"):
            L.append("  /!\\ REVISAR: " + "; ".join(p["problemas"]))
        L.append("=" * 44)
        return "\n".join(L)


# ===========================================================================
# Auto-test: corre `python nucleo.py` para validar el nucleo sin canales.
# ===========================================================================
if __name__ == "__main__":
    DB_TEST = AQUI / "_test_nucleo.db"
    DB_TEST.unlink(missing_ok=True)
    n = NucleoPedidos(db_path=DB_TEST)
    ok = []

    def chk(desc, cond):
        print(f"  [{'OK' if cond else 'FALLA'}] {desc}")
        ok.append(cond)

    # Crear un pedido como lo mandaria el bot (items crudos -> el nucleo los tarifica)
    p = n.crear(
        canal="whatsapp", cliente="Luis", modalidad="retiro", forma_pago="tarjeta",
        items=[
            {"categoria": "Churrascos", "producto": "Churrasco Italiano", "variante": None, "cantidad": 2},
            {"categoria": "Papas Fritas", "producto": "Papas Fritas", "variante": "grande", "cantidad": 1},
            {"categoria": "Bebestibles", "producto": "Lata CCU", "variante": None, "cantidad": 1, "notas": "Bilz"},
        ])
    chk(f"pedido creado con id={p['id']}", p["id"] == 1)
    chk("estado inicial 'recibido'", p["estado"] == "recibido")
    # Lata CCU: subio de 1500 a 2000 el 08-jul-2026 y volvio a bajar a 1500 -- precio
    # real confirmado contra el POS de Christian el 03-ago-2026.
    chk(f"total tarificado = 14400 (8900 promo + 4000 + 1500)", p["total"] == 14400)

    # Transiciones validas hacia cocina
    for est in ["confirmado", "en_preparacion", "listo", "entregado"]:
        r = n.transicionar(p["id"], est)
        chk(f"transicion -> {est}", r["estado"] == est)

    # Transicion invalida
    try:
        n.transicionar(p["id"], "en_preparacion")  # entregado es final
        chk("rechaza transicion invalida", False)
    except ValueError:
        chk("rechaza transicion invalida (entregado es final)", True)

    # Cola: el entregado ya no aparece; creamos otro y debe figurar
    p2 = n.crear(canal="cajero", cliente="Mesa 3", modalidad="retiro",
                 items=[{"categoria": "Boxes Pollo Crispy", "producto": "Box 1", "cantidad": 1}])
    cola = n.cola()
    chk("cola muestra solo activos (1)", len(cola) == 1 and cola[0]["id"] == p2["id"])

    # --- Candado del ciclo de DELIVERY con paso intermedio (29-jul-2026) ---
    # La pantalla del cajero (cajero.html) usa `listo` como "EN REPARTO": el pedido salio
    # con el repartidor pero TODAVIA NO llego, asi que tiene que seguir a la vista. Si
    # alguien saca `listo` de los estados de la cola, o corta la transicion
    # en_preparacion -> listo, el delivery desaparece de la pantalla en silencio y nadie
    # se entera hasta que un cliente reclama. Este bloque existe para que eso falle aca.
    chk("en_preparacion permite ir a 'listo' (paso EN REPARTO)",
        "listo" in TRANSICIONES["en_preparacion"])
    chk("desde 'listo' se puede cerrar el pedido", "entregado" in TRANSICIONES["listo"])
    pd = n.crear(canal="cajero", cliente="Ana", modalidad="delivery", direccion="Los Robles 123",
                 items=[{"categoria": "Boxes Pollo Crispy", "producto": "Box 1", "cantidad": 1}])
    n.transicionar(pd["id"], "en_preparacion")
    n.transicionar(pd["id"], "listo")
    ids_cola = [x["id"] for x in n.cola()]
    chk("un delivery EN REPARTO ('listo') SIGUE en la cola", pd["id"] in ids_cola)
    chk("y la cola le devuelve la modalidad, que es como la pantalla decide el boton",
        n.obtener(pd["id"])["modalidad"] == "delivery")
    n.transicionar(pd["id"], "entregado")
    chk("recien 'entregado' lo saca de la cola", pd["id"] not in [x["id"] for x in n.cola()])

    # --- Caja: abrir, vender, cerrar (los pedidos de arriba no tienen caja) ---
    cab = n.abrir_caja(fondo_inicial=20000, cajero="Test")
    chk("caja abierta con fondo 20000", n.caja_abierta()["fondo_inicial"] == 20000)
    pe = n.crear(canal="cajero", cliente="X", modalidad="retiro", forma_pago="efectivo",
                 items=[{"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1}],
                 pago_con=10000)
    chk("pedido nuevo asociado a la caja", pe.get("caja_id") == cab["id"])
    chk("vuelto = 10000 - 4900 = 5100", pe.get("vuelto") == 5100)
    res = n.resumen_caja(cab["id"])
    chk("resumen: esperado efectivo = 20000 + 4900 = 24900", res["esperado_efectivo"] == 24900)
    chk("resumen ignora pedidos sin caja", res["n_pedidos"] == 1)
    # editar el pedido: pasar a débito y agregar una bebida (caso real de Christian)
    ed = n.editar(pe["id"], cliente="X", modalidad="retiro", forma_pago="tarjeta debito",
                  items=[{"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1},
                         {"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}])
    chk("editar re-tarifica (4900 + 1500 = 6400)", ed["total"] == 6400)
    chk("editar cambia la forma de pago a débito", ed["forma_pago"] == "tarjeta debito")
    res2 = n.resumen_caja(cab["id"])
    chk("tras editar a débito, el efectivo esperado vuelve al fondo (20000)",
        res2["esperado_efectivo"] == 20000)
    cierre = n.cerrar_caja(contado_cierre=20000)
    chk("cierre cuadrado (descuadre 0)", cierre["descuadre"] == 0)
    chk("no queda caja abierta tras cerrar", n.caja_abierta() is None)
    chk("cerrar_caja dispara un backup automatico (punto 21a)", cierre.get("backup", {}).get("ok") is True)
    _archivo_backup = cierre.get("backup", {}).get("archivo")
    chk("el archivo de backup existe de verdad", bool(_archivo_backup) and Path(_archivo_backup).exists())
    if _archivo_backup:
        Path(_archivo_backup).unlink(missing_ok=True)   # el auto-test no deja rastro en backups/
    hist = n.listar_cajas()
    chk("listar_cajas trae la caja recien cerrada", any(c["caja_id"] == cab["id"] for c in hist))
    chk("listar_cajas trae el mismo descuadre que devolvio cerrar_caja",
        next(c for c in hist if c["caja_id"] == cab["id"])["descuadre"] == cierre["descuadre"])
    try:
        n.editar(pe["id"], cliente="X", modalidad="retiro",
                 items=[{"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1}])
        chk("rechaza editar con la caja ya cerrada", False)
    except ValueError:
        chk("rechaza editar con la caja ya cerrada", True)
    # registrar_pagos tambien respeta la caja cerrada
    try:
        n.registrar_pagos(pe["id"], [{"metodo": "efectivo", "monto": 6400}])
        chk("registrar_pagos rechazado con caja cerrada", False)
    except ValueError:
        chk("registrar_pagos rechazado con caja cerrada", True)

    print("== pago combinado (varios metodos) ==")
    cab2 = n.abrir_caja(fondo_inicial=0, cajero="Test2")
    # Lata CCU = 1500 (precio real confirmado contra el POS de Christian, 03-ago-2026):
    # 4900 (churrasco) + 1500 = 6400.
    pm = n.crear(canal="cajero", cliente="Mixto", modalidad="retiro",
                 items=[{"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1},
                        {"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}],
                 pagos=[{"metodo": "efectivo", "monto": 4500},
                        {"metodo": "tarjeta debito", "monto": 1900}])
    chk("pago combinado total 6400", pm["total"] == 6400)
    chk("forma_pago resumen = 'mixto'", pm["forma_pago"] == "mixto")
    chk("se guardan los 2 metodos", len(pm["pagos"]) == 2)
    res_pm = n.resumen_caja(cab2["id"])
    chk("resumen reparte efectivo 4500", res_pm["por_metodo"].get("efectivo") == 4500)
    chk("resumen reparte debito 1900", res_pm["por_metodo"].get("tarjeta debito") == 1900)
    chk("esperado efectivo = 0 + 4500", res_pm["esperado_efectivo"] == 4500)
    try:
        n.crear(canal="cajero", cliente="Z", modalidad="retiro",
                items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}],
                pagos=[{"metodo": "efectivo", "monto": 1000}])   # total 1500, paga 1000
        chk("rechaza pagos que no suman el total", False)
    except ValueError:
        chk("rechaza pagos que no suman el total", True)
    rp = n.registrar_pagos(pm["id"], [{"metodo": "transferencia", "monto": 6400}])
    chk("registrar_pagos redistribuye sin tocar el total",
        rp["total"] == 6400 and rp["forma_pago"] == "transferencia")
    chk("tras redistribuir, efectivo esperado vuelve a 0",
        n.resumen_caja(cab2["id"])["esperado_efectivo"] == 0)
    # vuelto sobre la PARTE efectivo en pago mixto (no sobre el total)
    pmv = n.crear(canal="cajero", cliente="V", modalidad="retiro",
                  items=[{"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1}],
                  pagos=[{"metodo": "efectivo", "monto": 2000}, {"metodo": "tarjeta debito", "monto": 2900}],
                  pago_con=5000)
    chk("vuelto = paga_con 5000 - efectivo 2000 = 3000", pmv.get("vuelto") == 3000)
    rpv = n.registrar_pagos(pmv["id"], [{"metodo": "efectivo", "monto": 4900}], pago_con=5000)
    chk("registrar_pagos recalcula vuelto (efectivo 4900, paga 5000 -> 100)", rpv.get("vuelto") == 100)

    print("== ajuste de tarjeta/transferencia al cerrar (punto E2, 03-jul) ==")
    # cab2 en este punto: efectivo=4900 (pmv), transferencia=6400 (pm) -> esperado_efectivo=4900
    cierre2 = n.cerrar_caja(contado_cierre=4900, contado_otros={"transferencia": 6400})
    chk("cuadre de efectivo sigue funcionando junto al ajuste de otros metodos",
        cierre2["descuadre"] == 0)
    chk("contado_otros se guarda tal cual se mando", cierre2["contado_otros"].get("transferencia") == 6400)
    chk("transferencia CUADRADA (6400 confirmado vs 6400 vendido)",
        cierre2["descuadre_otros"].get("transferencia") == 0)
    hist2 = n.listar_cajas()
    chk("listar_cajas tambien trae el descuadre_otros de una caja ya cerrada",
        next(c for c in hist2 if c["caja_id"] == cab2["id"])["descuadre_otros"].get("transferencia") == 0)
    cab3 = n.abrir_caja(fondo_inicial=0, cajero="Test3")
    n.crear(canal="cajero", cliente="Tarjeta", modalidad="retiro",
            items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}],
            pagos=[{"metodo": "tarjeta debito", "monto": 1500}])
    cierre3 = n.cerrar_caja(contado_cierre=0, contado_otros={"tarjeta debito": 1300})
    chk("tarjeta con descuadre real (1300 confirmado vs 1500 vendido = -200)",
        cierre3["descuadre_otros"].get("tarjeta debito") == -200)
    n.abrir_caja(fondo_inicial=0, cajero="Test4")
    chk("cerrar_caja sin contado_otros sigue funcionando (parametro opcional)",
        n.cerrar_caja(0).get("contado_otros") == {})

    print("== informe de ventas por fecha ==")
    hoy = _ahora()[:10]
    inf = n.ventas_periodo(desde=hoy, hasta=hoy)
    chk("informe de hoy trae pedidos", inf["n_pedidos"] >= 1)
    chk("informe incluye el mixto en transferencia",
        inf["por_metodo"].get("transferencia", 0) >= 6400)
    chk("rango sin ventas viene vacio",
        n.ventas_periodo(desde="2020-01-01", hasta="2020-12-31")["n_pedidos"] == 0)

    print("== flujo A cocina -> Entregado (punto 4) ==")
    pe4 = n.crear(canal="cajero", cliente="Deli", modalidad="delivery", forma_pago="efectivo",
                  direccion="Calle 1",
                  items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}])
    n.transicionar(pe4["id"], "en_preparacion")   # A cocina (salta 'confirmado')
    chk("recibido -> en_preparacion directo (sin confirmar)",
        n.obtener(pe4["id"])["estado"] == "en_preparacion")
    chk("en_preparacion sigue en la cola", any(x["id"] == pe4["id"] for x in n.cola()))
    n.transicionar(pe4["id"], "entregado")        # Entregado directo (sin pasar por 'listo')
    chk("en_preparacion -> entregado sale de la cola",
        not any(x["id"] == pe4["id"] for x in n.cola()))

    print("== sesiones (reemplazan al header X-Usuario-Id) ==")
    _adm = n.autenticar("Christian", "0000")
    _tok = n.crear_sesion(_adm["id"])
    chk("sesion valida resuelve al usuario", (n.usuario_por_sesion(_tok) or {}).get("id") == _adm["id"])
    chk("token inventado -> None", n.usuario_por_sesion("inventado") is None)
    chk("un id numerico NO sirve como token", n.usuario_por_sesion(str(_adm["id"])) is None)
    chk("token vacio -> None", n.usuario_por_sesion("") is None and n.usuario_por_sesion(None) is None)
    chk("la base guarda solo el hash, nunca el token",
        n._con.execute("SELECT COUNT(*) FROM sesiones WHERE token_hash=?", (_tok,)).fetchone()[0] == 0)
    n._con.execute("UPDATE sesiones SET expira_en='2000-01-01 00:00:00' WHERE token_hash=?",
                   (n._hash_token(_tok),)); n._con.commit()
    chk("sesion vencida -> None", n.usuario_por_sesion(_tok) is None)
    _tok2 = n.crear_sesion(_adm["id"])
    n.cerrar_sesion(_tok2)
    chk("tras cerrar sesion el token ya no sirve", n.usuario_por_sesion(_tok2) is None)
    chk("crear una sesion nueva limpia las vencidas",
        (n.crear_sesion(_adm["id"]), n._con.execute(
            "SELECT COUNT(*) FROM sesiones WHERE expira_en < '2001-01-01'").fetchone()[0])[1] == 0)

    print("== PIN con sal por usuario + limite de intentos ==")
    _h1, _h2 = n._hash_pin("4321"), n._hash_pin("4321")
    chk("el hash es pbkdf2 y no contiene el PIN", _h1.startswith("pbkdf2$") and "4321" not in _h1)
    chk("mismo PIN, hashes distintos (sal por usuario)", _h1 != _h2)
    chk("el PIN correcto coincide y uno malo no", n._pin_coincide("4321", _h1) and not n._pin_coincide("4322", _h1))
    _u = n.crear_usuario("Probando Login", "2468", n.listar_perfiles()[1]["id"])
    n._con.execute("UPDATE usuarios SET pin_hash=? WHERE nombre='Probando Login'",
                   (hashlib.sha256(b"patata::2468").hexdigest(),)); n._con.commit()
    chk("un usuario con el hash viejo todavia entra", n.autenticar("Probando Login", "2468") is not None)
    chk("...y su hash se migra a pbkdf2 en ese login",
        n._con.execute("SELECT pin_hash FROM usuarios WHERE nombre='Probando Login'").fetchone()[0].startswith("pbkdf2$"))
    for _ in range(n.LOGIN_FALLOS_MAX - 1):
        n.autenticar("Probando Login", "0000")
    chk("4 fallos: todavia no bloquea y el PIN correcto entra", n.autenticar("Probando Login", "2468") is not None)
    for _ in range(n.LOGIN_FALLOS_MAX):
        n.autenticar("Probando Login", "0000")
    try:
        n.autenticar("Probando Login", "2468"); _bloq = None
    except LoginBloqueado as e:
        _bloq = e.segundos
    chk("5 fallos seguidos: bloquea aunque ahora venga el PIN correcto", _bloq is not None and 0 < _bloq <= 61)
    n._con.execute("UPDATE intentos_login SET bloqueado_hasta='2000-01-01T00:00:00'"); n._con.commit()
    chk("pasado el bloqueo, el PIN correcto vuelve a entrar", n.autenticar("Probando Login", "2468") is not None)
    for _ in range(n.LOGIN_FALLOS_MAX):
        n.autenticar("no.existe@x.cl", "1")
    try:
        n.autenticar("no.existe@x.cl", "1"); _b2 = False
    except LoginBloqueado:
        _b2 = True
    chk("un usuario inexistente tambien se bloquea (no revela quien existe)", _b2)
    chk("bloquear a uno no bloquea a otro", n.autenticar("Christian", "0000") is not None)

    print("== usuarios / perfiles / login (Ola B) ==")
    perfiles = n.listar_perfiles()
    chk("seed crea perfiles Administrador + Cajero", len(perfiles) == 2)
    admin = n.autenticar("Christian", "0000")
    chk("login de Christian (PIN de ejemplo) OK y es admin", bool(admin) and admin["es_admin"])
    chk("Christian tiene todos los permisos", bool(admin) and len(admin["permisos"]) == len(PERMISOS))
    chk("login con PIN malo falla", n.autenticar("Christian", "0000") is None)
    chk("login por email tambien", n.autenticar("admin@ejemplo.cl", "0000") is not None)
    caj = next(p["id"] for p in perfiles if p["nombre"] == "Cajero")
    n.crear_usuario("Pedro", "5678", caj)
    pedro = n.autenticar("Pedro", "5678")
    chk("cajero nuevo se autentica y NO es admin", bool(pedro) and not pedro["es_admin"])
    chk("cajero no puede gestionar usuarios", "gestionar_usuarios" not in (pedro or {}).get("permisos", []))

    print("== CRUD usuarios/perfiles + guardas de seguridad ==")
    admin_id = next(u["id"] for u in n.listar_usuarios() if u["nombre"] == "Christian")
    ulloa_id = next(u["id"] for u in n.listar_usuarios() if u["nombre"] == "Ulloa y Silva")
    pedro_id = next(u["id"] for u in n.listar_usuarios() if u["nombre"] == "Pedro")
    admin_perfil_id = next(p["id"] for p in perfiles if p["nombre"] == "Administrador")

    # editar_usuario: cambiar nombre/email
    ed = n.editar_usuario(pedro_id, nombre="Pedro Cajero", email="pedro@x.com")
    chk("editar_usuario actualiza nombre", ed["nombre"] == "Pedro Cajero")

    # cambiar_pin: el PIN viejo deja de servir, el nuevo funciona
    n.cambiar_pin(pedro_id, "9999")
    chk("tras cambiar PIN, el viejo ya no autentica", n.autenticar("Pedro Cajero", "5678") is None)
    chk("tras cambiar PIN, el nuevo si autentica", n.autenticar("Pedro Cajero", "9999") is not None)

    # desactivar_usuario: un cajero se puede desactivar sin problema
    n.desactivar_usuario(pedro_id)
    chk("cajero desactivado ya no autentica", n.autenticar("Pedro Cajero", "9999") is None)
    n.reactivar_usuario(pedro_id)
    chk("reactivado vuelve a autenticar", n.autenticar("Pedro Cajero", "9999") is not None)

    # GUARDA: no se puede desactivar al ULTIMO admin activo (hay 2: Christian y Ulloa)
    n.desactivar_usuario(ulloa_id)   # queda 1 admin activo (Christian): permitido
    chk("desactivar al 2do admin (queda 1) esta permitido", n.autenticar("Ulloa y Silva", "0000") is None)
    try:
        n.desactivar_usuario(admin_id)   # dejaria 0 admins activos
        chk("rechaza desactivar al ULTIMO admin activo", False)
    except ValueError:
        chk("rechaza desactivar al ULTIMO admin activo", True)
    n.reactivar_usuario(ulloa_id)   # deshacer para no afectar el resto del test

    # con 2 admins activos (Christian + Ulloa), reasignar a UNO de ellos SI se permite
    ed2 = n.editar_usuario(ulloa_id, perfil_id=caj)
    chk("con otro admin activo, SI se puede reasignar", ed2["perfil_id"] == caj)
    n.editar_usuario(ulloa_id, perfil_id=admin_perfil_id)   # deshacer

    # GUARDA: no se puede reasignar al ULTIMO admin activo a un perfil no-admin
    n.desactivar_usuario(ulloa_id)   # vuelve a quedar Christian como unico admin activo
    try:
        n.editar_usuario(admin_id, perfil_id=caj)
        chk("rechaza sacarle el perfil admin al ULTIMO admin", False)
    except ValueError:
        chk("rechaza sacarle el perfil admin al ULTIMO admin", True)
    n.reactivar_usuario(ulloa_id)   # deshacer para el resto del test

    # crear_perfil / editar_perfil
    mesero_id = n.crear_perfil("Mesero 1", ["vender"])
    ed3 = n.editar_perfil(mesero_id, permisos=["vender", "editar_pedido"])
    chk("editar_perfil actualiza permisos", set(ed3["permisos"]) == {"vender", "editar_pedido"})

    # GUARDA: no se le puede quitar es_admin al perfil Administrador si nadie mas es admin
    try:
        n.editar_perfil(admin_perfil_id, es_admin=0)
        chk("rechaza quitar es_admin si no queda otro admin", False)
    except ValueError:
        chk("rechaza quitar es_admin si no queda otro admin", True)

    # GUARDA: no se puede borrar un perfil en uso (Cajero tiene usuarios)
    try:
        n.eliminar_perfil(caj)
        chk("rechaza borrar perfil EN USO", False)
    except ValueError:
        chk("rechaza borrar perfil EN USO", True)
    # perfil sin usuarios SI se puede borrar
    n.eliminar_perfil(mesero_id)
    chk("perfil sin usuarios SI se puede borrar", not any(p["id"] == mesero_id for p in n.listar_perfiles()))

    print("== auditoria (punto 21b): quien hizo que ==")
    p_aud = n.crear(canal="cajero", cliente="Auditoria", modalidad="retiro", forma_pago="efectivo",
                    items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}])
    # 2x Lata CCU = 3000 (promo 2x real, confirmada contra el POS de Christian el
    # 03-ago-2026: precio_2x $3000, no 2*1500=3000 -- coincide, sin descuento extra).
    n.editar(p_aud["id"], cliente="Auditoria Ed", modalidad="retiro",
             items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 2}], usuario="Pedro")
    n.registrar_pagos(p_aud["id"], [{"metodo": "transferencia", "monto": 3000}], usuario="Pedro")
    n.transicionar(p_aud["id"], "en_preparacion")
    n.transicionar(p_aud["id"], "cancelado", usuario="Ana")
    log = n.listar_auditoria()
    chk("auditoria registra 'editar_pedido' con quien lo hizo",
        any(e["usuario"] == "Pedro" and e["accion"] == "editar_pedido" and e["pedido_id"] == p_aud["id"] for e in log))
    chk("auditoria registra 'redistribuir_pago' con quien lo hizo",
        any(e["usuario"] == "Pedro" and e["accion"] == "redistribuir_pago" and e["pedido_id"] == p_aud["id"] for e in log))
    chk("auditoria registra 'anular' con quien lo hizo",
        any(e["usuario"] == "Ana" and e["accion"] == "anular" and e["pedido_id"] == p_aud["id"] for e in log))
    anulados_de_p_aud = [e for e in log if e["accion"] == "anular" and e["pedido_id"] == p_aud["id"]]
    chk("la transicion normal a cocina NO genera una entrada 'anular' extra (solo 1)",
        len(anulados_de_p_aud) == 1)

    mesero2_id = n.crear_perfil("Mesero 2", ["vender"], usuario="Christian")
    n.editar_perfil(mesero2_id, permisos=["vender", "editar_pedido"], usuario="Christian")
    uid_aud = n.crear_usuario("Test Auditoria", "0000", mesero2_id, usuario="Christian")
    n.cambiar_pin(uid_aud, "9876", usuario="Christian")
    n.desactivar_usuario(uid_aud, usuario="Christian")
    n.reactivar_usuario(uid_aud, usuario="Christian")
    log2 = n.listar_auditoria()
    for accion in ("crear_perfil", "editar_perfil", "crear_usuario",
                   "cambiar_pin", "desactivar_usuario", "reactivar_usuario"):
        chk(f"auditoria registra '{accion}' con quien lo hizo",
            any(e["usuario"] == "Christian" and e["accion"] == accion for e in log2))
    chk("cambiar_pin NUNCA guarda el PIN nuevo en el detalle",
        not any("9876" in (e.get("detalle") or "") for e in log2 if e["accion"] == "cambiar_pin"))
    chk("listar_auditoria trae lo mas reciente primero", log2[0]["id"] > log2[-1]["id"])

    print("== usuario_por_id (punto 21c: base del permiso en backend) ==")
    ua = n.usuario_por_id(admin_id)
    chk("usuario_por_id trae los permisos del admin", bool(ua) and len(ua["permisos"]) == len(PERMISOS)
        if ua else False)
    chk("usuario_por_id con id inexistente da None", n.usuario_por_id(999999) is None)
    n.desactivar_usuario(uid_aud)
    chk("usuario_por_id NO trae usuarios desactivados", n.usuario_por_id(uid_aud) is None)

    print("== multiples direcciones por telefono (un cliente puede repartir a 2 lugares) ==")
    chk("_partir_direccion sin prefijo", n._partir_direccion("Los Alamos 200") == (0, "Los Alamos 200"))
    chk("_partir_direccion con parentesis", n._partir_direccion("(1500) Los Alamos 200") == (1500, "Los Alamos 200"))
    chk("_partir_direccion con corchetes", n._partir_direccion("[2000] Los Alamos 200") == (2000, "Los Alamos 200"))
    tel_multi = "988887777"
    pc1 = n.crear(canal="cajero", cliente="Multi", modalidad="delivery", forma_pago="efectivo",
                  telefono=tel_multi, direccion="(1500) Casa: Los Alamos 200",
                  items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}])
    pc2 = n.crear(canal="cajero", cliente="Multi", modalidad="delivery", forma_pago="efectivo",
                  telefono=tel_multi, direccion="(2500) Trabajo: Av Central 900",
                  items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}])
    dirs = n.listar_direcciones_cliente(tel_multi)
    chk("quedan las 2 direcciones guardadas para el mismo telefono", len(dirs) == 2)
    chk("cada una con su propio monto",
        {d["direccion"]: d["delivery_cobro"] for d in dirs} ==
        {"Casa: Los Alamos 200": 1500, "Trabajo: Av Central 900": 2500})
    # pedir de nuevo a la MISMA direccion (con otro monto, ej. el local subio el precio de esa zona)
    # debe ACTUALIZAR esa fila (no duplicarla) y subir el contador de usos
    n.crear(canal="cajero", cliente="Multi", modalidad="delivery", forma_pago="efectivo",
           telefono=tel_multi, direccion="(1800) Casa: Los Alamos 200",
           items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}])
    dirs2 = n.listar_direcciones_cliente(tel_multi)
    chk("repetir la MISMA direccion actualiza, no duplica (siguen siendo 2)", len(dirs2) == 2)
    casa = next(d for d in dirs2 if d["direccion"] == "Casa: Los Alamos 200")
    chk("el monto de 'Casa' quedo actualizado a 1800", casa["delivery_cobro"] == 1800)
    chk("'Casa' ya tiene 2 usos (se pidio 2 veces)", casa["usos"] == 2)
    chk("cliente sin direcciones da lista vacia", n.listar_direcciones_cliente("999999999") == [])
    # clientes.direccion (el campo simple/legacy) sigue funcionando igual: "la ultima usada"
    cli = n.buscar_cliente_telefono(tel_multi)
    chk("clientes.direccion sigue siendo un solo campo (la ultima usada), sin romper nada",
        "1800" in cli["direccion"] and "Los Alamos" in cli["direccion"])

    print("\n--- COMANDA de ejemplo ---")
    print(n.comanda_texto(p["id"]))

    print("== 04-ago-2026 (P29): pedido PROGRAMADO ==")
    pprog = n.crear(canal="cajero", cliente="Programado", modalidad="retiro",
                    items=[{"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1}],
                    hora_programada="21:00")
    chk("hora_programada queda guardada en el pedido", pprog.get("hora_programada") == "21:00")
    chk("y sobrevive releerlo de la base", n.obtener(pprog["id"]).get("hora_programada") == "21:00")
    chk("aparece en la cola normal (visible, no escondido)",
        any(x["id"] == pprog["id"] for x in n.cola()))
    try:
        n.crear(canal="cajero", cliente="X", modalidad="retiro",
               items=[{"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1}],
               hora_programada="25:99")
        chk("rechaza una hora_programada invalida", False)
    except ValueError:
        chk("rechaza una hora_programada invalida", True)
    pnorm = n.crear(canal="cajero", cliente="Normal", modalidad="retiro",
                    items=[{"categoria": "Churrascos", "producto": "Churrasco Italiano", "cantidad": 1}])
    chk("un pedido normal (sin programar) queda con hora_programada=None",
        pnorm.get("hora_programada") is None)

    n._con.close()
    DB_TEST.unlink(missing_ok=True)
    print("\n" + ("TODO OK" if all(ok) else ">>> HAY FALLAS <<<"))

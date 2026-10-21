#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inventario.py — Núcleo del WMS de "Donde el Patata".
====================================================
El WMS NO es una app aparte: es un MÓDULO que se enchufa sobre el POS que ya
existe. Comparte la MISMA base SQLite (`pos/pedidos.db`), los MISMOS usuarios /
roles / permisos y el MISMO motor de precios (`bot/tarificar.py`). No duplica
nada de eso; agrega lo único que al POS le falta para gestionar inventario:

  - Proveedores          (maestro 3 de datos_maestros_clave.md)
  - Insumos              (maestro 2: unidad compra/uso, factor de conversión, lote/vencimiento)
  - Ubicaciones / bins   (sub-ubicaciones dentro del local: cámara fría, bodega seca, línea de armado)
  - Recetas / BOM        (maestro 9: liga cada producto del POS a sus insumos, con merma %)
  - Movimientos          (entradas, salidas por venta, ajustes) — la verdad del stock
  - Recepciones          (documento de ingreso de mercadería; se llena con el parser SII)
  - Productos terminados (limbo de lo confeccionado que no se vendió: revender / rebajar / regalar / destruir)

INTEGRACIÓN con el POS = una sola costura:
  * La identidad de cada línea de pedido del POS es (categoria, producto, variante).
    Una receta se define contra ESA misma identidad → el WMS "lee" la estructura
    del POS, no una copia. Si el menú cambia, solo se re-mapea la receta.
  * `sincronizar_estado(pedido_id, nuevo_estado)` es el gancho: en 'en_preparacion'
    (A cocina) se explota la receta y se descuenta el stock; si se 'cancela' un
    pedido YA confeccionado, el insumo NO vuelve (ya se usó) — se registra un
    producto terminado a resolver. Tiempo real, mismo proceso, sin batch.

SEMÁFORO DINÁMICO: el umbral de alerta no es fijo. Se calcula del consumo real
(historia de ventas del POS): punto de reorden = consumo_diario × (lead time +
días de seguridad). El admin puede pisarlo a 'manual' cuando quiera.

Sin dependencias externas: librería estándar + el núcleo del POS.
Auto-test: `python wms/inventario.py`  (usa una base temporal, NO toca la real).
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

AQUI = Path(__file__).resolve().parent      # .../Donde el Patata/wms
RAIZ = AQUI.parent                          # .../Donde el Patata
POS = RAIZ / "pos"
sys.path.insert(0, str(POS))                # para importar el núcleo del POS
from nucleo import NucleoPedidos, PERMISOS as PERMISOS_POS   # noqa: E402

# Permisos NUEVOS que agrega el WMS (se suman a los del POS; siguen fail-closed:
# un perfil no-admin no los tiene salvo que se le asignen explícitamente).
PERMISOS_WMS = [
    "gestionar_insumos",     # crear/editar insumos, proveedores, ubicaciones
    "gestionar_recetas",     # definir la ficha técnica (BOM) de cada producto
    "recibir_mercaderia",    # ingresar stock (recepción SII / manual)
    # --- 21-jul-2026: SEGREGACIÓN DE LLAVES (antes las dos cosas iban juntas) ---
    # El riesgo de "me equivoqué contando" y el de "regalé/boté mercadería" NO es el
    # mismo. Con una sola llave, "ajusté por conteo" es la excusa perfecta para tapar
    # un faltante por robo. Por eso ahora son permisos distintos:
    "ajustar_inventario",    # corregir por CONTEO (error honesto del día a día) — cajero
    "dar_de_baja",           # REGALAR / DESTRUIR / cortesías y mermas — jefe o supervisor
    "ver_reportes_wms",      # dashboards de stock, consumo, valorización
]

# Disposiciones de un producto terminado que DESTRUYEN valor: exigen 'dar_de_baja'.
# Las otras (revender, vender más barato, devolver insumos) recuperan o corrigen, y
# siguen bastando con 'ajustar_inventario'.
DISPOSICIONES_QUE_EXIGEN_BAJA = {"regalado", "destruido"}

# Conteo ciclico (§2): que dia de la semana toca lo semanal (0=lunes).
# Lunes: el fin de semana es el de mas movimiento, conviene contar despues.
DIA_CONTEO_SEMANAL = 0

ESTADO_DESCUENTO = "en_preparacion"          # cuándo se consume físicamente el insumo

# Semáforo dinámico: parámetros por defecto (el admin los ajusta por insumo).
LEAD_TIME_DEFAULT = 2                         # días que tarda en reponerse desde el CD
DIAS_SEGURIDAD_DEFAULT = 1                    # colchón sobre el lead time
DIAS_IDEAL_EXTRA = 3                          # cobertura extra para considerarlo "verde"
VENTANA_CONSUMO_DIAS = 14                     # ventana de historia para el consumo diario

# Destinos posibles de un producto terminado que no se vendió.
DISPOSICIONES = {"revendido", "vendido_menor", "regalado", "destruido", "devolver_insumos"}


def _ahora():
    return datetime.now().isoformat(timespec="seconds")


def _dias_entre(iso_desde, iso_hasta):
    try:
        return (datetime.fromisoformat(iso_hasta) - datetime.fromisoformat(iso_desde)).days
    except Exception:
        return 0


def _f(row):
    """sqlite3.Row -> dict (o None)."""
    return dict(row) if row is not None else None


class NucleoInventario:
    """Capa de inventario montada sobre un NucleoPedidos ya construido.

    Reutiliza SU conexión y SU lock: una sola conexión a la base = cero choques
    de escritura entre POS y WMS (que es justo lo que rompería tener dos apps)."""

    def __init__(self, nucleo: NucleoPedidos):
        self.n = nucleo
        self._con = nucleo._con        # MISMA conexión SQLite que el POS
        self._lock = nucleo._lock      # MISMO RLock: acceso serializado entre hilos
        self._crear_tablas()
        self._migrar_conteo_ciclico()
        self.asegurar_permisos_admin()

    # ------------------------------------------------------------------
    # Esquema
    # ------------------------------------------------------------------
    def _crear_tablas(self):
        with self._lock:
            self._con.executescript("""
            CREATE TABLE IF NOT EXISTS proveedores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL, rubro TEXT, contacto TEXT, telefono TEXT,
                direccion TEXT, rut TEXT, condiciones_pago TEXT, dias_entrega TEXT,
                pedido_minimo INTEGER DEFAULT 0,
                eval_precio INTEGER, eval_calidad INTEGER, eval_cumplimiento INTEGER,
                estado TEXT DEFAULT 'activo', creado_en TEXT
            );
            CREATE TABLE IF NOT EXISTS ubicaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local TEXT DEFAULT 'Huechuraba', nombre TEXT NOT NULL,
                tipo TEXT, estado TEXT DEFAULT 'activo'
            );
            CREATE TABLE IF NOT EXISTS insumos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT, nombre TEXT NOT NULL, categoria TEXT,
                unidad_compra TEXT, unidad_uso TEXT,
                factor_conversion REAL DEFAULT 1,    -- 1 u.compra = factor u.uso
                costo_unitario REAL DEFAULT 0,        -- costo por u.compra
                proveedor_id INTEGER, ubicacion_id INTEGER,
                stock_min REAL DEFAULT 0, stock_ideal REAL DEFAULT 0,  -- umbrales manuales (u.uso)
                control_lote INTEGER DEFAULT 0, vida_util_dias INTEGER,
                lead_time_dias REAL, dias_seguridad REAL,             -- parámetros del semáforo dinámico
                semaforo_modo TEXT DEFAULT 'auto',                    -- 'auto' (historia) | 'manual'
                estado TEXT DEFAULT 'activo', creado_en TEXT
            );
            CREATE TABLE IF NOT EXISTS recetas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                categoria TEXT NOT NULL, producto TEXT NOT NULL,
                variante TEXT DEFAULT '',            -- '' = aplica a todas las variantes
                insumo_id INTEGER NOT NULL,
                cantidad_neta REAL NOT NULL,          -- lo que entra al producto, en u.uso
                merma_pct REAL DEFAULT 0,             -- % de merma inevitable (0-100)
                creado_en TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_receta_ident
                ON recetas(categoria, producto, variante, insumo_id);
            CREATE TABLE IF NOT EXISTS movimientos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                en TEXT, tipo TEXT,                   -- entrada | salida | ajuste
                insumo_id INTEGER NOT NULL,
                cantidad REAL NOT NULL,               -- con signo (entrada +, salida -)
                ubicacion_id INTEGER,
                pedido_id INTEGER, recepcion_id INTEGER,
                usuario TEXT, motivo TEXT, lote TEXT, vencimiento TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mov_insumo ON movimientos(insumo_id);
            CREATE INDEX IF NOT EXISTS idx_mov_pedido ON movimientos(pedido_id);
            CREATE INDEX IF NOT EXISTS idx_mov_venta ON movimientos(insumo_id, motivo, en);
            CREATE TABLE IF NOT EXISTS recepciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                en TEXT, proveedor_id INTEGER,
                tipo_doc TEXT, folio TEXT, rut_emisor TEXT,
                neto INTEGER, iva INTEGER, total INTEGER, fecha_doc TEXT,
                ted_raw TEXT, usuario TEXT, estado TEXT DEFAULT 'ingresada'
            );
            CREATE TABLE IF NOT EXISTS productos_terminados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                en TEXT, categoria TEXT, producto TEXT, variante TEXT,
                cantidad REAL, pedido_origen INTEGER,
                estado TEXT DEFAULT 'disponible',     -- disponible | revendido | vendido_menor
                                                      -- | regalado | destruido | insumos_devueltos
                costo_estimado REAL DEFAULT 0,        -- costo de insumos que se fue en confeccionarlo
                precio_recuperado REAL DEFAULT 0,     -- si se vendió más barato
                resuelto_en TEXT, usuario TEXT, motivo TEXT
            );
            """)
            self._con.commit()

    def _migrar_conteo_ciclico(self):
        """§2 (21-jul-2026). Agrega lo que falta para el conteo cíclico, sin romper
        bases existentes: una columna en insumos y la tabla de conteos."""
        with self._lock:
            try:
                self._con.execute(
                    "ALTER TABLE insumos ADD COLUMN frecuencia_conteo TEXT DEFAULT 'ninguno'")
            except Exception:
                pass                                   # ya existía: nada que hacer
            self._con.executescript("""
            CREATE TABLE IF NOT EXISTS conteos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                en TEXT, insumo_id INTEGER,
                esperado REAL, contado REAL, diferencia REAL,
                costo_diferencia REAL DEFAULT 0,
                usuario TEXT,
                estado TEXT DEFAULT 'cuadra',      -- cuadra | pendiente_revision | resuelto
                resuelto_en TEXT, resuelto_por TEXT, motivo_resolucion TEXT,
                ajustado INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_conteo_estado ON conteos(estado);
            """)
            self._con.commit()

    def insumos_a_contar(self, dia_semana=None):
        """Qué toca contar hoy. Diario = siempre; semanal = un día fijo de la semana.
        DEVUELVE SOLO nombre y unidad: **jamás la cantidad esperada**. El conteo es
        CIEGO — si el que cuenta ve el número del sistema, escribe ese y el control
        se vuelve decorativo."""
        dia = datetime.now().weekday() if dia_semana is None else dia_semana
        with self._lock:
            filas = self._con.execute(
                "SELECT id, nombre, unidad_uso, frecuencia_conteo FROM insumos "
                "WHERE COALESCE(estado,'activo')!='inactivo' "
                "AND COALESCE(frecuencia_conteo,'ninguno')!='ninguno' "
                "ORDER BY nombre").fetchall()
        out = []
        for r in filas:
            frec = r["frecuencia_conteo"]
            if frec == "diario" or (frec == "semanal" and dia == DIA_CONTEO_SEMANAL):
                out.append({"insumo_id": r["id"], "nombre": r["nombre"],
                            "unidad": r["unidad_uso"] or "u", "frecuencia": frec})
        return out

    def registrar_conteo(self, insumo_id, contado, usuario=None):
        """Recibe el conteo FÍSICO y lo compara contra el sistema. TOLERANCIA 0: son
        unidades enteras y contables, cualquier diferencia escala.

        Devuelve SOLO si cuadra o no — nunca el esperado ni el tamaño de la diferencia.
        Esa información va al supervisor, no a quien cuenta.
        NO ajusta el stock: ajustar automáticamente taparía justo lo que se busca
        detectar. El ajuste lo autoriza el supervisor en resolver_conteo().
        """
        ins = self.obtener_insumo(insumo_id)
        if not ins:
            raise ValueError("insumo inexistente")
        esperado = self.stock_actual(insumo_id)
        contado = float(contado)
        dif = round(contado - esperado, 4)
        factor = ins.get("factor_conversion") or 1
        costo_uso = (ins.get("costo_unitario") or 0) / factor if factor else 0
        estado = "cuadra" if dif == 0 else "pendiente_revision"
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO conteos (en, insumo_id, esperado, contado, diferencia, "
                "costo_diferencia, usuario, estado) VALUES (?,?,?,?,?,?,?,?)",
                (_ahora(), insumo_id, esperado, contado, dif,
                 round(abs(dif) * costo_uso, 0), usuario, estado))
            self._con.commit()
        return {"cuadra": estado == "cuadra", "conteo_id": cur.lastrowid,
                "insumo": ins["nombre"]}

    def conteos_pendientes(self):
        """Para el SUPERVISOR: acá sí va el detalle completo de la diferencia."""
        with self._lock:
            return [_f(r) for r in self._con.execute(
                "SELECT c.*, i.nombre AS insumo_nombre, i.unidad_uso FROM conteos c "
                "JOIN insumos i ON i.id=c.insumo_id WHERE c.estado='pendiente_revision' "
                "ORDER BY c.en DESC").fetchall()]

    def resolver_conteo(self, conteo_id, ajustar=True, usuario=None, motivo=""):
        """El supervisor cierra la diferencia. Si ajustar=True, recién ahí el stock del
        sistema se alinea con lo contado (queda como movimiento 'ajuste', o sea aparece
        en salidas_sin_venta si fue a la baja)."""
        with self._lock:
            r = self._con.execute("SELECT * FROM conteos WHERE id=?", (conteo_id,)).fetchone()
        if not r or r["estado"] != "pendiente_revision":
            return {"ok": False, "motivo": "no está pendiente"}
        if ajustar and r["diferencia"]:
            self.ajustar(r["insumo_id"], r["diferencia"], usuario=usuario,
                         motivo=f"conteo cíclico: {motivo}".strip(": "))
        with self._lock:
            self._con.execute(
                "UPDATE conteos SET estado='resuelto', resuelto_en=?, resuelto_por=?, "
                "motivo_resolucion=?, ajustado=? WHERE id=?",
                (_ahora(), usuario, motivo, 1 if ajustar else 0, conteo_id))
            self._con.commit()
        return {"ok": True, "ajustado": bool(ajustar)}

    def asegurar_permisos_admin(self):
        """Todo perfil admin (es_admin=1) recibe también los permisos del WMS.
        Idempotente: solo agrega los que falten. No toca perfiles no-admin."""
        with self._lock:
            filas = self._con.execute(
                "SELECT id, permisos FROM perfiles WHERE es_admin=1").fetchall()
            for r in filas:
                actuales = json.loads(r["permisos"] or "[]")
                faltan = [p for p in PERMISOS_WMS if p not in actuales]
                if faltan:
                    self._con.execute("UPDATE perfiles SET permisos=? WHERE id=?",
                                      (json.dumps(actuales + faltan), r["id"]))
            self._con.commit()

    # ------------------------------------------------------------------
    # Maestros: proveedores, ubicaciones, insumos
    # ------------------------------------------------------------------
    def agregar_proveedor(self, nombre, **kw):
        campos = ("rubro", "contacto", "telefono", "direccion", "rut",
                  "condiciones_pago", "dias_entrega", "pedido_minimo",
                  "eval_precio", "eval_calidad", "eval_cumplimiento", "estado")
        d = {k: kw.get(k) for k in campos}
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO proveedores (nombre, rubro, contacto, telefono, direccion, rut, "
                "condiciones_pago, dias_entrega, pedido_minimo, eval_precio, eval_calidad, "
                "eval_cumplimiento, estado, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (nombre, d["rubro"], d["contacto"], d["telefono"], d["direccion"], d["rut"],
                 d["condiciones_pago"], d["dias_entrega"], d["pedido_minimo"] or 0,
                 d["eval_precio"], d["eval_calidad"], d["eval_cumplimiento"],
                 d["estado"] or "activo", _ahora()))
            self._con.commit()
            return cur.lastrowid

    def listar_proveedores(self):
        with self._lock:
            return [_f(r) for r in self._con.execute(
                "SELECT * FROM proveedores ORDER BY nombre").fetchall()]

    def agregar_ubicacion(self, nombre, tipo=None, local="Huechuraba"):
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO ubicaciones (local, nombre, tipo, estado) VALUES (?,?,?,'activo')",
                (local, nombre, tipo))
            self._con.commit()
            return cur.lastrowid

    def listar_ubicaciones(self):
        with self._lock:
            return [_f(r) for r in self._con.execute(
                "SELECT * FROM ubicaciones ORDER BY nombre").fetchall()]

    def agregar_insumo(self, nombre, **kw):
        campos = ("codigo", "categoria", "unidad_compra", "unidad_uso", "factor_conversion",
                  "costo_unitario", "proveedor_id", "ubicacion_id", "stock_min", "stock_ideal",
                  "control_lote", "vida_util_dias", "lead_time_dias", "dias_seguridad",
                  "semaforo_modo", "estado", "frecuencia_conteo")
        d = {k: kw.get(k) for k in campos}
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO insumos (codigo, nombre, categoria, unidad_compra, unidad_uso, "
                "factor_conversion, costo_unitario, proveedor_id, ubicacion_id, stock_min, "
                "stock_ideal, control_lote, vida_util_dias, lead_time_dias, dias_seguridad, "
                "semaforo_modo, estado, frecuencia_conteo, creado_en) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["codigo"], nombre, d["categoria"], d["unidad_compra"], d["unidad_uso"],
                 d["factor_conversion"] or 1, d["costo_unitario"] or 0, d["proveedor_id"],
                 d["ubicacion_id"], d["stock_min"] or 0, d["stock_ideal"] or 0,
                 1 if d["control_lote"] else 0, d["vida_util_dias"], d["lead_time_dias"],
                 d["dias_seguridad"], d["semaforo_modo"] or "auto", d["estado"] or "activo",
                 d["frecuencia_conteo"] or "ninguno", _ahora()))
            self._con.commit()
            return cur.lastrowid

    def listar_insumos(self, incluir_inactivos=False):
        """Los desactivados (borrados con historia) quedan fuera por defecto: no deben
        aparecer en selectores, semaforo ni reportes operativos."""
        with self._lock:
            sql = "SELECT * FROM insumos"
            if not incluir_inactivos:
                sql += " WHERE estado IS NULL OR estado != 'inactivo'"
            return [_f(r) for r in self._con.execute(sql + " ORDER BY nombre").fetchall()]

    def editar_insumo(self, insumo_id, **kw):
        """Actualiza SOLO los campos que vengan en kw; el resto queda intacto.
        Devuelve el insumo ya actualizado (21-jul-2026, pedido de Gonza: el admin
        tiene que poder corregir un insumo mal cargado sin borrarlo y rehacerlo)."""
        campos = ("nombre", "codigo", "categoria", "unidad_compra", "unidad_uso",
                  "factor_conversion", "costo_unitario", "proveedor_id", "ubicacion_id",
                  "stock_min", "stock_ideal", "control_lote", "vida_util_dias",
                  "lead_time_dias", "dias_seguridad", "semaforo_modo", "estado", "frecuencia_conteo")
        sets, valores = [], []
        for k in campos:
            if k in kw and kw[k] is not None:
                sets.append(f"{k}=?")
                valores.append(kw[k])
        if not sets:
            return self.obtener_insumo(insumo_id)
        valores.append(insumo_id)
        with self._lock:
            self._con.execute(f"UPDATE insumos SET {', '.join(sets)} WHERE id=?", valores)
            self._con.commit()
        return self.obtener_insumo(insumo_id)

    def eliminar_insumo(self, insumo_id):
        """Borrado SEGURO. Si el insumo no tiene historia (ni movimientos ni recetas)
        se borra de verdad. Si YA tiene historia se DESACTIVA en vez de borrarse:
        borrarlo dejaria huerfano el stock historico y las recetas que lo referencian.
        Devuelve como se resolvio, para poder avisarlo en la UI."""
        with self._lock:
            movs = self._con.execute("SELECT COUNT(*) c FROM movimientos WHERE insumo_id=?",
                                     (insumo_id,)).fetchone()["c"]
            recs = self._con.execute("SELECT COUNT(*) c FROM recetas WHERE insumo_id=?",
                                     (insumo_id,)).fetchone()["c"]
            if movs == 0 and recs == 0:
                self._con.execute("DELETE FROM insumos WHERE id=?", (insumo_id,))
                self._con.commit()
                return {"ok": True, "modo": "borrado"}
            self._con.execute("UPDATE insumos SET estado='inactivo' WHERE id=?", (insumo_id,))
            self._con.commit()
            return {"ok": True, "modo": "desactivado", "movimientos": movs, "recetas": recs}

    def obtener_insumo(self, insumo_id):
        with self._lock:
            return _f(self._con.execute(
                "SELECT * FROM insumos WHERE id=?", (insumo_id,)).fetchone())

    def configurar_semaforo(self, insumo_id, modo=None, stock_min=None, stock_ideal=None,
                            lead_time_dias=None, dias_seguridad=None):
        """El admin ajusta el semáforo de un insumo: pasar a 'manual' (usa stock_min/ideal
        fijos) o afinar los parámetros del modo 'auto' (lead time / días de seguridad)."""
        sets, args = [], []
        for col, val in (("semaforo_modo", modo), ("stock_min", stock_min),
                         ("stock_ideal", stock_ideal), ("lead_time_dias", lead_time_dias),
                         ("dias_seguridad", dias_seguridad)):
            if val is not None:
                sets.append(f"{col}=?"); args.append(val)
        if not sets:
            return
        args.append(insumo_id)
        with self._lock:
            self._con.execute(f"UPDATE insumos SET {', '.join(sets)} WHERE id=?", args)
            self._con.commit()

    # ------------------------------------------------------------------
    # Recetas / BOM
    # ------------------------------------------------------------------
    def definir_receta(self, categoria, producto, insumo_id, cantidad_neta,
                       merma_pct=0, variante=""):
        """Upsert de una línea de receta: (producto+variante) consume 'cantidad_neta'
        del insumo, con 'merma_pct' de pérdida inevitable. variante='' = todas."""
        variante = variante or ""
        with self._lock:
            self._con.execute(
                "INSERT INTO recetas (categoria, producto, variante, insumo_id, cantidad_neta, "
                "merma_pct, creado_en) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(categoria, producto, variante, insumo_id) DO UPDATE SET "
                "cantidad_neta=excluded.cantidad_neta, merma_pct=excluded.merma_pct",
                (categoria, producto, variante, insumo_id, cantidad_neta, merma_pct, _ahora()))
            self._con.commit()

    def eliminar_receta(self, categoria, producto, insumo_id, variante=""):
        """Quita UNA línea de la receta (21-jul-2026, pedido de Gonza: el admin tenía
        cómo agregar pero no cómo sacar un insumo de una receta mal armada).
        Editar una línea existente NO necesita esto: definir_receta hace upsert."""
        with self._lock:
            cur = self._con.execute(
                "DELETE FROM recetas WHERE categoria=? AND producto=? AND variante=? "
                "AND insumo_id=?", (categoria, producto, variante or "", insumo_id))
            self._con.commit()
            return {"ok": True, "borradas": cur.rowcount}

    def receta_de(self, categoria, producto):
        with self._lock:
            return [_f(r) for r in self._con.execute(
                "SELECT r.*, i.nombre AS insumo_nombre, i.unidad_uso "
                "FROM recetas r JOIN insumos i ON i.id=r.insumo_id "
                "WHERE r.categoria=? AND r.producto=? ORDER BY r.variante",
                (categoria, producto)).fetchall()]

    def _lineas_aplicables(self, categoria, producto, variante):
        """Insumos que consume un producto para una variante dada. Una línea
        específica de la variante GANA sobre la genérica ('') para el mismo insumo."""
        variante = variante or ""
        with self._lock:
            rows = self._con.execute(
                "SELECT insumo_id, variante, cantidad_neta, merma_pct FROM recetas "
                "WHERE categoria=? AND producto=?", (categoria, producto)).fetchall()
        d = {}
        for r in rows:                                   # genéricas primero
            if (r["variante"] or "") == "":
                d[r["insumo_id"]] = (r["cantidad_neta"], r["merma_pct"] or 0)
        for r in rows:                                   # específicas sobreescriben
            if variante and (r["variante"] or "") == variante:
                d[r["insumo_id"]] = (r["cantidad_neta"], r["merma_pct"] or 0)
        return d

    @staticmethod
    def _bruta(cantidad_neta, merma_pct):
        """Cantidad a descontar del insumo en bruto, incluyendo la merma.
        Ej: 50 g netos con 25% de merma -> 66.7 g descontados."""
        m = merma_pct or 0
        if m <= 0 or m >= 100:
            return cantidad_neta
        return cantidad_neta / (1 - m / 100.0)

    def explotar_item(self, item):
        """Un ítem del pedido -> [(insumo_id, consumo_en_u_uso), ...].
        item: {categoria, producto, variante, cantidad}."""
        cat = item.get("categoria")
        prod = item.get("producto")
        var = item.get("variante") or ""
        qty = item.get("cantidad") or 1
        out = []
        for insumo_id, (neta, merma) in self._lineas_aplicables(cat, prod, var).items():
            out.append((insumo_id, self._bruta(neta, merma) * qty))
        return out

    def explotar_pedido(self, pedido):
        """Consumo total de un pedido, consolidado por insumo (sin escribir nada).
        Sirve para PREVISUALIZAR el impacto antes de descontar."""
        acum = {}
        for it in pedido.get("items", []):
            for insumo_id, consumo in self.explotar_item(it):
                acum[insumo_id] = acum.get(insumo_id, 0) + consumo
        return acum

    MOTIVOS_SIN_VENTA = {"cortesia": "regalado", "merma": "destruido"}

    def consumir_sin_venta(self, items, motivo, usuario=None, detalle=""):
        """§1 del spec: confecciona algo que CONSUME stock pero NO registra venta.

        Dos casos reales del local: el jefe regala algo (cortesía) o se echó a perder /
        se botó (merma). Hoy eso o no se registraba, o se disfrazaba de venta anulada.

        Qué hace, en orden:
          1. Explota la receta de cada ítem y DESCUENTA los insumos (tipo 'salida', como
             una venta real: el insumo se gastó de verdad).
          2. Registra un producto_terminado ya resuelto como 'regalado' o 'destruido',
             con su costo. Así aparece SOLO (y una vez) en salidas_sin_venta().

        `motivo` es TIPADO: 'cortesia' o 'merma'. Texto libre no se acepta — sin eso el
        reporte no se puede agrupar ni auditar.
        NO crea pedido, NO toca la caja, NO suma a las ventas.
        """
        if motivo not in self.MOTIVOS_SIN_VENTA:
            raise ValueError(f"motivo debe ser uno de {sorted(self.MOTIVOS_SIN_VENTA)}")
        estado = self.MOTIVOS_SIN_VENTA[motivo]
        if not items:
            raise ValueError("no hay ítems que confeccionar")

        creados, costo_total = [], 0
        for it in items:
            cat = it.get("categoria"); prod = it.get("producto")
            var = it.get("variante") or ""
            qty = max(1, int(it.get("cantidad") or 1))
            item = {"categoria": cat, "producto": prod, "variante": var, "cantidad": qty}
            # 1) descontar los insumos de verdad (misma explosión que una venta)
            for insumo_id, cantidad in self.explotar_item(item):
                self._mov("salida", insumo_id, -abs(cantidad), usuario=usuario,
                          motivo=f"{motivo}: {detalle}".strip(": "))
            # 2) dejarlo registrado como salida sin venta, ya resuelta
            costo = self.costo_item(item)
            costo_total += costo
            with self._lock:
                cur = self._con.execute(
                    "INSERT INTO productos_terminados (en, categoria, producto, variante, "
                    "cantidad, estado, costo_estimado, resuelto_en, usuario, motivo) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (_ahora(), cat, prod, var, qty, estado, costo, _ahora(), usuario,
                     f"{motivo}: {detalle}".strip(": ")))
                self._con.commit()
                creados.append(cur.lastrowid)
        print(f"[SIN VENTA][{motivo.upper()}] {len(items)} item(s) por {usuario or 'alguien'} "
              f"-- costo ${costo_total:,.0f}. Motivo: {detalle or '(sin detalle)'}")
        return {"ok": True, "motivo": motivo, "estado": estado,
                "terminados": creados, "costo": round(costo_total, 0)}

    def salidas_sin_venta(self, desde=None, hasta=None):
        """§3 del spec de control (21-jul-2026): TODO lo que salió de stock SIN generar
        venta, en un solo lugar y valorizado en $.

        Antes el dato existía (movimientos y productos_terminados guardan usuario y
        motivo) pero estaba enterrado en tablas que nadie miraba nunca. Un dato
        registrado que no se reporta NO es un control.

        Junta dos fuentes y las devuelve separadas por tipo:
          - 'merma'     -> ajustes NEGATIVOS de stock (conteo, pérdida, rotura)
          - 'cortesia'  -> terminados REGALADOS
          - 'destruido' -> terminados DESTRUIDOS
        Filtrable por período; el detalle trae quién, cuándo y cuánto costó.
        """
        desde = desde or "0000-01-01"
        hasta = (hasta or "9999-12-31") + "T23:59:59"
        filas = []
        with self._lock:
            # 1) Ajustes negativos (merma / corrección a la baja)
            for r in self._con.execute(
                    "SELECT m.en, m.cantidad, m.usuario, m.motivo, i.nombre, i.unidad_uso, "
                    "i.costo_unitario, i.factor_conversion "
                    "FROM movimientos m JOIN insumos i ON i.id=m.insumo_id "
                    "WHERE m.tipo='ajuste' AND m.cantidad<0 AND m.en BETWEEN ? AND ? "
                    "ORDER BY m.en DESC", (desde, hasta)).fetchall():
                factor = r["factor_conversion"] or 1
                costo_uso = (r["costo_unitario"] or 0) / factor if factor else 0
                filas.append({
                    "tipo": "merma", "en": r["en"], "que": r["nombre"],
                    "cantidad": abs(r["cantidad"]), "unidad": r["unidad_uso"] or "",
                    "usuario": r["usuario"] or "—", "motivo": r["motivo"] or "",
                    "costo": round(abs(r["cantidad"]) * costo_uso, 0)})
            # 2) Terminados regalados o destruidos
            for r in self._con.execute(
                    "SELECT resuelto_en, estado, categoria, producto, variante, cantidad, "
                    "costo_estimado, usuario, motivo FROM productos_terminados "
                    "WHERE estado IN ('regalado','destruido') "
                    "AND COALESCE(resuelto_en,'') BETWEEN ? AND ? "
                    "ORDER BY resuelto_en DESC", (desde, hasta)).fetchall():
                filas.append({
                    "tipo": "cortesia" if r["estado"] == "regalado" else "destruido",
                    "en": r["resuelto_en"], "que": f"{r['producto']} {r['variante'] or ''}".strip(),
                    "cantidad": r["cantidad"], "unidad": "u",
                    "usuario": r["usuario"] or "—", "motivo": r["motivo"] or "",
                    "costo": round(r["costo_estimado"] or 0, 0)})

        filas.sort(key=lambda f: f["en"] or "", reverse=True)
        # Totales POR TIPO: cortesías y mermas nunca se suman en una sola cifra.
        totales = {}
        for f in filas:
            t = totales.setdefault(f["tipo"], {"eventos": 0, "costo": 0})
            t["eventos"] += 1
            t["costo"] += f["costo"]
        por_usuario = {}
        for f in filas:
            u = por_usuario.setdefault(f["usuario"], {"eventos": 0, "costo": 0})
            u["eventos"] += 1
            u["costo"] += f["costo"]
        return {"desde": desde[:10], "hasta": hasta[:10], "detalle": filas,
                "totales": totales, "por_usuario": por_usuario,
                "costo_total": sum(f["costo"] for f in filas)}

    def costo_item(self, item):
        """Costo en $ de los insumos que consume un ítem (para valorizar terminados/mermas)."""
        total = 0.0
        for insumo_id, consumo in self.explotar_item(item):
            ins = self.obtener_insumo(insumo_id)
            if not ins:
                continue
            factor = ins.get("factor_conversion") or 1
            costo_uso = (ins.get("costo_unitario") or 0) / factor if factor else 0
            total += consumo * costo_uso
        return total

    # ------------------------------------------------------------------
    # Movimientos y stock
    # ------------------------------------------------------------------
    def _mov(self, tipo, insumo_id, cantidad_uso, ubicacion_id=None, pedido_id=None,
             recepcion_id=None, usuario=None, motivo=None, lote=None, vencimiento=None, en=None):
        """Registra un movimiento. La cantidad se guarda CON SIGNO:
        entrada +, salida -, ajuste tal cual (puede ser negativo)."""
        c = abs(cantidad_uso)
        if tipo == "salida":
            c = -c
        elif tipo == "ajuste":
            c = cantidad_uso
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO movimientos (en, tipo, insumo_id, cantidad, ubicacion_id, "
                "pedido_id, recepcion_id, usuario, motivo, lote, vencimiento) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (en or _ahora(), tipo, insumo_id, c, ubicacion_id, pedido_id, recepcion_id,
                 usuario, motivo, lote, vencimiento))
            self._con.commit()
            return cur.lastrowid

    def recibir(self, insumo_id, cantidad_compra, usuario=None, recepcion_id=None,
                ubicacion_id=None, lote=None, vencimiento=None, motivo="recepcion"):
        """Ingresa mercadería. La cantidad viene en UNIDAD DE COMPRA (ej: 2 bidones)
        y se convierte a unidad de uso con el factor del insumo antes de sumar al stock."""
        ins = self.obtener_insumo(insumo_id)
        if not ins:
            raise ValueError(f"insumo {insumo_id} no existe")
        factor = ins.get("factor_conversion") or 1
        return self._mov("entrada", insumo_id, cantidad_compra * factor, ubicacion_id=ubicacion_id,
                         recepcion_id=recepcion_id, usuario=usuario, motivo=motivo,
                         lote=lote, vencimiento=vencimiento)

    def ajustar(self, insumo_id, cantidad_uso, usuario=None, motivo="ajuste"):
        """Corrección de stock en unidad de uso (positiva o negativa: conteo, merma extra)."""
        return self._mov("ajuste", insumo_id, cantidad_uso, usuario=usuario, motivo=motivo)

    def stock_actual(self, insumo_id):
        with self._lock:
            r = self._con.execute(
                "SELECT COALESCE(SUM(cantidad),0) AS s FROM movimientos WHERE insumo_id=?",
                (insumo_id,)).fetchone()
        return r["s"] or 0

    def _ya_descontado(self, pedido_id):
        """¿El pedido ya descontó stock por venta?"""
        with self._lock:
            v = self._con.execute(
                "SELECT COUNT(*) c FROM movimientos WHERE pedido_id=? AND motivo='venta'",
                (pedido_id,)).fetchone()["c"]
        return v > 0

    def descontar_pedido(self, pedido, usuario=None):
        """Explota las recetas del pedido y descuenta el stock. Idempotente:
        si ya se descontó, no lo hace de nuevo."""
        pid = pedido.get("id")
        if self._ya_descontado(pid):
            return {"ok": False, "motivo": "ya_descontado", "consumos": {}}
        consumos = self.explotar_pedido(pedido)
        for insumo_id, consumo in consumos.items():
            self._mov("salida", insumo_id, consumo, pedido_id=pid,
                      usuario=usuario, motivo="venta")
        return {"ok": True, "consumos": consumos}

    def resolver_cancelacion(self, pedido, usuario=None):
        """Un pedido YA confeccionado (que descontó insumos) se cancela: el insumo
        NO vuelve (ya se usó), pero lo confeccionado queda como PRODUCTO TERMINADO
        'disponible' para revender / rebajar / regalar / destruir. Si el pedido
        nunca llegó a cocina (no descontó), no hay nada que hacer."""
        pid = pedido.get("id")
        if not self._ya_descontado(pid):
            return {"ok": False, "motivo": "no_confeccionado"}
        creados = []
        with self._lock:
            ya = self._con.execute(
                "SELECT COUNT(*) c FROM productos_terminados WHERE pedido_origen=?",
                (pid,)).fetchone()["c"]
        if ya:                                           # idempotente: no duplicar el limbo
            return {"ok": False, "motivo": "ya_resuelto"}
        for it in pedido.get("items", []):
            costo = self.costo_item(it)
            with self._lock:
                cur = self._con.execute(
                    "INSERT INTO productos_terminados (en, categoria, producto, variante, "
                    "cantidad, pedido_origen, estado, costo_estimado, usuario) "
                    "VALUES (?,?,?,?,?,?,'disponible',?,?)",
                    (_ahora(), it.get("categoria"), it.get("producto"), it.get("variante") or "",
                     it.get("cantidad") or 1, pid, round(costo, 2), usuario))
                self._con.commit()
                creados.append(cur.lastrowid)
        if hasattr(self.n, "registrar_auditoria"):
            self.n.registrar_auditoria(usuario, "cancelar_confeccionado", pid,
                                       f"{len(creados)} producto(s) terminado(s) a resolver")
        return {"ok": True, "terminados": creados}

    def listar_terminados(self, estado=None):
        q = "SELECT * FROM productos_terminados"
        args = []
        if estado:
            q += " WHERE estado=?"; args.append(estado)
        q += " ORDER BY en DESC"
        with self._lock:
            return [_f(r) for r in self._con.execute(q, args).fetchall()]

    def _obtener_terminado(self, terminado_id):
        with self._lock:
            return _f(self._con.execute(
                "SELECT * FROM productos_terminados WHERE id=?", (terminado_id,)).fetchone())

    def disponer_terminado(self, terminado_id, disposicion, precio_recuperado=0,
                           usuario=None, motivo=None):
        """El admin resuelve un producto terminado 'disponible':
          - revendido / vendido_menor (con precio) / regalado / destruido: solo marca el destino.
          - devolver_insumos: caso "no se alcanzó a confeccionar" -> devuelve los insumos al stock."""
        if disposicion not in DISPOSICIONES:
            raise ValueError(f"disposición inválida: {disposicion}")
        t = self._obtener_terminado(terminado_id)
        if not t:
            raise ValueError(f"terminado {terminado_id} no existe")
        if t["estado"] != "disponible":
            raise ValueError("este producto terminado ya fue resuelto")
        if disposicion == "devolver_insumos":
            item = {"categoria": t["categoria"], "producto": t["producto"],
                    "variante": t["variante"], "cantidad": t["cantidad"]}
            for insumo_id, consumo in self.explotar_item(item):
                self._mov("entrada", insumo_id, consumo, pedido_id=t["pedido_origen"],
                          usuario=usuario, motivo="reversa_terminado")
            nuevo_estado = "insumos_devueltos"
        else:
            nuevo_estado = disposicion
        with self._lock:
            self._con.execute(
                "UPDATE productos_terminados SET estado=?, precio_recuperado=?, resuelto_en=?, "
                "usuario=?, motivo=? WHERE id=?",
                (nuevo_estado, precio_recuperado or 0, _ahora(), usuario, motivo, terminado_id))
            self._con.commit()
        if hasattr(self.n, "registrar_auditoria"):
            self.n.registrar_auditoria(usuario, "disponer_terminado", t["pedido_origen"],
                                       f"terminado #{terminado_id} -> {nuevo_estado}")
        return self._obtener_terminado(terminado_id)

    def perdida_terminados(self):
        """$ perdido en productos confeccionados que no se recuperaron
        (destruidos/regalados) menos lo recuperado en ventas rebajadas."""
        with self._lock:
            rows = self._con.execute(
                "SELECT estado, COALESCE(SUM(costo_estimado),0) c, "
                "COALESCE(SUM(precio_recuperado),0) r FROM productos_terminados "
                "GROUP BY estado").fetchall()
        perdida = 0.0
        for r in rows:
            if r["estado"] in ("destruido", "regalado"):
                perdida += r["c"]
            elif r["estado"] == "vendido_menor":
                perdida += max(0, r["c"] - r["r"])
        return round(perdida, 0)

    # ------------------------------------------------------------------
    # Recepciones (documento de ingreso de mercadería; lo llena el parser SII)
    # ------------------------------------------------------------------
    def registrar_recepcion(self, proveedor_id=None, tipo_doc=None, folio=None, rut_emisor=None,
                            neto=None, iva=None, total=None, fecha_doc=None, ted_raw=None,
                            usuario=None):
        """Crea la cabecera del documento de recepción. Las entradas de stock se
        ligan a ella con `recibir(..., recepcion_id=<id>)`."""
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO recepciones (en, proveedor_id, tipo_doc, folio, rut_emisor, neto, "
                "iva, total, fecha_doc, ted_raw, usuario, estado) VALUES (?,?,?,?,?,?,?,?,?,?,?,'ingresada')",
                (_ahora(), proveedor_id, tipo_doc, folio, rut_emisor, neto, iva, total,
                 fecha_doc, ted_raw, usuario))
            self._con.commit()
            return cur.lastrowid

    def listar_recepciones(self, limite=100):
        with self._lock:
            return [_f(r) for r in self._con.execute(
                "SELECT * FROM recepciones ORDER BY id DESC LIMIT ?", (limite,)).fetchall()]

    # ------------------------------------------------------------------
    # EL GANCHO con el POS
    # ------------------------------------------------------------------
    def sincronizar_estado(self, pedido_id, nuevo_estado, usuario=None):
        """Se llama JUSTO DESPUÉS de nucleo.transicionar(). Única costura POS↔WMS:
        en 'A cocina' descuenta; al 'cancelar' un confeccionado, lo manda al limbo."""
        pedido = self.n.obtener(pedido_id)
        if not pedido:
            return None
        if nuevo_estado == ESTADO_DESCUENTO:
            return self.descontar_pedido(pedido, usuario=usuario)
        if nuevo_estado == "cancelado":
            return self.resolver_cancelacion(pedido, usuario=usuario)
        return None

    # ------------------------------------------------------------------
    # Semáforo DINÁMICO (lee la historia real de ventas del POS)
    # ------------------------------------------------------------------
    def consumo_rango(self, insumo_id, desde, hasta=None):
        q = ("SELECT COALESCE(SUM(-cantidad),0) c FROM movimientos "
             "WHERE insumo_id=? AND tipo='salida' AND motivo='venta' AND en>=?")
        args = [insumo_id, desde]
        if hasta:
            q += " AND en<?"; args.append(hasta)
        with self._lock:
            return self._con.execute(q, args).fetchone()["c"] or 0

    def consumo_diario_promedio(self, insumo_id, ventana_dias=VENTANA_CONSUMO_DIAS):
        """Consumo diario según la historia real, sobre los días efectivamente
        observados (no divide por la ventana completa si el insumo es nuevo)."""
        desde = (datetime.now() - timedelta(days=ventana_dias)).isoformat(timespec="seconds")
        with self._lock:
            rows = self._con.execute(
                "SELECT en, -cantidad AS q FROM movimientos WHERE insumo_id=? AND tipo='salida' "
                "AND motivo='venta' AND en>=?", (insumo_id, desde)).fetchall()
        if not rows:
            return 0.0
        total = sum(r["q"] for r in rows)
        primer = min(r["en"] for r in rows)
        dias = max(1, min(_dias_entre(primer, _ahora()) + 1, ventana_dias))
        return total / dias

    def tendencia(self, insumo_id):
        """Compara los últimos 7 días contra los 7 previos: subiendo / estable / bajando."""
        hoy = datetime.now()
        d7 = (hoy - timedelta(days=7)).isoformat(timespec="seconds")
        d14 = (hoy - timedelta(days=14)).isoformat(timespec="seconds")
        reciente = self.consumo_rango(insumo_id, d7)
        previo = self.consumo_rango(insumo_id, d14, d7)
        if previo <= 0:
            return "nueva" if reciente > 0 else "sin_datos"
        ratio = reciente / previo
        if ratio >= 1.2:
            return "subiendo"
        if ratio <= 0.8:
            return "bajando"
        return "estable"

    @staticmethod
    def _semaforo_umbral(stock, reorden, ideal):
        if stock <= (reorden or 0):
            return "rojo"
        if stock < (ideal or 0):
            return "amarillo"
        return "verde"

    def evaluar_insumo(self, ins):
        """Foto viva de un insumo: stock, consumo diario, días de cobertura, punto de
        reorden (dinámico o manual), semáforo, tendencia y valorización."""
        stock = self.stock_actual(ins["id"])
        cd = self.consumo_diario_promedio(ins["id"])
        modo = ins.get("semaforo_modo") or "auto"
        factor = ins.get("factor_conversion") or 1
        costo_uso = (ins.get("costo_unitario") or 0) / factor if factor else 0
        if modo == "manual" or cd <= 0:
            reorden = ins.get("stock_min") or 0
            ideal = ins.get("stock_ideal") or 0
            modo_efectivo = "manual" if modo == "manual" else "auto_sin_datos"
        else:
            lead = ins.get("lead_time_dias") or LEAD_TIME_DEFAULT
            seg = ins.get("dias_seguridad") or DIAS_SEGURIDAD_DEFAULT
            reorden = cd * (lead + seg)
            ideal = cd * (lead + seg + DIAS_IDEAL_EXTRA)
            modo_efectivo = "auto"
        return {
            "id": ins["id"], "nombre": ins["nombre"], "categoria": ins.get("categoria"),
            "unidad_uso": ins.get("unidad_uso"),
            "stock": round(stock, 3),
            "consumo_diario": round(cd, 3),
            "dias_cobertura": round(stock / cd, 1) if cd > 0 else None,
            "punto_reorden": round(reorden, 2),
            "nivel_ideal": round(ideal, 2),
            "semaforo": self._semaforo_umbral(stock, reorden, ideal),
            "modo": modo_efectivo,
            "tendencia": self.tendencia(ins["id"]),
            "valorizado": round(stock * costo_uso, 0),
        }

    def tablero(self):
        """Foto del stock de cada insumo con su semáforo. Fuente del dashboard."""
        return [self.evaluar_insumo(ins) for ins in self.listar_insumos()]

    def alertas(self):
        """Insumos en rojo o amarillo (para el semáforo del dashboard y del POS)."""
        return [r for r in self.tablero() if r["semaforo"] != "verde"]

    def consumo_por_insumo(self, desde=None, hasta=None):
        """Consumo real (salidas por venta) por insumo, base del teórico-vs-real."""
        q = ("SELECT insumo_id, COALESCE(SUM(-cantidad),0) AS consumido "
             "FROM movimientos WHERE tipo='salida' AND motivo='venta'")
        args = []
        if desde:
            q += " AND en>=?"; args.append(desde)
        if hasta:
            q += " AND en<=?"; args.append(hasta)
        q += " GROUP BY insumo_id"
        with self._lock:
            return {r["insumo_id"]: r["consumido"] for r in self._con.execute(q, args).fetchall()}

    # ------------------------------------------------------------------
    # Semilla demo (Patata / Huechuraba) para el prototipo
    # ------------------------------------------------------------------
    def sembrar_demo(self):
        """Datos mínimos para ver el WMS funcionando: bodega, proveedor, insumos
        reales del local y la receta del Churrasco Italiano."""
        if self.listar_insumos():
            return {"ok": False, "motivo": "ya_hay_datos"}
        u_seca = self.agregar_ubicacion("Bodega Seca", "bodega_seca")
        u_fria = self.agregar_ubicacion("Cámara Fría", "camara_fria")
        self.agregar_ubicacion("Línea de Armado", "linea_armado")
        prov = self.agregar_proveedor("Distribuidora Central Agrosystems", rubro="Varios",
                                      telefono="+56 2 2555 0000")
        pan = self.agregar_insumo("Pan de churrasco", categoria="Pan", unidad_compra="bolsa",
                                  unidad_uso="unidad", factor_conversion=12, costo_unitario=2400,
                                  proveedor_id=prov, ubicacion_id=u_seca, stock_min=24, stock_ideal=120,
                                  lead_time_dias=2, dias_seguridad=1)
        tomate = self.agregar_insumo("Tomate", categoria="Verduras", unidad_compra="kg",
                                     unidad_uso="g", factor_conversion=1000, costo_unitario=1200,
                                     proveedor_id=prov, ubicacion_id=u_fria, stock_min=2000, stock_ideal=8000,
                                     lead_time_dias=2, dias_seguridad=1)
        palta = self.agregar_insumo("Palta", categoria="Verduras", unidad_compra="kg",
                                    unidad_uso="g", factor_conversion=1000, costo_unitario=6000,
                                    proveedor_id=prov, ubicacion_id=u_fria, stock_min=1500, stock_ideal=6000,
                                    control_lote=1, vida_util_dias=5, lead_time_dias=1, dias_seguridad=1)
        mayo = self.agregar_insumo("Mayonesa Patata", categoria="Salsas", unidad_compra="balde",
                                   unidad_uso="g", factor_conversion=5000, costo_unitario=9000,
                                   proveedor_id=prov, ubicacion_id=u_fria, stock_min=1000, stock_ideal=5000)
        self.definir_receta("Churrascos", "Churrasco Italiano", pan, 1, merma_pct=0)
        self.definir_receta("Churrascos", "Churrasco Italiano", tomate, 40, merma_pct=10)
        self.definir_receta("Churrascos", "Churrasco Italiano", palta, 50, merma_pct=25)
        self.definir_receta("Churrascos", "Churrasco Italiano", mayo, 20, merma_pct=0)
        return {"ok": True, "insumos": [pan, tomate, palta, mayo]}


# ======================================================================
# Auto-test — no toca la base real: usa un archivo temporal descartable.
# ======================================================================
def _auto_test():
    import tempfile
    fallos = []

    def chk(nombre, cond):
        print(f"  [{'OK  ' if cond else 'FALLA'}] {nombre}")
        if not cond:
            fallos.append(nombre)

    tmp = Path(tempfile.gettempdir()) / f"wms_test_{datetime.now():%H%M%S%f}.db"
    nucleo = None
    try:
        nucleo = NucleoPedidos(db_path=tmp)
        inv = NucleoInventario(nucleo)

        print("- Esquema y permisos")
        with inv._lock:
            fila = inv._con.execute("SELECT permisos FROM perfiles WHERE es_admin=1 LIMIT 1").fetchone()
        perms_admin = json.loads(fila["permisos"]) if fila else []
        chk("admin recibio los permisos WMS", all(p in perms_admin for p in PERMISOS_WMS))
        chk("permisos POS intactos en admin", all(p in perms_admin for p in PERMISOS_POS))

        print("- Semilla demo")
        demo = inv.sembrar_demo()
        chk("sembro insumos", demo["ok"] and len(demo["insumos"]) == 4)
        pan, tomate, palta, mayo = demo["insumos"]

        print("- Recepcion y factor de conversion")
        inv.recibir(pan, 20, motivo="recepcion")       # 20 bolsas * 12 = 240 unidades
        inv.recibir(palta, 3)                           # 3 kg -> 3000 g
        inv.recibir(tomate, 4)                          # 4 kg -> 4000 g
        inv.recibir(mayo, 1)                            # 1 balde -> 5000 g
        chk("factor de conversion (20 bolsas -> 240 u)", inv.stock_actual(pan) == 240)
        chk("palta recibida en gramos (3 kg -> 3000 g)", inv.stock_actual(palta) == 3000)

        print("- Explosion de receta (BOM) con merma")
        item = {"categoria": "Churrascos", "producto": "Churrasco Italiano", "variante": "", "cantidad": 1}
        cons = dict(inv.explotar_item(item))
        chk("merma palta 25% (50 -> 66.67 g)", abs(cons[palta] - 50 / 0.75) < 0.01)
        chk("merma tomate 10% (40 -> 44.44 g)", abs(cons[tomate] - 40 / 0.90) < 0.01)
        chk("pan sin merma (1 u)", cons[pan] == 1)

        print("- Descuento automatico enganchado al estado del POS")
        nucleo.abrir_caja(10000, cajero="test")
        pedido = nucleo.crear(canal="cajero", cliente="Test", modalidad="local",
                              items=[dict(item), dict(item)], forma_pago="efectivo")  # 2 italianos
        pan_antes = inv.stock_actual(pan)
        palta_antes = inv.stock_actual(palta)
        nucleo.transicionar(pedido["id"], "en_preparacion")
        inv.sincronizar_estado(pedido["id"], "en_preparacion", usuario="test")
        chk("2 italianos descuentan 2 panes", inv.stock_actual(pan) == pan_antes - 2)
        chk("palta descontada con merma x2", abs(inv.stock_actual(palta) - (palta_antes - 2 * 50 / 0.75)) < 0.01)
        chk("no descuenta dos veces el mismo pedido", inv.descontar_pedido(pedido)["ok"] is False)

        print("- Cancelacion: el confeccionado NO devuelve insumos (va al limbo)")
        palta_tras_venta = inv.stock_actual(palta)
        nucleo.transicionar(pedido["id"], "cancelado")
        inv.sincronizar_estado(pedido["id"], "cancelado", usuario="test")
        chk("la palta NO vuelve al stock (ya confeccionada)", inv.stock_actual(palta) == palta_tras_venta)
        terms = inv.listar_terminados("disponible")
        chk("queda 1 terminado con la linea de 2 unidades consolidada",
            len(terms) == 1 and terms[0]["cantidad"] == 2)
        chk("el terminado tiene costo estimado > 0", terms[0]["costo_estimado"] > 0)
        chk("resolver_cancelacion es idempotente", inv.resolver_cancelacion(pedido)["ok"] is False)

        print("- Disposicion: destruir (perdida)")
        inv.disponer_terminado(terms[0]["id"], "destruido", usuario="admin")
        chk("terminado marcado destruido", inv._obtener_terminado(terms[0]["id"])["estado"] == "destruido")
        chk("hay perdida contabilizada", inv.perdida_terminados() > 0)

        print("- Disposicion: devolver_insumos (no se alcanzo a confeccionar)")
        ped2 = nucleo.crear(canal="cajero", cliente="B", modalidad="local",
                            items=[dict(item)], forma_pago="efectivo")   # 1 italiano
        nucleo.transicionar(ped2["id"], "en_preparacion")
        inv.sincronizar_estado(ped2["id"], "en_preparacion", usuario="test")
        nucleo.transicionar(ped2["id"], "cancelado")
        inv.sincronizar_estado(ped2["id"], "cancelado", usuario="test")
        t2 = inv.listar_terminados("disponible")[0]
        palta_pre_dev = inv.stock_actual(palta)
        inv.disponer_terminado(t2["id"], "devolver_insumos", usuario="admin")
        chk("devolver_insumos SI repone la palta (1 u con merma)",
            abs(inv.stock_actual(palta) - (palta_pre_dev + 50 / 0.75)) < 0.01)
        chk("terminado marcado insumos_devueltos",
            inv._obtener_terminado(t2["id"])["estado"] == "insumos_devueltos")

        print("- Semaforo DINAMICO (lee historia de ventas)")
        din = inv.agregar_insumo("Insumo Dinamico", categoria="Test", unidad_compra="u",
                                 unidad_uso="u", factor_conversion=1, costo_unitario=100,
                                 stock_min=5, stock_ideal=50, lead_time_dias=2, dias_seguridad=1)
        # Historia: 10 u/dia por 10 dias (fechas pasadas) -> 100 consumidas, consumo diario ~10
        hoy = datetime.now()
        for d in range(10):
            fecha = (hoy - timedelta(days=d, hours=12)).isoformat(timespec="seconds")
            inv._mov("salida", din, 10, motivo="venta", en=fecha)
        cd = inv.consumo_diario_promedio(din)
        chk("consumo diario ~10 desde la historia", 8 <= cd <= 12)
        inv.recibir(din, 120)                           # +120 recibidas - 100 vendidas = stock 20
        ev = inv.evaluar_insumo(inv.obtener_insumo(din))
        chk("stock neto = 20 (120 recibidas - 100 vendidas)", ev["stock"] == 20)
        chk("dias de cobertura ~2 (20/10)", ev["dias_cobertura"] is not None and 1.5 <= ev["dias_cobertura"] <= 2.5)
        chk("semaforo dinamico en ROJO bajo el reorden (~30)", ev["semaforo"] == "rojo")
        inv.recibir(din, 60)                            # stock 80 > ideal dinamico ~60 -> VERDE
        chk("con mas stock pasa a VERDE", inv.evaluar_insumo(inv.obtener_insumo(din))["semaforo"] == "verde")

        print("- Override MANUAL del admin pisa lo dinamico")
        inv.configurar_semaforo(din, modo="manual", stock_min=5, stock_ideal=200)
        evm = inv.evaluar_insumo(inv.obtener_insumo(din))
        chk("modo manual respeta umbrales fijos (80 entre 5 y 200 = amarillo)", evm["semaforo"] == "amarillo")
        chk("el modo reportado es manual", evm["modo"] == "manual")

        print("- Insumo sin historia cae al umbral manual (honesto)")
        sinv = inv.agregar_insumo("Sin Ventas", unidad_uso="u", factor_conversion=1,
                                  stock_min=5, stock_ideal=50)
        inv.recibir(sinv, 10)
        ev_sv = inv.evaluar_insumo(inv.obtener_insumo(sinv))
        chk("insumo sin ventas usa modo auto_sin_datos", ev_sv["modo"] == "auto_sin_datos")

        print()
        print(f"RESULTADO: {'TODO OK' if not fallos else str(len(fallos)) + ' FALLA(S) -> ' + str(fallos)}")
    finally:
        try:
            nucleo._con.close()
        except Exception:
            pass
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    return not fallos


if __name__ == "__main__":
    sys.exit(0 if _auto_test() else 1)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finanzas.py — Capa contable / LIBRO DE CAJA de "Donde el Patata".
=================================================================
Traslada al sistema la "inteligencia" de la planilla Excel real de Christian
(una hoja por mes; columnas = días 1..31; filas = ingresos por medio de pago +
egresos por categoría; base tributaria Real/Teórico). NO es una app aparte: es
un MÓDULO más del WMS, montado sobre el MISMO `pedidos.db`, con los MISMOS
usuarios/permisos (igual que inventario.py). Reusa la conexión y el lock del POS.

QUÉ APORTA (lo único que faltaba para el libro de caja):
  - `categorias_gasto`  : catálogo maestro de líneas de egreso de la planilla,
                          tipadas (insumo_variable | costo_fijo | otro_egreso |
                          pago_delivery) y con frecuencia (diaria | mensual).
  - `egresos`           : hechos diarios (fecha, categoría, proveedor OPCIONAL,
                          monto, con/ sin factura, IVA, nota). La relación
                          proveedor↔insumo es M:N por diseño: cada egreso liga 1
                          proveedor (o ninguno = comodín) con 1 categoría. No hay
                          columna fija por proveedor.
  - `ingresos_manuales` : lo único que el POS no da (Boleta Manual del SII).
  - `ventas_dia`        : snapshot de ventas por medio de pago para días SIN POS
                          (histórico importado de la planilla). Para el mes en
                          curso los ingresos se LEEN del POS (cero doble digitación).

DÓNDE SE TOCA CON EL RESTO DEL SISTEMA (2 costuras, no más):
  1. INGRESOS  ← POS: `nucleo.ventas_periodo(fecha,fecha)` da efectivo/
     transferencia/tarjeta(=POS). No se re-digita ninguna venta.
  2. COMPRAS CON FACTURA ← WMS: una recepción SII (tabla `recepciones`, con
     neto/IVA/folio reales) puede generar el egreso contable con IVA crédito
     REAL — un mismo escaneo alimenta stock + contabilidad (ver egreso_desde_
     recepcion en el enganche; aquí queda el campo `recepcion_id`).

Reglas tributarias (de la planilla, respetadas al pie):
  Boleta Efectiva = Boleta Manual − Transferencia
  Ingreso Real     = Efectivo + Transferencia + POS      (toda la caja que entra)
  Ingreso Teórico  = POS + Transferencia + Boleta Efectiva (lo declarable)
  Total Egresos    = insumos variables + costos fijos
  Flujo Real       = Ingreso Real    − Total Egresos − Otros Egresos − Pago Delivery
  Flujo Teórico    = Ingreso Teórico − Total Egresos − Otros Egresos − Pago Delivery
  (Real − Teórico = caja no documentada.)

Sin dependencias externas: librería estándar + el núcleo del POS.
Auto-test: `python wms/finanzas.py`   (base temporal, NO toca la real).
"""

import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent      # .../Donde el Patata/wms
RAIZ = AQUI.parent                          # .../Donde el Patata
POS = RAIZ / "pos"
sys.path.insert(0, str(POS))                # para importar el núcleo del POS
from nucleo import NucleoPedidos             # noqa: E402

# Permisos NUEVOS que agrega la capa contable (se suman a los del POS/WMS;
# fail-closed: un perfil no-admin no los tiene salvo asignación explícita).
PERMISOS_FINANZAS = [
    "ver_finanzas",        # ver el libro de caja, resúmenes y consolidado
    "gestionar_finanzas",  # cargar egresos/ingresos manuales, editar categorías
]

IVA_TASA = 0.19                              # IVA Chile (precios al público lo incluyen)

# Tipos de línea de egreso.
TIPOS_EGRESO = {"insumo_variable", "costo_fijo", "otro_egreso", "pago_delivery"}

# Medio de pago del POS -> concepto de la planilla ("Ventas con POS" = tarjeta).
_MAP_POS = {"efectivo": "efectivo", "transferencia": "transferencia", "tarjeta": "pos"}

# ---------------------------------------------------------------------------
# Semilla de categorías = filas EXACTAS de la planilla real de Christian.
# (nombre, tipo, frecuencia, proveedor_sugerido)  — orden = layout de la planilla.
# El proveedor sugerido es solo una AYUDA de UI; el proveedor real de cada gasto
# se elige al cargar el egreso (o queda NULL = comodín "Otro"/"oferta"/sin dato).
# ---------------------------------------------------------------------------
CATEGORIAS_SEMILLA = [
    ("Pago al delivery",           "pago_delivery",   "diaria",  None),
    # --- insumos variables (proveedor / insumo de la planilla) ---
    ("Churrasco",                  "insumo_variable", "diaria",  "Germán"),
    ("Vienesas",                   "insumo_variable", "diaria",  "Ariztia"),
    ("Pan",                        "insumo_variable", "diaria",  "Jargui"),
    ("Tomates",                    "insumo_variable", "diaria",  "Tobar"),
    ("Paltas",                     "insumo_variable", "diaria",  "QuintaFresh"),
    ("Empanadita Queso",           "insumo_variable", "diaria",  "Min Verde HUILCO"),
    ("Papas congeladas",           "insumo_variable", "diaria",  None),
    ("Huevos",                     "insumo_variable", "diaria",  "Benzi"),
    ("Cartones, art de aseo",      "insumo_variable", "diaria",  "Todococktail"),
    ("Aceite",                     "insumo_variable", "diaria",  None),   # "oferta"
    ("Queso",                      "insumo_variable", "diaria",  None),
    ("Sal, tapapecho, té",         "insumo_variable", "diaria",  "Castro"),
    ("entrada",                    "insumo_variable", "diaria",  "Lovalledor"),
    ("Gas",                        "insumo_variable", "diaria",  "Gasco"),
    ("envases, bolsas",            "insumo_variable", "diaria",  "Kampito"),
    ("Poroto verde",               "insumo_variable", "diaria",  None),
    ("Toalla papel, servilletas",  "insumo_variable", "diaria",  None),
    ("Vasos, huevo en polvo",      "insumo_variable", "diaria",  None),
    ("Ketchup, mostaza",           "insumo_variable", "diaria",  "verde oliva"),
    ("Ají crema, salsa queso",     "insumo_variable", "diaria",  None),
    ("bbq, alitas de pollo",       "insumo_variable", "diaria",  None),
    ("tiritas de pollo",           "insumo_variable", "diaria",  None),
    ("nugget, aros de cebolla",    "insumo_variable", "diaria",  None),
    ("Fruna 500cc",                "insumo_variable", "diaria",  "Fruna"),
    ("Lata 350cc",                 "insumo_variable", "diaria",  "Fruna"),
    ("Botella 3000cc",             "insumo_variable", "diaria",  "Fruna"),
    # --- costos fijos / recurrentes (mensuales) ---
    ("Patata 50 cuotas",           "costo_fijo",      "mensual", None),
    ("Sueldo Chris",               "costo_fijo",      "mensual", None),
    ("Sueldo Yocy",                "costo_fijo",      "mensual", None),
    ("Dividendo Huechuraba",       "costo_fijo",      "mensual", None),
    ("Agua",                       "costo_fijo",      "mensual", None),
    ("Luz",                        "costo_fijo",      "mensual", None),
    ("Internet",                   "costo_fijo",      "mensual", None),
    ("Teléfono celular",           "costo_fijo",      "mensual", None),
    ("IVA",                        "costo_fijo",      "mensual", None),   # pago mensual al SII
    ("Patente",                    "costo_fijo",      "mensual", None),
    ("Bencina",                    "costo_fijo",      "mensual", None),
    ("previred.com",               "costo_fijo",      "mensual", None),
    ("contador",                   "costo_fijo",      "mensual", None),
    ("contribuciones Huechuraba",  "costo_fijo",      "mensual", None),
    # --- otros egresos (extraordinarios / capex) ---
    ("Reparaciones",               "otro_egreso",     "eventual", None),
    ("Compra de utensilios",       "otro_egreso",     "eventual", None),
]


def _ahora():
    return datetime.now().isoformat(timespec="seconds")


def _f(row):
    """sqlite3.Row -> dict (o None)."""
    return dict(row) if row is not None else None


def _iva_incluido(monto_con_iva):
    """IVA contenido en un monto que YA lo incluye (Chile: precio con IVA)."""
    return round((monto_con_iva or 0) * IVA_TASA / (1 + IVA_TASA))


class NucleoFinanzas:
    """Libro de caja montado sobre un NucleoPedidos ya construido.
    Reutiliza SU conexión y SU lock (una sola conexión = cero choques de
    escritura entre POS/WMS/Finanzas), igual que NucleoInventario."""

    def __init__(self, nucleo: NucleoPedidos):
        self.n = nucleo
        self._con = nucleo._con        # MISMA conexión SQLite que el POS
        self._lock = nucleo._lock      # MISMO RLock
        self._crear_tablas()
        self.asegurar_semilla()
        self.asegurar_permisos_admin()

    # ------------------------------------------------------------------
    # Esquema
    # ------------------------------------------------------------------
    def _crear_tablas(self):
        with self._lock:
            self._con.executescript("""
            CREATE TABLE IF NOT EXISTS categorias_gasto (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE,
                tipo TEXT NOT NULL,                 -- insumo_variable|costo_fijo|otro_egreso|pago_delivery
                frecuencia TEXT DEFAULT 'diaria',   -- diaria|mensual|eventual
                orden INTEGER DEFAULT 0,
                proveedor_sugerido TEXT,            -- ayuda de UI (texto libre)
                insumo_id INTEGER,                  -- link OPCIONAL al insumo del WMS
                activo INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS egresos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,                -- YYYY-MM-DD (día del gasto)
                categoria_id INTEGER NOT NULL,
                proveedor_id INTEGER,               -- NULL = comodín / sin proveedor
                monto INTEGER NOT NULL,
                con_factura INTEGER DEFAULT 0,      -- 0/1 (deducible / da crédito IVA)
                iva INTEGER,                        -- NULL = estimar (19/119) si con_factura
                recepcion_id INTEGER,               -- link a recepciones SII (escaneo)
                nota TEXT,
                origen TEXT DEFAULT 'manual',       -- manual|whatsapp|sii|import
                usuario TEXT, creado_en TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_egreso_fecha ON egresos(fecha);
            CREATE INDEX IF NOT EXISTS idx_egreso_cat   ON egresos(categoria_id);
            CREATE TABLE IF NOT EXISTS ingresos_manuales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                concepto TEXT DEFAULT 'boleta_manual',
                monto INTEGER NOT NULL,
                nota TEXT, origen TEXT DEFAULT 'manual', usuario TEXT, creado_en TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ingman_fecha ON ingresos_manuales(fecha);
            CREATE TABLE IF NOT EXISTS ventas_dia (
                fecha TEXT PRIMARY KEY,             -- snapshot de ventas por medio de pago
                efectivo INTEGER DEFAULT 0,
                transferencia INTEGER DEFAULT 0,
                pos INTEGER DEFAULT 0,
                origen TEXT DEFAULT 'import',       -- import|manual (el POS NO usa esta tabla)
                nota TEXT
            );
            """)
            self._con.commit()

    def asegurar_semilla(self):
        """Siembra las categorías de la planilla la primera vez (idempotente)."""
        with self._lock:
            n = self._con.execute("SELECT COUNT(*) c FROM categorias_gasto").fetchone()["c"]
            if n:
                return
            for i, (nombre, tipo, frec, prov) in enumerate(CATEGORIAS_SEMILLA):
                self._con.execute(
                    "INSERT OR IGNORE INTO categorias_gasto "
                    "(nombre, tipo, frecuencia, orden, proveedor_sugerido) VALUES (?,?,?,?,?)",
                    (nombre, tipo, frec, i, prov))
            self._con.commit()

    def asegurar_permisos_admin(self):
        """Da los permisos de finanzas a los perfiles Administrador (si el POS los tiene)."""
        try:
            with self._lock:
                filas = self._con.execute(
                    "SELECT id, permisos FROM perfiles").fetchall()
        except Exception:
            return  # el POS de esta base no tiene tabla perfiles (test aislado)
        import json as _json
        for pf in filas:
            try:
                permisos = _json.loads(pf["permisos"] or "[]")
            except Exception:
                continue
            # Administrador = tiene TODOS los permisos del POS; le sumamos los nuestros.
            if "gestionar_usuarios" in permisos:
                nuevos = [p for p in PERMISOS_FINANZAS if p not in permisos]
                if nuevos:
                    with self._lock:
                        self._con.execute("UPDATE perfiles SET permisos=? WHERE id=?",
                                          (_json.dumps(permisos + nuevos), pf["id"]))
                        self._con.commit()

    # ------------------------------------------------------------------
    # Categorías (catálogo maestro)
    # ------------------------------------------------------------------
    def listar_categorias(self, tipo=None, solo_activas=True):
        q = "SELECT * FROM categorias_gasto WHERE 1=1"
        args = []
        if tipo:
            q += " AND tipo=?"; args.append(tipo)
        if solo_activas:
            q += " AND activo=1"
        q += " ORDER BY orden, id"
        with self._lock:
            return [_f(r) for r in self._con.execute(q, args).fetchall()]

    def categoria_por_nombre(self, nombre):
        with self._lock:
            return _f(self._con.execute(
                "SELECT * FROM categorias_gasto WHERE nombre=? COLLATE NOCASE",
                (nombre,)).fetchone())

    def crear_categoria(self, nombre, tipo, frecuencia="diaria", proveedor_sugerido=None,
                        insumo_id=None):
        if tipo not in TIPOS_EGRESO:
            raise ValueError(f"tipo inválido: {tipo} (use {sorted(TIPOS_EGRESO)})")
        with self._lock:
            orden = (self._con.execute(
                "SELECT COALESCE(MAX(orden),0)+1 o FROM categorias_gasto").fetchone()["o"])
            cur = self._con.execute(
                "INSERT INTO categorias_gasto (nombre, tipo, frecuencia, orden, "
                "proveedor_sugerido, insumo_id) VALUES (?,?,?,?,?,?)",
                (nombre, tipo, frecuencia, orden, proveedor_sugerido, insumo_id))
            self._con.commit()
            return cur.lastrowid

    def _resolver_categoria(self, categoria):
        """Acepta id (int) o nombre (str) y devuelve la fila; error si no existe."""
        if isinstance(categoria, int):
            with self._lock:
                row = self._con.execute("SELECT * FROM categorias_gasto WHERE id=?",
                                        (categoria,)).fetchone()
        else:
            row = self.categoria_por_nombre(str(categoria))
        if not row:
            raise ValueError(f"categoría inexistente: {categoria!r}")
        return _f(row)

    # ------------------------------------------------------------------
    # Egresos (hechos diarios)
    # ------------------------------------------------------------------
    def registrar_egreso(self, fecha, categoria, monto, proveedor_id=None,
                         con_factura=False, iva=None, recepcion_id=None, nota=None,
                         origen="manual", usuario=None):
        """Registra un gasto de un día. `categoria` = id o nombre. `monto` en CLP.
        Si con_factura y no se pasa iva, se estima el IVA contenido (19/119)."""
        cat = self._resolver_categoria(categoria)
        monto = int(round(monto))
        if monto <= 0:
            raise ValueError("el monto del egreso debe ser > 0")
        con_factura = 1 if con_factura else 0
        if con_factura and iva is None:
            iva = _iva_incluido(monto)
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO egresos (fecha, categoria_id, proveedor_id, monto, con_factura, "
                "iva, recepcion_id, nota, origen, usuario, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (fecha, cat["id"], proveedor_id, monto, con_factura, iva, recepcion_id,
                 nota, origen, usuario, _ahora()))
            self._con.commit()
            return cur.lastrowid

    def listar_egresos(self, desde=None, hasta=None, categoria_id=None):
        q = ("SELECT e.*, c.nombre categoria, c.tipo FROM egresos e "
             "JOIN categorias_gasto c ON c.id=e.categoria_id WHERE 1=1")
        args = []
        if desde: q += " AND e.fecha>=?"; args.append(desde)
        if hasta: q += " AND e.fecha<=?"; args.append(hasta)
        if categoria_id: q += " AND e.categoria_id=?"; args.append(categoria_id)
        q += " ORDER BY e.fecha, e.id"
        with self._lock:
            return [_f(r) for r in self._con.execute(q, args).fetchall()]

    def eliminar_egreso(self, egreso_id):
        with self._lock:
            self._con.execute("DELETE FROM egresos WHERE id=?", (egreso_id,))
            self._con.commit()

    # ------------------------------------------------------------------
    # ENGANCHES (costuras con WMS y POS)
    # ------------------------------------------------------------------
    def egreso_desde_recepcion(self, recepcion_id, categoria, usuario=None, fecha=None):
        """Convierte una recepción SII (compra con factura) en un egreso contable con
        IVA crédito REAL. El MISMO escaneo que sumó stock alimenta la contabilidad
        (neto/IVA/proveedor/folio salen de la tabla `recepciones` del WMS). Idempotente
        por recepción: si ya existe un egreso ligado a esa recepción, no lo duplica."""
        with self._lock:
            try:
                rec = self._con.execute("SELECT * FROM recepciones WHERE id=?",
                                        (recepcion_id,)).fetchone()
            except Exception:
                raise ValueError("no existe la tabla recepciones (¿WMS no montado?)")
            ya = self._con.execute("SELECT id FROM egresos WHERE recepcion_id=?",
                                   (recepcion_id,)).fetchone()
        if ya:
            return ya["id"]                      # ya se contabilizó esa factura
        if not rec:
            raise ValueError(f"recepción inexistente: {recepcion_id}")
        rec = _f(rec)
        monto = rec.get("total") or ((rec.get("neto") or 0) + (rec.get("iva") or 0))
        if not monto:
            raise ValueError("la recepción no tiene monto (neto/total en 0)")
        fecha = fecha or (rec.get("fecha_doc") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
        nota = f"Recepción SII {rec.get('tipo_doc') or ''} folio {rec.get('folio') or ''}".strip()
        return self.registrar_egreso(
            fecha, categoria, monto, proveedor_id=rec.get("proveedor_id"),
            con_factura=True, iva=rec.get("iva"), recepcion_id=recepcion_id,
            nota=nota, origen="sii", usuario=usuario)

    def pago_delivery_sugerido(self, fecha):
        """Suma el delivery_cobro de los pedidos del POS del día (sugerencia editable
        para la línea 'Pago al delivery'). No incluye pedidos cancelados."""
        inf = self.n.ventas_periodo(fecha, fecha)
        return sum((p.get("delivery_cobro") or 0) for p in inf.get("pedidos", [])
                   if p.get("estado") != "cancelado")

    def registrar_pago_delivery(self, fecha, monto=None, usuario=None):
        """Registra el egreso 'Pago al delivery' del día. Si monto es None usa la
        suma de delivery_cobro del POS (auto). Reemplaza el del día si ya existía."""
        cat = self.categoria_por_nombre("Pago al delivery")
        with self._lock:
            self._con.execute(
                "DELETE FROM egresos WHERE fecha=? AND categoria_id=?", (fecha, cat["id"]))
            self._con.commit()
        monto = self.pago_delivery_sugerido(fecha) if monto is None else monto
        if not monto:
            return None
        return self.registrar_egreso(fecha, cat["id"], monto, nota="auto POS delivery_cobro",
                                     origen="pos", usuario=usuario)

    def registrar_gasto_texto(self, texto, fecha=None, usuario=None, origen="whatsapp"):
        """Parsea un mensaje libre del jefe y, si entiende monto+categoría, registra
        el egreso. Devuelve el resultado del parseo + si quedó registrado. Es la
        única llamada que el bot (modo jefe) necesita para capturar un gasto."""
        cats = self.listar_categorias()
        try:
            with self._lock:
                provs = [dict(r) for r in self._con.execute(
                    "SELECT id, nombre FROM proveedores").fetchall()]
        except Exception:
            provs = []                          # WMS no montado en esta base
        p = parsear_gasto(texto, cats, provs, fecha)
        if not p["ok"]:
            return {"registrado": False, **p}
        egreso_id = self.registrar_egreso(
            p["fecha"], p["categoria_id"], p["monto"], proveedor_id=p["proveedor_id"],
            con_factura=bool(p["con_factura"]), nota=f"WhatsApp: {texto.strip()}",
            origen=origen, usuario=usuario)
        return {"registrado": True, "egreso_id": egreso_id, **p}

    # ------------------------------------------------------------------
    # Ingresos manuales (Boleta Manual) y snapshot de ventas
    # ------------------------------------------------------------------
    def registrar_ingreso_manual(self, fecha, monto, concepto="boleta_manual",
                                 nota=None, origen="manual", usuario=None):
        monto = int(round(monto))
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO ingresos_manuales (fecha, concepto, monto, nota, origen, usuario, creado_en) "
                "VALUES (?,?,?,?,?,?,?)", (fecha, concepto, monto, nota, origen, usuario, _ahora()))
            self._con.commit()
            return cur.lastrowid

    def boleta_manual_dia(self, fecha):
        with self._lock:
            r = self._con.execute(
                "SELECT COALESCE(SUM(monto),0) s FROM ingresos_manuales "
                "WHERE fecha=? AND concepto='boleta_manual'", (fecha,)).fetchone()
        return r["s"] or 0

    def set_ventas_dia(self, fecha, efectivo=0, transferencia=0, pos=0,
                       origen="import", nota=None):
        """Fija/actualiza el snapshot de ventas de un día (para histórico o días sin
        POS). Para el mes en curso NO se usa: los ingresos se leen del POS en vivo."""
        with self._lock:
            self._con.execute(
                "INSERT INTO ventas_dia (fecha, efectivo, transferencia, pos, origen, nota) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(fecha) DO UPDATE SET "
                "efectivo=excluded.efectivo, transferencia=excluded.transferencia, "
                "pos=excluded.pos, origen=excluded.origen, nota=excluded.nota",
                (fecha, int(efectivo), int(transferencia), int(pos), origen, nota))
            self._con.commit()

    # ------------------------------------------------------------------
    # INGRESOS del día: snapshot (histórico) o POS en vivo
    # ------------------------------------------------------------------
    def ingresos_dia(self, fecha):
        """Ventas por medio de pago de un día. Si hay snapshot (histórico importado)
        lo usa; si no, LEE el POS (ventas_periodo). 'pos' = método tarjeta."""
        with self._lock:
            snap = self._con.execute("SELECT * FROM ventas_dia WHERE fecha=?",
                                     (fecha,)).fetchone()
        if snap:
            s = _f(snap)
            return {"efectivo": s["efectivo"], "transferencia": s["transferencia"],
                    "pos": s["pos"], "origen": s["origen"]}
        # Sin snapshot -> POS en vivo
        inf = self.n.ventas_periodo(fecha, fecha)
        pm = inf.get("por_metodo", {})
        out = {"efectivo": 0, "transferencia": 0, "pos": 0, "origen": "pos"}
        for metodo, monto in pm.items():
            destino = _MAP_POS.get(str(metodo).lower())
            if destino:
                out[destino] += monto
        return out

    # ------------------------------------------------------------------
    # RESÚMENES (día y mes) — las reglas Real/Teórico/flujo/IVA
    # ------------------------------------------------------------------
    def _agrupar_egresos(self, egresos):
        """Suma una lista de egresos por tipo + IVA crédito + con/sin factura."""
        por_tipo = {"insumo_variable": 0, "costo_fijo": 0, "otro_egreso": 0, "pago_delivery": 0}
        iva_credito = con_factura = sin_factura = 0
        for e in egresos:
            por_tipo[e["tipo"]] = por_tipo.get(e["tipo"], 0) + e["monto"]
            if e["con_factura"]:
                con_factura += e["monto"]
                iva_credito += e["iva"] if e["iva"] is not None else _iva_incluido(e["monto"])
            else:
                sin_factura += e["monto"]
        return por_tipo, iva_credito, con_factura, sin_factura

    def resumen_dia(self, fecha):
        """Cierre de un día: ingresos (Real/Teórico) − egresos = flujo (Real/Teórico)."""
        ing = self.ingresos_dia(fecha)
        efectivo, transferencia, pos = ing["efectivo"], ing["transferencia"], ing["pos"]
        boleta_manual = self.boleta_manual_dia(fecha)
        boleta_efectiva = boleta_manual - transferencia          # regla de la planilla
        ingreso_real = efectivo + transferencia + pos
        ingreso_teorico = pos + transferencia + boleta_efectiva

        egresos = self.listar_egresos(fecha, fecha)
        por_tipo, iva_credito, con_factura, sin_factura = self._agrupar_egresos(egresos)
        total_egresos = por_tipo["insumo_variable"] + por_tipo["costo_fijo"]
        otros_egresos = por_tipo["otro_egreso"]
        pago_delivery = por_tipo["pago_delivery"]

        flujo_real = ingreso_real - total_egresos - otros_egresos - pago_delivery
        flujo_teorico = ingreso_teorico - total_egresos - otros_egresos - pago_delivery

        iva_debito = _iva_incluido(ingreso_teorico if ingreso_teorico > 0 else 0)
        return {
            "fecha": fecha, "origen_ingresos": ing["origen"],
            "efectivo": efectivo, "transferencia": transferencia, "pos": pos,
            "pago_delivery": pago_delivery,
            "boleta_manual": boleta_manual, "boleta_efectiva": boleta_efectiva,
            "ingreso_real": ingreso_real, "ingreso_teorico": ingreso_teorico,
            "insumos_variables": por_tipo["insumo_variable"],
            "costos_fijos": por_tipo["costo_fijo"],
            "total_egresos": total_egresos, "otros_egresos": otros_egresos,
            "flujo_real": flujo_real, "flujo_teorico": flujo_teorico,
            "compras_con_factura": con_factura, "compras_sin_factura": sin_factura,
            "iva_debito": iva_debito, "iva_credito": iva_credito,
            "iva_neto": iva_debito - iva_credito,
            "no_documentado": ingreso_real - ingreso_teorico,
        }

    def resumen_mes(self, anio, mes):
        """Consolidado mensual: agrega los días del mes. Ingresos por medio de pago,
        egresos por categoría, Real/Teórico, flujos e IVA neto (débito − crédito)."""
        desde = f"{anio:04d}-{mes:02d}-01"
        hasta = f"{anio:04d}-{mes:02d}-31"
        # Ingresos: días con snapshot + días con POS. Recorremos los días 1..31.
        import calendar
        ndias = calendar.monthrange(anio, mes)[1]
        efectivo = transferencia = pos = boleta_manual = 0
        por_dia = []
        for d in range(1, ndias + 1):
            fecha = f"{anio:04d}-{mes:02d}-{d:02d}"
            rd = self.resumen_dia(fecha)
            if (rd["ingreso_real"] or rd["total_egresos"] or rd["otros_egresos"]
                    or rd["pago_delivery"] or rd["boleta_manual"]):
                por_dia.append(rd)
            efectivo += rd["efectivo"]; transferencia += rd["transferencia"]; pos += rd["pos"]
            boleta_manual += rd["boleta_manual"]
        boleta_efectiva = boleta_manual - transferencia
        ingreso_real = efectivo + transferencia + pos
        ingreso_teorico = pos + transferencia + boleta_efectiva

        egresos = self.listar_egresos(desde, hasta)
        por_tipo, iva_credito, con_factura, sin_factura = self._agrupar_egresos(egresos)
        total_egresos = por_tipo["insumo_variable"] + por_tipo["costo_fijo"]
        otros_egresos = por_tipo["otro_egreso"]
        pago_delivery = por_tipo["pago_delivery"]
        # Egresos por categoría (para el consolidado del contador).
        por_categoria = {}
        for e in egresos:
            por_categoria[e["categoria"]] = por_categoria.get(e["categoria"], 0) + e["monto"]
        flujo_real = ingreso_real - total_egresos - otros_egresos - pago_delivery
        flujo_teorico = ingreso_teorico - total_egresos - otros_egresos - pago_delivery
        iva_debito = _iva_incluido(ingreso_teorico if ingreso_teorico > 0 else 0)
        return {
            "anio": anio, "mes": mes, "n_dias_con_datos": len(por_dia),
            "efectivo": efectivo, "transferencia": transferencia, "pos": pos,
            "pago_delivery": pago_delivery,
            "boleta_manual": boleta_manual, "boleta_efectiva": boleta_efectiva,
            "ingreso_real": ingreso_real, "ingreso_teorico": ingreso_teorico,
            "insumos_variables": por_tipo["insumo_variable"],
            "costos_fijos": por_tipo["costo_fijo"],
            "total_egresos": total_egresos, "otros_egresos": otros_egresos,
            "flujo_real": flujo_real, "flujo_teorico": flujo_teorico,
            "compras_con_factura": con_factura, "compras_sin_factura": sin_factura,
            "iva_debito": iva_debito, "iva_credito": iva_credito,
            "iva_neto": iva_debito - iva_credito,
            "no_documentado": ingreso_real - ingreso_teorico,
            "por_categoria": por_categoria, "dias": por_dia,
        }


# ===========================================================================
# Captura de gasto por TEXTO (bot de WhatsApp, modo jefe)
# ===========================================================================
def _norm(s):
    """minúsculas sin tildes, para matchear proveedor/categoría de forma tolerante."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.lower().strip()


def _monto_de_texto(texto):
    """El monto de un mensaje: el mayor número (>=100) ignorando separador de miles."""
    montos = []
    for tok in re.findall(r"\d[\d.\,]*", texto):
        n = int(re.sub(r"\D", "", tok) or 0)
        if n >= 100:
            montos.append(n)
    return max(montos) if montos else 0


def parsear_gasto(texto, categorias, proveedores=None, fecha=None):
    """Interpreta un mensaje libre del jefe tipo 'Ariztía 39300 vienesas s/factura'
    y devuelve un dict estructurado (monto, categoría, proveedor, con_factura, fecha)
    + un eco de confirmación + qué faltó. NO escribe nada: es puro parseo (testeable).

    Reglas de factura: 'sin/ s/ factura' -> False; 'con/ c/ factura' o solo
    'factura' -> True; si no se menciona -> None (se asume sin factura, marcado)."""
    fecha = fecha or datetime.now().strftime("%Y-%m-%d")
    t = _norm(texto)
    palabras = set(re.split(r"[^a-z0-9]+", t))
    monto = _monto_de_texto(texto)

    if re.search(r"\bs/?\s*factura\b|\bsin\s+factura\b|\bsf\b|\bs/f\b", t):
        con_factura = False
    elif re.search(r"\bc/?\s*factura\b|\bcon\s+factura\b|\bcf\b|\bc/f\b|\bfactura\b|\bboleta\b", t):
        con_factura = True
    else:
        con_factura = None

    # Categoría: la que más palabras-clave (>=3 letras) comparte con el mensaje.
    mejor_cat, mejor = None, 0
    for cat in categorias:
        if cat["tipo"] == "pago_delivery":
            continue  # el delivery tiene su flujo propio (auto desde el POS)
        kws = [w for w in re.split(r"[^a-z]+", _norm(cat["nombre"])) if len(w) >= 3]
        score = sum(1 for w in kws if w in palabras)
        if score > mejor:
            mejor_cat, mejor = cat, score

    # Proveedor: match del nombre (maestro + sugeridos) como palabra del mensaje.
    nombres = [p["nombre"] for p in (proveedores or []) if p.get("nombre")]
    nombres += [c["proveedor_sugerido"] for c in categorias if c.get("proveedor_sugerido")]
    prov_nombre, prov_id = None, None
    for nom in sorted(set(nombres), key=len, reverse=True):
        tn = _norm(nom).split()
        if tn and all(w in palabras for w in tn):
            prov_nombre = nom
            for p in (proveedores or []):
                if _norm(p.get("nombre")) == _norm(nom):
                    prov_id = p.get("id")
            break

    faltantes = []
    if not monto:
        faltantes.append("monto")
    if not mejor_cat:
        faltantes.append("categoría")

    if faltantes:
        eco = ("No pude entender " + " ni ".join(faltantes) +
               f". Escribe algo como: 'Ariztía 39300 vienesas s/factura'.")
    else:
        fac = ("con factura" if con_factura else
               "sin factura" if con_factura is False else "sin factura (asumido)")
        prov_txt = f" ({prov_nombre})" if prov_nombre else ""
        eco = (f"Anotado: {mejor_cat['nombre']} ${monto:,} {fac}{prov_txt}, {fecha}. "
               "Responde 'ok' para confirmar o corrige.")

    return {
        "monto": monto,
        "categoria": mejor_cat["nombre"] if mejor_cat else None,
        "categoria_id": mejor_cat["id"] if mejor_cat else None,
        "proveedor": prov_nombre, "proveedor_id": prov_id,
        "con_factura": con_factura, "fecha": fecha,
        "faltantes": faltantes, "ok": not faltantes, "eco": eco,
    }


# ===========================================================================
# AUTO-TEST — base temporal, NO toca la real. `python wms/finanzas.py`
# ===========================================================================
def _test():
    import tempfile
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # consola Windows con tildes/signos
    except Exception:
        pass
    fails = [0]

    def chk(msg, cond):
        estado = "OK" if cond else "FALLA"
        if not cond:
            fails[0] += 1
        print(f"  [{estado}] {msg}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "fin_test.db"
        n = NucleoPedidos(db_path=db)
        fin = NucleoFinanzas(n)

        print("== semilla de categorías (planilla real) ==")
        cats = fin.listar_categorias()
        chk(f"se sembraron {len(cats)} categorías (esperado {len(CATEGORIAS_SEMILLA)})",
            len(cats) == len(CATEGORIAS_SEMILLA))
        chk("Churrasco es insumo_variable con proveedor sugerido Germán",
            (fin.categoria_por_nombre("Churrasco") or {}).get("tipo") == "insumo_variable"
            and fin.categoria_por_nombre("Churrasco")["proveedor_sugerido"] == "Germán")
        chk("Sueldo Chris es costo_fijo mensual",
            (fin.categoria_por_nombre("Sueldo Chris") or {}).get("frecuencia") == "mensual")
        chk("Pago al delivery es tipo pago_delivery",
            (fin.categoria_por_nombre("Pago al delivery") or {}).get("tipo") == "pago_delivery")

        print("== motor determinista con snapshot de ventas (día histórico) ==")
        F = "2026-05-10"
        fin.set_ventas_dia(F, efectivo=100000, transferencia=30000, pos=50000)
        fin.registrar_ingreso_manual(F, 40000)                       # boleta manual
        fin.registrar_egreso(F, "Pan", 20000)                        # insumo, sin factura
        fin.registrar_egreso(F, "Churrasco", 50000, con_factura=True)# insumo, con factura
        fin.registrar_egreso(F, "Luz", 30000)                        # costo fijo (ese día)
        fin.registrar_egreso(F, "Reparaciones", 15000)               # otro egreso
        fin.registrar_egreso(F, "Pago al delivery", 8000)            # pago delivery (egreso)
        r = fin.resumen_dia(F)
        chk("boleta_efectiva = 40000 − 30000 = 10000", r["boleta_efectiva"] == 10000)
        chk("ingreso_real = 100k+30k+50k = 180000", r["ingreso_real"] == 180000)
        chk("ingreso_teorico = 50k+30k+10k = 90000", r["ingreso_teorico"] == 90000)
        chk("insumos_variables = 20k+50k = 70000", r["insumos_variables"] == 70000)
        chk("costos_fijos = 30000", r["costos_fijos"] == 30000)
        chk("total_egresos = 100000", r["total_egresos"] == 100000)
        chk("otros_egresos = 15000", r["otros_egresos"] == 15000)
        chk("pago_delivery = 8000", r["pago_delivery"] == 8000)
        chk("flujo_real = 180k−100k−15k−8k = 57000", r["flujo_real"] == 57000)
        chk("flujo_teorico = 90k−100k−15k−8k = -33000", r["flujo_teorico"] == -33000)
        chk("no_documentado = real − teorico = 90000", r["no_documentado"] == 90000)
        chk("compras_con_factura = 50000", r["compras_con_factura"] == 50000)
        chk("compras_sin_factura = 20k+30k+15k+8k = 73000", r["compras_sin_factura"] == 73000)
        iva_c = _iva_incluido(50000)
        chk(f"iva_credito estimado del churrasco con factura = {iva_c}", r["iva_credito"] == iva_c)
        chk("iva_debito = 19/119 del teorico (90000)", r["iva_debito"] == _iva_incluido(90000))
        chk("iva_neto = debito − credito", r["iva_neto"] == _iva_incluido(90000) - iva_c)

        print("== enganche con el POS: ingresos leídos en vivo (sin snapshot) ==")
        hoy = datetime.now().strftime("%Y-%m-%d")
        pe = n.crear(canal="cajero", cliente="Test", modalidad="retiro", forma_pago="efectivo",
                     items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}])
        total_pe = pe["total"]
        ing = fin.ingresos_dia(hoy)
        chk("ingresos_dia(hoy) viene del POS (origen=pos)", ing["origen"] == "pos")
        chk(f"efectivo del POS = total del pedido ({total_pe}) y > 0",
            ing["efectivo"] == total_pe and total_pe > 0)
        rh = fin.resumen_dia(hoy)
        chk("ingreso_real de hoy = venta del POS", rh["ingreso_real"] == total_pe)

        print("== enganche SII: una recepción con factura se vuelve egreso con IVA real ==")
        from inventario import NucleoInventario
        inv = NucleoInventario(n)                 # monta la tabla recepciones del WMS
        FS = "2026-06-12"                          # junio: no contamina los totales de mayo
        rec_id = inv.registrar_recepcion(proveedor_id=None, tipo_doc="factura", folio="A-123",
                                         neto=100000, iva=19000, total=119000, fecha_doc=FS)
        eg_id = fin.egreso_desde_recepcion(rec_id, "Churrasco")
        egs = fin.listar_egresos(FS, FS)
        eg = next((e for e in egs if e["id"] == eg_id), None)
        chk("el egreso desde recepción existe", eg is not None)
        chk("monto = total de la factura (119000)", eg and eg["monto"] == 119000)
        chk("IVA = el REAL de la factura (19000), no estimado", eg and eg["iva"] == 19000)
        chk("queda con_factura=1 y ligado a la recepción",
            eg and eg["con_factura"] == 1 and eg["recepcion_id"] == rec_id)
        chk("origen='sii'", eg and eg["origen"] == "sii")
        chk("idempotente: reprocesar la misma recepción NO duplica",
            fin.egreso_desde_recepcion(rec_id, "Churrasco") == eg_id
            and len(fin.listar_egresos(FS, FS)) == 1)
        chk("ese día iva_credito = 19000 (real)", fin.resumen_dia(FS)["iva_credito"] == 19000)

        print("== pago al delivery: auto desde delivery_cobro del POS ==")
        n.crear(canal="cajero", cliente="D1", modalidad="delivery", forma_pago="efectivo",
                direccion="Calle 1", delivery_cobro=1500,
                items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}])
        n.crear(canal="cajero", cliente="D2", modalidad="delivery", forma_pago="efectivo",
                direccion="Calle 2", delivery_cobro=2000,
                items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}])
        chk("pago_delivery_sugerido(hoy) = 1500+2000 = 3500",
            fin.pago_delivery_sugerido(hoy) == 3500)
        fin.registrar_pago_delivery(hoy)
        chk("registrar_pago_delivery deja el egreso en 3500",
            fin.resumen_dia(hoy)["pago_delivery"] == 3500)

        print("== captura de gasto por WhatsApp (parser del modo jefe) ==")
        cats = fin.listar_categorias()
        p1 = parsear_gasto("Ariztía 39300 vienesas s/factura", cats)
        chk("monto = 39300", p1["monto"] == 39300)
        chk("categoría = Vienesas", p1["categoria"] == "Vienesas")
        chk("con_factura = False (s/factura)", p1["con_factura"] is False)
        chk("proveedor = Ariztia (match con tilde)", p1["proveedor"] == "Ariztia")
        p2 = parsear_gasto("gas 45.000 con factura", cats)
        chk("miles con punto: 45.000 -> 45000", p2["monto"] == 45000)
        chk("categoría = Gas", p2["categoria"] == "Gas")
        chk("con_factura = True (con factura)", p2["con_factura"] is True)
        p3 = parsear_gasto("pan 12000", cats)
        chk("sin mención de factura -> None (asumido)", p3["con_factura"] is None)
        chk("categoría = Pan", p3["categoria"] == "Pan")
        p4 = parsear_gasto("hola no se que es esto", cats)
        chk("mensaje sin monto/categoría -> no ok", not p4["ok"] and "monto" in p4["faltantes"])
        # registrar_gasto_texto: parsea Y registra en un solo paso
        FG = "2026-08-03"
        res = fin.registrar_gasto_texto("Jargui 25000 pan c/factura", fecha=FG)
        chk("registrar_gasto_texto dejó el egreso", res["registrado"] and res["egreso_id"])
        eg = fin.listar_egresos(FG, FG)
        chk("quedó 1 egreso de Pan 25000 con factura, origen whatsapp",
            len(eg) == 1 and eg[0]["categoria"] == "Pan" and eg[0]["monto"] == 25000
            and eg[0]["con_factura"] == 1 and eg[0]["origen"] == "whatsapp")

        print("== resumen mensual agrega los días ==")
        rm = fin.resumen_mes(2026, 5)
        chk("mes de mayo toma el día 10 cargado", rm["n_dias_con_datos"] == 1)
        chk("ingreso_real mensual = 180000", rm["ingreso_real"] == 180000)
        chk("flujo_real mensual = 57000", rm["flujo_real"] == 57000)
        chk("por_categoria incluye Churrasco=50000", rm["por_categoria"].get("Churrasco") == 50000)
        n._con.close()   # libera el lock del archivo para que Windows borre el tempdir

    print("\n" + ("TODO OK" if fails[0] == 0 else f"{fails[0]} CHEQUEO(S) FALLARON"))
    return fails[0] == 0


if __name__ == "__main__":
    ok = _test()
    sys.exit(0 if ok else 1)

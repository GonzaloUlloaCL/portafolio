#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
servidor.py — Servidor del WMS de "Donde el Patata".
====================================================
FastAPI que monta el WMS sobre el POS: reusa el MISMO núcleo (`pos/nucleo.py`),
su LOGIN y sus PERMISOS (token `X-Sesion`, fail-closed), y el módulo de
inventario (`wms/inventario.py`). Sirve la UI móvil (`wms.html`) y la reportería
con gráficos vendorizados localmente (ECharts, offline).

Correr (demo, base propia, NO toca pedidos.db real):
    python wms/servidor.py --open
Smoke test sin navegador:
    python wms/servidor.py --test

En producción (paso final, controlado) el mismo router se monta en el server del
POS apuntando a `pos/pedidos.db`, y el descuento se engancha en `transicionar`.
"""

import argparse
import sys
import webbrowser
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from inventario import (NucleoInventario, NucleoPedidos, PERMISOS_WMS,   # noqa: E402
                        DISPOSICIONES_QUE_EXIGEN_BAJA)
from finanzas import NucleoFinanzas, PERMISOS_FINANZAS                 # noqa: E402
import sii                                                             # noqa: E402
from nucleo import LoginBloqueado                                      # noqa: E402

HTML = AQUI / "wms.html"
STATIC = AQUI / "static"
DB_DEMO = AQUI / "wms_demo.db"

# Se inicializan en arrancar()
NUC: Optional[NucleoPedidos] = None
INV: Optional[NucleoInventario] = None
FIN: Optional[NucleoFinanzas] = None

app = FastAPI(title="WMS Patata")


# ----------------------------------------------------------------------
# Seguridad: reusa el login/permisos del POS (fail-closed)
# ----------------------------------------------------------------------
def usuario_actual(request: Request):
    return NUC.usuario_por_sesion(request.headers.get("X-Sesion"))


def exigir_login(request: Request):
    u = usuario_actual(request)
    if not u:
        raise HTTPException(401, "Identifícate para continuar")
    return u


def exigir(request: Request, permiso: str):
    u = exigir_login(request)
    permisos = u.get("permisos") or []
    if not (u.get("es_admin") or permiso in permisos):
        raise HTTPException(403, f"Te falta el permiso: {permiso}")
    return u


def _nombre(u):
    return (u or {}).get("nombre") or "(sin identificar)"


# ----------------------------------------------------------------------
# Modelos de entrada
# ----------------------------------------------------------------------
class LoginIn(BaseModel):
    identificador: str
    pin: str


class ProveedorIn(BaseModel):
    nombre: str
    rubro: Optional[str] = None
    telefono: Optional[str] = None
    rut: Optional[str] = None
    condiciones_pago: Optional[str] = None


class InsumoIn(BaseModel):
    nombre: str
    categoria: Optional[str] = None
    unidad_compra: Optional[str] = None
    unidad_uso: Optional[str] = None
    factor_conversion: Optional[float] = 1
    costo_unitario: Optional[float] = 0
    proveedor_id: Optional[int] = None
    ubicacion_id: Optional[int] = None
    stock_min: Optional[float] = 0
    stock_ideal: Optional[float] = 0
    control_lote: Optional[bool] = False
    vida_util_dias: Optional[int] = None
    lead_time_dias: Optional[float] = None
    dias_seguridad: Optional[float] = None
    codigo: Optional[str] = None


class SemaforoIn(BaseModel):
    modo: Optional[str] = None
    stock_min: Optional[float] = None
    stock_ideal: Optional[float] = None
    lead_time_dias: Optional[float] = None
    dias_seguridad: Optional[float] = None


class RecetaIn(BaseModel):
    categoria: str
    producto: str
    insumo_id: int
    cantidad_neta: float
    merma_pct: Optional[float] = 0
    variante: Optional[str] = ""


class CapturaIn(BaseModel):
    texto: str


class LineaRecepcion(BaseModel):
    insumo_id: int
    cantidad_compra: float
    lote: Optional[str] = None
    vencimiento: Optional[str] = None


class RecepcionIn(BaseModel):
    ted_texto: Optional[str] = None
    proveedor_id: Optional[int] = None
    lineas: List[LineaRecepcion] = []


class DisponerIn(BaseModel):
    disposicion: str
    precio_recuperado: Optional[float] = 0
    motivo: Optional[str] = None


class AjusteIn(BaseModel):
    insumo_id: int
    cantidad_uso: float
    motivo: Optional[str] = "ajuste"


class SimularIn(BaseModel):
    categoria: Optional[str] = "Churrascos"
    producto: Optional[str] = "Churrasco Italiano"
    cantidad: Optional[int] = 1


class EgresoIn(BaseModel):
    fecha: str
    categoria_id: int
    monto: int
    proveedor_id: Optional[int] = None
    con_factura: Optional[bool] = False
    iva: Optional[int] = None
    nota: Optional[str] = None


class IngresoManualIn(BaseModel):
    fecha: str
    monto: int
    nota: Optional[str] = None


class PagoDeliveryIn(BaseModel):
    fecha: str
    monto: Optional[int] = None      # None = auto desde delivery_cobro del POS


class GastoTextoIn(BaseModel):
    texto: str
    fecha: Optional[str] = None


# ----------------------------------------------------------------------
# UI + estáticos
# ----------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return HTML.read_text(encoding="utf-8")


@app.get("/api/salud")
def salud():
    return {"ok": True, "servicio": "WMS Patata", "permisos_wms": PERMISOS_WMS}


# ----------------------------------------------------------------------
# Login
# ----------------------------------------------------------------------
@app.post("/api/login")
def login(body: LoginIn):
    try:
        u = NUC.autenticar(body.identificador, body.pin)
    except LoginBloqueado as e:
        raise HTTPException(429, str(e), headers={"Retry-After": str(e.segundos)})
    if not u:
        raise HTTPException(401, "Usuario o PIN incorrecto")
    return {"ok": True, "sesion": NUC.crear_sesion(u["id"]), "usuario": {
        "id": u.get("id"), "nombre": u.get("nombre"),
        "es_admin": u.get("es_admin"), "permisos": u.get("permisos") or []}}


# ----------------------------------------------------------------------
# Catálogo (lectura: requiere estar logueado)
# ----------------------------------------------------------------------
@app.get("/api/insumos")
def insumos(request: Request):
    exigir_login(request)
    return INV.listar_insumos()


@app.get("/api/proveedores")
def proveedores(request: Request):
    exigir_login(request)
    return INV.listar_proveedores()


@app.get("/api/ubicaciones")
def ubicaciones(request: Request):
    exigir_login(request)
    return INV.listar_ubicaciones()


@app.get("/api/menu-productos")
def menu_productos(request: Request):
    exigir_login(request)
    out = []
    for cat in NUC.tarificador.menu.get("categorias", []):
        for p in cat.get("productos", []):
            out.append({"categoria": cat["nombre"], "producto": p["nombre"]})
    return out


@app.get("/api/recetas")
def recetas(request: Request, categoria: str, producto: str):
    exigir_login(request)
    return INV.receta_de(categoria, producto)


# ----------------------------------------------------------------------
# Escritura de maestros (requiere permiso)
# ----------------------------------------------------------------------
@app.post("/api/proveedores")
def crear_proveedor(request: Request, body: ProveedorIn):
    exigir(request, "gestionar_insumos")
    pid = INV.agregar_proveedor(**body.model_dump())
    return {"ok": True, "id": pid}


@app.post("/api/insumos")
def crear_insumo(request: Request, body: InsumoIn):
    exigir(request, "gestionar_insumos")
    datos = body.model_dump()
    nombre = datos.pop("nombre")
    iid = INV.agregar_insumo(nombre, **datos)
    return {"ok": True, "id": iid}


@app.put("/api/insumos/{insumo_id}")
def editar_insumo(request: Request, insumo_id: int, body: InsumoIn):
    """Corregir un insumo mal cargado sin tener que borrarlo y rehacerlo."""
    exigir(request, "gestionar_insumos")
    datos = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"ok": True, "insumo": INV.editar_insumo(insumo_id, **datos)}


@app.delete("/api/insumos/{insumo_id}")
def eliminar_insumo(request: Request, insumo_id: int):
    """Borra si no tiene historia; si ya tiene movimientos o recetas lo DESACTIVA
    (borrarlo dejaria huerfano el stock historico)."""
    exigir(request, "gestionar_insumos")
    return INV.eliminar_insumo(insumo_id)


@app.post("/api/sembrar-ejemplo")
def sembrar_ejemplo(request: Request):
    """Carga el set de EJEMPLO de Patata (pan/tomate/palta/mayo + receta de Churrasco
    Italiano) para que el usuario vea el WMS funcionando y aprenda a usar recetas.
    IDEMPOTENTE: si ya hay insumos cargados NO hace nada (no duplica ni pisa datos
    reales del local). Requiere el mismo permiso que crear un insumo."""
    exigir(request, "gestionar_insumos")
    if INV.listar_insumos():
        return {"ok": False, "motivo": "ya_hay_insumos"}
    r = INV.sembrar_demo()
    return {"ok": True, "insumos": len(r.get("insumos", []))}


@app.post("/api/insumos/{insumo_id}/semaforo")
def set_semaforo(request: Request, insumo_id: int, body: SemaforoIn):
    exigir(request, "gestionar_insumos")
    INV.configurar_semaforo(insumo_id, **body.model_dump())
    return {"ok": True, "insumo": INV.evaluar_insumo(INV.obtener_insumo(insumo_id))}


@app.post("/api/recetas")
def crear_receta(request: Request, body: RecetaIn):
    exigir(request, "gestionar_recetas")
    INV.definir_receta(body.categoria, body.producto, body.insumo_id,
                       body.cantidad_neta, body.merma_pct or 0, body.variante or "")
    return {"ok": True}


@app.delete("/api/recetas")
def borrar_receta(request: Request, categoria: str, producto: str,
                  insumo_id: int, variante: str = ""):
    """Quita un insumo de una receta. Editar una línea existente no pasa por acá:
    se re-graba con POST /api/recetas (definir_receta hace upsert)."""
    exigir(request, "gestionar_recetas")
    return INV.eliminar_receta(categoria, producto, insumo_id, variante)


# ----------------------------------------------------------------------
# Recepción de mercadería (cámara + SII)
# ----------------------------------------------------------------------
@app.post("/api/captura")
def captura(request: Request, body: CapturaIn):
    exigir(request, "recibir_mercaderia")
    return sii.leer_captura(body.texto)


@app.post("/api/recepcion")
def recepcion(request: Request, body: RecepcionIn):
    u = exigir(request, "recibir_mercaderia")
    datos_doc = {}
    if body.ted_texto:
        p = sii.parsear_ted(body.ted_texto)
        if p.get("ok"):
            datos_doc = {"tipo_doc": p.get("tipo_doc"), "folio": p.get("folio"),
                         "rut_emisor": p.get("rut_emisor"), "neto": p.get("neto"),
                         "iva": p.get("iva"), "total": p.get("total"),
                         "fecha_doc": p.get("fecha_doc"), "ted_raw": p.get("ted_raw")}
    rec_id = INV.registrar_recepcion(proveedor_id=body.proveedor_id, usuario=_nombre(u), **datos_doc)
    ingresados = []
    for ln in body.lineas:
        INV.recibir(ln.insumo_id, ln.cantidad_compra, usuario=_nombre(u), recepcion_id=rec_id,
                    lote=ln.lote, vencimiento=ln.vencimiento)
        ingresados.append({"insumo_id": ln.insumo_id, "stock": INV.stock_actual(ln.insumo_id)})
    return {"ok": True, "recepcion_id": rec_id, "ingresados": ingresados}


@app.get("/api/recepciones")
def recepciones(request: Request):
    exigir_login(request)
    return INV.listar_recepciones()


# ----------------------------------------------------------------------
# Ajustes e inventario
# ----------------------------------------------------------------------
@app.post("/api/ajuste")
def ajuste(request: Request, body: AjusteIn):
    u = exigir(request, "ajustar_inventario")
    INV.ajustar(body.insumo_id, body.cantidad_uso, usuario=_nombre(u), motivo=body.motivo or "ajuste")
    return {"ok": True, "stock": INV.stock_actual(body.insumo_id)}


# ----------------------------------------------------------------------
# Productos terminados (limbo de lo confeccionado no vendido)
# ----------------------------------------------------------------------
@app.get("/api/terminados")
def terminados(request: Request, estado: Optional[str] = None):
    exigir_login(request)
    return INV.listar_terminados(estado)


class ConteoIn(BaseModel):
    insumo_id: int
    contado: float


class ResolverConteoIn(BaseModel):
    ajustar: bool = True
    motivo: str = ""


@app.get("/api/conteo/hoy")
def conteo_hoy(request: Request):
    """§2: qué toca contar hoy. Devuelve nombre y unidad — **nunca la cantidad
    esperada**. El conteo es CIEGO: si el que cuenta ve el número del sistema,
    escribe ese y el control deja de servir."""
    exigir_login(request)
    return {"insumos": INV.insumos_a_contar()}


@app.post("/api/conteo")
def registrar_conteo(request: Request, body: ConteoIn):
    """Recibe el conteo físico. TOLERANCIA 0. La respuesta es deliberadamente pobre:
    solo 'cuadra' o 'no cuadra' — el detalle de la diferencia va al supervisor."""
    u = exigir(request, "ajustar_inventario")
    try:
        r = INV.registrar_conteo(body.insumo_id, body.contado, usuario=_nombre(u))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not r["cuadra"]:
        _avisar_diferencia_conteo(r["conteo_id"], _nombre(u))
    return {"cuadra": r["cuadra"],
            "mensaje": "Cuadra ✓" if r["cuadra"] else "No cuadra — llame a supervisor"}


@app.get("/api/conteo/pendientes")
def conteo_pendientes(request: Request):
    """Para el SUPERVISOR: acá sí va el detalle completo de cada diferencia."""
    exigir(request, "dar_de_baja")
    return {"pendientes": INV.conteos_pendientes()}


@app.post("/api/conteo/{conteo_id}/resolver")
def conteo_resolver(request: Request, conteo_id: int, body: ResolverConteoIn):
    """El supervisor cierra la diferencia; recién aquí el stock puede ajustarse."""
    u = exigir(request, "dar_de_baja")
    return INV.resolver_conteo(conteo_id, ajustar=body.ajustar,
                               usuario=_nombre(u), motivo=body.motivo)


def _avisar_diferencia_conteo(conteo_id, quien):
    """Manda el detalle de la diferencia por WhatsApp al jefe, vía el bot.

    Best-effort a propósito: si el bot no responde, el conteo YA quedó guardado y
    visible en /api/conteo/pendientes. Un aviso que falla no puede hacer perder el
    registro."""
    try:
        c = next((x for x in INV.conteos_pendientes() if x["id"] == conteo_id), None)
        if not c:
            return
        signo = "SOBRAN" if c["diferencia"] > 0 else "FALTAN"
        texto = (f"📋 CONTEO NO CUADRA — {c['insumo_nombre']}\n\n"
                 f"Esperado: {c['esperado']:g} {c['unidad_uso'] or ''}\n"
                 f"Contado: {c['contado']:g} {c['unidad_uso'] or ''}\n"
                 f"{signo}: {abs(c['diferencia']):g} {c['unidad_uso'] or ''} "
                 f"(${c['costo_diferencia']:,.0f})\n"
                 f"Contó: {quien}\n{c['en']}\n\n"
                 f"Hay que revisarlo en el POS antes de cerrar.")
        import urllib.request
        cuerpo = json.dumps({"texto": texto}).encode("utf-8")
        pet = urllib.request.Request(f"{_bot_url_wms()}/avisar-jefes", data=cuerpo,
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(pet, timeout=4).read()
    except Exception as e:
        print(f"[CONTEO][AVISO] no pude avisar por WhatsApp ({e}); "
              f"la diferencia igual quedó registrada.")


def _bot_url_wms():
    try:
        import impresora
        return str(impresora._cfg().get("bot_url", "http://127.0.0.1:8001")).rstrip("/")
    except Exception:
        return "http://127.0.0.1:8001"


class CortesiaIn(BaseModel):
    motivo: str                       # 'cortesia' o 'merma' (tipado, no texto libre)
    detalle: str = ""
    items: List[dict] = []


@app.post("/api/sin-venta")
def sin_venta(request: Request, body: CortesiaIn):
    """§1: confecciona algo que CONSUME stock pero NO registra venta (cortesía o merma).

    Exige 'dar_de_baja', NO 'ajustar_inventario': regalar o botar mercadería es una
    pérdida que autoriza el jefe, no una corrección de conteo del cajero."""
    u = exigir(request, "dar_de_baja")
    try:
        return INV.consumir_sin_venta(body.items, body.motivo,
                                      usuario=_nombre(u), detalle=body.detalle)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/salidas-sin-venta")
def salidas_sin_venta(request: Request, desde: Optional[str] = None,
                      hasta: Optional[str] = None):
    """§3: todo lo que salio de stock SIN venta, valorizado y por usuario.
    El control que faltaba: el dato ya se guardaba, pero nadie lo miraba."""
    exigir(request, "ver_reportes_wms")
    return INV.salidas_sin_venta(desde, hasta)


@app.post("/api/terminados/{terminado_id}/disponer")
def disponer(request: Request, terminado_id: int, body: DisponerIn):
    # 21-jul-2026: la llave depende de QUE se hace con el terminado. Regalar o destruir
    # es una PERDIDA -> exige 'dar_de_baja' (jefe). Revender, vender mas barato o
    # devolver insumos recupera/corrige -> basta 'ajustar_inventario' (cajero).
    permiso = ("dar_de_baja" if body.disposicion in DISPOSICIONES_QUE_EXIGEN_BAJA
               else "ajustar_inventario")
    u = exigir(request, permiso)
    try:
        t = INV.disponer_terminado(terminado_id, body.disposicion,
                                   precio_recuperado=body.precio_recuperado or 0,
                                   usuario=_nombre(u), motivo=body.motivo)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "terminado": t}


# ----------------------------------------------------------------------
# Dashboard / reportería
# ----------------------------------------------------------------------
@app.get("/api/tablero")
def tablero(request: Request):
    exigir_login(request)
    return INV.tablero()


@app.get("/api/reportes")
def reportes(request: Request):
    exigir(request, "ver_reportes_wms")
    tab = INV.tablero()
    nombres = {i["id"]: i["nombre"] for i in INV.listar_insumos()}
    por_cat, conteo = {}, {"rojo": 0, "amarillo": 0, "verde": 0}
    for r in tab:
        cat = r["categoria"] or "Sin categoría"
        por_cat[cat] = por_cat.get(cat, 0) + (r["valorizado"] or 0)
        conteo[r["semaforo"]] = conteo.get(r["semaforo"], 0) + 1
    consumo = [{"nombre": nombres.get(k, str(k)), "consumido": round(v, 2)}
               for k, v in INV.consumo_por_insumo().items()]
    consumo.sort(key=lambda x: -x["consumido"])
    ter = {}
    for t in INV.listar_terminados():
        ter[t["estado"]] = ter.get(t["estado"], 0) + 1
    return {
        "valorizacion_total": round(sum(r["valorizado"] or 0 for r in tab), 0),
        "por_categoria": [{"categoria": k, "valorizado": round(v, 0)} for k, v in por_cat.items()],
        "semaforo_conteo": conteo,
        "consumo": consumo[:12],
        "perdida_terminados": INV.perdida_terminados(),
        "terminados_por_estado": [{"estado": k, "n": v} for k, v in ter.items()],
        "alertas": INV.alertas(),
    }


# ----------------------------------------------------------------------
# Simulación de venta (demo del descuento en vivo, como pidió Atlas)
# ----------------------------------------------------------------------
@app.post("/api/simular-venta")
def simular_venta(request: Request, body: SimularIn):
    u = exigir(request, "vender")
    if not NUC.caja_abierta():
        NUC.abrir_caja(20000, cajero=_nombre(u))
    item = {"categoria": body.categoria, "producto": body.producto,
            "variante": "", "cantidad": body.cantidad or 1}
    pedido = NUC.crear(canal="cajero", cliente="Simulación WMS", modalidad="local",
                       items=[item], forma_pago="efectivo")
    NUC.transicionar(pedido["id"], "en_preparacion")
    res = INV.sincronizar_estado(pedido["id"], "en_preparacion", usuario=_nombre(u))
    nombres = {i["id"]: i["nombre"] for i in INV.listar_insumos()}
    consumos = [{"insumo": nombres.get(k, str(k)), "consumo": round(v, 2)}
                for k, v in (res.get("consumos") or {}).items()] if res else []
    return {"ok": True, "pedido_id": pedido["id"], "total": pedido["total"], "consumos": consumos}


# ----------------------------------------------------------------------
# Finanzas / libro de caja (ver_finanzas = leer, gestionar_finanzas = escribir)
# ----------------------------------------------------------------------
@app.get("/api/finanzas/categorias")
def fin_categorias(request: Request):
    exigir(request, "ver_finanzas")
    return FIN.listar_categorias()


@app.get("/api/finanzas/dia")
def fin_dia(request: Request, fecha: str):
    exigir(request, "ver_finanzas")
    return FIN.resumen_dia(fecha)


@app.get("/api/finanzas/mes")
def fin_mes(request: Request, anio: int, mes: int):
    exigir(request, "ver_finanzas")
    return FIN.resumen_mes(anio, mes)


@app.get("/api/finanzas/egresos")
def fin_egresos(request: Request, desde: str, hasta: str):
    exigir(request, "ver_finanzas")
    return FIN.listar_egresos(desde, hasta)


@app.get("/api/finanzas/pago-delivery-sugerido")
def fin_deli_sugerido(request: Request, fecha: str):
    exigir(request, "ver_finanzas")
    return {"sugerido": FIN.pago_delivery_sugerido(fecha)}


@app.post("/api/finanzas/egreso")
def fin_egreso(request: Request, body: EgresoIn):
    u = exigir(request, "gestionar_finanzas")
    eid = FIN.registrar_egreso(body.fecha, body.categoria_id, body.monto,
                               proveedor_id=body.proveedor_id, con_factura=body.con_factura,
                               iva=body.iva, nota=body.nota, origen="manual",
                               usuario=_nombre(u))
    return {"ok": True, "egreso_id": eid, "dia": FIN.resumen_dia(body.fecha)}


@app.delete("/api/finanzas/egreso/{egreso_id}")
def fin_egreso_del(request: Request, egreso_id: int, fecha: str):
    exigir(request, "gestionar_finanzas")
    FIN.eliminar_egreso(egreso_id)
    return {"ok": True, "dia": FIN.resumen_dia(fecha)}


@app.post("/api/finanzas/ingreso-manual")
def fin_ingreso(request: Request, body: IngresoManualIn):
    u = exigir(request, "gestionar_finanzas")
    FIN.registrar_ingreso_manual(body.fecha, body.monto, nota=body.nota, usuario=_nombre(u))
    return {"ok": True, "dia": FIN.resumen_dia(body.fecha)}


@app.post("/api/finanzas/pago-delivery")
def fin_pago_delivery(request: Request, body: PagoDeliveryIn):
    u = exigir(request, "gestionar_finanzas")
    FIN.registrar_pago_delivery(body.fecha, monto=body.monto, usuario=_nombre(u))
    return {"ok": True, "dia": FIN.resumen_dia(body.fecha)}


@app.post("/api/finanzas/gasto")
def fin_gasto(request: Request, body: GastoTextoIn):
    """Captura de gasto por texto (el bot de WhatsApp llega por aquí, igual que manda
    pedidos al POS por HTTP). Devuelve el eco de confirmación + si quedó registrado."""
    u = exigir(request, "gestionar_finanzas")
    return FIN.registrar_gasto_texto(body.texto, fecha=body.fecha, usuario=_nombre(u))


@app.post("/api/finanzas/gasto-bot")
def fin_gasto_bot(body: GastoTextoIn):
    """La 'llave' del bot para gastos: SIN header de usuario, pensado para localhost
    (mismo criterio que POST /api/pedidos del cajero). La autorizacion real la hace el
    bot -- solo el modo JEFE, validado por numero, emite gastos por WhatsApp."""
    if FIN is None:
        raise HTTPException(503, "finanzas no disponible")
    return FIN.registrar_gasto_texto(body.texto, fecha=body.fecha,
                                     usuario="bot (WhatsApp jefe)", origen="whatsapp")


# ----------------------------------------------------------------------
# Arranque
# ----------------------------------------------------------------------
def arrancar(db_path=DB_DEMO, seed=True):
    global NUC, INV, FIN
    NUC = NucleoPedidos(db_path=db_path)
    INV = NucleoInventario(NUC)
    FIN = NucleoFinanzas(NUC)
    if seed and not INV.listar_insumos():
        INV.sembrar_demo()
    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app


def integrar(nucleo, static_dir=None):
    """Prepara el WMS para montarse en OTRA app (el cajero de producción) compartiendo
    su MISMO núcleo/conexión (Capa 5). No arranca uvicorn: setea los globals y monta los
    estáticos. Devuelve `app`, lista para `app_padre.mount('/wms', app)`."""
    global NUC, INV, FIN, STATIC
    NUC = nucleo
    INV = NucleoInventario(nucleo)
    FIN = NucleoFinanzas(nucleo)
    if static_dir is not None:
        STATIC = Path(static_dir)
    ya_montado = any(getattr(r, "path", "") == "/static" for r in app.routes)
    if STATIC.exists() and not ya_montado:
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app


def _smoke_test():
    """Levanta el servidor en un hilo y le pega con urllib (solo stdlib, sin httpx)."""
    import json as _json
    import threading
    import time
    import urllib.error
    import urllib.request

    db = AQUI / "wms_test_smoke.db"
    arrancar(db_path=db, seed=True)
    port = 8019
    base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()

    def call(path, method="GET", body=None, headers=None):
        data = _json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                return r.status, _json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, _json.loads(e.read().decode() or "{}")

    for _ in range(60):                      # esperar a que el servidor esté listo
        try:
            if call("/api/salud")[0] == 200:
                break
        except Exception:
            time.sleep(0.1)

    fallos = []

    def chk(nombre, cond):
        print(f"  [{'OK  ' if cond else 'FALLA'}] {nombre}")
        if not cond:
            fallos.append(nombre)

    try:
        print("- Login y fail-closed")
        chk("sin login -> 401", call("/api/tablero")[0] == 401)
        st, r = call("/api/login", "POST", {"identificador": "Christian", "pin": "1111"})
        chk("login admin ok", st == 200 and r.get("ok"))
        H = {"X-Sesion": r["sesion"]}
        chk("admin tiene permisos WMS", all(p in r["usuario"]["permisos"] for p in PERMISOS_WMS))

        print("- Catalogo y tablero")
        chk("tablero responde con insumos", len(call("/api/tablero", headers=H)[1]) >= 4)
        chk("menu-productos trae productos del POS", len(call("/api/menu-productos", headers=H)[1]) > 0)

        print("- Captura SII")
        ted = sii.muestra_ted(total=119000)
        cap = call("/api/captura", "POST", {"texto": ted}, H)[1]
        chk("captura reconoce timbre SII", cap.get("clase") == "sii" and cap.get("total") == 119000)

        print("- Recepcion con SII")
        ins = call("/api/insumos", headers=H)[1]
        pan = next(i for i in ins if "Pan" in i["nombre"])
        stock_antes = next(x["stock"] for x in call("/api/tablero", headers=H)[1] if x["id"] == pan["id"])
        st, rec = call("/api/recepcion", "POST",
                       {"ted_texto": ted, "lineas": [{"insumo_id": pan["id"], "cantidad_compra": 10}]}, H)
        chk("recepcion ok", st == 200 and rec.get("ok"))
        stock_desp = next(x["stock"] for x in call("/api/tablero", headers=H)[1] if x["id"] == pan["id"])
        chk("stock del pan subio +120 (10 bolsas x12)", stock_desp == stock_antes + 120)

        print("- Simular venta -> descuento en vivo")
        st, sim = call("/api/simular-venta", "POST", {"cantidad": 3}, H)
        chk("simular venta ok", st == 200 and len(sim["consumos"]) >= 3)

        print("- Reporteria")
        rep = call("/api/reportes", headers=H)[1]
        chk("reporte trae valorizacion", rep.get("valorizacion_total", 0) > 0)
        chk("reporte trae semaforo", sum(rep["semaforo_conteo"].values()) >= 4)

        print("- Finanzas / libro de caja")
        cats = call("/api/finanzas/categorias", headers=H)[1]
        chk("categorias de la planilla sembradas (43)", len(cats) == 43)
        pan_cat = next(c for c in cats if c["nombre"] == "Pan")
        st, rr = call("/api/finanzas/egreso", "POST",
                      {"fecha": "2026-05-10", "categoria_id": pan_cat["id"], "monto": 20000}, H)
        chk("registrar egreso ok", st == 200 and rr.get("ok"))
        chk("el resumen del dia refleja el egreso", rr["dia"]["insumos_variables"] == 20000)
        st, gi = call("/api/finanzas/ingreso-manual", "POST",
                      {"fecha": "2026-05-10", "monto": 40000}, H)
        chk("boleta manual registrada", st == 200 and gi["dia"]["boleta_manual"] == 40000)
        g = call("/api/finanzas/gasto", "POST",
                 {"texto": "Jargui 25000 pan c/factura", "fecha": "2026-05-11"}, H)[1]
        chk("gasto por texto (canal WhatsApp) registrado",
            g.get("registrado") and g.get("categoria") == "Pan")
        chk("finanzas exige login (sin header -> 401)", call("/api/finanzas/categorias")[0] == 401)

        print("- Fail-closed: token de sesion inexistente")
        chk("header basura -> 401", call("/api/ajuste", "POST",
            {"insumo_id": pan["id"], "cantidad_uso": -1}, {"X-Sesion": "token-inventado"})[0] == 401)
    finally:
        server.should_exit = True
        time.sleep(0.3)
        try:
            NUC._con.close()
        except Exception:
            pass
        try:
            db.unlink(missing_ok=True)
        except Exception:
            pass

    print()
    print(f"RESULTADO: {'TODO OK' if not fallos else str(len(fallos)) + ' FALLA(S) -> ' + str(fallos)}")
    return not fallos


def main():
    ap = argparse.ArgumentParser(description="Servidor del WMS de Patata")
    ap.add_argument("--db", default=str(DB_DEMO))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--open", action="store_true", help="abre el navegador")
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("--test", action="store_true", help="smoke test sin navegador")
    args = ap.parse_args()

    if args.test:
        sys.exit(0 if _smoke_test() else 1)

    arrancar(db_path=Path(args.db), seed=not args.no_seed)
    if args.open:
        webbrowser.open(f"http://{args.host}:{args.port}")
    print(f"WMS Patata en http://{args.host}:{args.port}  (Ctrl+C para salir)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

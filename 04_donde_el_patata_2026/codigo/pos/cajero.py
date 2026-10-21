#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cajero.py — Cara CAJERO del POS de "Donde el Patata" (atención presencial).
===========================================================================
Primera UI sobre el núcleo (`pos/nucleo.py`). Es una pantalla local (localhost)
para que Christian:
  - tome pedidos presenciales y los DIGITE a mano (botones del menú),
  - vea el precio EN VIVO (lo calcula el tarificador, una sola verdad),
  - mande el pedido a la cola del núcleo (canal="cajero"),
  - mueva los pedidos por la cocina (recibido→...→entregado) y vea/imprima la comanda.

LA LLAVE PARA EL BOT (lo que pidió Gonza):
  El endpoint POST /api/pedidos acepta un campo `canal`. El cajero manda "cajero";
  el día que el bot esté listo, manda "whatsapp" por la MISMA puerta (o llama
  directo a `nucleo.crear(canal="whatsapp", ...)` en proceso). Misma cola, misma
  cocina, mismos precios. No hay que duplicar nada: la puerta ya queda abierta.

Servidor: FastAPI + uvicorn (ya en el venv). Front-end: una sola página
(`pos/cajero.html`), vanilla JS, sin build. Sin Gemini ni internet.

Correr:
    python pos/cajero.py            # levanta el servidor en http://127.0.0.1:8000
    python pos/cajero.py --port 9000
    python pos/cajero.py --test     # auto-test sin navegador (no necesita Gemini)
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import hmac
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from rutas import MENU_JSON, HTML_CAJERO, BOT, BACKUPS  # noqa: E402  (rutas conscientes de .py vs .exe)
# El núcleo y el tarificador (motor de precios + carta) son COMPARTIDOS. Reutilizarlos.
sys.path.insert(0, str(BOT))                   # dev: tarificar vive en bot/
from nucleo import LoginBloqueado, NucleoPedidos, ESTADOS, TRANSICIONES, CANALES, PERMISOS  # noqa: E402
from tarificar import Tarificador, _variantes_validas, _precio_variante, LABEL_VARIANTE  # noqa: E402
import impresora  # noqa: E402  (impresión térmica ESC/POS; importa win32print de forma diferida)


def cargar_menu():
    return json.loads(MENU_JSON.read_text(encoding="utf-8"))


def carta_para_ui(menu):
    """Transforma menu.json en una carta lista para pintar botones en el cajero.
    Cada producto sabe: qué variantes ofrece, si tiene sabores (variedades),
    su precio "desde" (para mostrar) y si tiene promo 2x. La lógica de qué
    variantes/precios tiene cada producto se reusa del tarificador (no se duplica).
    """
    cats = []
    for cat in menu.get("categorias", []):
        productos = []
        for p in cat["productos"]:
            vv = _variantes_validas(p)
            if vv:
                variantes = []
                for v in vv:
                    unit, _ = _precio_variante(p, v)
                    variantes.append({"valor": v, "label": LABEL_VARIANTE.get(v, v),
                                      "precio": unit})
                precios = [x["precio"] for x in variantes if x["precio"] is not None]
                precio_desde = min(precios) if precios else None
                promo_2x = any(_precio_variante(p, v)[1] for v in vv)
            else:
                unit, p2x = _precio_variante(p, None)
                variantes = []
                precio_desde = unit
                promo_2x = bool(p2x)
            productos.append({
                "nombre": p["nombre"],
                "desc": p.get("ingredientes") or p.get("contenido") or "",
                "variantes": variantes,
                "variedades": p.get("variedades", []),   # sabores -> van en notas
                "armados": p.get("armados", []),         # modificadores sin costo -> van en notas
                "desagrupar": bool(p.get("desagrupar")), # papas/salchipapas: un boton por tamano
                "precio_desde": precio_desde,
                "promo_2x": promo_2x,
            })
        cats.append({"nombre": cat["nombre"], "etiqueta": cat.get("etiqueta", ""),
                     "nota": cat.get("nota", ""), "productos": productos})
    return cats


# ===========================================================================
# Modelos de entrada (lo que el front manda al servidor)
# ===========================================================================
class ItemIn(BaseModel):
    categoria: str
    producto: str
    variante: Optional[str] = None
    cantidad: int = 1
    notas: str = ""


class PagoIn(BaseModel):
    metodo: str
    monto: int


class PedidoIn(BaseModel):
    canal: str = "cajero"           # <- la llave: el bot mandará "whatsapp" por acá
    cliente: str = ""
    modalidad: str = "retiro"
    items: List[ItemIn]
    forma_pago: Optional[str] = None
    direccion: Optional[str] = None
    delivery_cobro: int = 0
    notas: str = ""
    pago_con: Optional[int] = None   # con cuánto paga (efectivo) -> el núcleo calcula el vuelto
    mesa: Optional[str] = None       # modo local
    telefono: Optional[str] = None   # delivery/retiro -> liga al cliente y refresca la base
    pagos: Optional[List[PagoIn]] = None  # pago combinado: varios métodos que suman el total
    hora_programada: Optional[str] = None  # "HH:MM" (04-ago-2026, P29) -- ver NucleoPedidos.crear


class PagosIn(BaseModel):
    pagos: List[PagoIn]
    pago_con: Optional[int] = None   # efectivo con que paga (para recalcular el vuelto)


class PreviewIn(BaseModel):
    items: List[ItemIn]
    delivery_cobro: int = 0


class EstadoIn(BaseModel):
    estado: str


class CajaAbrirIn(BaseModel):
    fondo_inicial: int = 0
    cajero: str = ""


class CajaCerrarIn(BaseModel):
    contado_cierre: int = 0
    contado_otros: Optional[Dict[str, int]] = None   # tarjeta/transferencia confirmados (opcional)


class ClienteIn(BaseModel):
    telefono: Optional[str] = None
    nombre: str = ""
    direccion: str = ""


class ImpresoraIn(BaseModel):
    impresora: Optional[str] = None
    ancho: Optional[int] = None


class LoginIn(BaseModel):
    identificador: str = ""
    pin: str = ""


class UsuarioIn(BaseModel):
    nombre: str
    pin: str
    perfil_id: int
    email: str = ""


class UsuarioEditIn(BaseModel):
    nombre: Optional[str] = None
    email: Optional[str] = None
    perfil_id: Optional[int] = None


class PinIn(BaseModel):
    pin: str


class PerfilIn(BaseModel):
    nombre: str
    permisos: List[str] = []
    es_admin: int = 0


class PerfilEditIn(BaseModel):
    nombre: Optional[str] = None
    permisos: Optional[List[str]] = None
    es_admin: Optional[int] = None


# ===========================================================================
# App
# ===========================================================================
app = FastAPI(title="POS Patata — Cajero")
MENU = cargar_menu()


# ===========================================================================
# Candado del bot en la nube (Opcion B, 16-jul-2026)
# ===========================================================================
# Cuando el POS se expone por el tunel de Cloudflare para que el bot (en Railway)
# le mande pedidos, esa URL queda accesible desde TODO internet. Sin candado,
# cualquiera podria crear pedidos falsos, cerrar caja o leer las direcciones de
# los clientes. Este middleware unico protege TODA la app (incluido /wms/):
#
#   - Peticion de la LAN (cajero del local, celular en la WiFi): llega directo al
#     PC, SIN pasar por Cloudflare -> NO trae el header 'cf-connecting-ip' -> pasa
#     como siempre. La red local sigue siendo de confianza, sin tocar nada.
#   - Peticion por el tunel (unica ruta desde internet): Cloudflare SIEMPRE agrega
#     'cf-connecting-ip' y el cliente no puede quitarlo -> se exige el token del
#     bot. El unico de afuera que lo tiene es el bot.
#
# El candado se ACTIVA solo si hay token configurado (env POS_BOT_TOKEN o
# 'bot_token' en pos/config.json). Sin token, el POS funciona EXACTAMENTE como
# hoy -> no rompe la operacion real de Christian mientras el tunel siga cerrado.
def _bot_token():
    t = os.environ.get("POS_BOT_TOKEN", "").strip()
    if t:
        return t
    try:
        return str(impresora._cfg().get("bot_token") or "").strip()
    except Exception:
        return ""


def _candado_permite(token, por_tunel, x_bot_token):
    """Funcion pura (testeable sin HTTP): True = deja pasar, False = 403."""
    if not token:
        return True          # candado apagado -> comportamiento historico del POS
    if not por_tunel:
        return True          # LAN de confianza (no llego por el tunel)
    return bool(x_bot_token) and hmac.compare_digest(str(x_bot_token), str(token))


# ===========================================================================
# Puerta unica de la API (CoVentas, seguridad 1b). ANTES cada endpoint decidia solo si
# pedia login y la mayoria no lo hacia (ventas, caja, clientes, auditoria...). Ahora la
# regla es al reves y por defecto: TODO lo que cuelga de /api/ (y /wms/api/) exige sesion
# valida, salvo la lista publica. Un endpoint nuevo queda protegido sin acordarse de nada.
# El bot (Railway) no tiene sesion: entra con su X-Bot-Token, pero SOLO a los endpoints
# que realmente usa. Los permisos finos (exigir_permiso) siguen encima de esto.
# ===========================================================================
_API_PUBLICA = {("POST", "/api/login"), ("GET", "/api/usuarios-login"), ("POST", "/api/logout"),
                ("POST", "/wms/api/login"), ("GET", "/wms/api/salud")}
_API_BOT = {("POST", "/api/pedidos"), ("GET", "/api/menu-edit"),
            ("GET", "/api/clientes-export"), ("GET", "/api/pedidos-programados"),
            ("POST", "/wms/api/finanzas/gasto-bot")}


def _acceso_api(metodo, ruta, x_sesion, x_bot_token, token_bot, usuario_de_sesion):
    """Funcion pura (testeable sin HTTP). Devuelve 'publico' | 'sesion' | 'bot' | None."""
    ruta = ruta.rstrip("/") or "/"
    if not (ruta.startswith("/api/") or ruta.startswith("/wms/api/")):
        return "publico"                      # HTML, iconos, estaticos: no son datos
    if (metodo, ruta) in _API_PUBLICA:
        return "publico"
    if x_sesion and usuario_de_sesion(x_sesion):
        return "sesion"
    es_direccion = ruta.startswith("/api/clientes/") and ruta.endswith("/direcciones")
    if (((metodo, ruta) in _API_BOT) or (metodo == "GET" and es_direccion)) \
            and token_bot and x_bot_token and hmac.compare_digest(str(x_bot_token), str(token_bot)):
        return "bot"
    return None


@app.middleware("http")
async def _puerta_api(request: Request, call_next):
    acceso = _acceso_api(request.method, request.url.path,
                         request.headers.get("x-sesion", ""),
                         request.headers.get("x-bot-token", ""), _bot_token(),
                         NUCLEO.usuario_por_sesion)
    if acceso is None:
        return JSONResponse({"detail": "Debes iniciar sesion"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def _candado_bot(request: Request, call_next):
    if not _candado_permite(_bot_token(),
                            "cf-connecting-ip" in request.headers,
                            request.headers.get("x-bot-token", "")):
        return JSONResponse(
            {"detail": "POS protegido: esta peticion vino de internet sin el token del bot."},
            status_code=403)
    return await call_next(request)


def _db_inicial():
    # Prioridad: --db <ruta> (CLI) > POS_DB (env) > default (pos/pedidos.db).
    # Util para levantar una instancia de prueba sin tocar la base real.
    if "--db" in sys.argv:
        return sys.argv[sys.argv.index("--db") + 1]
    return os.environ.get("POS_DB")


_DB = _db_inicial()
NUCLEO = NucleoPedidos(db_path=_DB) if _DB else NucleoPedidos()


# ===========================================================================
# Permisos en el BACKEND (punto 21c). Hasta aca el gateo era solo en la UI: un
# cliente HTTP que se saltee la pantalla podia llamar cualquier endpoint. El
# frontend manda quien esta logueado en el token X-Sesion en cada request
# sensible; el backend vuelve a resolver sus permisos DESDE LA BASE (nunca confia
# en lo que mande el cliente) antes de dejarlo pasar.
# ===========================================================================
def usuario_actual(x_sesion: Optional[str] = Header(None)):
    """El usuario logueado, resuelto desde la base a partir del token de sesion que entrego
    /api/login (header X-Sesion). None si no vino, vencio o el usuario ya no esta activo.
    Ya NO se acepta X-Usuario-Id: era un numero adivinable que cualquiera podia falsificar."""
    return NUCLEO.usuario_por_sesion(x_sesion)


def exigir_permiso(permiso):
    """Fabrica un dependency de FastAPI: exige que el usuario identificado (header
    X-Sesion) tenga 'permiso'. Devuelve el usuario (para poder loguear auditoria
    con su nombre real) o corta la request con 401/403."""
    def _dep(usuario: Optional[dict] = Depends(usuario_actual)):
        if not usuario:
            raise HTTPException(status_code=401, detail="Debes iniciar sesión de nuevo")
        if permiso not in (usuario.get("permisos") or []):
            raise HTTPException(status_code=403, detail=f"Tu perfil no tiene el permiso '{permiso}'")
        return usuario
    return _dep


@app.get("/", response_class=HTMLResponse)
def home():
    # Se lee en cada request para que los ajustes de la pantalla se vean sin reiniciar.
    return HTMLResponse(HTML_CAJERO.read_text(encoding="utf-8"))


# --- PWA-lite (03-jul, "cajero portatil"): manifest + icono para que el celular pueda
# "Añadir a pantalla de inicio" con nombre e icono propios. NOTA HONESTA: sobre HTTP
# de red local (http://IP:8000) Chrome/Android lo agrega como ACCESO DIRECTO (abre en
# el navegador), no como app standalone — eso ultimo exige HTTPS. Para el uso real
# (el celular del local, mismo WiFi) el acceso directo cumple: un toque y esta adentro.
@app.get("/manifest.webmanifest")
def manifest():
    return Response(content=json.dumps({
        "name": f"POS {MENU['local']['nombre']}",
        "short_name": "POS Patata",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f4f1ec",
        "theme_color": "#2a2622",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }, ensure_ascii=False), media_type="application/manifest+json")


@app.get("/icon.svg")
def icono():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<rect width="100" height="100" rx="22" fill="#e8731f"/>
<ellipse cx="50" cy="54" rx="30" ry="24" fill="#c9994f" transform="rotate(-18 50 54)"/>
<circle cx="42" cy="48" r="2.6" fill="#7a5b2e"/><circle cx="58" cy="44" r="2.6" fill="#7a5b2e"/>
<circle cx="52" cy="62" r="2.6" fill="#7a5b2e"/>
<text x="50" y="90" font-size="15" font-weight="bold" font-family="Arial" fill="#fff"
 text-anchor="middle">POS</text></svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/api/menu")
def api_menu():
    return {
        "local": {
            "nombre": MENU["local"]["nombre"],
            "comuna": MENU["local"].get("comuna", ""),
            "pago": MENU["local"]["pago"]["metodos"],
        },
        "estados": ESTADOS,
        "transiciones": {k: sorted(v) for k, v in TRANSICIONES.items()},
        "categorias": carta_para_ui(MENU),
    }


class MenuEditIn(BaseModel):
    menu: Dict[str, Any]


@app.get("/api/menu-edit")
def api_menu_crudo():
    """El menu.json CRUDO (estructura real: precio, precio_2x, armados, combo...),
    para la pantalla de administracion (que edita y reescribe el archivo)."""
    return json.loads(MENU_JSON.read_text(encoding="utf-8"))


@app.post("/api/menu-edit")
def api_menu_guardar(m: MenuEditIn, usuario=Depends(exigir_permiso("editar_menu"))):
    """Valida el menu propuesto (debe cargar sin errores en el tarificador, incluidos
    los combos) y, si esta OK, hace un BACKUP del menu.json actual antes de reemplazarlo.
    Recarga el tarificador en caliente: no hace falta reiniciar el servidor."""
    global MENU
    try:
        nuevo_tarificador = Tarificador(m.menu)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"menu invalido: {e}")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    (BACKUPS / f"menu_{marca}.json").write_text(
        MENU_JSON.read_text(encoding="utf-8"), encoding="utf-8")
    MENU_JSON.write_text(json.dumps(m.menu, ensure_ascii=False, indent=2), encoding="utf-8")
    MENU = m.menu
    NUCLEO.recargar_menu(nuevo_tarificador)
    NUCLEO.registrar_auditoria(usuario["nombre"], "editar_menu", detalle="guardo cambios en el menu")
    return {"ok": True}


@app.post("/api/preview")
def api_preview(p: PreviewIn):
    """Precio EN VIVO mientras se arma el pedido (no guarda nada)."""
    cuenta = NUCLEO.tarificador.tarificar_pedido({
        "items": [i.model_dump() for i in p.items],
        "delivery_cobro": p.delivery_cobro, "total": 0,
    })
    return cuenta


# ---------------------------------------------------------------------------
# Anti-duplicado (21-jul-2026, bug real: un doble clic creo el pedido dos veces)
# ---------------------------------------------------------------------------
# La proteccion va ACA, en el backend, y no en el boton de la UI, porque el doble
# clic no es el unico vector: Meta REINTENTA los webhooks que no responden 200, y
# si un POST alcanzo a crear el pedido pero la respuesta se perdio, el reintento
# crearia un duplicado. Poniendolo en /api/pedidos queda cubierto de una sola vez
# el cajero, el bot y cualquier canal futuro.
VENTANA_DUPLICADO_SEG = 10
_ultimos_pedidos = {}          # firma -> (momento, pedido_ya_creado)


def _firma_pedido(p):
    """Identidad de la INTENCION del pedido: mismo cliente, mismos items, mismo
    total y misma modalidad = el mismo pedido, no dos."""
    items = sorted((i.categoria, i.producto, i.variante, i.cantidad, i.notas or "")
                   for i in p.items)
    return hashlib.sha256(repr(
        (p.canal, p.telefono or "", p.cliente or "", p.modalidad, p.forma_pago or "",
         items)).encode("utf-8")).hexdigest()


def _duplicado_reciente(firma):
    """Devuelve el pedido ya creado si esta misma intencion llego hace menos de
    VENTANA_DUPLICADO_SEG segundos. Limpia de paso las firmas vencidas."""
    ahora = time.time()
    for f, (t, _) in list(_ultimos_pedidos.items()):
        if ahora - t > VENTANA_DUPLICADO_SEG:
            _ultimos_pedidos.pop(f, None)
    reg = _ultimos_pedidos.get(firma)
    return reg[1] if reg else None


@app.post("/api/pedidos")
def api_crear(p: PedidoIn, forzar: bool = False):
    # forzar=True es la valvula de escape explicita: si de verdad quieren dos
    # pedidos identicos seguidos, la UI lo reenvia con esa marca.
    firma = _firma_pedido(p)
    if not forzar:
        ya = _duplicado_reciente(firma)
        if ya:
            print(f"[ANTI-DUPLICADO] misma intencion dentro de {VENTANA_DUPLICADO_SEG}s: "
                  f"devuelvo el pedido #{ya['id']} en vez de crear otro.")
            return {**ya, "duplicado_evitado": True}
    try:
        pedido = NUCLEO.crear(
            canal=p.canal, cliente=p.cliente, modalidad=p.modalidad,
            items=[i.model_dump() for i in p.items], forma_pago=p.forma_pago,
            direccion=p.direccion, delivery_cobro=p.delivery_cobro, notas=p.notas,
            pago_con=p.pago_con, mesa=p.mesa, telefono=p.telefono,
            pagos=[pg.model_dump() for pg in p.pagos] if p.pagos else None,
            hora_programada=p.hora_programada,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _ultimos_pedidos[firma] = (time.time(), pedido)
    if p.canal == "whatsapp" and not p.hora_programada:
        # El cliente ya cerro el pedido con el bot (tarificado real, sin intervencion
        # humana previa): a diferencia de un pedido armado en el cajero, ESTE no espera
        # el click "A cocina" -- se manda solo a cocina y se imprime la comanda al
        # instante (14-jul-2026: Christian esperaba ver el ticket salir solo al llegar
        # el pedido por WhatsApp). Best-effort: si falla (sin impresora, etc.) el
        # pedido queda igual creado y visible en la cola; solo se pierde el auto-print.
        # 04-ago-2026 (P29): si viene con hora_programada, ESTE candado es el punto --
        # no auto-imprimir ni pasar a cocina antes de tiempo solo porque llego por bot.
        try:
            pedido = NUCLEO.transicionar(pedido["id"], "en_preparacion", usuario="bot WhatsApp")
            impresora.imprimir_pedido(NUCLEO.pedido_comanda(pedido["id"]),
                                      local=MENU["local"]["nombre"])
        except Exception as e:
            print(f"[WHATSAPP][AVISO] pedido #{pedido['id']} creado pero el auto-print/"
                  f"paso a cocina fallo ({e}); Christian puede imprimirlo a mano desde la cola.")
    return pedido


@app.get("/api/pedidos/{pedido_id}")
def api_obtener(pedido_id: int):
    p = NUCLEO.obtener(pedido_id)
    if not p:
        raise HTTPException(status_code=404, detail="pedido no existe")
    return p


@app.put("/api/pedidos/{pedido_id}")
def api_editar(pedido_id: int, p: PedidoIn, usuario=Depends(exigir_permiso("editar_pedido"))):
    try:
        return NUCLEO.editar(
            pedido_id, cliente=p.cliente, modalidad=p.modalidad,
            items=[i.model_dump() for i in p.items], forma_pago=p.forma_pago,
            direccion=p.direccion, delivery_cobro=p.delivery_cobro, notas=p.notas,
            pago_con=p.pago_con, mesa=p.mesa, telefono=p.telefono,
            pagos=[pg.model_dump() for pg in p.pagos] if p.pagos else None,
            usuario=usuario["nombre"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/pedidos/{pedido_id}/pagos")
def api_pagos(pedido_id: int, c: PagosIn, usuario=Depends(exigir_permiso("redistribuir_pago"))):
    """Redistribuye SOLO el pago de un pedido (sin tocar items/total). Es lo que usa
    el Informe de ventas: el empleado reparte el 100% del total entre metodos."""
    try:
        return NUCLEO.registrar_pagos(pedido_id, [pg.model_dump() for pg in c.pagos],
                                      pago_con=c.pago_con, usuario=usuario["nombre"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/ventas")
def api_ventas(desde: str = "", hasta: str = ""):
    """Informe de ventas por rango de fechas (YYYY-MM-DD). Sin parametros = todo."""
    return NUCLEO.ventas_periodo(desde=desde or None, hasta=hasta or None)


@app.post("/api/login")
def api_login(c: LoginIn):
    try:
        u = NUCLEO.autenticar(c.identificador, c.pin)
    except LoginBloqueado as e:
        raise HTTPException(status_code=429, detail=str(e), headers={"Retry-After": str(e.segundos)})
    if not u:
        raise HTTPException(status_code=401, detail="Usuario o PIN incorrecto")
    return dict(u, sesion=NUCLEO.crear_sesion(u["id"]))


@app.get("/api/usuarios-login")
def api_usuarios_login():
    """Lo unico que la pantalla de login necesita antes de tener sesion: nombre y perfil de los
    usuarios activos. Sin id, sin email, sin permisos."""
    return {"usuarios": [{"nombre": u["nombre"], "perfil": u.get("perfil"), "activo": 1}
                         for u in NUCLEO.listar_usuarios() if u.get("activo")]}


@app.post("/api/logout")
def api_logout(x_sesion: Optional[str] = Header(None)):
    if x_sesion:
        NUCLEO.cerrar_sesion(x_sesion)
    return {"ok": True}


@app.get("/api/usuarios")
def api_usuarios():
    """Usuarios (sin PIN) y perfiles. Alimenta el login y la pantalla admin."""
    return {"usuarios": NUCLEO.listar_usuarios(), "perfiles": NUCLEO.listar_perfiles()}


@app.post("/api/usuarios")
def api_usuario_crear(u: UsuarioIn, usuario=Depends(exigir_permiso("gestionar_usuarios"))):
    try:
        uid = NUCLEO.crear_usuario(u.nombre, u.pin, u.perfil_id, email=u.email, usuario=usuario["nombre"])
        return next(x for x in NUCLEO.listar_usuarios() if x["id"] == uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/usuarios/{usuario_id}")
def api_usuario_editar(usuario_id: int, u: UsuarioEditIn, usuario=Depends(exigir_permiso("gestionar_usuarios"))):
    try:
        return NUCLEO.editar_usuario(usuario_id, nombre=u.nombre, email=u.email, perfil_id=u.perfil_id,
                                     usuario=usuario["nombre"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/usuarios/{usuario_id}/desactivar")
def api_usuario_desactivar(usuario_id: int, usuario=Depends(exigir_permiso("gestionar_usuarios"))):
    try:
        NUCLEO.desactivar_usuario(usuario_id, usuario=usuario["nombre"])
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/usuarios/{usuario_id}/reactivar")
def api_usuario_reactivar(usuario_id: int, usuario=Depends(exigir_permiso("gestionar_usuarios"))):
    try:
        NUCLEO.reactivar_usuario(usuario_id, usuario=usuario["nombre"])
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/usuarios/{usuario_id}/pin")
def api_usuario_pin(usuario_id: int, c: PinIn, usuario=Depends(exigir_permiso("gestionar_usuarios"))):
    try:
        NUCLEO.cambiar_pin(usuario_id, c.pin, usuario=usuario["nombre"])
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/perfiles")
def api_perfil_crear(p: PerfilIn, usuario=Depends(exigir_permiso("gestionar_usuarios"))):
    try:
        pid = NUCLEO.crear_perfil(p.nombre, p.permisos, es_admin=p.es_admin, usuario=usuario["nombre"])
        return next(x for x in NUCLEO.listar_perfiles() if x["id"] == pid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/perfiles/{perfil_id}")
def api_perfil_editar(perfil_id: int, p: PerfilEditIn, usuario=Depends(exigir_permiso("gestionar_usuarios"))):
    try:
        return NUCLEO.editar_perfil(perfil_id, nombre=p.nombre, permisos=p.permisos, es_admin=p.es_admin,
                                    usuario=usuario["nombre"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/perfiles/{perfil_id}")
def api_perfil_eliminar(perfil_id: int, usuario=Depends(exigir_permiso("gestionar_usuarios"))):
    try:
        NUCLEO.eliminar_perfil(perfil_id, usuario=usuario["nombre"])
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/permisos")
def api_permisos():
    """Catalogo de acciones que un perfil puede tener permitidas (para pintar checkboxes)."""
    return {"permisos": PERMISOS}


@app.get("/api/auditoria")
def api_auditoria(limite: int = 200):
    """Registro de quien hizo que (punto 21b): anular, editar pedido, redistribuir
    pago, y gestion de usuarios/perfiles/menu. Pantalla admin la muestra."""
    return {"eventos": NUCLEO.listar_auditoria(limite)}


# --- Caja (apertura / cierre / resumen del día) ---
@app.get("/api/caja")
def api_caja():
    caja = NUCLEO.caja_abierta()
    if not caja:
        return {"abierta": False, "caja": None, "resumen": None}
    return {"abierta": True, "caja": caja, "resumen": NUCLEO.resumen_caja(caja["id"])}


@app.post("/api/caja/abrir")
def api_caja_abrir(c: CajaAbrirIn):
    try:
        caja = NUCLEO.abrir_caja(c.fondo_inicial, c.cajero)
        return {"caja": caja, "resumen": NUCLEO.resumen_caja(caja["id"])}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/caja/cerrar")
def api_caja_cerrar(c: CajaCerrarIn):
    try:
        return NUCLEO.cerrar_caja(c.contado_cierre, contado_otros=c.contado_otros)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/cajas")
def api_cajas(limite: int = 30):
    """Historico de cierres (punto 12), mas recientes primero."""
    return {"cajas": NUCLEO.listar_cajas(limite)}


@app.get("/api/caja/{caja_id}")
def api_caja_detalle(caja_id: int):
    resumen = NUCLEO.resumen_caja(caja_id)
    if not resumen:
        raise HTTPException(status_code=404, detail="caja no existe")
    return resumen


@app.post("/api/caja/{caja_id}/imprimir")
def api_caja_imprimir(caja_id: int):
    resumen = NUCLEO.resumen_caja(caja_id)
    if not resumen:
        raise HTTPException(status_code=404, detail="caja no existe")
    try:
        nombre = impresora.imprimir_cierre(resumen, local=MENU["local"]["nombre"])
        return {"ok": True, "impresora": nombre}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Clientes (delivery/retiro por teléfono; local por nombre) ---
@app.get("/api/clientes/{telefono}")
def api_cliente_tel(telefono: str):
    c = NUCLEO.buscar_cliente_telefono(telefono)
    if not c:
        raise HTTPException(status_code=404, detail="cliente no encontrado")
    return c


@app.get("/api/clientes/{telefono}/direcciones")
def api_cliente_direcciones(telefono: str):
    return {"direcciones": NUCLEO.listar_direcciones_cliente(telefono)}


@app.get("/api/clientes")
def api_clientes_buscar(nombre: str = "", telefono: str = ""):
    if telefono:
        return {"clientes": NUCLEO.buscar_clientes_telefono(telefono)}
    return {"clientes": NUCLEO.buscar_clientes_nombre(nombre)}


@app.post("/api/clientes")
def api_cliente_guardar(c: ClienteIn):
    return NUCLEO.guardar_cliente(telefono=c.telefono, nombre=c.nombre, direccion=c.direccion)


@app.get("/api/clientes-export")
def api_clientes_export():
    """Volcado completo de clientes con telefono (04-ago-2026, 'nombre registrado' --
    mismo sentido que /api/menu-edit y /api/pedidos-programados: bot -> POS, protegido
    solo por el candado del tunel de mas arriba). El bot lo cachea localmente en un hilo
    de fondo (ver _refrescar_clientes_pos en bot/webhook.py) para no consultar el POS en
    vivo en el camino de cada mensaje."""
    return {"clientes": NUCLEO.listar_clientes_export()}


# --- Impresión térmica (ESC/POS directo a la X-Printer por el spooler de Windows) ---
@app.get("/api/impresoras")
def api_impresoras():
    return {"impresoras": impresora.listar_impresoras(),
            "actual": impresora.impresora_actual(),
            "config": impresora._cfg()}


@app.post("/api/impresora")
def api_impresora_guardar(c: ImpresoraIn):
    return impresora.guardar_config(impresora=c.impresora, ancho=c.ancho)


@app.post("/api/pedidos/{pedido_id}/imprimir")
def api_imprimir(pedido_id: int):
    p = NUCLEO.pedido_comanda(pedido_id)   # items con 'nombre_comanda' (etiqueta+producto)
    if not p:
        raise HTTPException(status_code=404, detail="pedido no existe")
    try:
        nombre = impresora.imprimir_pedido(p, local=MENU["local"]["nombre"])
        return {"ok": True, "impresora": nombre}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/imprimir/test")
def api_imprimir_test():
    try:
        nombre = impresora.imprimir_test(local=MENU["local"]["nombre"])
        return {"ok": True, "impresora": nombre}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Editor de comanda (07-jul-2026): Christian disena su comanda desde el POS ---
class TemaComandaIn(BaseModel):
    tema: dict


# Pedido de ejemplo para el preview y la impresion de prueba del editor (datos ficticios,
# con las 3 modalidades representadas por 'delivery' que es la mas completa: telefono +
# direccion + vuelto). nombre_comanda ya resuelto (la impresora no re-resuelve el menu).
PEDIDO_DEMO_COMANDA = {
    "id": 128, "canal": "cajero", "creado_en": "2026-07-07T21:30:00",
    "telefono": "987654321", "cliente": "María González",
    "modalidad": "delivery", "direccion": "Av. Recoleta 4521",
    "total": 14400, "delivery_cobro": 1500,
    "pagos": [{"metodo": "efectivo", "monto": 14400}],
    "pago_con": 15000, "vuelto": 600,
    "notas": "casa esquina, cuidado con el perro",
    "items": [
        {"cantidad": 2, "producto": "Churrasco Italiano", "nombre_comanda": "Churrasco Italiano", "ok": True, "notas": "Tomate/Palta"},
        {"cantidad": 1, "producto": "Papas Fritas", "nombre_comanda": "Papas Fritas", "variante": "grande", "ok": True},
        {"cantidad": 1, "producto": "Lata CCU", "nombre_comanda": "Lata CCU", "ok": True, "notas": "Bilz"},
    ],
}


@app.get("/api/tema-comanda")
def api_tema_comanda():
    """El tema guardado + las etiquetas de cada bloque + las lineas reales del pedido
    demo por bloque (para que el editor muestre un preview fiel sin re-implementar la
    logica de contenido en el navegador)."""
    demo = dict(PEDIDO_DEMO_COMANDA)
    demo["_local"] = MENU["local"]["nombre"]
    ancho = impresora._cfg().get("ancho", impresora.ANCHO_80MM)
    lineas = {bid: impresora._bloque_lineas(bid, demo, ancho)
              for bid in impresora.ETIQUETA_BLOQUE if bid != "qr"}
    return {"tema": impresora.tema_comanda(), "etiquetas": impresora.ETIQUETA_BLOQUE,
            "lineas": lineas, "ancho": ancho}


@app.post("/api/tema-comanda")
def api_tema_comanda_guardar(t: TemaComandaIn, usuario=Depends(exigir_permiso("editar_menu"))):
    impresora.guardar_tema_comanda(t.tema)
    NUCLEO.registrar_auditoria(usuario["nombre"], "editar_menu", detalle="editó el diseño de la comanda")
    return {"ok": True}


@app.post("/api/imprimir/prueba-comanda")
def api_imprimir_prueba(t: TemaComandaIn):
    """Imprime una comanda DEMO con el tema recibido (sin guardarlo), para que Christian
    vea en papel real como queda el diseno antes de dejarlo fijo."""
    try:
        nombre = impresora.impresora_actual()
        ancho = impresora._cfg().get("ancho", impresora.ANCHO_80MM)
        data = impresora.comanda_bytes(PEDIDO_DEMO_COMANDA, local=MENU["local"]["nombre"],
                                       ancho=ancho, tema=t.tema)
        impresora.imprimir_raw(nombre, data)
        return {"ok": True, "impresora": nombre}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/cola")
def api_cola():
    return {"pedidos": NUCLEO.cola()}


@app.get("/api/pedidos-programados")
def api_pedidos_programados():
    """Pedidos ACTIVOS con hora_programada (04-ago-2026, P29) -- lo que el bot consulta
    (mismo sentido que /api/menu-edit: bot -> POS, sin tunel para el candado de arriba)
    para mandar el recordatorio de 'hora de empezar a preparar' cerca de la hora pactada.
    Ver _hilo_recordar_programados en bot/webhook.py."""
    return {"pedidos": [p for p in NUCLEO.cola() if p.get("hora_programada")]}


@app.get("/api/bot/conversaciones")
def api_bot_conversaciones(desde_id: int = 0):
    """Proxy hacia el bot (webhook.py, servicio APARTE): trae la conversacion en vivo
    para la pantalla del POS (14-jul-2026, pedido de Christian: no tenia forma de ver
    que conversa el bot con los clientes). Gracioso si el bot no esta corriendo -- la
    UI debe poder mostrar 'sin conexion con el bot' en vez de romperse."""
    import urllib.request
    bot_url = impresora._cfg().get("bot_url", "http://127.0.0.1:8001").rstrip("/")
    try:
        pet = urllib.request.Request(f"{bot_url}/conversaciones?desde_id={desde_id}",
                                     headers=_headers_panel_bot())
        with urllib.request.urlopen(pet, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        return {"disponible": True, **data}
    except Exception as e:
        return {"disponible": False, "turnos": [], "pendientes_respaldo": 0, "error": str(e)}


def _bot_url():
    return impresora._cfg().get("bot_url", "http://127.0.0.1:8001").rstrip("/")


def _headers_panel_bot(extra=None):
    """Token del panel del bot (21-jul-2026). /conversaciones y /rechazados exponen
    conversaciones de clientes; con el bot en la nube quedaron alcanzables desde
    internet. Se configura 'bot_panel_token' en config.json y BOT_PANEL_TOKEN en
    Railway (mismo valor). Sin configurar, todo funciona como antes."""
    h = dict(extra or {})
    tok = (os.environ.get("BOT_PANEL_TOKEN", "").strip()
           or str(impresora._cfg().get("bot_panel_token") or "").strip())
    if tok:
        h["X-Panel-Token"] = tok
    return h


@app.get("/api/bot/rechazados")
def api_bot_rechazados():
    """Los pedidos que el POS rechazo, CON su detalle (21-jul-2026).

    Antes el POS solo mostraba 'hay N rechazados' y mandaba a Christian a releer el
    chat de WhatsApp para rearmar el pedido a mano. Ahora se trae el payload que el
    bot ya tenia guardado, para PRECARGAR la pantalla de armar pedido."""
    import urllib.request
    try:
        pet = urllib.request.Request(f"{_bot_url()}/rechazados",
                                     headers=_headers_panel_bot())
        with urllib.request.urlopen(pet, timeout=4) as r:
            return {"disponible": True, **json.loads(r.read().decode("utf-8"))}
    except Exception as e:
        return {"disponible": False, "rechazados": [], "error": str(e)}


@app.post("/api/bot/rechazados/{fid}/descartar")
def api_bot_descartar(fid: int, body: dict = None,
                      usuario: Optional[dict] = Depends(usuario_actual)):
    """Saca el aviso de la vista. Queda registrado quien lo descarto y por que:
    un aviso que se apaga sin dejar rastro es un agujero de control."""
    import urllib.request
    datos = json.dumps({"motivo": (body or {}).get("motivo", ""),
                        "usuario": (usuario or {}).get("nombre", "")}).encode("utf-8")
    pet = urllib.request.Request(f"{_bot_url()}/rechazados/{fid}/descartar",
                                 data=datos, method="POST",
                                 headers=_headers_panel_bot(
                                     {"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(pet, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"no pude avisarle al bot: {e}")


@app.get("/api/bot/pendientes")
def api_bot_pendientes():
    """Los pedidos del bot que aun no llegaron a esta cola, CON su detalle
    (29-jul-2026). Antes la pantalla solo decia "hay N pendientes, se reintentan
    solos" -- un aviso sin ninguna salida. Ahora se puede ver la conversacion,
    rearmar el pedido y cerrarlo.

    Esta llamada sale del POS hacia el bot: la direccion que funciona aunque el tunel
    este caido, que es exactamente cuando hay pendientes."""
    import urllib.request
    try:
        pet = urllib.request.Request(f"{_bot_url()}/pendientes",
                                     headers=_headers_panel_bot())
        with urllib.request.urlopen(pet, timeout=4) as r:
            return {"disponible": True, **json.loads(r.read().decode("utf-8"))}
    except Exception as e:
        return {"disponible": False, "pendientes": [], "error": str(e)}


@app.get("/api/bot/pendientes/{fid}/conversacion")
def api_bot_pendiente_conversacion(fid: int):
    """La conversacion aislada del cliente de ese pedido, para no buscarla entre las
    de todos los demas."""
    import urllib.request
    try:
        pet = urllib.request.Request(f"{_bot_url()}/pendientes/{fid}/conversacion",
                                     headers=_headers_panel_bot())
        with urllib.request.urlopen(pet, timeout=4) as r:
            return {"disponible": True, **json.loads(r.read().decode("utf-8"))}
    except Exception as e:
        return {"disponible": False, "telefono": None, "turnos": [], "error": str(e)}


@app.post("/api/bot/pendientes/{fid}/descartar")
def api_bot_cerrar_pendiente(fid: int, body: dict = None,
                             usuario: Optional[dict] = Depends(usuario_actual)):
    """Cierra el pendiente: lo llama el guardado cuando el pedido se ingreso a mano
    (motivo con el numero local) y tambien el boton de botarlo. Queda con autor."""
    import urllib.request
    datos = json.dumps({"motivo": (body or {}).get("motivo", ""),
                        "usuario": (usuario or {}).get("nombre", "")}).encode("utf-8")
    pet = urllib.request.Request(f"{_bot_url()}/pendientes/{fid}/descartar",
                                 data=datos, method="POST",
                                 headers=_headers_panel_bot(
                                     {"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(pet, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"no pude avisarle al bot: {e}")


@app.get("/api/bot/alertas")
def api_bot_alertas():
    """Avisos de sistema al jefe que WhatsApp NO pudo entregar, ni siquiera tras
    los reintentos del bot (27-jul-2026, S2 del plan: no depende de la ventana de
    24h de WhatsApp ni de que alguien este mirando el chat en ese momento)."""
    import urllib.request
    try:
        pet = urllib.request.Request(f"{_bot_url()}/alertas", headers=_headers_panel_bot())
        with urllib.request.urlopen(pet, timeout=4) as r:
            return {"disponible": True, **json.loads(r.read().decode("utf-8"))}
    except Exception as e:
        return {"disponible": False, "alertas": [], "error": str(e)}


@app.post("/api/bot/alertas/{fid}/descartar")
def api_bot_descartar_alerta(fid: int, usuario: Optional[dict] = Depends(usuario_actual)):
    import urllib.request
    pet = urllib.request.Request(f"{_bot_url()}/alertas/{fid}/descartar", method="POST",
                                 headers=_headers_panel_bot())
    try:
        with urllib.request.urlopen(pet, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"no pude avisarle al bot: {e}")


@app.get("/api/bot/casos-humanos")
def api_bot_casos_humanos():
    """Todo caso donde el bot derivo la conversacion a una persona (humano prometido,
    pedido grande, segundo pedido superpuesto, falla del cerebro) -- CON el aviso a
    los jefes ya mandado por WhatsApp, aparezca o no en su celular (15-sep-2026, caso
    real: un adulto mayor que necesitaba ayuda y el aviso nunca se leyo). Mismo patron
    que /api/bot/alertas y /api/bot/pendientes: una cola durable que Christian ve en
    esta pantalla, no depende de que revise el WhatsApp a tiempo."""
    import urllib.request
    try:
        pet = urllib.request.Request(f"{_bot_url()}/casos-humanos", headers=_headers_panel_bot())
        with urllib.request.urlopen(pet, timeout=4) as r:
            return {"disponible": True, **json.loads(r.read().decode("utf-8"))}
    except Exception as e:
        return {"disponible": False, "casos": [], "error": str(e)}


@app.post("/api/bot/casos-humanos/{fid}/descartar")
def api_bot_descartar_caso_humano(fid: int, usuario: Optional[dict] = Depends(usuario_actual)):
    import urllib.request
    datos = json.dumps({"usuario": (usuario or {}).get("nombre", "")}).encode("utf-8")
    pet = urllib.request.Request(f"{_bot_url()}/casos-humanos/{fid}/descartar",
                                 data=datos, method="POST",
                                 headers=_headers_panel_bot({"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(pet, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"no pude avisarle al bot: {e}")


@app.post("/api/pedidos/{pedido_id}/estado")
def api_estado(pedido_id: int, e: EstadoIn, usuario: Optional[dict] = Depends(usuario_actual)):
    # Solo la transicion a 'cancelado' (anular) exige permiso; las de cocina son de
    # uso constante durante el dia y no se restringen mas alla del login normal.
    if e.estado == "cancelado":
        if not usuario:
            raise HTTPException(status_code=401, detail="Debes iniciar sesión de nuevo")
        if "anular" not in (usuario.get("permisos") or []):
            raise HTTPException(status_code=403, detail="Tu perfil no tiene el permiso 'anular'")
    try:
        return NUCLEO.transicionar(pedido_id, e.estado, usuario=usuario["nombre"] if usuario else None)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@app.get("/api/pedidos/{pedido_id}/comanda")
def api_comanda(pedido_id: int):
    p = NUCLEO.obtener(pedido_id)
    if not p:
        raise HTTPException(status_code=404, detail="pedido no existe")
    return {"texto": NUCLEO.comanda_texto(pedido_id), "pedido": p}


# ===========================================================================
# Auto-test (sin navegador, sin Gemini):  python pos/cajero.py --test
# ===========================================================================
def _self_test():
    ok = []

    def chk(desc, cond):
        print(f"  [{'OK' if cond else 'FALLA'}] {desc}")
        ok.append(cond)

    print("== carta_para_ui ==")
    cats = carta_para_ui(cargar_menu())
    por_nombre = {c["nombre"]: c for c in cats}
    # 10 categorias desde 02-jul: Pollo Crispy se elimino (Nuggets/Empanaditas
    # viven desagrupados dentro de Papas Fritas; los Boxes siguen aparte)
    chk("todas las categorias presentes", len(cats) == 10)
    chk("Pollo Crispy ya no existe como categoria", "Pollo Crispy" not in por_nombre)

    def prod(cat, nom):
        return next(p for p in por_nombre[cat]["productos"] if p["nombre"] == nom)

    chk("Nuggets vive en Papas Fritas desagrupado",
        prod("Papas Fritas", "Nuggets")["desagrupar"]
        and [v["valor"] for v in prod("Papas Fritas", "Nuggets")["variantes"]] == ["6", "10"])
    chk("Empanaditas vive en Papas Fritas desagrupado",
        prod("Papas Fritas", "Empanaditas")["desagrupar"])

    esp = prod("Especiales", "Especial Kayser")
    chk("Especial Kayser ofrece churrasco+mechada",
        [v["valor"] for v in esp["variantes"]] == ["churrasco", "mechada"])
    vie = prod("Vienesas", "Vienesa Completo")
    chk("Vienesa Completo ofrece xl_30cm+normal_22cm",
        [v["valor"] for v in vie["variantes"]] == ["xl_30cm", "normal_22cm"])
    chk("Vienesa marca promo 2x", vie["promo_2x"] is True)
    papas = prod("Papas Fritas", "Papas Fritas")
    chk("Papas Fritas excluye 'individual' (null en menu)",
        [v["valor"] for v in papas["variantes"]] == ["mediana", "grande"])
    lata = prod("Bebestibles", "Lata CCU")
    # 28-jul-2026: sabores reales confirmados por Christian (P1 de
    # bot/REVISION_CHRISTIAN_20260728.md). Salen "Jugo Durazno" y "Coca Zero" (no se
    # venden) y entran "Pepsi Zero" y "Limón Soda".
    chk("Lata CCU reactivo sabores desagrupados (punto G, 02-jul): sin variantes, con variedades",
        lata["variantes"] == [] and
        lata["variedades"] == ["Pepsi", "Pepsi Zero", "Bilz", "Pap", "Kem", "Limón Soda"]
        and lata["desagrupar"] is True)
    chur = prod("Churrascos", "Churrasco Italiano")
    chk("Churrasco Italiano sin variantes pero con promo 2x",
        chur["variantes"] == [] and chur["promo_2x"] is True)

    print("== flujo cajero -> nucleo (DB temporal) ==")
    db_test = AQUI / "_test_cajero.db"
    db_test.unlink(missing_ok=True)
    n = NucleoPedidos(db_path=db_test)
    pedido = n.crear(
        canal="cajero", cliente="Mesón", modalidad="retiro", forma_pago="efectivo",
        notas="casa esquina, cuidado con el perro",   # comentario de la COMANDA
        items=[
            {"categoria": "Churrascos", "producto": "Churrasco Italiano", "variante": None,
             "cantidad": 3, "notas": "1 con el pan frío"},   # comentario del ITEM
            {"categoria": "Especiales", "producto": "Especial Kayser", "variante": "mechada", "cantidad": 1},
            {"categoria": "Bebestibles", "producto": "Lata CCU", "variante": None, "cantidad": 1, "notas": "Bilz"},
        ])
    chk("pedido cajero creado", pedido["estado"] == "recibido")
    # Lata CCU: subio de 1500 a 2000 el 08-jul-2026 y volvio a bajar a 1500 -- precio real
    # confirmado contra el POS de Christian el 03-ago-2026.
    chk("total = 13800 (3 italianos: 2x+1) + 6200 (Kayser mech) + 1500 (lata) = 21500",
        pedido["total"] == 21500)
    r = n.transicionar(pedido["id"], "confirmado")
    chk("transiciona a confirmado", r["estado"] == "confirmado")
    chk("aparece en la cola", any(x["id"] == pedido["id"] for x in n.cola()))
    comanda = n.comanda_texto(pedido["id"])
    chk("comanda muestra el sabor de la lata", "Bilz" in comanda)
    chk("comanda muestra el comentario del ITEM", "pan frío" in comanda)
    chk("comanda muestra la nota de la COMANDA", "cuidado con el perro" in comanda)
    chk("canal del pedido es 'cajero'", pedido["canal"] == "cajero")
    chk("la llave del bot existe ('whatsapp' es canal valido)", "whatsapp" in CANALES)

    print("== clientes: export para el bot (nombre registrado, 04-ago-2026) ==")
    n.guardar_cliente(telefono="912345678", nombre="Marisol", direccion="Los Aromos 456")
    n.guardar_cliente(telefono="", nombre="Sin Telefono", direccion="")
    n.guardar_cliente(telefono="900001111", nombre="", direccion="")
    export = n.listar_clientes_export()
    chk("el cliente con telefono Y nombre aparece en el export",
        any(c["telefono"] == "912345678" and c["nombre"] == "Marisol"
            and c["direccion"] == "Los Aromos 456" for c in export))
    chk("el cliente SIN telefono no aparece (el bot no tiene con que calzarlo)",
        not any(c["nombre"] == "Sin Telefono" for c in export))
    chk("el telefono SIN nombre no aparece (nada que mostrar como 'registrado')",
        not any(c["telefono"] == "900001111" for c in export))

    print("== caja: apertura / vuelto / cierre ==")
    cab = n.abrir_caja(fondo_inicial=15000, cajero="Christian")
    chk("caja abierta", n.caja_abierta() is not None)
    pc = n.crear(canal="cajero", cliente="Y", modalidad="retiro", forma_pago="efectivo",
                 items=[{"categoria": "Bebestibles", "producto": "Lata CCU", "cantidad": 1}],
                 pago_con=2500)
    chk("pedido nuevo va a la caja abierta", pc.get("caja_id") == cab["id"])
    chk("vuelto = 2500 - 1500 = 1000", pc.get("vuelto") == 1000)
    res = n.resumen_caja(cab["id"])
    chk("esperado efectivo = 15000 + 1500 = 16500 (ignora pedidos sin caja)",
        res["esperado_efectivo"] == 16500 and res["n_pedidos"] == 1)
    cierre = n.cerrar_caja(17500)
    chk("descuadre +1000 (contó 17500 vs 16500 esperado)", cierre["descuadre"] == 1000)
    if cierre.get("backup", {}).get("ok"):
        Path(cierre["backup"]["archivo"]).unlink(missing_ok=True)   # no dejar rastro en backups/
    n._con.close()
    db_test.unlink(missing_ok=True)

    print("== proxy /api/bot/conversaciones (bot NO corriendo en este test) ==")
    r = api_bot_conversaciones()
    chk("gracioso sin el bot arriba: disponible=False, no revienta",
        r["disponible"] is False and r["turnos"] == [])

    print("== S2: proxy /api/bot/alertas (bot NO corriendo en este test) ==")
    r_al = api_bot_alertas()
    chk("gracioso sin el bot arriba: disponible=False, no revienta",
        r_al["disponible"] is False and r_al["alertas"] == [])

    print("== panel de pendientes: proxies /api/bot/pendientes (bot NO corriendo) ==")
    # Estos son los que le dan salida al aviso que hasta hoy era una linea de texto
    # muerta. Con el bot caido tienen que degradar igual que los demas: la pantalla
    # muestra "sin conexion con el bot", nunca una excepcion.
    r_pend = api_bot_pendientes()
    chk("gracioso sin el bot arriba: disponible=False, no revienta",
        r_pend["disponible"] is False and r_pend["pendientes"] == [])
    r_conv = api_bot_pendiente_conversacion(1)
    chk("la conversacion tambien degrada sin reventar",
        r_conv["disponible"] is False and r_conv["turnos"] == []
        and r_conv["telefono"] is None)

    print("== 15-sep-2026: proxy /api/bot/casos-humanos (bot NO corriendo en este test) ==")
    r_ch = api_bot_casos_humanos()
    chk("gracioso sin el bot arriba: disponible=False, no revienta",
        r_ch["disponible"] is False and r_ch["casos"] == [])

    print("== candado del bot (Opcion B): solo el trafico del tunel exige token ==")
    chk("sin token configurado: TODO pasa (candado apagado, POS como hoy)",
        _candado_permite("", True, "") and _candado_permite("", False, "x"))
    chk("LAN (sin cf-connecting-ip): pasa sin token aunque el candado este puesto",
        _candado_permite("secretoXYZ", False, ""))
    chk("tunel + token correcto: pasa (es el bot)",
        _candado_permite("secretoXYZ", True, "secretoXYZ"))
    chk("tunel + token incorrecto: bloqueado (403)",
        not _candado_permite("secretoXYZ", True, "otro"))
    chk("tunel + SIN token: bloqueado (403) -- cierra el hueco de hoy",
        not _candado_permite("secretoXYZ", True, ""))

    print("== puerta unica de la API (todo cerrado por defecto) ==")
    _ses = lambda tok: {"ok": {"id": 1}}.get(tok)
    A = lambda m, r, ses="", bot="", tok="TOK": _acceso_api(m, r, ses, bot, tok, _ses)
    chk("sin sesion: /api/auditoria bloqueado", A("GET", "/api/auditoria") is None)
    chk("sin sesion: /api/ventas bloqueado", A("GET", "/api/ventas") is None)
    chk("sin sesion: crear pedido bloqueado", A("POST", "/api/pedidos") is None)
    chk("sin sesion: /wms/api/insumos bloqueado", A("GET", "/wms/api/insumos") is None)
    chk("sesion inventada: bloqueado", A("GET", "/api/ventas", ses="falsa") is None)
    chk("sesion valida: pasa", A("GET", "/api/ventas", ses="ok") == "sesion")
    chk("login es publico", A("POST", "/api/login") == "publico")
    chk("la lista de usuarios completa NO es publica", A("GET", "/api/usuarios") is None)
    chk("la lista minima del login si", A("GET", "/api/usuarios-login") == "publico")
    chk("el HTML y los estaticos son publicos", A("GET", "/") == "publico" and A("GET", "/wms/static/x.js") == "publico")
    chk("un endpoint inventado de /api/ nace cerrado", A("GET", "/api/loquesea") is None)
    chk("bot con token: puede crear pedidos", A("POST", "/api/pedidos", bot="TOK") == "bot")
    chk("bot con token: puede leer direcciones de un cliente",
        A("GET", "/api/clientes/912345678/direcciones", bot="TOK") == "bot")
    chk("bot con token NO puede leer ventas ni auditoria",
        A("GET", "/api/ventas", bot="TOK") is None and A("GET", "/api/auditoria", bot="TOK") is None)
    chk("bot con token NO puede cerrar caja", A("POST", "/api/caja/cerrar", bot="TOK") is None)
    chk("bot con token equivocado: bloqueado", A("POST", "/api/pedidos", bot="otro") is None)
    chk("sin token configurado el bot NO entra (falla cerrado)", A("POST", "/api/pedidos", bot="", tok="") is None)

    print("\n" + ("TODO OK" if all(ok) else ">>> HAY FALLAS <<<"))
    return all(ok)


# ---------------------------------------------------------------------------
# Capa 5 — WMS (inventario + finanzas) montado sobre el MISMO núcleo del POS.
# Comparte pedidos.db, login y permisos. Una venta real ("A cocina") descuenta
# stock vía el gancho _post_transicion. Fail-safe: si el WMS no carga, el POS sigue.
# El libro de caja / inventario quedan en  http://<host>:<port>/wms/
# ---------------------------------------------------------------------------
def _montar_wms():
    try:
        if getattr(sys, "frozen", False):
            wms_dir = Path(sys.executable).resolve().parent      # junto al exe (wms.html + static)
        else:
            wms_dir = AQUI.parent / "wms"
        sys.path.insert(0, str(wms_dir))
        import servidor as _wms                                   # servidor del WMS
        _wms.HTML = wms_dir / "wms.html"                          # frozen-aware para el home /wms/
        _wms.integrar(NUCLEO, static_dir=wms_dir / "static")     # MISMO núcleo/conexión
        NUCLEO._post_transicion = _wms.INV.sincronizar_estado    # venta real -> descuenta stock
        app.mount("/wms", _wms.app)
        print("  WMS (inventario + finanzas) montado en  /wms/")
    except Exception as e:
        print(f"[cajero][AVISO] no pude montar el WMS (el POS sigue funcionando): {e}")


_montar_wms()


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(0 if _self_test() else 1)
    # Levantar el servidor
    port = 8000
    # El .exe escucha SIEMPRE en la red local (celular/tablet de la misma WiFi;
    # el login con PIN protege). Antes habia que abrirlo con un .bat aparte y
    # Christian quedaba en modo solo-PC al seguir el LEEME (bug real, 22-jul).
    # En desarrollo se mantiene 127.0.0.1; --host sigue mandando en ambos casos.
    host = "0.0.0.0" if getattr(sys, "frozen", False) else "127.0.0.1"
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--host" in sys.argv:          # ej: --host 127.0.0.1 = solo este PC
        host = sys.argv[sys.argv.index("--host") + 1]
    import uvicorn
    print(f"  POS {MENU['local']['nombre']} — Cajero")
    if host == "0.0.0.0":
        def _ip_local():
            """Mejor esfuerzo: IP de este PC en la WiFi (solo para mostrarla)."""
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    s.connect(("8.8.8.8", 80))   # no envia nada; solo elige interfaz
                    return s.getsockname()[0]
                finally:
                    s.close()
            except OSError:
                return None
        ip = _ip_local()
        print(f"    En este PC:        http://127.0.0.1:{port}")
        print(f"    Desde el celular:  http://{ip or '<IP-de-este-PC>'}:{port}   (misma WiFi)")
    else:
        print(f"    En este PC:        http://{host}:{port}")
    print("  (cierra esta ventana para apagar el POS)")
    if "--open" in sys.argv or getattr(sys, "frozen", False):  # el .exe abre el navegador solo
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host=host, port=port)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sobre_veredicto.py — el motor de papel del Mesón del Patata (19-ago-2026).
================================================================================

POR QUE EXISTE
--------------
Medido el 18-ago leyendo `curador.py`: la nota que Christian escribe al marcar un
caso muere en `curaciones.db`. No hay una sola linea que la lleve a lo que el bot
aprendio; el unico camino era un `<textarea>` con las 451 lineas crudas. O sea que
el problema nunca fue que mandara cualquier cosa: es que no mandaba nada.

Este modulo es el eslabon que faltaba. Arma **el sobre** (lo que Christian copia y
pega en el Curador, su proyecto de ChatGPT) y lee **el veredicto** que el Curador
devuelve, con todos los candados puestos ANTES de tocar una sola letra de lo que el
bot aprendio.

El contrato completo, con ejemplos, esta en CONTRATO_SOBRE_Y_VEREDICTO.md. Si los
dos no dicen lo mismo, manda el contrato: este archivo lo implementa, no lo define.

POR QUE UN MODULO APARTE, Y NO ADENTRO DE curador.py
----------------------------------------------------
Porque asi se puede probar entero sin levantar el servidor, sin base de datos y sin
tocar la API (`python sobre_veredicto.py --autotest`). Es la misma decision que se
tomo con `candado_banco.py`: la logica que decide si algo entra a produccion se
prueba gratis y en segundos, o termina sin probarse.

LO QUE ESTE MODULO NO HACE, A PROPOSITO
---------------------------------------
No escribe archivos, no corre el banco, no habla con git y no sabe que existe una
pantalla. Recibe texto y devuelve texto. Quien decide aplicar es el Meson, despues
de que el banco diga verde.
"""

import argparse
import hashlib
import re
import sys
from datetime import datetime

VERSION_CONTRATO = "1"
MAX_CAMBIOS = 3

ABRE_SOBRE = "=== SOBRE DEL MESON DEL PATATA ==="
ABRE_VEREDICTO = "=== VEREDICTO DEL CURADOR ==="
CIERRA_VEREDICTO = "=== FIN DEL VEREDICTO ==="

DECISIONES = ("CAMBIAR", "NO_CAMBIAR", "PARA_GONZALO")

# ===========================================================================
# Jerga y voseo: el mismo candado que ya vive en banco_pruebas.py (JERGA_PALABRAS),
# pero aplicado ACA -- antes de tocar el archivo, no despues de pushear y esperar
# a que el banco lo detecte en el CI. Lista propia a proposito: este modulo no
# importa nada fuera de la libreria estandar (ver docstring), y son diez palabras.
# ===========================================================================
JERGA_PALABRAS = ("cachai", "cachái", "querís", "queris", "tenís", "po", "bacan",
                  "bacán", "filo", "weon", "weón", "fome",
                  "dale", "tenés", "tenes", "querés", "queres", "che")
JERGA_FRASES = ("al tiro", "ya po", "si po", "la raja", "mira vos")


def _jerga_en(texto):
    """Los modismos/voseo que trae un texto. Lista vacia = habla neutro."""
    t = (texto or "").lower()
    hallados = [p for p in JERGA_PALABRAS if re.search(rf"\b{re.escape(p)}\b", t)]
    hallados += [f for f in JERGA_FRASES if f in t]
    return sorted(set(hallados))


class ErrorDeVeredicto(Exception):
    """Un veredicto que no se puede aplicar. El mensaje lo lee CHRISTIAN, asi que se
    escribe en criollo y termina diciendo que hacer -- un error que solo describe la
    falla lo deja parado."""


# ===========================================================================
# Huella: la que detecta un veredicto que llego tarde
# ===========================================================================
def _a_lf(texto):
    return (texto or "").replace("\r\n", "\n").replace("\r", "\n")


def huella(texto):
    """Los primeros 8 del SHA-1, calculado siempre sobre LF.

    El archivo local es CRLF (Windows, el .exe de Christian) y la copia que viaja a
    Railway es LF. Sin normalizar, el MISMO contenido daria dos huellas distintas y
    todos los veredictos se rechazarian por 'llegaste tarde'."""
    return hashlib.sha1(_a_lf(texto).encode("utf-8")).hexdigest()[:8]


# ===========================================================================
# El sobre
# ===========================================================================
_TELEFONO = re.compile(r"\d{8,}")


def _sin_telefonos(texto):
    """El sobre sale del local y se pega en ChatGPT: no se lleva numeros de clientes.
    8 digitos seguidos es un telefono; los precios del menu ($5.000) no llegan."""
    return _TELEFONO.sub("(numero)", texto or "")


def _turno_a_lineas(turno, es_el_fallo):
    quien = "cliente" if turno.get("rol") == "usuario" else "bot"
    hora = (turno.get("creado_en") or "")[11:16]
    marca = ">>> " if es_el_fallo else "    "
    sangria = " " * (4 + 8 + 5 + 2)
    partes = _sin_telefonos(turno.get("texto") or "").split("\n")
    salida = [f"{marca}{quien:<8}{hora:<5}  {partes[0]}"]
    salida += [f"{sangria}{p}" for p in partes[1:]]
    return salida


def armar_sobre(caso, turnos, turno_fallo_id, nota, deberia, instrucciones, ahora=None):
    """El texto que Christian copia con un boton y pega en el Curador.

    `turnos` son dicts con id/rol/texto/creado_en, en orden. `instrucciones` es el
    contenido vigente de lo que el bot aprendio (system_prompt.md)."""
    h = huella(instrucciones)
    cuando = (ahora or datetime.now()).strftime("%Y-%m-%d %H:%M")
    lineas_instr = len(_a_lf(instrucciones).split("\n"))

    conversacion = []
    for t in turnos:
        conversacion += _turno_a_lineas(t, t.get("id") == turno_fallo_id)

    return "\n".join([
        ABRE_SOBRE,
        f"version: {VERSION_CONTRATO}",
        f"caso: {caso}",
        f"fecha: {cuando}",
        f"huella: {h}",
        f"lineas: {lineas_instr}",
        "",
        "[1] LO QUE PASO",
        "(el mensaje marcado con >>> es el que Christian señaló como el error)",
        "",
        *conversacion,
        "",
        "[2] LO QUE ESTUVO MAL (lo escribió Christian)",
        (nota or "").strip() or "(no lo escribió)",
        "",
        "[3] LO QUE TENDRIA QUE HABER CONTESTADO",
        (deberia or "").strip() or "(no lo escribió)",
        "",
        "[4] LO QUE EL BOT APRENDIO HASTA HOY",
        _a_lf(instrucciones).rstrip("\n"),
        "",
        "[5] LO QUE TIENES QUE DEVOLVER",
        _PLANTILLA_VEREDICTO,
        "",
        f"=== FIN DEL SOBRE · huella {h} ===",
        "",
    ])


_PLANTILLA_VEREDICTO = f"""Un solo bloque con esta forma exacta. Lo que escribas fuera de las
marcas se ignora, asi que puedes explicarte antes o despues todo lo que quieras.

{ABRE_VEREDICTO}
version: {VERSION_CONTRATO}
caso: (el mismo numero de arriba)
huella: (la misma huella de arriba)
decision: CAMBIAR | NO_CAMBIAR | PARA_GONZALO

motivo: (una sola linea, en criollo, la lee Christian)

CAMBIO 1
--- BUSCAR ---
(el texto actual, copiado EXACTO, y que aparezca una sola vez)
--- REEMPLAZAR POR ---
(el texto nuevo; vacio = borrar)
--- FIN DEL CAMBIO ---

balance: +N lineas / -N lineas
riesgo: (una linea)
{CIERRA_VEREDICTO}"""


# ===========================================================================
# El veredicto: leerlo
# ===========================================================================
_ENCABEZADO = re.compile(r"^\s*([a-zA-Z_]+)\s*:\s*(.*)$")
_CAMBIO = re.compile(r"^\s*CAMBIO\b", re.IGNORECASE)
_BUSCAR = re.compile(r"^\s*-{2,}\s*BUSCAR\s*-{2,}\s*$", re.IGNORECASE)
_REEMPLAZAR = re.compile(r"^\s*-{2,}\s*REEMPLAZAR POR\s*-{2,}\s*$", re.IGNORECASE)
_FIN_CAMBIO = re.compile(r"^\s*-{2,}\s*FIN DEL CAMBIO\s*-{2,}\s*$", re.IGNORECASE)
_CERCA = re.compile(r"^\s*```")


def leer_veredicto(pegado):
    """Del texto que Christian pego (la respuesta ENTERA del Curador) saca el bloque
    del veredicto y lo devuelve como dict. Levanta ErrorDeVeredicto con el mensaje ya
    escrito para el."""
    texto = _a_lf(pegado)
    if ABRE_VEREDICTO not in texto:
        raise ErrorDeVeredicto(
            "Esto no parece la respuesta del Curador. Copia la respuesta completa, "
            "desde el principio, y pégala de nuevo.")
    cuerpo = texto.split(ABRE_VEREDICTO, 1)[1]
    if CIERRA_VEREDICTO not in cuerpo:
        raise ErrorDeVeredicto(
            "La respuesta del Curador llegó cortada: le falta el final. Copiala "
            "completa y pégala de nuevo.")
    cuerpo = cuerpo.split(CIERRA_VEREDICTO, 1)[0]

    # ChatGPT casi siempre envuelve esto en ``` -- una linea de cerca no es contenido.
    lineas = [l for l in cuerpo.split("\n") if not _CERCA.match(l)]

    datos = {"cambios": []}
    i = 0
    while i < len(lineas) and not _CAMBIO.match(lineas[i]):
        m = _ENCABEZADO.match(lineas[i])
        if m:
            datos[m.group(1).lower()] = m.group(2).strip()
        i += 1

    while i < len(lineas):
        if not _CAMBIO.match(lineas[i]):
            m = _ENCABEZADO.match(lineas[i])
            if m and m.group(1).lower() in ("balance", "riesgo"):
                datos[m.group(1).lower()] = m.group(2).strip()
            i += 1
            continue
        i += 1
        while i < len(lineas) and not _BUSCAR.match(lineas[i]):
            if _CAMBIO.match(lineas[i]):
                break
            i += 1
        if i >= len(lineas) or not _BUSCAR.match(lineas[i]):
            raise ErrorDeVeredicto(
                "Un cambio del Curador vino sin la parte de BUSCAR. Pídele que "
                "devuelva el veredicto con el formato completo.")
        i += 1
        buscar = []
        while i < len(lineas) and not _REEMPLAZAR.match(lineas[i]):
            buscar.append(lineas[i])
            i += 1
        if i >= len(lineas):
            raise ErrorDeVeredicto(
                "Un cambio del Curador vino sin la parte de REEMPLAZAR POR. Pídele "
                "que devuelva el veredicto con el formato completo.")
        i += 1
        reemplazo = []
        while i < len(lineas) and not _FIN_CAMBIO.match(lineas[i]) and not _CAMBIO.match(lineas[i]):
            reemplazo.append(lineas[i])
            i += 1
        if i < len(lineas) and _FIN_CAMBIO.match(lineas[i]):
            i += 1
        datos["cambios"].append({"buscar": "\n".join(buscar).strip("\n"),
                                 "reemplazo": "\n".join(reemplazo).strip("\n")})
    return datos


def validar_veredicto(veredicto, caso, huella_actual):
    """Los candados que NO dependen del contenido de lo que el bot aprendio."""
    if veredicto.get("version") != VERSION_CONTRATO:
        raise ErrorDeVeredicto(
            "El Curador contestó con un formato viejo. Avísale a Gonzalo.")
    if str(veredicto.get("caso", "")).strip() != str(caso):
        raise ErrorDeVeredicto(
            f"Esta respuesta es de otro caso (dice {veredicto.get('caso')} y este es "
            f"el {caso}). Pega la respuesta del caso que tienes abierto.")
    if (veredicto.get("huella") or "").strip().lower() != str(huella_actual).lower():
        raise ErrorDeVeredicto(
            "El bot aprendió otra cosa desde que armaste este sobre, así que esta "
            "respuesta ya no calza. Arma el sobre de nuevo y pásalo otra vez.")
    decision = (veredicto.get("decision") or "").strip().upper()
    if decision not in DECISIONES:
        raise ErrorDeVeredicto(
            "El Curador no dijo claramente qué hacer. Pídele que termine con una "
            "decisión: CAMBIAR, NO_CAMBIAR o PARA_GONZALO.")
    cambios = veredicto.get("cambios") or []
    if decision == "CAMBIAR":
        if not cambios:
            raise ErrorDeVeredicto(
                "El Curador dijo que hay que cambiar algo pero no dijo qué. Pídele "
                "el cambio con el texto exacto.")
        if len(cambios) > MAX_CAMBIOS:
            raise ErrorDeVeredicto(
                f"El Curador mandó {len(cambios)} cambios de una vez y el máximo es "
                f"{MAX_CAMBIOS}. Un caso se arregla con el cambio más chico posible: "
                "pideselo de nuevo.")
        for n, c in enumerate(cambios, 1):
            if not c["buscar"].strip():
                raise ErrorDeVeredicto(
                    f"El cambio {n} no dice qué texto reemplazar. Pídele el veredicto "
                    "de nuevo.")
            hallados = _jerga_en(c["reemplazo"])
            if hallados:
                raise ErrorDeVeredicto(
                    f"El cambio {n} quedó con modismo o voseo ({', '.join(hallados)}): "
                    "el bot habla en español neutro, nunca voseo. Pídele al Curador que "
                    "reescriba ese cambio neutro.")
    return decision


# ===========================================================================
# El veredicto: aplicarlo
# ===========================================================================
_COMILLAS = {"‘": "'", "’": "'", "“": '"', "”": '"'}


def _suave(linea):
    """La comparacion tolerante: espacios al final y comillas curvas. ChatGPT las
    cambia solo, y eso no puede costar un veredicto bueno."""
    for curva, recta in _COMILLAS.items():
        linea = linea.replace(curva, recta)
    return linea.rstrip()


def _ventanas(hay, needle, norm):
    h = [norm(l) for l in hay]
    n = [norm(l) for l in needle]
    return [i for i in range(len(h) - len(n) + 1) if h[i:i + len(n)] == n]


def aplicar_cambios(instrucciones, cambios):
    """Devuelve (texto_nuevo, detalle). Todo en LF: quien escribe el archivo decide
    el fin de linea, no este modulo (bug real del 04-ago: forzar LF acá convirtió el
    archivo local de Windows sin querer).

    Los cambios se aplican EN ORDEN, cada uno sobre el resultado del anterior: si el
    Curador manda dos cambios que se pisan, el segundo no va a encontrar su ancla y
    todo el veredicto se rechaza -- que es lo correcto, no aplicar la mitad."""
    texto = _a_lf(instrucciones)
    detalle = []
    for n, c in enumerate(cambios, 1):
        buscar = _a_lf(c["buscar"])
        reemplazo = _a_lf(c["reemplazo"])
        apariciones = texto.count(buscar)
        if apariciones > 1:
            raise ErrorDeVeredicto(
                f"El texto del cambio {n} aparece {apariciones} veces, así que no se "
                "sabe cuál cambiar. Pídele al Curador que incluya la línea de arriba "
                "o la de abajo para que sea único.")
        if apariciones == 1:
            texto = _reemplazo_exacto(texto, buscar, reemplazo)
            detalle.append(f"cambio {n}: aplicado")
            continue
        # Segunda oportunidad: por lineas, ignorando espacios finales y comillas curvas.
        hay = texto.split("\n")
        needle = buscar.split("\n")
        pos = _ventanas(hay, needle, _suave)
        if len(pos) > 1:
            raise ErrorDeVeredicto(
                f"El texto del cambio {n} aparece {len(pos)} veces, así que no se sabe "
                "cuál cambiar. Pídele al Curador que lo haga único.")
        if not pos:
            raise ErrorDeVeredicto(
                f"El cambio {n} cita un texto que no existe tal cual en lo que el bot "
                "aprendió. Pídele al Curador que lo copie exacto, con tildes y "
                "mayúsculas.")
        i = pos[0]
        nuevas = reemplazo.split("\n") if reemplazo else []
        texto = "\n".join(hay[:i] + nuevas + hay[i + len(needle):])
        detalle.append(f"cambio {n}: aplicado (con tolerancia de espacios)")

    if not texto.strip():
        raise ErrorDeVeredicto(
            "Con ese cambio, el bot se quedaría sin instrucciones. No se aplica.")
    return texto, detalle


def _reemplazo_exacto(texto, buscar, reemplazo):
    pos = texto.index(buscar)
    fin = pos + len(buscar)
    arranca_linea = pos == 0 or texto[pos - 1] == "\n"
    termina_linea = fin == len(texto) or texto[fin] == "\n"
    if not reemplazo and arranca_linea and termina_linea:
        # Borrar lineas enteras no puede dejar una linea vacia donde estaban.
        corte = fin + 1 if fin < len(texto) else fin
        return texto[:pos] + texto[corte:]
    return texto[:pos] + reemplazo + texto[fin:]


def resumen_para_christian(instrucciones_antes, instrucciones_despues):
    """Lo que ve en pantalla antes de que el banco corra: cuanto se movió, sin una
    sola palabra de maquinaria."""
    antes = len(_a_lf(instrucciones_antes).split("\n"))
    despues = len(_a_lf(instrucciones_despues).split("\n"))
    if despues == antes:
        return "Queda del mismo largo."
    if despues < antes:
        return f"Le saca {antes - despues} línea(s): queda más corto."
    return f"Le agrega {despues - antes} línea(s)."


# ===========================================================================
# Autotest -- todo el motor, sin API, sin base y sin servidor
# ===========================================================================
INSTRUCCIONES_DE_PRUEBA = (
    "# Instrucciones\n"
    "1. Saluda con calidez.\n"
    "2. Si el cliente está registrado, usa su dirección guardada.\n"
    "3. Cobra el envío según la zona.\n"
    "4. Saluda con calidez.\n"
)


def _veredicto_de_prueba(caso, h, decision="CAMBIAR", cambios=None, version="1"):
    partes = [ABRE_VEREDICTO, f"version: {version}", f"caso: {caso}", f"huella: {h}",
              f"decision: {decision}", "", "motivo: una linea", ""]
    for n, (b, r) in enumerate(cambios or [], 1):
        partes += [f"CAMBIO {n}", "--- BUSCAR ---", b, "--- REEMPLAZAR POR ---", r,
                   "--- FIN DEL CAMBIO ---", ""]
    partes += ["balance: +0 lineas / -0 lineas", "riesgo: ninguno", CIERRA_VEREDICTO]
    return "\n".join(partes)


def autotest():
    fallas = []

    def chk(nombre, condicion):
        print(f"  [{'OK' if condicion else 'FALLA'}] {nombre}")
        if not condicion:
            fallas.append(nombre)

    def espera_error(nombre, fn, trozo=""):
        try:
            fn()
            chk(nombre + " (tenía que fallar)", False)
        except ErrorDeVeredicto as e:
            chk(f"{nombre} -> {str(e)[:60]}...", trozo.lower() in str(e).lower())

    h = huella(INSTRUCCIONES_DE_PRUEBA)

    print("== la huella ==")
    chk("CRLF y LF con el mismo contenido dan la MISMA huella",
        huella("a\r\nb") == huella("a\nb"))
    chk("un cambio de una letra cambia la huella",
        huella("a\nb") != huella("a\nc"))

    print("\n== el sobre ==")
    turnos = [{"id": 1, "rol": "usuario", "texto": "hola, soy el 56947712607",
               "creado_en": "2026-08-18T22:51:00"},
              {"id": 2, "rol": "asistente", "texto": "Hola!\nDime tu pedido",
               "creado_en": "2026-08-18T22:51:30"}]
    sobre = armar_sobre(12, turnos, 2, "no preguntó la dirección", "",
                        INSTRUCCIONES_DE_PRUEBA,
                        ahora=datetime(2026, 8, 19, 8, 20))
    chk("abre y cierra con la misma huella",
        sobre.startswith(ABRE_SOBRE) and sobre.count(h) >= 2)
    chk("marca con >>> el turno que Christian señaló",
        any(l.startswith(">>>") and "bot" in l for l in sobre.split("\n")))
    chk("no se lleva el teléfono del cliente",
        "56947712607" not in sobre and "(numero)" in sobre)
    chk("lleva adentro lo que el bot aprendió",
        "Cobra el envío según la zona." in sobre)
    chk("le dice al Curador qué formato devolver", ABRE_VEREDICTO in sobre)

    print("\n== leer el veredicto ==")
    v = leer_veredicto("bla bla\n" + _veredicto_de_prueba(12, h, cambios=[("a", "b")])
                       + "\nchau")
    chk("ignora lo que venga antes y después", v["caso"] == "12" and v["huella"] == h)
    chk("lee el cambio entero", v["cambios"] == [{"buscar": "a", "reemplazo": "b"}])
    dentro_de_cerca = "```\n" + _veredicto_de_prueba(12, h, cambios=[("a", "b")]) + "\n```"
    chk("sobrevive a que ChatGPT lo envuelva en ```",
        leer_veredicto(dentro_de_cerca)["cambios"][0]["buscar"] == "a")
    espera_error("un texto cualquiera no es un veredicto",
                 lambda: leer_veredicto("gracias, ahí te lo dejo"), "no parece")
    espera_error("un veredicto cortado se rechaza",
                 lambda: leer_veredicto(ABRE_VEREDICTO + "\nversion: 1\ncaso: 12"),
                 "cortada")

    print("\n== validar ==")
    ok = leer_veredicto(_veredicto_de_prueba(12, h, cambios=[("a", "b")]))
    chk("un veredicto sano valida", validar_veredicto(ok, 12, h) == "CAMBIAR")
    espera_error("un veredicto de otro caso se rechaza",
                 lambda: validar_veredicto(
                     leer_veredicto(_veredicto_de_prueba(9, h, cambios=[("a", "b")])), 12, h),
                 "otro caso")
    espera_error("un veredicto contra una versión vieja se rechaza",
                 lambda: validar_veredicto(
                     leer_veredicto(_veredicto_de_prueba(12, "ffffffff", cambios=[("a", "b")])),
                     12, h),
                 "aprendió otra cosa")
    espera_error("cuatro cambios de una vez se rechazan",
                 lambda: validar_veredicto(
                     leer_veredicto(_veredicto_de_prueba(
                         12, h, cambios=[("a", "b")] * 4)), 12, h),
                 "máximo")
    sin_cambios = leer_veredicto(_veredicto_de_prueba(12, h, decision="PARA_GONZALO"))
    chk("PARA_GONZALO no necesita cambios",
        validar_veredicto(sin_cambios, 12, h) == "PARA_GONZALO")
    espera_error("un cambio con voseo/modismo se rechaza",
                 lambda: validar_veredicto(
                     leer_veredicto(_veredicto_de_prueba(
                         12, h, cambios=[("a", "ya po, cachai que tenís que confirmar")])),
                     12, h),
                 "voseo")

    print("\n== aplicar ==")
    nuevo, _ = aplicar_cambios(INSTRUCCIONES_DE_PRUEBA, [
        {"buscar": "2. Si el cliente está registrado, usa su dirección guardada.",
         "reemplazo": "2. Si el cliente está registrado, ofrece su dirección y espera "
                      "que la confirme."}])
    chk("reemplaza el texto exacto", "espera que la confirme" in nuevo)
    chk("no toca el resto", "3. Cobra el envío según la zona." in nuevo)
    borrado, _ = aplicar_cambios(INSTRUCCIONES_DE_PRUEBA, [
        {"buscar": "3. Cobra el envío según la zona.", "reemplazo": ""}])
    chk("borrar una línea no deja un hueco vacío",
        "3. Cobra" not in borrado and "\n\n" not in borrado)
    espera_error("un texto que aparece dos veces se rechaza",
                 lambda: aplicar_cambios(INSTRUCCIONES_DE_PRUEBA, [
                     {"buscar": "1. Saluda con calidez.".replace("1. ", ""),
                      "reemplazo": "x"}]),
                 "veces")
    espera_error("un texto que no existe se rechaza",
                 lambda: aplicar_cambios(INSTRUCCIONES_DE_PRUEBA, [
                     {"buscar": "5. Baila cumbia.", "reemplazo": "x"}]),
                 "no existe")
    con_comillas, _ = aplicar_cambios(
        "Dice “hola” y espera.  \n",
        [{"buscar": 'Dice "hola" y espera.', "reemplazo": "Dice hola."}])
    chk("las comillas curvas de ChatGPT no cuestan un veredicto bueno",
        con_comillas.strip() == "Dice hola.")
    dos, _ = aplicar_cambios(INSTRUCCIONES_DE_PRUEBA, [
        {"buscar": "3. Cobra el envío según la zona.", "reemplazo": "3. Envío gratis."},
        {"buscar": "3. Envío gratis.", "reemplazo": "3. Envío a $2.000."}])
    chk("dos cambios se aplican en orden, uno sobre el otro",
        "3. Envío a $2.000." in dos)
    espera_error("dejar al bot sin instrucciones se rechaza",
                 lambda: aplicar_cambios("todo\n", [{"buscar": "todo", "reemplazo": ""}]),
                 "sin instrucciones")

    print("\n== lo que ve Christian ==")
    chk("dice en criollo que quedó más corto",
        "más corto" in resumen_para_christian(INSTRUCCIONES_DE_PRUEBA, borrado))

    print("\n" + "=" * 72)
    if fallas:
        print(f"{len(fallas)} FALLA(S): " + ", ".join(fallas))
        return 1
    print("todos los candados del motor de papel, en verde.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Motor del sobre y el veredicto.")
    ap.add_argument("--autotest", action="store_true",
                    help="corre todos los candados sin tocar la API ni la base")
    args = ap.parse_args()
    if args.autotest:
        sys.exit(autotest())
    ap.print_help()

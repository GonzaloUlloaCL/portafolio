#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
candado_banco.py — el candado que le falta al canal del prompt.
================================================================================

POR QUE EXISTE
--------------
`webhook.py --test` (545 candados) corre con un cerebro FALSO: no lee
`system_prompt.md`. Un cambio de prompt lo pasa SIEMPRE, aunque le borres el
Bloque 1 entero. Hasta hoy eso significaba que el canal del prompt entraba a
produccion sin ninguna medicion: lo unico que el CI verificaba de los .md era
que siguieran siendo UTF-8 valido.

Este script conecta `banco_pruebas.py` (los escenarios contra el modelo REAL) al
CI, y traduce su numero a una decision de deploy.

LA REGLA, Y POR QUE NO ES "EL PUNTAJE BAJO"
-------------------------------------------
El banco tiene ruido medido: el 12-ago, dos tandas de 3 corridas con el MISMO
prompt dieron 12/14/13 y 14/14/13. Un empeoramiento del 15% no se distingue del
ruido. Por eso el candado NO mira el total.

Mira los FIRMES: los escenarios que pasan en TODAS las corridas de la linea
base. Que un escenario firme falle en TODAS las corridas de ahora es una senal
mucho mas fuerte que un total que baja en 1.

    ROJO     un firme de la base falla en TODAS las corridas de ahora, y falla
             en al menos una corrida LIMPIA (sin la cuota de la API de por medio)
             -> exit 1, el CI queda rojo, Railway no despliega (Wait for CI)
    AMARILLO todo lo dudoso: firmes que se aflojaron, o que cayeron pero sin
             ninguna corrida limpia que lo confirme
             -> exit 0 con aviso ruidoso: despliega, pero queda en el registro
    VERDE    ningun firme se movio

QUE NO HACE, A PROPOSITO
------------------------
No dice "este prompt es mejor". No puede: para eso hace falta que la medicion
sea repetible, que es el pendiente #4. Esto es un detector de catastrofe, no un
juez de calidad. Sirve para que Christian pueda tocar el prompt solo sin que un
error grande llegue a los clientes, no para elegir entre dos redacciones.

QUE NO HAY QUE PONER ACA (para no volver a ponerlo): un rojo cuando la API de
OpenAI falla y la corrida no se puede comparar. Es tentador ("nada sin medir a
produccion"), pero repite el error del candado de git del 07-ago: una traba sin
salida, que deja a Christian esperando horas por algo que no depende de el ni
puede arreglar. Por eso un rojo exige una corrida limpia que lo respalde. La red
que queda debajo es que el canal del prompt es reversible en un clic.

    python bot/candado_banco.py --base bot/banco_linea_base_20260818.json
    python bot/candado_banco.py --base ... --repeticiones 2   # mas rapido, mas ruido
    python bot/candado_banco.py --autotest                    # sin tocar la API
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))


def clasificar(corridas):
    """De una lista de corridas a {id: [ok, ok, ...]}, y de ahi a firmes/inestables/rotos."""
    por_id = {}
    for c in corridas:
        for r in c["escenarios"]:
            por_id.setdefault(r["id"], []).append(r["ok"])
    return {
        "por_id": por_id,
        "firmes": sorted(i for i, oks in por_id.items() if all(oks)),
        "inestables": sorted(i for i, oks in por_id.items() if any(oks) and not all(oks)),
        "rotos": sorted(i for i, oks in por_id.items() if not any(oks)),
    }


def decidir(firmes, corridas):
    """(firmes de la linea base, corridas de ahora) -> veredicto.

    Separada de main() a proposito: es TODA la logica que decide si un deploy sale
    o no, y asi se puede probar entera sin gastar una sola llamada al modelo
    (`--autotest`). Devuelve (estado, detalle) con estado en rojo/amarillo/verde.
    """
    a = clasificar(corridas)
    firmes = list(firmes)
    limpias = [c for c in corridas if c.get("limpia")]

    # Un ROJO tiene que ser una afirmacion segura: el escenario falla en TODAS las
    # corridas Y falla en al menos una corrida limpia. Sin la segunda condicion, una
    # tarde mala de la API traba el deploy por algo que nadie puede arreglar; sin la
    # primera, el ruido normal del banco daria rojos falsos todo el tiempo.
    fallo_en_limpia = {r["id"] for c in limpias for r in c["escenarios"] if not r["ok"]}
    caidos = [i for i in firmes if i in a["rotos"] and i in fallo_en_limpia]
    caidos_sin_medir = [i for i in firmes if i in a["rotos"] and i not in fallo_en_limpia]
    flojos = [i for i in firmes if i in a["inestables"]]

    detalle = {"caidos": caidos, "caidos_sin_medir": caidos_sin_medir, "flojos": flojos,
               "limpias": len(limpias), "corridas": len(corridas), "por_id": a["por_id"]}
    if caidos:
        return "rojo", detalle
    if caidos_sin_medir or flojos or not limpias:
        return "amarillo", detalle
    return "verde", detalle


def imprimir_veredicto(estado, d, firmes):
    print("\n" + "=" * 72)
    print(f"corridas limpias: {d['limpias']} de {d['corridas']}")
    if estado == "rojo":
        print(f"[ROJO] {len(d['caidos'])} escenario(s) que pasaban SIEMPRE ahora fallan SIEMPRE:")
        for i in d["caidos"]:
            print(f"     {i}")
        print("   El deploy queda detenido. El bot sigue atendiendo con el prompt anterior.")
        print("=" * 72)
        print(f"::error::regresion del prompt: {', '.join(d['caidos'])}")
        return
    if estado == "amarillo":
        if d["caidos_sin_medir"]:
            print(f"[AMARILLO] {len(d['caidos_sin_medir'])} firme(s) fallaron en todas las corridas,")
            print(f"   pero NINGUNA fue limpia: {', '.join(d['caidos_sin_medir'])}")
            print("   Puede ser regresion real o puede ser la cuota de la API. No se detiene el")
            print("   deploy (ver el encabezado), pero esto hay que mirarlo a mano.")
        elif d["flojos"]:
            print(f"[AMARILLO] {len(d['flojos'])} escenario(s) firmes se volvieron inestables:")
            for i in d["flojos"]:
                oks = d["por_id"][i]
                print(f"     {i} ({sum(oks)}/{len(oks)} corridas)")
            print("   No alcanza para detener el deploy, pero queda anotado.")
        else:
            print("[AMARILLO] ninguna corrida salio limpia: este deploy va SIN medir.")
        print("=" * 72)
        return
    print(f"[VERDE] los {len(firmes)} escenarios firmes siguen firmes.")
    print("=" * 72)


# ===========================================================================
# Autotest -- toda la logica de decision, sin tocar la API
# ===========================================================================
def _corrida(resultados, limpia=True):
    """{'retiro_simple': True, ...} -> una corrida con la forma que usa el banco."""
    return {"limpia": limpia,
            "escenarios": [{"id": i, "ok": ok} for i, ok in resultados.items()]}


def autotest():
    fallas = []

    def check(nombre, esperado, firmes, corridas):
        estado, _ = decidir(firmes, corridas)
        ok = estado == esperado
        print(f"  [{'OK' if ok else 'FALLA'}] {nombre}: esperado {esperado}, dio {estado}")
        if not ok:
            fallas.append(nombre)

    F = ["retiro_simple", "promo_2x_mixta"]

    check("todo pasa -> verde", "verde", F,
          [_corrida({"retiro_simple": True, "promo_2x_mixta": True}),
           _corrida({"retiro_simple": True, "promo_2x_mixta": True})])

    check("un firme cae en todas, con corrida limpia -> rojo", "rojo", F,
          [_corrida({"retiro_simple": False, "promo_2x_mixta": True}),
           _corrida({"retiro_simple": False, "promo_2x_mixta": True})])

    check("un firme cae en todas, NINGUNA limpia -> amarillo", "amarillo", F,
          [_corrida({"retiro_simple": False, "promo_2x_mixta": True}, limpia=False),
           _corrida({"retiro_simple": False, "promo_2x_mixta": True}, limpia=False)])

    check("cae en todas; la unica limpia tambien lo ve fallar -> rojo", "rojo", F,
          [_corrida({"retiro_simple": False, "promo_2x_mixta": True}, limpia=False),
           _corrida({"retiro_simple": False, "promo_2x_mixta": True}, limpia=True)])

    check("un firme se afloja (pasa a veces) -> amarillo", "amarillo", F,
          [_corrida({"retiro_simple": True, "promo_2x_mixta": True}),
           _corrida({"retiro_simple": False, "promo_2x_mixta": True})])

    # El ruido del banco (spread de 2 en 3 corridas, medido el 12-ago) vive aca: un
    # escenario que la base ya daba por INESTABLE no puede detener ningun deploy.
    check("cae un escenario que no era firme -> verde", "verde", ["promo_2x_mixta"],
          [_corrida({"retiro_simple": False, "promo_2x_mixta": True}),
           _corrida({"retiro_simple": False, "promo_2x_mixta": True})])

    check("todas las corridas sucias, pero todo pasa -> amarillo", "amarillo", F,
          [_corrida({"retiro_simple": True, "promo_2x_mixta": True}, limpia=False)])

    check("dos firmes caen a la vez -> rojo", "rojo", F,
          [_corrida({"retiro_simple": False, "promo_2x_mixta": False})])

    print(f"\n{'TODO OK' if not fallas else 'FALLAN: ' + ', '.join(fallas)} "
          f"({8 - len(fallas)}/8)")
    return 1 if fallas else 0


def main():
    ap = argparse.ArgumentParser(description="Candado de regresion del prompt para el CI.")
    ap.add_argument("--base", help="json de linea base (lo genera banco_pruebas.py --json)")
    ap.add_argument("--repeticiones", type=int, default=3)
    ap.add_argument("--pausa", type=int, default=20)
    ap.add_argument("--json", default=None, help="guarda la corrida de ahora")
    ap.add_argument("--autotest", action="store_true",
                    help="prueba la logica de decision sin tocar la API")
    args = ap.parse_args()

    if args.autotest:
        print("== candado_banco.py: logica de decision ==")
        return autotest()

    if not args.base:
        print("::error::hace falta --base (o --autotest)")
        return 2

    base_path = Path(args.base)
    if not base_path.exists():
        print(f"::error::no encontre la linea base: {base_path}")
        return 1
    base = json.loads(base_path.read_text(encoding="utf-8"))
    base_corridas = base["corridas"]

    # La base y la corrida de ahora tienen que medir LO MISMO. Comparar contra una
    # base tomada con otro modelo (o con la temperatura de produccion) da un numero
    # que parece valido y no lo es: es la trampa que ya documenta banco_pruebas.py.
    modelo = base_corridas[0]["modelo"]
    temperatura = base_corridas[0]["temperatura"]
    if any(c["modelo"] != modelo or c["temperatura"] != temperatura for c in base_corridas):
        print("::error::la linea base mezcla modelos o temperaturas: no es comparable con nada")
        return 1

    # Una base tomada con la cuota de la API fallando no mide el bot, mide la cuota.
    #
    # Se mira `errores_api`, NO `errores_fatales`. Medido sobre el archivo real
    # `banco_linea_base_20260810.json`: sus tres corridas dieron 13, 11 y 9 con la
    # cuota ahogandolas, y las tres tienen errores_fatales=0 -- porque los reintentos
    # SI obtuvieron respuesta al final. O sea que filtrar por errores_fatales deja
    # pasar exactamente la base que hay que rechazar. Con --pausa 20 la meta es
    # alcanzable: las 6 corridas del 12-ago dieron 0 errores de API.
    sucias = [i for i, c in enumerate(base_corridas, 1)
              if c.get("errores_api") or c.get("errores_fatales")]
    if sucias:
        print(f"::error::la linea base tiene corridas contaminadas por la cuota de la API "
              f"(corridas {sucias}). Ese numero mide la cuota, no el bot. "
              f"Hay que volver a medirla con --pausa 20 hasta que de 0 errores.")
        return 1

    b = clasificar(base_corridas)
    print("=" * 72)
    print(f"LINEA BASE  {base_path.name}   ({base['fecha']}, {len(base_corridas)} corridas, "
          f"{modelo}, temperatura {temperatura})")
    print(f"  FIRMES ({len(b['firmes'])}): {', '.join(b['firmes']) or '-'}")
    print(f"  INESTABLES ({len(b['inestables'])}): {', '.join(b['inestables']) or '-'}")
    print(f"  ROTOS ({len(b['rotos'])}): {', '.join(b['rotos']) or '-'}")
    print("=" * 72)

    if not b["firmes"]:
        print("::error::la linea base no tiene NINGUN escenario firme: no hay nada que custodiar")
        return 1

    import banco_pruebas  # despues de las guardas: importarlo arrastra webhook.py entero

    # Se corre el banco COMPLETO, aunque la decision mire solo los firmes.
    #
    # 🪦 Aca decia: correr solo los firmes, porque son los unicos que pueden dar rojo
    # y cada escenario de menos son ~4 llamadas y 20 s. Estaba mal, y el error solo
    # aparece cuando esta corrida pasa a ser la linea base nueva (refresco en verde):
    # una base hecha solo de firmes PIERDE a los inestables, que quedan sin medicion
    # para siempre y sin forma de volver a entrar. El ahorro eran 3 escenarios de 14;
    # el precio era que el banco se fuera achicando solo, en silencio.
    escenarios = list(banco_pruebas.ESCENARIOS)
    huerfanos = sorted(set(b["firmes"]) - {e["id"] for e in escenarios})
    if huerfanos:
        print(f"[aviso] la base tiene firmes que ya no existen en el banco: {', '.join(huerfanos)}")
    nuevos = sorted({e["id"] for e in escenarios} - set(b["por_id"]))
    if nuevos:
        print(f"[aviso] escenarios sin linea base, el candado NO los cubre todavia: "
              f"{', '.join(nuevos)}")

    corridas = []
    for n in range(args.repeticiones):
        print(f"\n--- corrida {n + 1} de {args.repeticiones} "
              f"({len(escenarios)} escenarios) ---")
        resultados, cerebro = banco_pruebas.correr(escenarios, modelo, temperatura,
                                                   verboso=False, pausa=args.pausa)
        limpia = not (cerebro.errores_api or cerebro.errores_fatales)
        if not limpia:
            print(f"[aviso] la API fallo {cerebro.errores_api} vez/veces "
                  f"({cerebro.errores_fatales} sin respuesta): corrida no comparable")
        corridas.append({"puntaje": sum(1 for r in resultados if r["ok"]),
                         "de": len(resultados), "modelo": modelo, "temperatura": temperatura,
                         "errores_api": cerebro.errores_api,
                         "errores_fatales": cerebro.errores_fatales, "limpia": limpia,
                         "escenarios": [{k: r[k] for k in ("id", "ok", "empujones", "checks")}
                                        for r in resultados]})

    estado, detalle = decidir(b["firmes"], corridas)

    if args.json:
        # El estado va DENTRO del archivo a proposito. El CI decide si esta corrida
        # puede pasar a ser la linea base nueva leyendo este campo, NO por el exit
        # code: el exit code es 0 tanto en verde como en amarillo, y refrescar la
        # base con una corrida amarilla es justo como se degrada sin que nadie mire.
        Path(args.json).write_text(json.dumps(
            {"fecha": datetime.now().isoformat(timespec="seconds"),
             "base": base_path.name, "estado": estado, "corridas": corridas},
            ensure_ascii=False, indent=2), encoding="utf-8")

    imprimir_veredicto(estado, detalle, b["firmes"])
    return 1 if estado == "rojo" else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prueba la logica de git del Curador contra el .exe que se le manda a Christian.

Por que contra el .exe y no contra curador.py: lo que se le entrega a Christian es
el binario, y el binario resuelve sus rutas distinto (`sys.executable` en vez de
`__file__`). El 11-ago-2026 el arreglo del candado estaba bien en el .py y el
paquete igual llegaba trabado -- esta prueba es la que ve esa diferencia.

Los repos de juguete se construyen DESDE CERO en cada corrida. Reusarlos hace que
`git commit` no encuentre nada que commitear, el estado no se arma y la corrida mide
otra cosa que la que dice medir (pasó el 11-ago: el resultado "cambio" entre dos
corridas identicas).

Corre en una ruta CORTA de %TEMP% a proposito: con rutas largas `git rebase` muere
con "Filename too long" (MAX_PATH de Windows) y la prueba le echa la culpa al
curador de algo que es del entorno.

Uso:
    python bot/curador/prueba_git_curador.py                 # el paquete mas nuevo de dist/
    python bot/curador/prueba_git_curador.py "<ruta paquete>"
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PUERTO = 8099
BASE = Path(tempfile.gettempdir()) / "pt_curador"
PROYECTO = Path(__file__).resolve().parent.parent.parent

V1 = "# Prompt de juguete\n\nVersion UNO, la que ya estaba.\n"
V2 = "# Prompt de juguete\n\nVersion DOS, editada por la web de GitHub.\n"

resultados = []


def chequear(nombre, condicion, detalle=""):
    resultados.append(bool(condicion))
    print("  %-5s %s%s" % ("OK" if condicion else "FALLA", nombre,
                           ("  -- " + detalle) if detalle and not condicion else ""))


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError("git %s fallo: %s" % (" ".join(args), r.stderr))
    return r


def pedir(ruta, cuerpo=None):
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (PUERTO, ruta), data=datos,
        headers={"Content-Type": "application/json"} if datos else {})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read().decode("utf-8")


def _borrar_a_lo_bruto(func, path, exc):
    # .git/objects viene de solo lectura en Windows: sin esto rmtree deja restos.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def paquete_mas_nuevo():
    candidatos = [d for d in (PROYECTO / "dist").iterdir()
                  if d.is_dir() and d.name.startswith("Curador Patata")]
    if not candidatos:
        raise SystemExit("no hay ningun paquete 'Curador Patata*' en dist/")
    return max(candidatos, key=lambda d: (d / "Curador Patata.exe").stat().st_mtime)


def main():
    paquete_orig = Path(sys.argv[1]) if len(sys.argv) > 1 else paquete_mas_nuevo()
    print("== paquete bajo prueba:", paquete_orig.name)
    paquete = BASE / "Curador Patata"
    bare = BASE / "remoto.git"
    trabajo = BASE / "trabajo"
    clon = paquete / "despliegue_railway"

    print("== armando repos de juguete DESDE CERO ==")
    if BASE.exists():
        shutil.rmtree(BASE, onexc=_borrar_a_lo_bruto)
    BASE.mkdir(parents=True)
    shutil.copytree(paquete_orig, paquete)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)],
                   capture_output=True, check=True)
    trabajo.mkdir()
    git(trabajo, "init", "-b", "main")
    git(trabajo, "config", "user.email", "juguete@local")
    git(trabajo, "config", "user.name", "Juguete")
    (trabajo / "bot").mkdir()
    (trabajo / "bot" / "system_prompt.md").write_text(V1, encoding="utf-8", newline="\n")
    (trabajo / "requirements.txt").write_text("fastapi\n", encoding="utf-8", newline="\n")
    git(trabajo, "add", "-A")
    git(trabajo, "commit", "-m", "inicial")
    git(trabajo, "remote", "add", "origin", str(bare))
    git(trabajo, "push", "origin", "main")
    subprocess.run(["git", "clone", str(bare), str(clon)], capture_output=True, check=True)
    git(clon, "config", "user.email", "curador@local")
    git(clon, "config", "user.name", "Curador")
    (paquete / "system_prompt.md").write_text(V1, encoding="utf-8")

    print("== levantando el .exe en el puerto %d ==" % PUERTO)
    env = dict(os.environ)
    env["BROWSER"] = "where.exe"      # que no abra el navegador de quien corre la prueba
    proc = subprocess.Popen([str(paquete / "Curador Patata.exe"), "--port", str(PUERTO)],
                            cwd=str(paquete), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    vivo = False
    for _ in range(60):
        try:
            pedir("/api/estado")
            vivo = True
            break
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(1)

    try:
        chequear("el .exe levanta y responde", vivo, "no respondio en 60 s")
        if not vivo:
            return 1

        estado, html = pedir("/")
        chequear("sirve curador.html (lo encuentra al lado del .exe)",
                 estado == 200 and "<html" in html.lower() and len(html) > 5000,
                 "estado=%s largo=%d" % (estado, len(html)))

        _, cuerpo = pedir("/api/estado")
        chequear("con el clon puesto, /api/estado no levanta el cartel",
                 json.loads(cuerpo).get("despliegue_ok") is True, cuerpo[:120])

        _, cuerpo = pedir("/api/prompt")
        d = json.loads(cuerpo)
        chequear("lee system_prompt.md al lado del .exe", d.get("contenido") == V1,
                 "devolvio %r" % (d.get("contenido", "")[:60],))

        # -- alguien edita el prompt por la web de GitHub: el clon queda atras
        (trabajo / "bot" / "system_prompt.md").write_text(V2, encoding="utf-8", newline="\n")
        git(trabajo, "commit", "-am", "Update system_prompt.md")
        git(trabajo, "push", "origin", "main")

        _, cuerpo = pedir("/api/prompt")
        d = json.loads(cuerpo)
        chequear("al abrir el editor TRAE el prompt de GitHub",
                 d.get("traido_de_github") is True and d.get("contenido") == V2,
                 "respuesta=%s" % list(d))
        chequear("deja respaldo antes de pisar la copia local",
                 bool(d.get("respaldo")) and (paquete / str(d.get("respaldo", "x"))).exists(),
                 "respaldo=%r" % d.get("respaldo"))
        chequear("traer el prompt pone al dia el CLON, no solo el archivo",
                 git(clon, "rev-parse", "HEAD").stdout == git(clon, "rev-parse", "origin/main").stdout,
                 "HEAD=%s origin=%s" % (git(clon, "rev-parse", "--short", "HEAD").stdout.strip(),
                                        git(clon, "rev-parse", "--short", "origin/main").stdout.strip()))

        # -- el remoto avanza otra vez, pero sin tocar el prompt
        (trabajo / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8", newline="\n")
        git(trabajo, "commit", "-am", "otro cambio, no toca el prompt")
        git(trabajo, "push", "origin", "main")

        nuevo = V2 + "\n## ajuste hecho desde el curador\n"
        _, cuerpo = pedir("/api/prompt", {"contenido": nuevo})
        d = json.loads(cuerpo)
        push = d.get("push", {})
        chequear("guardar rebasa y PUSHEA (antes se trababa)",
                 d.get("ok") is True and push.get("ok") is True and push.get("pusheado") is True,
                 "push=%s" % push)

        git(trabajo, "fetch", "origin", "main")
        en_github = git(trabajo, "show", "origin/main:bot/system_prompt.md").stdout
        chequear("el ajuste llego a GitHub",
                 en_github.replace("\r\n", "\n").strip() == nuevo.strip(),
                 "en el remoto quedo: %r" % en_github[:80])
        ultimo = git(trabajo, "log", "--format=%s", "-1", "origin/main").stdout.strip()
        chequear("el commit lo firma el Curador", ultimo.startswith("Curador:"),
                 "ultimo commit: %r" % ultimo)
        chequear("el rebase NO piso el commit de la otra persona",
                 "uvicorn" in git(trabajo, "show", "origin/main:requirements.txt").stdout)

        # -- control negativo: con el clon atrasado tiene que volver el cartel.
        # Sin esto, los OK de arriba no prueban CUAL era la causa.
        inicial = git(clon, "rev-list", "--max-parents=0", "HEAD").stdout.split()[0]
        git(clon, "reset", "--hard", inicial)
        git(trabajo, "pull", "--rebase", "origin", "main")
        (trabajo / "bot" / "system_prompt.md").write_text(
            V2 + "\notra edicion por la web\n", encoding="utf-8", newline="\n")
        git(trabajo, "commit", "-am", "Update system_prompt.md otra vez")
        git(trabajo, "push", "origin", "main")
        _, cuerpo = pedir("/api/prompt", {"contenido": nuevo + "\nzzz\n"})
        push = json.loads(cuerpo).get("push", {})
        chequear("CONTROL: con el clon atrasado vuelve el cartel (esa era la causa)",
                 push.get("conflicto_prompt") is True, "push=%s" % push)

        # -- sin el clon: el aviso tiene que llegar al ABRIR la app, no recien al guardar.
        # Es el caso real de Christian: nunca hizo el Paso 2 del LEEME.
        shutil.rmtree(clon, onexc=_borrar_a_lo_bruto)
        _, cuerpo = pedir("/api/estado")
        chequear("sin el clon, /api/estado lo dice al abrir (cartel, antes de trabajar)",
                 json.loads(cuerpo).get("despliegue_ok") is False, cuerpo[:120])
        _, cuerpo = pedir("/api/prompt", {"contenido": nuevo + "\nsin clon\n"})
        push = json.loads(cuerpo).get("push", {})
        chequear("sin despliegue_railway avisa que falta la carpeta",
                 push.get("ok") is False and "no encontr" in push.get("error", ""),
                 "push=%s" % push)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    ok = sum(resultados)
    falla = len(resultados) - ok
    print("\n=== CONTEO: %d OK / %d FALLA ===" % (ok, falla))
    return 1 if falla else 0


if __name__ == "__main__":
    sys.exit(main())

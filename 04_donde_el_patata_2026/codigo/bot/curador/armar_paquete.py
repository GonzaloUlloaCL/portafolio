#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arma el paquete del Curador que se le manda a Christian.

Existe porque hasta el 11-ago-2026 el paquete se armaba a mano y eso se olvida:
el .exe NO lleva adentro ni el HTML ni los datos -- en modo frozen curador.py y
prototipo.py los buscan JUNTO al ejecutable (`Path(sys.executable).parent`), asi
que si falta uno la app compila igual y falla recien al abrirse en el PC de el.

NO incluye `despliegue_railway/`: ese clon lo hace Christian una sola vez con su
propio token (Paso 2 de LEEME_CHRISTIAN.md). Meterlo en el zip mandaria un repo
con credenciales adentro.

Uso (desde la raiz del proyecto, con el venv):
    python bot/curador/armar_paquete.py            # compila y arma con la fecha de hoy
    python bot/curador/armar_paquete.py --sin-compilar
"""
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

AQUI = Path(__file__).resolve().parent           # bot/curador/
PROYECTO = AQUI.parent.parent                    # Donde el Patata/
BUILD = PROYECTO / "build_pyi"
SALIDA_PYI = BUILD / "dist" / "Curador Patata"
DESTINO = PROYECTO / "dist" / f"Curador Patata (update {date.today():%Y%m%d})"

# origen (relativo al proyecto) -> nombre que tiene que quedar AL LADO del .exe
COPIAS = {
    "bot/curador/curador.html": "curador.html",
    "bot/curador/config_curador.json": "config_curador.json",
    "bot/curador/LEEME_CHRISTIAN.md": "LEEME_CHRISTIAN.md",
    "bot/system_prompt.md": "system_prompt.md",
    "bot/system_prompt_jefe.md": "system_prompt_jefe.md",
    "bot/cierre_pedido.md": "cierre_pedido.md",
    "data/menu.json": "menu.json",
    "bot/openai_key_bot.txt": "openai_key_bot.txt",
}


def main():
    if "--sin-compilar" not in sys.argv:
        print("== compilando con PyInstaller ==")
        r = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                            "--log-level", "ERROR", "Curador Patata.spec"],
                           cwd=str(BUILD))
        if r.returncode != 0:
            print("FALLO la compilacion")
            return 1

    if not (SALIDA_PYI / "Curador Patata.exe").exists():
        print("no encontre el .exe compilado en", SALIDA_PYI)
        return 1

    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    shutil.copytree(SALIDA_PYI, DESTINO)

    for origen, nombre in COPIAS.items():
        o = PROYECTO / origen
        if not o.exists():
            print("FALTA la fuente:", origen)
            return 1
        shutil.copy2(o, DESTINO / nombre)

    # El zip se arma con la carpeta adentro, como los paquetes del POS: Christian
    # descomprime y le queda "Curador Patata/" entera, no 662 archivos sueltos.
    zip_ruta = shutil.make_archive(str(DESTINO), "zip",
                                   root_dir=str(DESTINO.parent),
                                   base_dir=DESTINO.name)

    archivos = sum(1 for f in DESTINO.rglob("*") if f.is_file())
    print("\npaquete: %s" % DESTINO.name)
    print("   %d archivos" % archivos)
    print("   zip: %s (%.1f MB)" % (Path(zip_ruta).name,
                                    Path(zip_ruta).stat().st_size / 1024 / 1024))
    faltan = [n for n in COPIAS.values() if not (DESTINO / n).exists()]
    print("   archivos de datos al lado del .exe: %s" % ("todos" if not faltan else faltan))
    return 0


if __name__ == "__main__":
    sys.exit(main())

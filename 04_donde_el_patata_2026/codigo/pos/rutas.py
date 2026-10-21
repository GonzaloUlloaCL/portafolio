# -*- coding: utf-8 -*-
"""
rutas.py — Dónde están los datos del POS, según corra como .py o como .exe.
===========================================================================
- En **.exe** (PyInstaller): los datos viven JUNTO al ejecutable, en una carpeta
  escribible. Así `pedidos.db` se puede escribir y `menu.json` se puede editar sin
  recompilar.
- En **desarrollo** (.py): cada cosa en su lugar de siempre (pos/, data/, bot/).
"""
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # Corriendo como .exe: todo al lado del ejecutable.
    BASE = Path(sys.executable).resolve().parent
    POS = BASE
    BOT = BASE
    DATA = BASE
else:
    POS = Path(__file__).resolve().parent      # .../pos
    BASE = POS.parent                            # raíz del proyecto
    BOT = BASE / "bot"
    DATA = BASE / "data"

MENU_JSON = DATA / "menu.json"
HTML_CAJERO = POS / "cajero.html"
DB_DEFAULT = POS / "pedidos.db"
CONFIG_IMPRESORA = POS / "config.json"
BACKUPS = BASE / "backups"   # copias de seguridad (menu.json antes de editar, pedidos.db al cerrar caja)

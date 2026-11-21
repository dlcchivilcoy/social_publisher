#!/usr/bin/env python3
"""Clave fija del conector MCP para la app de ESCRITORIO de ChatGPT (21/11/2026). Lo corre el editor.

Por qué: la app de escritorio conecta el chat con un cliente MCP que no usa el login OAuth de Configuración; solo
manda «Authorization: Bearer» con el valor de una variable de entorno de la compu («Variable de entorno del token
portador» en el formulario del MCP). Este script:

  1. Genera una clave al azar (nunca se muestra).
  2. La carga en Vercel como MCP_STATIC_TOKEN (sensible) y vuelve a publicar el conector.
  3. La guarda en la variable de entorno de TU usuario de Windows EDITORIAL_MCP_TOKEN (registro HKCU\\Environment).

Después: en ChatGPT → Complementos → MCP → ingreso_editorial_diario (engranaje) → «Variable de entorno del token
portador» = EDITORIAL_MCP_TOKEN → Guardar, y cerrar y volver a abrir ChatGPT.
Para cortar este acceso: volver a correr el script (cambia la clave) o borrar MCP_STATIC_TOKEN en Vercel.

    python tools/editorial_token_chatgpt.py
"""
import ctypes
import secrets
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

CONECTOR = Path(__file__).resolve().parent.parent / "mcp-editorial"
NPX = shutil.which("npx.cmd") or shutil.which("npx") or "npx"
VARIABLE = "EDITORIAL_MCP_TOKEN"


def correr(args, entrada=None):
    r = subprocess.run([NPX, "--yes", *args], input=entrada, text=True, cwd=CONECTOR, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"Falló `{' '.join(args[:3])}…`:\n{(r.stderr or r.stdout)[-600:]}")


def main() -> int:
    if not (CONECTOR / ".vercel" / "project.json").exists():
        print("Falta el vínculo del conector con Vercel (mcp-editorial/.vercel).")
        return 2
    token = secrets.token_urlsafe(48)
    print("→ Vercel: renovando la sesión y cargando la clave fija…")
    correr(["vercel", "whoami"])
    correr(["vercel", "env", "add", "MCP_STATIC_TOKEN", "production", "--force", "--sensitive"], entrada=token)
    print("→ Vercel: publicando el conector…")
    correr(["vercel", "deploy", "--prod", "--yes"])
    print(f"→ Windows: guardando la clave en la variable {VARIABLE} de tu usuario…")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VARIABLE, 0, winreg.REG_SZ, token)
    # Avisa a Windows que cambiaron las variables (las apps que se abran de nuevo las ven).
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(ctypes.c_ulong()))
    print("\nListo. Ahora en ChatGPT:")
    print("  Configuración → Complementos → MCP → engranaje de ingreso_editorial_diario")
    print(f"  «Variable de entorno del token portador»: {VARIABLE}  → Guardar")
    print("  Después cerrá ChatGPT del todo y volvé a abrirlo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Guarda la API key de Buffer sin que pase por el chat ni quede en la pantalla.

La pide oculta (getpass), la prueba contra la API de Buffer y, si anda:
  1. la escribe como BUFFER_API_KEY en el .env de esta carpeta (reemplaza la vieja);
  2. sube el .env al secret ENV_FILE (igual que «Sincronizar credenciales.bat», bytes tal cual);
  3. la carga como secret BUFFER_API_KEY en policiales-bsas.

Uso: doble clic en «Cargar clave Buffer.bat».
"""
from __future__ import annotations

import getpass
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ENV = AQUI / ".env"
REPOS_EXTRA = ["dlcchivilcoy/policiales-bsas"]


def probar(clave: str) -> str:
    q = {"query": "query { account { email organizations { id name } } }"}
    req = urllib.request.Request(
        "https://api.buffer.com", data=json.dumps(q).encode(),
        headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        datos = json.loads(r.read())
    if datos.get("errors"):
        raise RuntimeError(datos["errors"][0].get("message", "error"))
    cuenta = datos["data"]["account"]
    orgs = ", ".join(o["name"] for o in cuenta.get("organizations") or [])
    return f"{cuenta.get('email')} (organización: {orgs})"


def escribir_env(clave: str) -> None:
    crudo = ENV.read_bytes()
    lineas = crudo.decode("utf-8").splitlines()
    lineas = [l for l in lineas if not l.startswith("BUFFER_API_KEY=")]
    lineas.append(f"BUFFER_API_KEY={clave}")
    ENV.write_bytes(("\n".join(lineas) + "\n").encode("utf-8"))


def main() -> int:
    if not ENV.exists():
        print("[ERROR] No encuentro el .env en", AQUI)
        return 1
    clave = getpass.getpass("Pegá la API key de Buffer (no se ve al pegar) y tocá Enter: ").strip()
    if not clave:
        print("No se pegó nada. No cambié nada.")
        return 1
    try:
        print("Probando la clave contra Buffer...")
        print("  OK, la clave es de:", probar(clave))
    except Exception as e:  # noqa: BLE001
        print("[ERROR] Buffer rechazó la clave:", e)
        print("No cambié nada.")
        return 1
    escribir_env(clave)
    print("  Guardada en .env")
    with ENV.open("rb") as f:
        r = subprocess.run(["gh", "secret", "set", "ENV_FILE"], stdin=f, cwd=AQUI)
    print("  Secret ENV_FILE actualizado" if r.returncode == 0 else "  [ERROR] no se pudo subir ENV_FILE")
    for repo in REPOS_EXTRA:
        r = subprocess.run(["gh", "secret", "set", "BUFFER_API_KEY", "-R", repo],
                           input=clave.encode(), cwd=AQUI)
        print(f"  Secret BUFFER_API_KEY en {repo}" if r.returncode == 0 else f"  [ERROR] {repo}")
    print("\nListo. Avisale a Claude que ya está cargada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

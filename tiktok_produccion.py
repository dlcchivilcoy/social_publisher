"""Pasar TikTok de sandbox a PRODUCCIÓN, el día que TikTok apruebe el review.

Hace los tres pasos de una: actualiza el .env con las credenciales nuevas y el
redirect de escritorio, re-autoriza (abre el navegador una vez) y deja creada la
tarea de Windows que manda el reel todos los días.

Uso:  venv\\Scripts\\python.exe tiktok_produccion.py
"""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ENV = RAIZ / ".env"
REDIRECT = "http://localhost:8723/callback/"
TAREA = "Diario TikTok Reel 2015"
HORA = "20:15"


def _actualizar_env(clave: str, secreto: str) -> None:
    """Reemplaza las 3 variables de TikTok en el .env, dejando el resto igual."""
    respaldo = ENV.with_suffix(f".bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(ENV, respaldo)
    print(f"   Respaldo del .env en: {respaldo.name}")

    nuevos = {
        "TIKTOK_CLIENT_KEY": clave,
        "TIKTOK_CLIENT_SECRET": secreto,
        "TIKTOK_REDIRECT_URI": REDIRECT,
    }
    lineas = ENV.read_text(encoding="utf-8").splitlines()
    vistos = set()
    for i, linea in enumerate(lineas):
        for k, v in nuevos.items():
            if linea.startswith(f"{k}="):
                lineas[i] = f"{k}={v}"
                vistos.add(k)
    for k, v in nuevos.items():  # por si alguna no estaba
        if k not in vistos:
            lineas.append(f"{k}={v}")
    ENV.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def _crear_tarea() -> None:
    bat = RAIZ / "run_tiktok_reel.bat"
    r = subprocess.run(
        ["schtasks", "/create", "/tn", TAREA, "/tr", str(bat),
         "/sc", "daily", "/st", HORA, "/f"],
        capture_output=True, text=True)
    if r.returncode == 0:
        print(f"   ✅ Tarea «{TAREA}» creada, todos los días a las {HORA}.")
    else:
        print(f"   ❌ No se pudo crear la tarea: {(r.stderr or r.stdout).strip()[:200]}")
        print("      Probá corriendo este script desde una terminal como administrador.")


def main() -> None:
    print("\n=== TikTok: pasar a PRODUCCIÓN ===\n")
    print("Necesitás las credenciales NUEVAS de producción, que están en")
    print("developers.tiktok.com → tu app → App details → Credentials.")
    print("(Las de sandbox ya no sirven: la client key vieja empieza con 'sbaw'.)\n")

    clave = input("Client key de producción: ").strip()
    secreto = input("Client secret de producción: ").strip()
    if not clave or not secreto:
        print("\n❌ Faltó alguna. No toqué nada.")
        sys.exit(1)
    if clave.startswith("sbaw"):
        print("\n⚠️  Esa client key es la de SANDBOX (empieza con 'sbaw').")
        if input("   ¿Seguir igual? (s/N): ").strip().lower() != "s":
            print("   Cancelado, no toqué nada.")
            sys.exit(1)

    print("\n1) Actualizando el .env…")
    _actualizar_env(clave, secreto)
    print(f"   Listo. Redirect: {REDIRECT}")

    print("\n2) Autorizando con tu cuenta de TikTok (se abre el navegador)…")
    print("   Entrá con la cuenta del diario (@diarioyradio) y aceptá.\n")
    r = subprocess.run([sys.executable, str(RAIZ / "tiktok_auth.py")])
    if r.returncode != 0:
        print("\n❌ La autorización falló. Revisá el mensaje de arriba.")
        print("   El .env ya quedó actualizado: cuando lo resuelvas, corré")
        print("   'venv\\Scripts\\python.exe tiktok_auth.py' y seguí con el paso 3.")
        sys.exit(1)

    print("\n3) Tarea de Windows diaria…")
    if input(f"   ¿Crear «{TAREA}» a las {HORA}? (S/n): ").strip().lower() in ("", "s"):
        _crear_tarea()
    else:
        print("   Salteada.")

    print("\n✅ Listo. Probá ahora mismo con:")
    print("   venv\\Scripts\\python.exe tiktok_reel.py --force")


if __name__ == "__main__":
    main()

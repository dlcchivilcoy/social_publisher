"""Abre la pantalla de confirmación de TikTok en el dominio que TikTok tiene declarado.

Para GRABAR el video de demostración de la auditoría. TikTok exige que el dominio que se
ve en el video coincida con el `Web/Desktop URL` de la app (hoy `diarioweb.vercel.app`),
y el botón del mail apunta al dominio principal — por eso este atajo.

Uso:  python demo_pantalla_tiktok.py            (pregunta el nombre de la carpeta)
      python demo_pantalla_tiktok.py <carpeta>

El nombre de la carpeta está al pie del mail de aprobación, entre comillas angulares.
"""
import sys
import urllib.parse
import webbrowser

from utils.config import get, load_config

DOMINIO = "https://diarioweb.vercel.app"


def main() -> None:
    load_config()
    token = (get("WEBAPP_TOKEN") or "").strip()
    if not token:
        print("Falta WEBAPP_TOKEN en el .env.")
        return

    nombre = sys.argv[1] if len(sys.argv) > 1 else input("Nombre de la carpeta o video: ").strip()
    if not nombre:
        print("No pusiste ningún nombre.")
        return

    # Las notas de corresponsal por FOTO son carpetas; los videos, archivos .mp4.
    kind = "" if nombre.lower().endswith(".mp4") else "folder"
    url = f"{DOMINIO}/api/aprobar-video?" + urllib.parse.urlencode(
        {"action": "approve", "name": nombre, "kind": kind, "token": token})

    print(f"\nAbriendo la pantalla en {DOMINIO} …")
    print("OJO: al tocar «Publicar» se publica de verdad en todas las redes.\n")
    webbrowser.open(url)


if __name__ == "__main__":
    main()

"""Hosting del .mp4 del reel en una URL pública para que Instagram lo baje.

Sube el video como ASSET de un GitHub Release con un tag fijo (`reel-latest`):
se sobrescribe cada día, así no infla el repo. En el workflow está disponible el
`GITHUB_TOKEN` (permiso contents:write) y `GITHUB_REPOSITORY`; en local se pueden
definir `GITHUB_TOKEN`/`REEL_REPO` en el .env. Facebook NO necesita esto (sube el
archivo directo), solo Instagram (que pide una URL pública del video).
"""
import os
import time
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("video_host")

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TAG = "reel-latest"


def _token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or get("GITHUB_TOKEN")
    if not tok:
        raise ValueError("No hay GITHUB_TOKEN (ni en el entorno ni en .env) para subir el reel")
    return tok


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or get("REEL_REPO") or "dlcchivilcoy/social_publisher"


def _headers(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _ensure_release(repo: str, h: dict) -> dict:
    r = requests.get(f"{API}/repos/{repo}/releases/tags/{TAG}", headers=h, timeout=30)
    if r.status_code == 200:
        return r.json()
    if r.status_code != 404:
        r.raise_for_status()
    # No existe: crear el release (prerelease para que no figure como "última versión")
    r = requests.post(f"{API}/repos/{repo}/releases", headers=h, timeout=30,
                      json={"tag_name": TAG, "name": "Reel del día (auto)", "prerelease": True,
                            "body": "Asset temporal del reel diario. Se sobrescribe cada día."})
    r.raise_for_status()
    return r.json()


def _es_transitorio(e: Exception) -> bool:
    """True si conviene REINTENTAR: corte/timeout de red, o 5xx/429 de GitHub (sobrecarga
    momentánea). Un 4xx "real" (401/404/422) NO se reintenta: es un error de config, no un hipo."""
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return e.response.status_code in (429, 500, 502, 503, 504)
    return False


def upload_reel(mp4_path: Path, intentos: int = 4) -> str:
    """Sube el .mp4 y devuelve la URL pública de descarga directa.

    REINTENTA ante fallos transitorios (red, timeout, 5xx/429 de GitHub): esta subida es el
    paso previo a publicar el reel en IG/FB y a embeber el video en la web, así que un hipo de
    red no debe tumbar toda la corrida del desgrabador (antes: una sola falla acá dejaba el
    video sin nota y sin aviso). El archivo se lee UNA sola vez y se reusa en cada intento."""
    mp4_path = Path(mp4_path)
    tok = _token()
    repo = _repo()
    h = _headers(tok)
    asset_name = mp4_path.name
    with open(mp4_path, "rb") as f:
        data = f.read()

    for i in range(max(1, intentos)):
        try:
            release = _ensure_release(repo, h)
            release_id = release["id"]

            # Borrar un asset previo con el mismo nombre (GitHub no permite duplicados)
            for a in release.get("assets", []):
                if a.get("name") == asset_name:
                    requests.delete(f"{API}/repos/{repo}/releases/assets/{a['id']}", headers=h, timeout=30)
                    logger.debug(f"Asset previo borrado: {asset_name}")

            up = requests.post(
                f"{UPLOADS}/repos/{repo}/releases/{release_id}/assets?name={asset_name}",
                headers={**h, "Content-Type": "video/mp4"},
                data=data, timeout=180,
            )
            up.raise_for_status()
            url = up.json()["browser_download_url"]
            logger.info(f"Reel subido a GitHub Release: {url}")
            return url
        except Exception as e:  # noqa: BLE001 — decidimos reintentar según el tipo de error
            if i < intentos - 1 and _es_transitorio(e):
                espera = 5 * (i + 1)
                logger.warning(f"Subida del reel falló (intento {i + 1}/{intentos}: {e}); "
                               f"reintento en {espera}s…")
                time.sleep(espera)
                continue
            raise

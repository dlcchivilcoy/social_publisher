import base64
import os
import time
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("image_host")

IMGBB_URL = "https://api.imgbb.com/1/upload"

# ── GitHub Release como RESPALDO de ImgBB ─────────────────────────────────────
# Si ImgBB se cae (pasó 2026-08-03: «Imgbb is currently down for maintenance» y las
# historias de tapa+farmacias no salieron en Instagram), subimos la imagen como asset
# de un GitHub Release —el mismo mecanismo que el .mp4 del reel— para no depender de un
# único hosting. En el workflow ya están GITHUB_TOKEN y GITHUB_REPOSITORY; en local se
# pueden definir GITHUB_TOKEN/REEL_REPO en el .env.
_GH_API = "https://api.github.com"
_GH_UPLOADS = "https://uploads.github.com"
_GH_TAG = "img-latest"


def _gh_token() -> str:
    return os.environ.get("GITHUB_TOKEN") or get("GITHUB_TOKEN") or ""


def _gh_repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or get("REEL_REPO") or "dlcchivilcoy/social_publisher"


def _gh_headers(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _gh_content_type(nombre: str) -> str:
    n = nombre.lower()
    if n.endswith(".png"):
        return "image/png"
    if n.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def _ensure_release(repo: str, h: dict) -> dict:
    r = requests.get(f"{_GH_API}/repos/{repo}/releases/tags/{_GH_TAG}", headers=h, timeout=30)
    if r.status_code == 200:
        return r.json()
    if r.status_code != 404:
        r.raise_for_status()
    # No existe: crear el release (prerelease para que no figure como "última versión").
    r = requests.post(f"{_GH_API}/repos/{repo}/releases", headers=h, timeout=30,
                      json={"tag_name": _GH_TAG, "name": "Imágenes del día (auto)", "prerelease": True,
                            "body": "Assets temporales de imágenes para IG cuando ImgBB no responde. "
                                    "Se sobrescriben (mismo nombre)."})
    r.raise_for_status()
    return r.json()


def _upload_to_github(image_path: Path) -> str:
    """Sube la imagen como asset de un GitHub Release y devuelve la URL pública de descarga."""
    tok = _gh_token()
    if not tok:
        raise ValueError("No hay GITHUB_TOKEN (ni en el entorno ni en .env) para el respaldo de imagen")
    repo = _gh_repo()
    h = _gh_headers(tok)
    asset_name = Path(image_path).name
    with open(image_path, "rb") as f:
        data = f.read()
    release = _ensure_release(repo, h)
    release_id = release["id"]
    # Borrar un asset previo con el mismo nombre (GitHub no permite duplicados).
    for a in release.get("assets", []):
        if a.get("name") == asset_name:
            requests.delete(f"{_GH_API}/repos/{repo}/releases/assets/{a['id']}", headers=h, timeout=30)
            logger.debug(f"Asset de imagen previo borrado: {asset_name}")
    up = requests.post(
        f"{_GH_UPLOADS}/repos/{repo}/releases/{release_id}/assets?name={asset_name}",
        headers={**h, "Content-Type": _gh_content_type(asset_name)},
        data=data, timeout=120,
    )
    up.raise_for_status()
    url = up.json()["browser_download_url"]
    logger.info(f"Imagen subida a GitHub Release (respaldo de ImgBB): {url}")
    return url


# ── ImgBB (hosting primario) ──────────────────────────────────────────────────
def _upload_to_imgbb(image_path: Path, intentos: int = 4) -> str:
    """Sube la imagen a ImgBB y devuelve una URL pública HTTPS (expira en 10 min).

    REINTENTA ante fallos transitorios de ImgBB (400/429/5xx o corte de red): antes, un
    solo tropiezo tumbaba TODO el carrusel de Instagram (2026-07-22: «400 Bad Request»
    en una corrida, y a los minutos ImgBB andaba perfecto). Si agota los intentos, lanza
    con el MENSAJE que devolvió ImgBB (no solo el código), para poder diagnosticar."""
    api_key = get("IMGBB_API_KEY")
    if not api_key:
        raise ValueError("IMGBB_API_KEY not set in .env")

    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    nombre = Path(image_path).name
    ultimo = ""
    for i in range(max(1, intentos)):
        try:
            resp = requests.post(
                IMGBB_URL,
                data={"key": api_key, "image": encoded, "expiration": 600},
                timeout=60,
            )
            if resp.ok:
                url = resp.json()["data"]["url"]
                logger.debug(f"ImgBB upload OK: {url}")
                return url
            ultimo = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:  # noqa: BLE001 — red/timeout: se reintenta igual
            ultimo = str(e)
        if i < intentos - 1:
            espera = 5 * (i + 1)
            logger.warning(f"ImgBB falló subiendo «{nombre}» ({ultimo}); "
                           f"reintento {i + 1}/{intentos - 1} en {espera}s…")
            time.sleep(espera)

    raise RuntimeError(f"ImgBB no pudo subir «{nombre}» tras {intentos} intentos — {ultimo}")


def upload_to_imgbb(image_path: Path, intentos: int = 4) -> str:
    """Devuelve una URL pública HTTPS de la imagen (la usan Instagram y Wix).

    Primario: ImgBB. RESPALDO: si ImgBB no responde tras varios intentos (p. ej.
    2026-08-03, ImgBB en mantenimiento → las historias de IG no salían), sube la imagen
    a un GitHub Release y usa esa URL, así IG no depende de un único hosting. Se conserva
    el nombre `upload_to_imgbb` por compatibilidad con quienes ya lo llaman."""
    image_path = Path(image_path)
    try:
        return _upload_to_imgbb(image_path, intentos=intentos)
    except Exception as imgbb_err:
        logger.warning(f"ImgBB no disponible ({imgbb_err}); intento el respaldo en GitHub Release…")
        try:
            return _upload_to_github(image_path)
        except Exception as gh_err:
            logger.error(f"El respaldo en GitHub Release también falló: {gh_err}")
            raise imgbb_err

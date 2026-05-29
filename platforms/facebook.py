from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("facebook")

GRAPH_VERSION = "v19.0"


def publish(body: str, image_path: Path) -> dict:
    page_id = get("FACEBOOK_PAGE_ID")
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")

    if not page_id or not token:
        raise ValueError("FACEBOOK_PAGE_ID o FACEBOOK_PAGE_ACCESS_TOKEN no configurados en .env")

    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/photos"

    with open(image_path, "rb") as img:
        resp = requests.post(
            url,
            params={"access_token": token},
            files={"source": (image_path.name, img, _mime(image_path))},
            data={"message": body},
            timeout=60,
        )

    _raise_for_status(resp)
    data = resp.json()
    logger.debug(f"Facebook post_id={data.get('post_id') or data.get('id')}")
    return {"success": True, "id": data.get("post_id") or data.get("id")}


def _mime(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def _raise_for_status(resp: requests.Response) -> None:
    if resp.status_code == 401:
        raise PermissionError("Facebook: token inválido o expirado (401) — revisá .env")
    if resp.status_code == 403:
        raise PermissionError("Facebook: permisos insuficientes (403) — revisá los permisos de la app")
    if resp.status_code == 429:
        raise RuntimeError("Facebook: límite de tasa alcanzado (429) — se reintentará la próxima vez")
    resp.raise_for_status()

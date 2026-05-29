from pathlib import Path

import requests

from utils.config import get
from utils.image_host import upload_to_imgbb
from utils.logger import get_logger

logger = get_logger("instagram")

GRAPH_VERSION = "v19.0"


def publish(body: str, image_path: Path) -> dict:
    user_id = get("INSTAGRAM_USER_ID")
    token = get("INSTAGRAM_ACCESS_TOKEN")

    if not user_id or not token:
        raise ValueError("INSTAGRAM_USER_ID o INSTAGRAM_ACCESS_TOKEN no configurados en .env")

    image_url = upload_to_imgbb(image_path)

    # Paso 1: crear contenedor de media
    container_resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{user_id}/media",
        params={"access_token": token},
        data={"image_url": image_url, "caption": body},
        timeout=30,
    )
    _raise_for_status(container_resp, "crear contenedor")
    creation_id = container_resp.json()["id"]
    logger.debug(f"Instagram creation_id={creation_id}")

    # Paso 2: publicar el contenedor
    publish_resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{user_id}/media_publish",
        params={"access_token": token},
        data={"creation_id": creation_id},
        timeout=30,
    )
    _raise_for_status(publish_resp, "publicar media")
    media_id = publish_resp.json()["id"]
    logger.debug(f"Instagram media publicado id={media_id}")
    return {"success": True, "id": media_id}


def _raise_for_status(resp: requests.Response, step: str) -> None:
    if resp.status_code == 401:
        raise PermissionError(f"Instagram ({step}): token inválido o expirado (401)")
    if resp.status_code == 403:
        raise PermissionError(f"Instagram ({step}): permisos insuficientes (403)")
    if resp.status_code == 429:
        raise RuntimeError(f"Instagram ({step}): límite de tasa (429) — se reintentará la próxima vez")
    resp.raise_for_status()

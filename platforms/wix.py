from pathlib import Path

import requests

from utils.config import get
from utils.image_host import upload_to_imgbb
from utils.logger import get_logger

logger = get_logger("wix")

MEDIA_IMPORT_URL = "https://www.wixapis.com/media/v1/files/import"
BLOG_POSTS_URL = "https://www.wixapis.com/blog/v3/posts"


def publish(title: str, body: str, image_path: Path) -> dict:
    api_key = get("WIX_API_KEY")
    site_id = get("WIX_SITE_ID")

    if not api_key or not site_id:
        raise ValueError("WIX_API_KEY o WIX_SITE_ID no configurados en .env")

    headers = {
        "Authorization": api_key,
        "wix-site-id": site_id,
        "Content-Type": "application/json",
    }

    # Paso 1: subir imagen a ImgBB para obtener URL pública
    image_url = upload_to_imgbb(image_path)

    # Paso 2: importar imagen al Media Manager de Wix
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    import_resp = requests.post(
        MEDIA_IMPORT_URL,
        headers=headers,
        json={"mediaType": "IMAGE", "url": image_url, "mimeType": mime},
        timeout=30,
    )
    _raise_for_status(import_resp, "importar imagen")
    wix_file_url = import_resp.json()["file"]["url"]
    logger.debug(f"Wix imagen importada: {wix_file_url}")

    # Paso 3: crear y publicar el post del blog
    post_payload = {
        "post": {
            "title": title,
            "richContent": {
                "nodes": [
                    {
                        "type": "PARAGRAPH",
                        "nodes": [
                            {
                                "type": "TEXT",
                                "textData": {"text": body},
                            }
                        ],
                    }
                ]
            },
            "media": {"wixMedia": {"imageUrl": wix_file_url}},
            "status": "PUBLISHED",
        }
    }

    post_resp = requests.post(
        BLOG_POSTS_URL,
        headers=headers,
        json=post_payload,
        timeout=30,
    )
    _raise_for_status(post_resp, "crear post")
    post_id = post_resp.json().get("post", {}).get("id")
    logger.debug(f"Wix post publicado id={post_id}")
    return {"success": True, "id": post_id}


def _raise_for_status(resp: requests.Response, step: str) -> None:
    if resp.status_code == 401:
        raise PermissionError(f"Wix ({step}): API key inválida (401) — revisá .env")
    if resp.status_code == 403:
        raise PermissionError(f"Wix ({step}): permisos insuficientes (403)")
    if resp.status_code == 429:
        raise RuntimeError(f"Wix ({step}): límite de tasa (429) — se reintentará la próxima vez")
    resp.raise_for_status()

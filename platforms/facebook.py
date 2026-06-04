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


def comment(object_id: str, message: str) -> dict:
    """Agrega un comentario (como la Página) a un posteo propio.

    Se usa para poner el link de la nota en el PRIMER COMENTARIO en vez del
    cuerpo del posteo: Facebook penaliza el alcance de los posteos con links
    externos, pero casi no penaliza los links en comentarios.
    """
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not token:
        raise ValueError("FACEBOOK_PAGE_ACCESS_TOKEN no configurado en .env")
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{object_id}/comments"
    resp = requests.post(url, params={"access_token": token},
                         data={"message": message}, timeout=60)
    _raise_for_status(resp)
    return {"success": True, "id": resp.json().get("id")}


def publish_story(image_path: Path) -> dict:
    """Publica la imagen como HISTORIA (story) de la Página de Facebook.

    Dos pasos: subir la foto SIN publicar (published=false) → obtener photo_id;
    luego crear la historia con /photo_stories. Requiere pages_manage_posts.

    NOTA: las Page Photo Stories por API son relativamente nuevas y a veces
    requieren elegibilidad extra de la página. Si falla, el llamador lo loguea
    y sigue (Instagram no se ve afectado).
    """
    page_id = get("FACEBOOK_PAGE_ID")
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise ValueError("FACEBOOK_PAGE_ID o FACEBOOK_PAGE_ACCESS_TOKEN no configurados en .env")

    # 1) Subir la foto sin publicarla en el feed → photo_id
    with open(image_path, "rb") as img:
        up = requests.post(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/photos",
            params={"access_token": token},
            files={"source": (image_path.name, img, _mime(image_path))},
            data={"published": "false"},
            timeout=60,
        )
    _raise_for_status(up)
    photo_id = up.json()["id"]

    # 2) Crear la historia con esa foto
    story = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/photo_stories",
        params={"access_token": token},
        data={"photo_id": photo_id},
        timeout=60,
    )
    _raise_for_status(story)
    data = story.json()
    logger.debug(f"Facebook story post_id={data.get('post_id') or data.get('id')}")
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

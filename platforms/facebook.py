import json
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("facebook")

GRAPH_VERSION = "v19.0"


def _place() -> str:
    """ID de página-lugar de Facebook para etiquetar la UBICACIÓN del posteo
    (ej. Chivilcoy, Buenos Aires). Configurable en FB_PLACE_ID del .env."""
    return get("FB_PLACE_ID") or ""


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


def publish_multi(message: str, image_paths: list[Path]) -> dict:
    """Publica VARIAS fotos en un solo posteo (carrusel/galería) de la Página.

    Sube cada foto sin publicar (published=false) → media_fbid; luego crea el
    posteo en /feed con attached_media. Si llega 1 sola imagen, cae a publish()."""
    page_id = get("FACEBOOK_PAGE_ID")
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise ValueError("FACEBOOK_PAGE_ID o FACEBOOK_PAGE_ACCESS_TOKEN no configurados en .env")

    paths = list(image_paths)
    if len(paths) < 2:
        return publish(message, paths[0])

    media_fbids: list[str] = []
    for p in paths:
        with open(p, "rb") as img:
            up = requests.post(
                f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/photos",
                params={"access_token": token},
                files={"source": (p.name, img, _mime(p))},
                data={"published": "false"},
                timeout=60,
            )
        _raise_for_status(up)
        media_fbids.append(up.json()["id"])

    data = {"message": message}
    if _place():
        data["place"] = _place()
    for i, fbid in enumerate(media_fbids):
        data[f"attached_media[{i}]"] = json.dumps({"media_fbid": fbid})

    resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/feed",
        params={"access_token": token},
        data=data,
        timeout=90,
    )
    _raise_for_status(resp)
    out = resp.json()
    logger.debug(f"Facebook multi-foto id={out.get('id')} ({len(media_fbids)} fotos)")
    return {"success": True, "id": out.get("id")}


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


def publish_video(message: str, video_path: Path) -> dict:
    """Publica un VIDEO (reel) en el feed de la Página subiendo el archivo directo.

    Facebook acepta la subida directa del .mp4 a /{page}/videos (no necesita URL
    pública, a diferencia de Instagram)."""
    page_id = get("FACEBOOK_PAGE_ID")
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise ValueError("FACEBOOK_PAGE_ID o FACEBOOK_PAGE_ACCESS_TOKEN no configurados en .env")

    video_path = Path(video_path)
    data = {"description": message}
    if _place():
        data["place"] = _place()
    with open(video_path, "rb") as vid:
        resp = requests.post(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/videos",
            params={"access_token": token},
            files={"source": (video_path.name, vid, "video/mp4")},
            data=data,
            timeout=300,
        )
    _raise_for_status(resp)
    data = resp.json()
    logger.debug(f"Facebook video id={data.get('id')}")
    return {"success": True, "id": data.get("id")}


def publish_video_story(video_path: Path) -> dict:
    """Publica un VIDEO como HISTORIA de la Página (flujo de subida en 3 fases).

    Las Page Video Stories usan subida reanudable: start → upload binario → finish.
    A veces la página necesita elegibilidad extra; si falla, el llamador lo loguea
    y sigue (Instagram no se ve afectado)."""
    page_id = get("FACEBOOK_PAGE_ID")
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise ValueError("FACEBOOK_PAGE_ID o FACEBOOK_PAGE_ACCESS_TOKEN no configurados en .env")

    video_path = Path(video_path)
    base = f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/video_stories"

    # 1) start → video_id + upload_url
    start = requests.post(base, params={"access_token": token}, data={"upload_phase": "start"}, timeout=60)
    _raise_for_status(start)
    sj = start.json()
    video_id = sj["video_id"]
    upload_url = sj["upload_url"]

    # 2) subir el binario al upload_url
    size = video_path.stat().st_size
    with open(video_path, "rb") as vid:
        up = requests.post(
            upload_url,
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
            data=vid.read(),
            timeout=300,
        )
    if up.status_code >= 400:
        raise RuntimeError(f"Facebook (subir video story): {up.status_code} {up.text[:200]}")

    # 3) finish → publica la historia
    finish = requests.post(base, params={"access_token": token},
                           data={"upload_phase": "finish", "video_id": video_id,
                                 "video_state": "PUBLISHED"}, timeout=120)
    _raise_for_status(finish)
    logger.debug(f"Facebook historia de video video_id={video_id}")
    return {"success": True, "id": video_id}


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

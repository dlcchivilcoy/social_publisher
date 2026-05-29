import tempfile
from pathlib import Path

import requests
from PIL import Image

from utils.config import get
from utils.image_host import upload_to_imgbb
from utils.logger import get_logger

logger = get_logger("instagram")

GRAPH_VERSION = "v19.0"
MAX_CAPTION = 2200  # límite de Instagram

# Instagram acepta proporciones (ancho/alto) entre 4:5 (0.8) y 1.91:1 (1.91).
MIN_RATIO = 0.8
MAX_RATIO = 1.91


def _as_jpeg(image_path: Path) -> Path:
    """Prepara la imagen para Instagram: la convierte a JPG y ajusta la proporción
    (agregando borde blanco, sin recortar) si está fuera del rango permitido.
    Devuelve un archivo temporal nuevo si hizo falta algún cambio, o la original si ya servía."""
    img = Image.open(image_path)
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    ratio = w / h
    needs_pad = ratio < MIN_RATIO or ratio > MAX_RATIO
    is_jpg = image_path.suffix.lower() in (".jpg", ".jpeg")

    if not needs_pad and is_jpg:
        return image_path  # ya sirve tal cual

    if needs_pad:
        if ratio < MIN_RATIO:
            # Muy angosta/alta → ensancho el lienzo
            new_w = round(h * MIN_RATIO)
            new_h = h
        else:
            # Muy ancha → agrando el alto
            new_w = w
            new_h = round(w / MAX_RATIO)
        canvas = Image.new("RGB", (new_w, new_h), (255, 255, 255))
        canvas.paste(img, ((new_w - w) // 2, (new_h - h) // 2))
        img = canvas
        logger.debug(f"Proporción ajustada para Instagram: {w}x{h} → {new_w}x{new_h}")

    tmp = Path(tempfile.gettempdir()) / (image_path.stem + "_ig.jpg")
    img.save(tmp, "JPEG", quality=90)
    logger.debug(f"Imagen preparada para Instagram: {tmp.name}")
    return tmp


def publish(body: str, image_path: Path) -> dict:
    user_id = get("INSTAGRAM_USER_ID")
    token = get("INSTAGRAM_ACCESS_TOKEN")
    if not user_id or not token:
        raise ValueError("INSTAGRAM_USER_ID o INSTAGRAM_ACCESS_TOKEN no configurados en .env")

    caption = body[:MAX_CAPTION]
    jpeg_path = _as_jpeg(image_path)
    temp_created = jpeg_path != image_path

    try:
        image_url = upload_to_imgbb(jpeg_path)

        # Paso 1: crear contenedor
        container = requests.post(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{user_id}/media",
            params={"access_token": token},
            data={"image_url": image_url, "caption": caption},
            timeout=30,
        )
        _raise_for_status(container, "crear contenedor")
        creation_id = container.json()["id"]

        # Paso 2: publicar contenedor
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
    finally:
        if temp_created and jpeg_path.exists():
            jpeg_path.unlink()


def _raise_for_status(resp: requests.Response, step: str) -> None:
    if resp.status_code == 401:
        raise PermissionError(f"Instagram ({step}): token inválido o expirado (401)")
    if resp.status_code == 403:
        raise PermissionError(f"Instagram ({step}): permisos insuficientes (403)")
    if resp.status_code == 429:
        raise RuntimeError(f"Instagram ({step}): límite de tasa (429) — se reintentará la próxima vez")
    if resp.status_code >= 400:
        raise RuntimeError(f"Instagram ({step}): {resp.status_code} {resp.text[:200]}")

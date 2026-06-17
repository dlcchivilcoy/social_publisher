import tempfile
import time
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
# Lado máximo de la imagen al subirla. Instagram la muestra a ~1080px; mandarla
# más liviana evita que su descarga se agote (error 2207003 con imágenes pesadas
# como la tapa en alta resolución).
MAX_DIM = 1440


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
    too_big = max(w, h) > MAX_DIM
    is_jpg = image_path.suffix.lower() in (".jpg", ".jpeg")

    if not needs_pad and not too_big and is_jpg:
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

    # Achicar para que Instagram pueda descargarla rápido (evita timeout 2207003).
    if max(img.size) > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)

    tmp = Path(tempfile.gettempdir()) / (image_path.stem + "_ig.jpg")
    img.save(tmp, "JPEG", quality=85, optimize=True)
    logger.debug(f"Imagen preparada para Instagram: {tmp.name}")
    return tmp


def _wait_container_ready(creation_id: str, token: str, *, timeout: int = 90, intervalo: int = 3) -> None:
    """Espera a que Instagram TERMINE de procesar la imagen del contenedor antes
    de publicarlo. Sin esto, publicar de inmediato una imagen grande (p. ej. la
    tapa) falla con 'Media ID is not available' (code 9007 / subcode 2207027)
    porque el medio todavía está en proceso. Consulta el estado del contenedor
    hasta que quede en FINISHED (o falla si da ERROR/EXPIRED o se agota el tiempo)."""
    fin = time.time() + timeout
    while time.time() < fin:
        r = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{creation_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        )
        estado = r.json().get("status_code") if r.ok else None
        if estado == "FINISHED":
            return
        if estado in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram: el medio quedó en estado {estado} al procesar la imagen")
        time.sleep(intervalo)
    raise RuntimeError("Instagram: el medio no terminó de procesarse a tiempo (timeout)")


def _crear_contenedor(user_id: str, token: str, data: dict, *, intentos: int = 3) -> str:
    """Crea el contenedor de media y devuelve su id. Reintenta ante el timeout
    transitorio de Instagram al descargar la imagen (subcode 2207003)."""
    ultimo = None
    for i in range(intentos):
        resp = requests.post(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{user_id}/media",
            params={"access_token": token},
            data=data,
            timeout=60,
        )
        if resp.ok:
            return resp.json()["id"]
        ultimo = resp
        if "2207003" in resp.text and i < intentos - 1:
            logger.warning(f"Instagram tardó en descargar la imagen; reintento {i + 1}/{intentos - 1}…")
            time.sleep(5)
            continue
        break
    _raise_for_status(ultimo, "crear contenedor")


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

        # Paso 1: crear contenedor (reintenta ante timeout de descarga 2207003)
        creation_id = _crear_contenedor(user_id, token, {"image_url": image_url, "caption": caption})

        # Paso 1.5: esperar a que Instagram procese la imagen (evita 2207027)
        _wait_container_ready(creation_id, token)

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


MAX_CAROUSEL = 10  # Instagram permite hasta 10 imágenes por carrusel


def publish_carousel(caption: str, image_paths: list[Path]) -> dict:
    """Publica un CARRUSEL (varias imágenes en un solo posteo) en Instagram.

    Flujo: por cada imagen se crea un contenedor hijo (is_carousel_item=true);
    luego un contenedor padre media_type=CAROUSEL con todos los hijos; se publica.
    IG exige entre 2 y 10 imágenes (si llega 1, cae a publish() simple; si llegan
    más de 10, toma las primeras 10). Todas las imágenes deben tener la MISMA
    proporción (el compositor las genera 1080x1350)."""
    user_id = get("INSTAGRAM_USER_ID")
    token = get("INSTAGRAM_ACCESS_TOKEN")
    if not user_id or not token:
        raise ValueError("INSTAGRAM_USER_ID o INSTAGRAM_ACCESS_TOKEN no configurados en .env")

    paths = list(image_paths)[:MAX_CAROUSEL]
    if len(paths) < 2:
        return publish(caption, paths[0])

    caption = caption[:MAX_CAPTION]
    temps: list[Path] = []
    try:
        child_ids: list[str] = []
        for p in paths:
            jpeg = _as_jpeg(p)
            if jpeg != p:
                temps.append(jpeg)
            url = upload_to_imgbb(jpeg)
            cid = _crear_contenedor(user_id, token, {"image_url": url, "is_carousel_item": "true"})
            _wait_container_ready(cid, token)
            child_ids.append(cid)

        carousel_id = _crear_contenedor(user_id, token, {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
        })
        _wait_container_ready(carousel_id, token)

        publish_resp = requests.post(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{user_id}/media_publish",
            params={"access_token": token},
            data={"creation_id": carousel_id},
            timeout=30,
        )
        _raise_for_status(publish_resp, "publicar carrusel")
        media_id = publish_resp.json()["id"]
        logger.debug(f"Instagram carrusel publicado id={media_id} ({len(child_ids)} imágenes)")
        return {"success": True, "id": media_id}
    finally:
        for t in temps:
            if t.exists():
                t.unlink()


def publish_story(image_path: Path) -> dict:
    """Publica la imagen como HISTORIA (story) de Instagram.

    Las historias por API no llevan caption ni stickers: solo la imagen.
    Requiere una URL pública (ImgBB) y el permiso instagram_content_publish.
    """
    user_id = get("INSTAGRAM_USER_ID")
    token = get("INSTAGRAM_ACCESS_TOKEN")
    if not user_id or not token:
        raise ValueError("INSTAGRAM_USER_ID o INSTAGRAM_ACCESS_TOKEN no configurados en .env")

    # OJO: las historias son 9:16 (ratio 0.5625). NO usar _as_jpeg() acá porque
    # rellenaría con bordes para forzar la proporción del feed y rompería el 9:16.
    # La imagen ya viene 1080x1920 JPG del compositor; solo la subimos.
    image_url = upload_to_imgbb(image_path)

    creation_id = _crear_contenedor(user_id, token, {"media_type": "STORIES", "image_url": image_url})

    # Esperar a que Instagram procese la imagen antes de publicar (evita 2207027)
    _wait_container_ready(creation_id, token)

    publish_resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{user_id}/media_publish",
        params={"access_token": token},
        data={"creation_id": creation_id},
        timeout=30,
    )
    _raise_for_status(publish_resp, "publicar story")
    media_id = publish_resp.json()["id"]
    logger.debug(f"Instagram story publicada id={media_id}")
    return {"success": True, "id": media_id}


def _wait_container_ready_long(creation_id: str, token: str, *, timeout: int = 300, intervalo: int = 5) -> None:
    """Igual que _wait_container_ready pero con timeout amplio: procesar un VIDEO
    (reel/historia de video) tarda mucho más que una imagen."""
    _wait_container_ready(creation_id, token, timeout=timeout, intervalo=intervalo)


def publish_reel(video_url: str, caption: str) -> dict:
    """Publica un REEL (video vertical) en Instagram a partir de una URL pública del .mp4.

    Flujo: crear contenedor media_type=REELS con video_url → esperar a que IG
    termine de procesar el video (FINISHED, puede tardar minutos) → media_publish.
    """
    user_id = get("INSTAGRAM_USER_ID")
    token = get("INSTAGRAM_ACCESS_TOKEN")
    if not user_id or not token:
        raise ValueError("INSTAGRAM_USER_ID o INSTAGRAM_ACCESS_TOKEN no configurados en .env")

    creation_id = _crear_contenedor(user_id, token, {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption[:MAX_CAPTION],
        "share_to_feed": "true",
    })
    _wait_container_ready_long(creation_id, token)

    publish_resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{user_id}/media_publish",
        params={"access_token": token},
        data={"creation_id": creation_id},
        timeout=60,
    )
    _raise_for_status(publish_resp, "publicar reel")
    media_id = publish_resp.json()["id"]
    logger.debug(f"Instagram reel publicado id={media_id}")
    return {"success": True, "id": media_id}


def publish_video_story(video_url: str) -> dict:
    """Publica un VIDEO como HISTORIA de Instagram (media_type=STORIES + video_url)."""
    user_id = get("INSTAGRAM_USER_ID")
    token = get("INSTAGRAM_ACCESS_TOKEN")
    if not user_id or not token:
        raise ValueError("INSTAGRAM_USER_ID o INSTAGRAM_ACCESS_TOKEN no configurados en .env")

    creation_id = _crear_contenedor(user_id, token, {"media_type": "STORIES", "video_url": video_url})
    _wait_container_ready_long(creation_id, token)

    publish_resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{user_id}/media_publish",
        params={"access_token": token},
        data={"creation_id": creation_id},
        timeout=60,
    )
    _raise_for_status(publish_resp, "publicar historia de video")
    media_id = publish_resp.json()["id"]
    logger.debug(f"Instagram historia de video publicada id={media_id}")
    return {"success": True, "id": media_id}


def _raise_for_status(resp: requests.Response, step: str) -> None:
    if resp.status_code == 401:
        raise PermissionError(f"Instagram ({step}): token inválido o expirado (401)")
    if resp.status_code == 403:
        raise PermissionError(f"Instagram ({step}): permisos insuficientes (403)")
    if resp.status_code == 429:
        raise RuntimeError(f"Instagram ({step}): límite de tasa (429) — se reintentará la próxima vez")
    if resp.status_code >= 400:
        raise RuntimeError(f"Instagram ({step}): {resp.status_code} {resp.text[:200]}")

import json
import time
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


def permalink(object_id: str) -> str:
    """URL pública del posteo/video de Facebook a partir de su id (best-effort; "" si falla)."""
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not object_id or not token:
        return ""
    try:
        r = requests.get(f"https://graph.facebook.com/{GRAPH_VERSION}/{object_id}",
                         params={"fields": "permalink_url", "access_token": token}, timeout=20)
        if r.ok:
            u = (r.json() or {}).get("permalink_url") or ""
            return ("https://www.facebook.com" + u) if u.startswith("/") else u
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude obtener el permalink de FB {object_id}: {e}")
    return ""


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


def publish_link(link: str, message: str = "") -> dict:
    """Publica SOLO un LINK en el muro de la Página (sin subir foto ni texto). Facebook
    arma la previsualización solo (foto + título + descripción) a partir de las etiquetas
    Open Graph de la nota. `message` opcional (por defecto vacío = solo el link)."""
    page_id = get("FACEBOOK_PAGE_ID")
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise ValueError("FACEBOOK_PAGE_ID o FACEBOOK_PAGE_ACCESS_TOKEN no configurados en .env")
    data = {"link": link}
    if message:
        data["message"] = message
    resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/feed",
        params={"access_token": token},
        data=data,
        timeout=60,
    )
    _raise_for_status(resp)
    d = resp.json()
    logger.debug(f"Facebook link post_id={d.get('id')}")
    return {"success": True, "id": d.get("id")}


def scrape_preview(url: str) -> dict:
    """Fuerza a Facebook a REFRESCAR (scrape) la vista previa de una URL y devuelve lo
    que extrajo: {'title', 'image'}. Es el equivalente por API al Depurador de
    Compartidos ('Scrape Again'): NO publica nada ni toca ningún posteo — solo
    actualiza la caché de la vista previa del link. Se usa para que, al postear el
    link de una nota, la tarjeta tome la FOTO PROPIA de la nota (og:image) y no la
    foto por defecto del sitio (lo que pasa cuando FB scrapeó la nota antes de tiempo
    y se quedó con esa foto cacheada ~30 días)."""
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not token:
        raise ValueError("FACEBOOK_PAGE_ACCESS_TOKEN no configurado en .env")
    resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/",
        params={"id": url, "scrape": "true", "access_token": token},
        timeout=60,
    )
    _raise_for_status(resp)
    d = resp.json()
    # La foto extraída aparece de forma fiable en image[]; og_object a veces viene vacío.
    img = d.get("image")
    if isinstance(img, list):
        img = img[0] if img else {}
    og = d.get("og_object") or {}
    og_img = og.get("image")
    if isinstance(og_img, list):
        og_img = og_img[0] if og_img else {}
    image_url = (img or {}).get("url") or (og_img or {}).get("url") or ""
    return {"title": og.get("title") or "", "image": image_url}


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
    """Publica un REEL en la Página. Intenta primero la API de REELS
    (/{page}/video_reels, 3 fases) para que salga como Reel de verdad (mejor alcance,
    aparece en la pestaña Reels). Si falla (elegibilidad, error de la API), cae al
    método clásico /{page}/videos (video común) para no perder el posteo."""
    video_path = Path(video_path)
    try:
        out = _publish_reel(message, video_path)
        logger.debug(f"Facebook REEL id={out.get('id')}")
        return out
    except Exception as e:
        logger.warning(f"Facebook: la API de Reels falló ({e}); reintento como video clásico (/videos).")
        out = _publish_video_clasico(message, video_path)
        logger.debug(f"Facebook video (fallback) id={out.get('id')}")
        return out


def _publish_reel(message: str, video_path: Path) -> dict:
    """Sube un Reel a /{page}/video_reels en 3 fases: start → subir binario → finish."""
    page_id = get("FACEBOOK_PAGE_ID")
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise ValueError("FACEBOOK_PAGE_ID o FACEBOOK_PAGE_ACCESS_TOKEN no configurados en .env")

    base = f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/video_reels"

    # 1) start → video_id + upload_url
    start = requests.post(base, params={"access_token": token},
                          data={"upload_phase": "start"}, timeout=60)
    _raise_for_status(start)
    sj = start.json()
    video_id = sj["video_id"]
    upload_url = sj.get("upload_url") or f"https://rupload.facebook.com/video-upload/{GRAPH_VERSION}/{video_id}"

    # 2) subir el binario
    size = video_path.stat().st_size
    with open(video_path, "rb") as vid:
        up = requests.post(
            upload_url,
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
            data=vid.read(),
            timeout=300,
        )
    if up.status_code >= 400:
        raise RuntimeError(f"subir reel: {up.status_code} {up.text[:200]}")

    # 3) finish → dispara la publicación del reel
    finish_data = {"upload_phase": "finish", "video_id": video_id,
                   "video_state": "PUBLISHED", "description": message}
    if _place():
        finish_data["place"] = _place()
    finish = requests.post(base, params={"access_token": token}, data=finish_data, timeout=120)
    _raise_for_status(finish)

    # ⚠️ El "finish" devuelve 200 al instante, pero el reel entra en PROCESAMIENTO
    # asíncrono de Facebook y puede FALLAR ahí SIN excepción → el reel nunca aparece
    # aunque hubiéramos dicho "ok". Por eso esperamos a que el estado sea publicado;
    # si falla o no termina a tiempo, lanzamos para caer al video clásico (/videos),
    # que sí publica de forma confiable.
    status_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}"
    for _ in range(15):  # ~ hasta 2,5 min
        time.sleep(10)
        st = requests.get(status_url, params={"access_token": token,
                                              "fields": "status"}, timeout=60)
        if st.status_code >= 400:
            continue
        status = st.json().get("status") or {}
        vstatus = str(status.get("video_status") or "").lower()
        pub = str((status.get("publishing_phase") or {}).get("status") or "").lower()
        if vstatus in ("ready", "published") or pub == "complete":
            return {"success": True, "id": video_id}
        if vstatus in ("error", "expired") or pub in ("error", "failed"):
            raise RuntimeError(f"el reel falló en el procesamiento de FB (status={status}).")
    raise RuntimeError("el reel no terminó de publicarse en FB (timeout de procesamiento).")


def _publish_video_clasico(message: str, video_path: Path) -> dict:
    """Publica el .mp4 directo a /{page}/videos (video común). Fallback del Reel."""
    page_id = get("FACEBOOK_PAGE_ID")
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise ValueError("FACEBOOK_PAGE_ID o FACEBOOK_PAGE_ACCESS_TOKEN no configurados en .env")

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
    return {"success": True, "id": resp.json().get("id")}


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


def video_insights(video_id: str) -> dict:
    """Estadísticas de un video de la página (para el ranking de corresponsales):
    {vistas, likes, comentarios, shares, alcance}. Best-effort: si falla, devuelve {}.

    Las COMPARTIDAS no están en el objeto video: viven en el POSTEO que lo contiene, y hay
    que pedirlas con el id COMPLETO `{page_id}_{post_id}` — con el post_id pelado la API
    responde «(#12) singular statuses API is deprecated». Requiere el permiso
    `pages_read_user_content` (agregado el 2026-08-31); sin él, `shares` queda en 0.
    Cada dato se pide por separado a propósito: si uno falla, los demás igual salen."""
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not token or not video_id:
        return {}
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    out = {"vistas": 0, "likes": 0, "comentarios": 0, "shares": 0, "alcance": 0}
    try:
        d = requests.get(f"{base}/{video_id}", timeout=30, params={
            "fields": "views,likes.summary(true),comments.summary(true),post_id",
            "access_token": token}).json()
        if "error" in d:
            logger.warning(f"[fb insights] {video_id}: {d['error'].get('message')}")
            return {}
        out["vistas"] = int(d.get("views") or 0)
        out["likes"] = int(((d.get("likes") or {}).get("summary") or {}).get("total_count") or 0)
        out["comentarios"] = int(((d.get("comments") or {}).get("summary") or {}).get("total_count") or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[fb insights] {video_id}: {e}")
        return {}

    post_id = d.get("post_id") or ""
    if post_id:
        pid = post_id if "_" in post_id else f"{get('FACEBOOK_PAGE_ID')}_{post_id}"
        try:
            s = requests.get(f"{base}/{pid}", timeout=30,
                             params={"fields": "shares", "access_token": token}).json()
            # Un posteo SIN compartidas no trae la clave `shares`: eso es 0, no un error.
            out["shares"] = int((s.get("shares") or {}).get("count") or 0)
            if "error" in s:
                logger.debug(f"[fb shares] {pid}: {s['error'].get('message')}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[fb shares] {pid}: {e}")
        out["alcance"] = _alcance_posteo(pid, token)
    return out


def _alcance_posteo(post_id: str, token: str) -> int:
    """Alcance del posteo. Hoy devuelve 0: Facebook RETIRÓ la métrica.

    Verificado el 2026-08-31 con `read_insights` ya otorgado: `post_impressions_unique` y
    `post_impressions` responden «(#100) The value must be a valid insights metric». O sea
    que no es un problema de permisos, la métrica ya no existe en esta versión de la API.

    No se prueban nombres a ciegas porque serían dos llamadas fallidas por cada video. Si en
    algún momento aparece el nombre correcto, se carga en `FB_METRICA_ALCANCE` y empieza a
    funcionar sin tocar el código. Sin alcance, el ranking usa las VISTAS para Facebook."""
    metrica = (get("FB_METRICA_ALCANCE") or "").strip()
    if not metrica:
        return 0
    try:
        r = requests.get(f"https://graph.facebook.com/{GRAPH_VERSION}/{post_id}/insights",
                         params={"metric": metrica, "access_token": token}, timeout=30).json()
        datos = r.get("data") or []
        return int(datos[0]["values"][0].get("value") or 0) if datos and datos[0].get("values") else 0
    except Exception:  # noqa: BLE001
        return 0

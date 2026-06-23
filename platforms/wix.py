import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from utils.config import get
from utils.image_host import upload_to_imgbb
from utils.logger import get_logger

logger = get_logger("wix")

# Zona horaria de Argentina (UTC-3) para las fechas de los datos estructurados.
TZ_AR = timezone(timedelta(hours=-3))


# ── SEO ───────────────────────────────────────────────────────────────────────
def _slugify(text: str, max_len: int = 70) -> str:
    """URL limpia: sin acentos ni ñ, minúsculas, separadas por guiones."""
    t = unicodedata.normalize("NFD", text or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")  # quita tildes
    t = t.replace("ñ", "n").replace("Ñ", "n").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if len(t) > max_len:
        t = t[:max_len].rsplit("-", 1)[0]
    return t or "nota"


def _meta_descripcion(description: str, body: str, limit: int = 155) -> str:
    texto = (description or "").strip() or (body or "").split("\n")[0].strip()
    texto = re.sub(r"\s+", " ", texto)
    if len(texto) <= limit:
        return texto
    return texto[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def _titulo_wix(title: str, limit: int = 200) -> str:
    """Wix exige título de máx. 200 caracteres. Recorta prolijo (en una palabra,
    con '…') si se pasa; si no, lo deja igual. Evita el error 400 de draftPost.title."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    if len(t) <= limit:
        return t
    corte = t[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return (corte + "…") if corte else t[:limit]


def _marca() -> str:
    return get("SEO_PUBLISHER_NAME") or "Diario La Campaña"


def _sitio_url() -> str:
    """URL canónica del sitio (con https). Configurable; usa el dominio con ñ."""
    raw = (get("STORY_SITE_URL") or "www.diariolacampaña.com.ar").strip()
    raw = re.sub(r"^https?://", "", raw).strip("/")
    return f"https://{raw}"


def _json_ld_newsarticle(title: str, descripcion: str, image_url: str) -> str:
    """Datos estructurados NewsArticle (Schema.org) para Google Noticias/Discover.

    Le dice a Google que esto es una NOTICIA: titular, imagen, fecha, autor y
    editor. Es lo que habilita los carruseles de noticias y la pestaña Noticias.
    """
    ahora = datetime.now(TZ_AR).isoformat(timespec="seconds")
    headline = (title or "").strip()
    if len(headline) > 110:  # Google recomienda titulares de hasta 110 caracteres
        headline = headline[:110].rsplit(" ", 1)[0]

    data = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": headline,
        "description": descripcion,
        "datePublished": ahora,
        "dateModified": ahora,
        "inLanguage": "es-AR",
        "author": {"@type": "Organization", "name": _marca(), "url": _sitio_url()},
        "publisher": {
            "@type": "Organization",
            "name": _marca(),
            "url": _sitio_url(),
        },
    }
    if image_url:
        data["image"] = [image_url]
    logo = get("SEO_PUBLISHER_LOGO_URL")
    if logo:
        data["publisher"]["logo"] = {"@type": "ImageObject", "url": logo}
    return json.dumps(data, ensure_ascii=False)


def _seo_tags(title: str, descripcion: str, image_url: str) -> list[dict]:
    ahora = datetime.now(TZ_AR).isoformat(timespec="seconds")
    tags = [
        {"type": "title", "children": title, "custom": False, "disabled": False},
        {"type": "meta", "props": {"name": "description", "content": descripcion},
         "custom": False, "disabled": False},
        {"type": "meta", "props": {"property": "og:title", "content": title}},
        {"type": "meta", "props": {"property": "og:description", "content": descripcion}},
        {"type": "meta", "props": {"property": "og:type", "content": "article"}},
        {"type": "meta", "props": {"property": "og:site_name", "content": _marca()}},
        {"type": "meta", "props": {"property": "og:locale", "content": "es_AR"}},
        # Señales de artículo de noticias (fecha y autor) para buscadores.
        {"type": "meta", "props": {"property": "article:published_time", "content": ahora}},
        {"type": "meta", "props": {"property": "article:modified_time", "content": ahora}},
        {"type": "meta", "props": {"property": "article:author", "content": _marca()}},
        {"type": "meta", "props": {"name": "twitter:card", "content": "summary_large_image"}},
        {"type": "meta", "props": {"name": "twitter:title", "content": title}},
        {"type": "meta", "props": {"name": "twitter:description", "content": descripcion}},
    ]
    if image_url:
        tags.append({"type": "meta", "props": {"property": "og:image", "content": image_url}})
        tags.append({"type": "meta", "props": {"property": "og:image:alt", "content": title}})
        tags.append({"type": "meta", "props": {"name": "twitter:image", "content": image_url}})
    # Datos estructurados NewsArticle (JSON-LD) — lo más importante para Google Noticias.
    tags.append({
        "type": "script",
        "props": {"type": "application/ld+json"},
        "children": _json_ld_newsarticle(title, descripcion, image_url),
        "custom": True,
        "disabled": False,
    })
    return tags

POSTS_QUERY_URL = "https://www.wixapis.com/blog/v3/posts/query"
MEDIA_IMPORT_URL = "https://www.wixapis.com/site-media/v1/files/import"
DRAFT_POSTS_URL = "https://www.wixapis.com/blog/v3/draft-posts"


def _headers() -> dict:
    api_key = get("WIX_API_KEY")
    site_id = get("WIX_SITE_ID")
    if not api_key or not site_id:
        raise ValueError("WIX_API_KEY o WIX_SITE_ID no configurados en .env")
    return {"Authorization": api_key, "wix-site-id": site_id, "Content-Type": "application/json"}


def _get_member_id(headers: dict) -> str:
    """Toma el autor de un post existente (o el de .env si está definido)."""
    configured = get("WIX_MEMBER_ID")
    if configured:
        return configured
    r = requests.post(POSTS_QUERY_URL, headers=headers, json={"query": {"paging": {"limit": 1}}}, timeout=30)
    _raise_for_status(r, "buscar autor")
    posts = r.json().get("posts", [])
    if not posts or not posts[0].get("memberId"):
        raise RuntimeError("No se pudo determinar el autor (memberId) del blog. Definí WIX_MEMBER_ID en .env")
    return posts[0]["memberId"]


DEPORTES_PAGES = {8, 9}
LOCALES_PAGES  = {2, 3, 5, 7}


def _category_ids(page: int) -> list[str]:
    """Devuelve los IDs de categoría según el número de página."""
    inicio   = get("WIX_CAT_INICIO")   or ""
    locales  = get("WIX_CAT_LOCALES")  or ""
    deportes = get("WIX_CAT_DEPORTES") or ""

    cats = [c for c in [inicio] if c]          # Inicio siempre
    if page in DEPORTES_PAGES and deportes:
        cats.append(deportes)
    elif page in LOCALES_PAGES and locales:
        cats.append(locales)
    return cats


def _importar_imagen(headers: dict, image_path: Path, title: str) -> tuple[str, str]:
    """Sube la imagen a ImgBB (URL pública temporal) y la importa al Media Manager
    de Wix con nombre descriptivo. Devuelve (file_id, image_url)."""
    image_url = upload_to_imgbb(image_path)
    mime = "image/png" if Path(image_path).suffix.lower() == ".png" else "image/jpeg"
    nombre_archivo = _slugify(title)[:80] or "nota"
    imp = requests.post(MEDIA_IMPORT_URL, headers=headers,
                        json={"mediaType": "IMAGE", "url": image_url, "mimeType": mime,
                              "displayName": nombre_archivo}, timeout=30)
    _raise_for_status(imp, "importar imagen")
    return imp.json()["file"]["id"], image_url


def _importar_video(headers: dict, video_url: str, display_name: str) -> str:
    """Importa un .mp4 (desde una URL pública, ej. el GitHub Release del reel) al
    Media Manager de Wix como VIDEO. Devuelve el file_id para usarlo en un nodo VIDEO.

    El procesamiento del video es asíncrono: el file_id sirve igual, y el video queda
    listo a los pocos segundos (antes de que el editor termine de revisar el borrador).
    """
    imp = requests.post(MEDIA_IMPORT_URL, headers=headers,
                        json={"mediaType": "VIDEO", "url": video_url,
                              "mimeType": "video/mp4",
                              "displayName": (_slugify(display_name)[:80] or "video")},
                        timeout=60)
    _raise_for_status(imp, "importar video")
    return imp.json()["file"]["id"]


def crear_borrador(title: str, body: str, image_path: Path, page: int = 0,
                   description: str = "", video_url: str = "") -> dict:
    """Crea un BORRADOR (draft) en el blog de Wix SIN publicarlo. La foto va como
    portada + dentro del cuerpo; si se pasa `video_url`, se importa a Wix y se embebe
    un nodo VIDEO arriba del texto. Devuelve {draft_id, file_id, image_url}."""
    headers = _headers()
    member_id = _get_member_id(headers)
    title = _titulo_wix(title)  # Wix limita el título a 200 caracteres

    file_id, image_url = _importar_imagen(headers, image_path, title)

    paragraphs = [p for p in body.split("\n") if p.strip()]
    nodes = [{
        "type": "IMAGE",
        "id": "img0",
        "nodes": [],
        "imageData": {
            "containerData": {"width": {"size": "CONTENT"}, "alignment": "CENTER", "textWrap": True},
            "image": {"src": {"id": file_id}},
        },
    }]
    # Video nativo embebido (arriba del texto, debajo de la foto). Best-effort: si el
    # import falla, se sigue sin video (la nota igual sale con foto).
    if video_url:
        try:
            video_id = _importar_video(headers, video_url, title)
            nodes.append({
                "type": "VIDEO",
                "id": "video0",
                "nodes": [],
                "videoData": {
                    "containerData": {"width": {"size": "CONTENT"}, "alignment": "CENTER"},
                    "video": {"src": {"id": video_id}},
                },
            })
            logger.info(f"Video importado a Wix (file_id={video_id}) y embebido en la nota.")
        except Exception as e:
            logger.error(f"No se pudo embeber el video nativo en Wix: {e}. La nota sale sin video.")

    for i, para in enumerate(paragraphs):
        nodes.append({
            "type": "PARAGRAPH",
            "id": f"p{i}",
            "nodes": [{"type": "TEXT", "id": "", "textData": {"text": para, "decorations": []}}],
        })

    category_ids = _category_ids(page)
    descripcion = _meta_descripcion(description, body)
    slug = _slugify(title)
    seo_data = {
        "tags": _seo_tags(title, descripcion, image_url),
        "settings": {"preventAutoRedirect": False},
    }

    draft_payload = {
        "draftPost": {
            "title": title,
            "memberId": member_id,
            "categoryIds": category_ids,
            "featured": True,
            "richContent": {"nodes": nodes},
            "media": {"wixMedia": {"image": {"id": file_id}}, "displayed": True, "custom": True},
            "seoSlug": slug,
            "seoData": seo_data,
        }
    }
    draft = requests.post(DRAFT_POSTS_URL, headers=headers, json=draft_payload, timeout=30)
    _raise_for_status(draft, "crear borrador")
    draft_id = draft.json()["draftPost"]["id"]
    logger.info(f"Borrador de Wix creado (sin publicar): draft_id={draft_id}")
    return {"draft_id": draft_id, "file_id": file_id, "image_url": image_url}


def _youtube_id(url_or_id: str) -> str:
    """Saca el ID de 11 chars de cualquier forma de URL de YouTube (watch, youtu.be,
    shorts, embed) o lo devuelve tal cual si ya es el ID."""
    s = (url_or_id or "").strip()
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([0-9A-Za-z_-]{11})", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", s):
        return s
    return s


def _youtube_oembed(watch_url: str) -> dict:
    """oEmbed de YouTube (best-effort) para sacar miniatura y dimensiones del player."""
    try:
        r = requests.get("https://www.youtube.com/oembed",
                         params={"url": watch_url, "format": "json"}, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"oEmbed de YouTube falló: {e}")
    return {}


def _nodo_video_youtube(url_or_id: str) -> dict:
    """Nodo VIDEO de Ricos para un video EXTERNO de YouTube (reproductor embebido,
    responsive en móvil y escritorio). Usa la URL pública del video como `src.url`."""
    vid = _youtube_id(url_or_id)
    watch = f"https://www.youtube.com/watch?v={vid}"
    oe = _youtube_oembed(watch)
    video_data = {
        "containerData": {"width": {"size": "CONTENT"}, "alignment": "CENTER"},
        "video": {"src": {"url": watch}},
    }
    thumb = oe.get("thumbnail_url") or f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
    if thumb:
        video_data["thumbnail"] = {"src": {"url": thumb}}
        w, h = oe.get("thumbnail_width"), oe.get("thumbnail_height")
        if w and h:
            video_data["thumbnail"]["width"] = int(w)
            video_data["thumbnail"]["height"] = int(h)
    if oe.get("title"):
        video_data["title"] = oe["title"][:100]
    return {"type": "VIDEO", "id": f"yt-{vid}", "nodes": [], "videoData": video_data}


def _get_draft(headers: dict, draft_id: str) -> dict:
    r = requests.get(f"{DRAFT_POSTS_URL}/{draft_id}", headers=headers, timeout=30)
    _raise_for_status(r, "leer borrador")
    return r.json()["draftPost"]


def insertar_video_youtube(draft_id: str, youtube_url: str) -> bool:
    """Inserta el reproductor de YouTube DENTRO del borrador, DEBAJO de la imagen
    principal y ENCIMA del cuerpo. Si ya había un video (el nativo del paso 1), lo
    REEMPLAZA por el de YouTube (un solo reproductor). Re-envía `media` para NO perder
    la portada (gotcha de Wix al editar por API). El PATCH es atómico: si falla, el
    borrador queda intacto (con su video nativo) y la nota igual se puede publicar.
    """
    headers = _headers()
    draft = _get_draft(headers, draft_id)
    rich = draft.get("richContent") or {}
    nodes = list(rich.get("nodes") or [])
    if not nodes:
        # Sin contenido recuperado: NO tocamos el borrador (no queremos pisar el cuerpo).
        logger.warning(f"El borrador {draft_id} no devolvió richContent; no embebo el YouTube.")
        return False

    yt_node = _nodo_video_youtube(youtube_url)  # se arma primero (si falla, no toco nada)
    # Saca cualquier VIDEO previo (el nativo) para no duplicar reproductores.
    nodes = [n for n in nodes if n.get("type") != "VIDEO"]
    # Ubica después de la 1ª imagen (debajo de la foto principal); si no hay, al principio.
    idx = next((i for i, n in enumerate(nodes) if n.get("type") == "IMAGE"), -1)
    nodes.insert(idx + 1, yt_node)
    rich["nodes"] = nodes

    payload = {
        "draftPost": {"id": draft_id, "richContent": rich, "media": draft.get("media")},
        "fieldMask": ["richContent", "media"],  # media reenviado o se borra la portada
    }
    r = requests.patch(f"{DRAFT_POSTS_URL}/{draft_id}", headers=headers, json=payload, timeout=30)
    _raise_for_status(r, "embeber YouTube")
    logger.info(f"Reproductor de YouTube embebido en el borrador {draft_id}.")
    return True


def publicar_borrador(draft_id: str) -> dict:
    """Publica un borrador ya creado. Devuelve {success, id, url}."""
    headers = _headers()
    pub = requests.post(f"{DRAFT_POSTS_URL}/{draft_id}/publish", headers=headers, json={}, timeout=30)
    _raise_for_status(pub, "publicar")

    post_url = ""
    try:
        r_url = requests.post(
            POSTS_QUERY_URL, headers=headers,
            json={"query": {"filter": {"id": {"$eq": draft_id}}, "paging": {"limit": 1}}, "fieldsets": ["URL"]},
            timeout=30,
        )
        posts = r_url.json().get("posts", [])
        if posts:
            url_obj = posts[0].get("url", {})
            post_url = url_obj.get("base", "") + url_obj.get("path", "")
    except Exception as e:
        logger.warning(f"No se pudo obtener la URL del post: {e}")

    logger.debug(f"Wix post publicado, draft_id={draft_id}, url={post_url}")
    return {"success": True, "id": draft_id, "url": post_url}


def publish(title: str, body: str, image_path: Path, page: int = 0,
            description: str = "") -> dict:
    """Crea el borrador y lo publica de una (flujo normal del diario)."""
    info = crear_borrador(title, body, image_path, page=page, description=description)
    return publicar_borrador(info["draft_id"])


def _reel_headline(title: str) -> str:
    """Saca el titular limpio del 'title' de Wix (que viene 'VOLANTA — titular').
    Quita la volanta (parte antes del em-dash) y devuelve el titular COMPLETO, sin
    recortar: el reel necesita el título entero (la placa lo autoajusta para que
    entre sin puntos suspensivos)."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    for sep in (" — ", " – "):  # em-dash y en-dash con espacios
        if sep in t:
            izq, der = (s.strip() for s in t.split(sep, 1))
            # La parte izquierda suele ser la volanta/titular; pero si es muy corta
            # (ej. "TENIS"), el titular real está a la derecha.
            t = der if len(izq) < 20 and der else izq
            break
    return t


def top_posts_today(limit: int = 5) -> list[dict]:
    """Las notas MÁS LEÍDAS publicadas HOY (para el reel del cierre del día).

    Filtra por firstPublishedDate >= hoy 00:00 (hora AR), ordena por
    metrics.views DESC y toma las primeras `limit`. Devuelve por cada una:
    {headline, excerpt, image_url, views, url}. Si hoy no hay notas, lista vacía.
    """
    hoy0 = datetime.now(TZ_AR).replace(hour=0, minute=0, second=0, microsecond=0)
    body = {
        "query": {
            "filter": {"firstPublishedDate": {"$gte": hoy0.isoformat()}},
            "sort": [{"fieldName": "metrics.views", "order": "DESC"}],
            "paging": {"limit": max(1, limit)},
        },
        "fieldsets": ["METRICS", "URL"],
    }
    r = requests.post(POSTS_QUERY_URL, headers=_headers(), json=body, timeout=30)
    _raise_for_status(r, "consultar más leídas")
    out = []
    for p in r.json().get("posts", []):
        media = p.get("media") or {}
        img = (((media.get("wixMedia") or {}).get("image") or {}).get("url")) or media.get("url") or ""
        url = p.get("url", {})
        out.append({
            "title": p.get("title", ""),  # título completo (para emparejar con la nota local)
            "headline": _reel_headline(p.get("title", "")),
            "excerpt": re.sub(r"\s+", " ", (p.get("excerpt") or "").strip()),
            "image_url": img,
            "views": (p.get("metrics") or {}).get("views") or 0,
            "url": url.get("base", "") + url.get("path", ""),
        })
    logger.info(f"Top {len(out)} notas más leídas de hoy obtenidas de Wix")
    return out


def _raise_for_status(resp: requests.Response, step: str) -> None:
    if resp.status_code == 401:
        raise PermissionError(f"Wix ({step}): API key inválida (401) — revisá .env")
    if resp.status_code == 403:
        raise PermissionError(f"Wix ({step}): permisos insuficientes (403)")
    if resp.status_code == 429:
        raise RuntimeError(f"Wix ({step}): límite de tasa (429) — se reintentará la próxima vez")
    if resp.status_code >= 400:
        raise RuntimeError(f"Wix ({step}): {resp.status_code} {resp.text[:200]}")

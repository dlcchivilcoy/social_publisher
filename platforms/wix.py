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


def publish(title: str, body: str, image_path: Path, page: int = 0,
            description: str = "") -> dict:
    headers = _headers()
    member_id = _get_member_id(headers)

    # 1) Imagen pública temporal (ImgBB)
    image_url = upload_to_imgbb(image_path)

    # 2) Importar al Media Manager de Wix (con nombre descriptivo = titular,
    #    así la imagen aporta a Google Imágenes en vez de un nombre genérico).
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    nombre_archivo = _slugify(title)[:80] or "nota"
    imp = requests.post(MEDIA_IMPORT_URL, headers=headers,
                        json={"mediaType": "IMAGE", "url": image_url, "mimeType": mime,
                              "displayName": nombre_archivo}, timeout=30)
    _raise_for_status(imp, "importar imagen")
    file_id = imp.json()["file"]["id"]

    # 3) Crear el borrador del post con categorías
    paragraphs = [p for p in body.split("\n") if p.strip()]
    nodes = []
    for i, para in enumerate(paragraphs):
        nodes.append({
            "type": "PARAGRAPH",
            "id": f"p{i}",
            "nodes": [{"type": "TEXT", "id": "", "textData": {"text": para, "decorations": []}}],
        })

    category_ids = _category_ids(page)
    featured = True  # TODAS las notas se muestran en Inicio (la portada muestra las destacadas)
    logger.debug(f"Wix categorías para página {page}: {category_ids}, featured: {featured}")

    # SEO: meta descripción, slug limpio (sin acentos) y etiquetas Open Graph/Twitter
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
            "featured": featured,
            "richContent": {"nodes": nodes},
            "media": {"wixMedia": {"image": {"id": file_id}}, "displayed": True, "custom": True},
            "seoSlug": slug,
            "seoData": seo_data,
        }
    }
    draft = requests.post(DRAFT_POSTS_URL, headers=headers, json=draft_payload, timeout=30)
    _raise_for_status(draft, "crear borrador")
    draft_id = draft.json()["draftPost"]["id"]

    # 4) Publicar el borrador
    pub = requests.post(f"{DRAFT_POSTS_URL}/{draft_id}/publish", headers=headers, json={}, timeout=30)
    _raise_for_status(pub, "publicar")

    # 5) Obtener la URL pública del post publicado
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


def _raise_for_status(resp: requests.Response, step: str) -> None:
    if resp.status_code == 401:
        raise PermissionError(f"Wix ({step}): API key inválida (401) — revisá .env")
    if resp.status_code == 403:
        raise PermissionError(f"Wix ({step}): permisos insuficientes (403)")
    if resp.status_code == 429:
        raise RuntimeError(f"Wix ({step}): límite de tasa (429) — se reintentará la próxima vez")
    if resp.status_code >= 400:
        raise RuntimeError(f"Wix ({step}): {resp.status_code} {resp.text[:200]}")

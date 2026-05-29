from pathlib import Path

import requests

from utils.config import get
from utils.image_host import upload_to_imgbb
from utils.logger import get_logger

logger = get_logger("wix")

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


def publish(title: str, body: str, image_path: Path, page: int = 0) -> dict:
    headers = _headers()
    member_id = _get_member_id(headers)

    # 1) Imagen pública temporal (ImgBB)
    image_url = upload_to_imgbb(image_path)

    # 2) Importar al Media Manager de Wix
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    imp = requests.post(MEDIA_IMPORT_URL, headers=headers,
                        json={"mediaType": "IMAGE", "url": image_url, "mimeType": mime}, timeout=30)
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
    featured = page in DEPORTES_PAGES  # páginas 8 y 9 aparecen en el inicio como destacadas
    logger.debug(f"Wix categorías para página {page}: {category_ids}, featured: {featured}")

    draft_payload = {
        "draftPost": {
            "title": title,
            "memberId": member_id,
            "categoryIds": category_ids,
            "featured": featured,
            "richContent": {"nodes": nodes},
            "media": {"wixMedia": {"image": {"id": file_id}}, "displayed": True, "custom": True},
        }
    }
    draft = requests.post(DRAFT_POSTS_URL, headers=headers, json=draft_payload, timeout=30)
    _raise_for_status(draft, "crear borrador")
    draft_id = draft.json()["draftPost"]["id"]

    # 4) Publicar el borrador
    pub = requests.post(f"{DRAFT_POSTS_URL}/{draft_id}/publish", headers=headers, json={}, timeout=30)
    _raise_for_status(pub, "publicar")
    logger.debug(f"Wix post publicado, draft_id={draft_id}, categorías={category_ids}")
    return {"success": True, "id": draft_id}


def _raise_for_status(resp: requests.Response, step: str) -> None:
    if resp.status_code == 401:
        raise PermissionError(f"Wix ({step}): API key inválida (401) — revisá .env")
    if resp.status_code == 403:
        raise PermissionError(f"Wix ({step}): permisos insuficientes (403)")
    if resp.status_code == 429:
        raise RuntimeError(f"Wix ({step}): límite de tasa (429) — se reintentará la próxima vez")
    if resp.status_code >= 400:
        raise RuntimeError(f"Wix ({step}): {resp.status_code} {resp.text[:200]}")

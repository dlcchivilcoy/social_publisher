"""Métricas de la NOTA EN LA WEB (Astro + Supabase), por slug.

Por qué existe (2026-08-31): el ranking de corresponsales medía la web con
`wix.views_de_post`, que devuelve **0** — las notas se sirven desde el sitio nuevo (Astro)
y las métricas viven en Supabase, no en Wix. O sea que la web no estaba aportando NADA al
puntaje. Acá se leen las tres tablas que la web escribe:

    vistas       (slug, total)
    likes        (slug, visitante, creado)
    comentarios  (slug, nombre, texto, oculto, creado)

Best-effort: ante cualquier error devuelve ceros. Una métrica que no se pudo leer no puede
tumbar el cálculo del ranking.
"""
import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("web_metrics")


def _config() -> tuple[str, str]:
    return (get("SUPABASE_URL") or "").rstrip("/"), (get("SUPABASE_SERVICE_KEY") or "").strip()


def slug_de_url(url: str) -> str:
    """Saca el slug de la URL de la nota (`…/single-post/{slug}` o `…/n/{slug}`)."""
    limpia = (url or "").strip().rstrip("/")
    if not limpia:
        return ""
    return limpia.rsplit("/", 1)[-1].split("?", 1)[0].split("#", 1)[0]


def _contar(tabla: str, slug: str, extra: str = "") -> int:
    """Cantidad de filas de `tabla` para ese slug, usando el header Content-Range de
    PostgREST (`count=exact`): no se traen las filas, solo el número."""
    url_base, key = _config()
    if not url_base or not key or not slug:
        return 0
    try:
        r = requests.get(f"{url_base}/rest/v1/{tabla}",
                         params={"select": "slug", "slug": f"eq.{slug}", **({} if not extra else {"oculto": extra})},
                         headers={"apikey": key, "Authorization": f"Bearer {key}",
                                  "Prefer": "count=exact", "Range": "0-0"}, timeout=30)
        rango = r.headers.get("Content-Range", "")   # formato "0-0/123"
        return int(rango.rsplit("/", 1)[-1]) if "/" in rango else 0
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[web] {tabla} de «{slug}»: {e}")
        return 0


def metricas(slug_o_url: str) -> dict:
    """{vistas, likes, comentarios} de una nota de la web. Ceros si no se pudo leer."""
    slug = slug_o_url if "/" not in (slug_o_url or "") else slug_de_url(slug_o_url)
    out = {"vistas": 0, "likes": 0, "comentarios": 0}
    if not slug:
        return out
    url_base, key = _config()
    if not url_base or not key:
        logger.debug("[web] sin SUPABASE_URL / SUPABASE_SERVICE_KEY: la web no suma al ranking.")
        return out
    # `vistas` guarda un TOTAL acumulado en una fila, no una fila por vista.
    try:
        r = requests.get(f"{url_base}/rest/v1/vistas",
                         params={"select": "total", "slug": f"eq.{slug}", "limit": 1},
                         headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)
        filas = r.json() if r.status_code < 400 else []
        out["vistas"] = int((filas[0] or {}).get("total") or 0) if filas else 0
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[web] vistas de «{slug}»: {e}")
    out["likes"] = _contar("likes", slug)
    out["comentarios"] = _contar("comentarios", slug, extra="is.false")   # sin los ocultos
    return out

"""Reposteo automático de publicidad de comercios a la HISTORIA de @dlcchivilcoy.

Cuando una de las cuentas configuradas (REPOST_CUENTAS) publica una imagen cuyo
caption MENCIONA a @dlcchivilcoy (REPOST_TRIGGER), se descarga y se sube como
historia a @dlcchivilcoy. Anti-repetición por id de media (.repost.json).

⚠️ Requiere **Business Discovery** habilitado en la app de Meta (Acceso Avanzado
de instagram_basic). Hasta que Meta lo apruebe, la API responde error #10 y este
módulo AVISA y no hace nada (queda listo para activarse el día de la aprobación).
"""
import json
import tempfile
from pathlib import Path

import requests

from platforms import instagram
from story_image import compose_repost_story
from utils.config import get
from utils.logger import get_logger

logger = get_logger("repost")

GRAPH = "v19.0"
LEDGER = Path(__file__).parent / ".repost.json"

CUENTAS_DEFAULT = [
    "almendracafe.chivilcoy", "almacendemuebleschivilcoy", "solarium.express",
    "inmobiliariapommares", "repuestosmartino", "capurro.automotores_",
]


def _cuentas() -> list[str]:
    raw = get("REPOST_CUENTAS")
    if not raw:
        return CUENTAS_DEFAULT
    return [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]


def _trigger() -> str:
    return (get("REPOST_TRIGGER") or "@dlcchivilcoy").lower()


def _leer_ledger() -> set[str]:
    try:
        if LEDGER.exists():
            return set(json.loads(LEDGER.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()


def _guardar_ledger(ids: set[str]) -> None:
    LEDGER.write_text(json.dumps(sorted(ids)[-500:], ensure_ascii=False, indent=2), encoding="utf-8")


def _business_discovery(igid: str, token: str, username: str, limit: int = 5) -> list[dict]:
    fields = (f"business_discovery.username({username})"
              "{username,media.limit(" + str(limit) + ")"
              "{id,caption,media_type,media_url,permalink,timestamp}}")
    r = requests.get(f"https://graph.facebook.com/{GRAPH}/{igid}",
                     params={"fields": fields, "access_token": token}, timeout=30)
    j = r.json()
    if "error" in j:
        err = j["error"]
        raise RuntimeError(f"{err.get('message','')} (code {err.get('code')})")
    return j.get("business_discovery", {}).get("media", {}).get("data", [])


def _descargar(url: str) -> Path:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    p = Path(tempfile.gettempdir()) / "repost_src.jpg"
    p.write_bytes(r.content)
    return p


def run_repost(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Reposteo de publicidad [{modo}] ===")

    igid = get("INSTAGRAM_USER_ID")
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN") or get("INSTAGRAM_ACCESS_TOKEN")
    if not igid or not token:
        logger.error("Faltan INSTAGRAM_USER_ID / token en .env.")
        return

    cuentas = _cuentas()
    trigger = _trigger()
    logger.info(f"Cuentas: {', '.join('@' + c for c in cuentas)} | disparador: «{trigger}»")
    ledger = _leer_ledger()
    nuevos = 0

    for u in cuentas:
        try:
            media = _business_discovery(igid, token, u)
        except Exception as e:
            msg = str(e)
            if "code 10" in msg or "(#10)" in msg:
                logger.warning("Business Discovery todavía SIN permiso de Meta (#10). El reposteo se "
                               "activará cuando se apruebe el Acceso Avanzado de instagram_basic. "
                               "Por ahora no se hace nada.")
                return  # mismo permiso para todas: no tiene sentido seguir
            logger.error(f"No se pudo leer @{u}: {e}")
            continue

        for m in media:
            mid = m.get("id")
            cap = (m.get("caption") or "").lower()
            if not mid or mid in ledger:
                continue
            if trigger not in cap:
                continue
            if m.get("media_type") not in ("IMAGE", "CAROUSEL_ALBUM"):
                continue
            url = m.get("media_url")
            if not url:
                logger.info(f"@{u}: media {mid} sin media_url (carrusel/video); se omite.")
                continue

            logger.info(f"Publicidad a repostear de @{u}: {m.get('permalink')}")
            if dry_run:
                logger.info("   [dry-run] se subiría como historia (NO se publica).")
                continue
            try:
                src = _descargar(url)
                img = compose_repost_story(src, pie=f"Publicidad · @{u}")
                instagram.publish_story(img)
                ledger.add(mid)
                _guardar_ledger(ledger)
                nuevos += 1
                logger.info(f"   ✅ Historia republicada (@{u}).")
            except Exception as e:
                logger.error(f"   FALLÓ republicar de @{u}: {e}")

    logger.info(f"=== Reposteo: fin ({nuevos} historia/s) ===")

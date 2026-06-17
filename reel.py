"""Reel "Las 5 más leídas del día" (cierre del día, 20:00).

Arma un video vertical 9:16 SIN música con las 5 notas más leídas publicadas hoy
(según las vistas de Wix), con transiciones, estética blanco+naranja+logo, y lo
publica en Facebook (feed + historia) e Instagram (reel + historia).

Flujo:
  1. top-5 más leídas de hoy (Wix) → descarga las fotos.
  2. compone intro + 1 placa por nota (con badge de ranking) + outro.
  3. arma el .mp4 (ffmpeg/xfade, sin audio).
  4. lo sube a un GitHub Release (URL pública) para que Instagram lo baje.
  5. publica: IG reel + IG historia; FB video + FB historia.
  6. ledger .reel.json (1 reel por día).
"""
import json
import tempfile
from datetime import date
from pathlib import Path

import requests

from platforms import facebook, instagram, wix
from story_image import compose_reel_intro, compose_reel_outro, compose_reel_slide
from utils.logger import get_logger
from video import build_slideshow

logger = get_logger("reel")

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

LEDGER = Path(__file__).parent / ".reel.json"
PREVIEW_DIR = Path(__file__).parent / "historias_preview"


def _fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def _site() -> str:
    from utils.config import get
    return get("STORY_SITE_URL") or "www.diariolacampaña.com.ar"


def _platforms() -> list[str]:
    from utils.config import get
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _ya_publicado_hoy() -> bool:
    if not LEDGER.exists():
        return False
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8")).get("last") == date.today().isoformat()
    except Exception:
        return False


def _marcar_publicado() -> None:
    LEDGER.write_text(json.dumps({"last": date.today().isoformat()}, ensure_ascii=False), encoding="utf-8")


def _bajar_foto(url: str, idx: int) -> Path | None:
    if not url:
        return None
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        tmp = Path(tempfile.gettempdir()) / f"reel_foto_{idx}.jpg"
        tmp.write_bytes(r.content)
        return tmp
    except Exception as e:
        logger.warning(f"No se pudo bajar la foto #{idx}: {e}")
        return None


def run_reel(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Reel '5 más leídas' [{modo}] — {hoy.isoformat()} ===")

    if not dry_run and _ya_publicado_hoy():
        logger.info("El reel de hoy ya se publicó. Nada que hacer.")
        return

    posts = wix.top_posts_today(limit=5)
    if not posts:
        logger.info("No hay notas publicadas hoy: no se arma reel.")
        return
    logger.info("Ranking: " + " | ".join(f"{i+1}·{p['headline'][:30]} ({p['views']})" for i, p in enumerate(posts)))

    # Descargar fotos
    fotos = []
    for i, p in enumerate(posts, 1):
        fotos.append(_bajar_foto(p["image_url"], i))

    site = _site()
    placas: list[Path] = []
    placas.append(compose_reel_intro(_fecha_larga(hoy).capitalize(), photos=[f for f in fotos if f]))
    for i, p in enumerate(posts):
        placas.append(compose_reel_slide(fotos[i], p["headline"], p["excerpt"], rank=i + 1, views=p["views"]))
    placas.append(compose_reel_outro(site))

    PREVIEW_DIR.mkdir(exist_ok=True)
    mp4 = build_slideshow(placas, PREVIEW_DIR / "reel.mp4")

    # Caption
    titulares = "\n".join(f"{i+1}. {p['headline']}" for i, p in enumerate(posts))
    caption = (
        f"🔥 Las {len(posts)} noticias más leídas de hoy en Diario La Campaña\n\n"
        f"{titulares}\n\n"
        f"📲 Leelas completas en {site}\n\n"
        f"#Chivilcoy #DiarioLaCampaña #Noticias #LoMásLeído #Actualidad"
    )

    if dry_run:
        logger.info(f"[dry-run] reel armado: {mp4} ({len(placas)} placas)")
        logger.info(f"[dry-run] caption:\n{caption}")
        logger.info("=== Reel: fin (dry-run) ===")
        return

    plats = _platforms()
    algun_ok = False

    # Subir el mp4 a una URL pública (solo lo necesita Instagram)
    video_url = None
    if "instagram" in plats:
        try:
            from utils.video_host import upload_reel
            video_url = upload_reel(mp4)
        except Exception as e:
            logger.error(f"No se pudo hostear el reel para Instagram: {e}")

    # Instagram: reel + historia de video
    if "instagram" in plats and video_url:
        try:
            instagram.publish_reel(video_url, caption)
            algun_ok = True
            logger.info("[instagram] reel OK")
        except Exception as e:
            logger.error(f"[instagram] reel FALLÓ: {e}")
        try:
            instagram.publish_video_story(video_url)
            algun_ok = True
            logger.info("[instagram] historia de video OK")
        except Exception as e:
            logger.error(f"[instagram] historia de video FALLÓ: {e}")

    # Facebook: video al feed + historia de video (sube el archivo directo)
    if "facebook" in plats:
        try:
            facebook.publish_video(caption, mp4)
            algun_ok = True
            logger.info("[facebook] video OK")
        except Exception as e:
            logger.error(f"[facebook] video FALLÓ: {e}")
        try:
            facebook.publish_video_story(mp4)
            algun_ok = True
            logger.info("[facebook] historia de video OK")
        except Exception as e:
            logger.error(f"[facebook] historia de video FALLÓ: {e}")

    if algun_ok:
        _marcar_publicado()
        logger.info("Reel publicado y registrado.")
    else:
        logger.error("El reel NO se pudo publicar en ninguna red — se reintentará la próxima corrida.")

    logger.info("=== Reel: fin ===")

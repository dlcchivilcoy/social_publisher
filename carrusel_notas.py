"""Carrusel de NOTAS (10:00).

Un solo posteo en Facebook + Instagram con TODAS las notas del día (un slide por
nota: foto + titular + resumen breve), ordenadas por el número con que empieza el
nombre del archivo. Además:
  - publica cada nota en Wix/web (destino del "seguí leyendo"),
  - publica UNA historia "Noticias de hoy".
Reemplaza el feed por nota (07:00/13:00) y las historias por nota (07:15).
"""
import re
from datetime import date
from pathlib import Path

from file_scanner import find_notes
from platforms import facebook, instagram, wix
from publisher import _hashtags, _load_ledger, _prepare_image, _resumen, _save_ledger
from story_image import compose_note_slide, compose_noticias_hoy_story
from utils.config import get
from utils.logger import get_logger

logger = get_logger("carrusel_notas")

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def _site() -> str:
    return get("STORY_SITE_URL") or "www.diariolacampaña.com.ar"


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _orden(note: dict) -> int:
    """Número con que empieza el nombre del .docx (1, 2, 3…). Sin número → al final."""
    m = re.match(r"\s*(\d+)", note["docx"].name)
    return int(m.group(1)) if m else 9999


def run_notes_carousel(posts_folder: Path, allowed_pages: set[int], dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Carrusel de notas [{modo}] — {hoy.isoformat()} — carpeta: {posts_folder} ===")

    notes = find_notes(posts_folder, allowed_pages)
    if not notes:
        logger.info("No se encontraron notas para el carrusel.")
        return

    ledger = _load_ledger(posts_folder)
    pending = [n for n in notes if n["key"] not in ledger]
    if not pending:
        logger.info("Todas las notas de hoy ya estaban publicadas. Nada que hacer.")
        return

    # Orden definido por el número al inicio del nombre del archivo
    pending.sort(key=_orden)
    logger.info("Orden del carrusel: " + " | ".join(f"{_orden(n)}·{(n.get('titular') or n['title'])[:30]}" for n in pending))

    site = _site()
    slides: list[Path] = []
    wix_ok = 0

    for i, note in enumerate(pending):
        resumen = _resumen(note.get("cuerpo", ""), note.get("body", ""), limit=180)
        try:
            slide = compose_note_slide(note["image"], note.get("volanta", ""), note.get("titular", "") or note["title"],
                                       resumen, con_cta=(i == 0), site_url=site)
            slides.append(slide)
        except Exception as e:
            logger.error(f"No se pudo componer el slide de «{(note.get('titular') or note['title'])[:40]}»: {e}")
            continue

        # Publicar la nota en Wix (la web es el destino del "seguí leyendo")
        if not dry_run:
            try:
                img = _prepare_image(note["image"])
                descripcion = _resumen(note.get("cuerpo", ""), note.get("body", ""), limit=155)
                wix.publish(note["title"], note["body"], img, page=note["page"], description=descripcion)
                wix_ok += 1
                if img != note["image"] and img.exists():
                    img.unlink()
                logger.info(f"[wix] OK — «{(note.get('titular') or note['title'])[:40]}»")
            except Exception as e:
                logger.error(f"[wix] FALLÓ — «{(note.get('titular') or note['title'])[:40]}»: {e}")

    if not slides:
        logger.error("No se pudo componer ningún slide. Se aborta el carrusel.")
        return

    # Caption: intro + titulares + CTA + hashtags (con emojis)
    titulares = "\n".join(f"• {(n.get('titular') or n['title'])}" for n in pending[:10])
    tags = []
    for n in pending:
        for t in _hashtags(n).split():
            if t not in tags:
                tags.append(t)
    hashtags = " ".join(tags[:10])
    caption = (
        f"📰 Noticias de hoy — {_fecha_larga(hoy).capitalize()}\n\n"
        f"{titulares}\n\n"
        f"📲 Deslizá para ver todas y seguí leyendo cada nota completa en nuestra web 👉 {site}\n\n"
        f"{hashtags}"
    )

    story_img = compose_noticias_hoy_story(_fecha_larga(hoy).capitalize(), site)

    if dry_run:
        logger.info(f"[dry-run] carrusel de {len(slides)} slide(s): {[s.name for s in slides]}")
        logger.info(f"[dry-run] historia: {story_img.name}")
        logger.info(f"[dry-run] caption:\n{caption}")
        logger.info("=== Carrusel de notas: fin (dry-run) ===")
        return

    plats = _platforms()
    algun_ok = False

    if "facebook" in plats:
        try:
            facebook.publish_multi(caption, slides)
            algun_ok = True
            logger.info("[facebook] carrusel OK")
        except Exception as e:
            logger.error(f"[facebook] carrusel FALLÓ: {e}")
    if "instagram" in plats:
        try:
            instagram.publish_carousel(caption, slides)
            algun_ok = True
            logger.info("[instagram] carrusel OK")
        except Exception as e:
            logger.error(f"[instagram] carrusel FALLÓ: {e}")

    # UNA historia "Noticias de hoy"
    story_fns = {"instagram": lambda: instagram.publish_story(story_img),
                 "facebook": lambda: facebook.publish_story(story_img)}
    for name in plats:
        fn = story_fns.get(name)
        if not fn:
            continue
        try:
            fn()
            algun_ok = True
            logger.info(f"[{name}] historia 'Noticias de hoy' OK")
        except Exception as e:
            logger.error(f"[{name}] historia FALLÓ: {e}")

    if algun_ok:
        for n in pending:
            ledger.add(n["key"])
        _save_ledger(posts_folder, ledger)
        logger.info(f"{len(pending)} nota(s) registradas como publicadas. Wix OK: {wix_ok}/{len(pending)}.")
    else:
        logger.error("El carrusel de notas NO se pudo publicar — se reintentará la próxima corrida.")

    logger.info("=== Carrusel de notas: fin ===")

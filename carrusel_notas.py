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

from file_scanner import _normalize, _page_number, _pair_in_folder, find_todays_edition
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


def _parse_nota(docx_path):
    """Lee el .docx y devuelve (volanta, titular, cuerpo_parrafos).

    Detecta si la nota EMPIEZA con volanta: si el primer párrafo es corto
    (kicker/categoría) y hay más párrafos, es volanta y el segundo es el titular;
    si el primero es largo, NO hay volanta y ese primer párrafo es el titular."""
    from docx import Document
    paras = [p.text.strip() for p in Document(str(docx_path)).paragraphs if p.text.strip()]
    if not paras:
        return "", "", []
    es_volanta = len(paras) >= 2 and (len(paras[0]) <= 55 or len(paras[0].split()) <= 7)
    if es_volanta:
        return paras[0], paras[1], paras[2:]
    return "", paras[0], paras[1:]


def _find_notes(posts_folder: Path) -> list:
    """Busca las notas del día. Soporta DOS estructuras dentro de la carpeta de la
    edición de hoy: (1) notas SUELTAS numeradas en la raíz (estructura nueva del
    carrusel) y, si no hay, (2) subcarpetas 'PÁGINA X' (estructura vieja)."""
    ed = find_todays_edition(posts_folder)
    if ed is None:
        return []
    notes = []
    # (1) notas sueltas directamente en la carpeta de la edición (numeradas)
    for n in _pair_in_folder(ed):
        n["page"] = 0
        n["edition"] = ed.name
        n["key"] = f"{ed.name}|{n['docx'].name}"
        notes.append(n)
    # (2) compatibilidad: subcarpetas 'pagina X'
    if not notes:
        for sub in sorted(ed.rglob("*")):
            if sub.is_dir() and "pagina" in _normalize(sub.name):
                pg = _page_number(sub.name) or 0
                for n in _pair_in_folder(sub):
                    n["page"] = pg
                    n["edition"] = ed.name
                    n["key"] = f"{ed.name}|p{pg}|{n['docx'].name}"
                    notes.append(n)
    logger.info(f"{len(notes)} nota(s) encontrada(s) en la edición de hoy")
    return notes


def run_notes_carousel(posts_folder: Path, allowed_pages: set[int], dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Carrusel de notas [{modo}] — {hoy.isoformat()} — carpeta: {posts_folder} ===")

    notes = _find_notes(posts_folder)
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
    bajadas: list[tuple] = []  # (titular, primer_parrafo) para el caption
    wix_ok = 0

    for note in pending:
        volanta, titular, cuerpo = _parse_nota(note["docx"])
        if not titular:
            titular = note.get("titular") or note["title"]
        primer = cuerpo[0] if cuerpo else ""
        try:
            slide = compose_note_slide(note["image"], volanta, titular, site_url=site)
            slides.append(slide)
        except Exception as e:
            logger.error(f"No se pudo componer el slide de «{titular[:40]}»: {e}")
            continue
        bajadas.append((titular, primer))

        # Publicar la nota en Wix (la web es el destino del "seguí leyendo")
        if not dry_run:
            try:
                img = _prepare_image(note["image"])
                wix_title = f"{volanta} — {titular}" if volanta else titular
                body = titular + ("\n\n" + "\n\n".join(cuerpo) if cuerpo else "")
                descripcion = (primer or titular)[:155]
                wix.publish(wix_title, body, img, page=note["page"], description=descripcion)
                wix_ok += 1
                if img != note["image"] and img.exists():
                    img.unlink()
                logger.info(f"[wix] OK — «{titular[:40]}»")
            except Exception as e:
                logger.error(f"[wix] FALLÓ — «{titular[:40]}»: {e}")

    if not slides:
        logger.error("No se pudo componer ningún slide. Se aborta el carrusel.")
        return

    # Caption (bajada): por nota → titular + primer párrafo. + CTA + hashtags.
    bloques = []
    for titular, primer in bajadas:
        p = (primer or "").strip()
        if len(p) > 120:
            p = p[:120].rsplit(" ", 1)[0].rstrip(" .,;:") + "…"
        bloques.append(f"📌 {titular}" + (f"\n{p}" if p else ""))
    cuerpo_cap = "\n\n".join(bloques)

    tags = []
    for n in pending:
        for t in _hashtags(n).split():
            if t not in tags:
                tags.append(t)
    hashtags = " ".join(tags[:10])
    caption = (
        f"📰 Noticias de hoy — {_fecha_larga(hoy).capitalize()}\n\n"
        f"{cuerpo_cap}\n\n"
        f"📲 Seguí leyendo cada nota completa en nuestra web 👉 {site}\n\n"
        f"{hashtags}"
    )

    story_img = compose_noticias_hoy_story(_fecha_larga(hoy).capitalize(), site,
                                           photos=[n["image"] for n in pending[:10]])

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

"""Carrusel de NOTAS (10:00).

Un solo posteo en Facebook + Instagram con TODAS las notas del día (un slide por
nota: foto + titular + resumen breve), ordenadas por el número con que empieza el
nombre del archivo. Además:
  - publica cada nota en Wix/web (destino del "seguí leyendo"),
  - comparte cada nota en X (Twitter) como tweet individual con link a la nota.
Reemplaza el feed por nota (07:00/13:00) y las historias por nota (07:15).
"""
import json
import re
import time
from datetime import date
from pathlib import Path

from file_scanner import _normalize, _page_number, _pair_in_folder, find_todays_edition
from platforms import facebook, instagram, wix
from publisher import _hashtags, _load_ledger, _prepare_image, _resumen, _save_ledger
from story_image import compose_note_slide
from utils.config import get
from utils.logger import get_logger

logger = get_logger("carrusel_notas")

# Ledger propio de la carga a la WEB (Wix), independiente de .publicado.json.
# La corrida de las 7:00 (--notes-web) sube las notas a Wix y las marca acá; el
# carrusel de las 10:00 NO vuelve a subir a Wix las que ya están marcadas (evita
# duplicar en la web), pero igual arma el carrusel/historia en FB/IG.
WEB_LEDGER_NAME = ".web.json"


def _load_web_ledger(posts_folder: Path) -> set[str]:
    path = posts_folder / WEB_LEDGER_NAME
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("No se pudo leer el registro de la web; se asume vacío.")
        return set()


def _save_web_ledger(posts_folder: Path, keys: set[str]) -> None:
    (posts_folder / WEB_LEDGER_NAME).write_text(
        json.dumps(sorted(keys), ensure_ascii=False, indent=2), encoding="utf-8")


# Ledger propio de X (Twitter): cada nota se tuitea UNA sola vez (independiente de
# Wix/FB/IG). Se persiste en state/ por el workflow para no duplicar entre corridas.
X_LEDGER_NAME = ".x.json"


def _load_x_ledger(posts_folder: Path) -> set[str]:
    path = posts_folder / X_LEDGER_NAME
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("No se pudo leer el registro de X; se asume vacío.")
        return set()


def _save_x_ledger(posts_folder: Path, keys: set[str]) -> None:
    (posts_folder / X_LEDGER_NAME).write_text(
        json.dumps(sorted(keys), ensure_ascii=False, indent=2), encoding="utf-8")


def _x_activo() -> bool:
    """X solo se activa si X_ACTIVO está explícitamente en 1/true. Apagado por
    defecto porque X pide un plan con créditos para postear (la API devuelve
    402 Payment Required sin él); prenderlo recién cuando el plan lo permita."""
    return str(get("X_ACTIVO") or "").strip().lower() in ("1", "true", "si", "sí", "on")


def _x_delay() -> int:
    try:
        return max(0, int(get("X_DELAY_SECONDS") or 5))
    except ValueError:
        return 5


def _postear_nota_x(note: dict, volanta: str, titular: str, primer: str,
                    wix_url: str, x_ledger: set[str], posts_folder: Path,
                    dry_run: bool) -> bool:
    """Tuitea UNA nota (título + resumen + foto + link). Devuelve True si tuiteó."""
    if not _x_activo() or note["key"] in x_ledger:
        return False
    from platforms import twitter
    titulo = f"{volanta} — {titular}" if volanta else titular
    resumen = _resumen_caption(primer, max_chars=180)
    img = _prepare_image(note["image"])
    try:
        twitter.publish(titulo, resumen, img, wix_url=wix_url, dry_run=dry_run)
        if not dry_run:
            x_ledger.add(note["key"])
            _save_x_ledger(posts_folder, x_ledger)
        logger.info(f"[x] {'(dry-run) ' if dry_run else ''}OK — «{titular[:40]}»")
        return True
    except Exception as e:
        logger.error(f"[x] FALLÓ — «{titular[:40]}»: {e}")
        return False
    finally:
        if img != note["image"] and img.exists():
            img.unlink()


def _resumen_caption(texto: str, max_chars: int = 280) -> str:
    """Resumen para el caption: ~5 líneas como máximo, cortado en final de oración
    o de palabra, SIN puntos suspensivos."""
    t = (texto or "").strip()
    if not t or len(t) <= max_chars:
        return t
    corte = t[:max_chars]
    for sep in (". ", "! ", "? "):
        idx = corte.rfind(sep)
        if idx >= max_chars * 0.5:
            return corte[:idx + 1].strip()
    return corte.rsplit(" ", 1)[0].rstrip(" ,;:").strip()


def _publicar_nota_wix(note: dict, volanta: str, titular: str, cuerpo: list, primer: str) -> str:
    """Sube UNA nota a Wix (portada + cuerpo). Si no falla, devuelve la URL de la
    nota (puede ser "" si Wix no la devolvió, pero igual fue exitosa); si falla,
    lanza excepción."""
    img = _prepare_image(note["image"])
    try:
        wix_title = f"{volanta} — {titular}" if volanta else titular
        body = titular + ("\n\n" + "\n\n".join(cuerpo) if cuerpo else "")
        descripcion = (primer or titular)[:155]
        res = wix.publish(wix_title, body, img, page=note["page"], description=descripcion)
        return (res or {}).get("url", "") or ""
    finally:
        if img != note["image"] and img.exists():
            img.unlink()

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

    if not dry_run and hoy.weekday() >= 5:  # 5=sábado, 6=domingo
        logger.info("Fin de semana: el carrusel de notas NO se publica (sáb/dom desactivado).")
        return

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
    web_ledger = _load_web_ledger(posts_folder)
    x_ledger = _load_x_ledger(posts_folder)
    slides: list[Path] = []
    bajadas: list[tuple] = []  # (titular, primer_parrafo) para el caption
    wix_ok = 0
    web_changed = False

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

        # Publicar la nota en Wix SOLO si la corrida de las 7:00 (--notes-web) no la
        # subió ya (evita duplicar en la web). Si se sube ahora, además se tuitea
        # (la corrida de las 7:00 ya la habría tuiteado).
        if note["key"] in web_ledger and not dry_run:
            logger.info(f"[wix] ya cargada a las 7:00 — se saltea «{titular[:40]}»")
        else:
            try:
                wix_url = "" if dry_run else _publicar_nota_wix(note, volanta, titular, cuerpo, primer)
                if not dry_run:
                    wix_ok += 1
                    web_ledger.add(note["key"])
                    web_changed = True
                    logger.info(f"[wix] OK — «{titular[:40]}»")
            except Exception as e:
                logger.error(f"[wix] FALLÓ — «{titular[:40]}»: {e}")
                wix_url = None
            if wix_url is not None and _postear_nota_x(
                    note, volanta, titular, primer, wix_url, x_ledger, posts_folder, dry_run):
                if not dry_run:
                    time.sleep(_x_delay())

    if not dry_run and web_changed:
        _save_web_ledger(posts_folder, web_ledger)

    if not slides:
        logger.error("No se pudo componer ningún slide. Se aborta el carrusel.")
        return

    # Caption (bajada): por nota → titular + primer párrafo. + CTA + hashtags.
    bloques = []
    for titular, primer in bajadas:
        # Descripción de cada nota acotada a ~3 líneas (pedido del usuario).
        p = _resumen_caption(primer, max_chars=130)
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

    if dry_run:
        logger.info(f"[dry-run] carrusel de {len(slides)} slide(s): {[s.name for s in slides]}")
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

    # (La historia "Noticias de hoy" se sacó a pedido del usuario 2026-06-23:
    # el carrusel y la carga a la web siguen; ya no se publica el mosaico.)

    if algun_ok:
        for n in pending:
            ledger.add(n["key"])
        _save_ledger(posts_folder, ledger)
        logger.info(f"{len(pending)} nota(s) registradas como publicadas. Wix OK: {wix_ok}/{len(pending)}.")
    else:
        logger.error("El carrusel de notas NO se pudo publicar — se reintentará la próxima corrida.")

    logger.info("=== Carrusel de notas: fin ===")


def run_notes_web(posts_folder: Path, allowed_pages: set[int], dry_run: bool = False) -> None:
    """Corrida de las 7:00 — SOLO carga las notas del día a la WEB (Wix). NO toca
    FB/IG. Así la nota está temprano en la web (y el reel mide vistas todo el día);
    el carrusel + la historia salen aparte a las 10:00 (--notes-carousel)."""
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Carga de notas a la WEB [{modo}] — {hoy.isoformat()} — carpeta: {posts_folder} ===")

    notes = _find_notes(posts_folder)
    if not notes:
        logger.info("No se encontraron notas para cargar a la web.")
        return

    web_ledger = _load_web_ledger(posts_folder)
    x_ledger = _load_x_ledger(posts_folder)
    pending = [n for n in notes if n["key"] not in web_ledger]
    if not pending:
        logger.info("Todas las notas de hoy ya estaban cargadas a la web. Nada que hacer.")
        return
    pending.sort(key=_orden)
    logger.info(f"{len(pending)} nota(s) para cargar a la web.")

    ok = 0
    for note in pending:
        volanta, titular, cuerpo = _parse_nota(note["docx"])
        if not titular:
            titular = note.get("titular") or note["title"]
        primer = cuerpo[0] if cuerpo else ""
        if dry_run:
            logger.info(f"[dry-run] subiría a la web: «{(volanta + ' — ') if volanta else ''}{titular[:50]}»")
            _postear_nota_x(note, volanta, titular, primer, "", x_ledger, posts_folder, dry_run=True)
            continue
        try:
            wix_url = _publicar_nota_wix(note, volanta, titular, cuerpo, primer)
            web_ledger.add(note["key"])
            _save_web_ledger(posts_folder, web_ledger)  # guarda tras cada una (resiliente)
            ok += 1
            logger.info(f"[wix] OK — «{titular[:40]}»")
        except Exception as e:
            logger.error(f"[wix] FALLÓ — «{titular[:40]}»: {e}")
            continue
        # Compartir la nota en X (Twitter) con link a la nota recién publicada.
        if _postear_nota_x(note, volanta, titular, primer, wix_url, x_ledger, posts_folder, dry_run):
            time.sleep(_x_delay())

    logger.info(f"=== Carga a la web: fin — {ok}/{len(pending)} subida(s) ===")

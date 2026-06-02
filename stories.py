"""Orquestadores de Historias (stories) para Instagram + Facebook.

- run_news_stories(...)        → una historia por nota del diario de hoy.
- run_youtube_live_story(...)  → historia del vivo de Radio del Centro (10:35).
- run_youtube_notes_stories(...) → historias de las notas subidas hoy (13:30),
                                   excluyendo el programa completo (La Mañana del Centro).

Las imágenes 9:16 se arman con story_image.py (texto quemado, sin links/stickers).
Cada orquestador lleva su propio ledger para no repetir.
"""
import json
from datetime import date
from pathlib import Path

import youtube
from file_scanner import find_notes
from platforms import facebook, instagram
from publisher import _resumen
from story_image import (compose_canal_story, compose_note_story,
                         compose_youtube_resumen_story, compose_youtube_story)
from utils.config import get
from utils.logger import get_logger

logger = get_logger("stories")

NEWS_LEDGER_NAME = ".historias.json"


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _publish(image_path: Path, dry_run: bool) -> dict:
    """Publica una historia ya compuesta en las redes configuradas. try/except por red."""
    if dry_run:
        logger.info(f"   [dry-run] imagen lista (NO se publica): {image_path.name}")
        return {"dry_run": True}

    results = {}
    fns = {"instagram": lambda: instagram.publish_story(image_path),
           "facebook":  lambda: facebook.publish_story(image_path)}
    for name in _platforms():
        fn = fns.get(name)
        if not fn:
            continue
        try:
            results[name] = fn()
            logger.info(f"   [{name}] historia publicada OK")
        except Exception as e:
            results[name] = {"success": False, "error": str(e)}
            logger.error(f"   [{name}] FALLÓ: {e}")
    return results


def _ok(results: dict) -> bool:
    return results.get("dry_run") or any(
        isinstance(r, dict) and r.get("success") for r in results.values()
    )


# --- Ledger simple basado en lista JSON (para noticias) -----------------
def _load_set(path: Path) -> set[str]:
    try:
        if path.exists():
            return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.warning(f"No se pudo leer {path.name}; se asume vacío.")
    return set()


def _save_set(path: Path, keys: set[str]) -> None:
    path.write_text(json.dumps(sorted(keys), ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) Historias de NOTICIAS
# ---------------------------------------------------------------------------
def run_news_stories(posts_folder: Path, allowed_pages: set[int], dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Historias de noticias [{modo}] ===")
    site_url = get("STORY_SITE_URL") or "www.diariolacampana.com.ar"

    notes = find_notes(posts_folder, allowed_pages)
    if not notes:
        logger.info("No hay notas para historias hoy.")
        return

    ledger_path = posts_folder / NEWS_LEDGER_NAME
    ledger = _load_set(ledger_path)
    pendientes = [n for n in notes if n["key"] not in ledger]
    if len(notes) - len(pendientes):
        logger.info(f"{len(notes) - len(pendientes)} nota(s) ya tenían historia (se omiten).")

    for note in pendientes:
        titular = note.get("titular") or note.get("title", "")
        logger.info(f"--- Historia [pág {note.get('page')}]: «{titular[:55]}» ---")
        resumen = _resumen(note.get("cuerpo", ""), note.get("body", ""), limit=240)
        try:
            img = compose_note_story(note["image"], note.get("volanta", ""),
                                     titular, resumen, site_url)
        except Exception as e:
            logger.error(f"   No se pudo componer la historia: {e} — omitida")
            continue

        results = _publish(img, dry_run)
        if not dry_run and _ok(results):
            ledger.add(note["key"])
            _save_set(ledger_path, ledger)

    logger.info("=== Historias de noticias: fin ===")


# ---------------------------------------------------------------------------
# 2) Historia del VIVO de YouTube
# ---------------------------------------------------------------------------
def run_youtube_live_story(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Historia del vivo de YouTube [{modo}] ===")
    handle = get("YT_HANDLE") or "RadiodelCentro"

    vivo = youtube.vivo_actual(handle)
    if not vivo:
        logger.info("El canal no está en vivo ahora. Nada que publicar.")
        return

    ledger = youtube.leer_ledger()
    if vivo["id"] in ledger:
        logger.info("El vivo de hoy ya se publicó como historia. Se omite.")
        return

    logger.info(f"EN VIVO: «{vivo['titulo'][:55]}» ({vivo['url']})")
    try:
        thumb = youtube.descargar_miniatura(vivo["id"])
        img = compose_youtube_story(
            thumb, vivo["titulo"], "Mirá el vivo",
            footer="Miranos que estamos en vivo por nuestro canal de YouTube",
            en_vivo=True)
    except Exception as e:
        logger.error(f"No se pudo componer la historia del vivo: {e}")
        return

    results = _publish(img, dry_run)
    if not dry_run and _ok(results):
        ledger.add(vivo["id"])
        youtube.guardar_ledger(ledger)
    logger.info("=== Historia del vivo: fin ===")


# ---------------------------------------------------------------------------
# 3) Historia RESUMEN de las NOTAS de YouTube (UNA sola con todas las del día)
# ---------------------------------------------------------------------------
def run_youtube_notes_stories(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Historia resumen de notas de YouTube [{modo}] ===")
    channel_id = get("YT_CHANNEL_ID") or "UCqiTJ2oRBLNO1ZzfrdiyjTw"
    excluir_raw = get("STORY_EXCLUDE_TITLE") or "MAÑANA DEL CENTRO"
    excluir = [youtube.normalizar(x) for x in excluir_raw.split(",") if x.strip()]

    videos = youtube.videos_de_hoy(channel_id)
    if not videos:
        logger.info("No hay videos de hoy en el canal.")
        return

    # Filtra el programa completo (La Mañana del Centro) — solo quedan las notas.
    notas = []
    for v in videos:
        tnorm = youtube.normalizar(v["titulo"])
        if any(x and x in tnorm for x in excluir):
            logger.info(f"Excluido (programa completo): «{v['titulo'][:55]}»")
            continue
        notas.append(v)

    if not notas:
        logger.info("No hay notas de YouTube para hoy (sin contar el programa completo).")
        return

    # Anti-repetición: una sola historia resumen por día (clave por fecha).
    ledger = youtube.leer_ledger()
    clave_dia = "resumen-" + date.today().isoformat()
    if not dry_run and clave_dia in ledger:
        logger.info("El resumen de notas de YouTube de hoy ya se publicó. Se omite.")
        return

    logger.info(f"{len(notas)} nota(s) para el resumen de hoy:")
    for v in notas:
        logger.info(f"   • {v['titulo'][:60]}")

    # Baja las miniaturas de cada nota.
    vids_img = []
    for v in notas:
        try:
            thumb = youtube.descargar_miniatura(v["id"])
            vids_img.append({"thumb": thumb, "titulo": v["titulo"]})
        except Exception as e:
            logger.warning(f"   miniatura no disponible para «{v['titulo'][:40]}»: {e}")
    if not vids_img:
        logger.error("No se pudo bajar ninguna miniatura — se omite.")
        return

    # UNA sola historia con todas las notas + CTA al canal de YouTube.
    try:
        img = compose_youtube_resumen_story(vids_img)
    except Exception as e:
        logger.error(f"No se pudo componer la historia resumen: {e}")
        return

    results = _publish(img, dry_run)
    if not dry_run and _ok(results):
        ledger.add(clave_dia)
        youtube.guardar_ledger(ledger)
        logger.info("Resumen de YouTube registrado (no se repite hoy).")
    if dry_run:
        logger.info("(dry-run) no se modificó el ledger.")
    logger.info("=== Historia resumen de notas: fin ===")


# ---------------------------------------------------------------------------
# 4) Historia PROMO del CANAL de WhatsApp (QR escaneable). Se publica a diario.
# ---------------------------------------------------------------------------
CANAL_LEDGER = Path(__file__).parent / ".canal_story.json"
CANAL_URL_DEFAULT = "https://whatsapp.com/channel/0029Vb81uxu4yltJLzTE611x"


def _canal_ya_hoy() -> bool:
    from datetime import date
    try:
        if CANAL_LEDGER.exists():
            return json.loads(CANAL_LEDGER.read_text(encoding="utf-8")).get("fecha") == date.today().isoformat()
    except Exception:
        pass
    return False


def _canal_marcar() -> None:
    CANAL_LEDGER.write_text(json.dumps({"fecha": date.today().isoformat()},
                                       ensure_ascii=False), encoding="utf-8")


def run_canal_story(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Historia promo del Canal de WhatsApp [{modo}] ===")
    url = get("CANAL_WSP_URL") or CANAL_URL_DEFAULT

    if not dry_run and _canal_ya_hoy():
        logger.info("La historia del canal ya se publicó hoy. Se omite.")
        return

    try:
        img = compose_canal_story(url)
    except Exception as e:
        logger.error(f"No se pudo componer la historia del canal: {e}")
        return

    results = _publish(img, dry_run)
    if not dry_run and _ok(results):
        _canal_marcar()
        logger.info("Historia del canal registrada (no se repite hoy).")
    logger.info("=== Historia promo del canal: fin ===")

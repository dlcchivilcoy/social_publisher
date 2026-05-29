import json
from pathlib import Path

from PIL import Image

from file_scanner import find_notes
from platforms import facebook, instagram, twitter, wix
from utils.logger import get_logger

logger = get_logger("publisher")

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_DIM = 1920
LEDGER_NAME = ".publicado.json"


def _load_ledger(posts_folder: Path) -> set[str]:
    path = posts_folder / LEDGER_NAME
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("No se pudo leer el registro de publicadas; se asume vacío.")
        return set()


def _save_ledger(posts_folder: Path, keys: set[str]) -> None:
    path = posts_folder / LEDGER_NAME
    path.write_text(json.dumps(sorted(keys), ensure_ascii=False, indent=2), encoding="utf-8")


def _prepare_image(image_path: Path) -> Path:
    """Valida la imagen y la redimensiona si pesa más de 5MB."""
    try:
        img = Image.open(image_path)
        img.verify()
    except Exception as e:
        raise ValueError(f"Imagen corrupta o ilegible: {image_path.name} — {e}")

    if image_path.stat().st_size > MAX_IMAGE_BYTES:
        img = Image.open(image_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)
        resized = image_path.with_name(image_path.stem + "_resized.jpg")
        img.save(resized, quality=85, optimize=True)
        logger.info(f"Imagen redimensionada: {image_path.name} → {resized.name}")
        return resized

    return image_path


def _parse_allowed_pages(raw: str) -> set[int]:
    pages = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            pages.add(int(part))
    return pages


def run_publish_cycle(posts_folder: Path, allowed_pages: set[int], dry_run: bool = False) -> None:
    mode = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Iniciando ciclo [{mode}] en: {posts_folder} ===")
    logger.info(f"Páginas permitidas: {sorted(allowed_pages)}")

    notes = find_notes(posts_folder, allowed_pages)
    if not notes:
        logger.info("No se encontraron notas para publicar.")
        return

    ledger = _load_ledger(posts_folder)
    pending = [n for n in notes if n["key"] not in ledger]
    already = len(notes) - len(pending)
    if already:
        logger.info(f"{already} nota(s) ya estaban publicadas (se omiten).")

    if dry_run:
        logger.info("--- Emparejados detectados (NO se publica nada) ---")
        for n in notes:
            estado = "YA PUBLICADA" if n["key"] in ledger else "pendiente"
            logger.info(
                f"[pág {n['page']}] «{n['title'][:60]}»  "
                f"foto: {n['image'].name}  (similitud {n['score']}, {estado})"
            )
        logger.info(f"Total: {len(notes)} nota(s), {len(pending)} pendiente(s).")
        return

    for note in pending:
        title = note["title"]
        body = note["body"]
        logger.info(f"--- Publicando [pág {note['page']}]: «{title[:60]}» ---")

        try:
            image_path = _prepare_image(note["image"])
        except Exception as e:
            logger.error(f"Error preparando imagen de «{title[:40]}»: {e} — omitida")
            continue

        page_num = note["page"]
        platform_calls = [
            ("wix",       lambda: wix.publish(title, body, image_path, page=page_num)),
            ("facebook",  lambda: facebook.publish(body, image_path)),
            ("instagram", lambda: instagram.publish(body, image_path)),
            ("twitter",   lambda: twitter.publish(body, image_path)),
        ]

        results = {}
        for name, fn in platform_calls:
            try:
                results[name] = fn()
                logger.info(f"[{name}] OK — «{title[:40]}»")
            except Exception as e:
                results[name] = {"success": False, "error": str(e)}
                logger.error(f"[{name}] FALLÓ — «{title[:40]}»: {e}")

        # Limpia la imagen redimensionada temporal.
        if image_path != note["image"] and image_path.exists():
            image_path.unlink()

        if any(r.get("success") for r in results.values()):
            ledger.add(note["key"])
            _save_ledger(posts_folder, ledger)
            logger.info(f"«{title[:40]}» registrada como publicada.")
        else:
            logger.error(f"TODAS las plataformas fallaron para «{title[:40]}» — se reintentará la próxima vez")

    logger.info("=== Ciclo finalizado ===")

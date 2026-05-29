import shutil
from pathlib import Path

from PIL import Image

from file_scanner import find_pairs, parse_text_file
from platforms import facebook, instagram, twitter, wix
from utils.logger import get_logger

logger = get_logger("publisher")

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_DIM = 1920


def _prepare_image(image_path: Path) -> Path:
    """Resize image if over 5MB and validate it's readable. Returns path to use."""
    try:
        img = Image.open(image_path)
        img.verify()
    except Exception as e:
        raise ValueError(f"Imagen corrupta o ilegible: {image_path.name} — {e}")

    if image_path.stat().st_size > MAX_IMAGE_BYTES:
        img = Image.open(image_path)
        img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)
        resized_path = image_path.with_stem(image_path.stem + "_resized")
        img.save(resized_path, quality=85, optimize=True)
        logger.info(f"Imagen redimensionada: {image_path.name} → {resized_path.name}")
        return resized_path

    return image_path


def run_publish_cycle(posts_folder: Path) -> None:
    logger.info(f"=== Iniciando ciclo de publicación en: {posts_folder} ===")

    pairs = find_pairs(posts_folder)
    if not pairs:
        logger.info("No hay pares sin publicar.")
        return

    published_folder = posts_folder / "published"
    published_folder.mkdir(exist_ok=True)

    for pair in pairs:
        stem = pair["stem"]
        logger.info(f"--- Procesando: {stem} ---")

        try:
            title, body = parse_text_file(pair["text"])
            image_path = _prepare_image(pair["image"])
        except Exception as e:
            logger.error(f"Error preparando '{stem}': {e} — omitido")
            continue

        platform_calls = [
            ("wix",       lambda: wix.publish(title, body, image_path)),
            ("facebook",  lambda: facebook.publish(body, image_path)),
            ("instagram", lambda: instagram.publish(body, image_path)),
            ("twitter",   lambda: twitter.publish(body, image_path)),
        ]

        results = {}
        for name, fn in platform_calls:
            try:
                results[name] = fn()
                logger.info(f"[{name}] OK — {stem}")
            except Exception as e:
                results[name] = {"success": False, "error": str(e)}
                logger.error(f"[{name}] FALLÓ — {stem}: {e}")

        any_success = any(r.get("success") for r in results.values())

        if any_success:
            for src in [pair["image"], pair["text"]]:
                shutil.move(str(src), published_folder / src.name)
            # Clean up resized temp file if created
            if image_path != pair["image"] and image_path.exists():
                image_path.unlink()
            logger.info(f"'{stem}' movido a published/")
        else:
            logger.error(f"TODAS las plataformas fallaron para '{stem}' — archivos sin mover, se reintentará mañana")

    logger.info("=== Ciclo finalizado ===")

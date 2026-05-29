from pathlib import Path

from utils.logger import get_logger

logger = get_logger("file_scanner")

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def find_pairs(folder: Path) -> list[dict]:
    """
    Returns a list of dicts with keys: image (Path), text (Path), stem (str).
    Pairs are matched by base filename (case-insensitive).
    The 'published' subfolder is always excluded.
    """
    if not folder.exists():
        logger.error(f"Posts folder not found: {folder}")
        return []

    images = {
        p.stem.lower(): p
        for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in IMAGE_EXTS
        and not p.name.startswith(".")
    }
    texts = {
        p.stem.lower(): p
        for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".txt"
        and not p.name.startswith(".")
    }

    pairs = []
    for stem, img_path in images.items():
        if stem in texts:
            pairs.append({"image": img_path, "text": texts[stem], "stem": stem})
        else:
            logger.warning(f"Imagen sin .txt correspondiente: {img_path.name}")

    for stem in texts:
        if stem not in images:
            logger.warning(f"Texto sin imagen correspondiente: {texts[stem].name}")

    logger.info(f"{len(pairs)} par(es) encontrado(s) en {folder}")
    return pairs


def parse_text_file(text_path: Path) -> tuple[str, str]:
    """
    Returns (title, body).
    If first line starts with 'TITLE:', that line becomes the title.
    Otherwise the file stem becomes the title.
    """
    raw = text_path.read_text(encoding="utf-8").strip()
    lines = raw.splitlines()

    if lines and lines[0].upper().startswith("TITLE:"):
        title = lines[0][len("TITLE:"):].strip()
        body = "\n".join(lines[1:]).strip()
    else:
        title = text_path.stem.replace("_", " ").replace("-", " ").title()
        body = raw

    return title, body

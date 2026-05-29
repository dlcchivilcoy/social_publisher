import re
from difflib import SequenceMatcher
from pathlib import Path

from docx import Document

from utils.logger import get_logger

logger = get_logger("file_scanner")

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
DOC_EXTS = {".docx"}
# Umbral mínimo de similitud para aceptar un emparejado nota↔foto.
MATCH_THRESHOLD = 0.40


def _normalize(name: str) -> str:
    """Pasa a minúsculas, saca acentos básicos y deja solo letras/números/espacios."""
    name = name.lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        name = name.replace(a, b)
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _similarity(doc_name: str, img_name: str) -> float:
    """Puntaje 0..1 de cuán parecidos son los nombres (nota vs foto)."""
    a, b = _normalize(doc_name), _normalize(img_name)
    if not a or not b:
        return 0.0

    score = SequenceMatcher(None, a, b).ratio()

    # Bonus por tokens que se contienen entre sí (ej: "salinardi" dentro de
    # "florencia salinardi", o "camion" dentro de "camion1").
    tokens_a = [t for t in a.split() if len(t) >= 3]
    tokens_b = [t for t in b.split() if len(t) >= 3]
    for ta in tokens_a:
        for tb in tokens_b:
            if ta in tb or tb in ta:
                ratio = min(len(ta), len(tb)) / max(len(ta), len(tb))
                score = max(score, ratio)
    return score


def _page_number(folder_name: str) -> int | None:
    m = re.search(r"(\d+)", folder_name)
    return int(m.group(1)) if m else None


def _read_docx(path: Path) -> tuple[str, str]:
    """
    Devuelve (titulo, cuerpo) según la estructura periodística del documento:
      línea 0 = volanta/categoría, línea 1 = titular, línea 2+ = cuerpo.
    Título  = "volanta — titular".
    Cuerpo  = titular + nota completa (para el texto de redes y blog).
    """
    doc = Document(str(path))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    if not paras:
        return path.stem, ""

    volanta = paras[0]
    titular = paras[1] if len(paras) > 1 else ""
    cuerpo = "\n".join(paras[2:]).strip() if len(paras) > 2 else ""

    if titular:
        title = f"{volanta} — {titular}"
        body = titular + ("\n\n" + cuerpo if cuerpo else "")
    else:
        title = volanta
        body = volanta
    return title, body


def _pair_in_folder(page_folder: Path) -> list[dict]:
    """Empareja cada .docx con su foto más parecida dentro de una página."""
    docs = [p for p in page_folder.iterdir()
            if p.is_file() and p.suffix.lower() in DOC_EXTS and not p.name.startswith("~")]
    images = [p for p in page_folder.iterdir()
              if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")]

    notes = []
    available = list(images)

    # Asigna de forma "greedy": calcula todos los puntajes y toma los mejores primero.
    scored = []
    for doc in docs:
        for img in images:
            scored.append((_similarity(doc.stem, img.stem), doc, img))
    scored.sort(key=lambda x: x[0], reverse=True)

    used_docs = set()
    used_imgs = set()
    for score, doc, img in scored:
        if doc in used_docs or img in used_imgs:
            continue
        if score < MATCH_THRESHOLD:
            continue
        title, body = _read_docx(doc)
        notes.append({
            "docx": doc,
            "image": img,
            "title": title,
            "body": body,
            "score": round(score, 2),
        })
        used_docs.add(doc)
        used_imgs.add(img)

    for doc in docs:
        if doc not in used_docs:
            logger.warning(f"Nota sin foto que coincida: {doc.name} (en {page_folder.name})")

    return notes


def find_notes(posts_folder: Path, allowed_pages: set[int]) -> list[dict]:
    """
    Recorre POSTS_FOLDER buscando carpetas de página (ej. 'la pagina 2') en
    cualquier nivel. Para las páginas permitidas, empareja notas (.docx) con
    sus fotos. Devuelve una lista de notas con: docx, image, title, body,
    page, edition, key (identificador único para el registro de publicadas).
    """
    if not posts_folder.exists():
        logger.error(f"Carpeta de posts no encontrada: {posts_folder}")
        return []

    notes = []
    # Busca cualquier carpeta cuyo nombre contenga "pagina" y un número.
    for page_folder in sorted(posts_folder.rglob("*")):
        if not page_folder.is_dir():
            continue
        if "pagina" not in _normalize(page_folder.name):
            continue

        page_num = _page_number(page_folder.name)
        if page_num is None:
            continue
        if page_num not in allowed_pages:
            logger.info(f"Página {page_num} ignorada (no está en la lista): {page_folder.name}")
            continue

        edition = page_folder.parent.name
        for note in _pair_in_folder(page_folder):
            note["page"] = page_num
            note["edition"] = edition
            note["key"] = f"{edition}|p{page_num}|{note['docx'].name}"
            notes.append(note)

    logger.info(f"{len(notes)} nota(s) encontrada(s) en páginas permitidas")
    return notes

import re
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from docx import Document

from utils.logger import get_logger

logger = get_logger("file_scanner")

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
DOC_EXTS = {".docx"}
MATCH_THRESHOLD = 0.40

# Meses en español → número
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _normalize(name: str) -> str:
    name = name.lower()
    for a, b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")):
        name = name.replace(a, b)
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _folder_date(folder_name: str) -> date | None:
    """
    Intenta extraer una fecha de un nombre de carpeta como:
      'diario para 29 de mayo'
      'diario para el 29 de mayo'
      '29 mayo'
      '29-05-2026'
    Devuelve un objeto date o None si no puede.
    """
    norm = _normalize(folder_name)

    # Patrón: número + mes en texto (ej. "29 de mayo", "29 mayo")
    for mes_nombre, mes_num in MESES.items():
        m = re.search(rf"(\d{{1,2}})\s*(?:de\s*)?{mes_nombre}", norm)
        if m:
            day = int(m.group(1))
            year = date.today().year
            try:
                return date(year, mes_num, day)
            except ValueError:
                pass

    # Patrón numérico: DD-MM-YYYY o DD/MM/YYYY
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", norm)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    return None


def find_todays_edition(posts_folder: Path) -> Path | None:
    """
    Busca dentro de POSTS_FOLDER una carpeta cuyo nombre corresponda
    a la fecha de HOY. Devuelve su Path o None si no existe.
    """
    today = date.today()
    if not posts_folder.exists():
        logger.error(f"Carpeta de posts no encontrada: {posts_folder}")
        return None

    candidates = []
    for item in posts_folder.iterdir():
        if not item.is_dir():
            continue
        d = _folder_date(item.name)
        if d == today:
            candidates.append(item)
        # También busca un nivel adentro (carpeta dentro de carpeta)
        else:
            for sub in item.iterdir():
                if sub.is_dir():
                    d2 = _folder_date(sub.name)
                    if d2 == today:
                        candidates.append(sub)

    if not candidates:
        logger.info(f"No hay carpeta de edición para hoy ({today.strftime('%d/%m/%Y')}) en {posts_folder}")
        return None

    # Si hay más de una, usar la más reciente por fecha de modificación
    edition = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    logger.info(f"Edición de hoy encontrada: {edition.name}")
    return edition


def _similarity(doc_name: str, img_name: str) -> float:
    a, b = _normalize(doc_name), _normalize(img_name)
    if not a or not b:
        return 0.0
    score = SequenceMatcher(None, a, b).ratio()
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
    Estructura periodística:
      línea 0 = volanta/categoría
      línea 1 = titular
      línea 2+ = cuerpo
    Título  = "volanta — titular"
    Cuerpo  = titular + nota completa
    """
    doc = Document(str(path))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    if not paras:
        return path.stem, ""
    volanta = paras[0]
    titular = paras[1] if len(paras) > 1 else ""
    cuerpo  = "\n".join(paras[2:]).strip() if len(paras) > 2 else ""
    if titular:
        title = f"{volanta} — {titular}"
        body  = titular + ("\n\n" + cuerpo if cuerpo else "")
    else:
        title = volanta
        body  = volanta
    return title, body


def _pair_in_folder(page_folder: Path) -> list[dict]:
    docs   = [p for p in page_folder.iterdir()
              if p.is_file() and p.suffix.lower() in DOC_EXTS and not p.name.startswith("~")]
    images = [p for p in page_folder.iterdir()
              if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")]

    scored = []
    for doc in docs:
        for img in images:
            scored.append((_similarity(doc.stem, img.stem), doc, img))
    scored.sort(key=lambda x: x[0], reverse=True)

    used_docs, used_imgs = set(), set()
    notes = []
    for score, doc, img in scored:
        if doc in used_docs or img in used_imgs:
            continue
        if score < MATCH_THRESHOLD:
            continue
        title, body = _read_docx(doc)
        notes.append({"docx": doc, "image": img, "title": title, "body": body, "score": round(score, 2)})
        used_docs.add(doc)
        used_imgs.add(img)

    for doc in docs:
        if doc not in used_docs:
            logger.warning(f"Nota sin foto que coincida: {doc.name} (en {page_folder.name})")
    return notes


def find_notes(posts_folder: Path, allowed_pages: set[int]) -> list[dict]:
    """
    1. Detecta la carpeta de edición de HOY dentro de posts_folder.
    2. Si no existe, no publica nada.
    3. Si existe, busca subcarpetas 'la pagina X' para las páginas permitidas.
    4. Empareja .docx con fotos por nombre parecido.
    5. El ledger en publisher.py se encarga de no repetir notas ya publicadas.
    """
    edition_folder = find_todays_edition(posts_folder)
    if edition_folder is None:
        return []

    notes = []
    for page_folder in sorted(edition_folder.rglob("*")):
        if not page_folder.is_dir():
            continue
        if "pagina" not in _normalize(page_folder.name):
            continue
        page_num = _page_number(page_folder.name)
        if page_num is None:
            continue
        if page_num not in allowed_pages:
            logger.info(f"Página {page_num} ignorada: {page_folder.name}")
            continue
        for note in _pair_in_folder(page_folder):
            note["page"]    = page_num
            note["edition"] = edition_folder.name
            note["key"]     = f"{edition_folder.name}|p{page_num}|{note['docx'].name}"
            notes.append(note)

    logger.info(f"{len(notes)} nota(s) encontrada(s) en páginas permitidas para hoy")
    return notes

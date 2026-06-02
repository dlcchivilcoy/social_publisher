import json
from pathlib import Path

from PIL import Image

from file_scanner import find_notes
from platforms import facebook, instagram, twitter, wix
from utils.config import get
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


SUMMARY_MAX = 300  # caracteres del resumen breve


def _resumen(cuerpo: str, body: str, limit: int = SUMMARY_MAX) -> str:
    """Toma el primer párrafo del cuerpo (o del body) y lo recorta prolijo."""
    texto = (cuerpo or body or "").strip()
    if not texto:
        return ""
    # primer párrafo
    parrafo = texto.split("\n")[0].strip()
    if len(parrafo) <= limit:
        return parrafo
    # recorte en el último espacio antes del límite
    corte = parrafo[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return corte + "…"


DEPORTES_PAGES = {8, 9}

# Emoji de categoría según la página
EMOJI_DEPORTES = "⚽"
EMOJI_LOCALES = "📣"


def _emoji_categoria(note: dict) -> str:
    return EMOJI_DEPORTES if note.get("page") in DEPORTES_PAGES else EMOJI_LOCALES


def _hashtags(note: dict) -> str:
    """Hashtags reales y funcionales: locales + de categoría + tema detectado."""
    tags = ["#Chivilcoy", "#DiarioLaCampaña"]
    texto = f"{note.get('volanta','')} {note.get('titular','')}".lower()

    if note.get("page") in DEPORTES_PAGES:
        tags.append("#Deportes")
        if "fútbol" in texto or "futbol" in texto:
            tags.append("#Fútbol")
        if "básquet" in texto or "basquet" in texto:
            tags.append("#Básquet")
    else:
        tags += ["#Noticias", "#Actualidad"]
        if any(p in texto for p in ("polic", "robo", "hurto", "delito", "delict", "choque", "accidente")):
            tags.append("#Policiales")
        if any(p in texto for p in ("concejo", "municipio", "intendente", "gobierno", "rendición", "rendicion")):
            tags.append("#Política")

    # quita duplicados conservando el orden
    vistos, limpio = set(), []
    for t in tags:
        if t not in vistos:
            vistos.add(t)
            limpio.append(t)
    return " ".join(limpio)


def _site_url() -> str:
    return get("STORY_SITE_URL") or "www.diariolacampaña.com.ar"


def _social_caption(note: dict, wix_url: str, *, usar_link_wix: bool = True) -> str:
    """
    Arma el texto para redes: emoji + volanta + titular + resumen + cierre + hashtags.

    usar_link_wix=True  → Facebook: pone el link clickeable a la nota de Wix.
    usar_link_wix=False → Instagram: el link no es clickeable, así que invita por texto
                          a entrar a la web (igual que en las historias).
    """
    volanta = (note.get("volanta") or "").strip()
    titular = (note.get("titular") or "").strip()
    resumen = _resumen(note.get("cuerpo", ""), note.get("body", ""))
    emoji = _emoji_categoria(note)

    partes = []
    if volanta and titular:
        partes.append(f"{emoji} {volanta}\n📰 {titular}")
    elif titular:
        partes.append(f"{emoji} {titular}")
    elif volanta:
        partes.append(f"{emoji} {volanta}")
    if resumen:
        partes.append(f"📝 {resumen}")
    if usar_link_wix:
        if wix_url:
            partes.append(f"🔗 Leé la nota completa 👉 {wix_url}")
    else:
        partes.append(f"📲 Seguí leyendo en {_site_url()}")
    partes.append(_hashtags(note))
    return "\n\n".join(partes)


def _parse_allowed_pages(raw: str) -> set[int]:
    pages = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            pages.add(int(part))
    return pages


# Orden de PRESENTACIÓN en el feed, de ARRIBA hacia abajo (las primeras se leen primero).
# Como en el feed lo último que se publica aparece arriba, internamente publicamos
# en orden INVERSO: las páginas de mayor interés (3, 5) se publican AL FINAL para que
# queden arriba, y deportes (8, 9) se publican PRIMERO para que queden abajo.
DEFAULT_FEED_ORDER = [3, 5, 2, 7, 8, 9]


def _feed_order() -> list[int]:
    raw = get("FEED_ORDER")
    if not raw:
        return DEFAULT_FEED_ORDER
    order = [int(p.strip()) for p in raw.split(",") if p.strip().isdigit()]
    return order or DEFAULT_FEED_ORDER


def _ordenar_para_feed(pending: list[dict]) -> list[dict]:
    """
    Reordena las notas para PUBLICAR. La página que aparece antes en FEED_ORDER
    debe quedar más arriba en el feed, por lo que se publica más tarde.
    Resultado: se publica de la página menos prioritaria a la más prioritaria.
    """
    orden = _feed_order()

    def rank(note: dict) -> int:
        page = note.get("page")
        return orden.index(page) if page in orden else len(orden)

    # rank bajo = más arriba en el feed = se publica último → reverse=True.
    # El sort es estable: dentro de una misma página se conserva el orden original.
    return sorted(pending, key=rank, reverse=True)


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

    # Orden de publicación: deportes primero (quedan abajo), página 3/5 al final (quedan arriba).
    pending = _ordenar_para_feed(pending)

    if dry_run:
        logger.info("--- Orden de PUBLICACIÓN (la última publicada queda ARRIBA en el feed) ---")
        for i, n in enumerate(pending, 1):
            logger.info(
                f"  {i}. [pág {n['page']}] «{n['title'][:60]}»  "
                f"foto: {n['image'].name}  (similitud {n['score']})"
            )
        if pending:
            logger.info(
                f"→ Arriba del feed quedará: [pág {pending[-1]['page']}] «{pending[-1]['title'][:50]}»"
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
        results = {}

        # 1) Wix primero — necesitamos su URL para el tweet
        descripcion = _resumen(note.get("cuerpo", ""), note.get("body", ""), limit=155)
        try:
            results["wix"] = wix.publish(title, body, image_path, page=page_num,
                                         description=descripcion)
            logger.info(f"[wix] OK — «{title[:40]}»")
        except Exception as e:
            results["wix"] = {"success": False, "error": str(e)}
            logger.error(f"[wix] FALLÓ — «{title[:40]}»: {e}")

        wix_url = results["wix"].get("url", "") if results["wix"].get("success") else ""

        # Facebook: link clickeable a Wix (ahí sí funciona).
        # Instagram: el link no es clickeable, invita a la web por texto.
        caption_fb = _social_caption(note, wix_url, usar_link_wix=True)
        caption_ig = _social_caption(note, wix_url, usar_link_wix=False)

        # 2) Facebook e Instagram vía API.
        # NOTA: X (Twitter) NO va por API — se publica solo desde Wix
        # (función nativa "Compartir en redes sociales", cuenta gratis de Wix).
        other_calls = [
            ("facebook",  lambda: facebook.publish(caption_fb, image_path)),
            ("instagram", lambda: instagram.publish(caption_ig, image_path)),
        ]
        for name, fn in other_calls:
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

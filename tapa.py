"""Publica la TAPA del diario (la imagen que se deja en la carpeta del PDF)
en el muro (feed) Y como historia de Instagram + Facebook.

- Toma la imagen MÁS NUEVA de TAPA_FOLDER (ignora el PDF y otros archivos).
- Memoria anti-repetición (.tapa.json): solo publica si la tapa es NUEVA
  (si no la cambiaste, no la repite), igual que el envío del PDF por WhatsApp.
- Se programa a las 00:00 (arranque del día).
"""
import json
from datetime import date
from pathlib import Path

from platforms import facebook, instagram
from publisher import _prepare_image
from story_image import compose_tapa_story
from utils.config import get
from utils.logger import get_logger

logger = get_logger("tapa")

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
LEDGER = Path(__file__).parent / ".tapa.json"
DEFAULT_FOLDER = r"C:\Users\Diario\Desktop\DIARIO PDF"

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _resolver_tapa(folder: Path) -> Path | None:
    """Devuelve la imagen más nueva de la carpeta (ignora PDF y otros)."""
    if not folder.exists():
        logger.error(f"Carpeta de la tapa no encontrada: {folder}")
        return None
    imgs = [p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")]
    if not imgs:
        return None
    return sorted(imgs, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _identidad(p: Path) -> str:
    st = p.stat()
    return f"{p.name}|{st.st_size}|{round(st.st_mtime)}"


def _ya_publicada(identidad: str) -> bool:
    try:
        if LEDGER.exists():
            return json.loads(LEDGER.read_text(encoding="utf-8")).get("identidad") == identidad
    except Exception:
        pass
    return False


def _marcar(identidad: str) -> None:
    LEDGER.write_text(json.dumps({"identidad": identidad, "fecha": date.today().isoformat()},
                                 ensure_ascii=False, indent=2), encoding="utf-8")


def run_tapa(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Tapa del diario [{modo}] ===")

    folder = Path(get("TAPA_FOLDER") or DEFAULT_FOLDER)
    tapa = _resolver_tapa(folder)
    if not tapa:
        logger.info(f"No hay imagen de tapa en {folder}. Nada que publicar.")
        return
    logger.info(f"Tapa encontrada: {tapa.name}")

    identidad = _identidad(tapa)
    if _ya_publicada(identidad):
        logger.info(f"La tapa «{tapa.name}» ya se publicó y no cambió. NO se repite. "
                    f"(Reemplazá la imagen por la tapa nueva para que se publique.)")
        return

    fecha = _fecha_larga(date.today())
    caption = f"📰 Tapa de hoy — Diario La Campaña\n{fecha.capitalize()}\nConseguí tu ejemplar 📲"

    plats = _platforms()
    algun_ok = False

    # 1) Muro (feed): foto + leyenda
    try:
        feed_img = _prepare_image(tapa)
    except Exception as e:
        logger.error(f"No se pudo preparar la tapa para el muro: {e}")
        feed_img = tapa
    feed_fns = {"facebook": lambda: facebook.publish(caption, feed_img),
                "instagram": lambda: instagram.publish(caption, feed_img)}
    for name in plats:
        fn = feed_fns.get(name)
        if not fn:
            continue
        if dry_run:
            logger.info(f"   [dry-run] muro {name}: listo (NO se publica)")
            continue
        try:
            fn()
            algun_ok = True
            logger.info(f"   [{name}] tapa publicada en el muro OK")
        except Exception as e:
            logger.error(f"   [{name}] muro FALLÓ: {e}")
    if feed_img != tapa and feed_img.exists():
        feed_img.unlink()

    # 2) Historia 9:16
    try:
        story_img = compose_tapa_story(tapa, fecha.capitalize())
    except Exception as e:
        logger.error(f"No se pudo componer la historia de la tapa: {e}")
        story_img = None
    if story_img:
        story_fns = {"instagram": lambda: instagram.publish_story(story_img),
                     "facebook": lambda: facebook.publish_story(story_img)}
        for name in plats:
            fn = story_fns.get(name)
            if not fn:
                continue
            if dry_run:
                logger.info(f"   [dry-run] historia {name}: imagen lista en historias_preview (NO se publica)")
                continue
            try:
                fn()
                algun_ok = True
                logger.info(f"   [{name}] tapa publicada como historia OK")
            except Exception as e:
                logger.error(f"   [{name}] historia FALLÓ: {e}")

    if not dry_run and algun_ok:
        _marcar(identidad)
        logger.info("Tapa registrada como publicada (no se repetirá hasta que la cambies).")
    elif not dry_run:
        logger.error("La tapa NO se pudo publicar en ninguna red — se reintentará la próxima corrida.")

    logger.info("=== Tapa: fin ===")

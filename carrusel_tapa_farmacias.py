"""Carrusel TAPA + FARMACIAS (08:00).

Un solo posteo en Facebook + Instagram con DOS slides:
  1) la tapa del diario,  2) las farmacias de turno de hoy.
Además publica la HISTORIA de la tapa. Reemplaza los posteos sueltos de tapa
(00:00) y farmacias (08:00) y la historia de farmacias.
"""
import json
from datetime import date
from pathlib import Path

import farmacias as farm
import tapa as tapa_mod
from platforms import facebook, instagram
from story_image import compose_tapa_slide, compose_tapa_story
from utils.config import get
from utils.logger import get_logger

logger = get_logger("carrusel_tf")

LEDGER = Path(__file__).parent / ".carrusel_tf.json"


def _site() -> str:
    return get("STORY_SITE_URL") or "www.diariolacampaña.com.ar"


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _ya_hoy(hoy: date) -> bool:
    try:
        if LEDGER.exists():
            return json.loads(LEDGER.read_text(encoding="utf-8")).get("fecha") == hoy.isoformat()
    except Exception:
        pass
    return False


def _marcar(hoy: date) -> None:
    LEDGER.write_text(json.dumps({"fecha": hoy.isoformat()}, ensure_ascii=False), encoding="utf-8")


def run_tapa_farmacias(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Carrusel Tapa+Farmacias [{modo}] — {hoy.isoformat()} ===")

    if not dry_run and _ya_hoy(hoy):
        logger.info("El carrusel tapa+farmacias de hoy ya se publicó. Se omite.")
        return

    # 1) Tapa (imagen más nueva de TAPA_FOLDER)
    folder = Path(get("TAPA_FOLDER") or tapa_mod.DEFAULT_FOLDER)
    tapa_img = tapa_mod._resolver_tapa(folder)
    if not tapa_img:
        logger.error(f"No hay imagen de tapa en {folder}. No se publica el carrusel.")
        return
    logger.info(f"Tapa: {tapa_img.name}")

    # 2) Farmacias de hoy (imagen + líneas de texto)
    feed_farm, lineas_cap, nombres, es_cambio = farm.farmacias_feed_de_hoy(hoy)
    if not feed_farm:
        logger.error(f"Sin datos de farmacias: {lineas_cap}. No se publica el carrusel.")
        return
    logger.info(f"Farmacias: {', '.join(nombres)}")

    fecha = farm._fecha_larga(hoy).capitalize()
    cabecera = "⚠️ CAMBIO de turno de hoy\n\n" if es_cambio else ""
    caption = (
        f"📰 Tapa y farmacias de turno — {fecha}\n\n"
        + cabecera
        + "💊 Farmacias de hoy:\n" + "\n".join(lineas_cap)
        + f"\n\n📲 Encontrá todas las notas en nuestra web 👉 {_site()}"
        + "\n\n#Chivilcoy #DiarioLaCampaña #Farmacias #Diario"
    )

    # Slides del carrusel: tapa primero, farmacias segundo
    slide_tapa = compose_tapa_slide(tapa_img)
    slides = [slide_tapa, feed_farm]
    story_tapa = compose_tapa_story(tapa_img, fecha)

    if dry_run:
        logger.info(f"[dry-run] carrusel: {[s.name for s in slides]} + historia {story_tapa.name} (NO se publica)")
        logger.info(f"[dry-run] caption:\n{caption}")
        logger.info("=== Carrusel Tapa+Farmacias: fin (dry-run) ===")
        return

    plats = _platforms()
    algun_ok = False

    # Carrusel al feed
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

    # Historia de la tapa
    story_fns = {"instagram": lambda: instagram.publish_story(story_tapa),
                 "facebook": lambda: facebook.publish_story(story_tapa)}
    for name in plats:
        fn = story_fns.get(name)
        if not fn:
            continue
        try:
            fn()
            algun_ok = True
            logger.info(f"[{name}] historia de la tapa OK")
        except Exception as e:
            logger.error(f"[{name}] historia FALLÓ: {e}")

    if algun_ok:
        _marcar(hoy)
        logger.info("Carrusel tapa+farmacias registrado como publicado hoy.")
    else:
        logger.error("El carrusel tapa+farmacias NO se pudo publicar — se reintentará la próxima corrida.")

    logger.info("=== Carrusel Tapa+Farmacias: fin ===")
